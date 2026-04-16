"""Nutrition domain tools — log_meal and daily_summary.

Implements ToolDefinition subclasses for meal logging (with food DB lookup
and custom entry support) and daily nutritional summary queries.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone, timedelta
from typing import Any

from src.domains.nutrition import food_db
from src.memory.db import Meal, SessionLocal
from src.tools.registry import ToolDefinition, ToolRegistry
from src.types import DomainName, PermissionLevel


# ---------------------------------------------------------------------------
# Unit conversion helpers
# ---------------------------------------------------------------------------

# Approximate conversion to grams for common units
_UNIT_TO_GRAMS: dict[str, float] = {
    "g": 1.0,
    "gram": 1.0,
    "grams": 1.0,
    "kg": 1000.0,
    "oz": 28.3495,
    "lb": 453.592,
    "cup": 240.0,       # rough average
    "tbsp": 15.0,
    "tsp": 5.0,
    "ml": 1.0,          # approximate for water-like density
    "serving": 100.0,   # default serving = 100 g
    "piece": 100.0,     # default piece = 100 g
}

# Valid meal types
_VALID_MEAL_TYPES = {"breakfast", "lunch", "dinner", "snack"}


def _quantity_to_grams(quantity: float, unit: str) -> float:
    """Convert *quantity* in *unit* to grams."""
    factor = _UNIT_TO_GRAMS.get(unit.lower().strip(), 100.0)
    return quantity * factor


# ---------------------------------------------------------------------------
# log_meal
# ---------------------------------------------------------------------------

_LOG_MEAL_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "food_item": {"type": "string"},
        "quantity": {"type": "number"},
        "unit": {"type": "string"},
        "meal_type": {
            "type": ["string", "null"],
            "enum": ["breakfast", "lunch", "dinner", "snack", None],
            "description": "Type of meal: breakfast, lunch, dinner, or snack.",
        },
        "eaten_at": {
            "type": ["string", "null"],
            "description": (
                "ISO 8601 datetime string for when the meal was actually eaten. "
                "If not provided, defaults to the current time."
            ),
        },
        # Optional fields for custom entries (when food not in DB)
        "calories": {"type": ["number", "null"]},
        "protein_g": {"type": ["number", "null"]},
        "carbs_g": {"type": ["number", "null"]},
        "fat_g": {"type": ["number", "null"]},
    },
    "required": ["food_item", "quantity", "unit"],
    "additionalProperties": False,
}

_LOG_MEAL_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "id": {"type": "string"},
        "food_item": {"type": "string"},
        "calories": {"type": "number"},
        "protein_g": {"type": "number"},
        "carbs_g": {"type": "number"},
        "fat_g": {"type": "number"},
        "meal_type": {"type": ["string", "null"]},
        "eaten_at": {"type": ["string", "null"]},
        "logged_at": {"type": "string"},
        "is_custom": {"type": "boolean"},
        "prompt_for_custom": {"type": "boolean"},
    },
    "required": ["food_item"],
}


class LogMealTool(ToolDefinition):
    """Log a meal entry.

    Looks up the food item in the bundled food database.  If found, computes
    macros for the given quantity/unit.  If not found, checks whether manual
    nutritional values were provided; if yes, stores a custom entry; if no,
    returns a prompt asking the user for manual values.

    Supports optional ``meal_type`` (breakfast/lunch/dinner/snack) and
    ``eaten_at`` (ISO datetime) to record when the meal was actually consumed.
    """

    name = "nutrition.log_meal"
    description = (
        "Log a meal with nutritional information. Optionally specify "
        "meal_type (breakfast, lunch, dinner, snack) and eaten_at "
        "(ISO datetime for when the meal was eaten)."
    )
    domain = DomainName.nutrition
    permission_level = PermissionLevel.write
    requires_confirmation = False
    input_schema = _LOG_MEAL_INPUT_SCHEMA
    output_schema = _LOG_MEAL_OUTPUT_SCHEMA

    def execute(self, inputs: dict[str, Any]) -> Any:
        food_item = inputs["food_item"]
        quantity = inputs["quantity"]
        unit = inputs["unit"]

        nutrition = food_db.lookup(food_item)
        is_custom = False

        if nutrition is not None:
            # Scale from per-100g to actual quantity
            grams = _quantity_to_grams(quantity, unit)
            scale = grams / 100.0
            calories = nutrition["calories"] * scale
            protein_g = nutrition["protein_g"] * scale
            carbs_g = nutrition["carbs_g"] * scale
            fat_g = nutrition["fat_g"] * scale
        else:
            # Check for manual values
            manual_cal = inputs.get("calories")
            manual_prot = inputs.get("protein_g")
            manual_carb = inputs.get("carbs_g")
            manual_fat = inputs.get("fat_g")

            if manual_cal is not None and manual_prot is not None and manual_carb is not None and manual_fat is not None:
                calories = manual_cal
                protein_g = manual_prot
                carbs_g = manual_carb
                fat_g = manual_fat
                is_custom = True
            else:
                # Prompt user for manual input
                return {
                    "food_item": food_item,
                    "prompt_for_custom": True,
                    "is_custom": False,
                    "message": (
                        f"Food item '{food_item}' was not found in the database. "
                        "Please provide calories, protein_g, carbs_g, and fat_g manually."
                    ),
                }

        meal_id = str(uuid.uuid4())
        now = datetime.now(tz=timezone.utc)

        # --- meal_type ---
        meal_type_raw = inputs.get("meal_type")
        meal_type = meal_type_raw.lower().strip() if meal_type_raw else None
        if meal_type and meal_type not in _VALID_MEAL_TYPES:
            meal_type = None  # silently ignore invalid values

        # --- eaten_at ---
        eaten_at_raw = inputs.get("eaten_at")
        eaten_at = None
        if eaten_at_raw:
            try:
                eaten_at = datetime.fromisoformat(eaten_at_raw)
                # Ensure timezone-aware (assume UTC if naive)
                if eaten_at.tzinfo is None:
                    eaten_at = eaten_at.replace(tzinfo=timezone.utc)
            except (ValueError, TypeError):
                eaten_at = None  # ignore unparseable values

        meal = Meal(
            id=meal_id,
            user_id="default_user",
            food_item=food_item,
            quantity=quantity,
            unit=unit,
            calories=calories,
            protein_g=protein_g,
            carbs_g=carbs_g,
            fat_g=fat_g,
            is_custom=is_custom,
            meal_type=meal_type,
            eaten_at=eaten_at or now,
            logged_at=now,
        )

        with SessionLocal() as session:
            try:
                session.add(meal)
                session.commit()
            except Exception:
                session.rollback()
                raise

        return {
            "id": meal_id,
            "food_item": food_item,
            "calories": round(calories, 1),
            "protein_g": round(protein_g, 1),
            "carbs_g": round(carbs_g, 1),
            "fat_g": round(fat_g, 1),
            "meal_type": meal_type,
            "eaten_at": (eaten_at or now).isoformat(),
            "logged_at": now.isoformat(),
            "is_custom": is_custom,
            "prompt_for_custom": False,
        }

    def rollback(self, context: dict[str, Any]) -> None:
        """Delete the meal row created by a previous execute()."""
        meal_id = context.get("output", {}).get("id")
        if not meal_id:
            return

        with SessionLocal() as session:
            try:
                meal = session.query(Meal).filter(Meal.id == meal_id).first()
                if meal:
                    session.delete(meal)
                    session.commit()
            except Exception:
                session.rollback()
                raise


# ---------------------------------------------------------------------------
# daily_summary
# ---------------------------------------------------------------------------

_DAILY_SUMMARY_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "date": {"type": "string", "format": "date"},
    },
    "required": ["date"],
    "additionalProperties": False,
}

_DAILY_SUMMARY_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "date": {"type": "string"},
        "total_calories": {"type": "number"},
        "total_protein_g": {"type": "number"},
        "total_carbs_g": {"type": "number"},
        "total_fat_g": {"type": "number"},
        "meal_count": {"type": "integer"},
        "meals": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "food_item": {"type": "string"},
                    "calories": {"type": "number"},
                    "meal_type": {"type": ["string", "null"]},
                    "eaten_at": {"type": ["string", "null"]},
                },
            },
        },
    },
    "required": ["date", "total_calories", "total_protein_g", "total_carbs_g", "total_fat_g", "meal_count"],
}


class DailySummaryTool(ToolDefinition):
    """Return a nutritional summary for a given date."""

    name = "nutrition.daily_summary"
    description = "Get a nutritional summary (calories, macros, meal breakdown) for a specified date."
    domain = DomainName.nutrition
    permission_level = PermissionLevel.read
    requires_confirmation = False
    input_schema = _DAILY_SUMMARY_INPUT_SCHEMA
    output_schema = _DAILY_SUMMARY_OUTPUT_SCHEMA

    def execute(self, inputs: dict[str, Any]) -> Any:
        date_str = inputs["date"]
        target_date = datetime.strptime(date_str, "%Y-%m-%d").date()
        day_start = datetime(target_date.year, target_date.month, target_date.day, tzinfo=timezone.utc)
        day_end = day_start + timedelta(days=1)

        with SessionLocal() as session:
            meals = (
                session.query(Meal)
                .filter(Meal.logged_at >= day_start, Meal.logged_at < day_end)
                .all()
            )

        total_cal = sum(m.calories for m in meals)
        total_prot = sum(m.protein_g for m in meals)
        total_carb = sum(m.carbs_g for m in meals)
        total_fat = sum(m.fat_g for m in meals)

        meals_detail = [
            {
                "food_item": m.food_item,
                "calories": round(m.calories, 1),
                "meal_type": m.meal_type,
                "eaten_at": m.eaten_at.isoformat() if m.eaten_at else None,
            }
            for m in meals
        ]

        return {
            "date": date_str,
            "total_calories": round(total_cal, 1),
            "total_protein_g": round(total_prot, 1),
            "total_carbs_g": round(total_carb, 1),
            "total_fat_g": round(total_fat, 1),
            "meal_count": len(meals),
            "meals": meals_detail,
        }


# ---------------------------------------------------------------------------
# log_water
# ---------------------------------------------------------------------------

_LOG_WATER_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "amount_ml": {
            "type": "number",
            "description": "Amount of water in millilitres.",
        },
    },
    "required": ["amount_ml"],
    "additionalProperties": False,
}

_LOG_WATER_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "id": {"type": "string"},
        "amount_ml": {"type": "number"},
        "daily_total_ml": {"type": "number"},
        "logged_at": {"type": "string"},
    },
    "required": ["id", "amount_ml", "daily_total_ml", "logged_at"],
}


class LogWaterTool(ToolDefinition):
    """Log a water intake entry and return today's running total."""

    name = "nutrition.log_water"
    description = (
        "Log water intake in millilitres. Returns the amount logged and "
        "today's cumulative total. Example: 'I drank 500ml of water'."
    )
    domain = DomainName.nutrition
    permission_level = PermissionLevel.write
    requires_confirmation = False
    input_schema = _LOG_WATER_INPUT_SCHEMA
    output_schema = _LOG_WATER_OUTPUT_SCHEMA

    def execute(self, inputs: dict[str, Any]) -> Any:
        from src.memory.db import WaterIntake

        amount_ml = inputs["amount_ml"]
        now = datetime.now(tz=timezone.utc)
        entry_id = str(uuid.uuid4())

        today_start = datetime(now.year, now.month, now.day, tzinfo=timezone.utc)
        today_end = today_start + timedelta(days=1)

        with SessionLocal() as session:
            try:
                entry = WaterIntake(
                    id=entry_id,
                    user_id="default_user",
                    amount_ml=amount_ml,
                    logged_at=now,
                )
                session.add(entry)
                session.commit()
            except Exception:
                session.rollback()
                raise

            from sqlalchemy import func

            daily_total = (
                session.query(func.coalesce(func.sum(WaterIntake.amount_ml), 0.0))
                .filter(
                    WaterIntake.user_id == "default_user",
                    WaterIntake.logged_at >= today_start,
                    WaterIntake.logged_at < today_end,
                )
                .scalar()
            )

        return {
            "id": entry_id,
            "amount_ml": amount_ml,
            "daily_total_ml": round(float(daily_total), 1),
            "logged_at": now.isoformat(),
        }

    def rollback(self, context: dict[str, Any]) -> None:
        from src.memory.db import WaterIntake

        entry_id = context.get("output", {}).get("id")
        if not entry_id:
            return

        with SessionLocal() as session:
            try:
                entry = session.query(WaterIntake).filter(WaterIntake.id == entry_id).first()
                if entry:
                    session.delete(entry)
                    session.commit()
            except Exception:
                session.rollback()
                raise


