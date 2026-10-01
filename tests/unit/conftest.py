"""Shared fixtures: a real SQLite (or Postgres via AWAAZ_TEST_PG) database, a
fixed clock, and a scripted stand-in for the Claude client."""

from __future__ import annotations

import os
import uuid
from dataclasses import dataclass, field
from datetime import datetime

import pytest
import sqlalchemy as sa

from awaaz import db, timeutil
from awaaz.agent.engine import Engine
from awaaz.agent.tools import ToolBox
from awaaz.config import Settings
from awaaz.integrations.whatsapp import WhatsApp
from awaaz.onboarding import onboard
from awaaz.repo import Repo

# Tuesday 2026-10-06 21:30 PKT: clinic closed (after hours).
NOW = datetime(2026, 10, 6, 21, 30, tzinfo=timeutil.PKT)

CLINIC = {
    "name": "Dr. Khan Dental Clinic", "type": "clinic", "city": "Peshawar",
    "languages": ["ur", "ps"], "ai_phone": "+92915550101",
    "staff_phone": "+923001110001", "owner_whatsapp": "+923001110001",
    "timings": {"slot_minutes": 30, "days": {d: [["10:00", "14:00"], ["16:00", "20:00"]]
                                            for d in ["mon", "tue", "wed", "thu", "sat"]}},
    "services": [{"name": "Checkup", "duration_min": 30, "price": 1500}],
    "faq": {"address": "University Road"},
}


@pytest.fixture
def repo(tmp_path):
    pg = os.environ.get("AWAAZ_TEST_PG")
    if pg:
        # Postgres with tests/pg_auth_stub.sql + supabase/migrations applied.
        engine = db.make_engine(pg)
        with engine.begin() as c:
            for t in reversed(db.metadata.sorted_tables):
                c.execute(sa.text(f'delete from "{t.name}"'))
        return Repo(engine)
    return Repo(db.make_engine(f"sqlite:///{tmp_path / 'test.db'}"))


@pytest.fixture
def clinic(repo, monkeypatch):
    monkeypatch.setattr(timeutil, "now", lambda: NOW)
    biz, token = onboard(repo, dict(CLINIC))
    biz["_token"] = token
    return biz


@pytest.fixture
def settings():
    return Settings(database_url="sqlite://", webhook_secret="s3cret")


# --- Scripted Claude --------------------------------------------------------

@dataclass
class Block:
    type: str
    text: str = ""
    id: str = ""
    name: str = ""
    input: dict = field(default_factory=dict)


@dataclass
class Msg:
    content: list
    stop_reason: str


def text(t: str) -> Block:
    return Block("text", text=t)


def tool(tool_name: str, /, **inp) -> Block:
    return Block("tool_use", id=f"toolu_{uuid.uuid4().hex[:8]}", name=tool_name, input=inp)


class _Stream:
    def __init__(self, msg: Msg):
        self.msg = msg

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    @property
    async def text_stream(self):
        for b in self.msg.content:
            if b.type == "text":
                for word in b.text.split(" "):
                    yield word + " "

    async def get_final_message(self):
        return self.msg


class FakeClaude:
    """Returns pre-scripted assistant messages in order and records requests."""

    def __init__(self, script: list[list[Block]]):
        self.script = list(script)
        self.requests: list[dict] = []
        self.messages = self

    def stream(self, **kw):
        self.requests.append({**kw, "messages": list(kw["messages"])})   # snapshot
        blocks = self.script.pop(0)
        stop = "tool_use" if any(b.type == "tool_use" for b in blocks) else "end_turn"
        return _Stream(Msg(blocks, stop))


@pytest.fixture
def make_engine(repo, settings):
    def _make(script):
        wa = WhatsApp(settings)
        tb = ToolBox(repo, wa, clock=lambda: NOW)
        return Engine(settings, repo, tb, client=FakeClaude(script)), wa
    return _make


async def run_turn(engine, session, said: str) -> str:
    return "".join([c async for c in engine.respond(session, said)]).strip()
