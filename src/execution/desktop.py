"""Execution Layer — desktop automation primitives.

Uses ``pyautogui`` for input simulation and ``subprocess`` for app
lifecycle.  All actions run on the **local** machine only.

Each function returns ``{success: bool, error: str | None}``.
``screenshot`` additionally returns ``image_bytes``.
"""

from __future__ import annotations

import io
import subprocess
import sys
from typing import Any

import pyautogui


# Prevent pyautogui from raising for coordinates near screen edges
pyautogui.FAILSAFE = False


def launch_app(app_name: str) -> dict[str, Any]:
    """Launch an application by name using the platform shell.

    On Windows uses ``start``, on macOS ``open``, on Linux ``xdg-open``.
    """
    try:
        if sys.platform == "win32":
            subprocess.Popen(["start", "", app_name], shell=True)
        elif sys.platform == "darwin":
            subprocess.Popen(["open", "-a", app_name])
        else:
            subprocess.Popen(["xdg-open", app_name])
        return {"success": True, "error": None}
    except Exception as exc:
        return {"success": False, "error": str(exc)}


def close_app(app_name: str) -> dict[str, Any]:
    """Close an application by name.

    On Windows uses ``taskkill``, on POSIX uses ``pkill``.
    """
    try:
        if sys.platform == "win32":
            result = subprocess.run(
                ["taskkill", "/IM", app_name, "/F"],
                capture_output=True,
                text=True,
            )
            if result.returncode != 0:
                return {"success": False, "error": result.stderr.strip() or "Process not found"}
        else:
            result = subprocess.run(
                ["pkill", "-f", app_name],
                capture_output=True,
                text=True,
            )
            if result.returncode != 0:
                return {"success": False, "error": "Process not found or already stopped"}
        return {"success": True, "error": None}
    except Exception as exc:
        return {"success": False, "error": str(exc)}


def keyboard_input(text: str) -> dict[str, Any]:
    """Simulate typing *text* via keyboard input."""
    try:
        pyautogui.typewrite(text, interval=0.02)
        return {"success": True, "error": None}
    except Exception as exc:
        return {"success": False, "error": str(exc)}


def mouse_click(x: int, y: int, button: str = "left") -> dict[str, Any]:
    """Simulate a mouse click at screen coordinates (*x*, *y*)."""
    try:
        pyautogui.click(x=x, y=y, button=button)
        return {"success": True, "error": None}
    except Exception as exc:
        return {"success": False, "error": str(exc)}


def screenshot() -> dict[str, Any]:
    """Capture a screenshot and return the PNG image bytes."""
    try:
        img = pyautogui.screenshot()
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        image_bytes = buf.getvalue()
        return {"success": True, "error": None, "image_bytes": image_bytes}
    except Exception as exc:
        return {"success": False, "error": str(exc), "image_bytes": None}
