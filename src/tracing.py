"""LangSmith tracing helpers for optional runtime instrumentation."""

from __future__ import annotations

import os
from functools import wraps
from typing import Any, Callable, TypeVar, cast

F = TypeVar("F", bound=Callable[..., Any])


def is_langsmith_enabled() -> bool:
    """Return True when LangSmith tracing is configured and enabled."""
    if "PYTEST_CURRENT_TEST" in os.environ:
        return False
    return (
        os.environ.get("LANGSMITH_TRACING", "").lower() == "true"
        and bool(os.environ.get("LANGSMITH_API_KEY"))
    )


def wrap_openai_client(client: Any) -> Any:
    """Wrap an OpenAI client with LangSmith instrumentation when available."""
    if not is_langsmith_enabled():
        return client

    try:
        from langsmith.wrappers import wrap_openai
    except ImportError:
        return client

    return wrap_openai(client)


def traceable_if_available(name: str) -> Callable[[F], F]:
    """Apply LangSmith's traceable decorator when tracing is enabled."""
    if not is_langsmith_enabled():
        return lambda func: func

    try:
        from langsmith import traceable
    except ImportError:
        return lambda func: func

    def decorator(func: F) -> F:
        wrapped = traceable(name=name)(func)
        return cast(F, wrapped)

    return decorator


def with_langsmith_extra(extra: dict[str, Any]) -> Callable[[F], F]:
    """Attach contextual metadata to a traced function when supported."""
    if not is_langsmith_enabled():
        return lambda func: func

    try:
        from langsmith import traceable
    except ImportError:
        return lambda func: func

    def decorator(func: F) -> F:
        traced = traceable(name=func.__name__)(func)

        @wraps(func)
        async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
            kwargs.setdefault("langsmith_extra", {}).update(extra)
            return await traced(*args, **kwargs)

        @wraps(func)
        def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
            kwargs.setdefault("langsmith_extra", {}).update(extra)
            return traced(*args, **kwargs)

        return cast(F, async_wrapper if _is_async_callable(func) else sync_wrapper)

    return decorator


def _is_async_callable(func: Callable[..., Any]) -> bool:
    return getattr(func, "__code__", None) is not None and bool(func.__code__.co_flags & 0x80)
