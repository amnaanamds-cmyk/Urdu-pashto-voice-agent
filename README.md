# Awaaz Desk

AI phone receptionist for clinics, hostels, and shops in KPK. Answers in
Urdu or Pashto (understands English and mixed speech), books, reschedules and
cancels appointments, answers FAQs, takes messages, and transfers to staff.

```
caller ──phone──▶ Vapi (line, STT, TTS) ──▶ /vapi/llm   Claude + tools ──▶ Postgres/Supabase
                                        └─▶ /vapi/webhook  call logs, recordings      │
owner ──browser──▶ /  dashboard (Today, Calendar, Calls, Messages, Settings, Billing) ◀┘
      ◀─WhatsApp── booking confirmations, messages, daily summary
```

## Try it

```bash
pip install -e ".[dev]"
cp .env.example .env                                   # add ANTHROPIC_API_KEY
python -m awaaz onboard scripts/seed/dr_khan_dental.json   # prints the dashboard token
python -m awaaz chat "Dr. Khan"                        # talk to the receptionist by text
python -m awaaz serve                                  # http://localhost:8000/login
python -m pytest                                       # 28+ tests, no network needed
```

Going to production (Supabase, Vapi number, WhatsApp, cron): [`docs/GO_LIVE.md`](docs/GO_LIVE.md).

## Layout

```
awaaz/
  agent/          engine.py (streaming Claude loop), tools.py (handlers), tools.json,
                  prompt.py + prompts/system_prompt.md, session.py, lang.py
  telephony/      vapi.py — assistant config, OpenAI-style SSE, live call control
  integrations/   whatsapp.py, hostix.py, secrets.py
  api/            app.py (Vapi endpoints), dashboard.py + templates/
  repo.py db.py   data access (Postgres or SQLite), tables
  jobs.py         daily slots, recording retention, WhatsApp summary
  onboarding.py   business setup from JSON
supabase/migrations/   production schema + row-level security
scripts/          accuracy_test.py (week-0 STT test), seed/*.json
tests/            unit/ (agent flows, API, repo), audio/ (accuracy test guide)
docs/             BUILD_SPEC.md (source of truth), GO_LIVE.md
```

## Status

Working: full call loop, booking/reschedule/cancel with atomic slot claims,
after-hours message taking, transfer during opening hours, hang-up, HOSTIX
rooms, WhatsApp confirmations, call logging with outcome/language/cost,
usage minutes, recording retention, owner dashboard, Postgres + RLS.

Not yet: Pashto strings reviewed by a native speaker, real HOSTIX API
contract, JazzCash/Easypaisa checkout, inbound WhatsApp bookings, reminder
calls (v2), multi-instance session store.
