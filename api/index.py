"""Vercel serverless entrypoint for the Personal AI Assistant.

Vercel's Python runtime detects a FastAPI `app` object in api/index.py.
We build the app here with a lazy, minimal runtime that skips local-only
features (voice input, pyautogui, openai-whisper) which require native
binaries that are unavailable in the Vercel build environment.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# Ensure the project root is on sys.path so `src.*` imports resolve.
_root = Path(__file__).parent.parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

# ---------------------------------------------------------------------------
# Minimal runtime bootstrap (no voice / system-control deps)
# ---------------------------------------------------------------------------

from src.config import AppConfig, app_config  # noqa: E402
from src.memory.db import SessionLocal, init_db  # noqa: E402
from src.memory.retrieval import RetrievalLayer  # noqa: E402
from src.memory.chat_sessions import ChatSessionManager  # noqa: E402
from src.orchestrator.intent_parser import IntentParser  # noqa: E402
from src.orchestrator.chat import ChatClient, _default_summariser  # noqa: E402
from src.orchestrator.llm import LLMClient  # noqa: E402
from src.orchestrator.orchestrator import Orchestrator  # noqa: E402
from src.orchestrator.router import MessageRouter  # noqa: E402
from src.safety.audit import AuditLog  # noqa: E402
from src.safety.confirmation import ConfirmationLayer  # noqa: E402
from src.safety.permissions import PermissionSystem  # noqa: E402
from src.safety.rate_limiter import RateLimiter  # noqa: E402
from src.tools.registry import ToolRegistry  # noqa: E402
from src.remote.auth import AuthManager  # noqa: E402
from src.remote.api import create_app  # noqa: E402

# Domain tools that don't require local-only binaries
from src.domains.gym.tools import register_gym_tools  # noqa: E402
from src.domains.nutrition.tools import register_nutrition_tools  # noqa: E402
from src.domains.productivity.tools import register_productivity_tools  # noqa: E402


def _build_app():
    init_db()

    cfg: AppConfig = app_config
    tools_cfg = cfg.tools
    permissions_cfg = cfg.permissions
    remote_cfg = cfg.remote

    audit_log = AuditLog(SessionLocal)
    confirmation_layer = ConfirmationLayer(audit_log=audit_log)
    permission_system = PermissionSystem()
    permission_system._config = permissions_cfg
    rate_limiter = RateLimiter(tools_cfg, audit_log=audit_log)

    tool_registry = ToolRegistry()
    disabled = set(tools_cfg.disabled_domains)

    # Register only cloud-safe domains — system_control uses pyautogui/pynput
    for domain_name, registrar in [
        ("gym", register_gym_tools),
        ("nutrition", register_nutrition_tools),
        ("productivity", register_productivity_tools),
    ]:
        if domain_name not in disabled:
            registrar(tool_registry)

    retrieval_layer = RetrievalLayer(
        session_factory=SessionLocal,
        vector_store=None,
        vector_db_enabled=False,
    )

    llm_client = LLMClient()
    chat_session_manager = ChatSessionManager(
        session_factory=SessionLocal,
        llm_summariser=_default_summariser,
    )
    chat_client = ChatClient(session_manager=chat_session_manager)
    router = MessageRouter(llm_client=llm_client)
    intent_parser = IntentParser(llm_client=llm_client, audit_log=audit_log)
    orchestrator = Orchestrator(
        intent_parser=intent_parser,
        tool_registry=tool_registry,
        permission_system=permission_system,
        rate_limiter=rate_limiter,
        confirmation_layer=confirmation_layer,
        audit_log=audit_log,
        retrieval_layer=retrieval_layer,
        router=router,
        chat_client=chat_client,
    )

    auth_manager = AuthManager(audit_log=audit_log, auth_config=remote_cfg)

    return create_app(
        orchestrator,
        confirmation_layer,
        auth_manager,
        permissions_cfg,
        transcription_engine=None,   # Whisper not available in serverless env
        session_manager=chat_session_manager,
    )


try:
    app = _build_app()
except Exception:
    # Fallback: bare FastAPI so Vercel's health check doesn't 502
    from fastapi import FastAPI
    app = FastAPI(title="Personal AI Assistant (degraded mode)")

    @app.get("/health")
    async def health():
        return {"status": "degraded", "reason": "runtime failed to initialize"}
