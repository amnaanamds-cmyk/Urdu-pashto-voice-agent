"""Scheduled jobs. Run once a day (cron / Supabase scheduled function / any scheduler):

    python -m awaaz jobs daily      # slots for 14 days + recording retention
    python -m awaaz jobs summary    # WhatsApp each owner today's numbers (run ~21:00 PKT)
"""

from __future__ import annotations

import logging
from datetime import date

from . import timeutil
from .config import Settings
from .integrations.whatsapp import WhatsApp
from .repo import Repo
from .telephony import vapi

log = logging.getLogger("awaaz.jobs")


def ensure_all_slots(repo: Repo, start: date | None = None, days: int = 14) -> int:
    start = start or timeutil.now().date()
    return sum(repo.ensure_slots(b["id"], start, days) for b in repo.list_businesses())


def purge_recordings(repo: Repo, settings: Settings) -> int:
    """Spec §11: recordings/transcripts expire per business. Deletes the copy at
    Vapi too when VAPI_API_KEY is set; our reference is only cleared once the
    provider copy is gone (or no provider key is configured)."""
    purged = 0
    for row in repo.expired_recordings():
        if settings.vapi_api_key and row.get("provider_call_id"):
            if not vapi.delete_call(settings.vapi_api_key, row["provider_call_id"]):
                log.warning("could not delete call %s at Vapi; will retry tomorrow", row["provider_call_id"])
                continue
        repo.clear_recording(row["id"])
        purged += 1
    return purged


def summary_text(stats: dict) -> str:
    return (f"Awaaz Desk: Aaj {stats['calls']} calls, {stats['bookings']} bookings, "
            f"{stats['after_hours']} after-hours ({stats['saved']} saved). "
            f"{stats['open_messages']} messages baqi.")


def send_summaries(repo: Repo, wa: WhatsApp, day: date | None = None) -> int:
    day = day or timeutil.now().date()
    sent = 0
    for b in repo.list_businesses():
        if b.get("owner_whatsapp"):
            sent += wa.send_text(b["owner_whatsapp"], summary_text(repo.day_stats(b["id"], day)))
    return sent
