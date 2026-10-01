"""Pakistan time helpers. All business times are Asia/Karachi (+05:00, no DST)."""

from __future__ import annotations

import re
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

PKT = ZoneInfo("Asia/Karachi")
DAY_KEYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


def now() -> datetime:
    return datetime.now(PKT)


def day_key(d: date) -> str:
    return DAY_KEYS[d.weekday()]


def parse_hhmm(s: str) -> time:
    h, m = s.strip().split(":")[:2]
    return time(int(h), int(m))


def open_ranges(timings: dict, d: date) -> list[tuple[time, time]]:
    days = (timings or {}).get("days", {})
    return [(parse_hhmm(a), parse_hhmm(b)) for a, b in days.get(day_key(d), [])]


def is_open(timings: dict, at: datetime) -> bool:
    at = at.astimezone(PKT)
    t = at.time()
    return any(a <= t < b for a, b in open_ranges(timings, at.date()))


def slot_starts(timings: dict, d: date) -> list[time]:
    step = int((timings or {}).get("slot_minutes", 30))
    out = []
    for a, b in open_ranges(timings, d):
        cur = datetime.combine(d, a)
        end = datetime.combine(d, b)
        while cur + timedelta(minutes=step) <= end:
            out.append(cur.time())
            cur += timedelta(minutes=step)
    return out


_PHONE_JUNK = re.compile(r"[^\d+]")


def normalize_phone(raw: str | None) -> str | None:
    """Pakistani numbers to E.164: 0300-1234567 / 923001234567 -> +923001234567."""
    if not raw:
        return None
    p = _PHONE_JUNK.sub("", raw)
    if p.startswith("00"):
        p = "+" + p[2:]
    elif p.startswith("0"):
        p = "+92" + p[1:]
    elif p.startswith("92") and len(p) == 12:
        p = "+" + p
    elif p.isdigit() and len(p) == 10 and p.startswith("3"):
        p = "+92" + p
    return p if p.startswith("+") else p or None


def utc_iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()
