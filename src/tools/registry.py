"""Tool Registry — core definitions and registry for the Personal AI Assistant.

Domain modules register their tools at startup by calling ``registry.register()``.
The Orchestrator discovers tools via ``registry.get()`` and ``registry.list_all()``
without ever importing domain modules directly.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from src.types import DomainName, PermissionLevel


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class DuplicateToolError(Exception):
    """Raised when a tool with the same name is registered more than once."""


class ToolNotFoundError(Exception):
    """Raised when a requested tool name is not found in the registry."""


# ---------------------------------------------------------------------------
# ToolDefinition — abstract base class
# ---------------------------------------------------------------------------


class ToolDefinition(ABC):
    """Abstract base class that every domain tool must implement.

    Subclasses must set the class-level (or instance-level) attributes and
    implement ``execute()``.  ``rollback()`` is optional; the default
    implementation raises ``NotImplementedError``.
    """

    name: str
    description: str
    domain: DomainName
    permission_level: PermissionLevel
    requires_confirmation: bool
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]

    @abstractmethod
    def execute(self, inputs: dict[str, Any]) -> Any:
        """Execute the tool with the given validated inputs."""

    def rollback(self, context: dict[str, Any]) -> None:
        """Undo the effect of a previous ``execute()`` call.

        Override in subclasses that support rollback.  The default
        implementation raises ``NotImplementedError`` to signal that this
        tool does not support rollback.
        """
        raise NotImplementedError(f"Tool '{self.name}' does not support rollback.")


# ---------------------------------------------------------------------------
# ToolRegistry
# ---------------------------------------------------------------------------


class ToolRegistry:
    """Central registry that maps tool names to ``ToolDefinition`` instances."""

    def __init__(self) -> None:
        self._tools: dict[str, ToolDefinition] = {}

    # ------------------------------------------------------------------
    # Mutation
    # ------------------------------------------------------------------

    def register(self, tool: ToolDefinition) -> None:
        """Register *tool* by its ``name`` attribute.

        Raises:
            DuplicateToolError: if a tool with the same name is already registered.
        """
        if tool.name in self._tools:
            raise DuplicateToolError(
                f"A tool named '{tool.name}' is already registered."
            )
        self._tools[tool.name] = tool

    # ------------------------------------------------------------------
    # Lookup
    # ------------------------------------------------------------------

    def get(self, name: str) -> ToolDefinition:
        """Return the tool registered under *name*.

        Raises:
            ToolNotFoundError: if no tool with that name exists.
        """
        try:
            return self._tools[name]
        except KeyError:
            raise ToolNotFoundError(f"No tool named '{name}' is registered.")

    # ------------------------------------------------------------------
    # Enumeration
    # ------------------------------------------------------------------

    def list_all(self) -> list[ToolDefinition]:
        """Return all registered tools in insertion order."""
        return list(self._tools.values())

    def list_by_domain(self, domain: DomainName) -> list[ToolDefinition]:
        """Return all tools belonging to *domain*."""
        return [t for t in self._tools.values() if t.domain == domain]
