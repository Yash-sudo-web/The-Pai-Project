"""Idempotent command submission.

A client that retries a request it never saw a response to must not run the
command twice. The flow is reserve-then-complete:

1. ``reserve`` inserts a row for the key. A duplicate insert means another
   attempt already owns it.
2. ``complete`` stores the response so a later retry replays it verbatim.

Reservations older than :data:`RECEIPT_TTL_SECONDS` are treated as abandoned
(the process died mid-command) and may be retried.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.exc import IntegrityError

from src.memory.db import CommandReceipt, SessionLocal

logger = logging.getLogger(__name__)

# How long a completed response stays replayable.
RECEIPT_TTL_SECONDS = 24 * 60 * 60

# How long an unfinished reservation blocks retries before it is assumed dead.
IN_PROGRESS_TIMEOUT_SECONDS = 120


@dataclass
class ReservationResult:
    """Outcome of trying to claim an idempotency key."""

    acquired: bool
    replay: dict[str, Any] | None = None
    in_progress: bool = False


def _row_id(user_id: str, key: str) -> str:
    return f"{user_id}:{key}"


def _as_aware(value: datetime | None) -> datetime:
    """SQLite hands back naive datetimes; normalise before comparing."""
    if value is None:
        return datetime.now(UTC)
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def reserve(user_id: str, key: str, session_factory=SessionLocal) -> ReservationResult:
    """Claim *key* for this attempt.

    Returns ``acquired=True`` when the caller should run the command,
    ``replay`` when a previous attempt already produced a response, and
    ``in_progress=True`` when another attempt is still running.
    """
    now = datetime.now(UTC)
    row_id = _row_id(user_id, key)

    with session_factory() as db:
        existing = db.query(CommandReceipt).filter(CommandReceipt.id == row_id).first()

        if existing is not None:
            age = (now - _as_aware(existing.created_at)).total_seconds()

            if existing.status == "completed":
                if age <= RECEIPT_TTL_SECONDS and existing.response:
                    return ReservationResult(acquired=False, replay=json.loads(existing.response))
                db.delete(existing)  # expired; let the caller start fresh
                db.commit()
            elif age <= IN_PROGRESS_TIMEOUT_SECONDS:
                return ReservationResult(acquired=False, in_progress=True)
            else:
                logger.warning("Reclaiming abandoned idempotency key %s", key)
                db.delete(existing)
                db.commit()

        try:
            db.add(
                CommandReceipt(
                    id=row_id,
                    user_id=user_id,
                    idempotency_key=key,
                    status="in_progress",
                    created_at=now,
                )
            )
            db.commit()
        except IntegrityError:
            # Another request inserted between our read and write.
            db.rollback()
            return ReservationResult(acquired=False, in_progress=True)

    return ReservationResult(acquired=True)


def complete(
    user_id: str, key: str, response: dict[str, Any], session_factory=SessionLocal
) -> None:
    """Store the response so a retry replays it."""
    with session_factory() as db:
        row = db.query(CommandReceipt).filter(CommandReceipt.id == _row_id(user_id, key)).first()
        if row is None:
            return
        row.status = "completed"
        row.response = json.dumps(response, default=str)
        db.commit()


def release(user_id: str, key: str, session_factory=SessionLocal) -> None:
    """Drop a reservation whose command failed, so the client may retry."""
    with session_factory() as db:
        row = db.query(CommandReceipt).filter(CommandReceipt.id == _row_id(user_id, key)).first()
        if row is not None and row.status != "completed":
            db.delete(row)
            db.commit()


def purge_expired(session_factory=SessionLocal) -> int:
    """Delete receipts past their TTL. Returns how many were removed."""
    cutoff = datetime.now(UTC) - timedelta(seconds=RECEIPT_TTL_SECONDS)
    with session_factory() as db:
        removed = (
            db.query(CommandReceipt).filter(CommandReceipt.created_at < cutoff).delete()
        )
        db.commit()
    return int(removed or 0)