# ---------------------------------------------------------------------------
# Shared helpers for goals
# ---------------------------------------------------------------------------

def _get_active_goal(user_id: str = "default_user") -> dict[str, Any] | None:
    """Return the active NutritionGoal for a user, or None."""
    from src.memory.db import NutritionGoal

    with SessionLocal() as session:
        goal = (
            session.query(NutritionGoal)
            .filter(NutritionGoal.user_id == user_id, NutritionGoal.is_active == True)
            .first()
        )
        if not goal:
            return None
        return {
            "id": goal.id,
            "calories": goal.calories,
            "protein_g": goal.protein_g,
            "carbs_g": goal.carbs_g,
            "fat_g": goal.fat_g,
            "water_ml": goal.water_ml,
            "effective_from": goal.effective_from.isoformat() if goal.effective_from else None,
        }


def _get_today_intake(user_id: str = "default_user") -> dict[str, Any]:
    """Return today's total meal intake and water for a user."""
    from src.memory.db import WaterIntake
    from sqlalchemy import func

    now = datetime.now(tz=timezone.utc)
    today_start = datetime(now.year, now.month, now.day, tzinfo=timezone.utc)
    today_end = today_start + timedelta(days=1)

    with SessionLocal() as session:
        meals = (
            session.query(Meal)
            .filter(
                Meal.user_id == user_id,
                Meal.logged_at >= today_start,
                Meal.logged_at < today_end,
            )
            .all()
        )

        water_total = (
            session.query(func.coalesce(func.sum(WaterIntake.amount_ml), 0.0))
            .filter(
                WaterIntake.user_id == user_id,
                WaterIntake.logged_at >= today_start,
                WaterIntake.logged_at < today_end,
            )
            .scalar()
        )

    total_cal = sum(m.calories for m in meals)
    total_prot = sum(m.protein_g for m in meals)
    total_carb = sum(m.carbs_g for m in meals)
    total_fat = sum(m.fat_g for m in meals)

    meal_types_eaten = set()
    meals_detail = []
    for m in meals:
        if m.meal_type:
            meal_types_eaten.add(m.meal_type)
        meals_detail.append({
            "food_item": m.food_item,
            "calories": round(m.calories, 1),
            "protein_g": round(m.protein_g, 1),
            "meal_type": m.meal_type,
            "eaten_at": m.eaten_at.isoformat() if m.eaten_at else None,
        })

    return {
        "total_calories": round(total_cal, 1),
        "total_protein_g": round(total_prot, 1),
        "total_carbs_g": round(total_carb, 1),
        "total_fat_g": round(total_fat, 1),
        "total_water_ml": round(float(water_total), 1),
        "meal_count": len(meals),
        "meal_types_eaten": sorted(meal_types_eaten),
        "meals": meals_detail,
    }


