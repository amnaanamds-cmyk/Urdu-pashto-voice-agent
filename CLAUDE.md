# Awaaz Desk — notes for Claude Code

Read `docs/BUILD_SPEC.md` first. It is the source of truth; keep it in sync
with code changes (schema, tools, prompt, edge cases).

## Rules

- **Latency is the product.** Target < 1.5 s end-of-speech → first audio
  (spec §6). Stream STT, LLM, and TTS; never block a turn on a non-streaming
  call. Say "ji, ek second" while a tool runs.
- **Never invent business facts.** Fees, timings, slots, rooms come from
  tools / DB only. The prompt says so; tool handlers must return errors, not
  guesses.
- **Minimal health data.** Bookings store name, phone, slot. No symptoms,
  anywhere (DB, logs, WhatsApp messages).
- **Recordings are owner-only and expire** (`businesses.recording_retention_days`,
  `purge_expired_recordings()`). Never commit audio; `tests/audio/clips/` is git-ignored.
- **Secrets** only via env (`.env.example`). `integrations.api_key_ref` stores a
  secret-store reference, never a key.
- **Multi-tenant:** every table has `business_id` + RLS. New tables must too.
- **Pashto text** shown to callers must be reviewed by a native Peshawari speaker
  before launch. Mark unreviewed strings with `# TODO(pashto-review)`.
- Time zone is Asia/Karachi (+05:00). Phones in E.164 (+92…).

## Layout

- `agent/prompts/system_prompt.md` — system prompt template (`{business_name}`, `{business_type}`, `{city}`, `{faq_json}`)
- `agent/tools.json` — Claude tool schemas; handlers go in `agent/`
- `api/supabase/migrations/` — SQL migrations, numbered
- `scripts/accuracy_test.py` — STT WER/CER + key-term scorer (stdlib only)

## Checks

```bash
python -m pytest tests/unit
```
