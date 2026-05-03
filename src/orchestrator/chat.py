"""Direct conversational response path for non-tool requests.

Now integrated with ChatSessionManager for message persistence,
cross-day context injection via session summaries, and full
intra-day conversation history.
"""

from __future__ import annotations

import os
from typing import Any

from src.orchestrator.llm import LLMClient, LLMError
from src.tracing import traceable_if_available


_CHAT_SYSTEM_PROMPT = """\
You are a helpful personal AI assistant for one user.

Rules:
- Answer naturally and concisely.
- Do not invent tool executions or claim an action was performed when it was not.
- If the user is chatting, greeting you, asking for help, or asking a general question, answer directly.
- If the user appears to want the system to perform an action, briefly explain that you can help and ask them to phrase it as a command if needed.
- If past conversation summaries are provided, use them as context to personalise your responses. Reference previous topics naturally when relevant.

## Gym / Workout Responses:
- When summarising workout history, present it in a clean, readable format: group by date, list exercises with sets × reps @ weight.
- When a PR (personal record) alert is included in tool results, celebrate it enthusiastically! Use encouraging language.
- When presenting progression suggestions, frame them as friendly coaching advice, not raw data. Mention the reasoning (e.g., "Since you hit 3×10 at 60kg last time, let's push to 62.5kg").
- If the user asks what to do today, combine the history and suggestions into a clear session plan.
"""

_SUMMARY_PROMPT = """\
You are a summarisation assistant. Given the following conversation from a single day, \
produce a concise summary (150-300 words) that captures:
1. Key topics discussed
2. Important decisions or preferences expressed by the user
3. Any tasks, goals, or follow-ups mentioned
4. The overall tone/mood of the conversation

Return ONLY the summary text, no headers or formatting.
"""

_TOOL_RESULT_SUMMARY_PROMPT = """\
You are summarising the results of tool executions for the user. Given the tool name, inputs, and outputs, write a natural, conversational response.

Rules:
- Do NOT include database IDs, technical status fields, or raw JSON.
- For workout history: present exercises with sets × reps @ weight, grouped by date.
- For PR checks: highlight the key records (max weight, estimated 1RM, total sessions).
- For progression suggestions: present as friendly coaching advice with reasoning.
- For PR alerts in log_workout: celebrate the achievement enthusiastically! 🎉
- For workout logging without PR: confirm what was logged in a friendly, concise way.
- For nutrition goal setting: confirm the targets set and encourage the user.
- For goal checking: present progress as a clear dashboard — consumed vs target, remaining, and percentage. Use visual cues (e.g. "72% of calories done ✅").
- For meal suggestions: present options conversationally with reasoning. If over budget, be empathetic but firm. If suggesting food, make it sound appetising.
- For nutrition reports: summarise daily averages, highlight best/worst days, and mention goal adherence percentage. Keep it motivating.
- Keep responses conversational and motivating.
"""


async def _default_summariser(messages: list[dict]) -> str:
    """Default LLM-based summariser for daily sessions."""
    import openai

    from src.tracing import wrap_openai_client

    api_key = os.environ.get("OPENROUTER_API_KEY") or os.environ.get("OPENAI_API_KEY")
    base_url = None
    model = os.environ.get("CHAT_MODEL", "gpt-5-mini")

    using_openrouter = bool(
        os.environ.get("OPENROUTER_API_KEY")
        or os.environ.get("OPENROUTER_BASE_URL")
        or (api_key and api_key.startswith("sk-or-v1-"))
    )
    if using_openrouter:
        base_url = os.environ.get("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
        model = os.environ.get("OPENROUTER_MODEL", model)

    client = wrap_openai_client(openai.AsyncOpenAI(api_key=api_key, base_url=base_url))

    # Build the conversation transcript for summarisation
    transcript = "\n".join(f"[{m['role']}]: {m['content']}" for m in messages)

    response = await client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": _SUMMARY_PROMPT},
            {"role": "user", "content": transcript},
        ],
        timeout=30.0,
    )
    content = response.choices[0].message.content
    if not content:
        raise LLMError("Summariser returned an empty response.")
    return content.strip()