# ---------------------------------------------------------------------------
# set_goals
# ---------------------------------------------------------------------------

_SET_GOALS_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "calories": {
            "type": "number",
            "description": "Daily calorie target.",
        },
        "protein_g": {
            "type": "number",
            "description": "Daily protein target in grams.",
        },
        "carbs_g": {
            "type": "number",
            "description": "Daily carbs target in grams.",
        },
        "fat_g": {
            "type": "number",
            "description": "Daily fat target in grams.",
        },
        "water_ml": {
            "type": ["number", "null"],
            "description": "Daily water intake target in ml. Optional.",
        },
    },
    "required": ["calories", "protein_g", "carbs_g", "fat_g"],
    "additionalProperties": False,
}

_SET_GOALS_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "id": {"type": "string"},
        "calories": {"type": "number"},
        "protein_g": {"type": "number"},
        "carbs_g": {"type": "number"},
        "fat_g": {"type": "number"},
        "water_ml": {"type": ["number", "null"]},
        "effective_from": {"type": "string"},
        "replaced_previous": {"type": "boolean"},
    },
    "required": ["id", "calories", "protein_g", "carbs_g", "fat_g"],
}


class SetGoalsTool(ToolDefinition):
    """Set or update daily nutrition goals (calories + macros).

    Deactivates any existing goal for the user and creates a new one.
    Goal history is preserved — old goals are kept with ``is_active=False``.
    """

    name = "nutrition.set_goals"
    description = (
        "Set daily nutrition goals: calories, protein, carbs, fat, and "
        "optionally water. Replaces any existing goal. Example: "
        "'Set my daily goal to 2200 calories, 150g protein, 250g carbs, 70g fat'."
    )
    domain = DomainName.nutrition
    permission_level = PermissionLevel.write
    requires_confirmation = False
    input_schema = _SET_GOALS_INPUT_SCHEMA
    output_schema = _SET_GOALS_OUTPUT_SCHEMA

    def execute(self, inputs: dict[str, Any]) -> Any:
        from src.memory.db import NutritionGoal

        goal_id = str(uuid.uuid4())
        today = datetime.now(tz=timezone.utc).date()
        replaced = False

        with SessionLocal() as session:
            try:
                # Deactivate existing goal
                existing = (
                    session.query(NutritionGoal)
                    .filter(NutritionGoal.user_id == "default_user", NutritionGoal.is_active == True)
                    .first()
                )
                if existing:
                    existing.is_active = False
                    replaced = True

                new_goal = NutritionGoal(
                    id=goal_id,
                    user_id="default_user",
                    calories=inputs["calories"],
                    protein_g=inputs["protein_g"],
                    carbs_g=inputs["carbs_g"],
                    fat_g=inputs["fat_g"],
                    water_ml=inputs.get("water_ml"),
                    effective_from=today,
                    is_active=True,
                )
                session.add(new_goal)
                session.commit()
            except Exception:
                session.rollback()
                raise

        return {
            "id": goal_id,
            "calories": inputs["calories"],
            "protein_g": inputs["protein_g"],
            "carbs_g": inputs["carbs_g"],
            "fat_g": inputs["fat_g"],
            "water_ml": inputs.get("water_ml"),
            "effective_from": today.isoformat(),
            "replaced_previous": replaced,
        }

    def rollback(self, context: dict[str, Any]) -> None:
        from src.memory.db import NutritionGoal

        goal_id = context.get("output", {}).get("id")
        if not goal_id:
            return

        with SessionLocal() as session:
            try:
                goal = session.query(NutritionGoal).filter(NutritionGoal.id == goal_id).first()
                if goal:
                    session.delete(goal)
                    # Re-activate the previous goal if one exists
                    prev = (
                        session.query(NutritionGoal)
                        .filter(
                            NutritionGoal.user_id == "default_user",
                            NutritionGoal.is_active == False,
                        )
                        .order_by(NutritionGoal.created_at.desc())
                        .first()
                    )
                    if prev:
                        prev.is_active = True
                    session.commit()
            except Exception:
                session.rollback()
                raise


