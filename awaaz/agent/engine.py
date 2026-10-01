"""The conversation engine: one caller turn in, streamed reply text out.

Streams Claude's reply as it is generated (latency budget, spec §6), runs tool
calls between rounds, and says a short filler while a tool runs so the line
never goes silent.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import AsyncIterator

import anthropic

from ..config import Settings
from ..repo import Repo
from . import prompt
from .session import CallSession
from .tools import TOOLS, ToolBox

log = logging.getLogger("awaaz.engine")

MAX_TOOL_ROUNDS = 4
NO_FILLER_TOOLS = {"end_call"}

# Said if Claude is unreachable. TODO(pashto-review): Pashto line.
FALLBACK = {
    ("voice", "ur"): "معذرت، ابھی سسٹم میں مسئلہ ہے۔ میں آپ کا پیغام اسٹاف تک پہنچا دیتی ہوں۔",
    ("voice", "ps"): "بښنه غواړم، اوس سیسټم کې ستونزه ده. ستاسو پیغام به کارکوونکو ته ورسوم.",
    ("voice", "en"): "Sorry, we're having a system problem. I'll pass your message to the staff.",
}


class Engine:
    def __init__(self, settings: Settings, repo: Repo, toolbox: ToolBox,
                 client: anthropic.AsyncAnthropic | None = None):
        self.s = settings
        self.repo = repo
        self.toolbox = toolbox
        self.client = client or anthropic.AsyncAnthropic(max_retries=1, timeout=15.0)

    def _system(self, session: CallSession) -> list[dict]:
        services = self.repo.list_services(session.business_id)
        return prompt.build_system(session.business, services, session.channel,
                                   session.caller_phone, now=self.toolbox.clock())

    async def respond(self, session: CallSession, user_text: str) -> AsyncIterator[str]:
        """Handle one caller utterance; yield reply text chunks as they stream.

        When the caller talks over the agent, Vapi drops the in-flight request
        and this generator is cancelled. The per-call lock keeps turns in
        order, and _repair() keeps the history valid for the next turn."""
        user_text = (user_text or "").strip()
        if not user_text:
            return
        async with session.lock:
            self._repair(session)
            async for piece in self._turn(session, user_text):
                yield piece

    async def _turn(self, session: CallSession, user_text: str) -> AsyncIterator[str]:
        session.user_texts.append(user_text)
        session.messages.append({"role": "user", "content": user_text})
        system = await asyncio.to_thread(self._system, session)
        lang = prompt.primary_language(session.business)

        for _ in range(MAX_TOOL_ROUNDS):
            spoke = False
            try:
                async with self.client.messages.stream(
                    model=self.s.model,
                    max_tokens=self.s.max_reply_tokens,
                    system=system,
                    tools=TOOLS,
                    messages=session.messages,
                ) as stream:
                    async for text in stream.text_stream:
                        if text:
                            spoke = True
                            yield text
                    final = await stream.get_final_message()
            except (anthropic.RateLimitError, anthropic.APIStatusError, anthropic.APIConnectionError) as e:
                log.error("claude call failed: %s", e)
                self._drop_dangling_user_turn(session)
                yield FALLBACK.get((session.channel, lang), FALLBACK[("voice", "ur")])
                return
            except Exception:
                # A live caller must never hear silence: anything else (bad
                # credentials, a bug) still gets the fallback line.
                log.exception("unexpected error in conversation turn")
                self._drop_dangling_user_turn(session)
                yield FALLBACK.get((session.channel, lang), FALLBACK[("voice", "ur")])
                return

            session.messages.append({"role": "assistant", "content": final.content})
            tool_uses = [b for b in final.content if b.type == "tool_use"]
            if final.stop_reason != "tool_use" or not tool_uses:
                return

            if not spoke and not any(t.name in NO_FILLER_TOOLS for t in tool_uses):
                yield prompt.FILLERS.get((session.channel, lang), prompt.FILLERS[("voice", "ur")])

            # Parallel tool calls: run them all, return every result in one user
            # message. Tools are not interruptible: if the caller barges in
            # mid-booking, the booking still completes and its result is kept.
            task = asyncio.ensure_future(asyncio.gather(*[
                asyncio.to_thread(self.toolbox.run, session, t.name, t.input) for t in tool_uses
            ]))
            try:
                results = await asyncio.shield(task)
            except asyncio.CancelledError:
                session.messages.append(_tool_results(tool_uses, await task))
                raise
            session.messages.append(_tool_results(tool_uses, results))
            log.info("tools %s -> %s", [t.name for t in tool_uses], [r.get("ok") for r in results])
            if session.ended:
                return   # end_call: the goodbye is spoken by the telephony layer

        log.warning("tool round limit hit for call %s", session.provider_call_id)

    @staticmethod
    def _repair(session: CallSession) -> None:
        """If the last turn was cut off between a tool_use and its result,
        answer the tool_use so the history stays valid."""
        if not session.messages or session.messages[-1]["role"] != "assistant":
            return
        tool_uses = [b for b in session.messages[-1]["content"]
                     if getattr(b, "type", None) == "tool_use"]
        if tool_uses:
            session.messages.append(_tool_results(
                tool_uses, [{"ok": False, "error": "interrupted", "instruction": "The caller spoke "
                             "before this ran; it did not happen."} for _ in tool_uses]))

    @staticmethod
    def _drop_dangling_user_turn(session: CallSession) -> None:
        """Keep history valid after a failed request: it must not end in a
        user turn we never answered, or the next turn would have two in a row."""
        while session.messages and session.messages[-1]["role"] == "user":
            last = session.messages[-1]["content"]
            if isinstance(last, list):   # tool results: keep, they answer a tool_use
                break
            session.messages.pop()


def _tool_results(tool_uses: list, results: list[dict]) -> dict:
    return {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": t.id,
         "content": json.dumps(r, ensure_ascii=False, default=str),
         **({"is_error": True} if not r.get("ok", False) else {})}
        for t, r in zip(tool_uses, results)
    ]}
