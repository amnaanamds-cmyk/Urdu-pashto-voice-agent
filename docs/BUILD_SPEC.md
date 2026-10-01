# Awaaz Desk: Build Spec

AI phone receptionist for clinics, hostels, and shops in KPK / Pakistan.
Speaks Pashto, Urdu, and English. Books, reschedules, answers FAQs, and
hands off to staff.

This is the source of truth for the build. Where code and this doc disagree,
fix one of them in the same PR.

---

## 1. Sample call scripts

**Clinic booking (Pashto → booking)**
> **AI:** Assalam o alaikum, Dr. Khan Dental Clinic. Za staso tsanga mrasta kawalay shum?
> **Caller:** Da ghakh dard de, appointment ghwaram.
> **AI:** Sabaa sahar 11 bajay ya maspakhin 4 bajay khali dy. Kom yaw?
> **Caller:** 4 bajay.
> **AI:** Sta num sa de?
> **Caller:** Imran.
> **AI:** Imran sahib, sabaa 4 bajay booking sho. WhatsApp ba darta raasi. Manana.

**Hostel inquiry (Urdu)**
> **AI:** Assalam o alaikum, Al-Noor Boys Hostel. Main aap ki kya madad kar sakti hoon?
> **Caller:** Room khali hai? Fees kitni hai?
> **AI:** Ji, 2 seater room khali hai, fees 12,000 mahana, khana shamil. Visit ke liye aana chahenge?
> *(fees and availability pulled live from HOSTIX)*

> ⚠️ **Launch blocker:** every Pashto line must be checked by a native
> Peshawari speaker before launch. A wrong phrase kills trust instantly.

## 2. Agent system prompt (core)

Canonical version lives in [`awaaz/agent/prompts/system_prompt.md`](../awaaz/agent/prompts/system_prompt.md); `awaaz/agent/prompt.py` adds the channel rules (voice replies in Urdu/Pashto script for TTS), business info, today's date, and the caller's number.

```
You are the receptionist for {business_name}, a {business_type} in {city}.
Speak in the caller's language: Pashto, Urdu, or English. Match mixed speech.
Keep every reply under 2 short sentences. This is a phone call.
Be warm and respectful: use "sahib", "ji", "manana/shukriya".

You can: check slots, book, reschedule, cancel, answer FAQs, transfer.
Always confirm name, day, and time before booking.
Never invent fees, timings, or availability — use tools only.
If unsure, say you'll pass the message to staff.

Medical emergency (clinics): tell the caller to call Rescue 1122 or go to
the nearest hospital immediately. Do not give medical advice.

Business info:
{faq_json}
```

## 3. Tools the agent calls

JSON schemas live in [`awaaz/agent/tools.json`](../awaaz/agent/tools.json), handlers in `awaaz/agent/tools.py`.

| Tool | Input | Does |
|---|---|---|
| `check_slots` | date, service | Returns free times |
| `book` | name, phone, date, time, service | Creates booking + triggers WhatsApp |
| `find_booking` | phone | Finds existing booking |
| `reschedule` | booking_id, new_time | Moves booking |
| `cancel` | booking_id | Cancels |
| `get_rooms` | (hostel) type | Live availability from HOSTIX |
| `transfer` | reason | Forwards to staff number |
| `take_message` | name, phone, message | Saves + WhatsApps owner |
| `end_call` | goodbye | Says goodbye and hangs up (wrong number, done, emergency) |

Phone defaults to caller ID. `book`/`reschedule` take date + HH:MM; `find_booking`, `reschedule`, `cancel` only touch bookings made from the caller's own number. `transfer` only works during opening hours and otherwise returns `staff_unavailable` so the agent takes a message.

## 4. Database (Supabase)

Schema + RLS in [`supabase/migrations/0001_init.sql`](../supabase/migrations/0001_init.sql); mirrored for SQLite dev in `awaaz/db.py`. Beyond the list below: `businesses` has `ai_phone` (number callers dial), `owner_whatsapp`, `voice`, `recording_retention_days`, `owner_token_hash`; `calls` has `provider_call_id`, `ended_reason`, `cost_usd`, `after_hours`; status columns are text + CHECK.

- `businesses`: id, name, type, city, languages, staff_phone, timings, faq_json, plan
- `services`: id, business_id, name, duration_min, price
- `slots`: id, business_id, date, start_time, status
- `bookings`: id, business_id, caller_name, caller_phone, service_id, slot_id, status, source (call/whatsapp)
- `calls`: id, business_id, caller_phone, language, duration_sec, outcome (booked/faq/transferred/dropped), recording_url, transcript
- `messages`: id, business_id, caller_phone, text, handled
- `usage`: business_id, month, minutes_used
- `integrations`: business_id, type (hostix), api_key_ref

Row-level security so each business sees only its own data. Same multi-tenant
pattern as HOSTYLLO.

## 5. Repo structure

```
awaaz/agent/         # Claude engine, prompts, tool handlers
awaaz/telephony/     # Vapi: assistant config, SSE, call control
awaaz/api/           # FastAPI: Vapi endpoints + owner dashboard (server-rendered)
awaaz/integrations/  # whatsapp, hostix, secrets
supabase/migrations/ # Postgres schema + RLS
tests/audio/         # real Pashto/Urdu call recordings for accuracy tests (git-ignored)
scripts/             # accuracy test, seed business configs
docs/                # this spec, GO_LIVE.md
```

