"""System Control domain tools — desktop automation with confirmation.

All tools require ``permission_level="execute"`` and
``requires_confirmation=True``.  Only a defined set of action types
is supported; unknown actions are rejected.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from src.execution import desktop
from src.tools.registry import ToolDefinition, ToolRegistry
from src.types import DomainName, PermissionLevel


# ---------------------------------------------------------------------------
# get_current_datetime
# ---------------------------------------------------------------------------

_GET_DATETIME_INPUT: dict[str, Any] = {
    "type": "object",
    "properties": {},
    "additionalProperties": False,
}

_GET_DATETIME_OUTPUT: dict[str, Any] = {
    "type": "object",
    "properties": {
        "iso": {"type": "string"},
        "date": {"type": "string"},
        "time": {"type": "string"},
        "timezone": {"type": "string"},
        "unix_timestamp": {"type": "number"},
    },
    "required": ["iso", "date", "time", "timezone", "unix_timestamp"],
}


class GetCurrentDateTimeTool(ToolDefinition):
    """Return the current date and time in UTC.

    This tool requires no inputs and is available to all agents so they
    can resolve relative temporal references like "today" or "yesterday".
    """

    name = "system_control.get_current_datetime"
    description = (
        "Get the current date and time. Use this tool whenever you need to "
        "know today's date, the current time, or resolve relative dates "
        "like 'today' or 'yesterday'."
    )
    domain = DomainName.system_control
    permission_level = PermissionLevel.read
    requires_confirmation = False
    input_schema = _GET_DATETIME_INPUT
    output_schema = _GET_DATETIME_OUTPUT

    def execute(self, inputs: dict[str, Any]) -> Any:
        now = datetime.now(tz=timezone.utc)
        return {
            "iso": now.isoformat(),
            "date": now.strftime("%Y-%m-%d"),
            "time": now.strftime("%H:%M:%S"),
            "timezone": "UTC",
            "unix_timestamp": now.timestamp(),
        }


# ---------------------------------------------------------------------------
# Permitted actions set
# ---------------------------------------------------------------------------

_PERMITTED_ACTIONS = frozenset([
    "launch_app",
    "close_app",
    "keyboard_input",
    "mouse_click",
    "screenshot",
])


# ---------------------------------------------------------------------------
# launch_app
# ---------------------------------------------------------------------------

_LAUNCH_APP_INPUT: dict[str, Any] = {
    "type": "object",
    "properties": {
        "app_name": {"type": "string"},
    },
    "required": ["app_name"],
    "additionalProperties": False,
}

_SYSTEM_RESULT_OUTPUT: dict[str, Any] = {
    "type": "object",
    "properties": {
        "success": {"type": "boolean"},
        "error": {"type": ["string", "null"]},
    },
    "required": ["success", "error"],
}


class LaunchAppTool(ToolDefinition):
    name = "system_control.launch_app"
    description = "Launch a desktop application by name."
    domain = DomainName.system_control
    permission_level = PermissionLevel.execute
    requires_confirmation = True
    input_schema = _LAUNCH_APP_INPUT
    output_schema = _SYSTEM_RESULT_OUTPUT

    def execute(self, inputs: dict[str, Any]) -> Any:
        result = desktop.launch_app(inputs["app_name"])
        if not result["success"]:
            raise RuntimeError(result["error"])
        return {"success": True, "error": None}


# ---------------------------------------------------------------------------
# close_app
# ---------------------------------------------------------------------------

_CLOSE_APP_INPUT: dict[str, Any] = {
    "type": "object",
    "properties": {
        "app_name": {"type": "string"},
    },
    "required": ["app_name"],
    "additionalProperties": False,
}


class CloseAppTool(ToolDefinition):
    name = "system_control.close_app"
    description = "Close a desktop application by name."
    domain = DomainName.system_control
    permission_level = PermissionLevel.execute
    requires_confirmation = True
    input_schema = _CLOSE_APP_INPUT
    output_schema = _SYSTEM_RESULT_OUTPUT

    def execute(self, inputs: dict[str, Any]) -> Any:
        result = desktop.close_app(inputs["app_name"])
        if not result["success"]:
            raise RuntimeError(result["error"])
        return {"success": True, "error": None}


# ---------------------------------------------------------------------------
# keyboard_input
# ---------------------------------------------------------------------------

_KEYBOARD_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "text": {"type": "string"},
    },
    "required": ["text"],
    "additionalProperties": False,
}


class KeyboardInputTool(ToolDefinition):
    name = "system_control.keyboard_input"
    description = "Simulate keyboard text input."
    domain = DomainName.system_control
    permission_level = PermissionLevel.execute
    requires_confirmation = True
    input_schema = _KEYBOARD_INPUT_SCHEMA
    output_schema = _SYSTEM_RESULT_OUTPUT

    def execute(self, inputs: dict[str, Any]) -> Any:
        result = desktop.keyboard_input(inputs["text"])
        if not result["success"]:
            raise RuntimeError(result["error"])
        return {"success": True, "error": None}


# ---------------------------------------------------------------------------
# mouse_click
# ---------------------------------------------------------------------------

_MOUSE_CLICK_INPUT: dict[str, Any] = {
    "type": "object",
    "properties": {
        "x": {"type": "integer"},
        "y": {"type": "integer"},
        "button": {"type": "string", "enum": ["left", "right", "middle"]},
    },
    "required": ["x", "y"],
    "additionalProperties": False,
}


class MouseClickTool(ToolDefinition):
    name = "system_control.mouse_click"
    description = "Simulate a mouse click at screen coordinates."
    domain = DomainName.system_control
    permission_level = PermissionLevel.execute
    requires_confirmation = True
    input_schema = _MOUSE_CLICK_INPUT
    output_schema = _SYSTEM_RESULT_OUTPUT

    def execute(self, inputs: dict[str, Any]) -> Any:
        button = inputs.get("button", "left")
        result = desktop.mouse_click(inputs["x"], inputs["y"], button)
        if not result["success"]:
            raise RuntimeError(result["error"])
        return {"success": True, "error": None}


# ---------------------------------------------------------------------------
# screenshot
# ---------------------------------------------------------------------------

_SCREENSHOT_INPUT: dict[str, Any] = {
    "type": "object",
    "properties": {},
    "additionalProperties": False,
}

_SCREENSHOT_OUTPUT: dict[str, Any] = {
    "type": "object",
    "properties": {
        "success": {"type": "boolean"},
        "error": {"type": ["string", "null"]},
        "has_image": {"type": "boolean"},
    },
    "required": ["success", "error", "has_image"],
}


class ScreenshotTool(ToolDefinition):
    name = "system_control.screenshot"
    description = "Capture a screenshot of the desktop."
    domain = DomainName.system_control
    permission_level = PermissionLevel.execute
    requires_confirmation = True
    input_schema = _SCREENSHOT_INPUT
    output_schema = _SCREENSHOT_OUTPUT

    def execute(self, inputs: dict[str, Any]) -> Any:
        result = desktop.screenshot()
        if not result["success"]:
            raise RuntimeError(result["error"])
        # Store image_bytes for audit/processing but validate with simpler schema
        return {
            "success": True,
            "error": None,
            "has_image": result.get("image_bytes") is not None,
        }


# ---------------------------------------------------------------------------
# Registration helper
# ---------------------------------------------------------------------------


def register_system_control_tools(registry: ToolRegistry) -> None:
    """Register all System Control domain tools into *registry*."""
    registry.register(GetCurrentDateTimeTool())
    registry.register(LaunchAppTool())
    registry.register(CloseAppTool())
    registry.register(KeyboardInputTool())
    registry.register(MouseClickTool())
    registry.register(ScreenshotTool())
