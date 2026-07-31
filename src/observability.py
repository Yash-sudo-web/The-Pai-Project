"""Per-command timing and token accounting.

The orchestrator's cost is split across a planning call, tool execution and a
summarisation call. Without a breakdown there is no way to tell whether a slow
command was the model, the database, or a single misbehaving tool — so every
command emits one structured line, and the aggregates are exposed on
``/metrics``.

Everything here is in-process: on a single-instance deployment that is the
whole picture, and on serverless each instance reports its own slice.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import defaultdict, deque
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Iterator

logger = logging.getLogger("pai.metrics")

# How many recent commands to keep for percentile calculations.
_WINDOW = 200


@dataclass
class TokenUsage:
    prompt: int = 0
    completion: int = 0
    cached: int = 0

    def add(self, other: "TokenUsage") -> None:
        self.prompt += other.prompt
        self.completion += other.completion
        self.cached += other.cached

    @property
    def total(self) -> int:
        return self.prompt + self.completion

    @property
    def cache_hit_rate(self) -> float:
        return (self.cached / self.prompt) if self.prompt else 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "prompt": self.prompt,
            "completion": self.completion,
            "cached": self.cached,
            "cache_hit_rate": round(self.cache_hit_rate, 3),
        }


@dataclass
class CommandMetrics:
    """Timings and token usage for one command."""

    path: str = "unknown"  # fast_path | chat | tools | error
    phases: dict[str, float] = field(default_factory=dict)
    tools: list[str] = field(default_factory=list)
    llm_calls: int = 0
    tokens: TokenUsage = field(default_factory=TokenUsage)
    started_at: float = field(default_factory=time.perf_counter)

    @contextmanager
    def phase(self, name: str) -> Iterator[None]:
        """Time a named phase, accumulating repeats (e.g. several tools)."""
        start = time.perf_counter()
        try:
            yield
        finally:
            elapsed = (time.perf_counter() - start) * 1000
            self.phases[name] = round(self.phases.get(name, 0.0) + elapsed, 1)

    def record_usage(self, usage: Any) -> None:
        """Absorb a provider ``usage`` object, tolerating missing fields."""
        self.llm_calls += 1
        if usage is None:
            return
        details = getattr(usage, "prompt_tokens_details", None)
        self.tokens.add(
            TokenUsage(
                prompt=int(getattr(usage, "prompt_tokens", 0) or 0),
                completion=int(getattr(usage, "completion_tokens", 0) or 0),
                cached=int(getattr(details, "cached_tokens", 0) or 0) if details else 0,
            )
        )

    @property
    def total_ms(self) -> float:
        return round((time.perf_counter() - self.started_at) * 1000, 1)

    def as_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "total_ms": self.total_ms,
            "phases_ms": dict(self.phases),
            "tools": self.tools,
            "llm_calls": self.llm_calls,
            "tokens": self.tokens.as_dict(),
        }


class MetricsRegistry:
    """Thread-safe rolling aggregates over recent commands."""

    def __init__(self, window: int = _WINDOW) -> None:
        self._lock = threading.Lock()
        self._window = window
        self._durations: deque[float] = deque(maxlen=window)
        self._by_path: dict[str, int] = defaultdict(int)
        self._phase_totals: dict[str, float] = defaultdict(float)
        self._phase_counts: dict[str, int] = defaultdict(int)
        self._tool_counts: dict[str, int] = defaultdict(int)
        self._tokens = TokenUsage()
        self._commands = 0
        self._errors = 0

    def record(self, metrics: CommandMetrics) -> None:
        payload = metrics.as_dict()
        with self._lock:
            self._commands += 1
            if metrics.path == "error":
                self._errors += 1
            self._durations.append(payload["total_ms"])
            self._by_path[metrics.path] += 1
            for name, value in metrics.phases.items():
                self._phase_totals[name] += value
                self._phase_counts[name] += 1
            for tool in metrics.tools:
                self._tool_counts[tool] += 1
            self._tokens.add(metrics.tokens)

        # One line per command, greppable and cheap to ship anywhere.
        logger.info(
            "command path=%s total_ms=%.1f phases=%s tools=%s llm_calls=%d tokens=%d/%d cached=%d",
            payload["path"],
            payload["total_ms"],
            payload["phases_ms"],
            ",".join(payload["tools"]) or "-",
            payload["llm_calls"],
            metrics.tokens.prompt,
            metrics.tokens.completion,
            metrics.tokens.cached,
        )

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            durations = sorted(self._durations)
            return {
                "commands": self._commands,
                "errors": self._errors,
                "window": len(durations),
                "latency_ms": {
                    "p50": _percentile(durations, 0.50),
                    "p95": _percentile(durations, 0.95),
                    "max": durations[-1] if durations else 0.0,
                },
                "by_path": dict(self._by_path),
                "avg_phase_ms": {
                    name: round(total / self._phase_counts[name], 1)
                    for name, total in self._phase_totals.items()
                },
                "tool_calls": dict(sorted(self._tool_counts.items(), key=lambda kv: -kv[1])),
                "tokens": self._tokens.as_dict(),
            }

    def reset(self) -> None:
        with self._lock:
            self.__init__(self._window)  # type: ignore[misc]


def _percentile(sorted_values: list[float], fraction: float) -> float:
    if not sorted_values:
        return 0.0
    index = min(int(len(sorted_values) * fraction), len(sorted_values) - 1)
    return sorted_values[index]


# Module-level registry: one process, one set of counters.
metrics_registry = MetricsRegistry()
