"""Shared fixtures.

``src.memory.db`` builds its engine at import time from ``DATABASE_URL``, so
the environment must be set before anything under ``src`` is imported. That is
why the assignments below run at module scope, above the ``src`` imports.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import types
import uuid
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

# --- Environment must be set before importing src.* ------------------------
_TEST_DB = Path(tempfile.gettempdir()) / f"pai_tests_{uuid.uuid4().hex}.db"
os.environ["DATABASE_URL"] = f"sqlite:///{_TEST_DB.as_posix()}"

TEST_API_KEY = "test-api-key-long-enough-for-the-guard"
TEST_JWT_SECRET = "test-jwt-secret-long-enough-for-guard"
TEST_PASSWORD = "correct horse battery staple"

os.environ["PAI_API_KEY"] = TEST_API_KEY
os.environ["PAI_JWT_SECRET"] = TEST_JWT_SECRET
os.environ["PAI_PASSWORD"] = TEST_PASSWORD
os.environ["PAI_SESSION_DAYS"] = "30"
os.environ.pop("LANGSMITH_TRACING", None)

from src.memory.db import Base, SessionLocal, engine  # noqa: E402
from src.orchestrator.orchestrator import SessionContext  # noqa: E402
from src.types import PermissionLevel  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def _schema():
    Base.metadata.create_all(bind=engine)
    yield
    engine.dispose()
    _TEST_DB.unlink(missing_ok=True)


@pytest.fixture(autouse=True)
def _clean_tables(_schema):
    """Truncate every table between tests so they cannot leak into each other."""
    yield
    with SessionLocal() as db:
        for table in reversed(Base.metadata.sorted_tables):
            db.execute(table.delete())
        db.commit()


# ---------------------------------------------------------------------------
# Fake LLM
# ---------------------------------------------------------------------------


def tool_call(function_name: str, arguments: dict) -> dict[str, str]:
    """Build one accumulated tool-call frame as the streaming client emits it."""
    return {
        "id": f"call_{function_name}",
        "name": function_name,
        "arguments": json.dumps(arguments),
    }


class FakeLLM:
    """Scripted stand-in for :class:`~src.orchestrator.llm.LLMClient`.

    ``script`` is a list of ``(content, tool_calls)`` pairs, one per planning
    call. Every request is recorded on ``calls`` so tests can assert on prompt
    shape and on how many round trips a path costs.
    """

    def __init__(self, script=None, summary: str = "Done."):
        self.script = list(script or [])
        self.summary = summary
        self.calls: list[dict] = []

    @property
    def planning_calls(self) -> list[dict]:
        return [c for c in self.calls if not c["stream_text"]]

    async def stream_with_tools(self, messages, tools=None, **kwargs):
        self.calls.append(
            {"messages": messages, "n_tools": len(tools or []), "stream_text": False}
        )
        content, calls = self.script.pop(0) if self.script else ("", [])
        if content:
            yield {"type": "text", "delta": content}
        yield {"type": "final", "content": content or "", "tool_calls": calls}

    async def stream_text(self, messages, tools=None, **kwargs):
        self.calls.append(
            {"messages": messages, "n_tools": len(tools or []), "stream_text": True}
        )
        yield self.summary

    async def raw_chat(self, messages, **kwargs):
        return self.summary


@pytest.fixture
def runtime():
    """A wired application runtime with the LLM stubbed out."""
    from src.main import create_runtime

    rt = create_runtime()
    rt.orchestrator._llm = FakeLLM()
    rt.orchestrator._chat_client._llm = rt.orchestrator._llm
    return rt


@pytest.fixture
def orchestrator(runtime):
    return runtime.orchestrator


@pytest.fixture
def session():
    return SessionContext(
        session_id="test-session",
        grants=[PermissionLevel.read, PermissionLevel.write, PermissionLevel.execute],
        user_id="test_user",
    )


@pytest.fixture
def client(runtime):
    from fastapi.testclient import TestClient

    with TestClient(runtime.app) as test_client:
        yield test_client


@pytest.fixture
def auth_headers():
    return {"Authorization": f"Bearer {TEST_API_KEY}"}


def make_tool(
    name: str,
    *,
    domain=None,
    permission=PermissionLevel.write,
    requires_confirmation: bool = False,
    result=None,
    raises: Exception | None = None,
    output_schema: dict | None = None,
):
    """A minimal ToolDefinition for exercising the pipeline in isolation."""
    from src.tools.registry import ToolDefinition
    from src.types import DomainName

    class _Tool(ToolDefinition):
        def __init__(self):
            self.name = name
            self.description = f"test tool {name}"
            self.domain = domain or DomainName.system_control
            self.permission_level = permission
            self.requires_confirmation = requires_confirmation
            self.input_schema = {"type": "object", "properties": {}}
            self.output_schema = output_schema or {"type": "object"}
            self.executed_with: list = []
            self.rolled_back: list = []

        def execute(self, inputs):
            self.executed_with.append(inputs)
            if raises is not None:
                raise raises
            return result if result is not None else {"ok": True}

        def rollback(self, context):
            self.rolled_back.append(context)

    return _Tool()


__all__ = [
    "FakeLLM",
    "TEST_API_KEY",
    "TEST_JWT_SECRET",
    "TEST_PASSWORD",
    "make_tool",
    "tool_call",
    "types",
]
