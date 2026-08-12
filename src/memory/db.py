"""Database initialization and ORM models for the Personal AI Assistant.

Exports: engine, SessionLocal, Base, init_db(), and all ORM model classes.
The DB URL defaults to the configured Supabase Postgres instance but can be
overridden via the DATABASE_URL environment variable.
"""

from __future__ import annotations

import logging
import os
import warnings
from datetime import UTC, datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    Date,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    REAL,
    Text,
    UniqueConstraint,
    create_engine,
    func,
)
from sqlalchemy.orm import DeclarativeBase, relationship, sessionmaker
from sqlalchemy.pool import NullPool
from sqlalchemy.types import DateTime

from src.config import load_env_file

logger = logging.getLogger(__name__)

load_env_file()

def _resolve_database_url() -> str:
    explicit = os.environ.get("DATABASE_URL")
    if explicit:
        # Normalise to psycopg v3 driver — Supabase pooler URLs use plain
        # postgresql:// which SQLAlchemy maps to psycopg2 (v2) by default.
        url = explicit
        for old_scheme in ("postgresql+psycopg2://", "postgresql://", "postgres://"):
            if url.startswith(old_scheme):
                url = "postgresql+psycopg://" + url[len(old_scheme):]
                break
        return url

    # Vercel's project root (/var/task) is read-only — only /tmp is writable.
    if os.environ.get("VERCEL") or not os.access(".", os.W_OK):
        return "sqlite:////tmp/assistant.db"

    return "sqlite:///./assistant.db"


DATABASE_URL: str = _resolve_database_url()

_IS_SQLITE = DATABASE_URL.startswith("sqlite")
# On a serverless host each invocation gets a short-lived process, so a
# connection pool is never reused — it just holds sockets open against the
# database. NullPool opens and closes per checkout instead.
_IS_SERVERLESS = bool(os.environ.get("VERCEL") or os.environ.get("AWS_LAMBDA_FUNCTION_NAME"))

_engine_kwargs: dict = {
    "connect_args": {"check_same_thread": False} if _IS_SQLITE else {},
    "pool_pre_ping": not _IS_SQLITE,
}
if _IS_SERVERLESS and not _IS_SQLITE:
    _engine_kwargs["poolclass"] = NullPool
    _engine_kwargs.pop("pool_pre_ping")

engine = create_engine(DATABASE_URL, **_engine_kwargs)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


class Workout(Base):
    """Gym workout log entry."""

    __tablename__ = "workouts"

    id = Column(Text, primary_key=True)
    user_id = Column(Text, nullable=False)
    exercise = Column(Text, nullable=False)
    sets = Column(Integer, nullable=True)
    reps = Column(Integer, nullable=True)
    weight_kg = Column(REAL, nullable=True)
    duration_s = Column(Integer, nullable=True)
    distance_m = Column(REAL, nullable=True)
    notes = Column(Text, nullable=True)
    worked_out_at = Column(DateTime(timezone=True), nullable=True)  # when the workout actually happened
    logged_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC))

    __table_args__ = (
        # Every history/PR query filters by user and either exercise name
        # (case-insensitively) or date; without these each one is a seq scan
        # over the network.
        Index("ix_workouts_user_exercise", "user_id", func.lower(exercise)),
        Index("ix_workouts_user_date", "user_id", worked_out_at.desc()),
    )


class Meal(Base):
    """Nutrition meal log entry."""

    __tablename__ = "meals"

    id = Column(Text, primary_key=True)
    user_id = Column(Text, nullable=False)
    food_item = Column(Text, nullable=False)
    quantity = Column(REAL, nullable=False)
    unit = Column(Text, nullable=False)
    calories = Column(REAL, nullable=False)
    protein_g = Column(REAL, nullable=False)
    carbs_g = Column(REAL, nullable=False)
    fat_g = Column(REAL, nullable=False)
    is_custom = Column(Boolean, nullable=False, default=False)
    meal_type = Column(Text, nullable=True)  # breakfast, lunch, dinner, snack
    eaten_at = Column(DateTime(timezone=True), nullable=True)  # when the user actually ate
    logged_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC))

    __table_args__ = (
        Index("ix_meals_user_eaten", "user_id", eaten_at.desc()),
    )


