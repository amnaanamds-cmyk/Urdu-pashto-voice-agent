"""Owner dashboard (spec §9): Today, Calendar, Calls, Messages, Settings, Billing.

Login is a per-business owner token issued at onboarding
(`python -m awaaz onboard` prints it). Every page is scoped to the logged-in
business. Recordings are proxied through the server so the provider's
recording URL is never shown to the browser.
"""

from __future__ import annotations

import asyncio
import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import httpx
from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.templating import Jinja2Templates

from .. import timeutil
from ..agent.tools import _spoken_time
from ..repo import NotFound, SlotTaken

router = APIRouter()
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


def _pkt(dt: datetime | None, fmt: str = "%d %b %H:%M") -> str:
    """Stored timestamps are UTC (SQLite returns them naive); show Pakistan time."""
    if dt is None:
        return "—"
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timeutil.PKT).strftime(fmt)


templates.env.filters["pkt"] = _pkt

OUTCOME_LABEL = {"booked": "Booked", "faq": "Answered", "transferred": "Transferred",
                 "message": "Message", "dropped": "Dropped", None: "—"}
LANG_LABEL = {"ps": "Pashto", "ur": "Urdu", "en": "English", "mixed": "Mixed", None: "—"}


def _svc(request: Request):
    return request.app.state.svc


def _business(request: Request) -> dict:
    bid = request.session.get("business_id")
    if not bid:
        raise HTTPException(303, headers={"Location": "/login"})
    try:
        return _svc(request).repo.get_business(bid)
    except NotFound:
        request.session.clear()
        raise HTTPException(303, headers={"Location": "/login"})


def _render(request: Request, name: str, biz: dict | None, **ctx) -> HTMLResponse:
    return templates.TemplateResponse(request, name, {
        "biz": biz, "outcome_label": OUTCOME_LABEL, "lang_label": LANG_LABEL,
        "spoken": _spoken_time, "page": name.removesuffix(".html"), **ctx})


@router.get("/login", response_class=HTMLResponse)
def login_form(request: Request, token: str | None = None):
    if token:
        return login(request, token)
    return _render(request, "login.html", None, error=None)


@router.post("/login")
def login(request: Request, token: str = Form(...)):
    biz = _svc(request).repo.business_by_token(token.strip())
    if not biz:
        return _render(request, "login.html", None, error="Token not recognised.")
    request.session["business_id"] = str(biz["id"])
    return RedirectResponse("/", status_code=303)


