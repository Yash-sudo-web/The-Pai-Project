"""Review domain tools — weekly summary and on-demand nudge check."""

from __future__ import annotations

from typing import Any

from src.context import current_user_id
from src.domains.review.aggregate import weekly_stats
from src.domains.review.nudges import due_for_user
from src.tools.registry import ToolDefinition, ToolRegistry
from src.types import DomainName, PermissionLevel


class WeeklyReviewTool(ToolDefinition):
    """Aggregate the week across gym, nutrition, and tasks in one call."""

    name = "review.weekly"
    description = (
        "Summarise the last week (or N days) across workouts, nutrition, and "
        "tasks in a single pass. Use for 'how was my week', 'weekly review', "
        "'how have I been doing'."
    )
    domain = DomainName.review
    permission_level = PermissionLevel.read
    requires_confirmation = False
    input_schema = {
        "type": "object",
        "properties": {
            "days": {
                "type": ["integer", "null"],
                "minimum": 1,
                "maximum": 90,
                "description": "How many days back to cover. Defaults to 7.",
            }
        },
        "additionalProperties": False,
    }
    output_schema = {
        "type": "object",
        "properties": {
            "period_start": {"type": "string"},
            "period_end": {"type": "string"},
            "gym": {"type": "object"},
            "nutrition": {"type": "object"},
            "productivity": {"type": "object"},
        },
        "required": ["period_start", "period_end"],
    }

    def execute(self, inputs: dict[str, Any]) -> Any:
        days = int(inputs.get("days") or 7)
        return weekly_stats(current_user_id(), days=days).as_dict()


class CheckNudgesTool(ToolDefinition):
    """Report anything currently worth the user's attention."""

    name = "review.check_nudges"
    description = (
        "Check whether anything needs attention right now — overdue tasks, "
        "protein or water behind target, a long gap since the last workout. "
        "Use for 'anything I should know', 'am I on track', 'what's pending'."
    )
    domain = DomainName.review
    permission_level = PermissionLevel.read
    requires_confirmation = False
    input_schema = {"type": "object", "properties": {}, "additionalProperties": False}
    output_schema = {
        "type": "object",
        "properties": {"nudges": {"type": "array"}, "count": {"type": "integer"}},
        "required": ["nudges", "count"],
    }

    def execute(self, inputs: dict[str, Any]) -> Any:
        nudges = due_for_user(current_user_id())
        return {"nudges": [n.as_dict() for n in nudges], "count": len(nudges)}


def register_review_tools(registry: ToolRegistry) -> None:
    """Register the review tools with *registry*."""
    registry.register(WeeklyReviewTool())
    registry.register(CheckNudgesTool())
