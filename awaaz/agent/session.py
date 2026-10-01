"""Per-call state and the telephony controls the agent can use."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol

from .. import timeutil


class CallControl(Protocol):
    """What the agent can do to a live call. Implemented by telephony/vapi.py;
    the text simulator and tests use NullControl."""

    def transfer(self, number: str, announce: str | None = None) -> bool: ...
    def end_call(self, goodbye: str) -> bool: ...


class NullControl:
    def __init__(self) -> None:
        self.transferred_to: str | None = None
        self.ended_with: str | None = None

    def transfer(self, number: str, announce: str | None = None) -> bool:
        self.transferred_to = number
        return True

    def end_call(self, goodbye: str) -> bool:
        self.ended_with = goodbye
        return True


@dataclass
class CallSession:
    business: dict
    caller_phone: str | None = None
    provider_call_id: str | None = None
    channel: str = "voice"   # "voice" (TTS reads replies) or "text" (simulator)
    control: CallControl = field(default_factory=NullControl)
    started_at: datetime = field(default_factory=timeutil.now)
    messages: list = field(default_factory=list)      # Claude message history
    user_texts: list[str] = field(default_factory=list)
    booked: bool = False
    transferred: bool = False
    message_taken: bool = False
    ended: bool = False
    tool_errors: int = 0
    lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)

    @property
    def business_id(self):
        return self.business["id"]

    @property
    def after_hours(self) -> bool:
        return not timeutil.is_open(self.business.get("timings") or {}, self.started_at)

    def outcome(self) -> str:
        if self.booked:
            return "booked"
        if self.transferred:
            return "transferred"
        if self.message_taken:
            return "message"
        return "faq" if self.user_texts else "dropped"
