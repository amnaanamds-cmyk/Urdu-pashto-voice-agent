-- Awaaz Desk initial schema. See docs/BUILD_SPEC.md §4.
--
-- Multi-tenant: every row belongs to a business; RLS limits dashboard users
-- (Supabase Auth) to their own business. The voice server uses the service
-- role / direct Postgres connection and scopes every query by business_id.
--
-- Status columns are text + CHECK (not Postgres enums) so the same app code
-- runs on Postgres and on SQLite for local development. Keep the CHECK lists
-- in sync with awaaz/db.py.

create extension if not exists pgcrypto;

-- Tables --------------------------------------------------------------------

create table businesses (
  id                        uuid primary key default gen_random_uuid(),
  name                      text not null,
  type                      text not null check (type in ('clinic', 'hostel', 'shop')),
  city                      text not null,
  languages                 jsonb not null default '["ur"]'::jsonb,   -- ["ps","ur","en"], first = primary
  ai_phone                  text unique,          -- E.164 number callers reach the AI on
  staff_phone               text,                 -- transfer destination
  owner_whatsapp            text,                 -- daily summary + messages go here
  timings                   jsonb not null default '{}'::jsonb,     -- {"slot_minutes":30,"days":{"mon":[["09:00","13:00"]],...}}
  faq_json                  jsonb not null default '{}'::jsonb,
  voice                     jsonb not null default '{}'::jsonb,     -- transcriber/voice overrides
  plan                      text not null default 'pilot',
  recording_retention_days  int  not null default 60 check (recording_retention_days between 1 and 90),
  owner_token_hash          text,                 -- sha256 of the owner dashboard token
  created_at                timestamptz not null default now()
);

-- Who can see which business (Supabase Auth dashboard users).
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
  duration_min int  not null default 30 check (duration_min > 0),
  price        numeric(10, 2),
  unique (business_id, name)
);

create table slots (
  id          uuid primary key default gen_random_uuid(),
  business_id uuid not null references businesses(id) on delete cascade,
  date        date not null,
  start_time  time not null,
  status      text not null default 'free' check (status in ('free', 'booked', 'blocked')),
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
  status       text not null default 'confirmed'
               check (status in ('confirmed', 'cancelled', 'no_show', 'completed')),
  source       text not null default 'call' check (source in ('call', 'whatsapp', 'dashboard')),
  created_at   timestamptz not null default now()
);

create table calls (
  id               uuid primary key default gen_random_uuid(),
  business_id      uuid not null references businesses(id) on delete cascade,
  provider_call_id text unique,                   -- Vapi call id
  caller_phone     text,
  language         text check (language in ('ps', 'ur', 'en', 'mixed')),
  duration_sec     int,
  outcome          text check (outcome in ('booked', 'faq', 'transferred', 'message', 'dropped')),
  ended_reason     text,
  cost_usd         numeric(10, 4),                -- provider-reported, for unit economics (§10)
  recording_url    text,
  transcript       text,
  after_hours      boolean not null default false,
  started_at       timestamptz not null default now()
);

create table messages (
  id           uuid primary key default gen_random_uuid(),
  business_id  uuid not null references businesses(id) on delete cascade,
  caller_name  text,
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

-- api_key_ref points to a secret (e.g. "env:HOSTIX_KEY_ALNOOR" or a Vault id), never the key.
create table integrations (
  business_id uuid not null references businesses(id) on delete cascade,
  type        text not null check (type in ('hostix')),
  base_url    text,
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
