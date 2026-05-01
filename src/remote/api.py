"""FastAPI REST and WebSocket interface for the assistant."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from concurrent.futures import Future, ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from typing import Any
import logging

from fastapi import Depends, FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from fastapi.websockets import WebSocketState
from pydantic import BaseModel

from src.config import PermissionsConfig
from src.orchestrator.orchestrator import Orchestrator, SessionContext
from src.remote.auth import AuthManager, AuthenticatedClient
from src.remote.dashboard import render_dashboard_html
from src.safety.confirmation import ConfirmationLayer
from src.types import PermissionLevel
from src.memory.chat_sessions import ChatSessionManager


class CommandRequest(BaseModel):
    command: str


class CommandTaskState(BaseModel):
    pending_id: str
    status: str
    result: dict[str, Any] | None = None


class MetaResponse(BaseModel):
    features: dict[str, bool]
    voice_input_mode: str


def _resolve_grants(permissions: PermissionsConfig, session_name: str) -> list[PermissionLevel]:
    session_config = permissions.get(session_name) or permissions.get("remote_session")
    if session_config is None:
        session_config = permissions.get("default_session")
    if session_config is None:
        return [PermissionLevel.read]
    return [PermissionLevel(grant) for grant in session_config.grants]


def create_app(
    orchestrator: Orchestrator,
    confirmation_layer: ConfirmationLayer,
    auth_manager: AuthManager,
    permissions_config: PermissionsConfig,
    session_manager: ChatSessionManager | None = None,
) -> FastAPI:
    """Create the FastAPI app with REST and WebSocket endpoints."""
    @asynccontextmanager
    async def _lifespan(app: FastAPI):
        app.state.command_tasks = {}
        app.state.command_results = {}
        app.state.executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="pai-remote")
        try:
            yield
        finally:
            app.state.executor.shutdown(wait=False, cancel_futures=True)

    app = FastAPI(title="Personal AI Assistant", lifespan=_lifespan)
    app.state.command_tasks = {}
    app.state.command_results = {}
    app.state.executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="pai-remote")

    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    async def dashboard() -> HTMLResponse:
        return HTMLResponse(render_dashboard_html())

    @app.get("/meta", response_model=MetaResponse, include_in_schema=False)
    async def meta() -> MetaResponse:
        return MetaResponse(
            features={"browser_voice_input": False, "backend_transcription": False},
            voice_input_mode="client_only",
        )

    @app.get("/health")
    async def health_check() -> dict[str, Any]:
        """System health check — reports DB connectivity and configuration status."""
        status: dict[str, Any] = {"status": "ok", "checks": {}}

        # DB connectivity
        try:
            from src.memory.db import SessionLocal as HealthSessionLocal
            with HealthSessionLocal() as db:
                db.execute(__import__("sqlalchemy").text("SELECT 1"))
            status["checks"]["database"] = "connected"
        except Exception as exc:
            status["status"] = "degraded"
            status["checks"]["database"] = f"error: {exc}"

        # LLM API key configured
        import os as _os
        llm_key = _os.environ.get("OPENROUTER_API_KEY") or _os.environ.get("OPENAI_API_KEY")
        status["checks"]["llm_api_key"] = "configured" if llm_key else "missing"

        # Voice (moved to client — always show disabled on server)
        status["checks"]["transcription"] = "disabled (client-side)"

        # Chat sessions
        status["checks"]["chat_sessions"] = "available" if session_manager else "disabled"

        return status


    async def _run_command(command: str, client: AuthenticatedClient):
        session = SessionContext(
            session_id=client.session_id,
            grants=_resolve_grants(permissions_config, client.session_name),
        )
        return await orchestrator.run(command, session)

    def _run_command_sync(command: str, client: AuthenticatedClient):
        return asyncio.run(_run_command(command, client))

    async def _await_pending_id(previous_ids: set[str], timeout_seconds: float = 10.0) -> str | None:
        deadline = asyncio.get_running_loop().time() + timeout_seconds
        while asyncio.get_running_loop().time() < deadline:
            current = {item["id"] for item in confirmation_layer.list_pending()}
            new_ids = current - previous_ids
            if new_ids:
                return next(iter(new_ids))
            await asyncio.sleep(0.05)
        return None

    async def _dispatch_command(command: str, client: AuthenticatedClient) -> dict[str, Any]:
        before_ids = {item["id"] for item in confirmation_layer.list_pending()}
        future = app.state.executor.submit(_run_command_sync, command, client)

        try:
            result = future.result(timeout=0.2)
            return {"status": "completed", "result": result.model_dump()}
        except FutureTimeoutError:
            pass

        pending_id = await _await_pending_id(before_ids)
        if pending_id is None:
            result = future.result()
            return {"status": "completed", "result": result.model_dump()}

        app.state.command_tasks[pending_id] = future
        return {"status": "pending_confirmation", "pending_id": pending_id}

    _MAX_COMMAND_LENGTH = 4000  # Prevent sending excessive prompts to LLM

    @app.post("/command")
    async def post_command(
        payload: CommandRequest,
        client: AuthenticatedClient = Depends(auth_manager.authenticate),
    ) -> dict[str, Any]:
        if not payload.command.strip():
            raise HTTPException(status_code=400, detail="Command cannot be empty")
        if len(payload.command) > _MAX_COMMAND_LENGTH:
            raise HTTPException(
                status_code=400,
                detail=f"Command too long ({len(payload.command)} chars). Maximum is {_MAX_COMMAND_LENGTH}.",
            )
        return await _dispatch_command(payload.command, client)

    @app.get("/status/{pending_id}")
    async def get_status(
        pending_id: str,
        client: AuthenticatedClient = Depends(auth_manager.authenticate),
    ) -> dict[str, Any]:
        _ = client
        task = app.state.command_tasks.get(pending_id)
        if task is None:
            if confirmation_layer.get_pending(pending_id) is not None:
                return {"pending_id": pending_id, "status": "awaiting_confirmation"}
            raise HTTPException(status_code=404, detail="Unknown pending id")

        if not task.done():
            try:
                result = task.result(timeout=0.2)
            except FutureTimeoutError:
                return {"pending_id": pending_id, "status": "awaiting_confirmation"}
            del app.state.command_tasks[pending_id]
            return {"pending_id": pending_id, "status": "completed", "result": result.model_dump()}

        result = task.result()
        del app.state.command_tasks[pending_id]
        return {"pending_id": pending_id, "status": "completed", "result": result.model_dump()}

    @app.post("/confirm/{pending_id}")
    async def post_confirm(
        pending_id: str,
        client: AuthenticatedClient = Depends(auth_manager.authenticate),
    ) -> dict[str, Any]:
        _ = client
        if confirmation_layer.get_pending(pending_id) is None and pending_id not in app.state.command_tasks:
            raise HTTPException(status_code=404, detail="Unknown pending id")
        confirmation_layer.approve(pending_id)
        return {"pending_id": pending_id, "status": "approved"}

    @app.post("/reject/{pending_id}")
    async def post_reject(
        pending_id: str,
        client: AuthenticatedClient = Depends(auth_manager.authenticate),
    ) -> dict[str, Any]:
        _ = client
        if confirmation_layer.get_pending(pending_id) is None and pending_id not in app.state.command_tasks:
            raise HTTPException(status_code=404, detail="Unknown pending id")
        confirmation_layer.reject(pending_id)
        return {"pending_id": pending_id, "status": "rejected"}

    @app.websocket("/stream")
    async def websocket_stream(websocket: WebSocket) -> None:
        authorization = websocket.headers.get("authorization")
        try:
            client = await auth_manager.authenticate(authorization=authorization)
        except HTTPException:
            await websocket.close(code=4401)
            return

        await websocket.accept()
        try:
            while websocket.client_state == WebSocketState.CONNECTED:
                payload = await websocket.receive_json()
                command = payload.get("command")
                if not command:
                    await websocket.send_json({"error": "Missing command"})
                    continue
                response = await _dispatch_command(command, client)
                await websocket.send_json(response)
        except WebSocketDisconnect:
            return

    # ------------------------------------------------------------------
    # Chat history & sessions endpoints
    # ------------------------------------------------------------------

    @app.get("/chat/history")
    async def get_chat_history(
        limit: int = 50,
    ) -> dict:
        """Return messages for today's active session."""
        if session_manager is None:
            raise HTTPException(status_code=503, detail="Chat sessions not configured")

        session = await session_manager.get_or_create_session("default_user")
        messages = session_manager.get_messages(session.id, limit=limit)
        return {
            "session_id": session.id,
            "session_date": str(session.session_date),
            "messages": messages,
        }

    @app.get("/chat/sessions")
    async def get_chat_sessions(
        limit: int = 30,
    ) -> dict:
        """Return a list of past sessions with summaries."""
        if session_manager is None:
            raise HTTPException(status_code=503, detail="Chat sessions not configured")

        sessions = session_manager.get_sessions_list("default_user", limit=limit)
        return {"sessions": sessions}

    return app
