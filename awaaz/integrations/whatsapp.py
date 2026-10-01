"""WhatsApp Cloud API (Meta) sender.

Business-initiated messages to a number that hasn't messaged you in the last
24 h must use an approved template. Booking confirmations to callers therefore
use WHATSAPP_BOOKING_TEMPLATE when set (body params: name, day, time, business).
Plain text works for the owner's own number once they've messaged the business
number, and in Meta's test mode.

With no WHATSAPP_TOKEN configured, messages are logged instead of sent, so
local development and tests never hit the network.
"""

from __future__ import annotations

import logging

import httpx

from ..config import Settings

log = logging.getLogger("awaaz.whatsapp")
GRAPH = "https://graph.facebook.com/v21.0"


class WhatsApp:
    def __init__(self, settings: Settings, client: httpx.Client | None = None):
        self.s = settings
        self.client = client or httpx.Client(timeout=10)
        self.outbox: list[dict] = []   # what was sent/logged; handy in tests and the CLI

    @property
    def enabled(self) -> bool:
        return bool(self.s.whatsapp_token and self.s.whatsapp_phone_number_id)

    def _post(self, payload: dict) -> bool:
        self.outbox.append(payload)
        if not self.enabled:
            log.info("whatsapp (not configured, logged only): %s", payload)
            return True
        try:
            r = self.client.post(
                f"{GRAPH}/{self.s.whatsapp_phone_number_id}/messages",
                headers={"Authorization": f"Bearer {self.s.whatsapp_token}"},
                json=payload,
            )
            if r.status_code >= 400:
                log.error("whatsapp send failed %s: %s", r.status_code, r.text[:500])
                return False
            return True
        except httpx.HTTPError as e:
            log.error("whatsapp send error: %s", e)
            return False

    def send_text(self, to: str, body: str) -> bool:
        if not to:
            return False
        return self._post({
            "messaging_product": "whatsapp",
            "to": to.lstrip("+"),
            "type": "text",
            "text": {"body": body[:4096]},
        })

    def send_template(self, to: str, name: str, params: list[str]) -> bool:
        return self._post({
            "messaging_product": "whatsapp",
            "to": to.lstrip("+"),
            "type": "template",
            "template": {
                "name": name,
                "language": {"code": self.s.whatsapp_template_lang},
                "components": [{
                    "type": "body",
                    "parameters": [{"type": "text", "text": p} for p in params],
                }],
            },
        })

    def booking_confirmation(self, to: str, *, name: str, day: str, time: str, business: str) -> bool:
        if self.s.whatsapp_booking_template:
            return self.send_template(to, self.s.whatsapp_booking_template, [name, day, time, business])
        return self.send_text(to, f"{business}: {name}, aap ki booking {day} {time} confirm hai. Shukriya!")
