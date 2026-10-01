-- Minimal stand-in for Supabase's auth schema so supabase/migrations apply to a
-- plain Postgres for tests:
--   createdb awaaz_test && psql awaaz_test -f tests/pg_auth_stub.sql \
--     && psql awaaz_test -f supabase/migrations/0001_init.sql
--   AWAAZ_TEST_PG=postgresql:///awaaz_test python -m pytest
create schema if not exists auth;
create table if not exists auth.users (id uuid primary key);
create or replace function auth.uid() returns uuid language sql stable as
  $$ select nullif(current_setting('request.jwt.claim.sub', true), '')::uuid $$;
