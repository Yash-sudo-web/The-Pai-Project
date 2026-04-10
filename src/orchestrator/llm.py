"""LLM client wrapper for the Personal AI Assistant Orchestrator.

Wraps OpenAI chat completions (or any compatible API) and instructs the LLM
to return a structured ``IntentPlan`` JSON object.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any

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
# System prompt builder
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT_TEMPLATE = """\
You are an intent-parsing assistant. Your job is to interpret the user's natural language command and return ONLY a valid JSON object — no markdown, no explanation, no extra text.

Current date and time (UTC): {current_datetime}

The JSON object must match this schema exactly:
{{
  "action_type": "<string describing the action, e.g. log_workout>",
  "domain": "<one of: gym, nutrition, productivity, system_control>",
  "parameters": {{<key-value pairs of extracted parameters>}},
  "steps": [
    {{
      "tool_name": "<name of a registered tool>",
      "inputs": {{<validated inputs for that tool>}}
    }}
  ],
  "requires_confirmation": <true | false>
}}

Available tools (name → input_schema):
{tools_json}

Rules:
- Use ONLY tool names from the list above.
- Set requires_confirmation to true if any selected tool has requires_confirmation=true.
- If the intent is ambiguous, set action_type to "clarify" and steps to [].
- Return ONLY the JSON object. Do not wrap it in markdown code fences.
- When the user refers to relative dates like "today", "yesterday", "this week", resolve them using the current date/time provided above.
"""


def _build_system_prompt(tools: list[ToolDefinition]) -> str:
    tools_info: list[dict[str, Any]] = [
        {
            "name": t.name,
            "description": t.description,
            "input_schema": t.input_schema,
            "requires_confirmation": t.requires_confirmation,
        }
        for t in tools
    ]
    now = datetime.now(tz=timezone.utc)
    return _SYSTEM_PROMPT_TEMPLATE.format(
        tools_json=json.dumps(tools_info, indent=2),
        current_datetime=now.strftime("%Y-%m-%d %H:%M:%S UTC"),
    )


# ---------------------------------------------------------------------------
# LLMClient
# ---------------------------------------------------------------------------


class LLMClient:
    """Thin async wrapper around OpenAI chat completions."""

    def __init__(
        self,
        api_key: str | None = None,
        model: str = "gpt-5-mini",
        base_url: str | None = None,
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
        self._client = wrap_openai_client(openai.AsyncOpenAI(
            api_key=resolved_key,
            base_url=resolved_base_url,
            default_headers=default_headers,
        ))

    @traceable_if_available("llm.complete")
    async def complete(
        self,
        user_message: str,
        tools: list[ToolDefinition],
        timeout: float = 30.0,
    ) -> str:
        """Call the LLM and return the raw JSON string response.

        Args:
            user_message: The user's natural language command.
            tools: Registered tools whose names and schemas are injected into
                   the system prompt.
            timeout: Request timeout in seconds (default 30).

        Returns:
            Raw JSON string from the LLM response content.

        Raises:
            LLMError: On any API or connection error.
        """
        system_prompt = _build_system_prompt(tools)
        try:
            response = await self._client.chat.completions.create(
                model=self._model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_message},
                ],
                timeout=timeout,
            )
        except openai.APIError as exc:
            raise LLMError(str(exc)) from exc
        except Exception as exc:
            raise LLMError(str(exc)) from exc

        content = response.choices[0].message.content
        if content is None:
            raise LLMError("LLM returned an empty response.")
        return content
