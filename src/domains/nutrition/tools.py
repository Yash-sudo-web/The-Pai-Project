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

        session = SessionLocal()
        try:
            session.add(meal)
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

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

        session = SessionLocal()
        try:
            meal = session.query(Meal).filter(Meal.id == meal_id).first()
            if meal:
                session.delete(meal)
                session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()


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

        session = SessionLocal()
        try:
            meals = (
                session.query(Meal)
                .filter(Meal.logged_at >= day_start, Meal.logged_at < day_end)
                .all()
            )
        finally:
            session.close()

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
# Registration helper
# ---------------------------------------------------------------------------


def register_nutrition_tools(registry: ToolRegistry) -> None:
    """Register all Nutrition domain tools into *registry*."""
    registry.register(LogMealTool())
    registry.register(DailySummaryTool())