class Task(Base):
    """Productivity task record."""

    __tablename__ = "tasks"

    id = Column(Text, primary_key=True)
    user_id = Column(Text, nullable=False)
    title = Column(Text, nullable=False)
    due_date = Column(DateTime(timezone=True), nullable=True)
    status = Column(
        Text,
        nullable=False,
        default="pending",
    )
    completed_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC))

    __table_args__ = (
        CheckConstraint("status IN ('pending', 'completed', 'overdue')", name="ck_tasks_status"),
        Index("ix_tasks_user_status_due", "user_id", "status", "due_date"),
    )


class AuditLog(Base):
    """Append-only audit log for every tool invocation."""

    __tablename__ = "audit_log"

    id = Column(Text, primary_key=True)
    timestamp = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC))
    tool_name = Column(Text, nullable=False)
    domain = Column(Text, nullable=False)
    inputs = Column(Text, nullable=False)       # JSON string
    output = Column(Text, nullable=True)        # JSON string
    error = Column(Text, nullable=True)
    approval_status = Column(Text, nullable=False)
    session_id = Column(Text, nullable=False)

    __table_args__ = (
        Index("ix_audit_session_ts", "session_id", timestamp.desc()),
    )


class Note(Base):
    """Unstructured note for semantic recall / Vector DB fallback."""

    __tablename__ = "notes"

    id = Column(Text, primary_key=True)
    user_id = Column(Text, nullable=False)
    content = Column(Text, nullable=False)
    embedding = Column(LargeBinary, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC))


class WaterIntake(Base):
    """Water intake log entry."""

    __tablename__ = "water_intake"

    id = Column(Text, primary_key=True)
    user_id = Column(Text, nullable=False)
    amount_ml = Column(REAL, nullable=False)
    logged_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC))


class NutritionGoal(Base):
    """Daily nutrition targets for a user.

    Only one row per user should have ``is_active=True`` at any time.
    When goals are updated, the old row is deactivated and a new one is
    created, preserving goal history via ``effective_from``.
    """

    __tablename__ = "nutrition_goals"

    id = Column(Text, primary_key=True)
    user_id = Column(Text, nullable=False)
    calories = Column(REAL, nullable=False)
    protein_g = Column(REAL, nullable=False)
    carbs_g = Column(REAL, nullable=False)
    fat_g = Column(REAL, nullable=False)
    water_ml = Column(REAL, nullable=True)  # optional daily water target
    effective_from = Column(Date, nullable=False)
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC))

    __table_args__ = (
        Index("ix_nutrition_goals_user_active", "user_id", "is_active"),
    )


class ChatSession(Base):
    """Daily chat session — exactly one per user per calendar day."""

    __tablename__ = "chat_sessions"

    id = Column(Text, primary_key=True)
    user_id = Column(Text, nullable=False)
    session_date = Column(Date, nullable=False)
    status = Column(Text, nullable=False, default="active")
    summary = Column(Text, nullable=True)
    message_count = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC))
    closed_at = Column(DateTime(timezone=True), nullable=True)

    messages = relationship("ChatMessage", back_populates="session", order_by="ChatMessage.created_at")

    __table_args__ = (
        UniqueConstraint("user_id", "session_date", name="uq_chat_session_user_date"),
        CheckConstraint("status IN ('active', 'closed')", name="ck_chat_session_status"),
        Index("ix_chat_sessions_user_date", "user_id", session_date.desc()),
    )


