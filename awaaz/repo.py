"""Data access. Every function that touches tenant data takes business_id and
scopes its queries by it, so a booking id from one business can never be read
or changed through another business's call."""

from __future__ import annotations

import difflib
import hashlib
import uuid
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal

import sqlalchemy as sa

from . import db, timeutil


class NotFound(Exception):
    pass


class SlotTaken(Exception):
    pass


def _row(r) -> dict | None:
    return dict(r._mapping) if r is not None else None


def _uuid(v) -> uuid.UUID:
    if isinstance(v, uuid.UUID):
        return v
    try:
        return uuid.UUID(str(v))
    except ValueError as e:
        raise NotFound(f"bad id: {v}") from e


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


@dataclass
class Repo:
    engine: sa.Engine

    # --- Businesses -------------------------------------------------------

    def create_business(self, **fields) -> dict:
        fields.setdefault("id", uuid.uuid4())
        for k in ("ai_phone", "staff_phone", "owner_whatsapp"):
            if fields.get(k):
                fields[k] = timeutil.normalize_phone(fields[k])
        with self.engine.begin() as c:
            c.execute(db.businesses.insert().values(**fields))
        return self.get_business(fields["id"])

    def get_business(self, business_id) -> dict:
        with self.engine.connect() as c:
            r = c.execute(sa.select(db.businesses).where(db.businesses.c.id == _uuid(business_id))).first()
        if r is None:
            raise NotFound("business")
        return _row(r)

    def business_by_ai_phone(self, phone: str | None) -> dict | None:
        phone = timeutil.normalize_phone(phone)
        if not phone:
            return None
        with self.engine.connect() as c:
            return _row(c.execute(sa.select(db.businesses).where(db.businesses.c.ai_phone == phone)).first())

    def business_by_token(self, token: str) -> dict | None:
        with self.engine.connect() as c:
            return _row(c.execute(
                sa.select(db.businesses).where(db.businesses.c.owner_token_hash == hash_token(token))
            ).first())

    def list_businesses(self) -> list[dict]:
        with self.engine.connect() as c:
            return [_row(r) for r in c.execute(sa.select(db.businesses).order_by(db.businesses.c.created_at))]

    def update_business(self, business_id, **fields) -> dict:
        for k in ("ai_phone", "staff_phone", "owner_whatsapp"):
            if k in fields:
                fields[k] = timeutil.normalize_phone(fields[k])
        with self.engine.begin() as c:
            c.execute(db.businesses.update().where(db.businesses.c.id == _uuid(business_id)).values(**fields))
        return self.get_business(business_id)

    # --- Services ---------------------------------------------------------

    def add_service(self, business_id, name: str, duration_min: int = 30, price=None) -> dict:
        row = {"id": uuid.uuid4(), "business_id": _uuid(business_id), "name": name,
               "duration_min": duration_min, "price": price}
        with self.engine.begin() as c:
            c.execute(db.services.insert().values(**row))
        return row

    def list_services(self, business_id) -> list[dict]:
        with self.engine.connect() as c:
            return [_row(r) for r in c.execute(
                sa.select(db.services).where(db.services.c.business_id == _uuid(business_id))
                .order_by(db.services.c.name))]

    def find_service(self, business_id, name: str | None) -> dict | None:
        """Match what the caller/agent said to a configured service, tolerantly."""
        svcs = self.list_services(business_id)
        if not svcs:
            return None
        if not name:
            return svcs[0] if len(svcs) == 1 else None
        key = name.strip().lower()
        by_name = {s["name"].lower(): s for s in svcs}
        if key in by_name:
            return by_name[key]
        for n, s in by_name.items():
            if key in n or n in key:
                return s
        close = difflib.get_close_matches(key, list(by_name), n=1, cutoff=0.6)
        if close:
            return by_name[close[0]]
        return svcs[0] if len(svcs) == 1 else None

    # --- Slots ------------------------------------------------------------

    def ensure_slots(self, business_id, start: date, days: int = 14) -> int:
        """Create free slots from business timings for [start, start+days). Idempotent."""
        biz = self.get_business(business_id)
        bid = biz["id"]
        wanted = {(start + timedelta(d), t)
                  for d in range(days)
                  for t in timeutil.slot_starts(biz["timings"], start + timedelta(d))}
        if not wanted:
            return 0
        try:
            with self.engine.begin() as c:
                have = {(r.date, r.start_time) for r in c.execute(
                    sa.select(db.slots.c.date, db.slots.c.start_time).where(
                        db.slots.c.business_id == bid,
                        db.slots.c.date >= start, db.slots.c.date < start + timedelta(days)))}
                new = [{"id": uuid.uuid4(), "business_id": bid, "date": d, "start_time": t, "status": "free"}
                       for d, t in sorted(wanted - have)]
                if new:
                    c.execute(db.slots.insert(), new)
        except sa.exc.IntegrityError:
            return 0  # a concurrent request created them first
        return len(new)

    def free_times(self, business_id, d: date, now: datetime | None = None) -> list[time]:
        now = (now or timeutil.now()).astimezone(timeutil.PKT)
        self.ensure_slots(business_id, d, 1)
        with self.engine.connect() as c:
            times = [r.start_time for r in c.execute(
                sa.select(db.slots.c.start_time).where(
                    db.slots.c.business_id == _uuid(business_id), db.slots.c.date == d,
                    db.slots.c.status == "free").order_by(db.slots.c.start_time))]
        if d == now.date():
            cutoff = (now + timedelta(minutes=30)).time()   # don't offer a slot starting in 5 minutes
            times = [t for t in times if t >= cutoff]
        return times

    def next_free_day(self, business_id, after: date, horizon: int = 14,
                      now: datetime | None = None) -> tuple[date, list[time]] | None:
        for i in range(1, horizon + 1):
            d = after + timedelta(i)
            times = self.free_times(business_id, d, now)
            if times:
                return d, times
        return None

    def _claim_slot(self, c, bid, d: date, t: time) -> uuid.UUID:
        slot = c.execute(sa.select(db.slots.c.id, db.slots.c.status).where(
            db.slots.c.business_id == bid, db.slots.c.date == d, db.slots.c.start_time == t)).first()
        if slot is None:
            raise SlotTaken("no such slot")
        # Atomic claim: only one concurrent caller can flip free -> booked.
        res = c.execute(db.slots.update()
                        .where(db.slots.c.id == slot.id, db.slots.c.status == "free")
                        .values(status="booked"))
        if res.rowcount != 1:
            raise SlotTaken("slot already booked")
        return slot.id

    # --- Bookings ---------------------------------------------------------

    def book(self, business_id, *, name: str, phone: str, d: date, t: time,
             service: str | None, source: str = "call") -> dict:
        bid = _uuid(business_id)
        svc = self.find_service(bid, service)
        self.ensure_slots(bid, d, 1)
        with self.engine.begin() as c:
            slot_id = self._claim_slot(c, bid, d, t)
            row = {"id": uuid.uuid4(), "business_id": bid, "caller_name": name.strip(),
                   "caller_phone": timeutil.normalize_phone(phone) or phone,
                   "service_id": svc["id"] if svc else None, "slot_id": slot_id,
                   "status": "confirmed", "source": source}
            c.execute(db.bookings.insert().values(**row))
        return self.get_booking(bid, row["id"])

    def _booking_query(self):
        b, s, sv = db.bookings, db.slots, db.services
        return (sa.select(b, s.c.date, s.c.start_time, sv.c.name.label("service"))
                .select_from(b.outerjoin(s, b.c.slot_id == s.c.id).outerjoin(sv, b.c.service_id == sv.c.id)))

    def get_booking(self, business_id, booking_id) -> dict:
        q = self._booking_query().where(db.bookings.c.business_id == _uuid(business_id),
                                        db.bookings.c.id == _uuid(booking_id))
        with self.engine.connect() as c:
            r = c.execute(q).first()
        if r is None:
            raise NotFound("booking")
        return _row(r)

    def upcoming_bookings(self, business_id, phone: str, today: date | None = None) -> list[dict]:
        today = today or timeutil.now().date()
        q = self._booking_query().where(
            db.bookings.c.business_id == _uuid(business_id),
            db.bookings.c.caller_phone == (timeutil.normalize_phone(phone) or phone),
            db.bookings.c.status == "confirmed",
            db.slots.c.date >= today,
        ).order_by(db.slots.c.date, db.slots.c.start_time)
        with self.engine.connect() as c:
            return [_row(r) for r in c.execute(q)]

    def bookings_between(self, business_id, start: date, end: date) -> list[dict]:
        q = self._booking_query().where(
            db.bookings.c.business_id == _uuid(business_id),
            db.slots.c.date >= start, db.slots.c.date <= end,
        ).order_by(db.slots.c.date, db.slots.c.start_time)
        with self.engine.connect() as c:
            return [_row(r) for r in c.execute(q)]

    def reschedule(self, business_id, booking_id, d: date, t: time) -> dict:
        bid = _uuid(business_id)
        old = self.get_booking(bid, booking_id)
        if old["status"] != "confirmed":
            raise NotFound("booking is not active")
        self.ensure_slots(bid, d, 1)
        with self.engine.begin() as c:
            new_slot = self._claim_slot(c, bid, d, t)
            if old["slot_id"]:
                c.execute(db.slots.update().where(db.slots.c.id == old["slot_id"]).values(status="free"))
            c.execute(db.bookings.update().where(db.bookings.c.id == old["id"]).values(slot_id=new_slot))
        return self.get_booking(bid, booking_id)

    def cancel(self, business_id, booking_id) -> dict:
        bid = _uuid(business_id)
        old = self.get_booking(bid, booking_id)
        with self.engine.begin() as c:
            c.execute(db.bookings.update().where(db.bookings.c.id == old["id"]).values(status="cancelled"))
            if old["slot_id"]:
                c.execute(db.slots.update().where(db.slots.c.id == old["slot_id"]).values(status="free"))
        return self.get_booking(bid, booking_id)

    # --- Messages ---------------------------------------------------------

    def add_message(self, business_id, *, name: str | None, phone: str | None, text: str) -> dict:
        row = {"id": uuid.uuid4(), "business_id": _uuid(business_id), "caller_name": name,
               "caller_phone": timeutil.normalize_phone(phone) or phone, "text": text, "handled": False}
        with self.engine.begin() as c:
            c.execute(db.messages.insert().values(**row))
        return row

    def list_messages(self, business_id, only_open: bool = False, limit: int = 100) -> list[dict]:
        q = sa.select(db.messages).where(db.messages.c.business_id == _uuid(business_id))
        if only_open:
            q = q.where(db.messages.c.handled.is_(False))
        with self.engine.connect() as c:
            return [_row(r) for r in c.execute(q.order_by(db.messages.c.created_at.desc()).limit(limit))]

    def set_message_handled(self, business_id, message_id, handled: bool = True) -> None:
        with self.engine.begin() as c:
            c.execute(db.messages.update().where(
                db.messages.c.business_id == _uuid(business_id), db.messages.c.id == _uuid(message_id)
            ).values(handled=handled))

    # --- Calls & usage ----------------------------------------------------

    def record_call(self, business_id, **fields) -> dict:
        """Insert or update a call by provider_call_id."""
        bid = _uuid(business_id)
        pcid = fields.get("provider_call_id")
        with self.engine.begin() as c:
            existing = None
            if pcid:
                existing = c.execute(sa.select(db.calls.c.id).where(
                    db.calls.c.business_id == bid, db.calls.c.provider_call_id == pcid)).first()
            if existing:
                c.execute(db.calls.update().where(db.calls.c.id == existing.id).values(**fields))
                cid = existing.id
            else:
                cid = uuid.uuid4()
                c.execute(db.calls.insert().values(id=cid, business_id=bid, **fields))
        with self.engine.connect() as c:
            return _row(c.execute(sa.select(db.calls).where(db.calls.c.id == cid)).first())

    def list_calls(self, business_id, limit: int = 100) -> list[dict]:
        with self.engine.connect() as c:
            return [_row(r) for r in c.execute(
                sa.select(db.calls).where(db.calls.c.business_id == _uuid(business_id))
                .order_by(db.calls.c.started_at.desc()).limit(limit))]

    def get_call(self, business_id, call_id) -> dict:
        with self.engine.connect() as c:
            r = c.execute(sa.select(db.calls).where(
                db.calls.c.business_id == _uuid(business_id), db.calls.c.id == _uuid(call_id))).first()
        if r is None:
            raise NotFound("call")
        return _row(r)

    def add_usage(self, business_id, month: date, minutes: float) -> None:
        bid, month = _uuid(business_id), month.replace(day=1)
        with self.engine.begin() as c:
            res = c.execute(db.usage.update().where(
                db.usage.c.business_id == bid, db.usage.c.month == month
            ).values(minutes_used=db.usage.c.minutes_used + Decimal(str(round(minutes, 2)))))
            if res.rowcount == 0:
                c.execute(db.usage.insert().values(business_id=bid, month=month,
                                                   minutes_used=Decimal(str(round(minutes, 2)))))

    def usage_for(self, business_id, month: date) -> float:
        with self.engine.connect() as c:
            v = c.execute(sa.select(db.usage.c.minutes_used).where(
                db.usage.c.business_id == _uuid(business_id), db.usage.c.month == month.replace(day=1))).scalar()
        return float(v or 0)

    def day_stats(self, business_id, d: date) -> dict:
        """Calls/bookings for one PKT day. Used by the dashboard and daily WhatsApp summary."""
        start = datetime.combine(d, time(0), timeutil.PKT).astimezone(timezone.utc)
        end = start + timedelta(days=1)
        bid = _uuid(business_id)
        with self.engine.connect() as c:
            calls = c.execute(sa.select(db.calls.c.outcome, db.calls.c.after_hours).where(
                db.calls.c.business_id == bid, db.calls.c.started_at >= start,
                db.calls.c.started_at < end)).all()
            booked = c.execute(sa.select(sa.func.count()).select_from(db.bookings).where(
                db.bookings.c.business_id == bid, db.bookings.c.created_at >= start,
                db.bookings.c.created_at < end, db.bookings.c.status != "cancelled")).scalar()
            open_msgs = c.execute(sa.select(sa.func.count()).select_from(db.messages).where(
                db.messages.c.business_id == bid, db.messages.c.handled.is_(False))).scalar()
        return {
            "calls": len(calls),
            "bookings": int(booked or 0),
            "after_hours": sum(1 for r in calls if r.after_hours),
            # "Missed-then-saved": after-hours calls the AI turned into a booking or message.
            "saved": sum(1 for r in calls if r.after_hours and r.outcome in ("booked", "message")),
            "transferred": sum(1 for r in calls if r.outcome == "transferred"),
            "open_messages": int(open_msgs or 0),
        }

    def expired_recordings(self, now: datetime | None = None) -> list[dict]:
        now = now or datetime.now(timezone.utc)
        out = []
        for biz in self.list_businesses():
            cutoff = now - timedelta(days=int(biz["recording_retention_days"]))
            with self.engine.connect() as c:
                out += [_row(r) for r in c.execute(
                    sa.select(db.calls.c.id, db.calls.c.business_id, db.calls.c.provider_call_id).where(
                        db.calls.c.business_id == biz["id"], db.calls.c.started_at < cutoff,
                        sa.or_(db.calls.c.recording_url.is_not(None), db.calls.c.transcript.is_not(None))))]
        return out

    def clear_recording(self, call_id) -> None:
        with self.engine.begin() as c:
            c.execute(db.calls.update().where(db.calls.c.id == _uuid(call_id))
                      .values(recording_url=None, transcript=None))

    # --- Integrations -----------------------------------------------------

    def set_integration(self, business_id, type_: str, api_key_ref: str, base_url: str | None = None) -> None:
        bid = _uuid(business_id)
        with self.engine.begin() as c:
            c.execute(db.integrations.delete().where(
                db.integrations.c.business_id == bid, db.integrations.c.type == type_))
            c.execute(db.integrations.insert().values(
                business_id=bid, type=type_, api_key_ref=api_key_ref, base_url=base_url))

    def get_integration(self, business_id, type_: str) -> dict | None:
        with self.engine.connect() as c:
            return _row(c.execute(sa.select(db.integrations).where(
                db.integrations.c.business_id == _uuid(business_id), db.integrations.c.type == type_)).first())
