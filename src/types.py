"""Core shared types and interfaces for the Personal AI Assistant."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field


class DomainName(str, Enum):
    """Supported domain modules."""

    gym = "gym"
    nutrition = "nutrition"
    productivity = "productivity"
    system_control = "system_control"


class PermissionLevel(str, Enum):
    """Permission hierarchy: read < write < execute < admin."""

    read = "read"
    write = "write"
    execute = "execute"
    admin = "admin"

    def __le__(self, other: "PermissionLevel") -> bool:
        order = [
            PermissionLevel.read,
            PermissionLevel.write,
            PermissionLevel.execute,
            PermissionLevel.admin,
        ]
        return order.index(self) <= order.index(other)

    def __lt__(self, other: "PermissionLevel") -> bool:
        return self != other and self <= other

    def __ge__(self, other: "PermissionLevel") -> bool:
        return other <= self

    def __gt__(self, other: "PermissionLevel") -> bool:
        return other < self


class ToolInvocation(BaseModel):
    """A single tool call with its inputs."""

    tool_name: str
    inputs: dict[str, Any]


class IntentPlan(BaseModel):
    """Structured plan produced by the Orchestrator's intent parser."""

    action_type: str
    domain: DomainName
    parameters: dict[str, Any]
    steps: list[ToolInvocation]
    requires_confirmation: bool


class AuditEntry(BaseModel):
    """Append-only audit log record for every tool invocation."""

    id: str
    timestamp: datetime
    tool_name: str
    domain: DomainName
    inputs: Any
    output: Any | None = None
    error: str | None = None
    approval_status: Literal["not_required", "approved", "rejected", "timeout"]
    session_id: str


class RetrievedRecord(BaseModel):
    """A record returned by the Retrieval Layer."""

    text: str
    score: float
    metadata: dict[str, Any] = Field(default_factory=dict)
