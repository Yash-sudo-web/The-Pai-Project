"""Database initialization and ORM models for the Personal AI Assistant.

Exports: engine, SessionLocal, Base, init_db(), and all ORM model classes.
The DB URL defaults to the configured Supabase Postgres instance but can be
overridden via the DATABASE_URL environment variable.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    Date,
    ForeignKey,
    Integer,
    LargeBinary,
    REAL,
    Text,
    UniqueConstraint,
    create_engine,
)
from sqlalchemy.orm import DeclarativeBase, relationship, sessionmaker
from sqlalchemy.types import DateTime

from src.config import load_env_file

load_env_file()

def _resolve_database_url() -> str:
    explicit = os.environ.get("DATABASE_URL")
    if explicit:
        return explicit

    return "sqlite:///./assistant.db"


DATABASE_URL: str = _resolve_database_url()

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {},
    pool_pre_ping=not DATABASE_URL.startswith("sqlite"),
)

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


class Note(Base):
    """Unstructured note for semantic recall / Vector DB fallback."""

    __tablename__ = "notes"

    id = Column(Text, primary_key=True)
    user_id = Column(Text, nullable=False)
    content = Column(Text, nullable=False)
    embedding = Column(LargeBinary, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC))


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
    )


def init_db() -> None:
    """Create all tables if they do not already exist."""
    Base.metadata.create_all(bind=engine)
