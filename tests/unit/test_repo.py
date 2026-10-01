from datetime import date, time

import pytest

from awaaz.repo import SlotTaken
from conftest import NOW


def test_slots_generated_from_timings(repo, clinic):
    wed = date(2026, 10, 7)
    times = repo.free_times(clinic["id"], wed, NOW)
    assert times[0] == time(10, 0) and times[-1] == time(19, 30)
    assert len(times) == 16
    assert repo.free_times(clinic["id"], date(2026, 10, 9), NOW) == []   # Friday closed


def test_today_hides_past_slots(repo, clinic):
    assert repo.free_times(clinic["id"], NOW.date(), NOW) == []          # 21:30, all gone


def test_book_claims_slot_once(repo, clinic):
    d = date(2026, 10, 7)
    b = repo.book(clinic["id"], name="Imran", phone="0300-1234567", d=d, t=time(16, 0), service="checkup")
    assert b["caller_phone"] == "+923001234567" and b["service"] == "Checkup"
    assert time(16, 0) not in repo.free_times(clinic["id"], d, NOW)
    with pytest.raises(SlotTaken):
        repo.book(clinic["id"], name="Other", phone="+923009999999", d=d, t=time(16, 0), service=None)


def test_reschedule_and_cancel_free_slots(repo, clinic):
    d = date(2026, 10, 7)
    b = repo.book(clinic["id"], name="Imran", phone="+923001234567", d=d, t=time(16, 0), service=None)
    repo.reschedule(clinic["id"], b["id"], d, time(17, 0))
    free = repo.free_times(clinic["id"], d, NOW)
    assert time(16, 0) in free and time(17, 0) not in free
    repo.cancel(clinic["id"], b["id"])
    assert time(17, 0) in repo.free_times(clinic["id"], d, NOW)


def test_tenant_isolation(repo, clinic):
    from awaaz.onboarding import onboard
    from awaaz.repo import NotFound
    other, _ = onboard(repo, {"name": "Other", "type": "shop", "city": "Mardan"})
    b = repo.book(clinic["id"], name="Imran", phone="+923001234567", d=date(2026, 10, 7), t=time(16, 0), service=None)
    with pytest.raises(NotFound):
        repo.get_booking(other["id"], b["id"])
    with pytest.raises(NotFound):
        repo.cancel(other["id"], b["id"])


def test_usage_accumulates(repo, clinic):
    repo.add_usage(clinic["id"], date(2026, 10, 6), 1.5)
    repo.add_usage(clinic["id"], date(2026, 10, 20), 2.0)
    assert repo.usage_for(clinic["id"], date(2026, 10, 1)) == 3.5
