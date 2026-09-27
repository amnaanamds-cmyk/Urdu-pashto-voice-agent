# Awaaz Desk

AI phone receptionist for clinics, hostels, and shops in KPK. Answers in
Pashto, Urdu, or English; books, reschedules, answers FAQs, and transfers to staff.

- **Build spec:** [`docs/BUILD_SPEC.md`](docs/BUILD_SPEC.md)
- **Agent prompt:** [`agent/prompts/system_prompt.md`](agent/prompts/system_prompt.md)
- **Agent tools:** [`agent/tools.json`](agent/tools.json)
- **Database:** [`api/supabase/migrations/0001_init.sql`](api/supabase/migrations/0001_init.sql)
- **Week-0 accuracy test:** [`tests/audio/README.md`](tests/audio/README.md)

```
agent/          LiveKit/Vapi agent, prompts, tool handlers
api/            booking + webhook endpoints, Supabase migrations
dashboard/      Next.js owner app
integrations/   hostix, whatsapp, telephony
tests/audio/    Pashto/Urdu accuracy test (audio itself is git-ignored)
scripts/        onboarding, seed data, accuracy_test.py
```

## Status

Month 1: accuracy test → telephony → Urdu agent. See spec §14.

```bash
python scripts/accuracy_test.py tests/audio/manifest.example.csv tests/audio/hyps.example.csv
python -m pytest tests/unit
```
