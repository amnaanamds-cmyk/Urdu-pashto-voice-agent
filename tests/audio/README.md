# Audio accuracy test (week 0)

Goal: before building anything else, find out whether any speech-to-text engine
understands **Peshawari Pashto** well enough to book appointments. If it doesn't,
the product plan changes (Urdu-first, or a custom model).

## 1. Collect 30+ clips

Real phone audio (8 kHz, as it arrives over the line), 3–15 seconds each.
Get consent from every speaker. Aim for roughly:

| Bucket | Clips |
|---|---|
| Peshawari Pashto, quiet | 8 |
| Pashto, noisy (street, shop, bike) | 6 |
| Urdu, quiet | 6 |
| Urdu, noisy | 4 |
| Mixed Pashto/Urdu/English | 6 |

Record what callers actually say: names, days ("sabaa", "kal"), times
("4 bajay", "char bajay"), "appointment", fees, room types.

Put audio in `tests/audio/clips/` — it is git-ignored. **Never commit call
recordings** (spec §11). Share them through encrypted storage instead.

## 2. Write reference transcripts

Copy `manifest.example.csv` to `manifest.csv` and fill one row per clip:

| Column | Meaning |
|---|---|
| `clip` | file name in `clips/` |
| `language` | `ps`, `ur`, `en`, or `mixed` |
| `dialect` | e.g. `peshawari`, `yusufzai`, `kandahari` |
| `noise` | `quiet`, `street`, `shop`, … |
| `reference` | exactly what was said, by a **native speaker** |
| `key_terms` | words the booking depends on, `|`-separated; `/` for accepted alternates (`4/char`) |

Pick one script per engine and stick to it (Roman Urdu/Pashto vs. Arabic
script). The scorer folds common Arabic-script variants (ي/ی, ك/ک, ہ/ه,
diacritics, Urdu digits), but it cannot compare Roman to Arabic script.

## 3. Transcribe with each STT engine

For each engine, write `results/<engine>.csv` with columns `clip,hypothesis`.
Try the same clips on every engine you are considering.

## 4. Score

```bash
python scripts/accuracy_test.py tests/audio/manifest.csv results/*.csv --json results/report.json
```

A clip is **usable** when WER ≤ 0.25 (`--max-wer`) and every key term was
recognized. Decision rule for month 1:

- Pashto quiet usable ≥ 80% → build Pashto in month 2 as planned.
- 60–80% → launch Urdu first, add Pashto with confirmation prompts ("4 bajay, sahi?").
- < 60% → Urdu only; revisit Pashto STT (fine-tuning) later.