class ChatClient:
    """Handles normal conversational replies without invoking tool planning.

    When a ``ChatSessionManager`` is provided, every message is persisted
    and past session summaries + today's history are injected into the
    LLM context window automatically.
    """

    def __init__(
        self,
        llm_client: LLMClient | None = None,
        model: str | None = None,
        session_manager=None,
    ) -> None:
        self._llm = llm_client or LLMClient(model=model or os.environ.get("CHAT_MODEL", "gpt-5-mini"))
        self._session_manager = session_manager

    @traceable_if_available("chat.complete")
    async def complete(self, message: str, user_id: str = "default_user") -> str:
        greeting = self._local_fast_path(message)
        if greeting is not None:
            # Still persist greetings if session manager is available
            if self._session_manager is not None:
                session = await self._session_manager.get_or_create_session(user_id)
                self._session_manager.add_message(session.id, "user", message)
                self._session_manager.add_message(session.id, "assistant", greeting)
            return greeting

        # Build LLM messages list
        llm_messages: list[dict] = [{"role": "system", "content": _CHAT_SYSTEM_PROMPT}]

        if self._session_manager is not None:
            # Inject cross-day summaries and today's conversation history
            context = await self._session_manager.build_context_messages(user_id)
            llm_messages.extend(context)

        # Add the current user message
        llm_messages.append({"role": "user", "content": message})

        try:
            reply = await self._llm.raw_chat(
                messages=llm_messages,
                timeout=20.0,
                model_override=os.environ.get("CHAT_MODEL"),
            )
        except Exception as exc:
            raise LLMError(str(exc)) from exc

        # Persist both messages
        if self._session_manager is not None:
            session = await self._session_manager.get_or_create_session(user_id)
            self._session_manager.add_message(session.id, "user", message)
            self._session_manager.add_message(session.id, "assistant", reply)

        return reply

    @staticmethod
    def _local_fast_path(message: str) -> str | None:
        normalized = message.strip().lower()
        if normalized in {"hi", "hello", "hey", "yo"}:
            return "Hello! I can chat, answer questions, and help with commands for workouts, meals, tasks, and system control."
        if normalized in {"thanks", "thank you"}:
            return "You're welcome."
        return None

    @traceable_if_available("chat.generate_tool_summary_response")
    async def generate_tool_summary_response(self, original_command: str, results: list[Any]) -> str:
        """Use the LLM to generate a natural conversational summary of tool execution results."""
        import json
        
        system_prompt = _TOOL_RESULT_SUMMARY_PROMPT
        
        # Strip internal/technical fields before sending to the LLM
        _INTERNAL_KEYS = {"id", "logged_at", "created_at", "updated_at", "user_id"}
        
        def _sanitize(obj: Any) -> Any:
            if isinstance(obj, dict):
                return {k: _sanitize(v) for k, v in obj.items() if k not in _INTERNAL_KEYS}
            if isinstance(obj, list):
                return [_sanitize(item) for item in obj]
            return obj
        
        sanitized_results = _sanitize(results)
        
        user_content = (
            f"Original command: {original_command}\n"
            f"Tool Execution Results:\n{json.dumps(sanitized_results, indent=2, default=str)}"
        )
        
        try:
            reply = await self._llm.raw_chat(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_content},
                ],
                timeout=20.0,
                model_override=os.environ.get("CHAT_MODEL"),
            )
            return reply.strip()
        except Exception:
            pass # Fall back below if LLM fails
            
        return "Task completed successfully."

    @traceable_if_available("chat.log_tool_interaction")
    async def log_tool_interaction(self, command: str, summary: str, user_id: str = "default_user") -> None:
        """Persist a tool command and its summary to the session history."""
        if self._session_manager is not None:
            session = await self._session_manager.get_or_create_session(user_id)
            self._session_manager.add_message(session.id, "user", command)
            self._session_manager.add_message(session.id, "assistant", summary)
