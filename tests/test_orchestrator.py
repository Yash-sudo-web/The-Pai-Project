"""Orchestrator: routing, LLM round-trip counts, safety gating, rollback."""

from __future__ import annotations

import asyncio

import pytest

from conftest import FakeLLM, make_tool, tool_call
from src.safety.rate_limiter import RateLimiter
from src.types import PermissionLevel


def _register(orchestrator, tool):
    orchestrator._tool_registry.register(tool)
    return tool


async def _events(orchestrator, command, session):
    return [event async for event in orchestrator.run_stream(command, session)]


# ---------------------------------------------------------------------------
# Round-trip budget — the whole point of the tool-calling rewrite
# ---------------------------------------------------------------------------


class TestCallBudget:
    async def test_greeting_costs_no_llm_call(self, orchestrator, session):
        response = await orchestrator.run("hi", session)
        assert response.success
        assert orchestrator._llm.calls == [], "fast path must not reach the LLM"

    @pytest.mark.parametrize("greeting", ["hi", "Hello", "hey!", "thanks", "Thank you."])
    async def test_fast_path_variants(self, orchestrator, session, greeting):
        await orchestrator.run(greeting, session)
        assert orchestrator._llm.calls == []

    async def test_plain_chat_costs_one_call(self, orchestrator, session):
        orchestrator._llm = FakeLLM([("Python is a language.", [])])
        orchestrator._chat_client._llm = orchestrator._llm

        response = await orchestrator.run("what is python", session)

        assert response.message == "Python is a language."
        assert len(orchestrator._llm.calls) == 1

    async def test_tool_command_costs_two_calls(self, orchestrator, session):
        _register(orchestrator, make_tool("t.echo"))
        orchestrator._llm = FakeLLM([(None, [tool_call("t__echo", {})])], summary="All set.")
        orchestrator._chat_client._llm = orchestrator._llm

        response = await orchestrator.run("do the thing", session)

        assert response.success and response.message == "All set."
        assert len(orchestrator._llm.calls) == 2
        assert [c["stream_text"] for c in orchestrator._llm.calls] == [False, True]


# ---------------------------------------------------------------------------
# Prompt construction
# ---------------------------------------------------------------------------


class TestPromptShape:
    async def test_static_prefix_first_and_clock_last(self, orchestrator, session):
        orchestrator._llm = FakeLLM([("hello", [])])
        orchestrator._chat_client._llm = orchestrator._llm
        await orchestrator.run("tell me something", session)

        messages = orchestrator._llm.calls[0]["messages"]
        assert messages[0]["role"] == "system"
        assert "Current date" not in messages[0]["content"], (
            "a value that changes every second must not sit in the cacheable prefix"
        )
        assert messages[-1]["role"] == "user"
        assert "Current date and time" in messages[-2]["content"]

    async def test_tool_schemas_are_not_inlined_in_the_prompt(self, orchestrator, session):
        orchestrator._llm = FakeLLM([("hello", [])])
        orchestrator._chat_client._llm = orchestrator._llm
        await orchestrator.run("tell me something", session)

        call = orchestrator._llm.calls[0]
        assert "input_schema" not in call["messages"][0]["content"]
        assert call["n_tools"] > 0, "tools must travel in the tools parameter"

    async def test_results_replay_as_tool_messages(self, orchestrator, session):
        _register(orchestrator, make_tool("t.echo", result={"id": "secret", "value": 42}))
        orchestrator._llm = FakeLLM([(None, [tool_call("t__echo", {})])])
        orchestrator._chat_client._llm = orchestrator._llm

        await orchestrator.run("do the thing", session)

        follow_up = orchestrator._llm.calls[1]["messages"]
        assert follow_up[-1]["role"] == "tool"
        assert follow_up[-2]["role"] == "assistant" and follow_up[-2]["tool_calls"]
        assert "42" in follow_up[-1]["content"]
        assert "secret" not in follow_up[-1]["content"], "internal ids must be stripped"

    async def test_follow_up_reuses_the_same_prefix(self, orchestrator, session):
        """Identical prefixes are what makes the second call a cache hit."""
        _register(orchestrator, make_tool("t.echo"))
        orchestrator._llm = FakeLLM([(None, [tool_call("t__echo", {})])])
        orchestrator._chat_client._llm = orchestrator._llm

        await orchestrator.run("do the thing", session)

        planning, follow_up = orchestrator._llm.calls
        assert follow_up["messages"][: len(planning["messages"])] == planning["messages"]
        assert follow_up["n_tools"] == planning["n_tools"]


# ---------------------------------------------------------------------------
# Safety gating
# ---------------------------------------------------------------------------


