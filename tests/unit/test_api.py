"""Vapi endpoints, dashboard and jobs over HTTP, with Claude scripted."""

import json
from datetime import date, time

import pytest
from fastapi.testclient import TestClient

from awaaz import jobs
from awaaz.api.app import Services, create_app
from awaaz.integrations.whatsapp import WhatsApp
from conftest import NOW, text, tool

H = {"x-awaaz-secret": "s3cret"}


@pytest.fixture
def app_for(repo, settings, make_engine):
    def _make(script):
        engine, wa = make_engine(script)
        svc = Services(settings, repo, wa, engine)
        return TestClient(create_app(svc)), svc
    return _make


def llm_body(clinic, said, call_id="call-1", control="https://control.test/c1"):
    return {
        "model": "x", "stream": True,
        "messages": [{"role": "assistant", "content": "السلام علیکم"}, {"role": "user", "content": said}],
        "metadata": {"business_id": str(clinic["id"])},
        "call": {"id": call_id, "monitor": {"controlUrl": control}},
        "customer": {"number": "+923331234567"},
    }


def sse_text(resp) -> str:
    out = []
    for line in resp.text.splitlines():
        if line.startswith("data: ") and line != "data: [DONE]":
            out.append(json.loads(line[6:])["choices"][0]["delta"].get("content", ""))
    return "".join(out)


def test_secret_required(clinic, app_for):
    client, _ = app_for([])
    assert client.post("/vapi/webhook", json={"message": {"type": "status-update"}}).status_code == 401


def test_assistant_request_by_dialled_number(clinic, app_for):
    client, _ = app_for([])
    r = client.post("/vapi/webhook", headers=H, json={"message": {
        "type": "assistant-request", "phoneNumber": {"number": "+92915550101"}}})
    a = r.json()["assistant"]
    assert a["model"]["provider"] == "custom-llm" and a["model"]["url"].endswith("/vapi/llm")
    assert a["model"]["headers"] == H and a["metadata"]["business_id"] == str(clinic["id"])
    assert "ریکارڈ" in a["firstMessage"]                     # recording notice (spec §11)
    assert a["monitorPlan"]["controlEnabled"] and a["artifactPlan"]["recordingEnabled"]
    unknown = client.post("/vapi/webhook", headers=H, json={"message": {
        "type": "assistant-request", "phoneNumber": {"number": "+920000000000"}}})
    assert "error" in unknown.json()


def test_full_call_booking_then_report(repo, clinic, app_for):
    client, svc = app_for([
        [tool("check_slots", date="2026-10-07")], [text("کل 4 بجے خالی ہے۔")],
        [tool("book", name="Imran", date="2026-10-07", time="16:00")], [text("بکنگ ہو گئی۔")],
    ])
    r1 = client.post("/vapi/llm/chat/completions", headers=H, json=llm_body(clinic, "appointment chahiye"))
    assert r1.headers["content-type"].startswith("text/event-stream")
    assert sse_text(r1).startswith("جی، ایک سیکنڈ") and r1.text.rstrip().endswith("data: [DONE]")
    r2 = client.post("/vapi/llm/chat/completions", headers=H, json=llm_body(clinic, "4 bajay, Imran"))
    assert "بکنگ" in sse_text(r2)

    report = {"message": {
        "type": "end-of-call-report", "endedReason": "customer-ended-call", "cost": 0.21,
        "startedAt": "2026-10-06T16:30:00Z", "endedAt": "2026-10-06T16:32:05Z",
        "artifact": {"recordingUrl": "https://storage.test/rec.wav", "transcript": "AI: ...\nUser: ..."},
        "call": {"id": "call-1"}, "customer": {"number": "+923331234567"}}}
    assert client.post("/vapi/webhook", headers=H, json=report).json() == {"ok": True}
    [call] = repo.list_calls(clinic["id"])
    assert call["outcome"] == "booked" and call["duration_sec"] == 125 and call["after_hours"]
    assert call["recording_url"] and float(call["cost_usd"]) == 0.21
    assert repo.usage_for(clinic["id"], date(2026, 10, 1)) == 2.1
    assert "call-1" not in svc.sessions
    stats = repo.day_stats(clinic["id"], NOW.date())
    assert stats["calls"] == 1 and stats["saved"] == 1
    # bookings.created_at uses the real clock, not the test's fixed NOW
    from datetime import datetime
    from awaaz.timeutil import PKT
    assert repo.day_stats(clinic["id"], datetime.now(PKT).date())["bookings"] == 1


def test_dashboard_login_and_pages(repo, clinic, app_for):
    client, _ = app_for([])
    assert client.get("/", follow_redirects=False).status_code == 303
    assert "not recognised" in client.post("/login", data={"token": "nope"}).text
    r = client.post("/login", data={"token": clinic["_token"]})
    assert r.status_code == 200 and "Dr. Khan Dental Clinic" in r.text
    repo.book(clinic["id"], name="Imran", phone="+923331234567", d=date(2026, 10, 7), t=time(16, 0), service=None)
    repo.add_message(clinic["id"], name="Asad", phone="+923000000001", text="call back")
    for path in ["/", "/calendar", "/calls", "/messages", "/settings", "/billing"]:
        assert client.get(path).status_code == 200, path
    assert "Imran" in client.get("/calendar").text
    assert "call back" in client.get("/messages").text


def test_dashboard_settings_validation(clinic, app_for):
    client, svc = app_for([])
    client.post("/login", data={"token": clinic["_token"]})
    bad = client.post("/settings", data={"timings": '{"days": {"monday": []}}', "faq": "{}", "voice": "{}"})
    assert "unknown day" in bad.text
    ok = client.post("/settings", data={"timings": '{"slot_minutes": 20, "days": {"mon": [["09:00","12:00"]]}}',
                                        "faq": '{"fee": "1500"}', "voice": "{}", "staff_phone": "03001234567",
                                        "recording_retention_days": "30"})
    assert ok.status_code == 200 and "Saved" in ok.text
    biz = svc.repo.get_business(clinic["id"])
    assert biz["staff_phone"] == "+923001234567" and biz["timings"]["slot_minutes"] == 20


def test_jobs_summary_and_purge(repo, clinic, settings):
    from datetime import datetime, timedelta, timezone
    wa = WhatsApp(settings)
    repo.record_call(clinic["id"], provider_call_id="old", recording_url="https://x/r.wav", transcript="t",
                     started_at=datetime.now(timezone.utc) - timedelta(days=61))
    repo.record_call(clinic["id"], provider_call_id="new", recording_url="https://x/r2.wav",
                     started_at=datetime.now(timezone.utc))
    assert jobs.purge_recordings(repo, settings) == 1         # default retention: 60 days
    calls = {c["provider_call_id"]: c for c in repo.list_calls(clinic["id"])}
    assert calls["old"]["recording_url"] is None and calls["new"]["recording_url"]
    assert jobs.send_summaries(repo, wa) == 1 and "Aaj" in wa.outbox[0]["text"]["body"]