Telephony is Vapi with our server as its custom LLM (fastest path to a real
number; swap to LiveKit later behind `CallControl` if costs demand it). The
dashboard is server-rendered from the same app instead of a separate Next.js
app, to keep one deployable for the pilot.

## 6. Latency budget (the make-or-break)

Callers hang up if the AI pauses too long. Target **under ~1.5 s** from when the
caller stops talking to when the AI starts speaking.

| Step | Target |
|---|---|
| End-of-speech detection | ~300 ms |
| Speech-to-text final | ~300 ms |
| Claude first token | ~500 ms |
| Text-to-speech first audio | ~300 ms |

**Tricks:**
- Stream everything.
- Use a fast Claude model for conversation (default `claude-haiku-4-5`, `AWAAZ_MODEL` to change).
- Say "ji, ek second" while a tool runs.
- Keep replies short.

## 7. Edge cases

| Situation | Behavior |
|---|---|
| Silence 5 s | "Ji, aap sun rahe hain?" then polite hang-up |
| Heavy noise / not understood twice | Offer transfer or take a message |
| Angry caller | Apologize once, transfer to staff |
| Asks for a human | Transfer immediately, no arguing |
| Medical emergency | Rescue 1122 / hospital, end booking flow |
| Wrong number / spam | Short polite end |
| No slots left | Offer next available day or waitlist |
| Staff line busy on transfer | Take a message, WhatsApp the owner |

In code: silence → Vapi `customer.speech.timeout` hook (5 s); transfer outside
opening hours or a failed transfer → `staff_unavailable` → `take_message`;
taken/double-booked slot → nearest free times; nothing free → next available
day; Claude unreachable → a fixed apology line and offer to take a message;
everything else is prompt rules (`system_prompt.md`) with tests in
`tests/unit/test_agent.py`.

## 8. Business onboarding (target: 10 minutes)

1. Sign up and pick your type (clinic/hostel/shop).
2. Enter timings, services, and fees. For hostels, connect HOSTIX instead.
3. Pick a voice and languages.
4. Add the staff transfer number.
5. Get your AI number, or set call forwarding on busy/no-answer.
6. Make a test call and go live.

**Smart default:** forward only **missed and after-hours calls** to the AI at
first. Owners trust it faster when it's catching calls they'd lose anyway.

## 9. Owner dashboard screens

- **Today:** calls, bookings, missed-then-saved count
- **Calendar:** bookings, drag to reschedule
- **Calls:** list with language, outcome, recording, transcript
- **Messages:** callers who left a message
- **Settings:** FAQ, timings, voice, transfer number
- **Billing:** minutes used, plan, JazzCash/Easypaisa payment

Plus a **daily WhatsApp summary:** "Aaj 23 calls, 9 bookings, 6 after-hours."

## 10. Unit economics (fill in during pilot)

```
Cost per minute = telephony + STT + Claude + TTS
Avg call length ≈ 1.5–2 min → cost per call
Monthly cost per customer = calls/month × cost per call + WhatsApp msgs
Target: monthly cost ≤ 30% of plan price
```

Don't guess these. Log real numbers from the pilot and set prices after.

| Item | Cost / min (PKR) | Source / date |
|---|---|---|
| Telephony | _TBD_ | |
| STT | _TBD_ | |
| Claude | _TBD_ | |
| TTS | _TBD_ | |
| WhatsApp / msg | _TBD_ | |

## 11. Privacy and security

- Clinic calls can include health info. **Store minimal data:** name, phone,
  and slot. No symptoms in the booking record.
- Announce recording at the start of every call.
- Auto-delete recordings after a set period (e.g., 30–90 days), configurable
  per business (`businesses.recording_retention_days`). `python -m awaaz jobs daily`
  deletes the call at Vapi and clears our recording URL + transcript.
- Encrypt recordings, and only the owner can access them. The dashboard proxies audio so the storage URL never reaches the browser; RLS makes `calls` owner-only.
- Keep API keys in env/secret storage, never in code or chat.

## 12. Testing plan

1. **Audio accuracy (week 0):** 30+ real recordings (Peshawari Pashto, Urdu,
   mixed, noisy). Measure how many are transcribed correctly.
   → [`scripts/accuracy_test.py`](../scripts/accuracy_test.py), see
   [`tests/audio/README.md`](../tests/audio/README.md).
2. **Scripted calls:** 50 test calls covering every edge case in §7.
3. **Shadow mode:** the AI listens and suggests but the staff answers, for
   1 week, to compare.
4. **Live pilot:** after-hours only, then expand.

## 13. Sales kit

- **Demo:** call the number live in front of the owner. Nothing sells better.
- **Pilot offer:** 30 days free on missed and after-hours calls only.
- **Proof metric:** "You missed X calls last month. We caught them."
- **Objection "log robot se baat nahi karte":** show the transfer-to-human
  feature and the local voice.
- **Objection "price":** compare with a receptionist's salary and lost patients.

## 14. Month-by-month plan

| Month | Goal |
|---|---|
| 1 | Accuracy test, telephony sorted, Urdu agent working |
| 2 | Pashto + booking + dashboard, pilot in 5 HOSTIX hostels |
| 3 | 5 clinics pilot, fix issues, finalize pricing |
| 4 | 20 paying customers, reminder calls (v2) |
| 6 | 50+ customers, reseller network in KPK |
