"""Onboard a business from a JSON file (spec §8). See scripts/seed/*.json."""

from __future__ import annotations

import json
import secrets
from pathlib import Path

from . import timeutil
from .repo import Repo, hash_token

REQUIRED = ("name", "type", "city")


def new_owner_token() -> str:
    return secrets.token_urlsafe(18)


def onboard(repo: Repo, cfg: dict) -> tuple[dict, str]:
    missing = [k for k in REQUIRED if not cfg.get(k)]
    if missing:
        raise ValueError(f"missing fields: {', '.join(missing)}")
    if cfg["type"] not in ("clinic", "hostel", "shop"):
        raise ValueError("type must be clinic, hostel, or shop")
    for day, ranges in (cfg.get("timings", {}).get("days") or {}).items():
        if day not in timeutil.DAY_KEYS:
            raise ValueError(f"unknown day {day!r}; use mon..sun")
        for a, b in ranges:
            timeutil.parse_hhmm(a), timeutil.parse_hhmm(b)
    token = new_owner_token()
    biz = repo.create_business(
        name=cfg["name"], type=cfg["type"], city=cfg["city"],
        languages=cfg.get("languages") or ["ur"],
        ai_phone=cfg.get("ai_phone"), staff_phone=cfg.get("staff_phone"),
        owner_whatsapp=cfg.get("owner_whatsapp"),
        timings=cfg.get("timings") or {}, faq_json=cfg.get("faq") or {},
        voice=cfg.get("voice") or {}, plan=cfg.get("plan", "pilot"),
        recording_retention_days=int(cfg.get("recording_retention_days", 60)),
        owner_token_hash=hash_token(token),
    )
    for s in cfg.get("services", []):
        repo.add_service(biz["id"], s["name"], int(s.get("duration_min", 30)), s.get("price"))
    if cfg.get("hostix"):
        h = cfg["hostix"]
        repo.set_integration(biz["id"], "hostix", h["api_key_ref"], h.get("base_url"))
    repo.ensure_slots(biz["id"], timeutil.now().date(), 14)
    return biz, token


def rotate_token(repo: Repo, business_id) -> str:
    token = new_owner_token()
    repo.update_business(business_id, owner_token_hash=hash_token(token))
    return token


def load(path: str | Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))
