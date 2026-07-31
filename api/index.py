"""Vercel serverless entrypoint for the Personal AI Assistant.

Vercel's Python runtime requires a bare top-level assignment of the form
`app = <callable>()` so its static AST parser can detect the ASGI handler.
All error handling is therefore kept inside `_build_app()` rather than
wrapping the assignment itself in a try/except.

Wiring lives in ``src.main.create_runtime`` so this entrypoint and the local
one cannot drift apart; the only difference is the profile.
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

from fastapi import FastAPI

# Ensure the project root is on sys.path so `src.*` imports resolve.
_root = Path(__file__).parent.parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

logger = logging.getLogger(__name__)
_init_error: str | None = None  # populated when _build_app() fails


def _build_app() -> FastAPI:
    """Build the full runtime app, or return a degraded app on failure."""
    try:
        from src.main import create_runtime

        return create_runtime(profile="serverless").app

    except Exception as exc:
        import traceback

        global _init_error
        _init_error = f"{type(exc).__name__}: {exc}\n{traceback.format_exc()}"
        logger.exception("PAI runtime failed to initialise; starting in degraded mode")
        _app = FastAPI(title="Personal AI Assistant (degraded)")

        @_app.get("/health")
        async def health():
            return {
                "status": "degraded",
                "reason": "runtime failed to initialize",
                "error": _init_error,
            }

        return _app


# ── Bare top-level assignment ── Vercel's AST parser requires this exact form.
app = _build_app()
