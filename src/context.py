"""Per-request context that domain tools can read without new parameters.

Tool ``execute()`` signatures take only their validated inputs, so the owning
user cannot be passed as an argument without changing every tool. A
``ContextVar`` gives the same effect: the orchestrator binds the identity once
per command and any tool — sync or async — reads it via
:func:`current_user_id`.

``ContextVar`` values propagate into ``asyncio.to_thread`` (it copies the
current context), so tools offloaded to the thread pool see the right user.
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import timedelta, timezone
from typing import Iterator

# The user's wall clock. Daily sessions roll over on this boundary and
# time-of-day rules ("is it evening yet?") are judged against it — comparing
# against UTC would put "evening" in the middle of the night.
LOCAL_TZ = timezone(timedelta(hours=5, minutes=30))  # IST

# Kept as the default so existing single-user rows stay reachable after the
# identity plumbing lands.
DEFAULT_USER_ID = os.environ.get("PAI_DEFAULT_USER_ID", "default_user")

_current_user_id: ContextVar[str] = ContextVar("pai_current_user_id", default=DEFAULT_USER_ID)


def current_user_id() -> str:
    """Return the user the in-flight command belongs to."""
    return _current_user_id.get()


@contextmanager
def user_scope(user_id: str | None) -> Iterator[str]:
    """Bind *user_id* for the duration of the block."""
    resolved = user_id or DEFAULT_USER_ID
    token = _current_user_id.set(resolved)
    try:
        yield resolved
    finally:
        _current_user_id.reset(token)
