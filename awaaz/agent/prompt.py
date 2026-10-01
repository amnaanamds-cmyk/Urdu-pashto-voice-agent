"""Builds the system prompt for a call."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from .. import timeutil

TEMPLATE = (Path(__file__).parent / "prompts" / "system_prompt.md").read_text(encoding="utf-8")

LANG_NAMES = {"ps": "Pashto", "ur": "Urdu", "en": "English"}
SCRIPT_RULES = {
    "ur": "Write Urdu in Urdu script (اردو), never Roman Urdu.",
    "ps": "Write Pashto in Pashto script (پښتو), never Roman Pashto.",
    "en": "Write in plain English.",
}

# Short filler the voice says while a tool runs (spec §6). Keep these tiny.
# TODO(pashto-review): Pashto filler.
FILLERS = {
    ("voice", "ur"): "جی، ایک سیکنڈ۔",
    ("voice", "ps"): "ښه، یوه شیبه۔",
    ("voice", "en"): "One second, please.",
    ("text", "ur"): "Ji, ek second.",
    ("text", "ps"): "Sha, yawa sheba.",
    ("text", "en"): "One second, please.",
}


def primary_language(business: dict) -> str:
    langs = business.get("languages") or ["ur"]
    return langs[0] if langs[0] in LANG_NAMES else "ur"


def channel_rules(business: dict, channel: str) -> str:
    if channel == "text":
        return ("Channel: text chat simulator. Reply in the caller's language and write the way "
                "they write (Roman Urdu/Pashto is fine). No markdown, no lists, no emojis.")
    lang = primary_language(business)
    others = [LANG_NAMES[x] for x in business.get("languages", []) if x in LANG_NAMES and x != lang]
    rules = [
        f"Channel: live phone call. Your words are read aloud by the business's {LANG_NAMES[lang]} text-to-speech voice.",
        f"Reply in {LANG_NAMES[lang]}. {SCRIPT_RULES[lang]}",
        "No markdown, lists, emojis, or symbols the voice can't say. Write numbers as digits.",
    ]
    if others:
        rules.append(f"You understand {', '.join(others)} too; if the caller can't follow "
                     f"{LANG_NAMES[lang]}, keep it very simple or offer a transfer.")
    rules.append("Callers' words come from speech recognition and may have errors; "
                 "if a name or time sounds wrong, confirm it.")
    return "\n".join(rules)


def business_info(business: dict, services: list[dict]) -> str:
    info = {
        "timings": business.get("timings", {}).get("days", {}),
        "services": [{"name": s["name"], "duration_min": s["duration_min"],
                      "price_pkr": float(s["price"]) if s.get("price") is not None else None}
                     for s in services],
        **(business.get("faq_json") or {}),
    }
    return json.dumps(info, ensure_ascii=False, sort_keys=True)


def build_system(business: dict, services: list[dict], channel: str,
                 caller_phone: str | None, now: datetime | None = None) -> list[dict]:
    """Two blocks: the stable business prompt, then per-call context (date,
    caller) so the stable part stays a cacheable prefix."""
    now = (now or timeutil.now()).astimezone(timeutil.PKT)
    stable = TEMPLATE.format(
        business_name=business["name"],
        business_type=business["type"],
        city=business["city"],
        channel_rules=channel_rules(business, channel),
        faq_json=business_info(business, services),
    )
    open_now = timeutil.is_open(business.get("timings") or {}, now)
    context = (
        f"Now: {now.strftime('%A %Y-%m-%d %H:%M')} (Pakistan time). "
        f"The business is {'open' if open_now else 'closed'} right now"
        f"{'' if open_now else ' — staff are not available for transfer; take messages instead'}.\n"
        f"Caller's number: {caller_phone or 'hidden (ask for it before booking)'}."
    )
    return [{"type": "text", "text": stable}, {"type": "text", "text": context}]
