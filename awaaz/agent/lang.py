"""Lightweight language guess for call logs (ps / ur / en / mixed).

Works on Arabic-script STT output (letters unique to Pashto vs Urdu) and on
Roman text (common function words). It labels calls for the dashboard; it
does not drive the conversation.
"""

from __future__ import annotations

import re

PASHTO_LETTERS = set("ټډړښږځڅېۍګڼ")
URDU_LETTERS = set("ٹڈڑےںھ")
PASHTO_WORDS = {"za", "zama", "sta", "staso", "tsanga", "sanga", "de", "dy", "sabaa", "manana", "ghwaram",
                "kawalay", "shum", "yaw", "kom", "sho", "ao", "na", "kho", "che", "da", "ta", "ba", "der"}
URDU_WORDS = {"hai", "hain", "main", "mein", "aap", "kya", "kal", "chahiye", "kitni", "kitna", "nahi",
              "shukriya", "ji", "ka", "ki", "ke", "se", "ko", "hoon", "kar", "dein", "baje"}
EN_WORDS = {"the", "is", "i", "want", "please", "appointment", "booking", "what", "you", "my", "need"}

_WORD = re.compile(r"[a-z']+")


def detect(texts: list[str]) -> str | None:
    text = " ".join(t for t in texts if t)
    if not text.strip():
        return None
    ps = sum(ch in PASHTO_LETTERS for ch in text)
    ur = sum(ch in URDU_LETTERS for ch in text)
    words = _WORD.findall(text.lower())
    ps += sum(w in PASHTO_WORDS for w in words)
    ur += sum(w in URDU_WORDS for w in words)
    en = sum(w in EN_WORDS for w in words)
    scores = {"ps": ps, "ur": ur, "en": en}
    best = max(scores, key=scores.get)
    if scores[best] == 0:
        return None
    second = sorted(scores.values())[-2]
    return "mixed" if second and second >= 0.5 * scores[best] else best