# ---------------------------------------------------------------------------
# check_goals
# ---------------------------------------------------------------------------

_CHECK_GOALS_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {},
    "additionalProperties": False,
}

_CHECK_GOALS_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "has_goals": {"type": "boolean"},
        "goals": {"type": ["object", "null"]},
        "consumed": {"type": "object"},
        "remaining": {"type": ["object", "null"]},
        "progress_pct": {"type": ["object", "null"]},
    },
    "required": ["has_goals", "consumed"],
}


class CheckGoalsTool(ToolDefinition):
    """Check today's nutrition progress against active goals.

    Returns consumed totals, remaining macros, and percentage progress.
    If no goals are set, still returns today's consumed totals.
    """

    name = "nutrition.check_goals"
    description = (
        "Check today's nutrition progress vs your daily goals. Returns "
        "consumed totals, remaining calories/macros, and percentage progress. "
        "Use this when the user asks 'how am I doing today', 'how many calories left', "
        "'am I on track', or checks their daily nutrition status."
    )
    domain = DomainName.nutrition
    permission_level = PermissionLevel.read
    requires_confirmation = False
    input_schema = _CHECK_GOALS_INPUT_SCHEMA
    output_schema = _CHECK_GOALS_OUTPUT_SCHEMA

    def execute(self, inputs: dict[str, Any]) -> Any:
        goal = _get_active_goal()
        intake = _get_today_intake()

        if not goal:
            return {
                "has_goals": False,
                "goals": None,
                "consumed": intake,
                "remaining": None,
                "progress_pct": None,
                "message": (
                    "No nutrition goals set yet. Use 'set my nutrition goals' "
                    "to configure daily targets."
                ),
            }

        remaining = {
            "calories": round(goal["calories"] - intake["total_calories"], 1),
            "protein_g": round(goal["protein_g"] - intake["total_protein_g"], 1),
            "carbs_g": round(goal["carbs_g"] - intake["total_carbs_g"], 1),
            "fat_g": round(goal["fat_g"] - intake["total_fat_g"], 1),
        }
        if goal.get("water_ml"):
            remaining["water_ml"] = round(goal["water_ml"] - intake["total_water_ml"], 1)

        def _pct(consumed: float, target: float) -> float:
            return round((consumed / target) * 100, 1) if target > 0 else 0.0

        progress = {
            "calories": _pct(intake["total_calories"], goal["calories"]),
            "protein_g": _pct(intake["total_protein_g"], goal["protein_g"]),
            "carbs_g": _pct(intake["total_carbs_g"], goal["carbs_g"]),
            "fat_g": _pct(intake["total_fat_g"], goal["fat_g"]),
        }
        if goal.get("water_ml"):
            progress["water_ml"] = _pct(intake["total_water_ml"], goal["water_ml"])

        return {
            "has_goals": True,
            "goals": goal,
            "consumed": intake,
            "remaining": remaining,
            "progress_pct": progress,
        }


