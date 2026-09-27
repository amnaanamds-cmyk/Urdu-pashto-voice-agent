-- Awaaz Desk initial schema. See docs/BUILD_SPEC.md §4.
-- Multi-tenant: every row belongs to a business; RLS limits access to that
-- business's members. The agent/API use the service role and bypass RLS.

create extension if not exists pgcrypto;

-- Types ---------------------------------------------------------------------

create type business_type   as enum ('clinic', 'hostel', 'shop');
create type slot_status     as enum ('free', 'held', 'booked', 'blocked');
create type booking_status  as enum ('confirmed', 'rescheduled', 'cancelled', 'no_show', 'completed');
create type booking_source  as enum ('call', 'whatsapp');
create type call_outcome    as enum ('booked', 'faq', 'transferred', 'message', 'dropped');
create type call_language   as enum ('ps', 'ur', 'en', 'mixed');
create type integration_type as enum ('hostix');

-- Tables --------------------------------------------------------------------

create table businesses (
  id                        uuid primary key default gen_random_uuid(),
  name                      text not null,
  type                      business_type not null,
  city                      text not null,
  languages                 call_language[] not null default '{ur}',
  staff_phone               text,
  timings                   jsonb not null default '{}'::jsonb,
  faq_json                  jsonb not null default '{}'::jsonb,
  plan                      text not null default 'pilot',
  recording_retention_days  int  not null default 60 check (recording_retention_days between 1 and 90),
  created_at                timestamptz not null default now()
);

-- Who can see which business (dashboard users).
create table business_members (
  business_id uuid not null references businesses(id) on delete cascade,
  user_id     uuid not null references auth.users(id) on delete cascade,
  role        text not null default 'owner' check (role in ('owner', 'staff')),
  primary key (business_id, user_id)
);

create table services (
  id           uuid primary key default gen_random_uuid(),
  business_id  uuid not null references businesses(id) on delete cascade,
  name         text not null,
  duration_min int  not null check (duration_min > 0),
  price        numeric(10, 2)
);

create table slots (
  id          uuid primary key default gen_random_uuid(),
  business_id uuid not null references businesses(id) on delete cascade,
  date        date not null,
  start_time  time not null,
  status      slot_status not null default 'free',
  unique (business_id, date, start_time)
);

-- Minimal data only: no symptoms / health details (spec §11).
create table bookings (
  id           uuid primary key default gen_random_uuid(),
  business_id  uuid not null references businesses(id) on delete cascade,
  caller_name  text not null,
  caller_phone text not null,
  service_id   uuid references services(id) on delete set null,
  slot_id      uuid references slots(id) on delete set null,
  status       booking_status not null default 'confirmed',
  source       booking_source not null default 'call',
  created_at   timestamptz not null default now()
);

create table calls (
  id            uuid primary key default gen_random_uuid(),
  business_id   uuid not null references businesses(id) on delete cascade,
  caller_phone  text,
  language      call_language,
  duration_sec  int,
  outcome       call_outcome,
  recording_url text,
  transcript    text,
  after_hours   boolean not null default false,
  started_at    timestamptz not null default now()
);

create table messages (
  id           uuid primary key default gen_random_uuid(),
  business_id  uuid not null references businesses(id) on delete cascade,
  caller_phone text,
  text         text not null,
  handled      boolean not null default false,
  created_at   timestamptz not null default now()
);

create table usage (
  business_id  uuid not null references businesses(id) on delete cascade,
  month        date not null,  -- first day of month
  minutes_used numeric(10, 2) not null default 0,
  primary key (business_id, month)
);

-- api_key_ref points to a secret store entry (e.g. Supabase Vault id), never the key itself.
create table integrations (
  business_id uuid not null references businesses(id) on delete cascade,
  type        integration_type not null,
  api_key_ref text not null,
  primary key (business_id, type)
);

create index on slots    (business_id, date, status);
create index on bookings (business_id, caller_phone);
create index on calls    (business_id, started_at desc);
create index on messages (business_id, handled);

-- Row-level security ----------------------------------------------------------

create or replace function is_member(b uuid) returns boolean
language sql stable security definer set search_path = public as $$
  select exists (
    select 1 from business_members
    where business_id = b and user_id = auth.uid()
  );
$$;

create or replace function is_owner(b uuid) returns boolean
language sql stable security definer set search_path = public as $$
  select exists (
    select 1 from business_members
    where business_id = b and user_id = auth.uid() and role = 'owner'
  );
$$;

alter table businesses       enable row level security;
alter table business_members enable row level security;
alter table services         enable row level security;
alter table slots            enable row level security;
alter table bookings         enable row level security;
alter table calls            enable row level security;
alter table messages         enable row level security;
alter table usage            enable row level security;
alter table integrations     enable row level security;

create policy member_read   on businesses for select using (is_member(id));
create policy owner_update  on businesses for update using (is_owner(id)) with check (is_owner(id));

create policy self_read     on business_members for select using (user_id = auth.uid());

create policy member_all on services for all using (is_member(business_id)) with check (is_member(business_id));
create policy member_all on slots    for all using (is_member(business_id)) with check (is_member(business_id));
create policy member_all on bookings for all using (is_member(business_id)) with check (is_member(business_id));
create policy member_all on messages for all using (is_member(business_id)) with check (is_member(business_id));

-- Calls hold recordings/transcripts: owner only (spec §11).
create policy owner_read on calls for select using (is_owner(business_id));

create policy member_read on usage        for select using (is_member(business_id));
create policy owner_read  on integrations for select using (is_owner(business_id));

-- Recording retention -----------------------------------------------------------
-- Run daily (pg_cron or a scheduled function). Also delete the storage object.

create or replace function purge_expired_recordings() returns int
language sql security definer set search_path = public as $$
  with purged as (
    update calls c
       set recording_url = null, transcript = null
      from businesses b
     where c.business_id = b.id
       and (c.recording_url is not null or c.transcript is not null)
       and c.started_at < now() - make_interval(days => b.recording_retention_days)
    returning c.id
  )
  select count(*)::int from purged;
$$;
