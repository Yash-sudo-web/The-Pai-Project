"""LLM client wrapper for the Personal AI Assistant Orchestrator.

Wraps OpenAI-compatible chat completions and exposes native tool calling, so
the model selects tools through the provider's function-calling API rather
than by emitting hand-parsed JSON.

Prompt layout matters here: providers cache on the *longest common prefix* of
a request, so everything static (the persona, the tool guidance) is emitted
first and anything volatile (the current timestamp) is appended as a separate
message after the conversation history. Putting the clock near the top would
invalidate the cache on every single request.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from typing import Any, AsyncIterator, Callable, Iterable, Sequence

import openai

from src.tracing import traceable_if_available, wrap_openai_client
from src.tools.registry import ToolDefinition


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class LLMError(Exception):
    """Raised when the LLM API call fails for any reason."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


# ---------------------------------------------------------------------------
# Tool name <-> function name mapping
# ---------------------------------------------------------------------------

# OpenAI-compatible function names must match ^[a-zA-Z0-9_-]{1,64}$, so the
# dotted tool names ("gym.log_workout") need escaping on the wire.
_INVALID_FUNCTION_CHARS = re.compile(r"[^a-zA-Z0-9_-]")


def tool_name_to_function_name(tool_name: str) -> str:
    """Encode a registry tool name as a provider-safe function name."""
    return _INVALID_FUNCTION_CHARS.sub("__", tool_name)


def build_function_name_map(tools: Sequence[ToolDefinition]) -> dict[str, str]:
    """Return ``{function_name: tool_name}`` for decoding tool call responses."""
    return {tool_name_to_function_name(t.name): t.name for t in tools}


# The registry is fixed at startup, so the serialised schema block is built
# once and reused instead of being rebuilt on every request.
_TOOL_SCHEMA_CACHE: dict[tuple[str, ...], list[dict[str, Any]]] = {}


def to_openai_tools(tools: Sequence[ToolDefinition]) -> list[dict[str, Any]]:
    """Convert registry tools into the provider's function-calling schema."""
    cache_key = tuple(tool.name for tool in tools)
    cached = _TOOL_SCHEMA_CACHE.get(cache_key)
    if cached is not None:
        return cached

    payload: list[dict[str, Any]] = []
    for tool in tools:
        schema = dict(tool.input_schema or {"type": "object", "properties": {}})
        schema.setdefault("type", "object")
        schema.setdefault("properties", {})
        description = tool.description
        if tool.requires_confirmation:
            description = f"{description} (Requires explicit user confirmation before it runs.)"
        payload.append(
            {
                "type": "function",
                "function": {
                    "name": tool_name_to_function_name(tool.name),
                    "description": description,
                    "parameters": schema,
                },
            }
        )

    _TOOL_SCHEMA_CACHE[cache_key] = payload
    return payload


# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

# Static, cacheable prefix. Tool *schemas* are no longer inlined here — the
# provider receives them in the `tools` parameter instead, which removes ~5k
# tokens of duplicated JSON from every request.
ASSISTANT_SYSTEM_PROMPT = """\
You are a personal AI assistant for a single user. You have tools for logging \
and querying the user's gym workouts, nutrition, tasks, and device control.

How to respond:
- If the request needs the user's personal data or an action, call the \
appropriate tool(s). You may call several tools at once when the request needs it.
- If it is a greeting, a general question, or small talk, just answer directly \
without calling any tool.
- Never claim you performed an action unless a tool actually ran.
- Resolve relative dates ("today", "last Monday", "this week") using the \
current date/time supplied at the end of the conversation.
- When summarising tool results, write naturally: no database IDs, no raw JSON, \
no internal status fields.

## Gym
- For pre-session planning ("what should I do at the gym today", "what did I do \
last Wednesday"), call `gym.get_workout_history` for the relevant day, then \
`gym.suggest_progression` for each key exercise — in the same turn.
- For personal records or best lifts, use `gym.check_pr`.
- `gym.log_workout` detects PRs automatically. When a PR comes back, celebrate it.
- Present history grouped by date, as sets × reps @ weight. Frame progression \
suggestions as coaching advice with the reasoning ("you hit 3×10 at 60kg last \
time, so let's push to 62.5kg").
- You already know the current date — never call a clock tool just to answer a \
gym question about "today".

## Nutrition
- "set my calories to 2200, protein 150g" → `nutrition.set_goals`.
- "how am I doing", "calories left", "am I on track" → `nutrition.check_goals`. \
Present it as a dashboard: consumed vs target, remaining, percentage.
- "I'm hungry", "what should I eat" → `nutrition.meal_suggestion`, passing any \
qualifier ("something light", "high protein") in `context`.
- "how have I been eating this week/month" → `nutrition.nutrition_report` with \
period="week" or "month".
- If the user is over budget, be empathetic but honest.

Keep replies conversational, concise, and motivating.
"""


