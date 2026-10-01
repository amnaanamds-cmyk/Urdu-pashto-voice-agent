"""HTTP server: Vapi custom-LLM endpoint, Vapi webhooks, owner dashboard.

Run:  uvicorn --factory awaaz.api.app:create_app --host 0.0.0.0 --port 8000
"""

from __future__ import annotations

import asyncio
import hmac
import logging
import math
import os
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from starlette.middleware.sessions import SessionMiddleware

from .. import timeutil
from ..agent import lang
from ..agent.engine import Engine
from ..agent.session import CallSession
from ..agent.tools import ToolBox
from ..config import Settings
from ..db import make_engine
from ..integrations.whatsapp import WhatsApp
from ..repo import NotFound, Repo
from ..telephony import vapi

log = logging.getLogger("awaaz.api")

SESSION_TTL_SEC = 2 * 3600


@dataclass
class Services:
    settings: Settings
    repo: Repo
    whatsapp: WhatsApp
    engine: Engine
    sessions: dict[str, CallSession] = field(default_factory=dict)
    touched: dict[str, float] = field(default_factory=dict)

    def gc(self) -> None:
        cutoff = time.time() - SESSION_TTL_SEC
        for k in [k for k, t in self.touched.items() if t < cutoff]:
            self.sessions.pop(k, None)
            self.touched.pop(k, None)


def build_services(settings: Settings | None = None, engine: Engine | None = None,
                   repo: Repo | None = None) -> Services:
    settings = settings or Settings.from_env()
    repo = repo or Repo(make_engine(settings.database_url))
    wa = WhatsApp(settings)
    engine = engine or Engine(settings, repo, ToolBox(repo, wa))
    return Services(settings, repo, wa, engine)


