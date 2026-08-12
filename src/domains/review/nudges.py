"""Proactive nudge rules.

Pure functions over :mod:`src.domains.review.aggregate` snapshots, so they can
be unit-tested without a clock or a scheduler. Delivery is the caller's job:
``GET /nudges`` returns whatever is currently due, and a scheduled
``POST /cron/nudges`` evaluates them on a timer and pushes to the device.

Every rule declares a :attr:`Nudge.channel`. That split is the difference
between a useful assistant and one the user mutes:

``push``
    Actionable at the moment it arrives — there is something to *do* about it
    right now.
``in_app``
    True and worth seeing, but retrospective or low-stakes. Surfaced when the
    user opens the app; never interrupts them.

A nudge being true does not mean it should be sent. :func:`select_for_push`
applies the interruption budget — quiet hours, a daily cap, and a per-kind
dedup window — because the rules themselves are stateless and a condition like
"no workout in 6 days" stays true every time the cron fires.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Callable

from src.context import LOCAL_TZ
from src.domains.review.aggregate import TodaySnapshot, today_snapshot

# Local hour after which "you're behind on protein" is actionable rather than
# just an artefact of it being early. Compared in LOCAL_TZ, not UTC.
EVENING_HOUR = 19

# Days without a logged workout before it is worth mentioning.
WORKOUT_GAP_DAYS = 4

# The window the morning brief may fire in, local time. A brief that arrives
# at lunchtime because the cron slipped is worse than no brief.
MORNING_HOUR = 7
MORNING_CUTOFF_HOUR = 11

# No push leaves the server between these local hours. A nudge that would have
# fired is dropped rather than queued — by morning it is stale, and the next
# tick re-derives whatever is still true.
QUIET_START_HOUR = 22
QUIET_END_HOUR = 7

# Ceiling on interruptions per rolling 24h, regardless of how much is wrong.
# Three "you are failing" pings in a day is how an assistant gets muted.
MAX_PUSHES_PER_DAY = 2

# Default gap before the same kind may be pushed again. Just under a day, so a
# daily cron can fire the same kind on consecutive days without the window
# from yesterday's send swallowing today's.
DEFAULT_DEDUP_HOURS = 18


@dataclass
class Nudge:
    kind: str
    message: str
    priority: str  # low | medium | high
    channel: str = "push"  # push | in_app
    dedup_hours: int = DEFAULT_DEDUP_HOURS
    title: str = "PAI"

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


Rule = Callable[[TodaySnapshot, datetime], Nudge | None]


# ---------------------------------------------------------------------------
# Rules
# ---------------------------------------------------------------------------


def _in_quiet_hours(local_now: datetime) -> bool:
    """True inside the do-not-disturb window, which wraps across midnight."""
    hour = local_now.hour
    return hour >= QUIET_START_HOUR or hour < QUIET_END_HOUR


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
        title="Protein check",
        message=(
            f"You're {remaining:.0f}g short of your {target:.0f}g protein target "
            "with the day nearly done."
        ),
        priority="medium",
        channel="push",
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
        title="Calories",
        message=(
            f"You're {snapshot.calories - target:.0f} kcal over today's "
            f"{target:.0f} kcal target."
        ),
        priority="low",
        # Retrospective: the calories are already eaten, so a push offers
        # nothing to act on. Show it when the app is opened.
        channel="in_app",
    )


def _no_recent_workout(snapshot: TodaySnapshot, now: datetime) -> Nudge | None:
    gap = snapshot.days_since_workout
    if gap is None or gap < WORKOUT_GAP_DAYS:
        return None
    return Nudge(
        kind="workout_gap",
        title="Training",
        message=f"No workout logged in {gap} days — want to plan a session?",
        priority="medium",
        # The gap is real but the evening is the wrong time to raise it; the
        # morning brief carries it to the push channel when the day is still
        # plannable.
        channel="in_app",
    )


def _overdue_tasks(snapshot: TodaySnapshot, now: datetime) -> Nudge | None:
    if not snapshot.overdue_tasks:
        return None
    count = len(snapshot.overdue_tasks)
    head = snapshot.overdue_tasks[0]
    detail = head if count == 1 else f"{head} (+{count - 1} more)"
    return Nudge(
        kind="overdue_tasks",
        title="Overdue",
        message=f"{count} task{'s' if count > 1 else ''} overdue: {detail}",
        priority="high",
        channel="push",
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
        title="Water",
        message=(
            f"You've had {snapshot.water_ml:.0f}ml of water against a "
            f"{target:.0f}ml target."
        ),
        priority="low",
        # Would be true most evenings; pushing it trains the user to swipe
        # notifications away without reading them.
        channel="in_app",
    )


def _morning_brief(snapshot: TodaySnapshot, now: datetime) -> Nudge | None:
    """The one push worth sending unprompted: what today looks like.

    Predictable, arrives when the day can still be arranged around it, and
    replaces several individual nags with a single line.
    """
    if not (MORNING_HOUR <= now.hour < MORNING_CUTOFF_HOUR):
        return None

    parts: list[str] = []
    overdue = len(snapshot.overdue_tasks)
    if overdue:
        parts.append(f"{overdue} overdue")
    if snapshot.pending_tasks:
        parts.append(f"{snapshot.pending_tasks} task{'s' if snapshot.pending_tasks > 1 else ''} pending")

    gap = snapshot.days_since_workout
    if gap is not None and gap >= WORKOUT_GAP_DAYS:
        parts.append(f"no workout in {gap} days")
    elif gap is not None and gap >= 1:
        parts.append(f"last workout {gap}d ago")

    if not parts:
        # Nothing outstanding — say nothing rather than manufacturing a ping.
        return None

    return Nudge(
        kind="morning_brief",
        title="Today",
        message=" · ".join(parts).capitalize(),
        priority="medium",
        channel="push",
    )


def _nothing_logged(snapshot: TodaySnapshot, now: datetime) -> Nudge | None:
    """Catches the silence that every other rule misses.

    The other nutrition and gym rules all read logged data, so if the user
    stops logging entirely they go quiet — exactly when a nudge would help
    most. This one fires on the absence instead.

    It is deliberately gated on the user having *asked* to be tracked: an
    account with no nutrition goal and no tasks is either brand new or not
    being used for this, and nagging it would be noise.
    """
    if now.hour < EVENING_HOUR:
        return None

    opted_in = snapshot.goal is not None or snapshot.pending_tasks > 0
    if not opted_in:
        return None

    logged_today = (
        snapshot.calories > 0
        or snapshot.protein_g > 0
        or snapshot.water_ml > 0
        or snapshot.days_since_workout == 0
    )
    if logged_today:
        return None

    return Nudge(
        kind="nothing_logged",
        title="Nothing logged",
        message="No meals, water or training logged today. Want to catch up?",
        priority="medium",
        channel="push",
    )


def _weekly_review_ready(snapshot: TodaySnapshot, now: datetime) -> Nudge | None:
    """Sunday evening: the week's data is complete enough to look back on."""
    if now.weekday() != 6 or not (EVENING_HOUR <= now.hour < QUIET_START_HOUR):
        return None
    # An account with no goal, no tasks and no training history has nothing to
    # review; offering a summary of nothing is worse than staying quiet.
    has_history = (
        snapshot.goal is not None
        or snapshot.pending_tasks > 0
        or snapshot.days_since_workout is not None
    )
    if not has_history:
        return None
    return Nudge(
        kind="weekly_review",
        title="Weekly review",
        message="Your week is ready to review — ask for the summary when you have a minute.",
        priority="low",
        channel="push",
        # Once a week, not once a day.
        dedup_hours=6 * 24,
    )


