"""Tool handlers for the agent (schemas in tools.json).

Handlers return plain dicts that become tool_result JSON. Failures return
{"ok": false, "error": ...} with an instruction for the agent, never a guess
(CLAUDE.md: tool handlers must return errors, not guesses).
"""

from __future__ import annotations

import json
import logging
from datetime import date, datetime, time
from pathlib import Path
from typing import Callable

from .. import timeutil
from ..integrations import hostix
from ..integrations.whatsapp import WhatsApp
from ..repo import NotFound, Repo, SlotTaken
from .session import CallSession

log = logging.getLogger("awaaz.tools")

TOOLS: list[dict] = json.loads((Path(__file__).parent / "tools.json").read_text(encoding="utf-8"))

URDU_DAYS = ["Peer", "Mangal", "Budh", "Jumeraat", "Juma", "Hafta", "Itwar"]


class ToolInputError(ValueError):
    pass


def _parse_date(v) -> date:
    try:
        return date.fromisoformat(str(v).strip())
    except ValueError as e:
        raise ToolInputError(f"date must be YYYY-MM-DD, got {v!r}") from e


def _parse_time(v) -> time:
    try:
        return timeutil.parse_hhmm(str(v))
    except (ValueError, IndexError) as e:
        raise ToolInputError(f"time must be HH:MM, got {v!r}") from e


def _spoken_time(t: time) -> str:
    h = t.hour % 12 or 12
    part = "subah" if t.hour < 12 else ("dopahar" if t.hour < 16 else ("shaam" if t.hour < 19 else "raat"))
    mins = f":{t.minute:02d}" if t.minute else ""
    return f"{part} {h}{mins} bajay"


def _day_label(d: date, today: date) -> str:
    rel = {0: "aaj", 1: "kal"}.get((d - today).days)
    base = f"{URDU_DAYS[d.weekday()]} {d.day}/{d.month}"
    return f"{rel} ({base})" if rel else base


def _booking_view(b: dict, today: date) -> dict:
    return {
        "booking_id": str(b["id"]),
        "name": b["caller_name"],
        "date": b["date"].isoformat() if b.get("date") else None,
        "time": b["start_time"].strftime("%H:%M") if b.get("start_time") else None,
        "spoken": f"{_day_label(b['date'], today)}, {_spoken_time(b['start_time'])}" if b.get("date") else None,
        "service": b.get("service"),
        "status": b["status"],
    }