class ChatMessage(Base):
    """Individual chat message belonging to a daily session."""

    __tablename__ = "chat_messages"

    id = Column(Text, primary_key=True)
    session_id = Column(Text, ForeignKey("chat_sessions.id"), nullable=False)
    role = Column(Text, nullable=False)  # 'user' or 'assistant'
    content = Column(Text, nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC))

    session = relationship("ChatSession", back_populates="messages")

    __table_args__ = (
        CheckConstraint("role IN ('user', 'assistant', 'system')", name="ck_chat_message_role"),
        # Context building fetches the newest N messages for one session on
        # every conversational turn.
        Index("ix_chat_messages_session_created", "session_id", created_at.desc()),
    )


class CommandReceipt(Base):
    """Idempotency record for a submitted command.

    A mobile client that retries after a timeout would otherwise log the same
    meal twice. The key is reserved before the command runs, so a retry that
    arrives mid-flight is refused rather than duplicated.
    """

    __tablename__ = "command_receipts"

    id = Column(Text, primary_key=True)  # f"{user_id}:{idempotency_key}"
    user_id = Column(Text, nullable=False)
    idempotency_key = Column(Text, nullable=False)
    status = Column(Text, nullable=False, default="in_progress")
    response = Column(Text, nullable=True)  # JSON payload of the original reply
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC))

    __table_args__ = (
        CheckConstraint("status IN ('in_progress', 'completed')", name="ck_receipt_status"),
        Index("ix_command_receipts_created", created_at.desc()),
    )


class DeviceToken(Base):
    """An FCM registration token for one install of the client app.

    Tokens are rotated by FCM without warning, so the token itself is the
    primary key rather than the device: a rotated token arrives as a fresh
    row and the stale one is dropped when FCM reports it UNREGISTERED.
    """

    __tablename__ = "device_tokens"

    token = Column(Text, primary_key=True)
    user_id = Column(Text, nullable=False)
    platform = Column(Text, nullable=False)  # ios | android
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC))
    last_seen_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC))

    __table_args__ = (
        CheckConstraint("platform IN ('ios', 'android')", name="ck_device_platform"),
        Index("ix_device_tokens_user", "user_id"),
    )


class NudgeDelivery(Base):
    """One row per nudge kind actually pushed to a device.

    The nudge rules are stateless — they report what is true right now, which
    for something like a workout gap stays true for days. This ledger is what
    stops the same nudge going out on every cron tick; see
    ``src.domains.review.nudges.DEDUP_WINDOW_HOURS``.
    """

    __tablename__ = "nudge_deliveries"

    id = Column(Text, primary_key=True)
    user_id = Column(Text, nullable=False)
    kind = Column(Text, nullable=False)
    sent_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC))

    __table_args__ = (
        Index("ix_nudge_deliveries_user_kind_sent", "user_id", "kind", sent_at.desc()),
    )


def init_db() -> None:
    """Create tables and indexes for a database Alembic does not manage.

    Once a database has an ``alembic_version`` table, migrations own the
    schema and this becomes a no-op — otherwise ``create_all`` would quietly
    add tables that no migration knows about.

    For unmanaged databases, indexes are created explicitly because
    ``create_all`` skips existing tables entirely, so an index added to a
    model after the table was first created would never be built.
    """
    if _alembic_manages_schema():
        return

    Base.metadata.create_all(bind=engine)

    with warnings.catch_warnings():
        # SQLite cannot reflect expression-based indexes, so checkfirst warns
        # about ix_workouts_user_exercise on every call. Nothing actionable.
        warnings.filterwarnings("ignore", message=".*expression-based index.*")
        with engine.begin() as connection:
            for table in Base.metadata.sorted_tables:
                for index in table.indexes:
                    try:
                        index.create(bind=connection, checkfirst=True)
                    except Exception:  # pragma: no cover - index already present
                        logger.debug("Could not create index %s", index.name, exc_info=True)


def _alembic_manages_schema() -> bool:
    """True when the database carries an Alembic version stamp."""
    try:
        from sqlalchemy import inspect

        return inspect(engine).has_table("alembic_version")
    except Exception:
        return False
