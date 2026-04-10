"""Intent parser for the Personal AI Assistant Orchestrator.

Calls the LLM client, parses the JSON response into an ``IntentPlan``,
and handles all failure modes by returning a ``ClarificationRequest``.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Union

from pydantic import BaseModel, ValidationError

from src.orchestrator.llm import LLMClient, LLMError
from src.tracing import traceable_if_available
from src.tools.registry import ToolDefinition
from src.types import AuditEntry, DomainName, IntentPlan


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


class ClarificationRequest(BaseModel):
    """Returned when the parser cannot produce a valid IntentPlan."""

    question: str


# Union type for the parse result
ParseResult = Union[IntentPlan, ClarificationRequest]


# ---------------------------------------------------------------------------
# IntentParser
# ---------------------------------------------------------------------------


class IntentParser:
    """Parses natural language commands into structured ``IntentPlan`` objects.

    Args:
        llm_client: The LLM client used to generate intent JSON.
        audit_log: Optional audit log; failures are recorded when provided.
    """

    def __init__(
        self,
        llm_client: LLMClient,
        audit_log=None,  # AuditLog | None — avoid circular import at type level
    ) -> None:
        self._llm = llm_client
        self._audit_log = audit_log

    async def parse(
        self,
        command: str,
        tools: list[ToolDefinition],
        session_id: str = "default",
    ) -> ParseResult:
        """Parse a natural language command into a ``ParseResult``.

        Args:
            command: The user's natural language command.
            tools: Registered tools whose schemas are injected into the prompt.
            session_id: Session identifier used in audit log entries.

        Returns:
            An ``IntentPlan`` on success, or a ``ClarificationRequest`` on any
            failure or ambiguity.
        """
        raw: str | None = None
        parsed_domain: DomainName | None = None

        # --- Step 1: call LLM ---
        try:
            raw = await self._llm.complete(command, tools)
        except LLMError as exc:
            self._log_failure(
                command=command,
                session_id=session_id,
                domain=None,
                error=str(exc),
            )
            return ClarificationRequest(
                question="I'm having trouble connecting to the AI service. Please try again."
            )

        # --- Step 2: parse JSON ---
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, ValueError) as exc:
            self._log_failure(
                command=command,
                session_id=session_id,
                domain=None,
                error=str(exc),
            )
            return ClarificationRequest(
                question="I couldn't understand that. Could you rephrase?"
            )

        # --- Step 3: check for clarify action_type ---
        if isinstance(data, dict) and data.get("action_type") == "clarify":
            return ClarificationRequest(question="Could you clarify your request?")

        # --- Step 4: validate against IntentPlan schema ---
        try:
            plan = IntentPlan.model_validate(data)
        except ValidationError as exc:
            # Try to extract domain from raw data for better audit context
            if isinstance(data, dict):
                try:
                    parsed_domain = DomainName(data.get("domain", ""))
                except ValueError:
                    parsed_domain = None

            self._log_failure(
                command=command,
                session_id=session_id,
                domain=parsed_domain,
                error=str(exc),
            )
            return ClarificationRequest(
                question="I couldn't parse your request. Could you be more specific?"
            )

        return plan

    parse = traceable_if_available("intent_parser.parse")(parse)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _log_failure(
        self,
        command: str,
        session_id: str,
        domain: DomainName | None,
        error: str,
    ) -> None:
        """Record a parse failure to the audit log if one is configured."""
        if self._audit_log is None:
            return

        entry = AuditEntry(
            id=str(uuid.uuid4()),
            timestamp=datetime.now(tz=timezone.utc),
            tool_name="orchestrator.intent_parser",
            domain=domain if domain is not None else DomainName.system_control,
            inputs={"command": command},
            output=None,
            error=error,
            approval_status="not_required",
            session_id=session_id,
        )
        self._audit_log.record(entry)
