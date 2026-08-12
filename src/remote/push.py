"""Push delivery via the FCM HTTP v1 API.

Firebase is reached directly over REST rather than through ``firebase-admin``:
the sending half of that SDK is one authenticated POST, and skipping it keeps
a large dependency out of a serverless cold start.

Configuration is entirely environmental, and a deployment with none of it set
is expected rather than exceptional — :class:`PushSender` reports
``enabled=False`` and every send becomes a no-op, so local development and the
test suite never need Firebase credentials.

Required to actually send:

``PAI_FCM_PROJECT_ID``
    Firebase project id, e.g. ``pai-client-4f2a1``.
``PAI_FCM_CREDENTIALS``
    The service-account JSON. Either the JSON itself (what you paste into a
    Vercel environment variable) or a path to the downloaded key file.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

FCM_SCOPE = "https://www.googleapis.com/auth/firebase.messaging"
FCM_ENDPOINT = "https://fcm.googleapis.com/v1/projects/{project_id}/messages:send"

# FCM's verdict that a token belongs to an app that has been uninstalled or
# whose token has rotated. Anything else is transient and the token is kept.
_DEAD_TOKEN_STATUSES = frozenset({"UNREGISTERED", "INVALID_ARGUMENT", "NOT_FOUND"})

_REQUEST_TIMEOUT_SECONDS = 10.0


@dataclass
class PushResult:
    """Outcome of one fan-out to a user's devices."""

    sent: int = 0
    failed: int = 0
    skipped_reason: str | None = None
    dead_tokens: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "sent": self.sent,
            "failed": self.failed,
            "skipped_reason": self.skipped_reason,
            "pruned_tokens": len(self.dead_tokens),
        }


def _load_credentials_blob() -> dict[str, Any] | None:
    """Read the service-account JSON from the env var, inline or by path."""
    raw = (os.environ.get("PAI_FCM_CREDENTIALS") or "").strip()
    if not raw:
        return None

    if raw.startswith("{"):
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            logger.error("PAI_FCM_CREDENTIALS is set but is not valid JSON; push disabled")
            return None

    path = Path(raw)
    if not path.exists():
        logger.error("PAI_FCM_CREDENTIALS points at %s which does not exist; push disabled", path)
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        logger.error("Could not read service-account JSON at %s; push disabled", path)
        return None


class PushSender:
    """Sends notifications to FCM, one HTTP call per device token.

    FCM removed the multicast ``/batch`` endpoint, so a fan-out really is N
    requests; they are issued concurrently. A single-user deployment has one
    or two devices, so this stays cheap.
    """

    def __init__(self, project_id: str | None = None, credentials_blob: dict | None = None):
        self._project_id = project_id or os.environ.get("PAI_FCM_PROJECT_ID") or None
        self._blob = credentials_blob if credentials_blob is not None else _load_credentials_blob()
        self._credentials: Any = None
        self._disabled_reason: str | None = None

        if not self._project_id:
            self._disabled_reason = "PAI_FCM_PROJECT_ID is not set"
        elif not self._blob:
            self._disabled_reason = "PAI_FCM_CREDENTIALS is not set"

    @property
    def enabled(self) -> bool:
        return self._disabled_reason is None

    @property
    def disabled_reason(self) -> str | None:
        return self._disabled_reason

    # ------------------------------------------------------------------
    # Auth
    # ------------------------------------------------------------------

    def _access_token(self) -> str:
        """Mint (or refresh) an OAuth2 access token. Blocking — call in a thread.

        ``google.auth`` caches the token on the credentials object and only
        performs a network round trip once it is close to expiry, so calling
        this per send is cheap on a warm instance.
        """
        from google.auth.transport.requests import Request
        from google.oauth2 import service_account

        if self._credentials is None:
            self._credentials = service_account.Credentials.from_service_account_info(
                self._blob, scopes=[FCM_SCOPE]
            )

        if not self._credentials.valid:
            self._credentials.refresh(Request())
        return str(self._credentials.token)

    # ------------------------------------------------------------------
    # Sending
    # ------------------------------------------------------------------

    @staticmethod
    def _build_message(
        token: str, title: str, body: str, data: dict[str, str] | None
    ) -> dict[str, Any]:
        """One FCM v1 message envelope.

        ``apns.payload.aps.sound`` is what makes iOS actually alert rather
        than silently badge, and every value under ``data`` must be a string
        or FCM rejects the whole request.
        """
        return {
            "message": {
                "token": token,
                "notification": {"title": title, "body": body},
                "data": {k: str(v) for k, v in (data or {}).items()},
                "apns": {
                    "headers": {"apns-priority": "10"},
                    "payload": {"aps": {"sound": "default", "badge": 1}},
                },
                "android": {"priority": "high"},
            }
        }

    async def send_to_tokens(
        self,
        tokens: list[str],
        title: str,
        body: str,
        data: dict[str, str] | None = None,
    ) -> PushResult:
        """Deliver one notification to every token, concurrently.

        Tokens FCM rejects as dead are returned on the result rather than
        deleted here — storage is the caller's concern.
        """
        if not self.enabled:
            return PushResult(skipped_reason=self._disabled_reason)
        if not tokens:
            return PushResult(skipped_reason="no registered devices")

        try:
            import httpx
        except ImportError:  # pragma: no cover - httpx ships with the openai client
            return PushResult(skipped_reason="httpx is not installed")

        try:
            access_token = await asyncio.to_thread(self._access_token)
        except Exception as exc:
            logger.exception("Could not mint an FCM access token")
            return PushResult(skipped_reason=f"auth failed: {type(exc).__name__}: {exc}")

        url = FCM_ENDPOINT.format(project_id=self._project_id)
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json; UTF-8",
        }

        result = PushResult()
        async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT_SECONDS) as http:
            responses = await asyncio.gather(
                *(
                    http.post(url, headers=headers, json=self._build_message(t, title, body, data))
                    for t in tokens
                ),
                return_exceptions=True,
            )

        for token, response in zip(tokens, responses):
            if isinstance(response, BaseException):
                result.failed += 1
                logger.warning("FCM request failed for one token: %s", response)
                continue
            if response.status_code < 300:
                result.sent += 1
                continue

            result.failed += 1
            status = _error_status(response)
            logger.warning(
                "FCM rejected a token: http=%s status=%s", response.status_code, status
            )
            if status in _DEAD_TOKEN_STATUSES:
                result.dead_tokens.append(token)

        return result


def _error_status(response: Any) -> str:
    """Pull FCM's symbolic error status out of an error response body."""
    try:
        payload = response.json()
    except Exception:
        return "UNPARSEABLE"

    error = payload.get("error", {}) if isinstance(payload, dict) else {}
    for detail in error.get("details", []) or []:
        if isinstance(detail, dict) and detail.get("errorCode"):
            return str(detail["errorCode"])
    return str(error.get("status", "UNKNOWN"))
