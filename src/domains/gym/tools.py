"""Gym domain tools — workout logging, history, PR tracking, and progression.

Implements ToolDefinition subclasses for:
- Workout logging (with partial-data support for cardio entries)
- Workout history querying (by date, day-of-week, exercise, date range)
- Personal record (PR) tracking and detection
- Progressive overload suggestions
- Progress report querying
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import and_, func

from src.memory.db import SessionLocal, Workout
from src.tools.registry import ToolDefinition, ToolRegistry
from src.types import DomainName, PermissionLevel


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

# Day-of-week name → ISO weekday number (Monday=1 … Sunday=7)
_DAY_NAME_TO_ISO: dict[str, int] = {
    "monday": 1, "tuesday": 2, "wednesday": 3, "thursday": 4,
    "friday": 5, "saturday": 6, "sunday": 7,
}


def _group_workout_sessions(workouts: list) -> list[dict]:
    """Group flat workout rows into per-session, per-exercise summaries.

    Returns a list of dicts, one per unique (date, exercise) combination:
    ``{"date": "YYYY-MM-DD", "exercise": "...", "sets": [...], ...}``
    """
    # Key: (date_str, exercise_lower) → list of rows
    sessions: dict[tuple[str, str], list] = defaultdict(list)

    for w in workouts:
        dt = w.worked_out_at or w.logged_at
        date_str = dt.strftime("%Y-%m-%d") if dt else "unknown"
        key = (date_str, w.exercise.lower())
        sessions[key].append(w)

    result = []
    for (date_str, _), rows in sessions.items():
        exercise = rows[0].exercise
        sets_detail = []
        for r in rows:
            s = {}
            if r.sets is not None:
                s["sets"] = r.sets
            if r.reps is not None:
                s["reps"] = r.reps
            if r.weight_kg is not None:
                s["weight_kg"] = r.weight_kg
            if r.duration_s is not None:
                s["duration_s"] = r.duration_s
            if r.distance_m is not None:
                s["distance_m"] = r.distance_m
            sets_detail.append(s)

        weights = [r.weight_kg for r in rows if r.weight_kg is not None]
        total_sets = sum(r.sets or 1 for r in rows)

        entry: dict[str, Any] = {
            "date": date_str,
            "exercise": exercise,
            "total_sets": total_sets,
            "sets_detail": sets_detail,
        }
        if weights:
            entry["max_weight_kg"] = max(weights)
        # Volume = sum(sets * reps * weight) for rows that have all three
        vol = 0.0
        has_vol = False
        for r in rows:
            if r.sets is not None and r.reps is not None and r.weight_kg is not None:
                vol += r.sets * r.reps * r.weight_kg
                has_vol = True
        if has_vol:
            entry["total_volume_kg"] = round(vol, 1)

        if rows[0].notes:
            entry["notes"] = rows[0].notes
        result.append(entry)

    # Sort by date then exercise
    result.sort(key=lambda e: (e["date"], e["exercise"]))
    return result


def _compute_pr_info(exercise: str, user_id: str = "default_user") -> dict[str, Any]:
    """Return PR data for an exercise: max weight, max volume session, 1RM estimate."""
    with SessionLocal() as session:
        rows = (
            session.query(Workout)
            .filter(
                func.lower(Workout.exercise) == exercise.lower(),
                Workout.user_id == user_id,
            )
            .order_by(Workout.worked_out_at.desc().nullslast())
            .all()
        )

    if not rows:
        return {
            "exercise": exercise,
            "has_data": False,
            "max_weight_kg": None,
            "max_weight_date": None,
            "best_volume_kg": None,
            "best_volume_date": None,
            "estimated_1rm_kg": None,
            "total_sessions": 0,
        }

    # Max weight ever lifted
    max_w_row = max(
        (r for r in rows if r.weight_kg is not None),
        key=lambda r: r.weight_kg,
        default=None,
    )
    max_weight = max_w_row.weight_kg if max_w_row else None
    max_weight_date = None
    if max_w_row and (max_w_row.worked_out_at or max_w_row.logged_at):
        max_weight_date = (max_w_row.worked_out_at or max_w_row.logged_at).strftime("%Y-%m-%d")

    # Best single-session volume
    grouped = _group_workout_sessions(rows)
    best_vol = None
    best_vol_date = None
    for g in grouped:
        v = g.get("total_volume_kg")
        if v is not None and (best_vol is None or v > best_vol):
            best_vol = v
            best_vol_date = g["date"]

    # Estimate 1RM using Epley formula: 1RM = w * (1 + r/30)
    estimated_1rm = None
    for r in rows:
        if r.weight_kg and r.reps and r.reps > 0:
            e1rm = r.weight_kg * (1 + r.reps / 30.0)
            if estimated_1rm is None or e1rm > estimated_1rm:
                estimated_1rm = e1rm
    if estimated_1rm is not None:
        estimated_1rm = round(estimated_1rm, 1)

    # Count unique session dates
    dates = set()
    for r in rows:
        dt = r.worked_out_at or r.logged_at
        if dt:
            dates.add(dt.strftime("%Y-%m-%d"))

    return {
        "exercise": exercise,
        "has_data": True,
        "max_weight_kg": max_weight,
        "max_weight_date": max_weight_date,
        "best_volume_kg": best_vol,
        "best_volume_date": best_vol_date,
        "estimated_1rm_kg": estimated_1rm,
        "total_sessions": len(dates),
    }


# ---------------------------------------------------------------------------
# log_workout (enhanced with automatic PR detection)
# ---------------------------------------------------------------------------

_LOG_WORKOUT_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "exercise": {"type": "string"},
        "sets": {"type": ["integer", "null"],
                 "description": "Number of sets. When 'weights' array is provided, this is inferred from its length."},
        "reps": {"type": ["integer", "null"],
                 "description": "Reps per set (assumed same for all sets)."},
        "weight_kg": {"type": ["number", "null"],
                      "description": "Weight in kg (use this ONLY when all sets use the same weight). "
                                     "If sets have different weights, use 'weights' instead."},
        "weights": {"type": ["array", "null"],
                    "items": {"type": "number"},
                    "description": "Per-set weights in kg, e.g. [50, 55, 60, 60]. "
                                   "Length determines the number of sets. "
                                   "ALWAYS prefer this over 'weight_kg' when sets use different weights."},
        "duration_s": {"type": ["integer", "null"]},
        "distance_m": {"type": ["number", "null"]},
        "notes": {"type": ["string", "null"]},
        "worked_out_at": {"type": ["string", "null"], "format": "date-time",
                          "description": "When the workout was actually performed (ISO datetime). Defaults to now if omitted."},
    },
    "required": ["exercise"],
    "additionalProperties": False,
}

_LOG_WORKOUT_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "ids": {"type": "array", "items": {"type": "string"},
                "description": "IDs of the created workout rows (one per set)."},
        "exercise": {"type": "string"},
        "sets_logged": {"type": "integer"},
        "worked_out_at": {"type": "string"},
        "pr_alert": {"type": ["object", "null"],
                     "description": "Non-null if a new personal record was set."},
    },
    "required": ["ids", "exercise", "sets_logged"],
}


class LogWorkoutTool(ToolDefinition):
    """Log a workout entry to the database.

    Supports per-set weights via the ``weights`` array — each entry in
    the array creates a separate DB row with sets=1 so progress tracking
    is accurate.  When all sets share the same weight, use ``weight_kg``
    and ``sets`` instead.  Cardio entries may omit weight entirely.

    Automatically checks for personal records after logging and includes
    a ``pr_alert`` in the output if a new PR was set.
    """

    name = "gym.log_workout"
    description = (
        "Log a workout entry. When sets have DIFFERENT weights, pass a 'weights' "
        "array (e.g. [50, 55, 60, 60]) — one DB row per set is created automatically. "
        "When all sets share the SAME weight, pass 'weight_kg' and 'sets'. "
        "Automatically detects if a new personal record (PR) was set."
    )
    domain = DomainName.gym
    permission_level = PermissionLevel.write
    requires_confirmation = False
    input_schema = _LOG_WORKOUT_INPUT_SCHEMA
    output_schema = _LOG_WORKOUT_OUTPUT_SCHEMA

    def execute(self, inputs: dict[str, Any]) -> Any:
        now = datetime.now(tz=timezone.utc)
        exercise = inputs["exercise"]

        # Snapshot the old PRs BEFORE inserting
        old_pr = _compute_pr_info(exercise)

        # Parse worked_out_at if provided, otherwise default to now
        worked_out_at_raw = inputs.get("worked_out_at")
        if worked_out_at_raw:
            worked_out_at = datetime.fromisoformat(worked_out_at_raw)
        else:
            worked_out_at = now

        reps = inputs.get("reps")
        weights_array = inputs.get("weights")

        # Build a list of (sets_value, weight_value) for each row to create
        if weights_array:
            # Per-set weights — create one row per entry
            rows_spec = [(1, w) for w in weights_array]
        else:
            # Single weight_kg (or None for cardio) — one row
            rows_spec = [(inputs.get("sets"), inputs.get("weight_kg"))]

        created_ids: list[str] = []
        with SessionLocal() as session:
            try:
                for sets_val, weight_val in rows_spec:
                    workout_id = str(uuid.uuid4())
                    workout = Workout(
                        id=workout_id,
                        user_id="default_user",
                        exercise=exercise,
                        sets=sets_val,
                        reps=reps,
                        weight_kg=weight_val,
                        duration_s=inputs.get("duration_s"),
                        distance_m=inputs.get("distance_m"),
                        notes=inputs.get("notes"),
                        worked_out_at=worked_out_at,
                        logged_at=now,
                    )
                    session.add(workout)
                    created_ids.append(workout_id)
                session.commit()
            except Exception:
                session.rollback()
                raise

        # --- PR detection ---
        pr_alert = None
        new_pr = _compute_pr_info(exercise)
        if new_pr["has_data"]:
            pr_changes = []
            if (
                new_pr["max_weight_kg"] is not None
                and (old_pr["max_weight_kg"] is None or new_pr["max_weight_kg"] > old_pr["max_weight_kg"])
            ):
                pr_changes.append({
                    "type": "max_weight",
                    "new_value": new_pr["max_weight_kg"],
                    "old_value": old_pr.get("max_weight_kg"),
                    "unit": "kg",
                })
            if (
                new_pr["estimated_1rm_kg"] is not None
                and (old_pr["estimated_1rm_kg"] is None or new_pr["estimated_1rm_kg"] > old_pr["estimated_1rm_kg"])
            ):
                pr_changes.append({
                    "type": "estimated_1rm",
                    "new_value": new_pr["estimated_1rm_kg"],
                    "old_value": old_pr.get("estimated_1rm_kg"),
                    "unit": "kg",
                })
            if pr_changes:
                pr_alert = {
                    "exercise": exercise,
                    "records_broken": pr_changes,
                }

        return {
            "ids": created_ids,
            "exercise": exercise,
            "sets_logged": len(created_ids),
            "worked_out_at": worked_out_at.isoformat(),
            "pr_alert": pr_alert,
        }

    def rollback(self, context: dict[str, Any]) -> None:
        """Delete the workout rows created by a previous execute()."""
        output = context.get("output", {})
        # Support both new 'ids' list and legacy single 'id'
        ids = output.get("ids", [])
        if not ids and output.get("id"):
            ids = [output["id"]]
        if not ids:
            return

        with SessionLocal() as session:
            try:
                for wid in ids:
                    workout = session.query(Workout).filter(Workout.id == wid).first()
                    if workout:
                        session.delete(workout)
                session.commit()
            except Exception:
                session.rollback()
                raise


# ---------------------------------------------------------------------------
# get_workout_history
# ---------------------------------------------------------------------------

_GET_HISTORY_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "date": {
            "type": ["string", "null"],
            "format": "date",
            "description": (
                "Specific date (YYYY-MM-DD) to retrieve workouts for. "
                "Use this for 'what did I do on Monday' style queries "
                "(resolve the actual date first)."
            ),
        },
        "start_date": {
            "type": ["string", "null"],
            "format": "date",
            "description": "Start of date range (YYYY-MM-DD, inclusive).",
        },
        "end_date": {
            "type": ["string", "null"],
            "format": "date",
            "description": "End of date range (YYYY-MM-DD, inclusive).",
        },
        "exercise": {
            "type": ["string", "null"],
            "description": "Filter by exercise name (case-insensitive substring match).",
        },
        "day_of_week": {
            "type": ["string", "null"],
            "enum": ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday", None],
            "description": (
                "Get the most recent workout session on this weekday. "
                "Useful for 'what did I do last Wednesday' queries."
            ),
        },
        "limit": {
            "type": ["integer", "null"],
            "description": "Max number of session-days to return. Defaults to 7.",
        },
    },
    "additionalProperties": False,
}

_GET_HISTORY_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "sessions": {"type": "array"},
        "total_entries": {"type": "integer"},
        "query_description": {"type": "string"},
    },
    "required": ["sessions", "total_entries", "query_description"],
}


class GetWorkoutHistoryTool(ToolDefinition):
    """Retrieve workout history with flexible filtering.

    Supports querying by specific date, date range, day-of-week, and/or
    exercise name. Results are grouped into per-exercise session summaries
    showing sets, reps, weights, and volume.

    This is the primary tool for "what did I do last …" style questions.
    """

    name = "gym.get_workout_history"
    description = (
        "Retrieve workout history. Supports: specific date, date range, "
        "day_of_week (e.g. 'monday' for most recent Monday), and exercise "
        "filter. Returns grouped sessions with sets, reps, weights, and volume. "
        "Use this for 'what did I do last Monday', 'show my workouts this week', "
        "'what exercises did I do yesterday' style queries."
    )
    domain = DomainName.gym
    permission_level = PermissionLevel.read
    requires_confirmation = False
    input_schema = _GET_HISTORY_INPUT_SCHEMA
    output_schema = _GET_HISTORY_OUTPUT_SCHEMA

    def execute(self, inputs: dict[str, Any]) -> Any:
        date_str = inputs.get("date")
        start_str = inputs.get("start_date")
        end_str = inputs.get("end_date")
        exercise_filter = inputs.get("exercise")
        day_of_week = inputs.get("day_of_week")
        limit = inputs.get("limit") or 7
        query_parts = []

        with SessionLocal() as session:
            query = session.query(Workout).filter(Workout.user_id == "default_user")

            # --- Specific date ---
            if date_str:
                target = datetime.strptime(date_str, "%Y-%m-%d").date()
                day_start = datetime(target.year, target.month, target.day, tzinfo=timezone.utc)
                day_end = day_start + timedelta(days=1)
                query = query.filter(
                    Workout.worked_out_at >= day_start,
                    Workout.worked_out_at < day_end,
                )
                query_parts.append(f"on {date_str}")

            # --- Day of week (find most recent occurrence) ---
            elif day_of_week and day_of_week.lower() in _DAY_NAME_TO_ISO:
                target_dow = _DAY_NAME_TO_ISO[day_of_week.lower()]
                now = datetime.now(tz=timezone.utc)
                # Walk back up to 14 days to find the most recent target day
                for offset in range(1, 15):
                    candidate = now - timedelta(days=offset)
                    if candidate.isoweekday() == target_dow:
                        day_start = datetime(
                            candidate.year, candidate.month, candidate.day,
                            tzinfo=timezone.utc,
                        )
                        day_end = day_start + timedelta(days=1)
                        query = query.filter(
                            Workout.worked_out_at >= day_start,
                            Workout.worked_out_at < day_end,
                        )
                        query_parts.append(f"on last {day_of_week.capitalize()} ({day_start.strftime('%Y-%m-%d')})")
                        break

            # --- Date range ---
            else:
                if start_str:
                    dt_start = datetime.strptime(start_str, "%Y-%m-%d")
                    dt_start = dt_start.replace(tzinfo=timezone.utc)
                    query = query.filter(Workout.worked_out_at >= dt_start)
                    query_parts.append(f"from {start_str}")
                if end_str:
                    dt_end = datetime.strptime(end_str, "%Y-%m-%d")
                    dt_end = dt_end.replace(hour=23, minute=59, second=59, tzinfo=timezone.utc)
                    query = query.filter(Workout.worked_out_at <= dt_end)
                    query_parts.append(f"to {end_str}")

            # --- Exercise filter ---
            if exercise_filter:
                query = query.filter(func.lower(Workout.exercise).contains(exercise_filter.lower()))
                query_parts.append(f"exercise='{exercise_filter}'")

            workouts = (
                query
                .order_by(Workout.worked_out_at.desc().nullslast())
                .all()
            )

        grouped = _group_workout_sessions(workouts)

        # Apply session-day limit (group by date, take last N unique dates)
        if grouped:
            unique_dates = sorted(set(g["date"] for g in grouped), reverse=True)[:limit]
            date_set = set(unique_dates)
            grouped = [g for g in grouped if g["date"] in date_set]

        desc = "Workout history"
        if query_parts:
            desc += " " + ", ".join(query_parts)
        if not grouped:
            desc += " (no results found)"

        return {
            "sessions": grouped,
            "total_entries": len(grouped),
            "query_description": desc,
        }


# ---------------------------------------------------------------------------
# check_pr
# ---------------------------------------------------------------------------

_CHECK_PR_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "exercise": {
            "type": "string",
            "description": "Exercise name to check PRs for (case-insensitive).",
        },
    },
    "required": ["exercise"],
    "additionalProperties": False,
}

_CHECK_PR_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "exercise": {"type": "string"},
        "has_data": {"type": "boolean"},
        "max_weight_kg": {"type": ["number", "null"]},
        "max_weight_date": {"type": ["string", "null"]},
        "best_volume_kg": {"type": ["number", "null"]},
        "best_volume_date": {"type": ["string", "null"]},
        "estimated_1rm_kg": {"type": ["number", "null"]},
        "total_sessions": {"type": "integer"},
    },
    "required": ["exercise", "has_data"],
}


class CheckPRTool(ToolDefinition):
    """Check personal records for a given exercise.

    Returns the all-time max weight, best session volume, estimated 1RM
    (Epley formula), and total session count.
    """

    name = "gym.check_pr"
    description = (
        "Check personal records (PRs) for an exercise. Returns all-time max "
        "weight, best session volume, estimated 1RM, and total sessions. "
        "Use this when the user asks about their PRs, records, or best lifts."
    )
    domain = DomainName.gym
    permission_level = PermissionLevel.read
    requires_confirmation = False
    input_schema = _CHECK_PR_INPUT_SCHEMA
    output_schema = _CHECK_PR_OUTPUT_SCHEMA

    def execute(self, inputs: dict[str, Any]) -> Any:
        return _compute_pr_info(inputs["exercise"])


# ---------------------------------------------------------------------------
# suggest_progression
# ---------------------------------------------------------------------------

_SUGGEST_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "exercise": {
            "type": "string",
            "description": "Exercise to generate progression suggestions for.",
        },
        "num_recent_sessions": {
            "type": ["integer", "null"],
            "description": "Number of recent sessions to analyse. Defaults to 3.",
        },
    },
    "required": ["exercise"],
    "additionalProperties": False,
}

_SUGGEST_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "exercise": {"type": "string"},
        "has_history": {"type": "boolean"},
        "recent_sessions": {"type": "array"},
        "suggestions": {"type": "array"},
        "current_pr": {"type": "object"},
    },
    "required": ["exercise", "has_history"],
}


class SuggestProgressionTool(ToolDefinition):
    """Suggest progressive overload for an exercise based on recent history.

    Analyses the last N sessions and recommends weight increases, rep
    increases, or set increases following standard progressive overload
    principles:
    - If all sets hit target reps → increase weight by 2.5–5 kg
    - If not all sets hit target reps → keep weight, aim for more reps
    - If stalled for 3+ sessions → suggest a deload
    """

    name = "gym.suggest_progression"
    description = (
        "Suggest the next workout for an exercise based on progressive overload. "
        "Analyses recent sessions and recommends weight/rep/set changes. "
        "Use this when the user asks 'what should I do for bench press today', "
        "'what weight should I use', or wants training advice for a specific exercise."
    )
    domain = DomainName.gym
    permission_level = PermissionLevel.read
    requires_confirmation = False
    input_schema = _SUGGEST_INPUT_SCHEMA
    output_schema = _SUGGEST_OUTPUT_SCHEMA

    def execute(self, inputs: dict[str, Any]) -> Any:
        exercise = inputs["exercise"]
        num_sessions = inputs.get("num_recent_sessions") or 3

        with SessionLocal() as session:
            rows = (
                session.query(Workout)
                .filter(
                    func.lower(Workout.exercise) == exercise.lower(),
                    Workout.user_id == "default_user",
                )
                .order_by(Workout.worked_out_at.desc().nullslast())
                .all()
            )

        if not rows:
            return {
                "exercise": exercise,
                "has_history": False,
                "recent_sessions": [],
                "suggestions": [
                    {
                        "type": "start_fresh",
                        "message": (
                            f"No history found for '{exercise}'. Start with a comfortable "
                            "weight for 3 sets of 8-10 reps to establish a baseline."
                        ),
                    }
                ],
                "current_pr": _compute_pr_info(exercise),
            }

        # Group into sessions and take the most recent N
        grouped = _group_workout_sessions(rows)
        # Sort most-recent first for analysis
        grouped.sort(key=lambda g: g["date"], reverse=True)
        recent = grouped[:num_sessions]

        suggestions = self._generate_suggestions(exercise, recent)

        return {
            "exercise": exercise,
            "has_history": True,
            "recent_sessions": recent,
            "suggestions": suggestions,
            "current_pr": _compute_pr_info(exercise),
        }

    @staticmethod
    def _generate_suggestions(exercise: str, recent_sessions: list[dict]) -> list[dict]:
        """Generate progressive overload suggestions from recent sessions."""
        suggestions: list[dict] = []

        if not recent_sessions:
            return suggestions

        latest = recent_sessions[0]
        latest_sets = latest.get("sets_detail", [])

        # Extract key numbers from the latest session
        latest_weights = [s.get("weight_kg") for s in latest_sets if s.get("weight_kg") is not None]
        latest_reps = [s.get("reps") for s in latest_sets if s.get("reps") is not None]
        latest_max_weight = max(latest_weights) if latest_weights else None
        latest_num_sets = latest.get("total_sets", len(latest_sets))

        if not latest_weights:
            # Cardio / bodyweight — suggest duration or distance increase
            suggestions.append({
                "type": "general",
                "message": (
                    f"Last session ({latest['date']}): {latest_num_sets} sets. "
                    "Try adding a set or increasing duration/distance by 10%."
                ),
            })
            return suggestions

        # --- Check for stall (same max weight across last 3 sessions) ---
        if len(recent_sessions) >= 3:
            session_maxes = []
            for s in recent_sessions[:3]:
                ws = [sd.get("weight_kg") for sd in s.get("sets_detail", []) if sd.get("weight_kg") is not None]
                if ws:
                    session_maxes.append(max(ws))
            if len(session_maxes) == 3 and len(set(session_maxes)) == 1:
                suggestions.append({
                    "type": "deload",
                    "message": (
                        f"You've used {session_maxes[0]} kg for 3 sessions straight. "
                        f"Consider a deload: drop to {round(session_maxes[0] * 0.85, 1)} kg "
                        "for the same reps, then build back up next session."
                    ),
                })

        # --- Progressive overload suggestions ---
        avg_reps = sum(latest_reps) / len(latest_reps) if latest_reps else 0

        # Weight increase suggestion
        if latest_max_weight is not None:
            if avg_reps >= 10:
                increment = 5.0 if latest_max_weight >= 40 else 2.5
            elif avg_reps >= 6:
                increment = 2.5
            else:
                increment = 1.25  # Very heavy work — small increments

            new_weight = round(latest_max_weight + increment, 1)
            reps_str = f"{int(min(latest_reps))}–{int(max(latest_reps))}" if latest_reps and min(latest_reps) != max(latest_reps) else str(int(avg_reps)) if avg_reps else "?"

            suggestions.append({
                "type": "increase_weight",
                "message": (
                    f"Increase weight: {latest_num_sets} × {reps_str} reps "
                    f"at {new_weight} kg (up from {latest_max_weight} kg)."
                ),
                "recommended_weight_kg": new_weight,
                "recommended_sets": latest_num_sets,
                "recommended_reps": int(avg_reps) if avg_reps else None,
            })

        # Rep increase suggestion (keep same weight)
        if latest_max_weight is not None and avg_reps > 0:
            target_reps = int(avg_reps) + 2
            suggestions.append({
                "type": "increase_reps",
                "message": (
                    f"Keep weight at {latest_max_weight} kg, aim for "
                    f"{latest_num_sets} × {target_reps} reps (up from ~{int(avg_reps)})."
                ),
                "recommended_weight_kg": latest_max_weight,
                "recommended_sets": latest_num_sets,
                "recommended_reps": target_reps,
            })

        # Set increase suggestion
        if latest_num_sets < 5:
            suggestions.append({
                "type": "increase_sets",
                "message": (
                    f"Add a set: {latest_num_sets + 1} × {int(avg_reps)} reps "
                    f"at {latest_max_weight} kg."
                ),
                "recommended_weight_kg": latest_max_weight,
                "recommended_sets": latest_num_sets + 1,
                "recommended_reps": int(avg_reps) if avg_reps else None,
            })

        return suggestions


# ---------------------------------------------------------------------------
# get_progress (existing — kept for backwards compatibility)
# ---------------------------------------------------------------------------

_GET_PROGRESS_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "exercise": {"type": "string"},
        "start_date": {"type": ["string", "null"], "format": "date-time"},
        "end_date": {"type": ["string", "null"], "format": "date-time"},
    },
    "required": ["exercise"],
    "additionalProperties": False,
}

_GET_PROGRESS_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "exercise": {"type": "string"},
        "max_weight_kg": {"type": ["number", "null"]},
        "total_volume": {"type": ["number", "null"]},
        "session_count": {"type": "integer"},
    },
    "required": ["exercise", "max_weight_kg", "total_volume", "session_count"],
}


class GetProgressTool(ToolDefinition):
    """Query workout progress for a given exercise over a time range."""

    name = "gym.get_progress"
    description = "Get a progress report for a specified exercise and time range."
    domain = DomainName.gym
    permission_level = PermissionLevel.read
    requires_confirmation = False
    input_schema = _GET_PROGRESS_INPUT_SCHEMA
    output_schema = _GET_PROGRESS_OUTPUT_SCHEMA

    def execute(self, inputs: dict[str, Any]) -> Any:
        exercise = inputs["exercise"]
        start_date = inputs.get("start_date")
        end_date = inputs.get("end_date")

        with SessionLocal() as session:
            query = session.query(Workout).filter(Workout.exercise == exercise)

            if start_date:
                dt_start = datetime.fromisoformat(start_date)
                query = query.filter(Workout.logged_at >= dt_start)
            if end_date:
                dt_end = datetime.fromisoformat(end_date)
                query = query.filter(Workout.logged_at <= dt_end)

            workouts = query.all()

        if not workouts:
            return {
                "exercise": exercise,
                "max_weight_kg": None,
                "total_volume": None,
                "session_count": 0,
            }

        weights = [w.weight_kg for w in workouts if w.weight_kg is not None]
        max_weight = max(weights) if weights else None

        # Volume = sets * reps * weight for entries that have all three
        total_volume = 0.0
        has_volume = False
        for w in workouts:
            if w.sets is not None and w.reps is not None and w.weight_kg is not None:
                total_volume += w.sets * w.reps * w.weight_kg
                has_volume = True

        return {
            "exercise": exercise,
            "max_weight_kg": max_weight,
            "total_volume": total_volume if has_volume else None,
            "session_count": len(workouts),
        }


# ---------------------------------------------------------------------------
# Registration helper
# ---------------------------------------------------------------------------


def register_gym_tools(registry: ToolRegistry) -> None:
    """Register all Gym domain tools into *registry*."""
    registry.register(LogWorkoutTool())
    registry.register(GetProgressTool())
    registry.register(GetWorkoutHistoryTool())
    registry.register(CheckPRTool())
    registry.register(SuggestProgressionTool())

