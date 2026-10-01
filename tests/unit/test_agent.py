"""Conversation flows from the spec, with Claude scripted and everything else real."""

import asyncio
import json
from datetime import date, time

from awaaz.agent.session import CallSession, NullControl
from conftest import NOW, run_turn, text, tool


def session_for(clinic, **kw):
    return CallSession(business=clinic, caller_phone="+923331234567", channel="voice", **kw)


def tool_results(engine):
    """tool_result payloads Claude was sent, in order."""
    out = []
    for req in engine.client.requests:
        last = req["messages"][-1]
        if last["role"] == "user" and isinstance(last["content"], list):
            out += [json.loads(r["content"]) for r in last["content"]]
    return out


def test_booking_flow_spec_script(repo, clinic, make_engine):
    engine, wa = make_engine([
        [tool("check_slots", date="2026-10-07")],
        [text("سبا 4 بجے خالی ہے۔ نام بتائیں؟")],
        [tool("book", name="Imran", date="2026-10-07", time="16:00", service="checkup")],
        [text("عمران صاحب، کل 4 بجے بکنگ ہو گئی۔ واٹس ایپ آ جائے گا۔")],
    ])
    s = session_for(clinic)
    first = asyncio.run(run_turn(engine, s, "da ghakh dard de, appointment ghwaram"))
    assert first.startswith("جی، ایک سیکنڈ")          # filler while the tool ran
    asyncio.run(run_turn(engine, s, "4 bajay, Imran"))

    slots, booked = tool_results(engine)
    assert slots["ok"] and slots["free"][0]["time"] == "10:00"
    assert booked["ok"] and booked["time"] == "16:00" and booked["whatsapp_sent"]
    assert s.outcome() == "booked"
    [b] = repo.upcoming_bookings(clinic["id"], "+923331234567", NOW.date())
    assert b["caller_name"] == "Imran" and b["start_time"] == time(16, 0)
    assert wa.outbox[0]["to"] == "923331234567"


def test_system_prompt_has_date_and_business(clinic, make_engine):
    engine, _ = make_engine([[text("جی")]])
    asyncio.run(run_turn(engine, session_for(clinic), "hello"))
    req = engine.client.requests[0]
    stable, context = req["system"][0]["text"], req["system"][1]["text"]
    assert "Dr. Khan Dental Clinic" in stable and "University Road" in stable and "1500" in stable
    assert "Tuesday 2026-10-06" in context and "closed" in context
    assert {t["name"] for t in req["tools"]} >= {"book", "transfer", "end_call"}


def test_transfer_after_hours_falls_back_to_message(repo, clinic, make_engine):
    control = NullControl()
    engine, wa = make_engine([
        [tool("transfer", reason="asked_for_human")],
        [text("اسٹاف ابھی دستیاب نہیں، پیغام لے لوں؟")],
        [tool("take_message", name="Asad", message="Call back about braces price")],
        [text("ٹھیک ہے، اسٹاف آپ کو کال کرے گا۔")],
    ])
    s = session_for(clinic, control=control)
    asyncio.run(run_turn(engine, s, "insaan se baat karni hai"))
    asyncio.run(run_turn(engine, s, "haan, Asad, braces ki price"))
    transfer, msg = tool_results(engine)
    assert transfer["error"] == "staff_unavailable" and control.transferred_to is None
    assert msg["ok"] and s.outcome() == "message"
    assert repo.list_messages(clinic["id"])[0]["text"] == "Call back about braces price"
    assert wa.outbox[-1]["to"] == "923001110001"   # owner notified


def test_transfer_during_hours(repo, clinic, make_engine, monkeypatch):
    from awaaz.agent import tools as tools_mod
    control = NullControl()
    engine, _ = make_engine([[tool("transfer", reason="angry")], [text("جوڑ رہی ہوں۔")]])
    engine.toolbox.clock = lambda: NOW.replace(hour=11)
    s = session_for(clinic, control=control)
    asyncio.run(run_turn(engine, s, "bohat bura service hai!"))
    assert control.transferred_to == "+923001110001" and s.outcome() == "transferred"


def test_no_slots_offers_next_day(repo, clinic, make_engine):
    engine, _ = make_engine([[tool("check_slots", date="2026-10-09")], [text("جمعہ بند ہے۔")]])
    asyncio.run(run_turn(engine, session_for(clinic), "juma ko"))
    [res] = tool_results(engine)
    assert res["free"] == [] and res["next_available"]["date"] == "2026-10-10"


def test_double_booking_rejected(repo, clinic, make_engine):
    repo.book(clinic["id"], name="X", phone="+923000000000", d=date(2026, 10, 7), t=time(16, 0), service=None)
    engine, _ = make_engine([[tool("book", name="Imran", date="2026-10-07", time="16:00")], [text("یہ وقت بک ہے۔")]])
    s = session_for(clinic)
    asyncio.run(run_turn(engine, s, "4 bajay"))
    [res] = tool_results(engine)
    assert res["error"] == "slot_not_available" and "16:30" in res["free_same_day"]
    assert s.outcome() != "booked"


