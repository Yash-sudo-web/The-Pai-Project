"""Authentication helpers for the remote FastAPI interface."""

from __future__ import annotations

import logging
import os
import secrets
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import Header, HTTPException, status
from jose import ExpiredSignatureError, JWTError, jwt

from src.config import RemoteAuthConfig
from src.types import AuditEntry, DomainName

logger = logging.getLogger(__name__)

# Minimum length for a credential to be considered usable.
MIN_SECRET_LENGTH = 24

# Substrings that mark a value as a template placeholder rather than a real
# secret. Committed example configs use these, so accepting them would mean
# anyone with the repo holds a valid bearer token.
_PLACEHOLDER_MARKERS = (
    "choose_a_long_random_string",
    "changeme",
    "change_me",
    "your_api_key",
    "your_secret",
    "your_password",
    "set_your_password",
    "placeholder",
    "example",
    "xxxxx",
)


def _is_placeholder(value: str) -> bool:
    lowered = value.lower()
    return any(marker in lowered for marker in _PLACEHOLDER_MARKERS)


def _clean_secret(value: str | None, *, label: str, source: str) -> str | None:
    """Return *value* if it is a usable secret, else ``None`` (with a warning)."""
    if value is None:
        return None
    value = value.strip()
    if not value:
        return None
    if _is_placeholder(value):
        logger.critical(
            "%s from %s is a template placeholder and has been REJECTED. "
            "Set a real value in the environment before serving traffic.",
            label,
            source,
        )
        return None
    if len(value) < MIN_SECRET_LENGTH:
        logger.critical(
            "%s from %s is only %d chars; minimum is %d. REJECTED.",
            label,
            source,
            len(value),
            MIN_SECRET_LENGTH,
        )
        return None
    return value


MIN_PASSWORD_LENGTH = 6


def _clean_password(value: str | None) -> str | None:
    """Return the configured login password, or ``None`` if unusable.

    The value shipped in ``.env.example`` is rejected outright so a deployment
    that was never edited fails closed instead of accepting a public default.
    """
    if value is None:
        return None
    value = value.strip()
    if not value:
        return None
    if _is_placeholder(value):
        logger.critical(
            "PAI_PASSWORD is still the example value and has been REJECTED. "
            "Set a real password in .env before signing in."
        )
        return None
    if len(value) < MIN_PASSWORD_LENGTH:
        logger.critical(
            "PAI_PASSWORD is only %d characters; minimum is %d. REJECTED.",
            len(value),
            MIN_PASSWORD_LENGTH,
        )
        return None
    return value


def _positive_int(value: str | None, default: int) -> int:
    try:
        parsed = int(value) if value else default
    except ValueError:
        return default
    return parsed if parsed > 0 else default


def _resolve_secret(
    explicit: str | None,
    env_var: str,
    config_value: str | None,
    label: str,
) -> str | None:
    """Resolve a credential, preferring the environment over the config file.

    Precedence is explicit argument > environment > config file. The config
    file is last because it is checked into the repo on most deployments;
    an env var must always be able to override it.
    """
    for value, source in (
        (explicit, "constructor argument"),
        (os.environ.get(env_var), f"${env_var}"),
        (config_value, "config/remote.json"),
    ):
        cleaned = _clean_secret(value, label=label, source=source)
        if cleaned is not None:
            return cleaned
    return None


@dataclass
class AuthenticatedClient:
    session_id: str
    session_name: str
    auth_type: str
    user_id: str


@dataclass
class IssuedToken:
    access_token: str
    expires_at: datetime
    user_id: str


