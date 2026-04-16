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
- You may include MULTIPLE steps in a single plan when the user's request requires several tool calls.

## Gym / Workout Planning Guidance

When the user asks about **pre-session planning** (e.g., "what should I do at the gym today", "what did I do last week on this day", "what weight should I use for bench press"), create a MULTI-STEP plan:

1. **History lookup**: Use `gym.get_workout_history` first to retrieve what they did on the relevant day (use `day_of_week` for "last Monday" or `date` for specific dates).
2. **Progression suggestion**: For each key exercise found (or asked about), include a `gym.suggest_progression` step.

Example multi-step plan for "What should I do today? I usually do chest on Wednesdays":
```json
{{
  "steps": [
    {{"tool_name": "gym.get_workout_history", "inputs": {{"day_of_week": "wednesday"}}}},
    {{"tool_name": "gym.suggest_progression", "inputs": {{"exercise": "bench press"}}}},
    {{"tool_name": "gym.suggest_progression", "inputs": {{"exercise": "incline dumbbell press"}}}}
  ]
}}
```

When the user asks about **personal records / PRs / best lifts**, use `gym.check_pr`.

When **logging a workout**, use `gym.log_workout` — it automatically detects PRs. If the user mentions a PR was broken, celebrate it in the summary.

When the user asks **"what day is it"** combined with gym questions, you already have the current date/time above — resolve it directly and use `gym.get_workout_history` with the relevant date or day_of_week. Do NOT use `system_control.get_current_datetime` for this — you already know the date.

## Nutrition Planning Guidance

When the user **sets nutrition goals** (e.g., "set my calories to 2200, protein 150g, carbs 250g, fat 70g"), use `nutrition.set_goals`.

When the user asks **"how am I doing"**, **"calories left"**, **"am I on track"**, or checks their nutrition status, use `nutrition.check_goals` — it compares today's intake against active goals and returns remaining macros + progress %.

When the user says **"I'm hungry"**, **"what should I eat"**, **"suggest a meal"**, or asks for food recommendations, use `nutrition.meal_suggestion`. Pass any context from the user (e.g. "something light", "high protein") in the `context` field. This tool automatically checks time of day, remaining calorie budget, and meals already eaten.

For **"I'm hungry"** style requests, create a MULTI-STEP plan:
1. `nutrition.meal_suggestion` — to get smart, context-aware suggestions

When the user asks for **weekly/monthly nutrition reports** ("how have I been eating this week", "monthly nutrition summary"), use `nutrition.nutrition_report` with period="week" or period="month".
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

    async def raw_chat(
        self,
        messages: list[dict],
        timeout: float = 20.0,
        temperature: float | None = None,
        model_override: str | None = None,
    ) -> str:
        """Send raw chat messages and return the text response.

        This is the public API for modules that need to call the LLM
        directly with custom message lists (e.g. router, chat client,
        summariser) without reaching into private attributes.

        Args:
            messages: List of ``{"role": ..., "content": ...}`` dicts.
            timeout: Request timeout in seconds.
            temperature: Optional temperature override.
            model_override: Optional model name override.

        Returns:
            The text content of the LLM response.

        Raises:
            LLMError: On any API or connection error, or empty response.
        """
        kwargs: dict = {
            "model": model_override or self._model,
            "messages": messages,
            "timeout": timeout,
        }
        if temperature is not None:
            kwargs["temperature"] = temperature

        try:
            response = await self._client.chat.completions.create(**kwargs)
        except Exception as exc:
            raise LLMError(str(exc)) from exc

        content = response.choices[0].message.content
        if content is None:
            raise LLMError("LLM returned an empty response.")
        return content

    @property
    def model(self) -> str:
        """The resolved model name."""
        return self._model