class ToolBox:
    def __init__(self, repo: Repo, whatsapp: WhatsApp,
                 get_rooms: Callable[[dict, str | None], list[dict]] = hostix.get_rooms,
                 clock: Callable[[], datetime] = timeutil.now):
        self.repo = repo
        self.wa = whatsapp
        self.get_rooms_fn = get_rooms
        self.clock = clock

    def run(self, session: CallSession, name: str, args: dict) -> dict:
        fn = getattr(self, f"t_{name}", None)
        if fn is None:
            return {"ok": False, "error": f"unknown tool {name}"}
        if not isinstance(args, dict):
            return {"ok": False, "error": "tool input must be an object"}
        try:
            return fn(session, **args)
        except ToolInputError as e:
            return {"ok": False, "error": str(e)}
        except TypeError as e:   # missing/unknown argument names
            return {"ok": False, "error": f"bad arguments: {e}"}
        except Exception:
            log.exception("tool %s failed", name)
            session.tool_errors += 1
            return {"ok": False, "error": "system_error",
                    "instruction": "Apologize briefly and offer to take a message for staff."}

    # --- Tools ------------------------------------------------------------

    def t_check_slots(self, s: CallSession, date: str, service: str | None = None) -> dict:
        now = self.clock()
        d = _parse_date(date)
        if d < now.date():
            return {"ok": False, "error": "date is in the past", "today": now.date().isoformat()}
        times = self.repo.free_times(s.business_id, d, now)
        out = {"ok": True, "date": d.isoformat(), "day": _day_label(d, now.date()),
               "free": [{"time": t.strftime("%H:%M"), "spoken": _spoken_time(t)} for t in times[:8]]}
        if len(times) > 8:
            out["more_free_count"] = len(times) - 8
        if not times:
            nxt = self.repo.next_free_day(s.business_id, d, now=now)
            out["next_available"] = None if nxt is None else {
                "date": nxt[0].isoformat(), "day": _day_label(nxt[0], now.date()),
                "free": [{"time": t.strftime("%H:%M"), "spoken": _spoken_time(t)} for t in nxt[1][:6]]}
            out["instruction"] = ("Offer the next available day." if nxt else
                                  "Nothing free in the next 2 weeks: offer to take a message for the waitlist.")
        return out

    def t_book(self, s: CallSession, name: str, date: str, time: str,
               phone: str | None = None, service: str | None = None) -> dict:
        phone = timeutil.normalize_phone(phone) or s.caller_phone
        if not phone:
            return {"ok": False, "error": "need_phone", "instruction": "Ask for the caller's mobile number."}
        if not name or not name.strip():
            return {"ok": False, "error": "need_name", "instruction": "Ask for the caller's name."}
        d, t = _parse_date(date), _parse_time(time)
        try:
            b = self.repo.book(s.business_id, name=name, phone=phone, d=d, t=t, service=service)
        except SlotTaken:
            free = self.repo.free_times(s.business_id, d, self.clock())
            mins = lambda x: x.hour * 60 + x.minute
            nearest = sorted(sorted(free, key=lambda x: abs(mins(x) - mins(t)))[:4])
            return {"ok": False, "error": "slot_not_available",
                    "free_same_day": [x.strftime("%H:%M") for x in nearest],
                    "instruction": "Apologize and offer one of the free times."}
        s.booked = True
        today = self.clock().date()
        view = _booking_view(b, today)
        sent = self.wa.booking_confirmation(phone, name=b["caller_name"], day=_day_label(d, today),
                                            time=_spoken_time(t), business=s.business["name"])
        view.update(ok=True, whatsapp_sent=sent)
        return view

    def t_find_booking(self, s: CallSession, phone: str | None = None) -> dict:
        # Only the caller's own bookings: a stranger shouldn't hear someone
        # else's appointment by naming their number. A spoken number is used
        # only when caller ID is hidden.
        phone = s.caller_phone or timeutil.normalize_phone(phone)
        if not phone:
            return {"ok": False, "error": "need_phone", "instruction": "Ask which number the booking was made with."}
        today = self.clock().date()
        found = self.repo.upcoming_bookings(s.business_id, phone, today)
        return {"ok": True, "bookings": [_booking_view(b, today) for b in found]}

    def _own_booking(self, s: CallSession, booking_id: str) -> dict | None:
        """Callers may only change bookings made from their own number."""
        try:
            b = self.repo.get_booking(s.business_id, booking_id)
        except NotFound:
            return None
        if s.caller_phone and b["caller_phone"] != s.caller_phone:
            return None
        return b

    def t_reschedule(self, s: CallSession, booking_id: str, date: str, time: str) -> dict:
        if not self._own_booking(s, booking_id):
            return {"ok": False, "error": "booking_not_found", "instruction": "Use find_booking first."}
        d, t = _parse_date(date), _parse_time(time)
        try:
            b = self.repo.reschedule(s.business_id, booking_id, d, t)
        except SlotTaken:
            return {"ok": False, "error": "slot_not_available", "instruction": "Check slots again and offer another time."}
        except NotFound:
            return {"ok": False, "error": "booking_not_active"}
        s.booked = True
        today = self.clock().date()
        self.wa.booking_confirmation(b["caller_phone"], name=b["caller_name"], day=_day_label(d, today),
                                     time=_spoken_time(t), business=s.business["name"])
        return {"ok": True, **_booking_view(b, today)}

    def t_cancel(self, s: CallSession, booking_id: str) -> dict:
        if not self._own_booking(s, booking_id):
            return {"ok": False, "error": "booking_not_found", "instruction": "Use find_booking first."}
        b = self.repo.cancel(s.business_id, booking_id)
        return {"ok": True, **_booking_view(b, self.clock().date())}

    def t_get_rooms(self, s: CallSession, type: str | None = None) -> dict:
        if s.business["type"] != "hostel":
            return {"ok": False, "error": "not_a_hostel"}
        integ = self.repo.get_integration(s.business_id, "hostix")
        try:
            rooms = self.get_rooms_fn(integ, type)
        except hostix.HostixError as e:
            log.warning("hostix: %s", e)
            return {"ok": False, "error": "rooms_unavailable",
                    "instruction": "Say you can't check rooms right now and offer to take a message."}
        return {"ok": True, "rooms": rooms}

    def t_transfer(self, s: CallSession, reason: str = "other") -> dict:
        biz = s.business
        staff = biz.get("staff_phone")
        if not staff or not timeutil.is_open(biz.get("timings") or {}, self.clock()):
            return {"ok": False, "error": "staff_unavailable",
                    "instruction": "Staff can't take calls right now. Offer to take a message; they will call back."}
        if not s.control.transfer(staff, None):
            return {"ok": False, "error": "staff_unavailable", "instruction": "Offer to take a message."}
        s.transferred = True
        return {"ok": True, "instruction": "Say one short line that you are connecting them now."}

    def t_take_message(self, s: CallSession, message: str, name: str | None = None,
                       phone: str | None = None) -> dict:
        phone = timeutil.normalize_phone(phone) or s.caller_phone
        self.repo.add_message(s.business_id, name=name, phone=phone, text=message)
        s.message_taken = True
        owner = s.business.get("owner_whatsapp")
        if owner:
            self.wa.send_text(owner, f"Awaaz Desk: naya paigham\n{name or 'Caller'} ({phone or 'number nahi'}): {message}")
        return {"ok": True, "instruction": "Tell the caller staff will get back to them."}

    def t_end_call(self, s: CallSession, goodbye: str) -> dict:
        s.ended = True
        s.control.end_call(goodbye)
        return {"ok": True}