@router.post("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=303)


@router.get("/", response_class=HTMLResponse)
async def today(request: Request):
    biz = _business(request)
    repo = _svc(request).repo
    d = timeutil.now().date()
    stats = await asyncio.to_thread(repo.day_stats, biz["id"], d)
    upcoming = await asyncio.to_thread(repo.bookings_between, biz["id"], d, d)
    calls = (await asyncio.to_thread(repo.list_calls, biz["id"], 8))
    return _render(request, "today.html", biz, stats=stats, day=d,
                   bookings=[b for b in upcoming if b["status"] == "confirmed"], calls=calls)


@router.get("/calendar", response_class=HTMLResponse)
async def calendar(request: Request, start: str | None = None):
    biz = _business(request)
    repo = _svc(request).repo
    d0 = date.fromisoformat(start) if start else timeutil.now().date()
    days = [d0 + timedelta(i) for i in range(7)]
    rows = await asyncio.to_thread(repo.bookings_between, biz["id"], days[0], days[-1])
    by_day = {d: [b for b in rows if b["date"] == d] for d in days}
    free = {d: await asyncio.to_thread(repo.free_times, biz["id"], d) for d in days}
    return _render(request, "calendar.html", biz, days=days, by_day=by_day, free=free,
                   prev=(d0 - timedelta(7)).isoformat(), next=(d0 + timedelta(7)).isoformat(), error=None)


@router.post("/bookings/{booking_id}/cancel")
async def cancel_booking(request: Request, booking_id: str):
    biz = _business(request)
    await asyncio.to_thread(_svc(request).repo.cancel, biz["id"], booking_id)
    return RedirectResponse("/calendar", status_code=303)


@router.post("/bookings/{booking_id}/move")
async def move_booking(request: Request, booking_id: str, new_date: str = Form(...), new_time: str = Form(...)):
    biz = _business(request)
    try:
        await asyncio.to_thread(_svc(request).repo.reschedule, biz["id"], booking_id,
                                date.fromisoformat(new_date), timeutil.parse_hhmm(new_time))
    except (SlotTaken, NotFound, ValueError):
        return RedirectResponse(f"/calendar?start={new_date}", status_code=303)
    return RedirectResponse(f"/calendar?start={new_date}", status_code=303)


@router.post("/bookings")
async def add_booking(request: Request, name: str = Form(...), phone: str = Form(...),
                      day: str = Form(...), at: str = Form(...)):
    biz = _business(request)
    try:
        await asyncio.to_thread(_svc(request).repo.book, biz["id"], name=name, phone=phone,
                                d=date.fromisoformat(day), t=timeutil.parse_hhmm(at),
                                service=None, source="dashboard")
    except (SlotTaken, ValueError):
        pass
    return RedirectResponse(f"/calendar?start={day}", status_code=303)


@router.get("/calls", response_class=HTMLResponse)
async def calls(request: Request):
    biz = _business(request)
    rows = await asyncio.to_thread(_svc(request).repo.list_calls, biz["id"], 200)
    return _render(request, "calls.html", biz, calls=rows)


@router.get("/calls/{call_id}", response_class=HTMLResponse)
async def call_detail(request: Request, call_id: str):
    biz = _business(request)
    try:
        call = await asyncio.to_thread(_svc(request).repo.get_call, biz["id"], call_id)
    except NotFound:
        raise HTTPException(404)
    return _render(request, "call.html", biz, call=call)


@router.get("/calls/{call_id}/recording")
async def call_recording(request: Request, call_id: str):
    biz = _business(request)
    try:
        call = await asyncio.to_thread(_svc(request).repo.get_call, biz["id"], call_id)
    except NotFound:
        raise HTTPException(404)
    url = call.get("recording_url")
    if not url:
        raise HTTPException(404, "no recording (expired or not recorded)")
    client = httpx.AsyncClient(timeout=30, follow_redirects=True)
    upstream = await client.send(client.build_request("GET", url), stream=True)
    if upstream.status_code >= 400:
        await upstream.aclose()
        await client.aclose()
        raise HTTPException(502, "recording unavailable")

    async def body():
        try:
            async for chunk in upstream.aiter_bytes():
                yield chunk
        finally:
            await upstream.aclose()
            await client.aclose()

    return StreamingResponse(body(), media_type=upstream.headers.get("content-type", "audio/wav"),
                             headers={"Cache-Control": "private, no-store"})


@router.get("/messages", response_class=HTMLResponse)
async def messages(request: Request):
    biz = _business(request)
    rows = await asyncio.to_thread(_svc(request).repo.list_messages, biz["id"])
    return _render(request, "messages.html", biz, messages=rows)


@router.post("/messages/{message_id}/handled")
async def message_handled(request: Request, message_id: str):
    biz = _business(request)
    await asyncio.to_thread(_svc(request).repo.set_message_handled, biz["id"], message_id, True)
    return RedirectResponse("/messages", status_code=303)


@router.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request, saved: int = 0):
    biz = _business(request)
    return _render(request, "settings.html", biz, saved=bool(saved), error=None,
                   faq=json.dumps(biz["faq_json"], ensure_ascii=False, indent=2),
                   timings=json.dumps(biz["timings"], ensure_ascii=False, indent=2),
                   voice=json.dumps(biz["voice"], ensure_ascii=False, indent=2))


@router.post("/settings")
def settings_save(request: Request, faq: str = Form("{}"), timings: str = Form("{}"), voice: str = Form("{}"),
                  staff_phone: str = Form(""), owner_whatsapp: str = Form(""),
                  recording_retention_days: int = Form(60)):
    biz = _business(request)
    try:
        faq_j, timings_j, voice_j = json.loads(faq or "{}"), json.loads(timings or "{}"), json.loads(voice or "{}")
        for day, ranges in (timings_j.get("days") or {}).items():
            if day not in timeutil.DAY_KEYS:
                raise ValueError(f"unknown day {day!r}; use mon..sun")
            for a, b in ranges:
                timeutil.parse_hhmm(a), timeutil.parse_hhmm(b)
        if not 1 <= recording_retention_days <= 90:
            raise ValueError("recording retention must be 1-90 days")
    except (ValueError, TypeError) as e:
        return _render(request, "settings.html", biz, saved=False, error=str(e),
                       faq=faq, timings=timings, voice=voice)
    _svc(request).repo.update_business(
        biz["id"], faq_json=faq_j, timings=timings_j, voice=voice_j,
        staff_phone=staff_phone or None, owner_whatsapp=owner_whatsapp or None,
        recording_retention_days=recording_retention_days)
    return RedirectResponse("/settings?saved=1", status_code=303)


@router.get("/billing", response_class=HTMLResponse)
async def billing(request: Request):
    biz = _business(request)
    repo = _svc(request).repo
    m = timeutil.now().date().replace(day=1)
    prev = (m - timedelta(days=1)).replace(day=1)
    used = await asyncio.to_thread(repo.usage_for, biz["id"], m)
    used_prev = await asyncio.to_thread(repo.usage_for, biz["id"], prev)
    return _render(request, "billing.html", biz, month=m, used=used, prev=prev, used_prev=used_prev)