def test_cannot_touch_someone_elses_booking(repo, clinic, make_engine):
    b = repo.book(clinic["id"], name="X", phone="+923000000000", d=date(2026, 10, 7), t=time(16, 0), service=None)
    engine, _ = make_engine([[tool("cancel", booking_id=str(b["id"]))], [text("نہیں ملی۔")]])
    asyncio.run(run_turn(engine, session_for(clinic), "cancel"))
    assert tool_results(engine)[0]["error"] == "booking_not_found"
    assert repo.get_booking(clinic["id"], b["id"])["status"] == "confirmed"


def test_reschedule_flow(repo, clinic, make_engine):
    b = repo.book(clinic["id"], name="Imran", phone="+923331234567", d=date(2026, 10, 7), t=time(16, 0), service=None)
    engine, _ = make_engine([
        [tool("find_booking")], [text("کل 4 بجے والی؟")],
        [tool("reschedule", booking_id=str(b["id"]), date="2026-10-08", time="11:00")], [text("ہو گیا۔")],
    ])
    s = session_for(clinic)
    asyncio.run(run_turn(engine, s, "time change karna hai"))
    asyncio.run(run_turn(engine, s, "jumeraat 11 bajay"))
    found, moved = tool_results(engine)
    assert found["bookings"][0]["booking_id"] == str(b["id"])
    assert moved["ok"] and moved["date"] == "2026-10-08" and moved["time"] == "11:00"


def test_end_call_hangs_up_without_extra_speech(clinic, make_engine):
    control = NullControl()
    engine, _ = make_engine([[tool("end_call", goodbye="اللہ حافظ")]])
    s = session_for(clinic, control=control)
    out = asyncio.run(run_turn(engine, s, "galat number"))
    assert out == "" and control.ended_with == "اللہ حافظ" and s.ended


def test_bad_tool_input_is_an_error_not_a_crash(clinic, make_engine):
    engine, _ = make_engine([[tool("book", name="Imran", date="kal", time="4")], [text("تاریخ؟")]])
    asyncio.run(run_turn(engine, session_for(clinic), "kal"))
    assert "YYYY-MM-DD" in tool_results(engine)[0]["error"]


def test_claude_outage_says_fallback(clinic, make_engine):
    import anthropic, httpx
    engine, _ = make_engine([])

    def boom(**kw):
        raise anthropic.APIConnectionError(request=httpx.Request("POST", "https://api.anthropic.com"))
    engine.client.stream = boom
    s = session_for(clinic)
    out = asyncio.run(run_turn(engine, s, "hello"))
    assert "اسٹاف" in out and s.messages == []


def test_hostel_rooms_from_hostix(repo, make_engine, monkeypatch):
    from awaaz.onboarding import onboard
    import conftest
    monkeypatch.setattr("awaaz.timeutil.now", lambda: NOW)
    hostel, _ = onboard(repo, {"name": "Al-Noor Boys Hostel", "type": "hostel", "city": "Peshawar",
                               "hostix": {"base_url": "https://hostix.test", "api_key_ref": "env:X"}})
    engine, _ = make_engine([[tool("get_rooms", type="2 seater")], [text("جی، 2 سیٹر خالی ہے۔")]])
    seen = {}

    def fake_rooms(integ, room_type):
        seen.update(integ=integ, type=room_type)
        return [{"type": "2 seater", "available": 1, "fee_monthly": 12000, "meals_included": True}]
    engine.toolbox.get_rooms_fn = fake_rooms
    asyncio.run(run_turn(engine, CallSession(business=hostel, caller_phone="+923330000000"), "room khali hai?"))
    assert tool_results(engine)[0]["rooms"][0]["fee_monthly"] == 12000
    assert seen["integ"]["base_url"] == "https://hostix.test" and seen["type"] == "2 seater"


def test_barge_in_during_filler_keeps_history_valid(clinic, make_engine):
    """Caller interrupts right after the filler: tool_use must still get a result."""
    engine, _ = make_engine([[tool("check_slots", date="2026-10-07")], [text("جی؟")]])
    s = session_for(clinic)

    async def interrupted():
        gen = engine.respond(s, "appointment")
        assert (await gen.__anext__()).startswith("جی، ایک سیکنڈ")   # filler, then hang up on it
        await gen.aclose()
        return await run_turn(engine, s, "hello?")
    assert asyncio.run(interrupted()) == "جی؟"
    roles = [m["role"] for m in s.messages]
    assert roles == ["user", "assistant", "user", "user", "assistant"]
    assert json.loads(s.messages[2]["content"][0]["content"])["error"] == "interrupted"


def test_barge_in_during_tool_keeps_booking(repo, clinic, make_engine):
    engine, _ = make_engine([[text("ٹھیک ہے۔"), tool("book", name="Imran", date="2026-10-07", time="16:00")]])
    s = session_for(clinic)
    real_run = engine.toolbox.run

    def slow_run(*a):
        import time as _t
        _t.sleep(0.2)
        return real_run(*a)
    engine.toolbox.run = slow_run

    async def interrupted():
        task = asyncio.ensure_future(run_turn(engine, s, "book karo"))
        await asyncio.sleep(0.05)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
    asyncio.run(interrupted())
    assert s.booked and repo.upcoming_bookings(clinic["id"], "+923331234567", NOW.date())
    assert json.loads(s.messages[-1]["content"][0]["content"])["ok"]
