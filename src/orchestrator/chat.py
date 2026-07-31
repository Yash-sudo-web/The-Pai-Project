"""Conversation context and persistence for the orchestrator.

This module no longer owns a separate LLM round trip. The orchestrator makes
one tool-calling request that either answers directly or calls tools, so this
class exists to assemble the message list and persist each turn.
"""

from __future__ import annotations

import asyncio
import os
from typing import Any

from src.context import DEFAULT_USER_ID
from src.orchestrator.llm import (
    ASSISTANT_SYSTEM_PROMPT,
    LLMClient,
    LLMError,
    current_datetime_message,
)
from src.tracing import traceable_if_available

_SUMMARY_PROMPT = """\
You are a summarisation assistant. Given the following conversation from a single day, \
produce a concise summary (150-300 words) that captures:
1. Key topics discussed
2. Important decisions or preferences expressed by the user
3. Any tasks, goals, or follow-ups mentioned
4. The overall tone/mood of the conversation

Return ONLY the summary text, no headers or formatting.
"""

# Canned answers for inputs that need no model at all. Checked before any
# network call, so these cost zero tokens and return in single-digit ms.
_FAST_PATH_REPLIES: dict[str, str] = {
    "hi": "Hello! I can chat, answer questions, and help with commands for workouts, meals, tasks, and system control.",
    "hello": "Hello! I can chat, answer questions, and help with commands for workouts, meals, tasks, and system control.",
    "hey": "Hello! I can chat, answer questions, and help with commands for workouts, meals, tasks, and system control.",
    "yo": "Hello! I can chat, answer questions, and help with commands for workouts, meals, tasks, and system control.",
    "thanks": "You're welcome.",
    "thank you": "You're welcome.",
    "ty": "You're welcome.",
}


async def _default_summariser(messages: list[dict]) -> str:
    """Default LLM-based summariser for daily sessions."""
    client = LLMClient(model=os.environ.get("SUMMARY_MODEL", os.environ.get("CHAT_MODEL", "gpt-5-mini")))
    transcript = "\n".join(f"[{m['role']}]: {m['content']}" for m in messages)

    content = await client.raw_chat(
        messages=[
            {"role": "system", "content": _SUMMARY_PROMPT},
            {"role": "user", "content": transcript},
        ],
        timeout=30.0,
    )
    if not content:
        raise LLMError("Summariser returned an empty response.")
    return content.strip()


class ChatClient:
    """Assembles conversation context and persists turns.

    When a ``ChatSessionManager`` is provided, every turn is persisted and
    past session summaries + today's history are injected into the context
    window automatically.
    """

    def __init__(
        self,
        llm_client: LLMClient | None = None,
        model: str | None = None,
        session_manager=None,
    ) -> None:
        self._llm = llm_client or LLMClient(model=model or os.environ.get("CHAT_MODEL", "gpt-5-mini"))
        self._session_manager = session_manager

    @property
    def session_manager(self):
        return self._session_manager

    # ------------------------------------------------------------------
    # Context assembly
    # ------------------------------------------------------------------

    @staticmethod
    def local_fast_path(message: str) -> str | None:
        """Return a canned reply for trivial input, or ``None``."""
        return _FAST_PATH_REPLIES.get(message.strip().lower().rstrip("!."))

    async def build_messages(
        self, user_message: str, user_id: str = DEFAULT_USER_ID
    ) -> tuple[list[dict], str | None]:
        """Build the request messages and return them with today's session id.

        Ordering is deliberate: the static system prompt comes first so the
        provider can cache it, the volatile timestamp comes last.
        """
        messages: list[dict] = [{"role": "system", "content": ASSISTANT_SYSTEM_PROMPT}]
        session_id: str | None = None

        if self._session_manager is not None:
            session = await self._session_manager.get_or_create_session(user_id)
            session_id = session.id
            # Off the event loop: these are blocking SQLAlchemy round trips.
            messages.extend(
                await asyncio.to_thread(
                    self._session_manager.build_context_messages, user_id, session_id
                )
            )

        messages.append(current_datetime_message())
        messages.append({"role": "user", "content": user_message})
        return messages, session_id

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    @traceable_if_available("chat.persist_turn")
    async def persist_turn(
        self,
        user_message: str,
        assistant_message: str,
        user_id: str = DEFAULT_USER_ID,
        session_id: str | None = None,
    ) -> None:
        """Persist a user/assistant exchange in a single transaction."""
        if self._session_manager is None:
            return
        if session_id is None:
            session = await self._session_manager.get_or_create_session(user_id)
            session_id = session.id
        await asyncio.to_thread(
            self._session_manager.add_messages,
            session_id,
            [("user", user_message), ("assistant", assistant_message)],
        )

    # ------------------------------------------------------------------
    # Deprecated shims — kept so external callers do not break
    # ------------------------------------------------------------------

    async def log_tool_interaction(
        self,
        command: str,
        summary: str,
        user_id: str = DEFAULT_USER_ID,
        session_id: str | None = None,
    ) -> None:
        """Alias for :meth:`persist_turn`."""
        await self.persist_turn(command, summary, user_id=user_id, session_id=session_id)


def sanitize_tool_result(obj: Any) -> Any:
    """Strip internal identifiers and timestamps before showing results to the LLM."""
    internal_keys = {"id", "ids", "logged_at", "created_at", "updated_at", "user_id", "session_id"}

    if isinstance(obj, dict):
        return {k: sanitize_tool_result(v) for k, v in obj.items() if k not in internal_keys}
    if isinstance(obj, list):
        return [sanitize_tool_result(item) for item in obj]
    return obj
