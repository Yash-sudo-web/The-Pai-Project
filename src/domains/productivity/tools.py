"""Productivity domain tools — create_task, complete_task, daily_summary.

Implements ToolDefinition subclasses for task lifecycle management and
daily productivity reporting.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone, timedelta
from typing import Any

from src.memory.db import SessionLocal, Task
from src.tools.registry import ToolDefinition, ToolRegistry
from src.types import DomainName, PermissionLevel


# ---------------------------------------------------------------------------
# create_task
# ---------------------------------------------------------------------------

_CREATE_TASK_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "due_date": {"type": ["string", "null"], "format": "date-time"},
    },
    "required": ["title"],
    "additionalProperties": False,
}

_CREATE_TASK_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "id": {"type": "string"},
        "title": {"type": "string"},
        "status": {"type": "string"},
        "created_at": {"type": "string"},
    },
    "required": ["id", "title", "status", "created_at"],
}


class CreateTaskTool(ToolDefinition):
    """Create a new task with status 'pending'."""

    name = "productivity.create_task"
    description = "Create a new task with a title and optional due date."
    domain = DomainName.productivity
    permission_level = PermissionLevel.write
    requires_confirmation = False
    input_schema = _CREATE_TASK_INPUT_SCHEMA
    output_schema = _CREATE_TASK_OUTPUT_SCHEMA

    def execute(self, inputs: dict[str, Any]) -> Any:
        task_id = str(uuid.uuid4())
        now = datetime.now(tz=timezone.utc)

        due_date = None
        if inputs.get("due_date"):
            due_date = datetime.fromisoformat(inputs["due_date"])

        task = Task(
            id=task_id,
            user_id="default_user",
            title=inputs["title"],
            due_date=due_date,
            status="pending",
            created_at=now,
        )

        with SessionLocal() as session:
            try:
                session.add(task)
                session.commit()
            except Exception:
                session.rollback()
                raise

        return {
            "id": task_id,
            "title": inputs["title"],
            "status": "pending",
            "created_at": now.isoformat(),
        }

    def rollback(self, context: dict[str, Any]) -> None:
        """Delete the task created by a previous execute()."""
        task_id = context.get("output", {}).get("id")
        if not task_id:
            return

        with SessionLocal() as session:
            try:
                task = session.query(Task).filter(Task.id == task_id).first()
                if task:
                    session.delete(task)
                    session.commit()
            except Exception:
                session.rollback()
                raise


# ---------------------------------------------------------------------------
# complete_task
# ---------------------------------------------------------------------------

_COMPLETE_TASK_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "task_id": {"type": "string"},
    },
    "required": ["task_id"],
    "additionalProperties": False,
}

_COMPLETE_TASK_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "id": {"type": "string"},
        "title": {"type": "string"},
        "status": {"type": "string"},
        "completed_at": {"type": "string"},
    },
    "required": ["id", "title", "status", "completed_at"],
}


class CompleteTaskTool(ToolDefinition):
    """Mark an existing task as completed."""

    name = "productivity.complete_task"
    description = "Mark a task as completed by its ID."
    domain = DomainName.productivity
    permission_level = PermissionLevel.write
    requires_confirmation = False
    input_schema = _COMPLETE_TASK_INPUT_SCHEMA
    output_schema = _COMPLETE_TASK_OUTPUT_SCHEMA

    def execute(self, inputs: dict[str, Any]) -> Any:
        task_id = inputs["task_id"]
        now = datetime.now(tz=timezone.utc)

        with SessionLocal() as session:
            try:
                task = session.query(Task).filter(Task.id == task_id).first()
                if task is None:
                    raise ValueError(f"Task with id '{task_id}' not found.")

                task.status = "completed"
                task.completed_at = now
                session.commit()

                return {
                    "id": task.id,
                    "title": task.title,
                    "status": "completed",
                    "completed_at": now.isoformat(),
                }
            except Exception:
                session.rollback()
                raise


# ---------------------------------------------------------------------------
# daily_summary
# ---------------------------------------------------------------------------

_DAILY_SUMMARY_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {},
    "additionalProperties": False,
}

_DAILY_SUMMARY_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "pending": {"type": "integer"},
        "completed": {"type": "integer"},
        "overdue": {"type": "integer"},
    },
    "required": ["pending", "completed", "overdue"],
}


class ProductivityDailySummaryTool(ToolDefinition):
    """Return counts of pending, completed, and overdue tasks."""

    name = "productivity.daily_summary"
    description = "Get counts of pending, completed, and overdue tasks."
    domain = DomainName.productivity
    permission_level = PermissionLevel.read
    requires_confirmation = False
    input_schema = _DAILY_SUMMARY_INPUT_SCHEMA
    output_schema = _DAILY_SUMMARY_OUTPUT_SCHEMA

    def execute(self, inputs: dict[str, Any]) -> Any:
        now = datetime.now(tz=timezone.utc)

        with SessionLocal() as session:
            all_tasks = session.query(Task).all()

        pending = 0
        completed = 0
        overdue = 0

        for t in all_tasks:
            if t.status == "completed":
                completed += 1
            elif t.status == "pending":
                if t.due_date is not None:
                    # Handle both naive and aware datetimes from the DB
                    due = t.due_date
                    cmp_now = now if due.tzinfo is not None else now.replace(tzinfo=None)
                    if due < cmp_now:
                        overdue += 1
                    else:
                        pending += 1
                else:
                    pending += 1
            elif t.status == "overdue":
                overdue += 1

        return {
            "pending": pending,
            "completed": completed,
            "overdue": overdue,
        }

# ---------------------------------------------------------------------------
# list_tasks
# ---------------------------------------------------------------------------

_LIST_TASKS_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "status": {
            "type": ["string", "null"],
            "enum": ["pending", "completed", "overdue", None],
            "description": "Filter by status. Omit to return all tasks.",
        },
        "search": {
            "type": ["string", "null"],
            "description": "Optional search term to filter tasks by title (case-insensitive substring match).",
        },
        "limit": {
            "type": ["integer", "null"],
            "description": "Maximum number of tasks to return. Defaults to 20.",
        },
    },
    "additionalProperties": False,
}

_LIST_TASKS_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "tasks": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "title": {"type": "string"},
                    "status": {"type": "string"},
                    "due_date": {"type": ["string", "null"]},
                    "created_at": {"type": "string"},
                    "completed_at": {"type": ["string", "null"]},
                },
            },
        },
        "total_count": {"type": "integer"},
    },
    "required": ["tasks", "total_count"],
}


class ListTasksTool(ToolDefinition):
    """List and search tasks with optional status and title filters."""

    name = "productivity.list_tasks"
    description = (
        "List tasks with optional filtering. Use 'status' to filter by "
        "pending/completed/overdue, and 'search' to find tasks by title. "
        "Returns task IDs that can be used with complete_task."
    )
    domain = DomainName.productivity
    permission_level = PermissionLevel.read
    requires_confirmation = False
    input_schema = _LIST_TASKS_INPUT_SCHEMA
    output_schema = _LIST_TASKS_OUTPUT_SCHEMA

    def execute(self, inputs: dict[str, Any]) -> Any:
        status_filter = inputs.get("status")
        search_term = inputs.get("search")
        limit = inputs.get("limit") or 20

        with SessionLocal() as session:
            query = session.query(Task).filter(Task.user_id == "default_user")

            if status_filter:
                query = query.filter(Task.status == status_filter)

            if search_term:
                query = query.filter(Task.title.ilike(f"%{search_term}%"))

            tasks = (
                query
                .order_by(Task.created_at.desc())
                .limit(limit)
                .all()
            )

            total_count = query.count()

        return {
            "tasks": [
                {
                    "id": t.id,
                    "title": t.title,
                    "status": t.status,
                    "due_date": t.due_date.isoformat() if t.due_date else None,
                    "created_at": t.created_at.isoformat(),
                    "completed_at": t.completed_at.isoformat() if t.completed_at else None,
                }
                for t in tasks
            ],
            "total_count": total_count,
        }


# ---------------------------------------------------------------------------
# Registration helper
# ---------------------------------------------------------------------------


def register_productivity_tools(registry: ToolRegistry) -> None:
    """Register all Productivity domain tools into *registry*."""
    registry.register(CreateTaskTool())
    registry.register(CompleteTaskTool())
    registry.register(ProductivityDailySummaryTool())
    registry.register(ListTasksTool())
