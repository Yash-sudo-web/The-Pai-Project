"""Vercel serverless entrypoint for the Personal AI Assistant.

Vercel's Python runtime requires a bare top-level assignment of the form
`app = <callable>()` so its static AST parser can detect the ASGI handler.
All error handling is therefore kept inside `_build_app()` rather than
wrapping the assignment itself in a try/except.
"""
from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

from fastapi import FastAPI

# Ensure the project root is on sys.path so `src.*` imports resolve.
_root = Path(__file__).parent.parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

logger = logging.getLogger(__name__)


def _build_app() -> FastAPI:
    """Build the full runtime app, or return a degraded app on failure."""
    try:
        from src.config import AppConfig, app_config
        from src.memory.db import SessionLocal, init_db
        from src.memory.retrieval import RetrievalLayer
        from src.memory.chat_sessions import ChatSessionManager
        from src.orchestrator.intent_parser import IntentParser
        from src.orchestrator.chat import ChatClient, _default_summariser
        from src.orchestrator.llm import LLMClient
        from src.orchestrator.orchestrator import Orchestrator
        from src.orchestrator.router import MessageRouter
        from src.safety.audit import AuditLog
        from src.safety.confirmation import ConfirmationLayer
        from src.safety.permissions import PermissionSystem
        from src.safety.rate_limiter import RateLimiter
        from src.tools.registry import ToolRegistry
        from src.remote.auth import AuthManager
        from src.remote.api import create_app
        from src.domains.gym.tools import register_gym_tools
        from src.domains.nutrition.tools import register_nutrition_tools
        from src.domains.productivity.tools import register_productivity_tools

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

        # Register only cloud-safe domains (system_control needs pyautogui/pynput)
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
            transcription_engine=None,  # Whisper not available in serverless env
            session_manager=chat_session_manager,
        )

    except Exception:
        logger.exception("PAI runtime failed to initialise; starting in degraded mode")
        _app = FastAPI(title="Personal AI Assistant (degraded)")

        @_app.get("/health")
        async def health():
            return {"status": "degraded", "reason": "runtime failed to initialize"}

        return _app


# ── Bare top-level assignment ── Vercel's AST parser requires this exact form.
app = _build_app()
