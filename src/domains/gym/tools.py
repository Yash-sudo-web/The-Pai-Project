"""Gym domain tools — log_workout and get_progress.

Implements ToolDefinition subclasses for workout logging (with partial-data
support for cardio entries) and progress report querying.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import and_

from src.memory.db import SessionLocal, Workout
from src.tools.registry import ToolDefinition, ToolRegistry
from src.types import DomainName, PermissionLevel


# ---------------------------------------------------------------------------
# log_workout
# ---------------------------------------------------------------------------

_LOG_WORKOUT_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "exercise": {"type": "string"},
        "sets": {"type": ["integer", "null"]},
        "reps": {"type": ["integer", "null"]},
        "weight_kg": {"type": ["number", "null"]},
        "duration_s": {"type": ["integer", "null"]},
        "distance_m": {"type": ["number", "null"]},
        "notes": {"type": ["string", "null"]},
    },
    "required": ["exercise"],
    "additionalProperties": False,
}

_LOG_WORKOUT_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "id": {"type": "string"},
        "exercise": {"type": "string"},
        "logged_at": {"type": "string"},
    },
    "required": ["id", "exercise", "logged_at"],
}


class LogWorkoutTool(ToolDefinition):
    """Log a workout entry to the database.

    Supports partial data — cardio entries may omit sets/reps/weight while
    strength entries may omit duration/distance.
    """

    name = "gym.log_workout"
    description = "Log a workout entry with exercise details."
    domain = DomainName.gym
    permission_level = PermissionLevel.write
    requires_confirmation = False
    input_schema = _LOG_WORKOUT_INPUT_SCHEMA
    output_schema = _LOG_WORKOUT_OUTPUT_SCHEMA

    def execute(self, inputs: dict[str, Any]) -> Any:
        workout_id = str(uuid.uuid4())
        now = datetime.now(tz=timezone.utc)

        workout = Workout(
            id=workout_id,
            user_id="default_user",
            exercise=inputs["exercise"],
            sets=inputs.get("sets"),
            reps=inputs.get("reps"),
            weight_kg=inputs.get("weight_kg"),
            duration_s=inputs.get("duration_s"),
            distance_m=inputs.get("distance_m"),
            notes=inputs.get("notes"),
            logged_at=now,
        )

        session = SessionLocal()
        try:
            session.add(workout)
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

        return {
            "id": workout_id,
            "exercise": inputs["exercise"],
            "logged_at": now.isoformat(),
        }

    def rollback(self, context: dict[str, Any]) -> None:
        """Delete the workout row created by a previous execute()."""
        workout_id = context.get("output", {}).get("id")
        if not workout_id:
            return

        session = SessionLocal()
        try:
            workout = session.query(Workout).filter(Workout.id == workout_id).first()
            if workout:
                session.delete(workout)
                session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()


# ---------------------------------------------------------------------------
# get_progress
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

        session = SessionLocal()
        try:
            query = session.query(Workout).filter(Workout.exercise == exercise)

            if start_date:
                dt_start = datetime.fromisoformat(start_date)
                query = query.filter(Workout.logged_at >= dt_start)
            if end_date:
                dt_end = datetime.fromisoformat(end_date)
                query = query.filter(Workout.logged_at <= dt_end)

            workouts = query.all()
        finally:
            session.close()

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
