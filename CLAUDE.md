# Awaaz Desk — notes for Claude Code

Read `docs/BUILD_SPEC.md` first. It is the source of truth; keep it in sync
with code changes (schema, tools, prompt, edge cases).

## Rules

- **Latency is the product.** Target < 1.5 s end-of-speech → first audio
  (spec §6). The engine streams Claude's text straight to Vapi; never add a
  blocking step before the first token. A filler ("جی، ایک سیکنڈ") is spoken
  while tools run. Default model is `claude-haiku-4-5` (override `AWAAZ_MODEL`).
- **Never invent business facts.** Fees, timings, slots, rooms come from
  tools / DB only. Tool handlers return `{"ok": false, "error", "instruction"}`,
  never guesses.
- **Minimal health data.** Bookings store name, phone, slot. No symptoms,
  anywhere (DB, logs, WhatsApp messages).
- **Recordings are owner-only and expire** (`businesses.recording_retention_days`,
  `python -m awaaz jobs daily`). Never commit audio; `tests/audio/clips/` is git-ignored.
- **Secrets** only via env (`.env.example`). `integrations.api_key_ref` is a
  reference like `env:NAME`, never a key.
- **Multi-tenant:** every table has `business_id` + RLS, and every repo query
  filters by it. New tables must too. Callers can only see/change bookings
  made from their own number.
- **Pashto text** shown to callers must be reviewed by a native Peshawari speaker
  before launch. Mark unreviewed strings with `TODO(pashto-review)`.
- **Voice replies are written in Urdu/Pashto script** (TTS can't read Roman Urdu well).
- Time zone is Asia/Karachi (+05:00). Phones in E.164 (+92…). Store timestamps in UTC.
- Schema changes: new numbered file in `supabase/migrations/` **and** update `awaaz/db.py`.

## Layout

- `awaaz/agent/` — `engine.py` (Claude streaming loop), `tools.py` + `tools.json`, `prompt.py` + `prompts/system_prompt.md`
- `awaaz/telephony/vapi.py` — Vapi assistant config, SSE format, call control
- `awaaz/api/` — FastAPI app (`create_app`), dashboard + Jinja templates
- `awaaz/repo.py`, `awaaz/db.py` — data access, table definitions
- `scripts/accuracy_test.py` — STT WER/CER + key-term scorer (stdlib only)

## Checks

```bash
python -m pytest                                   # SQLite
AWAAZ_TEST_PG=postgresql://... python -m pytest    # against Postgres with the migration applied
```
