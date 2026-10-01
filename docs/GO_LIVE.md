# Going live

Everything below the dashed line in each step is a one-time setup. Order matters.

## 0. Local first (15 minutes)

```bash
pip install -e ".[dev]"
cp .env.example .env            # set ANTHROPIC_API_KEY at minimum
python -m awaaz onboard scripts/seed/dr_khan_dental.json
python -m awaaz chat "Dr. Khan"                     # Roman Urdu/Pashto by text
python -m awaaz chat "Dr. Khan" --channel voice     # replies in Urdu script, as the TTS gets them
python -m awaaz serve                               # dashboard at http://localhost:8000/login
```

Use the chat to run the 50 scripted edge-case calls (spec §12.2) before any phone is involved.

## 1. Database: Supabase

1. Create a Supabase project (region closest to Pakistan: Mumbai / `ap-south-1`).
2. SQL editor → run `supabase/migrations/0001_init.sql` (or `supabase db push`).
3. Set `DATABASE_URL` to the **Session pooler** connection string.

The server connects as the database owner and scopes every query by
`business_id`. RLS protects the tables for anything that uses Supabase Auth
(e.g. a future Next.js dashboard): members see only their business; call
recordings/transcripts are owner-only.

## 2. Server

Any host with HTTPS works (Render, Railway, Fly.io, a VPS + Caddy). With Docker:

```bash
docker build -t awaaz-desk .
docker run -p 8000:8000 --env-file .env awaaz-desk
```

Set `PUBLIC_BASE_URL` to the public HTTPS URL and random values for
`AWAAZ_WEBHOOK_SECRET` and `AWAAZ_SESSION_SECRET`. Check `GET /health`.

Run one instance for now: live call state is kept in memory per process
(Vapi sends every turn of a call to the same URL; with several instances,
use sticky routing by call id or move sessions to Redis).

Schedule (cron or your host's scheduler):

```
5 0 * * *    python -m awaaz jobs daily      # slots for 14 days + recording retention
0 16 * * *   python -m awaaz jobs summary    # 21:00 PKT daily WhatsApp summary (cron in UTC)
```

## 3. Phone line: Vapi

Vapi does telephony, speech-to-text, and text-to-speech; our server is the brain.

1. Buy or import a number in Vapi. For Pakistan, import a number from a SIP
   trunk / Twilio that can receive forwarded calls from Pakistani mobiles
   (owners forward busy/no-answer to it).
2. On the number: **no assistant**, Server URL = `https://<host>/vapi/webhook`,
   header `x-awaaz-secret: <AWAAZ_WEBHOOK_SECRET>`.
3. Put that number in the business config (`ai_phone`) and onboard.

On each call Vapi asks `/vapi/webhook` for an assistant; we return one built
for that business (greeting with recording notice, Azure Urdu or Pashto
transcriber and voice, custom LLM → `/vapi/llm`, recording on, call control on).

**Verify on the first test call** (fields come from Vapi's SDK types; docs
were not reachable when this was written):
- the silence hook (`hooks[].do[].exact`) speaks "جی، آپ سن رہے ہیں؟" after 5 s;
- transfer and hang-up via `monitor.controlUrl` work;
- `end-of-call-report` shows up on the Calls page with a playable recording.

Transcriber/voice defaults: Azure `ur-IN` / `ps-AF` STT, `ur-PK-UzmaNeural` /
`ps-AF-LatifaNeural` TTS. Change per business on the Settings page
(`{"transcriber": {...}, "voice": {...}}`) once the week-0 accuracy test picks
the best engine.

## 4. WhatsApp

1. Meta Business → WhatsApp Cloud API → phone number id + permanent token.
2. Create and get approved a **utility template** for booking confirmations
   with 4 body variables: name, day, time, business. Set
   `WHATSAPP_BOOKING_TEMPLATE`. (Free-text messages only reach people who
   messaged you in the last 24 h.)
3. Owners should send one message to the business WhatsApp number so their
   messages and daily summaries arrive as plain text.

Without a token, messages are logged instead of sent.

## 5. HOSTIX (hostels)

`awaaz/integrations/hostix.py` assumes `GET {base_url}/api/rooms?type=` →
`[{type, available, fee_monthly, meals_included}]`. Adjust to the real HOSTIX
API, then set the key as an env var and reference it in the business config
(`"api_key_ref": "env:HOSTIX_KEY_ALNOOR"`).

## 6. Before the first real caller

- [ ] Native Peshawari speaker reviewed every `TODO(pashto-review)` string
      and the Pashto greeting/silence lines in `awaaz/telephony/vapi.py`.
- [ ] Week-0 accuracy test done (`tests/audio/README.md`) and transcriber chosen.
- [ ] 50 scripted test calls (text simulator, then phone).
- [ ] Owner has the dashboard token and knows how to forward missed/after-hours calls.
- [ ] Recording retention set per business (Settings).
