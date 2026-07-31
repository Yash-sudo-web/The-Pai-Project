"""Application wiring and entrypoint for the Personal AI Assistant."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any, Literal

from src.config import AppConfig, app_config
from src.domains.gym.tools import register_gym_tools
from src.domains.memory.tools import register_memory_tools
from src.domains.nutrition.tools import register_nutrition_tools
from src.domains.productivity.tools import register_productivity_tools
from src.domains.review.tools import register_review_tools
from src.memory.chat_sessions import ChatSessionManager
from src.memory.db import SessionLocal, init_db
from src.memory.retrieval import RetrievalLayer
from src.memory.vector_store import VectorStore, VectorStoreUnavailableError
from src.orchestrator.chat import ChatClient, _default_summariser
from src.orchestrator.llm import LLMClient
from src.orchestrator.orchestrator import Orchestrator
from src.remote.auth import AuthManager
from src.safety.audit import AuditLog
from src.safety.confirmation import ConfirmationLayer
from src.safety.permissions import PermissionSystem
from src.safety.rate_limiter import RateLimiter
from src.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)

Profile = Literal["local", "serverless"]


@dataclass
class ApplicationRuntime:
    config: AppConfig
    audit_log: AuditLog
    confirmation_layer: ConfirmationLayer
    permission_system: PermissionSystem
    rate_limiter: RateLimiter
    tool_registry: ToolRegistry
    retrieval_layer: RetrievalLayer
    chat_session_manager: ChatSessionManager
    llm_client: LLMClient
    orchestrator: Orchestrator
    auth_manager: AuthManager
    app: Any


def _register_domain_tools(
    registry: ToolRegistry,
    disabled_domains: set[str],
    profile: Profile,
    audit_log: AuditLog | None = None,
) -> None:
    domain_registrars: dict[str, Any] = {
        "gym": register_gym_tools,
        "nutrition": register_nutrition_tools,
        "productivity": register_productivity_tools,
        # undo_last reads the audit log and calls other tools' rollback().
        "memory": lambda reg: register_memory_tools(reg, audit_log=audit_log),
        "review": register_review_tools,
    }

    # system_control drives the local desktop via pyautogui/pynput, which
    # cannot work — and must not be exposed — from a serverless host.
    if profile == "local":
        from src.domains.system_control.tools import register_system_control_tools

        domain_registrars["system_control"] = register_system_control_tools

    for domain_name, registrar in domain_registrars.items():
        if domain_name in disabled_domains:
            continue
        registrar(registry)


def create_runtime(
    config: AppConfig = app_config, profile: Profile = "local"
) -> ApplicationRuntime:
    """Build and wire the application runtime.

    Parameters
    ----------
    config:
        Loaded application config.
    profile:
        ``"local"`` enables desktop control and the optional vector store;
        ``"serverless"`` registers only the cloud-safe domains.
    """
    try:
        init_db()
    except Exception:
        # Tables normally already exist against a remote database, where
        # create_all() is a no-op; a failure here should not stop boot.
        logger.warning("init_db() failed — assuming tables already exist", exc_info=True)

    tools_config = config.tools
    permissions_config = config.permissions
    remote_config = config.remote

    audit_log = AuditLog(SessionLocal)
    confirmation_layer = ConfirmationLayer(audit_log=audit_log)
    permission_system = PermissionSystem()
    permission_system._config = permissions_config
    rate_limiter = RateLimiter(tools_config, audit_log=audit_log)
    tool_registry = ToolRegistry()
    _register_domain_tools(
        tool_registry, set(tools_config.disabled_domains), profile, audit_log
    )

    vector_store = None
    if profile == "local" and os.environ.get("VECTOR_DB_ENABLED", "").lower() == "true":
        try:
            vector_store = VectorStore()
        except VectorStoreUnavailableError:
            vector_store = None

    retrieval_layer = RetrievalLayer(
        session_factory=SessionLocal,
        vector_store=vector_store,
        vector_db_enabled=vector_store is not None,
    )
    llm_client = LLMClient()
    chat_session_manager = ChatSessionManager(
        session_factory=SessionLocal,
        llm_summariser=_default_summariser,
    )
    chat_client = ChatClient(llm_client=llm_client, session_manager=chat_session_manager)
    orchestrator = Orchestrator(
        tool_registry=tool_registry,
        permission_system=permission_system,
        rate_limiter=rate_limiter,
        confirmation_layer=confirmation_layer,
        audit_log=audit_log,
        llm_client=llm_client,
        chat_client=chat_client,
        retrieval_layer=retrieval_layer,
    )

    from src.remote.api import create_app

    auth_manager = AuthManager(audit_log=audit_log, auth_config=remote_config)
    app = create_app(
        orchestrator,
        confirmation_layer,
        auth_manager,
        permissions_config,
        session_manager=chat_session_manager,
        profile=profile,
    )

    return ApplicationRuntime(
        config=config,
        audit_log=audit_log,
        confirmation_layer=confirmation_layer,
        permission_system=permission_system,
        rate_limiter=rate_limiter,
        tool_registry=tool_registry,
        retrieval_layer=retrieval_layer,
        chat_session_manager=chat_session_manager,
        llm_client=llm_client,
        orchestrator=orchestrator,
        auth_manager=auth_manager,
        app=app,
    )


try:
    app = create_runtime().app
except Exception:  # pragma: no cover - allows importing without optional runtime deps
    app = None


def main() -> None:
    """Run the FastAPI app via uvicorn."""
    import uvicorn

    uvicorn.run("src.main:app", host="0.0.0.0", port=8000, reload=False)


if __name__ == "__main__":  # pragma: no cover
    main()
