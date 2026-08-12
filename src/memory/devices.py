"""Persistent state for push delivery: device tokens and what was sent.

Two small registries that the push path needs and nothing else does:

* the FCM tokens to deliver to, keyed by the token itself because FCM rotates
  them without telling the client which device it replaced, and
* a ledger of nudges already pushed, so a rule that stays true for days
  ("no workout in 6 days") does not push every time the cron fires.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import func

from src.memory.db import DeviceToken, NudgeDelivery, SessionLocal

logger = logging.getLogger(__name__)

VALID_PLATFORMS = ("ios", "android")


# ---------------------------------------------------------------------------
# Device tokens
# ---------------------------------------------------------------------------


def register(
    user_id: str, token: str, platform: str, session_factory=SessionLocal
) -> bool:
    """Record *token* for *user_id*. Returns True when it was newly added.

    The client re-registers on every launch, so the common case is an existing
    row whose ``last_seen_at`` just needs bumping.
    """
    if platform not in VALID_PLATFORMS:
        raise ValueError(f"platform must be one of {VALID_PLATFORMS}, got {platform!r}")

    now = datetime.now(UTC)
    with session_factory() as db:
        row = db.query(DeviceToken).filter(DeviceToken.token == token).first()
        if row is not None:
            row.last_seen_at = now
            # A token can migrate between users on a shared device (log out,
            # log in as someone else); the newest claim wins.
            row.user_id = user_id
            row.platform = platform
            db.commit()
            return False

        db.add(
            DeviceToken(
                token=token,
                user_id=user_id,
                platform=platform,
                created_at=now,
                last_seen_at=now,
            )
        )
        db.commit()
    return True


def tokens_for(user_id: str, session_factory=SessionLocal) -> list[str]:
    """Every token currently registered to *user_id*."""
    with session_factory() as db:
        rows = (
            db.query(DeviceToken.token)
            .filter(DeviceToken.user_id == user_id)
            .order_by(DeviceToken.last_seen_at.desc())
            .all()
        )
    return [row[0] for row in rows]


def unregister(token: str, session_factory=SessionLocal) -> bool:
    """Drop *token*. Returns True when a row was actually removed."""
    with session_factory() as db:
        removed = db.query(DeviceToken).filter(DeviceToken.token == token).delete()
        db.commit()
    return bool(removed)


def unregister_many(tokens: list[str], session_factory=SessionLocal) -> int:
    """Drop several tokens at once — used to prune what FCM rejects."""
    if not tokens:
        return 0
    with session_factory() as db:
        removed = (
            db.query(DeviceToken)
            .filter(DeviceToken.token.in_(tokens))
            .delete(synchronize_session=False)
        )
        db.commit()
    return int(removed or 0)


# ---------------------------------------------------------------------------
# Delivery ledger
# ---------------------------------------------------------------------------


def _as_aware(value: datetime | None) -> datetime | None:
    """SQLite hands back naive datetimes; normalise before comparing."""
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def last_sent_map(
    user_id: str, kinds: list[str], since: datetime, session_factory=SessionLocal
) -> dict[str, datetime]:
    """Most recent send time per kind, for kinds sent after *since*.

    Each kind carries its own dedup window, so the caller passes the widest of
    them as *since* and compares the returned timestamps per kind — one query
    instead of one per rule.
    """
    if not kinds:
        return {}
    with session_factory() as db:
        rows = (
            db.query(NudgeDelivery.kind, func.max(NudgeDelivery.sent_at))
            .filter(
                NudgeDelivery.user_id == user_id,
                NudgeDelivery.kind.in_(kinds),
                NudgeDelivery.sent_at >= since,
            )
            .group_by(NudgeDelivery.kind)
            .all()
        )
    return {kind: aware for kind, ts in rows if (aware := _as_aware(ts)) is not None}


def record_sent(
    user_id: str, kind: str, now: datetime | None = None, session_factory=SessionLocal
) -> None:
    """Note that *kind* went out, so the dedup window starts now."""
    with session_factory() as db:
        db.add(
            NudgeDelivery(
                id=uuid.uuid4().hex,
                user_id=user_id,
                kind=kind,
                sent_at=now or datetime.now(UTC),
            )
        )
        db.commit()


def pushes_since(
    user_id: str, since: datetime, session_factory=SessionLocal
) -> int:
    """How many nudges were pushed to *user_id* since *since* — the daily cap."""
    with session_factory() as db:
        count = (
            db.query(NudgeDelivery)
            .filter(NudgeDelivery.user_id == user_id, NudgeDelivery.sent_at >= since)
            .count()
        )
    return int(count or 0)


def purge_deliveries(
    older_than_days: int = 30, session_factory=SessionLocal
) -> int:
    """Trim the ledger; only the recent window is ever consulted."""
    cutoff = datetime.now(UTC) - timedelta(days=older_than_days)
    with session_factory() as db:
        removed = db.query(NudgeDelivery).filter(NudgeDelivery.sent_at < cutoff).delete()
        db.commit()
    return int(removed or 0)