def create_app(services: Services | None = None) -> FastAPI:
    svc = services or build_services()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if not svc.settings.webhook_secret:
            log.warning("AWAAZ_WEBHOOK_SECRET is not set: Vapi endpoints are unauthenticated (dev only)")
        if not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")):
            log.warning("ANTHROPIC_API_KEY is not set: calls will only hear the fallback line")
        yield

    app = FastAPI(title="Awaaz Desk", lifespan=lifespan)
    app.state.svc = svc
    app.add_middleware(SessionMiddleware, secret_key=svc.settings.session_secret,
                       same_site="lax", https_only=svc.settings.public_base_url.startswith("https"))

    def check_secret(request: Request) -> None:
        want = svc.settings.webhook_secret
        if want and not hmac.compare_digest(request.headers.get(vapi.SECRET_HEADER, ""), want):
            raise HTTPException(401, "bad secret")

    @app.get("/health")
    def health():
        return {"ok": True}

    # --- Vapi: custom LLM -------------------------------------------------

    @app.post("/vapi/llm/chat/completions")
    async def vapi_llm(request: Request):
        check_secret(request)
        body = await request.json()
        call = body.get("call") or {}
        call_id = call.get("id") or "no-call-id"
        biz_id = (body.get("metadata") or {}).get("business_id") or \
                 ((call.get("assistant") or {}).get("metadata") or {}).get("business_id")
        session = svc.sessions.get(call_id)
        if session is None:
            try:
                business = await asyncio.to_thread(svc.repo.get_business, biz_id)
            except NotFound:
                raise HTTPException(404, "unknown business")
            customer = body.get("customer") or call.get("customer") or {}
            session = CallSession(
                business=business,
                caller_phone=timeutil.normalize_phone(customer.get("number")),
                provider_call_id=call_id,
                channel="voice",
                control=vapi.VapiControl((call.get("monitor") or {}).get("controlUrl")),
            )
            svc.sessions[call_id] = session
        svc.touched[call_id] = time.time()
        svc.gc()

        text = vapi.latest_user_text(body.get("messages") or [])
        model = svc.settings.model
        chunk_id = vapi.new_chunk_id()

        if not body.get("stream", True):
            reply = "".join([c async for c in svc.engine.respond(session, text)])
            return JSONResponse({
                "id": chunk_id, "object": "chat.completion", "created": int(time.time()), "model": model,
                "choices": [{"index": 0, "finish_reason": "stop",
                             "message": {"role": "assistant", "content": reply}}],
            })

        async def gen():
            async for piece in svc.engine.respond(session, text):
                yield vapi.sse_chunk(chunk_id, model, piece)
            yield vapi.sse_chunk(chunk_id, model, finish="stop")
            yield "data: [DONE]\n\n"

        return StreamingResponse(gen(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    # --- Vapi: server messages --------------------------------------------

    @app.post("/vapi/webhook")
    async def vapi_webhook(request: Request):
        check_secret(request)
        msg = (await request.json()).get("message") or {}
        kind = msg.get("type")
        if kind == "assistant-request":
            dialled = (msg.get("phoneNumber") or {}).get("number") or \
                      ((msg.get("call") or {}).get("phoneNumber") or {}).get("number")
            business = await asyncio.to_thread(svc.repo.business_by_ai_phone, dialled)
            if business is None:
                log.error("assistant-request for unknown number %s", dialled)
                return JSONResponse({"error": "This number is not set up yet."})
            return {"assistant": vapi.assistant_config(
                business, svc.settings.public_base_url, svc.settings.webhook_secret, svc.settings.model)}
        if kind == "end-of-call-report":
            await asyncio.to_thread(finish_call, svc, msg)
            return {"ok": True}
        return {"ok": True}

    from .dashboard import router as dashboard_router
    app.include_router(dashboard_router)
    return app


def _parse_ts(v) -> datetime | None:
    if not v:
        return None
    try:
        return datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except ValueError:
        return None


def finish_call(svc: Services, msg: dict) -> dict | None:
    """Persist an end-of-call-report: call row, usage minutes, outcome."""
    call = msg.get("call") or {}
    call_id = call.get("id")
    session = svc.sessions.pop(call_id, None) if call_id else None
    svc.touched.pop(call_id, None)

    biz_id = session.business_id if session else \
        ((call.get("assistant") or msg.get("assistant") or {}).get("metadata") or {}).get("business_id")
    if not biz_id:
        dialled = (msg.get("phoneNumber") or call.get("phoneNumber") or {}).get("number")
        biz = svc.repo.business_by_ai_phone(dialled)
        biz_id = biz["id"] if biz else None
    if not biz_id:
        log.error("end-of-call-report for unknown business (call %s)", call_id)
        return None
    business = session.business if session else svc.repo.get_business(biz_id)

    # Store UTC: SQLite keeps the wall-clock time and drops the offset.
    started = (_parse_ts(msg.get("startedAt") or call.get("startedAt")) or timeutil.now()).astimezone(timezone.utc)
    ended = _parse_ts(msg.get("endedAt") or call.get("endedAt"))
    duration = int((ended - started).total_seconds()) if ended else None
    artifact = msg.get("artifact") or {}
    customer = msg.get("customer") or call.get("customer") or {}
    texts = session.user_texts if session else [artifact.get("transcript") or ""]

    row = svc.repo.record_call(
        biz_id,
        provider_call_id=call_id,
        caller_phone=timeutil.normalize_phone(customer.get("number")),
        language=lang.detect(texts),
        duration_sec=duration,
        outcome=session.outcome() if session else "dropped",
        ended_reason=str(msg.get("endedReason") or "")[:120] or None,
        cost_usd=msg.get("cost"),
        recording_url=artifact.get("recordingUrl"),
        transcript=artifact.get("transcript"),
        after_hours=not timeutil.is_open(business.get("timings") or {}, started),
        started_at=started,
    )
    if duration:
        svc.repo.add_usage(biz_id, started.astimezone(timeutil.PKT).date(), math.ceil(duration / 6) / 10)
    return row
