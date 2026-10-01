"""Vapi integration: Vapi handles the phone line, speech-to-text and TTS;
our server is its "custom LLM" (Claude + tools) and receives its webhooks.

Field names follow Vapi's server SDK types (vapi_server_sdk 1.11):
CreateAssistantDto, CustomLlmModel, ServerMessage{message}, Artifact,
ClientInboundMessage{transfer, say, end-call}, MonitorPlan.controlEnabled.

Flow per call:
  1. Call hits the business's Vapi number -> Vapi POSTs `assistant-request`
     to /vapi/webhook. We look the business up by the dialled number and
     return a transient assistant (assistant_config below).
  2. Each caller turn -> Vapi POSTs OpenAI-style chat to
     /vapi/llm/chat/completions; we stream Claude's reply back as SSE.
  3. transfer / end_call tools -> POST to the call's monitor.controlUrl.
  4. `end-of-call-report` -> we log the call, usage, recording, outcome.
"""

from __future__ import annotations

import json
import logging
import time
import uuid

import httpx

from ..agent import prompt

log = logging.getLogger("awaaz.vapi")

SECRET_HEADER = "x-awaaz-secret"

# Azure speech has ps-AF and ur-IN transcription and ur-PK / ps-AF neural
# voices. Override per business via businesses.voice
# ({"transcriber": {...}, "voice": {...}}) once the week-0 accuracy test picks
# a winner.
DEFAULT_TRANSCRIBER = {
    "ur": {"provider": "azure", "language": "ur-IN"},
    "ps": {"provider": "azure", "language": "ps-AF"},
    "en": {"provider": "deepgram", "language": "en"},
}
DEFAULT_VOICE = {
    "ur": {"provider": "azure", "voiceId": "ur-PK-UzmaNeural"},
    "ps": {"provider": "azure", "voiceId": "ps-AF-LatifaNeural"},
    "en": {"provider": "azure", "voiceId": "en-US-AvaMultilingualNeural"},
}

# First line of every call: greeting + recording notice (spec §11).
# TODO(pashto-review): Pashto greeting and silence prompt.
GREETING = {
    "ur": "السلام علیکم، {name}۔ یہ کال ریکارڈ ہو رہی ہے۔ میں آپ کی کیا مدد کر سکتی ہوں؟",
    "ps": "السلام علیکم، {name}. دا کال ثبتیږي. زه ستاسو څنګه مرسته کولی شم؟",
    "en": "Assalam o alaikum, {name}. This call is recorded. How can I help you?",
}
SILENCE_PROMPT = {
    "ur": "جی، آپ سن رہے ہیں؟",
    "ps": "هو، تاسو اورئ؟",
    "en": "Hello, are you still there?",
}


def assistant_config(business: dict, base_url: str, secret: str, model: str) -> dict:
    """Transient assistant returned in response to assistant-request."""
    lang = prompt.primary_language(business)
    overrides = business.get("voice") or {}
    headers = {SECRET_HEADER: secret} if secret else {}
    return {
        "name": f"Awaaz Desk - {business['name']}"[:40],
        "firstMessage": GREETING[lang].format(name=business["name"]),
        "transcriber": overrides.get("transcriber") or DEFAULT_TRANSCRIBER[lang],
        "voice": overrides.get("voice") or DEFAULT_VOICE[lang],
        "model": {
            "provider": "custom-llm",
            "url": f"{base_url}/vapi/llm",
            "model": model,
            "headers": headers,
            # `variable` sends assistant.metadata plus call / customer /
            # phoneNumber with each request.
            "metadataSendMode": "variable",
            "timeoutSeconds": 20,
        },
        "server": {"url": f"{base_url}/vapi/webhook", "headers": headers},
        "serverMessages": ["end-of-call-report", "status-update"],
        "monitorPlan": {"controlEnabled": True},
        "artifactPlan": {"recordingEnabled": True},
        "maxDurationSeconds": 600,
        "hooks": [{
            "on": "customer.speech.timeout",
            "options": {"timeoutSeconds": 5, "triggerMaxCount": 2},
            "do": [{"type": "say", "exact": SILENCE_PROMPT[lang]}],
        }],
        "metadata": {"business_id": str(business["id"])},
    }


# --- OpenAI-compatible streaming (what Vapi's custom-llm expects) -----------

def sse_chunk(chunk_id: str, model: str, content: str | None = None, finish: str | None = None) -> str:
    delta = {"content": content} if content is not None else {}
    if content is not None and finish is None:
        delta["role"] = "assistant"
    body = {
        "id": chunk_id,
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": model,
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
    }
    return f"data: {json.dumps(body, ensure_ascii=False)}\n\n"


def new_chunk_id() -> str:
    return f"chatcmpl-{uuid.uuid4().hex[:24]}"


def latest_user_text(messages: list[dict]) -> str:
    """Caller words since the assistant last spoke (Vapi resends full history)."""
    parts = []
    for m in reversed(messages or []):
        if m.get("role") == "assistant":
            break
        if m.get("role") == "user":
            c = m.get("content")
            if isinstance(c, list):
                c = " ".join(p.get("text", "") for p in c if isinstance(p, dict))
            parts.append(str(c or ""))
    return " ".join(reversed(parts)).strip()


# --- Live call control ------------------------------------------------------

class VapiControl:
    """CallControl backed by the call's monitor.controlUrl."""

    def __init__(self, control_url: str | None, client: httpx.Client | None = None):
        self.url = control_url
        self.client = client or httpx.Client(timeout=5)

    def _send(self, payload: dict) -> bool:
        if not self.url:
            log.warning("no controlUrl for call; cannot %s", payload.get("type"))
            return False
        try:
            r = self.client.post(self.url, json=payload)
            if r.status_code >= 400:
                log.error("vapi control %s failed %s: %s", payload.get("type"), r.status_code, r.text[:300])
                return False
            return True
        except httpx.HTTPError as e:
            log.error("vapi control error: %s", e)
            return False

    def transfer(self, number: str, announce: str | None = None) -> bool:
        payload = {"type": "transfer", "destination": {"type": "number", "number": number}}
        if announce:
            payload["content"] = announce
        return self._send(payload)

    def end_call(self, goodbye: str) -> bool:
        return self._send({"type": "say", "content": goodbye, "endCallAfterSpoken": True})


def delete_call(api_key: str, call_id: str, client: httpx.Client | None = None) -> bool:
    """Delete a call (and its recording) from Vapi. Used by the retention job."""
    client = client or httpx.Client(timeout=10)
    try:
        r = client.delete(f"https://api.vapi.ai/call/{call_id}",
                          headers={"Authorization": f"Bearer {api_key}"})
        return r.status_code < 400 or r.status_code == 404
    except httpx.HTTPError as e:
        log.error("vapi delete %s: %s", call_id, e)
        return False