# ---------------------------------------------------------------------------
# meal_suggestion
# ---------------------------------------------------------------------------

_MEAL_SUGGESTION_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "context": {
            "type": ["string", "null"],
            "description": (
                "Optional context from the user, e.g. 'I'm feeling hungry', "
                "'I want something light', 'I need a high-protein snack'."
            ),
        },
    },
    "additionalProperties": False,
}

_MEAL_SUGGESTION_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "time_of_day": {"type": "string"},
        "meal_type_suggested": {"type": "string"},
        "has_goals": {"type": "boolean"},
        "budget": {"type": ["object", "null"]},
        "suggestion_type": {"type": "string"},
        "suggestions": {"type": "array"},
        "reasoning": {"type": "string"},
    },
    "required": ["time_of_day", "suggestion_type", "suggestions", "reasoning"],
}


class MealSuggestionTool(ToolDefinition):
    """Suggest what to eat based on time, remaining macros, and context.

    Analyses:
    - Current time of day → appropriate meal type
    - Remaining macro budget (if goals are set)
    - Meals already eaten → what's missing
    - User context (hungry, want something light, etc.)

    Provides actionable suggestions from the food database.
    """

    name = "nutrition.meal_suggestion"
    description = (
        "Get a smart meal suggestion based on the time of day, remaining "
        "macros, and what you've already eaten. Use when the user says "
        "'I'm hungry', 'what should I eat', 'suggest a meal', or "
        "'I need a snack'. Considers calorie budget and nutrition goals."
    )
    domain = DomainName.nutrition
    permission_level = PermissionLevel.read
    requires_confirmation = False
    input_schema = _MEAL_SUGGESTION_INPUT_SCHEMA
    output_schema = _MEAL_SUGGESTION_OUTPUT_SCHEMA

    def execute(self, inputs: dict[str, Any]) -> Any:
        user_context = inputs.get("context", "")
        now = datetime.now(tz=timezone.utc)
        hour = now.hour

        # Determine time of day and suggested meal type
        if 5 <= hour < 11:
            time_of_day = "morning"
            default_meal = "breakfast"
        elif 11 <= hour < 15:
            time_of_day = "afternoon"
            default_meal = "lunch"
        elif 15 <= hour < 18:
            time_of_day = "afternoon_snack"
            default_meal = "snack"
        elif 18 <= hour < 22:
            time_of_day = "evening"
            default_meal = "dinner"
        else:
            time_of_day = "late_night"
            default_meal = "snack"

        goal = _get_active_goal()
        intake = _get_today_intake()
        meals_eaten = intake["meal_types_eaten"]

        # If the default meal type was already eaten, suggest snack
        if default_meal in meals_eaten and default_meal != "snack":
            suggested_meal = "snack"
        else:
            suggested_meal = default_meal

        # Compute remaining budget
        budget = None
        if goal:
            budget = {
                "calories": round(goal["calories"] - intake["total_calories"], 1),
                "protein_g": round(goal["protein_g"] - intake["total_protein_g"], 1),
                "carbs_g": round(goal["carbs_g"] - intake["total_carbs_g"], 1),
                "fat_g": round(goal["fat_g"] - intake["total_fat_g"], 1),
            }

        # Decide suggestion strategy
        suggestions = []
        reasoning = ""

        if budget and budget["calories"] <= 0:
            # Over calorie budget
            return {
                "time_of_day": time_of_day,
                "meal_type_suggested": suggested_meal,
                "has_goals": True,
                "budget": budget,
                "suggestion_type": "over_budget",
                "suggestions": [
                    {
                        "option": "Hold off",
                        "description": (
                            "You've already hit your calorie target for today. "
                            "If you can, try to hold off until tomorrow."
                        ),
                    },
                    {
                        "option": "Light snack",
                        "description": "If you really need something, try water, black coffee, or a small handful of raw veggies.",
                        "approx_calories": 20,
                    },
                ],
                "reasoning": (
                    f"You've consumed {intake['total_calories']} cal today "
                    f"against a {goal['calories']} cal target. You're over by "
                    f"{abs(budget['calories'])} calories."
                ),
            }

        if budget and budget["calories"] <= 200:
            # Very little calorie room
            suggestion_type = "light_snack"
            reasoning = (
                f"Only {budget['calories']} cal remaining today. "
                f"Suggesting very light options."
            )
            suggestions = self._suggest_light_options(budget)

        elif budget and budget["protein_g"] > 20 and budget["calories"] > 200:
            # Protein-focused suggestion
            suggestion_type = "high_protein"
            reasoning = (
                f"You still need {budget['protein_g']}g protein today with "
                f"{budget['calories']} cal to spare. Prioritising protein."
            )
            suggestions = self._suggest_high_protein(budget, suggested_meal)

        else:
            # General balanced suggestion
            suggestion_type = "balanced"
            if budget:
                reasoning = (
                    f"You have {budget['calories']} cal, {budget['protein_g']}g protein, "
                    f"{budget['carbs_g']}g carbs, {budget['fat_g']}g fat remaining."
                )
            else:
                reasoning = "No goals set — suggesting balanced options for this time of day."
            suggestions = self._suggest_balanced(suggested_meal, budget)

        return {
            "time_of_day": time_of_day,
            "meal_type_suggested": suggested_meal,
            "has_goals": goal is not None,
            "budget": budget,
            "suggestion_type": suggestion_type,
            "suggestions": suggestions,
            "reasoning": reasoning,
        }

    @staticmethod
    def _suggest_light_options(budget: dict) -> list[dict]:
        """Low-calorie options when budget is tight."""
        options = []
        cal_left = budget["calories"]

        light_foods = [
            ("yogurt", 100, "g", "Greek yogurt — high protein, low cal"),
            ("apple", 150, "g", "An apple — crunchy and filling"),
            ("broccoli", 200, "g", "Steamed broccoli — virtually free calories"),
            ("egg", 50, "g", "A hard-boiled egg — compact protein"),
        ]
        for name, qty, unit, desc in light_foods:
            info = food_db.lookup(name)
            if info:
                from src.domains.nutrition.tools import _quantity_to_grams
                grams = _quantity_to_grams(qty, unit)
                cal = info["calories"] * grams / 100
                if cal <= cal_left + 50:  # small tolerance
                    options.append({
                        "option": f"{qty}{unit} {name}",
                        "description": desc,
                        "approx_calories": round(cal),
                        "approx_protein_g": round(info["protein_g"] * grams / 100, 1),
                    })
        return options[:3]

    @staticmethod
    def _suggest_high_protein(budget: dict, meal_type: str) -> list[dict]:
        """Protein-rich options when protein target needs catching up."""
        options = []
        protein_foods = [
            ("chicken breast", 200, "g", "Grilled chicken breast"),
            ("tuna", 150, "g", "Canned tuna"),
            ("whey protein", 30, "g", "Protein shake"),
            ("egg", 150, "g", "3 eggs (scrambled or boiled)"),
            ("salmon", 150, "g", "Baked salmon fillet"),
            ("tofu", 200, "g", "Pan-fried tofu"),
        ]
        cal_left = budget.get("calories", 9999)
        for name, qty, unit, desc in protein_foods:
            info = food_db.lookup(name)
            if info:
                from src.domains.nutrition.tools import _quantity_to_grams
                grams = _quantity_to_grams(qty, unit)
                cal = info["calories"] * grams / 100
                prot = info["protein_g"] * grams / 100
                if cal <= cal_left + 100:
                    options.append({
                        "option": f"{qty}{unit} {name}",
                        "description": desc,
                        "approx_calories": round(cal),
                        "approx_protein_g": round(prot, 1),
                    })
        return options[:4]

    @staticmethod
    def _suggest_balanced(meal_type: str, budget: dict | None) -> list[dict]:
        """Balanced meal suggestions based on time of day."""
        breakfast_options = [
            {"option": "Oatmeal with banana", "description": "100g oatmeal + 1 banana — slow carbs and energy", "approx_calories": 157},
            {"option": "Eggs with toast", "description": "2 eggs + 2 slices bread — classic protein-rich breakfast", "approx_calories": 420},
            {"option": "Yogurt with almonds", "description": "200g yogurt + 20g almonds — protein and healthy fats", "approx_calories": 234},
        ]
        lunch_options = [
            {"option": "Chicken and rice", "description": "200g chicken breast + 150g rice — lean protein and carbs", "approx_calories": 525},
            {"option": "Dal with rice", "description": "200g lentils + 150g rice — high fiber vegetarian meal", "approx_calories": 427},
            {"option": "Salmon with sweet potato", "description": "150g salmon + 200g sweet potato — omega-3 and complex carbs", "approx_calories": 484},
        ]
        dinner_options = [
            {"option": "Chicken and veggies", "description": "200g chicken breast + 200g broccoli — light and high protein", "approx_calories": 398},
            {"option": "Tofu stir-fry with rice", "description": "200g tofu + 150g rice + veggies — balanced plant-based", "approx_calories": 347},
            {"option": "Beef with potato", "description": "150g beef + 200g potato — hearty and satisfying", "approx_calories": 529},
        ]
        snack_options = [
            {"option": "Protein shake", "description": "30g whey protein + water — quick 120cal, 24g protein", "approx_calories": 120},
            {"option": "Apple with peanut butter", "description": "1 apple + 15g peanut butter — sweet and filling", "approx_calories": 166},
            {"option": "Handful of almonds", "description": "25g almonds — healthy fats and protein", "approx_calories": 145},
        ]

        mapping = {
            "breakfast": breakfast_options,
            "lunch": lunch_options,
            "dinner": dinner_options,
            "snack": snack_options,
        }
        return mapping.get(meal_type, snack_options)