def current_datetime_message() -> dict[str, str]:
    """A volatile system message; always append it AFTER the static prefix."""
    now = datetime.now(tz=timezone.utc)
    return {
        "role": "system",
        "content": f"Current date and time (UTC): {now.strftime('%Y-%m-%d %H:%M:%S')}",
    }


# ---------------------------------------------------------------------------
# LLMClient
# ---------------------------------------------------------------------------


class LLMClient:
    """Thin async wrapper around OpenAI-compatible chat completions."""

    def __init__(
        self,
        api_key: str | None = None,
        model: str = "gpt-5-mini",
        base_url: str | None = None,
        max_retries: int = 2,
    ) -> None:
        resolved_key = api_key or os.environ.get("OPENROUTER_API_KEY") or os.environ.get("OPENAI_API_KEY")
        resolved_base_url = base_url
        resolved_model = model
        default_headers: dict[str, str] | None = None

        using_openrouter = bool(
            os.environ.get("OPENROUTER_API_KEY")
            or os.environ.get("OPENROUTER_BASE_URL")
            or (resolved_key and resolved_key.startswith("sk-or-v1-"))
        )
        if using_openrouter:
            resolved_base_url = (
                base_url
                or os.environ.get("OPENROUTER_BASE_URL")
                or "https://openrouter.ai/api/v1"
            )
            resolved_model = os.environ.get("OPENROUTER_MODEL", model)

            site_url = os.environ.get("OPENROUTER_SITE_URL")
            app_name = os.environ.get("OPENROUTER_APP_NAME")
            headers: dict[str, str] = {}
            if site_url:
                headers["HTTP-Referer"] = site_url
            if app_name:
                headers["X-Title"] = app_name
            default_headers = headers or None

        self._model = resolved_model
        self._fallback_model = os.environ.get("LLM_FALLBACK_MODEL")
        # A single long-lived client keeps the httpx connection pool (and its
        # TLS session) warm across requests; rebuilding it per command cost a
        # fresh handshake on every LLM call.
        self._client = wrap_openai_client(
            openai.AsyncOpenAI(
                api_key=resolved_key,
                base_url=resolved_base_url,
                default_headers=default_headers,
                max_retries=max_retries,
            )
        )

    # ------------------------------------------------------------------
    # Core call
    # ------------------------------------------------------------------

    async def _create(self, **kwargs: Any) -> Any:
        """Issue a completion, retrying once on the fallback model."""
        try:
            return await self._client.chat.completions.create(**kwargs)
        except Exception as exc:
            if self._fallback_model and kwargs.get("model") != self._fallback_model:
                try:
                    return await self._client.chat.completions.create(
                        **{**kwargs, "model": self._fallback_model}
                    )
                except Exception as fallback_exc:
                    raise LLMError(str(fallback_exc)) from fallback_exc
            raise LLMError(str(exc)) from exc

    @traceable_if_available("llm.complete_with_tools")
    async def complete_with_tools(
        self,
        messages: list[dict],
        tools: Sequence[ToolDefinition] | None = None,
        timeout: float = 30.0,
        model_override: str | None = None,
        temperature: float | None = None,
    ) -> Any:
        """Return the raw assistant message (``.content`` and/or ``.tool_calls``)."""
        kwargs: dict[str, Any] = {
            "model": model_override or self._model,
            "messages": messages,
            "timeout": timeout,
        }
        if tools:
            kwargs["tools"] = to_openai_tools(tools)
            kwargs["tool_choice"] = "auto"
        if temperature is not None:
            kwargs["temperature"] = temperature

        response = await self._create(**kwargs)
        choices = getattr(response, "choices", None)
        if not choices:
            raise LLMError("LLM returned no choices.")
        return choices[0].message

    async def stream_with_tools(
        self,
        messages: list[dict],
        tools: Sequence[ToolDefinition] | None = None,
        timeout: float = 30.0,
        model_override: str | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """Stream a tool-calling turn.

        Yields ``{"type": "text", "delta": str}`` as prose arrives and exactly
        one closing ``{"type": "final", "content": str, "tool_calls": [...]}``.

        Streaming the *planning* call is what makes conversation feel instant:
        when the model answers directly the user sees the first words in a few
        hundred milliseconds instead of after the full generation. When it
        calls tools instead, no text is emitted and the accumulated calls come
        back in the final frame.
        """
        kwargs: dict[str, Any] = {
            "model": model_override or self._model,
            "messages": messages,
            "timeout": timeout,
            "stream": True,
        }
        if tools:
            kwargs["tools"] = to_openai_tools(tools)
            kwargs["tool_choice"] = "auto"
        # Streaming responses omit usage unless it is asked for explicitly.
        kwargs["stream_options"] = {"include_usage": True}

        try:
            stream = await self._client.chat.completions.create(**kwargs)
        except Exception as exc:
            raise LLMError(str(exc)) from exc

        content_parts: list[str] = []
        # Tool call fragments arrive spread across chunks, keyed by index.
        partial: dict[int, dict[str, str]] = {}
        usage: Any = None

        try:
            async for chunk in stream:
                usage = getattr(chunk, "usage", None) or usage
                choices = getattr(chunk, "choices", None)
                if not choices:
                    continue
                delta = getattr(choices[0], "delta", None)
                if delta is None:
                    continue

                piece = getattr(delta, "content", None)
                if piece:
                    content_parts.append(piece)
                    yield {"type": "text", "delta": piece}

                for fragment in getattr(delta, "tool_calls", None) or []:
                    slot = partial.setdefault(
                        getattr(fragment, "index", 0) or 0,
                        {"id": "", "name": "", "arguments": ""},
                    )
                    if getattr(fragment, "id", None):
                        slot["id"] = fragment.id
                    function = getattr(fragment, "function", None)
                    if function is not None:
                        if getattr(function, "name", None):
                            slot["name"] += function.name
                        if getattr(function, "arguments", None):
                            slot["arguments"] += function.arguments
        except Exception as exc:
            raise LLMError(str(exc)) from exc

        yield {
            "type": "final",
            "content": "".join(content_parts),
            "tool_calls": [partial[index] for index in sorted(partial)],
            "usage": usage,
        }

    async def stream_text(
        self,
        messages: list[dict],
        tools: Sequence[ToolDefinition] | None = None,
        timeout: float = 30.0,
        model_override: str | None = None,
        temperature: float | None = None,
        on_usage: Callable[[Any], None] | None = None,
    ) -> AsyncIterator[str]:
        """Yield response text incrementally. Used by the SSE endpoint.

        Passing *tools* keeps this request's prefix byte-identical to the
        planning request that preceded it, which turns the whole tool block
        into a cache hit; ``tool_choice="none"`` still forces a text answer.
        """
        kwargs: dict[str, Any] = {
            "model": model_override or self._model,
            "messages": messages,
            "timeout": timeout,
            "stream": True,
        }
        if tools:
            kwargs["tools"] = to_openai_tools(tools)
            kwargs["tool_choice"] = "none"
        if temperature is not None:
            kwargs["temperature"] = temperature
        kwargs["stream_options"] = {"include_usage": True}

        try:
            stream = await self._client.chat.completions.create(**kwargs)
        except Exception as exc:
            raise LLMError(str(exc)) from exc

        try:
            async for chunk in stream:
                usage = getattr(chunk, "usage", None)
                if usage is not None and on_usage is not None:
                    on_usage(usage)
                choices = getattr(chunk, "choices", None)
                if not choices:
                    continue
                delta = getattr(choices[0], "delta", None)
                piece = getattr(delta, "content", None) if delta else None
                if piece:
                    yield piece
        except Exception as exc:
            raise LLMError(str(exc)) from exc

    async def raw_chat(
        self,
        messages: list[dict],
        timeout: float = 20.0,
        temperature: float | None = None,
        model_override: str | None = None,
    ) -> str:
        """Send raw chat messages and return the text response."""
        kwargs: dict[str, Any] = {
            "model": model_override or self._model,
            "messages": messages,
            "timeout": timeout,
        }
        if temperature is not None:
            kwargs["temperature"] = temperature

        response = await self._create(**kwargs)
        content = response.choices[0].message.content
        if content is None:
            raise LLMError("LLM returned an empty response.")
        return content

    @property
    def model(self) -> str:
        """The resolved model name."""
        return self._model


# ---------------------------------------------------------------------------
# Response helpers
# ---------------------------------------------------------------------------


def decode_tool_calls(
    raw_calls: Iterable[dict[str, str]], function_name_map: dict[str, str]
) -> list[tuple[str, str, dict[str, Any]]]:
    """Decode accumulated tool calls into ``(call_id, tool_name, arguments)``.

    Malformed argument JSON becomes an empty dict; input validation downstream
    produces a better error message than a parse failure here would.
    """
    decoded: list[tuple[str, str, dict[str, Any]]] = []

    for call in raw_calls:
        function_name = call.get("name") or ""
        if not function_name:
            continue
        tool_name = function_name_map.get(function_name, function_name.replace("__", "."))

        try:
            arguments = json.loads(call.get("arguments") or "{}")
        except (json.JSONDecodeError, TypeError, ValueError):
            arguments = {}
        if not isinstance(arguments, dict):
            arguments = {}

        decoded.append((call.get("id") or tool_name, tool_name, arguments))

    return decoded


def assistant_replay_message(
    content: str | None, raw_calls: Iterable[dict[str, str]]
) -> dict[str, Any]:
    """Rebuild the assistant turn so it can be replayed in the follow-up call."""
    payload: dict[str, Any] = {"role": "assistant", "content": content or None}
    calls = [
        {
            "id": call.get("id") or call.get("name", ""),
            "type": "function",
            "function": {
                "name": call.get("name", ""),
                "arguments": call.get("arguments") or "{}",
            },
        }
        for call in raw_calls
        if call.get("name")
    ]
    if calls:
        payload["tool_calls"] = calls
    return payload
