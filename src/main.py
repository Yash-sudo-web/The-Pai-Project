"""Application wiring and entrypoint for the Personal AI Assistant."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

from src.config import AppConfig, app_config
from src.domains.gym.tools import register_gym_tools
from src.domains.nutrition.tools import register_nutrition_tools
from src.domains.productivity.tools import register_productivity_tools
from src.domains.system_control.tools import register_system_control_tools
from src.memory.db import SessionLocal, init_db
from src.memory.retrieval import RetrievalLayer
from src.memory.vector_store import VectorStore, VectorStoreUnavailableError
from src.orchestrator.intent_parser import IntentParser
from src.orchestrator.chat import ChatClient
from src.orchestrator.llm import LLMClient
from src.orchestrator.orchestrator import Orchestrator
from src.orchestrator.router import MessageRouter
from src.safety.audit import AuditLog
from src.safety.confirmation import ConfirmationLayer
from src.safety.permissions import PermissionSystem
from src.safety.rate_limiter import RateLimiter
from src.tools.registry import ToolRegistry
from src.voice.transcription import TranscriptionEngine
from src.voice.voice_input import VoiceInputModule


@dataclass
class ApplicationRuntime:
    config: AppConfig
    audit_log: AuditLog
    confirmation_layer: ConfirmationLayer
    permission_system: PermissionSystem
    rate_limiter: RateLimiter
    tool_registry: ToolRegistry
    retrieval_layer: RetrievalLayer
    intent_parser: IntentParser
    orchestrator: Orchestrator
    auth_manager: AuthManager
    app: Any
    voice_module: VoiceInputModule | None


def _register_domain_tools(registry: ToolRegistry, disabled_domains: set[str]) -> None:
    domain_registrars = {
        "gym": register_gym_tools,
        "nutrition": register_nutrition_tools,
        "productivity": register_productivity_tools,
        "system_control": register_system_control_tools,
    }
    for domain_name, registrar in domain_registrars.items():
        if domain_name in disabled_domains:
            continue
        registrar(registry)


def create_runtime(config: AppConfig = app_config, enable_voice: bool = False) -> ApplicationRuntime:
    """Build and wire the application runtime."""
    init_db()
    tools_config = config.tools
    permissions_config = config.permissions
    remote_config = config.remote

    audit_log = AuditLog(SessionLocal)
    confirmation_layer = ConfirmationLayer(audit_log=audit_log)
    permission_system = PermissionSystem()
    permission_system._config = permissions_config
    rate_limiter = RateLimiter(tools_config, audit_log=audit_log)
    tool_registry = ToolRegistry()
    _register_domain_tools(tool_registry, set(tools_config.disabled_domains))

    vector_store = None
    if os.environ.get("VECTOR_DB_ENABLED", "").lower() == "true":
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
    chat_client = ChatClient()
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
    from src.remote.api import create_app
    from src.remote.auth import AuthManager

    auth_manager = AuthManager(audit_log=audit_log, auth_config=remote_config)
    transcription_engine = TranscriptionEngine()
    app = create_app(
        orchestrator,
        confirmation_layer,
        auth_manager,
        permissions_config,
        transcription_engine=transcription_engine,
    )

    voice_module = None
    if enable_voice:
        voice_module = VoiceInputModule(
            transcription_engine=transcription_engine,
            on_transcription=lambda _text: None,
            audit_log=audit_log,
        )

    return ApplicationRuntime(
        config=config,
        audit_log=audit_log,
        confirmation_layer=confirmation_layer,
        permission_system=permission_system,
        rate_limiter=rate_limiter,
        tool_registry=tool_registry,
        retrieval_layer=retrieval_layer,
        intent_parser=intent_parser,
        orchestrator=orchestrator,
        auth_manager=auth_manager,
        app=app,
        voice_module=voice_module,
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