RULES: tuple[Rule, ...] = (
    _overdue_tasks,
    _protein_behind,
    _no_recent_workout,
    _calories_over,
    _water_behind,
    _morning_brief,
    _nothing_logged,
    _weekly_review_ready,
)

_PRIORITY_ORDER = {"high": 0, "medium": 1, "low": 2}


# ---------------------------------------------------------------------------
# Evaluation and selection
# ---------------------------------------------------------------------------


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


@dataclass
class PushSelection:
    """What survived the interruption budget, and what stopped the rest."""

    to_send: list[Nudge] = field(default_factory=list)
    suppressed: dict[str, str] = field(default_factory=dict)  # kind -> reason

    def as_dict(self) -> dict[str, Any]:
        return {
            "to_send": [n.as_dict() for n in self.to_send],
            "suppressed": self.suppressed,
        }


def dedup_cutoffs(nudges: list[Nudge], now: datetime) -> dict[str, datetime]:
    """The earliest send time that still blocks each kind, keyed by kind."""
    return {n.kind: now - timedelta(hours=n.dedup_hours) for n in nudges}


def select_for_push(
    nudges: list[Nudge],
    now: datetime,
    already_sent: set[str] | None = None,
    sent_in_last_day: int = 0,
) -> PushSelection:
    """Apply the interruption budget to *nudges*, which must be priority-sorted.

    *already_sent* holds the kinds inside their dedup window, and
    *sent_in_last_day* the count against :data:`MAX_PUSHES_PER_DAY`. Both come
    from the delivery ledger — this function stays pure so the policy can be
    tested without a database.
    """
    already_sent = already_sent or set()
    local_now = now.astimezone(LOCAL_TZ)
    selection = PushSelection()

    if _in_quiet_hours(local_now):
        selection.suppressed = {n.kind: "quiet_hours" for n in nudges if n.channel == "push"}
        return selection

    budget = MAX_PUSHES_PER_DAY - sent_in_last_day

    for nudge in nudges:
        if nudge.channel != "push":
            selection.suppressed[nudge.kind] = "in_app_only"
            continue
        if nudge.kind in already_sent:
            selection.suppressed[nudge.kind] = "recently_sent"
            continue
        if budget <= 0:
            selection.suppressed[nudge.kind] = "daily_cap"
            continue
        selection.to_send.append(nudge)
        budget -= 1

    return selection