class TestSafetyGating:
    async def test_permission_denied_blocks_execution(self, orchestrator, session):
        tool = _register(
            orchestrator, make_tool("t.admin", permission=PermissionLevel.admin)
        )
        orchestrator._llm = FakeLLM([(None, [tool_call("t__admin", {})])])
        orchestrator._chat_client._llm = orchestrator._llm

        response = await orchestrator.run("do admin things", session)

        assert not response.success
        assert "Permission denied" in response.message
        assert response.failed_step == 0
        assert tool.executed_with == [], "tool must not run when denied"

    async def test_rate_limit_blocks_execution(self, orchestrator, session):
        from src.config import RateLimitConfig, ToolConfig, ToolsConfig

        _register(orchestrator, make_tool("t.limited"))
        orchestrator._rate_limiter = RateLimiter(
            ToolsConfig(
                tools=[
                    ToolConfig(
                        name="t.limited",
                        domain="system_control",
                        permission_level="write",
                        requires_confirmation=False,
                        rate_limit=RateLimitConfig(max_calls=0, window_seconds=60),
                    )
                ]
            )
        )
        orchestrator._llm = FakeLLM([(None, [tool_call("t__limited", {})])])
        orchestrator._chat_client._llm = orchestrator._llm

        response = await orchestrator.run("go", session)
        assert not response.success and "Rate limit" in response.message

    async def test_output_validation_failure_is_reported(self, orchestrator, session):
        _register(
            orchestrator,
            make_tool(
                "t.badout",
                result={"wrong": True},
                output_schema={
                    "type": "object",
                    "properties": {"required_field": {"type": "string"}},
                    "required": ["required_field"],
                },
            ),
        )
        orchestrator._llm = FakeLLM([(None, [tool_call("t__badout", {})])])
        orchestrator._chat_client._llm = orchestrator._llm

        response = await orchestrator.run("go", session)
        assert not response.success and "Output validation failed" in response.message

    async def test_execution_error_is_reported(self, orchestrator, session):
        _register(orchestrator, make_tool("t.boom", raises=RuntimeError("disk on fire")))
        orchestrator._llm = FakeLLM([(None, [tool_call("t__boom", {})])])
        orchestrator._chat_client._llm = orchestrator._llm

        response = await orchestrator.run("go", session)
        assert not response.success
        assert "Tool execution failed" in response.message and "disk on fire" in response.message

    async def test_unknown_tool_is_reported(self, orchestrator, session):
        orchestrator._llm = FakeLLM([(None, [tool_call("t__nope", {})])])
        orchestrator._chat_client._llm = orchestrator._llm

        response = await orchestrator.run("go", session)
        assert not response.success and "No tool named" in response.message


# ---------------------------------------------------------------------------
# Confirmation
# ---------------------------------------------------------------------------


class TestConfirmationFlow:
    async def test_pending_id_is_emitted_then_approval_runs_the_tool(
        self, orchestrator, session
    ):
        tool = _register(orchestrator, make_tool("t.sensitive", requires_confirmation=True))
        orchestrator._llm = FakeLLM([(None, [tool_call("t__sensitive", {})])], summary="Ran it.")
        orchestrator._chat_client._llm = orchestrator._llm

        events: list[dict] = []

        async def _drive():
            async for event in orchestrator.run_stream("do the sensitive thing", session):
                events.append(event)

        task = asyncio.ensure_future(_drive())
        for _ in range(200):
            pending = [e for e in events if e["type"] == "pending_confirmation"]
            if pending:
                orchestrator._confirmation_layer.approve(pending[0]["pending_id"])
                break
            await asyncio.sleep(0.01)
        await task

        assert tool.executed_with == [{}]
        assert events[-1]["response"].success

    async def test_rejection_stops_the_tool(self, orchestrator, session):
        tool = _register(orchestrator, make_tool("t.sensitive", requires_confirmation=True))
        orchestrator._llm = FakeLLM([(None, [tool_call("t__sensitive", {})])])
        orchestrator._chat_client._llm = orchestrator._llm

        events: list[dict] = []

        async def _drive():
            async for event in orchestrator.run_stream("do it", session):
                events.append(event)

        task = asyncio.ensure_future(_drive())
        for _ in range(200):
            pending = [e for e in events if e["type"] == "pending_confirmation"]
            if pending:
                orchestrator._confirmation_layer.reject(pending[0]["pending_id"])
                break
            await asyncio.sleep(0.01)
        await task

        assert tool.executed_with == []
        assert not events[-1]["response"].success
        assert "not approved" in events[-1]["response"].message


# ---------------------------------------------------------------------------
# Rollback
# ---------------------------------------------------------------------------


