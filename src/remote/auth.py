"""Authentication helpers for the remote FastAPI interface."""

from __future__ import annotations

import os
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from fastapi import Header, HTTPException, status
from jose import JWTError, jwt

from src.config import RemoteAuthConfig
from src.types import AuditEntry, DomainName


@dataclass
class AuthenticatedClient:
    session_id: str
    session_name: str
    auth_type: str


class AuthManager:
    """Validates Bearer JWT tokens or pre-shared API keys."""

    def __init__(
        self,
        jwt_secret: str | None = None,
        api_key: str | None = None,
        algorithm: str = "HS256",
        audit_log: Any | None = None,
        auth_config: RemoteAuthConfig | None = None,
    ) -> None:
        config_secret = auth_config.jwt_secret if auth_config is not None else None
        config_api_key = auth_config.api_key if auth_config is not None else None
        config_algorithm = auth_config.jwt_algorithm if auth_config is not None else None

        self._jwt_secret = jwt_secret or config_secret or os.environ.get("PAI_JWT_SECRET")
        self._api_key = api_key or config_api_key or os.environ.get("PAI_API_KEY")
        self._algorithm = config_algorithm or algorithm
        self._audit_log = audit_log

    async def authenticate(self, authorization: str | None = Header(default=None)) -> AuthenticatedClient:
        """FastAPI dependency entrypoint."""
        if not authorization:
            self._log_failure("missing_authorization")
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing authorization header")

        scheme, _, credential = authorization.partition(" ")
        if scheme.lower() != "bearer" or not credential:
            self._log_failure("invalid_authorization_scheme")
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid authorization header")

        if self._api_key and credential == self._api_key:
            return AuthenticatedClient(
                session_id="remote-api-key",
                session_name="remote_session",
                auth_type="api_key",
            )

        if self._jwt_secret:
            try:
                payload = jwt.decode(credential, self._jwt_secret, algorithms=[self._algorithm])
            except JWTError:
                self._log_failure("invalid_jwt")
                raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token") from None

            return AuthenticatedClient(
                session_id=str(payload.get("sub", "remote-jwt")),
                session_name=str(payload.get("session", "remote_session")),
                auth_type="jwt",
            )

        self._log_failure("authentication_not_configured")
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication is not configured")

    def _log_failure(self, reason: str) -> None:
        if self._audit_log is None:
            return
        entry = AuditEntry(
            id=str(uuid.uuid4()),
            timestamp=datetime.now(tz=timezone.utc),
            tool_name="remote.auth",
            domain=DomainName.system_control,
            inputs={"reason": reason},
            output=None,
            error=f"Authentication failed: {reason}",
            approval_status="not_required",
            session_id="remote",
        )
        self._audit_log.record(entry)
