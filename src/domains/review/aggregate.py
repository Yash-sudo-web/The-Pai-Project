"""Cross-domain aggregation shared by the weekly review and the nudge rules.

Both features ask the same questions — what did the week look like, is the
user on track today — so the queries live here once rather than being
duplicated in a tool and a scheduler.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import func

from src.memory.db import Meal, NutritionGoal, SessionLocal, Task, WaterIntake, Workout


def _day_bounds(day: date) -> tuple[datetime, datetime]:
    start = datetime(day.year, day.month, day.day, tzinfo=UTC)
    return start, start + timedelta(days=1)


@dataclass
class WeeklyStats:
    start: date
    end: date
    workout_days: int
    workout_sets: int
    exercises: list[str]
    total_volume_kg: float
    days_logged_nutrition: int
    avg_calories: float
    avg_protein_g: float
    tasks_created: int
    tasks_completed: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "period_start": self.start.isoformat(),
            "period_end": self.end.isoformat(),
            "gym": {
                "days_trained": self.workout_days,
                "total_sets": self.workout_sets,
                "exercises": self.exercises,
                "total_volume_kg": round(self.total_volume_kg, 1),
            },
            "nutrition": {
                "days_logged": self.days_logged_nutrition,
                "avg_calories": round(self.avg_calories, 1),
                "avg_protein_g": round(self.avg_protein_g, 1),
            },
            "productivity": {
                "tasks_created": self.tasks_created,
                "tasks_completed": self.tasks_completed,
            },
        }


def weekly_stats(user_id: str, days: int = 7, today: date | None = None) -> WeeklyStats:
    """Aggregate the last *days* days of activity for *user_id*."""
    end = today or datetime.now(UTC).date()
    start = end - timedelta(days=days - 1)
    window_start = datetime(start.year, start.month, start.day, tzinfo=UTC)
    window_end = datetime(end.year, end.month, end.day, tzinfo=UTC) + timedelta(days=1)

    with SessionLocal() as db:
        workouts = (
            db.query(Workout)
            .filter(
                Workout.user_id == user_id,
                func.coalesce(Workout.worked_out_at, Workout.logged_at) >= window_start,
                func.coalesce(Workout.worked_out_at, Workout.logged_at) < window_end,
            )
            .all()
        )
        meals = (
            db.query(Meal)
            .filter(
                Meal.user_id == user_id,
                func.coalesce(Meal.eaten_at, Meal.logged_at) >= window_start,
                func.coalesce(Meal.eaten_at, Meal.logged_at) < window_end,
            )
            .all()
        )
        tasks_created = (
            db.query(func.count(Task.id))
            .filter(
                Task.user_id == user_id,
                Task.created_at >= window_start,
                Task.created_at < window_end,
            )
            .scalar()
            or 0
        )
        tasks_completed = (
            db.query(func.count(Task.id))
            .filter(
                Task.user_id == user_id,
                Task.status == "completed",
                Task.completed_at.isnot(None),
                Task.completed_at >= window_start,
                Task.completed_at < window_end,
            )
            .scalar()
            or 0
        )

    workout_days = {
        (w.worked_out_at or w.logged_at).date() for w in workouts if (w.worked_out_at or w.logged_at)
    }
    volume = sum(
        (w.sets or 1) * (w.reps or 0) * (w.weight_kg or 0)
        for w in workouts
        if w.weight_kg is not None
    )

    meal_days = {
        (m.eaten_at or m.logged_at).date() for m in meals if (m.eaten_at or m.logged_at)
    }
    day_count = max(len(meal_days), 1)

    return WeeklyStats(
        start=start,
        end=end,
        workout_days=len(workout_days),
        workout_sets=sum(w.sets or 1 for w in workouts),
        exercises=sorted({w.exercise for w in workouts}),
        total_volume_kg=volume,
        days_logged_nutrition=len(meal_days),
        avg_calories=sum(m.calories for m in meals) / day_count,
        avg_protein_g=sum(m.protein_g for m in meals) / day_count,
        tasks_created=int(tasks_created),
        tasks_completed=int(tasks_completed),
    )


# ---------------------------------------------------------------------------
# Today's state, used by the nudge rules
# ---------------------------------------------------------------------------


@dataclass
class TodaySnapshot:
    calories: float
    protein_g: float
    water_ml: float
    goal: dict[str, Any] | None
    days_since_workout: int | None
    overdue_tasks: list[str]
    pending_tasks: int


def today_snapshot(user_id: str, now: datetime | None = None) -> TodaySnapshot:
    """Everything the nudge rules need, in one pass over the database."""
    now = now or datetime.now(UTC)
    start, end = _day_bounds(now.date())

    with SessionLocal() as db:
        meals = (
            db.query(Meal)
            .filter(
                Meal.user_id == user_id,
                func.coalesce(Meal.eaten_at, Meal.logged_at) >= start,
                func.coalesce(Meal.eaten_at, Meal.logged_at) < end,
            )
            .all()
        )
        water = (
            db.query(func.sum(WaterIntake.amount_ml))
            .filter(
                WaterIntake.user_id == user_id,
                WaterIntake.logged_at >= start,
                WaterIntake.logged_at < end,
            )
            .scalar()
            or 0.0
        )
        goal_row = (
            db.query(NutritionGoal)
            .filter(NutritionGoal.user_id == user_id, NutritionGoal.is_active.is_(True))
            .order_by(NutritionGoal.created_at.desc())
            .first()
        )
        goal = (
            {
                "calories": goal_row.calories,
                "protein_g": goal_row.protein_g,
                "carbs_g": goal_row.carbs_g,
                "fat_g": goal_row.fat_g,
                "water_ml": goal_row.water_ml,
            }
            if goal_row
            else None
        )

        last_workout = (
            db.query(func.max(func.coalesce(Workout.worked_out_at, Workout.logged_at)))
            .filter(Workout.user_id == user_id)
            .scalar()
        )

        overdue = (
            db.query(Task)
            .filter(
                Task.user_id == user_id,
                Task.status == "pending",
                Task.due_date.isnot(None),
                Task.due_date < now,
            )
            .order_by(Task.due_date.asc())
            .limit(5)
            .all()
        )
        pending = (
            db.query(func.count(Task.id))
            .filter(Task.user_id == user_id, Task.status == "pending")
            .scalar()
            or 0
        )

    days_since = None
    if last_workout is not None:
        last = last_workout if last_workout.tzinfo else last_workout.replace(tzinfo=UTC)
        days_since = (now.date() - last.date()).days

    return TodaySnapshot(
        calories=sum(m.calories for m in meals),
        protein_g=sum(m.protein_g for m in meals),
        water_ml=float(water),
        goal=goal,
        days_since_workout=days_since,
        overdue_tasks=[t.title for t in overdue],
        pending_tasks=int(pending),
    )