# ---------------------------------------------------------------------------
# Scheduling plan for on-device notifications
# ---------------------------------------------------------------------------

# Local times the client schedules notifications for. Both sit outside quiet
# hours by construction, and one notification per slot is what keeps delivery
# inside MAX_PUSHES_PER_DAY without needing a ledger.
MORNING_SLOT = (8, 10)
EVENING_SLOT = (19, 40)

# The brief is a digest of the individual morning-relevant rules, so when it is
# due it replaces them rather than firing alongside a subset of itself.
_DIGEST_KIND = "morning_brief"


@dataclass
class PlannedNudge:
    """One notification the device should schedule, and when to show it."""

    at: datetime
    nudge: Nudge

    def as_dict(self) -> dict[str, Any]:
        return {"at": self.at.isoformat(), **self.nudge.as_dict()}


def next_slot_times(now: datetime) -> list[datetime]:
    """The next occurrence of each daily slot, in local time, soonest first."""
    local_now = now.astimezone(LOCAL_TZ)
    found: list[datetime] = []

    for hour, minute in (MORNING_SLOT, EVENING_SLOT):
        slot = local_now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if slot <= local_now:
            slot += timedelta(days=1)
        found.append(slot)

    return sorted(found)


def plan(snapshot: TodaySnapshot, now: datetime | None = None) -> list[PlannedNudge]:
    """Decide what the device should show at each upcoming slot.

    The rules already take the evaluation time as a parameter, so each slot is
    scored as if it were that moment — which is how a slot hours away still
    gets the rule set appropriate to its time of day.

    What it cannot do is predict *data*: a slot is evaluated against today's
    snapshot, so an evening plan built at breakfast reflects breakfast. The
    client re-requests this often enough that the plan in force at fire time is
    only minutes old; see ``docs/notifications.md``.
    """
    now = now or datetime.now(UTC)
    planned: list[PlannedNudge] = []

    for at in next_slot_times(now):
        due = [n for n in evaluate(snapshot, at) if n.channel == "push"]
        if not due:
            continue

        digest = next((n for n in due if n.kind == _DIGEST_KIND), None)
        # evaluate() has already sorted by priority, so due[0] is the most
        # urgent when there is no digest to prefer.
        planned.append(PlannedNudge(at=at, nudge=digest or due[0]))

    return planned


def plan_for_user(user_id: str, now: datetime | None = None) -> list[PlannedNudge]:
    """Convenience wrapper that loads the snapshot and builds the plan."""
    now = now or datetime.now(UTC)
    return plan(today_snapshot(user_id, now=now), now=now)
