"""The safety layer: permissions, rate limits, confirmation, audit, rollback."""

from __future__ import annotations

import asyncio

import pytest

from src.config import RateLimitConfig, ToolConfig, ToolsConfig
from src.safety.confirmation import ConfirmationLayer
from src.safety.permissions import AuthorizationError, PermissionSystem
from src.safety.rate_limiter import RateLimitError, RateLimiter
from src.types import PermissionLevel, ToolInvocation


# ---------------------------------------------------------------------------
# Permissions
# ---------------------------------------------------------------------------


class TestPermissions:
    @pytest.mark.parametrize(
        "required,grants,allowed",
        [
            (PermissionLevel.read, [PermissionLevel.read], True),
            (PermissionLevel.read, [PermissionLevel.admin], True),
            (PermissionLevel.write, [PermissionLevel.read], False),
            (PermissionLevel.execute, [PermissionLevel.write], False),
            (PermissionLevel.execute, [PermissionLevel.execute], True),
            (PermissionLevel.admin, [PermissionLevel.execute], False),
            (PermissionLevel.write, [], False),
        ],
    )
    def test_check_enforces_hierarchy(self, required, grants, allowed):
        system = PermissionSystem()
        if allowed:
            system.check(required, grants)
        else:
            with pytest.raises(AuthorizationError):
                system.check(required, grants)

    def test_level_ordering(self):
        assert PermissionLevel.read < PermissionLevel.write
        assert PermissionLevel.write < PermissionLevel.execute
        assert PermissionLevel.execute < PermissionLevel.admin
        assert PermissionLevel.admin >= PermissionLevel.read


# ---------------------------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------------------------


def _tools_config(name: str, max_calls: int, window: int) -> ToolsConfig:
    return ToolsConfig(
        tools=[
            ToolConfig(
                name=name,
                domain="system_control",
                permission_level="write",
                requires_confirmation=False,
                rate_limit=RateLimitConfig(max_calls=max_calls, window_seconds=window),
            )
        ]
    )


class TestRateLimiter:
    def test_allows_up_to_the_limit_then_blocks(self):
        limiter = RateLimiter(_tools_config("t.a", max_calls=3, window=60))
        for _ in range(3):
            limiter.check("t.a")
            limiter.record("t.a")
        with pytest.raises(RateLimitError):
            limiter.check("t.a")

    def test_window_slides(self, monkeypatch):
        limiter = RateLimiter(_tools_config("t.a", max_calls=2, window=10))
        now = [1000.0]
        monkeypatch.setattr("src.safety.rate_limiter.time.monotonic", lambda: now[0])

        for _ in range(2):
            limiter.check("t.a")
            limiter.record("t.a")
        with pytest.raises(RateLimitError):
            limiter.check("t.a")

        # Step past the window; the old calls should age out.
        now[0] += 11
        limiter.check("t.a")

    def test_limits_are_per_tool(self):
        config = ToolsConfig(
            tools=_tools_config("t.a", 1, 60).tools + _tools_config("t.b", 1, 60).tools
        )
        limiter = RateLimiter(config)
        limiter.check("t.a")
        limiter.record("t.a")
        limiter.check("t.b")  # unaffected by t.a exhausting its budget

    def test_unconfigured_tool_is_not_limited(self):
        limiter = RateLimiter(_tools_config("t.a", 1, 60))
        for _ in range(50):
            limiter.check("t.unknown")
            limiter.record("t.unknown")


# ---------------------------------------------------------------------------
# Confirmation
# ---------------------------------------------------------------------------


class TestConfirmation:
    async def test_approve_resolves_as_approved(self):
        layer = ConfirmationLayer(timeout_seconds=5)
        invocation = ToolInvocation(tool_name="system_control.launch_app", inputs={})

        task = asyncio.ensure_future(layer.request(invocation))
        pending_id = await _wait_for_pending(layer)
        layer.approve(pending_id)

        assert await task == "approved"
        assert layer.get_pending(pending_id) is None, "resolved entry must be cleared"

    async def test_reject_resolves_as_rejected(self):
        layer = ConfirmationLayer(timeout_seconds=5)
        task = asyncio.ensure_future(
            layer.request(ToolInvocation(tool_name="system_control.launch_app", inputs={}))
        )
        pending_id = await _wait_for_pending(layer)
        layer.reject(pending_id)

        assert await task == "rejected"

    async def test_timeout_resolves_as_timeout(self):
        layer = ConfirmationLayer(timeout_seconds=0.05)
        result = await layer.request(
            ToolInvocation(tool_name="system_control.launch_app", inputs={})
        )
        assert result == "timeout"
        assert layer.list_pending() == []

    async def test_unknown_id_is_ignored(self):
        layer = ConfirmationLayer(timeout_seconds=5)
        layer.approve("does-not-exist")  # must not raise
        layer.reject("does-not-exist")

    async def test_pending_snapshot_exposes_tool_name(self):
        layer = ConfirmationLayer(timeout_seconds=5)
        task = asyncio.ensure_future(
            layer.request(ToolInvocation(tool_name="gym.log_workout", inputs={"a": 1}))
        )
        pending_id = await _wait_for_pending(layer)
        snapshot = layer.get_pending(pending_id)
        assert snapshot["tool_name"] == "gym.log_workout"
        assert snapshot["inputs"] == {"a": 1}
        layer.reject(pending_id)
        await task


async def _wait_for_pending(layer: ConfirmationLayer, attempts: int = 100) -> str:
    for _ in range(attempts):
        pending = layer.list_pending()
        if pending:
            return str(pending[0]["id"])
        await asyncio.sleep(0.01)
    raise AssertionError("confirmation was never registered")


# ---------------------------------------------------------------------------
# Audit log
# ---------------------------------------------------------------------------


class TestAuditLog:
    def test_records_are_persisted_and_queryable(self):
        import uuid
        from datetime import datetime, timezone

        from src.memory.db import SessionLocal
        from src.safety.audit import AuditLog
        from src.types import AuditEntry, DomainName

        log = AuditLog(SessionLocal)
        entry = AuditEntry(
            id=str(uuid.uuid4()),
            timestamp=datetime.now(tz=timezone.utc),
            tool_name="gym.log_workout",
            domain=DomainName.gym,
            inputs={"exercise": "squat"},
            output={"ok": True},
            error=None,
            approval_status="not_required",
            session_id="s1",
        )
        log.record(entry)

        from src.memory.db import AuditLog as AuditRow

        with SessionLocal() as db:
            rows = db.query(AuditRow).filter(AuditRow.session_id == "s1").all()
        assert len(rows) == 1
        assert rows[0].tool_name == "gym.log_workout"
