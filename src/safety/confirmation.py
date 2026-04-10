"""Confirmation Layer for the Personal AI Assistant.

Intercepts sensitive tool invocations and requires explicit user approval
before execution proceeds. Pending confirmations time out after 60 seconds.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Literal

from src.types import AuditEntry, DomainName, ToolInvocation

if TYPE_CHECKING:
    from src.safety.audit import AuditLog

logger = logging.getLogger(__name__)

ConfirmationResult = Literal["approved", "rejected", "timeout"]


@dataclass
class PendingConfirmation:
    id: str
    tool_name: str
    inputs: object
    created_at: datetime
    timeout_seconds: float
    event: threading.Event = field(default_factory=threading.Event)
    result: ConfirmationResult | None = None


class ConfirmationLayer:
    """Pause execution on sensitive tools and await explicit user approval.

    Parameters
    ----------
    audit_log:
        Optional :class:`~src.safety.audit.AuditLog` instance used to record
        rejection and timeout events.
    timeout_seconds:
        How long (in seconds) to wait for a response before auto-cancelling.
        Defaults to 60.
    """

    def __init__(
        self,
        audit_log: "AuditLog | None" = None,
        timeout_seconds: float = 60.0,
    ) -> None:
        self._audit_log = audit_log
        self._timeout_seconds = timeout_seconds
        self._pending: dict[str, PendingConfirmation] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def request(self, invocation: ToolInvocation) -> ConfirmationResult:
        """Create a pending confirmation and wait for approval, rejection, or timeout.

        Parameters
        ----------
        invocation:
            The tool invocation awaiting confirmation.

        Returns
        -------
        ConfirmationResult
            ``"approved"``, ``"rejected"``, or ``"timeout"``.
        """
        confirmation_id = str(uuid.uuid4())
        pending = PendingConfirmation(
            id=confirmation_id,
            tool_name=invocation.tool_name,
            inputs=invocation.inputs,
            created_at=datetime.now(tz=timezone.utc),
            timeout_seconds=self._timeout_seconds,
        )
        self._pending[confirmation_id] = pending

        try:
            completed = await asyncio.to_thread(
                pending.event.wait,
                self._timeout_seconds,
            )
            if completed:
                result = pending.result or "rejected"
            else:
                raise asyncio.TimeoutError
        except asyncio.TimeoutError:
            result = "timeout"
            logger.warning(
                "Confirmation %s for tool '%s' timed out after %ss",
                confirmation_id,
                invocation.tool_name,
                self._timeout_seconds,
            )
            self._log_audit(
                invocation=invocation,
                approval_status="timeout",
                error=f"Confirmation timed out after {self._timeout_seconds:.0f}s",
            )
        finally:
            self._pending.pop(confirmation_id, None)

        return result

    def approve(self, id: str) -> None:
        """Resolve a pending confirmation as approved.

        Parameters
        ----------
        id:
            The UUID of the pending confirmation to approve.
        """
        pending = self._pending.get(id)
        if pending is None:
            logger.warning("approve() called for unknown or expired confirmation id=%s", id)
            return
        pending.result = "approved"
        pending.event.set()

    def reject(self, id: str) -> None:
        """Resolve a pending confirmation as rejected and log to the audit log.

        Parameters
        ----------
        id:
            The UUID of the pending confirmation to reject.
        """
        pending = self._pending.get(id)
        if pending is None:
            logger.warning("reject() called for unknown or expired confirmation id=%s", id)
            return
        pending.result = "rejected"
        pending.event.set()
        self._log_audit(
            invocation=ToolInvocation(tool_name=pending.tool_name, inputs=pending.inputs),
            approval_status="rejected",
            error=None,
        )

    def list_pending(self) -> list[dict[str, object]]:
        """Return snapshots of all currently pending confirmations."""
        return [
            {
                "id": pending.id,
                "tool_name": pending.tool_name,
                "inputs": pending.inputs,
                "created_at": pending.created_at,
                "timeout_seconds": pending.timeout_seconds,
            }
            for pending in self._pending.values()
        ]

    def get_pending(self, id: str) -> dict[str, object] | None:
        """Return a snapshot for a pending confirmation by id."""
        pending = self._pending.get(id)
        if pending is None:
            return None
        return {
            "id": pending.id,
            "tool_name": pending.tool_name,
            "inputs": pending.inputs,
            "created_at": pending.created_at,
            "timeout_seconds": pending.timeout_seconds,
        }

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _log_audit(
        self,
        invocation: ToolInvocation,
        approval_status: Literal["rejected", "timeout"],
        error: str | None,
    ) -> None:
        if self._audit_log is None:
            return

        # Attempt to derive domain from tool_name prefix (e.g. "system_control.launch_app")
        domain_str = invocation.tool_name.split(".")[0] if "." in invocation.tool_name else "system_control"
        try:
            domain = DomainName(domain_str)
        except ValueError:
            domain = DomainName.system_control

        entry = AuditEntry(
            id=str(uuid.uuid4()),
            timestamp=datetime.now(tz=timezone.utc),
            tool_name=invocation.tool_name,
            domain=domain,
            inputs=invocation.inputs,
            output=None,
            error=error,
            approval_status=approval_status,
            session_id="system",
        )
        try:
            self._audit_log.record(entry)
        except Exception:
            logger.exception("Failed to write audit entry for confirmation %s", approval_status)
