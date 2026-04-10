"""Permission system for the Personal AI Assistant.

Enforces the read < write < execute < admin hierarchy.
Loaded from a config file at startup; no runtime elevation.
"""

from __future__ import annotations

from src.config import PermissionsConfig, load_permissions_config
from src.types import PermissionLevel


class AuthorizationError(Exception):
    """Raised when a session lacks the required permission level for a tool."""


class PermissionSystem:
    """Loads session grants from config and enforces the permission hierarchy."""

    def __init__(self) -> None:
        self._config: PermissionsConfig | None = None

    # ------------------------------------------------------------------
    # Config loading
    # ------------------------------------------------------------------

    def load_from_config(self, path: str = "config/permissions.json") -> None:
        """Load permission grants from *path* using the shared config loader."""
        self._config = load_permissions_config(path)

    # ------------------------------------------------------------------
    # Core check
    # ------------------------------------------------------------------

    def check(
        self,
        tool_permission_level: PermissionLevel,
        session_grants: list[PermissionLevel],
    ) -> bool:
        """Return True if any grant in *session_grants* satisfies *tool_permission_level*.

        A grant satisfies the requirement when grant >= tool_permission_level
        (i.e. the grant is at least as powerful as what the tool requires).

        Raises:
            AuthorizationError: if no grant is sufficient.
        """
        for grant in session_grants:
            if grant >= tool_permission_level:
                return True
        raise AuthorizationError(
            f"Session grants {[g.value for g in session_grants]!r} are insufficient "
            f"for tool requiring {tool_permission_level.value!r}."
        )

    # ------------------------------------------------------------------
    # Session helpers
    # ------------------------------------------------------------------

    def get_session_grants(self, session_name: str) -> list[PermissionLevel]:
        """Return the list of :class:`PermissionLevel` grants for *session_name*.

        Raises:
            RuntimeError: if config has not been loaded yet.
            KeyError: if *session_name* is not found in the config.
        """
        if self._config is None:
            raise RuntimeError(
                "PermissionSystem config not loaded. Call load_from_config() first."
            )
        session = self._config.get(session_name)
        if session is None:
            raise KeyError(f"Session {session_name!r} not found in permissions config.")
        return [PermissionLevel(g) for g in session.grants]
