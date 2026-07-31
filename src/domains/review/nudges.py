"""Proactive nudge rules.

Pure functions over :mod:`src.domains.review.aggregate` snapshots, so they can
be unit-tested without a clock or a scheduler. Delivery is the caller's job:
``GET /nudges`` returns whatever is currently due, and a scheduled
``POST /cron/nudges`` can evaluate them on a timer.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any, Callable

from src.context import LOCAL_TZ
from src.domains.review.aggregate import TodaySnapshot, today_snapshot

# Local hour after which "you're behind on protein" is actionable rather than
# just an artefact of it being early. Compared in LOCAL_TZ, not UTC.
EVENING_HOUR = 19

# Days without a logged workout before it is worth mentioning.
WORKOUT_GAP_DAYS = 4


@dataclass
class Nudge:
    kind: str
    message: str
    priority: str  # low | medium | high

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


Rule = Callable[[TodaySnapshot, datetime], Nudge | None]


def _protein_behind(snapshot: TodaySnapshot, now: datetime) -> Nudge | None:
    goal = snapshot.goal
    if not goal or not goal.get("protein_g") or now.hour < EVENING_HOUR:
        return None
    target = float(goal["protein_g"])
    remaining = target - snapshot.protein_g
    if remaining <= target * 0.2:  # within 20% is close enough
        return None
    return Nudge(
        kind="protein_behind",
        message=(
            f"You're {remaining:.0f}g short of your {target:.0f}g protein target "
            "with the day nearly done."
        ),
        priority="medium",
    )


def _calories_over(snapshot: TodaySnapshot, now: datetime) -> Nudge | None:
    goal = snapshot.goal
    if not goal or not goal.get("calories"):
        return None
    target = float(goal["calories"])
    if snapshot.calories <= target * 1.1:
        return None
    return Nudge(
        kind="calories_over",
        message=(
            f"You're {snapshot.calories - target:.0f} kcal over today's "
            f"{target:.0f} kcal target."
        ),
        priority="low",
    )


def _no_recent_workout(snapshot: TodaySnapshot, now: datetime) -> Nudge | None:
    gap = snapshot.days_since_workout
    if gap is None or gap < WORKOUT_GAP_DAYS:
        return None
    return Nudge(
        kind="workout_gap",
        message=f"No workout logged in {gap} days — want to plan a session?",
        priority="medium",
    )


def _overdue_tasks(snapshot: TodaySnapshot, now: datetime) -> Nudge | None:
    if not snapshot.overdue_tasks:
        return None
    count = len(snapshot.overdue_tasks)
    head = snapshot.overdue_tasks[0]
    detail = head if count == 1 else f"{head} (+{count - 1} more)"
    return Nudge(
        kind="overdue_tasks",
        message=f"{count} task{'s' if count > 1 else ''} overdue: {detail}",
        priority="high",
    )


def _water_behind(snapshot: TodaySnapshot, now: datetime) -> Nudge | None:
    goal = snapshot.goal
    if not goal or not goal.get("water_ml") or now.hour < EVENING_HOUR:
        return None
    target = float(goal["water_ml"])
    if snapshot.water_ml >= target * 0.8:
        return None
    return Nudge(
        kind="water_behind",
        message=(
            f"You've had {snapshot.water_ml:.0f}ml of water against a "
            f"{target:.0f}ml target."
        ),
        priority="low",
    )


RULES: tuple[Rule, ...] = (
    _overdue_tasks,
    _protein_behind,
    _no_recent_workout,
    _calories_over,
    _water_behind,
)

_PRIORITY_ORDER = {"high": 0, "medium": 1, "low": 2}


def evaluate(snapshot: TodaySnapshot, now: datetime | None = None) -> list[Nudge]:
    """Run every rule against *snapshot*, most urgent first.

    *now* is converted to the user's local zone first, so time-of-day rules
    read the hour the user would see on a clock.
    """
    now = (now or datetime.now(UTC)).astimezone(LOCAL_TZ)
    found = [nudge for rule in RULES if (nudge := rule(snapshot, now)) is not None]
    found.sort(key=lambda n: _PRIORITY_ORDER.get(n.priority, 3))
    return found


def due_for_user(user_id: str, now: datetime | None = None) -> list[Nudge]:
    """Convenience wrapper that loads the snapshot and evaluates the rules."""
    now = now or datetime.now(UTC)
    return evaluate(today_snapshot(user_id, now=now), now=now)
