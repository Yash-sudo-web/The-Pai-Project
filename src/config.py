"""Config loader for the Personal AI Assistant.

Loads tool registry config (config/tools.json) and session permission grants
(config/permissions.json) at startup using Pydantic v2 models.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

from pydantic import BaseModel


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class RateLimitConfig(BaseModel):
    max_calls: int
    window_seconds: int


class ToolConfig(BaseModel):
    name: str
    domain: str
    permission_level: str
    requires_confirmation: bool
    rate_limit: RateLimitConfig


class ToolsConfig(BaseModel):
    tools: list[ToolConfig]
    disabled_domains: list[str] = []


class SessionPermissions(BaseModel):
    grants: list[str]


class PermissionsConfig(BaseModel):
    """Mapping of session_name -> SessionPermissions."""

    sessions: dict[str, SessionPermissions] = {}

    def get(self, session_name: str) -> SessionPermissions | None:
        return self.sessions.get(session_name)

    def __getitem__(self, session_name: str) -> SessionPermissions:
        return self.sessions[session_name]

    def __contains__(self, session_name: object) -> bool:
        return session_name in self.sessions


class RemoteAuthConfig(BaseModel):
    jwt_secret: str | None = None
    api_key: str | None = None
    jwt_algorithm: str = "HS256"


# ---------------------------------------------------------------------------
# Loader functions
# ---------------------------------------------------------------------------


def load_env_file(path: str = ".env", override: bool = False) -> None:
    """Load simple KEY=VALUE pairs from a dotenv-style file into os.environ."""
    env_path = Path(path)
    if not env_path.exists():
        return

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")

        if not key:
            continue
        if key in os.environ and not override:
            continue
        os.environ[key] = value


def load_tools_config(path: str = "config/tools.json") -> ToolsConfig:
    """Load and validate the tool registry config from *path*."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return ToolsConfig.model_validate(data)


def load_permissions_config(path: str = "config/permissions.json") -> PermissionsConfig:
    """Load and validate the session permissions config from *path*.

    The JSON is a flat mapping of session_name -> {grants: [...]}, which is
    normalised into a ``PermissionsConfig`` with a ``sessions`` dict.
    """
    raw: dict = json.loads(Path(path).read_text(encoding="utf-8"))
    sessions = {name: SessionPermissions.model_validate(value) for name, value in raw.items()}
    return PermissionsConfig(sessions=sessions)


def load_remote_auth_config(path: str = "config/remote.json") -> RemoteAuthConfig:
    """Load remote authentication settings from *path*."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return RemoteAuthConfig.model_validate(data)


# ---------------------------------------------------------------------------
# Module-level AppConfig (lazy-loaded on first access)
# ---------------------------------------------------------------------------


@dataclass
class AppConfig:
    """Holds both configs; populated lazily on first access."""

    _tools: ToolsConfig | None = field(default=None, repr=False)
    _permissions: PermissionsConfig | None = field(default=None, repr=False)
    _remote: RemoteAuthConfig | None = field(default=None, repr=False)
    _tools_path: str = field(default="config/tools.json", repr=False)
    _permissions_path: str = field(default="config/permissions.json", repr=False)
    _remote_path: str = field(default="config/remote.json", repr=False)

    @property
    def tools(self) -> ToolsConfig:
        if self._tools is None:
            self._tools = load_tools_config(self._tools_path)
        return self._tools

    @property
    def permissions(self) -> PermissionsConfig:
        if self._permissions is None:
            self._permissions = load_permissions_config(self._permissions_path)
        return self._permissions

    @property
    def remote(self) -> RemoteAuthConfig:
        if self._remote is None:
            self._remote = load_remote_auth_config(self._remote_path)
        return self._remote

    def reload(self) -> None:
        """Force a reload of both config files."""
        self._tools = load_tools_config(self._tools_path)
        self._permissions = load_permissions_config(self._permissions_path)
        self._remote = load_remote_auth_config(self._remote_path)


# Singleton instance used throughout the application.
load_env_file()
app_config = AppConfig()
