"""Settings from the environment. See .env.example."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _load_dotenv(path: Path = Path(".env")) -> None:
    """Minimal .env loader (KEY=VALUE lines); real env vars win."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.split(" #", 1)[0].strip().strip('"').strip("'")
        os.environ.setdefault(key.strip(), value)


@dataclass(frozen=True)
class Settings:
    database_url: str = "sqlite:///awaaz.db"
    # Spec §6: a fast model for conversation. Override with AWAAZ_MODEL.
    model: str = "claude-haiku-4-5"
    max_reply_tokens: int = 300
    public_base_url: str = "http://localhost:8000"
    # Shared secret Vapi sends in the x-awaaz-secret header (set in the
    # assistant's server + custom-llm headers). Empty = no check (dev only).
    webhook_secret: str = ""
    session_secret: str = "dev-only-change-me"
    vapi_api_key: str = ""
    whatsapp_token: str = ""
    whatsapp_phone_number_id: str = ""
    whatsapp_booking_template: str = ""   # approved template name; empty = plain text
    whatsapp_template_lang: str = "ur"

    @classmethod
    def from_env(cls) -> "Settings":
        _load_dotenv()
        env = os.environ.get
        return cls(
            database_url=env("DATABASE_URL", cls.database_url),
            model=env("AWAAZ_MODEL", cls.model),
            max_reply_tokens=int(env("AWAAZ_MAX_REPLY_TOKENS", cls.max_reply_tokens)),
            public_base_url=env("PUBLIC_BASE_URL", cls.public_base_url).rstrip("/"),
            webhook_secret=env("AWAAZ_WEBHOOK_SECRET", ""),
            session_secret=env("AWAAZ_SESSION_SECRET", cls.session_secret),
            vapi_api_key=env("VAPI_API_KEY", ""),
            whatsapp_token=env("WHATSAPP_TOKEN", ""),
            whatsapp_phone_number_id=env("WHATSAPP_PHONE_NUMBER_ID", ""),
            whatsapp_booking_template=env("WHATSAPP_BOOKING_TEMPLATE", ""),
            whatsapp_template_lang=env("WHATSAPP_TEMPLATE_LANG", "ur"),
        )