class LoginThrottle:
    """Exponential backoff on failed logins.

    This is a single-user deployment, so the lockout is global rather than
    per-account: there is no other account for an attacker to lock out, and a
    global counter is immune to source-address spoofing.
    """

    def __init__(self, free_attempts: int = 5, base_delay: float = 2.0, max_delay: float = 300.0) -> None:
        self._free_attempts = free_attempts
        self._base_delay = base_delay
        self._max_delay = max_delay
        self._failures = 0
        self._blocked_until = 0.0

    def retry_after(self) -> int:
        """Seconds the caller must wait, or 0 when a login may be attempted."""
        remaining = self._blocked_until - time.monotonic()
        return max(0, int(remaining) + 1) if remaining > 0 else 0

    def record_failure(self) -> None:
        self._failures += 1
        if self._failures <= self._free_attempts:
            return
        delay = min(self._base_delay * (2 ** (self._failures - self._free_attempts - 1)), self._max_delay)
        self._blocked_until = time.monotonic() + delay
        logger.warning(
            "Failed login #%d; further attempts blocked for %.0fs", self._failures, delay
        )

    def record_success(self) -> None:
        self._failures = 0
        self._blocked_until = 0.0


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

        self._jwt_secret = _resolve_secret(jwt_secret, "PAI_JWT_SECRET", config_secret, "JWT secret")
        self._api_key = _resolve_secret(api_key, "PAI_API_KEY", config_api_key, "API key")
        self._algorithm = config_algorithm or algorithm
        self._audit_log = audit_log
        self._default_user_id = os.environ.get("PAI_DEFAULT_USER_ID", "default_user")

        # Password login. The password lives in the environment (.env locally,
        # project env vars on the host) — both are already the trust boundary
        # for the API key and database URL sitting beside it.
        self._password = _clean_password(os.environ.get("PAI_PASSWORD"))

        self._session_days = _positive_int(os.environ.get("PAI_SESSION_DAYS"), default=30)
        self._throttle = LoginThrottle()

        if self._jwt_secret is None and self._api_key is None:
            logger.critical(
                "No usable authentication credential configured. Every request will "
                "be rejected with 401. Set PAI_API_KEY (and/or PAI_JWT_SECRET) to at "
                "least %d random characters.",
                MIN_SECRET_LENGTH,
            )
        if self.password_login_available:
            logger.info("Password login enabled; sessions last %d days.", self._session_days)

    @property
    def default_user_id(self) -> str:
        """The user a credential-only (no JWT subject) caller resolves to.

        The scheduler authenticates as the deployment rather than as a person,
        so it needs this to know whose nudges to evaluate.
        """
        return self._default_user_id

    # ------------------------------------------------------------------
    # Password login
    # ------------------------------------------------------------------

    @property
    def password_login_available(self) -> bool:
        """True when a password is configured and tokens can be signed."""
        return bool(self._jwt_secret) and bool(self._password)

    @property
    def session_days(self) -> int:
        return self._session_days

    def login(self, password: str) -> IssuedToken:
        """Exchange the account password for a long-lived bearer token.

        Raises ``HTTPException`` on a bad password, on lockout, or when
        password login is not configured.
        """
        if not self.password_login_available:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=(
                    "Password login is not configured. Set PAI_PASSWORD and "
                    "PAI_JWT_SECRET in .env (or your host's env vars)."
                ),
            )

        retry_after = self._throttle.retry_after()
        if retry_after:
            self._log_failure("login_throttled")
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"Too many failed attempts. Try again in {retry_after}s.",
                headers={"Retry-After": str(retry_after)},
            )

        if not self._password_matches(password):
            self._throttle.record_failure()
            self._log_failure("invalid_password")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail="Incorrect password"
            )

        self._throttle.record_success()
        return self.issue_token(self._default_user_id)

    def _password_matches(self, password: str) -> bool:
        if not password or not self._password:
            return False
        return secrets.compare_digest(password, self._password)

    def issue_token(self, user_id: str, days: int | None = None) -> IssuedToken:
        """Sign a bearer token for *user_id*."""
        if not self._jwt_secret:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="PAI_JWT_SECRET is not configured; cannot issue tokens.",
            )

        now = datetime.now(tz=timezone.utc)
        expires_at = now + timedelta(days=days or self._session_days)
        token = jwt.encode(
            {
                "sub": user_id,
                "uid": user_id,
                "session": "remote_session",
                "iat": int(now.timestamp()),
                "exp": int(expires_at.timestamp()),
            },
            self._jwt_secret,
            algorithm=self._algorithm,
        )
        return IssuedToken(access_token=token, expires_at=expires_at, user_id=user_id)

    async def authenticate(self, authorization: str | None = Header(default=None)) -> AuthenticatedClient:
        """FastAPI dependency entrypoint."""
        if not authorization:
            self._log_failure("missing_authorization")
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing authorization header")

        scheme, _, credential = authorization.partition(" ")
        if scheme.lower() != "bearer" or not credential:
            self._log_failure("invalid_authorization_scheme")
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid authorization header")

        # compare_digest keeps the comparison time independent of how many
        # leading characters happen to match.
        if self._api_key and secrets.compare_digest(credential, self._api_key):
            return AuthenticatedClient(
                session_id="remote-api-key",
                session_name="remote_session",
                auth_type="api_key",
                user_id=self._default_user_id,
            )

        if self._jwt_secret:
            try:
                payload = jwt.decode(credential, self._jwt_secret, algorithms=[self._algorithm])
            except ExpiredSignatureError:
                self._log_failure("expired_jwt")
                # Distinct detail so the client knows to prompt for the
                # password again rather than reporting a broken config.
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED, detail="Session expired"
                ) from None
            except JWTError:
                self._log_failure("invalid_jwt")
                raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token") from None

            subject = str(payload.get("sub", "remote-jwt"))
            return AuthenticatedClient(
                session_id=subject,
                session_name=str(payload.get("session", "remote_session")),
                auth_type="jwt",
                user_id=str(payload.get("uid", subject)),
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
