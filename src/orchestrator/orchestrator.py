"""Orchestrator — planning loop for the Personal AI Assistant.

Coordinates planning, permission checks, rate limiting, confirmation,
input/output validation, tool execution, and audit logging for every command.

A command costs at most two LLM round trips: one tool-calling request that
either answers directly or selects tools, and — only when tools ran — one
follow-up that turns the results into prose. Pure conversation costs one, and
trivial input costs none.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, AsyncIterator

from pydantic import BaseModel

from src.context import DEFAULT_USER_ID, user_scope
from src.orchestrator.chat import ChatClient, sanitize_tool_result
from src.orchestrator.llm import (
    LLMClient,
    LLMError,
    assistant_replay_message,
    build_function_name_map,
    decode_tool_calls,
)
from src.observability import CommandMetrics, metrics_registry
from src.safety.audit import AuditLog
from src.safety.confirmation import ConfirmationLayer
from src.safety.permissions import AuthorizationError, PermissionSystem
from src.safety.rate_limiter import RateLimitError, RateLimiter
from src.tracing import traceable_if_available
from src.tools.registry import ToolDefinition, ToolRegistry
from src.tools.validation import (
    InputValidationError,
    OutputValidationError,
    validate_input,
    validate_output,
)
from src.types import AuditEntry, DomainName, PermissionLevel, ToolInvocation

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Public models
# ---------------------------------------------------------------------------


class SessionContext(BaseModel):
    """Carries session identity and permission grants for a single request."""

    session_id: str
    grants: list[PermissionLevel]
    user_id: str = DEFAULT_USER_ID


class OrchestratorResponse(BaseModel):
    """Returned by :meth:`Orchestrator.run` for every command."""

    success: bool
    message: str
    results: list[Any] = []
    clarification_question: str | None = None
    failed_step: int | None = None
    rollback_warnings: list[str] = []


class StepFailure(Exception):
    """A single plan step could not complete.

    Carries everything the caller needs to audit the failure and build the
    user-facing message, so each phase of the pipeline raises instead of
    repeating the audit/rollback/return block.
    """

    def __init__(
        self,
        reason: str,
        detail: str,
        *,
        approval_status: str = "not_required",
        output: Any = None,
    ) -> None:
        super().__init__(detail)
        self.reason = reason
        self.detail = detail
        self.approval_status = approval_status
        self.output = output

    def user_message(self, step_index: int) -> str:
        return f"{self.reason} at step {step_index}: {self.detail}"


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


class Orchestrator:
    """Central coordinator that runs the full planning loop.

    Parameters
    ----------
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
    llm_client:
        Client used for planning and result summarisation.
    chat_client:
        Builds conversation context and persists turns.
    retrieval_layer:
        Optional RAG layer; when provided its context is prepended to the
        command before planning.
    """

    def __init__(
        self,
        tool_registry: ToolRegistry,
        permission_system: PermissionSystem,
        rate_limiter: RateLimiter,
        confirmation_layer: ConfirmationLayer,
        audit_log: AuditLog,
        llm_client: LLMClient,
        chat_client: ChatClient,
        retrieval_layer=None,
    ) -> None:
        self._tool_registry = tool_registry
        self._permission_system = permission_system
        self._rate_limiter = rate_limiter
        self._confirmation_layer = confirmation_layer
        self._audit_log = audit_log
        self._llm = llm_client
        self._chat_client = chat_client
        self._retrieval_layer = retrieval_layer

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def run(self, command: str, session: SessionContext) -> OrchestratorResponse:
        """Execute the full planning loop for *command* under *session*."""
        final: OrchestratorResponse | None = None
        async for event in self.run_stream(command, session):
            if event["type"] == "result":
                final = event["response"]
        if final is None:  # pragma: no cover - run_stream always emits a result
            final = OrchestratorResponse(success=False, message="No response produced.")
        return final

    run = traceable_if_available("orchestrator.run")(run)

    async def run_stream(
        self, command: str, session: SessionContext
    ) -> AsyncIterator[dict[str, Any]]:
        """Run *command*, yielding events as they happen.

        Event shapes
        ------------
        ``{"type": "token", "text": str}``            incremental reply text
        ``{"type": "tool", "name": str, "status": str}``  tool lifecycle
        ``{"type": "pending_confirmation", "pending_id": str}``
        ``{"type": "result", "response": OrchestratorResponse}``  always last
        """
        metrics = CommandMetrics()
        try:
            with user_scope(session.user_id):
                async for event in self._run_stream(command, session, metrics):
                    yield event
        finally:
            metrics_registry.record(metrics)

    # ------------------------------------------------------------------
    # Core loop
    # ------------------------------------------------------------------

    async def _run_stream(
        self, command: str, session: SessionContext, metrics: CommandMetrics
    ) -> AsyncIterator[dict[str, Any]]:
        text = command.strip()
        if not text:
            metrics.path = "error"
            yield self._result(
                OrchestratorResponse(
                    success=False,
                    message="Could you clarify your request?",
                    clarification_question="Could you clarify your request?",
                )
            )
            return

        # --- Zero-token fast path -------------------------------------
        canned = ChatClient.local_fast_path(text)
        if canned is not None:
            metrics.path = "fast_path"
            yield {"type": "token", "text": canned}
            with metrics.phase("persist"):
                await self._chat_client.persist_turn(text, canned, user_id=session.user_id)
            yield self._result(OrchestratorResponse(success=True, message=canned))
            return

        # --- Plan (one LLM call, tools attached) ----------------------
        with metrics.phase("context"):
            enriched = await self._enrich_command(text)
            messages, session_id = await self._chat_client.build_messages(
                enriched, session.user_id
            )

        tools = self._tool_registry.list_all()
        function_name_map = build_function_name_map(tools)

        # The planning call is streamed: when the model answers directly the
        # user sees text immediately instead of waiting for the full turn.
        planned_content = ""
        raw_calls: list[dict[str, str]] = []
        try:
            with metrics.phase("plan"):
                async for frame in self._llm.stream_with_tools(messages, tools=tools):
                    if frame["type"] == "text":
                        yield {"type": "token", "text": frame["delta"]}
                    else:
                        planned_content = frame["content"]
                        raw_calls = frame["tool_calls"]
                        metrics.record_usage(frame.get("usage"))
        except LLMError as exc:
            metrics.path = "error"
            self._record_audit(
                tool_name="orchestrator.planner",
                domain=DomainName.system_control,
                inputs={"command": text},
                output=None,
                error=str(exc),
                approval_status="not_required",
                session_id=session.session_id,
            )
            message = "I'm having trouble connecting to the AI service. Please try again."
            yield self._result(
                OrchestratorResponse(
                    success=False, message=message, clarification_question=message
                )
            )
            return

        tool_calls = decode_tool_calls(raw_calls, function_name_map)

        # --- No tools: the model already answered ---------------------
        if not tool_calls:
            metrics.path = "chat"
            reply = planned_content.strip()
            if not reply:
                reply = "Could you clarify your request?"
                yield {"type": "token", "text": reply}
            with metrics.phase("persist"):
                await self._chat_client.persist_turn(
                    text, reply, user_id=session.user_id, session_id=session_id
                )
            yield self._result(OrchestratorResponse(success=True, message=reply))
            return

        metrics.path = "tools"

        # --- Execute the plan -----------------------------------------
        results: list[Any] = []
        completed_steps: list[tuple[int, str, Any, Any]] = []
        tool_messages: list[dict[str, Any]] = []

        for step_index, (call_id, tool_name, inputs) in enumerate(tool_calls):
            yield {"type": "tool", "name": tool_name, "status": "started"}
            metrics.tools.append(tool_name)

            approval_holder: dict[str, str] = {"status": "not_required"}
            try:
                tool = self._resolve_tool(tool_name, session)

                if tool.requires_confirmation:
                    # Waiting on a human is not latency worth measuring.
                    async for event in self._await_confirmation(
                        tool_name, inputs, approval_holder
                    ):
                        yield event

                with metrics.phase("tools"):
                    tool_result = await self._execute_step(tool, inputs, approval_holder)
            except StepFailure as exc:
                metrics.path = "error"
                yield {"type": "tool", "name": tool_name, "status": "failed"}
                self._record_audit(
                    tool_name=tool_name,
                    domain=self._domain_for(tool_name),
                    inputs=inputs,
                    output=exc.output,
                    error=exc.detail,
                    approval_status=exc.approval_status,
                    session_id=session.session_id,
                )
                rollback_warnings = self._rollback_completed_steps(
                    completed_steps, session.session_id
                )
                message = exc.user_message(step_index)
                with metrics.phase("persist"):
                    await self._chat_client.persist_turn(
                        text, message, user_id=session.user_id, session_id=session_id
                    )
                yield self._result(
                    OrchestratorResponse(
                        success=False,
                        message=message,
                        results=results,
                        failed_step=step_index,
                        rollback_warnings=rollback_warnings,
                    )
                )
                return

            self._record_audit(
                tool_name=tool_name,
                domain=tool.domain,
                inputs=inputs,
                output=tool_result,
                error=None,
                approval_status=approval_holder["status"],
                session_id=session.session_id,
            )

            results.append(tool_result)
            completed_steps.append((step_index, tool_name, inputs, tool_result))
            self._rate_limiter.record(tool_name)
            tool_messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call_id,
                    "content": json.dumps(
                        sanitize_tool_result(tool_result), separators=(",", ":"), default=str
                    ),
                }
            )
            yield {"type": "tool", "name": tool_name, "status": "completed"}

        # --- Summarise results (second LLM call, cached prefix) -------
        follow_up = (
            messages + [assistant_replay_message(planned_content, raw_calls)] + tool_messages
        )
        summary_parts: list[str] = []
        try:
            with metrics.phase("summary"):
                async for piece in self._llm.stream_text(
                    follow_up, tools=tools, timeout=30.0, on_usage=metrics.record_usage
                ):
                    summary_parts.append(piece)
                    yield {"type": "token", "text": piece}
        except LLMError:
            logger.warning("Result summarisation failed; falling back to a plain confirmation.")

        summary = "".join(summary_parts).strip() or "Task completed successfully."
        if not summary_parts:
            yield {"type": "token", "text": summary}

        with metrics.phase("persist"):
            await self._chat_client.persist_turn(
                text, summary, user_id=session.user_id, session_id=session_id
            )
        yield self._result(
            OrchestratorResponse(success=True, message=summary, results=results)
        )

    # ------------------------------------------------------------------
    # Pipeline phases
    # ------------------------------------------------------------------

    def _resolve_tool(self, tool_name: str, session: SessionContext) -> ToolDefinition:
        """Look up *tool_name* and check permissions and rate limits."""
        try:
            tool = self._tool_registry.get(tool_name)
        except Exception as exc:
            raise StepFailure("Error", str(exc)) from exc

        try:
            self._permission_system.check(tool.permission_level, session.grants)
        except AuthorizationError as exc:
            raise StepFailure("Permission denied", str(exc)) from exc
        except Exception as exc:
            raise StepFailure("Error", str(exc)) from exc

        try:
            self._rate_limiter.check(tool_name)
        except RateLimitError as exc:
            raise StepFailure("Rate limit exceeded", str(exc)) from exc

        return tool

    async def _await_confirmation(
        self, tool_name: str, inputs: Any, approval_holder: dict[str, str]
    ) -> AsyncIterator[dict[str, Any]]:
        """Request approval, emitting the pending id so a client can respond."""
        invocation = ToolInvocation(tool_name=tool_name, inputs=inputs)
        request = asyncio.ensure_future(self._confirmation_layer.request(invocation))

        # Give the confirmation layer a moment to register the pending entry
        # so the id can be surfaced to the client before we block on it.
        pending_id = await self._poll_for_pending_id(tool_name)
        if pending_id is not None:
            yield {"type": "pending_confirmation", "pending_id": pending_id, "tool": tool_name}

        outcome = await request
        approval_holder["status"] = outcome
        if outcome != "approved":
            raise StepFailure(
                "Action not approved",
                f"confirmation was {outcome}",
                approval_status=outcome,
            )

    async def _poll_for_pending_id(
        self, tool_name: str, attempts: int = 20, interval: float = 0.01
    ) -> str | None:
        for _ in range(attempts):
            for pending in self._confirmation_layer.list_pending():
                if pending.get("tool_name") == tool_name:
                    return str(pending["id"])
            await asyncio.sleep(interval)
        return None

    async def _execute_step(
        self, tool: ToolDefinition, inputs: Any, approval_holder: dict[str, str]
    ) -> Any:
        """Validate, run, and validate the output of a single tool."""
        approval_status = approval_holder["status"]

        try:
            validate_input(tool, inputs)
        except InputValidationError as exc:
            raise StepFailure(
                "Input validation failed", str(exc), approval_status=approval_status
            ) from exc

        try:
            # Tools are synchronous and hit the database, so they run off the
            # event loop; the contextvar carrying the user id travels with them.
            result = await asyncio.to_thread(tool.execute, inputs)
        except Exception as exc:
            raise StepFailure(
                "Tool execution failed", str(exc), approval_status=approval_status
            ) from exc

        try:
            validate_output(tool, result)
        except OutputValidationError as exc:
            raise StepFailure(
                "Output validation failed",
                str(exc),
                approval_status=approval_status,
                output=result,
            ) from exc

        return result

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _result(response: OrchestratorResponse) -> dict[str, Any]:
        return {"type": "result", "response": response}

    def _domain_for(self, tool_name: str) -> DomainName:
        """Best-effort domain for audit entries when the tool may not exist."""
        try:
            return self._tool_registry.get(tool_name).domain
        except Exception:
            prefix = tool_name.split(".", 1)[0]
            try:
                return DomainName(prefix)
            except ValueError:
                return DomainName.system_control

    def _rollback_completed_steps(
        self,
        completed_steps: list[tuple[int, str, Any, Any]],
        session_id: str,
    ) -> list[str]:
        """Attempt to roll back all completed steps in reverse order.

        Returns warning messages for any rollback that failed with an
        unexpected exception (i.e. not ``NotImplementedError``).
        """
        warnings: list[str] = []

        for step_index, tool_name, inputs, result in reversed(completed_steps):
            # inputs is not guaranteed to be a mapping, so build the audit
            # payload without unpacking it.
            audit_inputs: dict[str, Any] = {"rollback_for_step": step_index, "inputs": inputs}
            try:
                tool = self._tool_registry.get(tool_name)
                tool.rollback({"inputs": inputs, "output": result})
                self._record_audit(
                    tool_name=tool_name,
                    domain=tool.domain,
                    inputs=audit_inputs,
                    output={"rollback": "success"},
                    error=None,
                    approval_status="not_required",
                    session_id=session_id,
                )
            except NotImplementedError:
                # Tool doesn't support rollback — skip silently
                pass
            except Exception as exc:
                warnings.append(
                    f"Rollback failed for step {step_index} (tool '{tool_name}'): {exc}. "
                    "Manual intervention may be required."
                )
                self._record_audit(
                    tool_name=tool_name,
                    domain=self._domain_for(tool_name),
                    inputs=audit_inputs,
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

        context_text = "\n".join(f"- {record.text}" for record in context_records)
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
            logger.exception("Failed to record audit entry for tool '%s'", tool_name)
