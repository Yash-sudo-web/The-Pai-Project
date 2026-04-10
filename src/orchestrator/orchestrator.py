"""Orchestrator — planning loop for the Personal AI Assistant.

Coordinates intent parsing, permission checks, rate limiting, confirmation,
input/output validation, tool execution, and audit logging for every command.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel

from src.orchestrator.chat import ChatClient
from src.orchestrator.intent_parser import ClarificationRequest, IntentParser
from src.orchestrator.router import MessageRouter
from src.safety.audit import AuditLog
from src.safety.confirmation import ConfirmationLayer
from src.safety.permissions import AuthorizationError, PermissionSystem
from src.safety.rate_limiter import RateLimitError, RateLimiter
from src.tracing import traceable_if_available
from src.tools.registry import ToolRegistry
from src.tools.validation import (
    InputValidationError,
    OutputValidationError,
    validate_input,
    validate_output,
)
from src.types import AuditEntry, DomainName, PermissionLevel, ToolInvocation


# ---------------------------------------------------------------------------
# Public models
# ---------------------------------------------------------------------------


class SessionContext(BaseModel):
    """Carries session identity and permission grants for a single request."""

    session_id: str
    grants: list[PermissionLevel]


class OrchestratorResponse(BaseModel):
    """Returned by :meth:`Orchestrator.run` for every command."""

    success: bool
    message: str
    results: list[Any] = []
    clarification_question: str | None = None
    failed_step: int | None = None
    rollback_warnings: list[str] = []


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


class Orchestrator:
    """Central coordinator that runs the full planning loop.

    Parameters
    ----------
    intent_parser:
        Parses natural language commands into ``IntentPlan`` objects.
    tool_registry:
        Registry of all available tools.
    permission_system:
        Enforces per-tool permission level requirements.
    rate_limiter:
        Enforces per-tool sliding-window rate limits.
    confirmation_layer:
        Pauses execution on sensitive tools and awaits user approval.
    audit_log:
        Append-only log of every tool invocation.
    retrieval_layer:
        Optional RAG layer; when provided its context is prepended to the
        command before intent parsing.
    """

    def __init__(
        self,
        intent_parser: IntentParser,
        tool_registry: ToolRegistry,
        permission_system: PermissionSystem,
        rate_limiter: RateLimiter,
        confirmation_layer: ConfirmationLayer,
        audit_log: AuditLog,
        retrieval_layer=None,
        router: MessageRouter | None = None,
        chat_client: ChatClient | None = None,
    ) -> None:
        self._intent_parser = intent_parser
        self._tool_registry = tool_registry
        self._permission_system = permission_system
        self._rate_limiter = rate_limiter
        self._confirmation_layer = confirmation_layer
        self._audit_log = audit_log
        self._retrieval_layer = retrieval_layer
        self._router = router
        self._chat_client = chat_client

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def run(self, command: str, session: SessionContext) -> OrchestratorResponse:
        """Execute the full planning loop for *command* under *session*.

        Steps
        -----
        1. Optionally inject retrieval context into the command.
        2. Parse intent via the LLM-backed intent parser.
        3. For each step in the plan:
           a. Check permissions.
           b. Check rate limit.
           c. If confirmation required, await user approval.
           d. Validate inputs.
           e. Execute tool.
           f. Validate outputs.
           g. Log audit entry.
        4. Return a consolidated :class:`OrchestratorResponse`.
        """
        if self._router is not None:
            route = await self._router.route(command)
            if route.kind == "clarify":
                return OrchestratorResponse(
                    success=False,
                    message="Could you clarify your request?",
                    clarification_question="Could you clarify your request?",
                )
            if route.kind == "chat" and self._chat_client is not None:
                try:
                    reply = await self._chat_client.complete(command)
                except Exception as exc:
                    return OrchestratorResponse(
                        success=False,
                        message=f"Chat response failed: {exc}",
                    )
                return OrchestratorResponse(
                    success=True,
                    message=reply,
                    results=[],
                )

        enriched_command = await self._enrich_command(command)

        tools = self._tool_registry.list_all()
        parse_result = await self._intent_parser.parse(
            enriched_command, tools, session_id=session.session_id
        )

        if isinstance(parse_result, ClarificationRequest):
            return OrchestratorResponse(
                success=False,
                message=parse_result.question,
                clarification_question=parse_result.question,
            )

        plan = parse_result
        results: list[Any] = []
        # Each entry: (step_index, tool_name, inputs, tool_result)
        completed_steps: list[tuple[int, str, Any, Any]] = []

        for step_index, step in enumerate(plan.steps):
            tool_name = step.tool_name
            inputs = step.inputs
            approval_status = "not_required"

            # --- 1. Permission check ---
            try:
                tool = self._tool_registry.get(tool_name)
                self._permission_system.check(tool.permission_level, session.grants)
            except AuthorizationError as exc:
                self._record_audit(
                    tool_name=tool_name,
                    domain=plan.domain,
                    inputs=inputs,
                    output=None,
                    error=str(exc),
                    approval_status=approval_status,
                    session_id=session.session_id,
                )
                rollback_warnings = self._rollback_completed_steps(
                    completed_steps, plan.domain, session.session_id
                )
                return OrchestratorResponse(
                    success=False,
                    message=f"Permission denied at step {step_index}: {exc}",
                    results=results,
                    failed_step=step_index,
                    rollback_warnings=rollback_warnings,
                )
            except Exception as exc:
                self._record_audit(
                    tool_name=tool_name,
                    domain=plan.domain,
                    inputs=inputs,
                    output=None,
                    error=str(exc),
                    approval_status=approval_status,
                    session_id=session.session_id,
                )
                rollback_warnings = self._rollback_completed_steps(
                    completed_steps, plan.domain, session.session_id
                )
                return OrchestratorResponse(
                    success=False,
                    message=f"Error at step {step_index}: {exc}",
                    results=results,
                    failed_step=step_index,
                    rollback_warnings=rollback_warnings,
                )

            # --- 2. Rate limit check ---
            try:
                self._rate_limiter.check(tool_name)
            except RateLimitError as exc:
                self._record_audit(
                    tool_name=tool_name,
                    domain=plan.domain,
                    inputs=inputs,
                    output=None,
                    error=str(exc),
                    approval_status=approval_status,
                    session_id=session.session_id,
                )
                rollback_warnings = self._rollback_completed_steps(
                    completed_steps, plan.domain, session.session_id
                )
                return OrchestratorResponse(
                    success=False,
                    message=f"Rate limit exceeded at step {step_index}: {exc}",
                    results=results,
                    failed_step=step_index,
                    rollback_warnings=rollback_warnings,
                )

            # --- 3. Confirmation (if required) ---
            if tool.requires_confirmation:
                invocation = ToolInvocation(tool_name=tool_name, inputs=inputs)
                confirmation_result = await self._confirmation_layer.request(invocation)
                approval_status = confirmation_result  # "approved" | "rejected" | "timeout"

                if confirmation_result != "approved":
                    self._record_audit(
                        tool_name=tool_name,
                        domain=plan.domain,
                        inputs=inputs,
                        output=None,
                        error=f"Confirmation {confirmation_result} at step {step_index}",
                        approval_status=approval_status,
                        session_id=session.session_id,
                    )
                    rollback_warnings = self._rollback_completed_steps(
                        completed_steps, plan.domain, session.session_id
                    )
                    return OrchestratorResponse(
                        success=False,
                        message=f"Action not approved at step {step_index}: confirmation was {confirmation_result}",
                        results=results,
                        failed_step=step_index,
                        rollback_warnings=rollback_warnings,
                    )

            # --- 4. Input validation ---
            try:
                validate_input(tool, inputs)
            except InputValidationError as exc:
                self._record_audit(
                    tool_name=tool_name,
                    domain=plan.domain,
                    inputs=inputs,
                    output=None,
                    error=str(exc),
                    approval_status=approval_status,
                    session_id=session.session_id,
                )
                rollback_warnings = self._rollback_completed_steps(
                    completed_steps, plan.domain, session.session_id
                )
                return OrchestratorResponse(
                    success=False,
                    message=f"Input validation failed at step {step_index}: {exc}",
                    results=results,
                    failed_step=step_index,
                    rollback_warnings=rollback_warnings,
                )

            # --- 5. Execute tool ---
            tool_result: Any = None
            try:
                tool_result = tool.execute(inputs)
            except Exception as exc:
                self._record_audit(
                    tool_name=tool_name,
                    domain=plan.domain,
                    inputs=inputs,
                    output=None,
                    error=str(exc),
                    approval_status=approval_status,
                    session_id=session.session_id,
                )
                rollback_warnings = self._rollback_completed_steps(
                    completed_steps, plan.domain, session.session_id
                )
                return OrchestratorResponse(
                    success=False,
                    message=f"Tool execution failed at step {step_index}: {exc}",
                    results=results,
                    failed_step=step_index,
                    rollback_warnings=rollback_warnings,
                )

            # --- 6. Output validation ---
            try:
                validate_output(tool, tool_result)
            except OutputValidationError as exc:
                self._record_audit(
                    tool_name=tool_name,
                    domain=plan.domain,
                    inputs=inputs,
                    output=tool_result,
                    error=str(exc),
                    approval_status=approval_status,
                    session_id=session.session_id,
                )
                rollback_warnings = self._rollback_completed_steps(
                    completed_steps, plan.domain, session.session_id
                )
                return OrchestratorResponse(
                    success=False,
                    message=f"Output validation failed at step {step_index}: {exc}",
                    results=results,
                    failed_step=step_index,
                    rollback_warnings=rollback_warnings,
                )

            # --- 7. Audit log ---
            self._record_audit(
                tool_name=tool_name,
                domain=plan.domain,
                inputs=inputs,
                output=tool_result,
                error=None,
                approval_status=approval_status,
                session_id=session.session_id,
            )

            # --- 8. Record result and mark step complete ---
            results.append(tool_result)
            completed_steps.append((step_index, tool_name, inputs, tool_result))
            self._rate_limiter.record(tool_name)

        summary_message = "Task completed successfully."
        if self._chat_client is not None and results:
            summary_message = await self._chat_client.generate_tool_summary_response(command, results)

        return OrchestratorResponse(
            success=True,
            message=summary_message,
            results=results,
        )

    run = traceable_if_available("orchestrator.run")(run)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _rollback_completed_steps(
        self,
        completed_steps: list[tuple[int, str, Any, Any]],
        domain: DomainName,
        session_id: str,
    ) -> list[str]:
        """Attempt to roll back all completed steps in reverse order.

        Parameters
        ----------
        completed_steps:
            List of ``(step_index, tool_name, inputs, tool_result)`` tuples
            recorded during the forward pass.
        domain:
            Domain of the current plan (used for audit entries).
        session_id:
            Session identifier (used for audit entries).

        Returns
        -------
        list[str]
            Warning messages for any rollback that failed with an unexpected
            exception (i.e. not ``NotImplementedError``).
        """
        warnings: list[str] = []

        for step_index, tool_name, inputs, result in reversed(completed_steps):
            try:
                tool = self._tool_registry.get(tool_name)
                context = {"inputs": inputs, "output": result}
                tool.rollback(context)
                # Rollback succeeded — log it
                self._record_audit(
                    tool_name=tool_name,
                    domain=domain,
                    inputs={"rollback_for_step": step_index, **inputs},
                    output={"rollback": "success"},
                    error=None,
                    approval_status="not_required",
                    session_id=session_id,
                )
            except NotImplementedError:
                # Tool doesn't support rollback — skip silently
                pass
            except Exception as exc:
                warning = (
                    f"Rollback failed for step {step_index} (tool '{tool_name}'): {exc}. "
                    "Manual intervention may be required."
                )
                warnings.append(warning)
                self._record_audit(
                    tool_name=tool_name,
                    domain=domain,
                    inputs={"rollback_for_step": step_index, **inputs},
                    output=None,
                    error=str(exc),
                    approval_status="not_required",
                    session_id=session_id,
                )

        return warnings

    async def _enrich_command(self, command: str) -> str:
        """Prepend retrieval context to *command* when a retrieval layer is set."""
        if self._retrieval_layer is None:
            return command

        try:
            context_records = await self._retrieval_layer.query(command, top_k=5)
        except Exception:
            # Retrieval failure is non-fatal; proceed without context
            return command

        if not context_records:
            return command

        context_text = "\n".join(
            f"- {record.text}" for record in context_records
        )
        return f"[Context]\n{context_text}\n\n[Command]\n{command}"

    def _record_audit(
        self,
        tool_name: str,
        domain: DomainName,
        inputs: Any,
        output: Any,
        error: str | None,
        approval_status: str,
        session_id: str,
    ) -> None:
        """Write an audit entry, swallowing any logging errors."""
        try:
            entry = AuditEntry(
                id=str(uuid.uuid4()),
                timestamp=datetime.now(tz=timezone.utc),
                tool_name=tool_name,
                domain=domain,
                inputs=inputs,
                output=output,
                error=error,
                approval_status=approval_status,  # type: ignore[arg-type]
                session_id=session_id,
            )
            self._audit_log.record(entry)
        except Exception:
            pass  # audit failures must not interrupt the main flow
