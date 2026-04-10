"""Rate limiter for the Personal AI Assistant.

Implements a sliding-window counter per tool stored in-memory using
``collections.deque``. Limits are loaded from ``ToolsConfig`` at startup.
"""

from __future__ import annotations

import time
import uuid
from collections import deque
from datetime import datetime, timezone

from src.config import ToolsConfig
from src.types import AuditEntry, DomainName


class RateLimitError(Exception):
    """Raised when a tool invocation exceeds its configured rate limit."""

    def __init__(self, tool_name: str) -> None:
        self.tool_name = tool_name
        super().__init__(f"Rate limit exceeded for {tool_name}")


class RateLimiter:
    """Sliding-window rate limiter with per-tool independence.

    Args:
        tools_config: Loaded tool registry config containing rate limit settings.
        audit_log: Optional audit log instance; if provided, limit breaches are
                   recorded before raising ``RateLimitError``.
    """

    def __init__(
        self,
        tools_config: ToolsConfig,
        audit_log=None,  # AuditLog | None — avoid circular import
    ) -> None:
        # Build lookup: tool_name -> (max_calls, window_seconds)
        self._limits: dict[str, tuple[int, int]] = {}
        self._domains: dict[str, str] = {}
        for tool in tools_config.tools:
            self._limits[tool.name] = (
                tool.rate_limit.max_calls,
                tool.rate_limit.window_seconds,
            )
            self._domains[tool.name] = tool.domain

        # Sliding window storage: tool_name -> deque of monotonic timestamps
        self._windows: dict[str, deque[float]] = {}
        self._audit_log = audit_log

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def check(self, tool_name: str) -> bool:
        """Return ``True`` if the tool is within its rate limit.

        Raises:
            RateLimitError: If the invocation would exceed the configured limit.
                            The rejection is also logged to the audit log when
                            one is configured.
        """
        if tool_name not in self._limits:
            # Unknown tool — no limit configured, allow it
            return True

        max_calls, window_seconds = self._limits[tool_name]
        now = time.monotonic()
        window = self._get_window(tool_name)

        # Evict timestamps outside the sliding window
        cutoff = now - window_seconds
        while window and window[0] <= cutoff:
            window.popleft()

        if len(window) >= max_calls:
            self._log_rejection(tool_name)
            raise RateLimitError(tool_name)

        return True

    def record(self, tool_name: str) -> None:
        """Record a new invocation timestamp for *tool_name*.

        Call this AFTER a successful ``check()`` to register the invocation
        in the sliding window.
        """
        window = self._get_window(tool_name)
        window.append(time.monotonic())

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _get_window(self, tool_name: str) -> deque[float]:
        if tool_name not in self._windows:
            self._windows[tool_name] = deque()
        return self._windows[tool_name]

    def _log_rejection(self, tool_name: str) -> None:
        if self._audit_log is None:
            return

        domain_str = self._domains.get(tool_name, "unknown")
        try:
            domain = DomainName(domain_str)
        except ValueError:
            domain = DomainName.system_control  # fallback; shouldn't happen

        entry = AuditEntry(
            id=str(uuid.uuid4()),
            timestamp=datetime.now(tz=timezone.utc),
            tool_name=tool_name,
            domain=domain,
            inputs={"reason": "rate_limit_exceeded"},
            output=None,
            error=f"Rate limit exceeded for {tool_name}",
            approval_status="not_required",
            session_id="system",
        )
        self._audit_log.record(entry)
