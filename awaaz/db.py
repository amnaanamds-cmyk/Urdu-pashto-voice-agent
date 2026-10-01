"""Table definitions (SQLAlchemy Core).

Production schema lives in supabase/migrations/ (with RLS). These definitions
mirror it so the same queries run on Postgres/Supabase and on SQLite for local
development (`create_all` is used for SQLite only). Keep both in sync.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

metadata = sa.MetaData()

JSONType = sa.JSON().with_variant(JSONB(), "postgresql")


def _uuid_pk() -> sa.Column:
    return sa.Column("id", sa.Uuid, primary_key=True, default=uuid.uuid4)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _biz_fk() -> sa.Column:
    return sa.Column("business_id", sa.Uuid, sa.ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False)


businesses = sa.Table(
    "businesses", metadata,
    _uuid_pk(),
    sa.Column("name", sa.Text, nullable=False),
    sa.Column("type", sa.Text, nullable=False),
    sa.Column("city", sa.Text, nullable=False),
    sa.Column("languages", JSONType, nullable=False, default=lambda: ["ur"]),
    sa.Column("ai_phone", sa.Text, unique=True),
    sa.Column("staff_phone", sa.Text),
    sa.Column("owner_whatsapp", sa.Text),
    sa.Column("timings", JSONType, nullable=False, default=dict),
    sa.Column("faq_json", JSONType, nullable=False, default=dict),
    sa.Column("voice", JSONType, nullable=False, default=dict),
    sa.Column("plan", sa.Text, nullable=False, default="pilot"),
    sa.Column("recording_retention_days", sa.Integer, nullable=False, default=60),
    sa.Column("owner_token_hash", sa.Text),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, default=_now),
    sa.CheckConstraint("type in ('clinic', 'hostel', 'shop')"),
)

services = sa.Table(
    "services", metadata,
    _uuid_pk(), _biz_fk(),
    sa.Column("name", sa.Text, nullable=False),
    sa.Column("duration_min", sa.Integer, nullable=False, default=30),
    sa.Column("price", sa.Numeric(10, 2)),
    sa.UniqueConstraint("business_id", "name"),
)

slots = sa.Table(
    "slots", metadata,
    _uuid_pk(), _biz_fk(),
    sa.Column("date", sa.Date, nullable=False),
    sa.Column("start_time", sa.Time, nullable=False),
    sa.Column("status", sa.Text, nullable=False, default="free"),
    sa.UniqueConstraint("business_id", "date", "start_time"),
    sa.CheckConstraint("status in ('free', 'booked', 'blocked')"),
)

bookings = sa.Table(
    "bookings", metadata,
    _uuid_pk(), _biz_fk(),
    sa.Column("caller_name", sa.Text, nullable=False),
    sa.Column("caller_phone", sa.Text, nullable=False),
    sa.Column("service_id", sa.Uuid, sa.ForeignKey("services.id", ondelete="SET NULL")),
    sa.Column("slot_id", sa.Uuid, sa.ForeignKey("slots.id", ondelete="SET NULL")),
    sa.Column("status", sa.Text, nullable=False, default="confirmed"),
    sa.Column("source", sa.Text, nullable=False, default="call"),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, default=_now),
    sa.CheckConstraint("status in ('confirmed', 'cancelled', 'no_show', 'completed')"),
    sa.CheckConstraint("source in ('call', 'whatsapp', 'dashboard')"),
)

calls = sa.Table(
    "calls", metadata,
    _uuid_pk(), _biz_fk(),
    sa.Column("provider_call_id", sa.Text, unique=True),
    sa.Column("caller_phone", sa.Text),
    sa.Column("language", sa.Text),
    sa.Column("duration_sec", sa.Integer),
    sa.Column("outcome", sa.Text),
    sa.Column("ended_reason", sa.Text),
    sa.Column("cost_usd", sa.Numeric(10, 4)),
    sa.Column("recording_url", sa.Text),
    sa.Column("transcript", sa.Text),
    sa.Column("after_hours", sa.Boolean, nullable=False, default=False),
    sa.Column("started_at", sa.DateTime(timezone=True), nullable=False, default=_now),
    sa.CheckConstraint("language in ('ps', 'ur', 'en', 'mixed')"),
    sa.CheckConstraint("outcome in ('booked', 'faq', 'transferred', 'message', 'dropped')"),
)

messages = sa.Table(
    "messages", metadata,
    _uuid_pk(), _biz_fk(),
    sa.Column("caller_name", sa.Text),
    sa.Column("caller_phone", sa.Text),
    sa.Column("text", sa.Text, nullable=False),
    sa.Column("handled", sa.Boolean, nullable=False, default=False),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, default=_now),
)

usage = sa.Table(
    "usage", metadata,
    sa.Column("business_id", sa.Uuid, sa.ForeignKey("businesses.id", ondelete="CASCADE"), primary_key=True),
    sa.Column("month", sa.Date, primary_key=True),
    sa.Column("minutes_used", sa.Numeric(10, 2), nullable=False, default=0),
)

integrations = sa.Table(
    "integrations", metadata,
    sa.Column("business_id", sa.Uuid, sa.ForeignKey("businesses.id", ondelete="CASCADE"), primary_key=True),
    sa.Column("type", sa.Text, primary_key=True),
    sa.Column("base_url", sa.Text),
    sa.Column("api_key_ref", sa.Text, nullable=False),
)


def make_engine(url: str) -> sa.Engine:
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    if url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url[len("postgresql://"):]
    if url.startswith("sqlite"):
        engine = sa.create_engine(url, connect_args={"check_same_thread": False})

        @sa.event.listens_for(engine, "connect")
        def _fk_on(dbapi_conn, _):
            dbapi_conn.execute("pragma foreign_keys=on")

        metadata.create_all(engine)
        return engine
    return sa.create_engine(url, pool_pre_ping=True)