class TestRollback:
    async def test_completed_steps_roll_back_in_reverse_order(self, orchestrator, session):
        order: list[str] = []

        first = _register(orchestrator, make_tool("t.first"))
        second = _register(orchestrator, make_tool("t.second"))
        _register(orchestrator, make_tool("t.third", raises=RuntimeError("nope")))

        first.rollback = lambda ctx: order.append("first")  # type: ignore[assignment]
        second.rollback = lambda ctx: order.append("second")  # type: ignore[assignment]

        orchestrator._llm = FakeLLM(
            [(None, [tool_call("t__first", {}), tool_call("t__second", {}), tool_call("t__third", {})])]
        )
        orchestrator._chat_client._llm = orchestrator._llm

        response = await orchestrator.run("three steps", session)

        assert not response.success and response.failed_step == 2
        assert order == ["second", "first"], "rollback must unwind in reverse"

    async def test_tools_without_rollback_are_skipped_silently(self, orchestrator, session):
        from src.tools.registry import ToolDefinition

        plain = _register(orchestrator, make_tool("t.plain"))
        plain.rollback = ToolDefinition.rollback.__get__(plain)  # restore the default
        _register(orchestrator, make_tool("t.fails", raises=RuntimeError("nope")))

        orchestrator._llm = FakeLLM(
            [(None, [tool_call("t__plain", {}), tool_call("t__fails", {})])]
        )
        orchestrator._chat_client._llm = orchestrator._llm

        response = await orchestrator.run("two steps", session)
        assert response.rollback_warnings == []

    async def test_rollback_failure_surfaces_a_warning(self, orchestrator, session):
        broken = _register(orchestrator, make_tool("t.broken"))
        _register(orchestrator, make_tool("t.fails", raises=RuntimeError("nope")))

        def _explode(ctx):
            raise RuntimeError("rollback also failed")

        broken.rollback = _explode  # type: ignore[assignment]

        orchestrator._llm = FakeLLM(
            [(None, [tool_call("t__broken", {}), tool_call("t__fails", {})])]
        )
        orchestrator._chat_client._llm = orchestrator._llm

        response = await orchestrator.run("two steps", session)
        assert len(response.rollback_warnings) == 1
        assert "Manual intervention" in response.rollback_warnings[0]

    async def test_non_mapping_inputs_do_not_crash_rollback(self, orchestrator, session):
        """Audit used to build its payload with ``{**inputs}``, which blew up."""
        good = _register(orchestrator, make_tool("t.good"))
        _register(orchestrator, make_tool("t.fails", raises=RuntimeError("nope")))
        orchestrator._llm = FakeLLM(
            [(None, [tool_call("t__good", {"a": 1}), tool_call("t__fails", {})])]
        )
        orchestrator._chat_client._llm = orchestrator._llm

        response = await orchestrator.run("two steps", session)
        assert not response.success
        assert good.rolled_back, "rollback should still have been attempted"


# ---------------------------------------------------------------------------
# Multi-step plans and identity
# ---------------------------------------------------------------------------


class TestPlanExecution:
    async def test_multiple_tool_calls_run_in_order(self, orchestrator, session):
        first = _register(orchestrator, make_tool("t.one", result={"n": 1}))
        second = _register(orchestrator, make_tool("t.two", result={"n": 2}))

        orchestrator._llm = FakeLLM(
            [(None, [tool_call("t__one", {"x": 1}), tool_call("t__two", {"y": 2})])]
        )
        orchestrator._chat_client._llm = orchestrator._llm

        response = await orchestrator.run("two things", session)

        assert response.success
        assert response.results == [{"n": 1}, {"n": 2}]
        assert first.executed_with == [{"x": 1}] and second.executed_with == [{"y": 2}]

    async def test_user_identity_reaches_the_tool(self, orchestrator, session):
        from src.context import current_user_id

        seen: list[str] = []

        tool = _register(orchestrator, make_tool("t.whoami"))
        tool.execute = lambda inputs: (seen.append(current_user_id()), {"ok": True})[1]

        orchestrator._llm = FakeLLM([(None, [tool_call("t__whoami", {})])])
        orchestrator._chat_client._llm = orchestrator._llm

        await orchestrator.run("who am i", session)
        assert seen == ["test_user"], "the contextvar must survive asyncio.to_thread"

    async def test_events_are_emitted_in_a_usable_order(self, orchestrator, session):
        _register(orchestrator, make_tool("t.echo"))
        orchestrator._llm = FakeLLM([(None, [tool_call("t__echo", {})])])
        orchestrator._chat_client._llm = orchestrator._llm

        events = await _events(orchestrator, "go", session)
        kinds = [e["type"] for e in events]

        assert kinds[0] == "tool" and kinds[-1] == "result"
        assert "token" in kinds
        assert [e["status"] for e in events if e["type"] == "tool"] == ["started", "completed"]

    async def test_empty_command_asks_for_clarification(self, orchestrator, session):
        response = await orchestrator.run("   ", session)
        assert not response.success and response.clarification_question
        assert orchestrator._llm.calls == []

    async def test_llm_failure_degrades_gracefully(self, orchestrator, session):
        from src.orchestrator.llm import LLMError

        class Failing(FakeLLM):
            async def stream_with_tools(self, messages, tools=None, **kwargs):
                raise LLMError("upstream 503")
                yield  # pragma: no cover - makes this an async generator

        orchestrator._llm = Failing()
        orchestrator._chat_client._llm = orchestrator._llm

        response = await orchestrator.run("do something", session)
        assert not response.success and "trouble connecting" in response.message
