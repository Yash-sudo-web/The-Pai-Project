"""FastAPI REST, SSE, and WebSocket interface for the assistant."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import secrets
import time
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any, AsyncIterator

from fastapi import Depends, FastAPI, Header, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.websockets import WebSocketState
from pydantic import BaseModel

from src.config import PermissionsConfig
from src.observability import metrics_registry
from src.orchestrator.orchestrator import Orchestrator, OrchestratorResponse, SessionContext
from src.remote.auth import AuthManager, AuthenticatedClient
from src.safety.confirmation import ConfirmationLayer
from src.remote.push import PushSender
from src.types import PermissionLevel
from src.memory import devices, idempotency
from src.memory.chat_sessions import ChatSessionManager

logger = logging.getLogger(__name__)

# A command awaiting confirmation is parked in memory. Cap both how many can
# be parked and how long, so a client that never polls /status cannot leak
# tasks indefinitely.
MAX_PENDING_RUNS = 64
PENDING_RUN_TTL_SECONDS = 300.0

_MAX_COMMAND_LENGTH = 4000  # Prevent sending excessive prompts to LLM

# How often the device re-asks for its notification plan. Local notifications
# carry whatever text they were scheduled with, so freshness at fire time is
# bounded by this interval — hence the tightening as a slot approaches.
NUDGE_REFRESH_SECONDS = 15 * 60
NUDGE_PRE_SLOT_SECONDS = 120


class CommandRequest(BaseModel):
    command: str


class CommandTaskState(BaseModel):
    pending_id: str
    status: str
    result: dict[str, Any] | None = None


class LoginRequest(BaseModel):
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_at: str
    user_id: str


class AuthStatusResponse(BaseModel):
    password_login: bool
    session_days: int


class DeviceRegistration(BaseModel):
    token: str
    platform: str  # ios | android


class CommandRun:
    """Tracks one in-flight command so /status and /confirm can find it."""

    def __init__(self) -> None:
        self.pending_id: str | None = None
        self.response: OrchestratorResponse | None = None
        self.pending_event = asyncio.Event()
        self.done_event = asyncio.Event()
        self.task: asyncio.Task | None = None
        self.created_at = time.monotonic()

    @property
    def expired(self) -> bool:
        return time.monotonic() - self.created_at > PENDING_RUN_TTL_SECONDS


def _resolve_grants(permissions: PermissionsConfig, session_name: str) -> list[PermissionLevel]:
    session_config = permissions.get(session_name) or permissions.get("remote_session")
    if session_config is None:
        session_config = permissions.get("default_session")
    if session_config is None:
        return [PermissionLevel.read]
    return [PermissionLevel(grant) for grant in session_config.grants]


def _refresh_delay_seconds(planned: list, now: datetime | None = None) -> int:
    """Seconds until the device should ask for its plan again.

    Normally the flat interval, but shortened so a refresh always lands just
    before a slot fires — that last refresh is what makes the notification
    carry current numbers rather than the ones from a quarter of an hour ago.
    """
    now = now or datetime.now(UTC)
    delay = float(NUDGE_REFRESH_SECONDS)

    for item in planned:
        # A slot already inside the lead window fires before another refresh
        # could reach it, so it is skipped and the flat interval stands.
        lead = (item.at - now).total_seconds() - NUDGE_PRE_SLOT_SECONDS
        if 0 < lead < delay:
            delay = lead

    # Never busy-loop, however close the slot is.
    return max(60, int(delay))


def _sse(event: str, data: Any) -> str:
    return f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"


def create_app(
    orchestrator: Orchestrator,
    confirmation_layer: ConfirmationLayer,
    auth_manager: AuthManager,
    permissions_config: PermissionsConfig,
    session_manager: ChatSessionManager | None = None,
    profile: str = "local",
    push_sender: PushSender | None = None,
) -> FastAPI:
    """Create the FastAPI app with REST, SSE, and WebSocket endpoints."""

    # Reads its config from the environment and stays disabled when Firebase
    # is not set up, so this is safe to build unconditionally.
    push = push_sender if push_sender is not None else PushSender()

    @asynccontextmanager
    async def _lifespan(app: FastAPI):
        app.state.runs = {}
        if profile == "serverless":
            logger.warning(
                "Running the serverless profile: confirmation-gated tools rely on "
                "in-process state, so /confirm may reach a different instance than "
                "the one holding the paused command. Prefer tools that do not "
                "require confirmation on this deployment."
            )
        try:
            yield
        finally:
            for run in app.state.runs.values():
                if run.task is not None:
                    run.task.cancel()
            app.state.runs.clear()

    app = FastAPI(title="Personal AI Assistant", lifespan=_lifespan)
    app.state.runs = {}

    # ------------------------------------------------------------------
    # Meta
    # ------------------------------------------------------------------

    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    async def index() -> HTMLResponse:
        """A landing page confirming the deployment is up.

        The old 1.4k-line HTML dashboard was superseded by the Flutter client
        and had been calling voice endpoints that no longer exist.
        """
        return HTMLResponse(
            "<!doctype html><meta charset=utf-8>"
            "<title>Personal AI Assistant</title>"
            "<style>body{font:15px/1.6 system-ui,sans-serif;background:#08080f;"
            "color:#f1f5f9;display:grid;place-items:center;height:100vh;margin:0}"
            "a{color:#a78bfa}code{background:#191926;padding:2px 6px;border-radius:4px}"
            "div{text-align:center}</style>"
            "<div><h1>Personal AI Assistant</h1>"
            "<p>API is running. Use the app, or see "
            "<a href='/docs'>/docs</a> and <a href='/health'>/health</a>.</p>"
            "<p><code>POST /auth/login</code> &rarr; <code>POST /command/stream</code></p></div>"
        )

    @app.get("/health")
    async def health_check() -> dict[str, Any]:
        """System health check — reports DB connectivity and configuration status."""
        status: dict[str, Any] = {"status": "ok", "profile": profile, "checks": {}}

        try:
            from sqlalchemy import text as sql_text

            from src.memory.db import SessionLocal as HealthSessionLocal

            def _ping() -> None:
                with HealthSessionLocal() as db:
                    db.execute(sql_text("SELECT 1"))

            await asyncio.to_thread(_ping)
            status["checks"]["database"] = "connected"
        except Exception as exc:
            status["status"] = "degraded"
            status["checks"]["database"] = f"error: {exc}"

        llm_key = os.environ.get("OPENROUTER_API_KEY") or os.environ.get("OPENAI_API_KEY")
        status["checks"]["llm_api_key"] = "configured" if llm_key else "missing"
        status["checks"]["transcription"] = "disabled (client-side)"
        status["checks"]["chat_sessions"] = "available" if session_manager else "disabled"
        status["checks"]["streaming"] = "available"

        return status

    @app.get("/metrics")
    async def metrics(
        client: AuthenticatedClient = Depends(auth_manager.authenticate),
    ) -> dict[str, Any]:
        """Rolling latency, token, and tool-usage aggregates for this process."""
        _ = client
        return metrics_registry.snapshot()

    # ------------------------------------------------------------------
    # Authentication
    # ------------------------------------------------------------------

    @app.get("/auth/status", response_model=AuthStatusResponse)
    async def auth_status() -> AuthStatusResponse:
        """Unauthenticated: lets a client decide whether to show a login form."""
        return AuthStatusResponse(
            password_login=auth_manager.password_login_available,
            session_days=auth_manager.session_days,
        )

    @app.post("/auth/login", response_model=TokenResponse)
    async def login(payload: LoginRequest) -> TokenResponse:
        """Exchange the account password for a long-lived bearer token.

        Hashing is deliberately slow, so it runs off the event loop.
        """
        issued = await asyncio.to_thread(auth_manager.login, payload.password)
        return TokenResponse(
            access_token=issued.access_token,
            expires_at=issued.expires_at.isoformat(),
            user_id=issued.user_id,
        )

    @app.get("/auth/me")
    async def whoami(
        client: AuthenticatedClient = Depends(auth_manager.authenticate),
    ) -> dict[str, Any]:
        """Validate the stored credential on app start."""
        return {
            "user_id": client.user_id,
            "session_name": client.session_name,
            "auth_type": client.auth_type,
        }

    @app.post("/auth/refresh", response_model=TokenResponse)
    async def refresh(
        client: AuthenticatedClient = Depends(auth_manager.authenticate),
    ) -> TokenResponse:
        """Extend a still-valid session so regular use never requires re-login."""
        if client.auth_type != "jwt":
            raise HTTPException(
                status_code=400, detail="Only password sessions can be refreshed"
            )
        issued = auth_manager.issue_token(client.user_id)
        return TokenResponse(
            access_token=issued.access_token,
            expires_at=issued.expires_at.isoformat(),
            user_id=issued.user_id,
        )

    # ------------------------------------------------------------------
    # Command dispatch
    # ------------------------------------------------------------------

    def _session_for(client: AuthenticatedClient) -> SessionContext:
        return SessionContext(
            session_id=client.session_id,
            grants=_resolve_grants(permissions_config, client.session_name),
            user_id=client.user_id,
        )

    def _validate_command(command: str) -> str:
        stripped = command.strip()
        if not stripped:
            raise HTTPException(status_code=400, detail="Command cannot be empty")
        if len(command) > _MAX_COMMAND_LENGTH:
            raise HTTPException(
                status_code=400,
                detail=f"Command too long ({len(command)} chars). Maximum is {_MAX_COMMAND_LENGTH}.",
            )
        return stripped

    def _evict_stale_runs() -> None:
        runs: dict[str, CommandRun] = app.state.runs
        for key in [k for k, run in runs.items() if run.expired]:
            run = runs.pop(key)
            if run.task is not None and not run.task.done():
                run.task.cancel()
        while len(runs) >= MAX_PENDING_RUNS:
            _, oldest = min(runs.items(), key=lambda item: item[1].created_at)
            runs.pop(next(k for k, v in runs.items() if v is oldest))
            if oldest.task is not None and not oldest.task.done():
                oldest.task.cancel()

    async def _drain(run: CommandRun, command: str, session: SessionContext) -> None:
        """Consume the orchestrator's event stream into *run*."""
        try:
            async for event in orchestrator.run_stream(command, session):
                if event["type"] == "pending_confirmation":
                    run.pending_id = event["pending_id"]
                    run.pending_event.set()
                elif event["type"] == "result":
                    run.response = event["response"]
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception("Command failed")
            run.response = OrchestratorResponse(success=False, message=f"Command failed: {exc}")
        finally:
            run.done_event.set()

    async def _dispatch_command(command: str, client: AuthenticatedClient) -> dict[str, Any]:
        """Run a command on the event loop, returning early if it needs approval."""
        run = CommandRun()
        run.task = asyncio.create_task(_drain(run, command, _session_for(client)))

        waiters = {
            asyncio.create_task(run.pending_event.wait()),
            asyncio.create_task(run.done_event.wait()),
        }
        try:
            await asyncio.wait(waiters, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for waiter in waiters:
                waiter.cancel()

        if run.done_event.is_set():
            response = run.response or OrchestratorResponse(
                success=False, message="No response produced."
            )
            return {"status": "completed", "result": response.model_dump()}

        assert run.pending_id is not None
        _evict_stale_runs()
        app.state.runs[run.pending_id] = run
        return {"status": "pending_confirmation", "pending_id": run.pending_id}

    @app.post("/command")
    async def post_command(
        payload: CommandRequest,
        client: AuthenticatedClient = Depends(auth_manager.authenticate),
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ) -> dict[str, Any]:
        command = _validate_command(payload.command)

        if not idempotency_key:
            return await _dispatch_command(command, client)

        reservation = await asyncio.to_thread(
            idempotency.reserve, client.user_id, idempotency_key
        )
        if reservation.replay is not None:
            return reservation.replay
        if reservation.in_progress:
            raise HTTPException(
                status_code=409,
                detail="A command with this Idempotency-Key is still running.",
            )

        try:
            result = await _dispatch_command(command, client)
        except Exception:
            # Leave the key free so the client can retry a failed submission.
            await asyncio.to_thread(idempotency.release, client.user_id, idempotency_key)
            raise

        await asyncio.to_thread(
            idempotency.complete, client.user_id, idempotency_key, result
        )
        return result

    @app.post("/command/stream")
    async def post_command_stream(
        payload: CommandRequest,
        client: AuthenticatedClient = Depends(auth_manager.authenticate),
    ) -> StreamingResponse:
        """Stream the reply as Server-Sent Events.

        Emits `token` frames as text arrives, `tool` frames as tools run, and
        a final `result` frame. Cuts perceived latency to first paint from
        seconds to well under one.
        """
        command = _validate_command(payload.command)
        session = _session_for(client)

        async def _events() -> AsyncIterator[str]:
            try:
                async for event in orchestrator.run_stream(command, session):
                    kind = event["type"]
                    if kind == "token":
                        yield _sse("token", {"text": event["text"]})
                    elif kind == "tool":
                        yield _sse("tool", {"name": event["name"], "status": event["status"]})
                    elif kind == "pending_confirmation":
                        # No registry entry needed: the stream stays open and
                        # delivers the result itself, and /confirm resolves the
                        # id against the confirmation layer directly.
                        yield _sse("pending_confirmation", {"pending_id": event["pending_id"]})
                    elif kind == "result":
                        yield _sse("result", event["response"].model_dump())
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.exception("Streaming command failed")
                yield _sse("error", {"message": str(exc)})
            yield _sse("done", {})

        return StreamingResponse(
            _events(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache, no-transform",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    @app.get("/status/{pending_id}")
    async def get_status(
        pending_id: str,
        client: AuthenticatedClient = Depends(auth_manager.authenticate),
    ) -> dict[str, Any]:
        _ = client
        run: CommandRun | None = app.state.runs.get(pending_id)
        if run is None:
            if confirmation_layer.get_pending(pending_id) is not None:
                return {"pending_id": pending_id, "status": "awaiting_confirmation"}
            raise HTTPException(status_code=404, detail="Unknown pending id")

        if not run.done_event.is_set():
            return {"pending_id": pending_id, "status": "awaiting_confirmation"}

        app.state.runs.pop(pending_id, None)
        response = run.response or OrchestratorResponse(
            success=False, message="No response produced."
        )
        return {"pending_id": pending_id, "status": "completed", "result": response.model_dump()}

    @app.post("/confirm/{pending_id}")
    async def post_confirm(
        pending_id: str,
        client: AuthenticatedClient = Depends(auth_manager.authenticate),
    ) -> dict[str, Any]:
        _ = client
        if confirmation_layer.get_pending(pending_id) is None and pending_id not in app.state.runs:
            raise HTTPException(status_code=404, detail="Unknown pending id")
        confirmation_layer.approve(pending_id)
        return {"pending_id": pending_id, "status": "approved"}

    @app.post("/reject/{pending_id}")
    async def post_reject(
        pending_id: str,
        client: AuthenticatedClient = Depends(auth_manager.authenticate),
    ) -> dict[str, Any]:
        _ = client
        if confirmation_layer.get_pending(pending_id) is None and pending_id not in app.state.runs:
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
    # Proactive nudges
    # ------------------------------------------------------------------

    @app.get("/nudges")
    async def get_nudges(
        client: AuthenticatedClient = Depends(auth_manager.authenticate),
    ) -> dict[str, Any]:
        """Anything currently worth surfacing — poll this on app launch."""
        from src.domains.review.nudges import due_for_user

        nudges = await asyncio.to_thread(due_for_user, client.user_id)
        return {"nudges": [n.as_dict() for n in nudges], "count": len(nudges)}

    @app.get("/nudges/schedule")
    async def nudge_schedule(
        client: AuthenticatedClient = Depends(auth_manager.authenticate),
    ) -> dict[str, Any]:
        """The notifications the device should schedule locally.

        Delivery on this deployment is on-device rather than pushed: iOS holds
        the notification and fires it even if the app has since died. The app
        re-requests this plan on the returned cadence and re-schedules, which
        is what keeps the text current.
        """
        from src.domains.review.nudges import plan_for_user

        planned = await asyncio.to_thread(plan_for_user, client.user_id)
        return {
            "slots": [p.as_dict() for p in planned],
            "count": len(planned),
            "refresh_after_seconds": _refresh_delay_seconds(planned),
        }

    async def _authenticate_scheduler(
        authorization: str | None = Header(default=None),
    ) -> AuthenticatedClient:
        """Auth for the cron trigger, which is not a person.

        Vercel Cron sends ``Authorization: Bearer $CRON_SECRET``, a value it
        will not let us reuse as the app's API key, so that secret is accepted
        here in addition to the normal credentials. When ``CRON_SECRET`` is
        unset this degrades to ordinary authentication.
        """
        cron_secret = os.environ.get("CRON_SECRET")
        if cron_secret and authorization:
            _, _, credential = authorization.partition(" ")
            if credential and secrets.compare_digest(credential, cron_secret):
                return AuthenticatedClient(
                    session_id="scheduler",
                    session_name="remote_session",
                    auth_type="api_key",
                    user_id=auth_manager.default_user_id,
                )
        return await auth_manager.authenticate(authorization=authorization)

    # Vercel Cron only ever issues GET, but a manual trigger (or any other
    # scheduler) naturally reaches for POST, so both are mounted.
    @app.api_route("/cron/nudges", methods=["GET", "POST"])
    async def cron_nudges(
        client: AuthenticatedClient = Depends(_authenticate_scheduler),
    ) -> dict[str, Any]:
        """Scheduled evaluation: work out what is due and push it.

        Everything that decides *whether* to interrupt lives in
        :func:`~src.domains.review.nudges.select_for_push`; this endpoint only
        supplies it with the delivery history and then performs the sends.
        """
        from src.domains.review.nudges import dedup_cutoffs, due_for_user, select_for_push

        user_id = client.user_id
        now = datetime.now(UTC)

        nudges = await asyncio.to_thread(due_for_user, user_id, now)
        push_candidates = [n for n in nudges if n.channel == "push"]

        already_sent: set[str] = set()
        if push_candidates:
            cutoffs = dedup_cutoffs(push_candidates, now)
            widest = min(cutoffs.values())
            last_sent = await asyncio.to_thread(
                devices.last_sent_map, user_id, list(cutoffs), widest
            )
            already_sent = {
                kind for kind, cutoff in cutoffs.items()
                if (ts := last_sent.get(kind)) is not None and ts >= cutoff
            }

        sent_in_last_day = await asyncio.to_thread(
            devices.pushes_since, user_id, now - timedelta(hours=24)
        )
        selection = select_for_push(nudges, now, already_sent, sent_in_last_day)

        tokens = await asyncio.to_thread(devices.tokens_for, user_id)
        delivered: list[str] = []
        pruned = 0

        for nudge in selection.to_send:
            result = await push.send_to_tokens(
                tokens, nudge.title, nudge.message, {"kind": nudge.kind}
            )
            if result.dead_tokens:
                pruned += await asyncio.to_thread(devices.unregister_many, result.dead_tokens)
                tokens = [t for t in tokens if t not in set(result.dead_tokens)]
            if result.sent:
                # Only a delivered nudge opens its dedup window; a Firebase
                # outage must not silence tomorrow's attempt.
                await asyncio.to_thread(devices.record_sent, user_id, nudge.kind, now)
                delivered.append(nudge.kind)
            else:
                logger.warning(
                    "nudge not delivered user=%s kind=%s reason=%s",
                    user_id, nudge.kind, result.skipped_reason,
                )

        receipts_purged = await asyncio.to_thread(idempotency.purge_expired)
        deliveries_purged = await asyncio.to_thread(devices.purge_deliveries)

        return {
            "nudges": [n.as_dict() for n in nudges],
            "count": len(nudges),
            "push_enabled": push.enabled,
            "push_disabled_reason": push.disabled_reason,
            "devices": len(tokens),
            "delivered": delivered,
            "suppressed": selection.suppressed,
            "tokens_pruned": pruned,
            "receipts_purged": receipts_purged,
            "deliveries_purged": deliveries_purged,
        }

    # ------------------------------------------------------------------
    # Device registration for push
    # ------------------------------------------------------------------

    @app.post("/devices/register")
    async def register_device(
        registration: DeviceRegistration,
        client: AuthenticatedClient = Depends(auth_manager.authenticate),
    ) -> dict[str, Any]:
        """Record an FCM token. The client calls this on every launch.

        FCM rotates tokens on its own schedule, so re-registering is normal
        and idempotent rather than an error.
        """
        token = registration.token.strip()
        if not token:
            raise HTTPException(status_code=400, detail="token must not be empty")
        try:
            created = await asyncio.to_thread(
                devices.register, client.user_id, token, registration.platform
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None

        return {"registered": True, "created": created, "push_enabled": push.enabled}

    @app.delete("/devices/{token}")
    async def unregister_device(
        token: str,
        client: AuthenticatedClient = Depends(auth_manager.authenticate),
    ) -> dict[str, Any]:
        """Stop pushing to one device — logout, or notifications turned off."""
        removed = await asyncio.to_thread(devices.unregister, token)
        return {"removed": removed}

    @app.post("/devices/test")
    async def test_push(
        client: AuthenticatedClient = Depends(auth_manager.authenticate),
    ) -> dict[str, Any]:
        """Send a notification right now, bypassing the rules.

        The push path has several independent things that can be wrong — token
        registration, service-account credentials, APNs setup — and waiting for
        the daily cron to find out which is a poor debugging loop.
        """
        tokens = await asyncio.to_thread(devices.tokens_for, client.user_id)
        result = await push.send_to_tokens(
            tokens, "PAI", "Test notification — push is working.", {"kind": "test"}
        )
        if result.dead_tokens:
            await asyncio.to_thread(devices.unregister_many, result.dead_tokens)
        return {"push_enabled": push.enabled, "devices": len(tokens), **result.as_dict()}

    # ------------------------------------------------------------------
    # Chat history & sessions endpoints
    # ------------------------------------------------------------------

    def _require_sessions() -> ChatSessionManager:
        if session_manager is None:
            raise HTTPException(status_code=503, detail="Chat sessions not configured")
        return session_manager

    @app.get("/chat/history")
    async def get_chat_history(
        limit: int = 50,
        client: AuthenticatedClient = Depends(auth_manager.authenticate),
    ) -> dict:
        """Return messages for today's active session."""
        manager = _require_sessions()
        session = await manager.get_or_create_session(client.user_id)
        messages = await asyncio.to_thread(manager.get_messages, session.id, limit)
        return {
            "session_id": session.id,
            "session_date": str(session.session_date),
            "messages": messages,
        }

    @app.get("/chat/sessions")
    async def get_chat_sessions(
        limit: int = 30,
        client: AuthenticatedClient = Depends(auth_manager.authenticate),
    ) -> dict:
        """Return a list of past sessions with summaries."""
        manager = _require_sessions()
        sessions = await asyncio.to_thread(manager.get_sessions_list, client.user_id, limit)
        return {"sessions": sessions}

    @app.get("/chat/session/{session_id}/messages")
    async def get_session_messages(
        session_id: str,
        limit: int = 100,
        client: AuthenticatedClient = Depends(auth_manager.authenticate),
    ) -> dict:
        """Return messages for a specific session owned by the caller."""
        manager = _require_sessions()

        # Ownership check: without it, any authenticated caller could read any
        # session by guessing its id.
        owned = await asyncio.to_thread(manager.get_session, session_id, client.user_id)
        if owned is None:
            raise HTTPException(status_code=404, detail="Unknown session")

        messages = await asyncio.to_thread(manager.get_messages, session_id, limit)
        return {"session_id": session_id, "messages": messages}

    return app
