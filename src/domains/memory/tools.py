"""Memory domain tools — remember, recall, and forget personal facts.

The ``notes`` table and the retrieval layer already existed but nothing wrote
to them, so RAG context was always empty. These tools give the assistant an
explicit way to persist things worth keeping ("I'm allergic to shellfish",
"my gym is closed on Sundays") and to look them up later.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import or_

from src.context import current_user_id
from src.memory.db import Note, SessionLocal
from src.tools.registry import ToolDefinition, ToolRegistry
from src.types import DomainName, PermissionLevel

# Notes carry their metadata as JSON in the (otherwise unused) embedding blob,
# matching what RetrievalLayer already does.
_CATEGORIES = ["preference", "fact", "person", "routine", "goal", "other"]


def _encode(metadata: dict[str, Any]) -> bytes | None:
    return json.dumps(metadata).encode("utf-8") if metadata else None


def _decode(payload: bytes | None) -> dict[str, Any]:
    if not payload:
        return {}
    try:
        data = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _summarise(note: Note) -> dict[str, Any]:
    metadata = _decode(note.embedding)
    return {
        "id": note.id,
        "content": note.content,
        "category": metadata.get("category", "other"),
        "remembered_at": note.created_at.isoformat() if note.created_at else None,
    }


# ---------------------------------------------------------------------------
# remember
# ---------------------------------------------------------------------------


class RememberTool(ToolDefinition):
    """Persist a fact about the user for later recall."""

    name = "memory.remember"
    description = (
        "Save a durable fact, preference, or detail about the user so it can be "
        "recalled in future conversations. Use for things worth keeping across "
        "days (allergies, equipment, routines, people, goals) — not for "
        "transient chat."
    )
    domain = DomainName.memory
    permission_level = PermissionLevel.write
    requires_confirmation = False
    input_schema = {
        "type": "object",
        "properties": {
            "content": {
                "type": "string",
                "description": "The fact to remember, written as a standalone sentence.",
            },
            "category": {
                "type": ["string", "null"],
                "enum": _CATEGORIES + [None],
                "description": "Rough kind of fact, for grouping.",
            },
        },
        "required": ["content"],
        "additionalProperties": False,
    }
    output_schema = {
        "type": "object",
        "properties": {
            "id": {"type": "string"},
            "content": {"type": "string"},
            "category": {"type": "string"},
            "replaced": {"type": "boolean"},
        },
        "required": ["content"],
    }

    def execute(self, inputs: dict[str, Any]) -> Any:
        content = (inputs.get("content") or "").strip()
        if not content:
            raise ValueError("Nothing to remember: content was empty.")

        category = inputs.get("category") or "other"
        if category not in _CATEGORIES:
            category = "other"

        user_id = current_user_id()
        note_id = str(uuid.uuid4())

        with SessionLocal() as session:
            # Re-stating the same fact updates it rather than piling up copies.
            existing = (
                session.query(Note)
                .filter(Note.user_id == user_id, Note.content.ilike(content))
                .first()
            )
            if existing is not None:
                existing.created_at = datetime.now(UTC)
                existing.embedding = _encode({"category": category})
                note_id = existing.id
                session.commit()
                return {
                    "id": note_id,
                    "content": content,
                    "category": category,
                    "replaced": True,
                }

            session.add(
                Note(
                    id=note_id,
                    user_id=user_id,
                    content=content,
                    embedding=_encode({"category": category}),
                    created_at=datetime.now(UTC),
                )
            )
            session.commit()

        return {"id": note_id, "content": content, "category": category, "replaced": False}

    def rollback(self, context: dict[str, Any]) -> None:
        output = (context or {}).get("output") or {}
        note_id = output.get("id")
        if not note_id or output.get("replaced"):
            raise NotImplementedError("Nothing to undo for this remember call.")
        with SessionLocal() as session:
            session.query(Note).filter(
                Note.id == note_id, Note.user_id == current_user_id()
            ).delete()
            session.commit()


# ---------------------------------------------------------------------------
# recall
# ---------------------------------------------------------------------------


class RecallTool(ToolDefinition):
    """Look up previously remembered facts."""

    name = "memory.recall"
    description = (
        "Search facts previously saved with memory.remember. Use when the user "
        "asks what you know about them, or when a saved preference would change "
        "your answer. Omit the query to list the most recent."
    )
    domain = DomainName.memory
    permission_level = PermissionLevel.read
    requires_confirmation = False
    input_schema = {
        "type": "object",
        "properties": {
            "query": {
                "type": ["string", "null"],
                "description": "Words to search for. Omit to list recent memories.",
            },
            "category": {"type": ["string", "null"], "enum": _CATEGORIES + [None]},
            "limit": {"type": ["integer", "null"], "minimum": 1, "maximum": 50},
        },
        "additionalProperties": False,
    }
    output_schema = {
        "type": "object",
        "properties": {
            "memories": {"type": "array"},
            "count": {"type": "integer"},
        },
        "required": ["memories"],
    }

    def execute(self, inputs: dict[str, Any]) -> Any:
        query = (inputs.get("query") or "").strip()
        category = inputs.get("category")
        limit = int(inputs.get("limit") or 10)

        with SessionLocal() as session:
            statement = session.query(Note).filter(Note.user_id == current_user_id())
            terms = [t for t in query.lower().split() if t]
            if terms:
                statement = statement.filter(
                    or_(*[Note.content.ilike(f"%{term}%") for term in terms])
                )
            notes = statement.order_by(Note.created_at.desc()).limit(limit * 3).all()

        results = [_summarise(note) for note in notes]
        if category:
            results = [r for r in results if r["category"] == category]

        if terms:
            # Rank by how many of the search terms each note actually contains.
            def _score(item: dict[str, Any]) -> int:
                haystack = item["content"].lower()
                return sum(1 for term in terms if term in haystack)

            results.sort(key=_score, reverse=True)

        results = results[:limit]
        return {"memories": results, "count": len(results)}


# ---------------------------------------------------------------------------
# forget
# ---------------------------------------------------------------------------


class ForgetTool(ToolDefinition):
    """Delete a remembered fact."""

    name = "memory.forget"
    description = (
        "Delete a previously remembered fact, by id or by matching text. Use "
        "when the user asks you to forget something or corrects a stale fact."
    )
    domain = DomainName.memory
    permission_level = PermissionLevel.write
    requires_confirmation = True
    input_schema = {
        "type": "object",
        "properties": {
            "id": {"type": ["string", "null"], "description": "Exact memory id."},
            "query": {
                "type": ["string", "null"],
                "description": "Text to match when the id is unknown.",
            },
        },
        "additionalProperties": False,
    }
    output_schema = {
        "type": "object",
        "properties": {
            "forgotten": {"type": "array"},
            "count": {"type": "integer"},
        },
        "required": ["count"],
    }

    def execute(self, inputs: dict[str, Any]) -> Any:
        note_id = inputs.get("id")
        query = (inputs.get("query") or "").strip()
        if not note_id and not query:
            raise ValueError("Provide either an id or a query describing what to forget.")

        user_id = current_user_id()
        with SessionLocal() as session:
            statement = session.query(Note).filter(Note.user_id == user_id)
            if note_id:
                statement = statement.filter(Note.id == note_id)
            else:
                statement = statement.filter(Note.content.ilike(f"%{query}%"))

            notes = statement.all()
            forgotten = [_summarise(note) for note in notes]
            for note in notes:
                session.delete(note)
            session.commit()

        return {"forgotten": forgotten, "count": len(forgotten)}


# ---------------------------------------------------------------------------
# undo_last
# ---------------------------------------------------------------------------

# Tools whose effects can be reversed after the fact. Anything not listed is
# either read-only or has no meaningful inverse.
UNDOABLE_TOOLS = (
    "gym.log_workout",
    "nutrition.log_meal",
    "nutrition.log_water",
    "nutrition.set_goals",
    "productivity.create_task",
    "memory.remember",
)


class UndoLastTool(ToolDefinition):
    """Reverse the most recent undoable action.

    The orchestrator already rolls back a partially-applied plan when a step
    fails. This covers the other case: the plan succeeded, but it was not what
    the user meant — a misheard "log 100kg bench" that needs taking back.
    """

    name = "memory.undo_last"
    description = (
        "Undo the most recent logged action — a workout, meal, water entry, "
        "task, nutrition goal, or saved memory. Use when the user says undo, "
        "cancel that, remove that, or that was wrong."
    )
    domain = DomainName.memory
    permission_level = PermissionLevel.write
    requires_confirmation = True
    input_schema = {
        "type": "object",
        "properties": {
            "tool_name": {
                "type": ["string", "null"],
                "description": (
                    "Restrict the undo to one action type, e.g. 'nutrition.log_meal'. "
                    "Omit to undo whatever happened most recently."
                ),
            }
        },
        "additionalProperties": False,
    }
    output_schema = {
        "type": "object",
        "properties": {
            "undone": {"type": "boolean"},
            "tool_name": {"type": ["string", "null"]},
            "description": {"type": "string"},
        },
        "required": ["undone"],
    }

    def __init__(self, registry: ToolRegistry, audit_log: Any) -> None:
        self._registry = registry
        self._audit_log = audit_log

    def execute(self, inputs: dict[str, Any]) -> Any:
        wanted = inputs.get("tool_name")
        candidates = (wanted,) if wanted else UNDOABLE_TOOLS
        if wanted and wanted not in UNDOABLE_TOOLS:
            return {
                "undone": False,
                "tool_name": wanted,
                "description": f"{wanted} cannot be undone.",
            }

        entry = self._latest_undoable(candidates)
        if entry is None:
            return {
                "undone": False,
                "tool_name": None,
                "description": "Nothing recent to undo.",
            }

        tool = self._registry.get(entry.tool_name)
        tool.rollback({"inputs": entry.inputs, "output": entry.output})

        return {
            "undone": True,
            "tool_name": entry.tool_name,
            "description": _describe(entry.tool_name, entry.inputs),
        }

    def _latest_undoable(self, tool_names: tuple[str, ...]):
        """Most recent successful entry among *tool_names*, if any."""
        best = None
        for tool_name in tool_names:
            for entry in self._audit_log.query(tool_name=tool_name):
                if entry.error is not None or entry.output is None:
                    continue
                if best is None or entry.timestamp > best.timestamp:
                    best = entry
        return best


def _describe(tool_name: str, inputs: Any) -> str:
    """A short human phrase for what was undone."""
    values = inputs if isinstance(inputs, dict) else {}
    label = {
        "gym.log_workout": values.get("exercise"),
        "nutrition.log_meal": values.get("food_item"),
        "nutrition.log_water": values.get("amount_ml"),
        "productivity.create_task": values.get("title"),
        "memory.remember": values.get("content"),
    }.get(tool_name)
    subject = tool_name.split(".", 1)[-1].replace("_", " ")
    return f"Undid {subject}: {label}" if label else f"Undid {subject}."


def register_memory_tools(
    registry: ToolRegistry, audit_log: Any | None = None
) -> None:
    """Register every memory tool with *registry*.

    ``undo_last`` needs to reach other tools' ``rollback()`` and read the
    audit log, so it is constructed with both rather than importing them.
    """
    registry.register(RememberTool())
    registry.register(RecallTool())
    registry.register(ForgetTool())
    if audit_log is not None:
        registry.register(UndoLastTool(registry, audit_log))