# ---------------------------------------------------------------------------
# nutrition_report
# ---------------------------------------------------------------------------

_REPORT_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "period": {
            "type": "string",
            "enum": ["week", "month"],
            "description": "Report period: 'week' (last 7 days) or 'month' (last 30 days).",
        },
    },
    "required": ["period"],
    "additionalProperties": False,
}

_REPORT_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "period": {"type": "string"},
        "start_date": {"type": "string"},
        "end_date": {"type": "string"},
        "total_days_tracked": {"type": "integer"},
        "total_meals": {"type": "integer"},
        "daily_averages": {"type": "object"},
        "goal_adherence": {"type": ["object", "null"]},
        "daily_breakdown": {"type": "array"},
    },
    "required": ["period", "start_date", "end_date", "total_days_tracked"],
}


class NutritionReportTool(ToolDefinition):
    """Generate a weekly or monthly nutrition report.

    Aggregates meal data over the requested period and computes:
    - Daily averages (calories, protein, carbs, fat)
    - Goal adherence (days where target was met for each macro)
    - Per-day breakdown for trend visibility
    """

    name = "nutrition.nutrition_report"
    description = (
        "Generate a weekly or monthly nutrition report. Shows daily averages, "
        "goal adherence, and per-day breakdown. Use when the user asks for "
        "'my nutrition this week', 'monthly report', 'how have I been eating', "
        "or wants to review trends."
    )
    domain = DomainName.nutrition
    permission_level = PermissionLevel.read
    requires_confirmation = False
    input_schema = _REPORT_INPUT_SCHEMA
    output_schema = _REPORT_OUTPUT_SCHEMA

    def execute(self, inputs: dict[str, Any]) -> Any:
        period = inputs["period"]
        now = datetime.now(tz=timezone.utc)
        days = 7 if period == "week" else 30

        end_date = now.date()
        start_date = end_date - timedelta(days=days - 1)
        dt_start = datetime(start_date.year, start_date.month, start_date.day, tzinfo=timezone.utc)
        dt_end = datetime(end_date.year, end_date.month, end_date.day, tzinfo=timezone.utc) + timedelta(days=1)

        with SessionLocal() as session:
            meals = (
                session.query(Meal)
                .filter(
                    Meal.user_id == "default_user",
                    Meal.logged_at >= dt_start,
                    Meal.logged_at < dt_end,
                )
                .order_by(Meal.logged_at)
                .all()
            )

        # Group meals by date
        from collections import defaultdict
        daily: dict[str, dict] = defaultdict(lambda: {
            "calories": 0.0, "protein_g": 0.0, "carbs_g": 0.0,
            "fat_g": 0.0, "meal_count": 0,
        })
        for m in meals:
            dt = m.logged_at or m.eaten_at
            day_key = dt.strftime("%Y-%m-%d") if dt else "unknown"
            daily[day_key]["calories"] += m.calories
            daily[day_key]["protein_g"] += m.protein_g
            daily[day_key]["carbs_g"] += m.carbs_g
            daily[day_key]["fat_g"] += m.fat_g
            daily[day_key]["meal_count"] += 1

        days_tracked = len(daily)

        # Daily averages
        if days_tracked > 0:
            avg = {
                "calories": round(sum(d["calories"] for d in daily.values()) / days_tracked, 1),
                "protein_g": round(sum(d["protein_g"] for d in daily.values()) / days_tracked, 1),
                "carbs_g": round(sum(d["carbs_g"] for d in daily.values()) / days_tracked, 1),
                "fat_g": round(sum(d["fat_g"] for d in daily.values()) / days_tracked, 1),
                "meals_per_day": round(sum(d["meal_count"] for d in daily.values()) / days_tracked, 1),
            }
        else:
            avg = {"calories": 0, "protein_g": 0, "carbs_g": 0, "fat_g": 0, "meals_per_day": 0}

        # Goal adherence
        goal = _get_active_goal()
        adherence = None
        if goal and days_tracked > 0:
            # A day "meets" a target if it's within ±10% of goal
            def _met(actual: float, target: float) -> bool:
                return actual >= target * 0.9

            cal_met = sum(1 for d in daily.values() if _met(d["calories"], goal["calories"]))
            prot_met = sum(1 for d in daily.values() if _met(d["protein_g"], goal["protein_g"]))
            carb_met = sum(1 for d in daily.values() if _met(d["carbs_g"], goal["carbs_g"]))
            fat_met = sum(1 for d in daily.values() if _met(d["fat_g"], goal["fat_g"]))

            adherence = {
                "calories": {"days_met": cal_met, "total_days": days_tracked, "pct": round(cal_met / days_tracked * 100, 1)},
                "protein_g": {"days_met": prot_met, "total_days": days_tracked, "pct": round(prot_met / days_tracked * 100, 1)},
                "carbs_g": {"days_met": carb_met, "total_days": days_tracked, "pct": round(carb_met / days_tracked * 100, 1)},
                "fat_g": {"days_met": fat_met, "total_days": days_tracked, "pct": round(fat_met / days_tracked * 100, 1)},
                "goal_targets": {
                    "calories": goal["calories"],
                    "protein_g": goal["protein_g"],
                    "carbs_g": goal["carbs_g"],
                    "fat_g": goal["fat_g"],
                },
            }

        # Per-day breakdown (sorted by date)
        breakdown = [
            {
                "date": day_key,
                "calories": round(data["calories"], 1),
                "protein_g": round(data["protein_g"], 1),
                "carbs_g": round(data["carbs_g"], 1),
                "fat_g": round(data["fat_g"], 1),
                "meal_count": data["meal_count"],
            }
            for day_key, data in sorted(daily.items())
        ]

        return {
            "period": period,
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
            "total_days_tracked": days_tracked,
            "total_meals": len(meals),
            "daily_averages": avg,
            "goal_adherence": adherence,
            "daily_breakdown": breakdown,
        }


# ---------------------------------------------------------------------------
# Registration helper
# ---------------------------------------------------------------------------


def register_nutrition_tools(registry: ToolRegistry) -> None:
    """Register all Nutrition domain tools into *registry*."""
    registry.register(LogMealTool())
    registry.register(DailySummaryTool())
    registry.register(LogWaterTool())
    registry.register(SetGoalsTool())
    registry.register(CheckGoalsTool())
    registry.register(MealSuggestionTool())
    registry.register(NutritionReportTool())
