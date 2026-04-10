"""Direct conversational response path for non-tool requests."""

from __future__ import annotations

import os

from src.orchestrator.llm import LLMClient, LLMError
from src.tracing import traceable_if_available


_CHAT_SYSTEM_PROMPT = """\
You are a helpful personal AI assistant for one user.

Rules:
- Answer naturally and concisely.
- Do not invent tool executions or claim an action was performed when it was not.
- If the user is chatting, greeting you, asking for help, or asking a general question, answer directly.
- If the user appears to want the system to perform an action, briefly explain that you can help and ask them to phrase it as a command if needed.
"""


class ChatClient:
    """Handles normal conversational replies without invoking tool planning."""

    def __init__(
        self,
        llm_client: LLMClient | None = None,
        model: str | None = None,
    ) -> None:
        self._llm = llm_client or LLMClient(model=model or os.environ.get("CHAT_MODEL", "gpt-5-mini"))

    @traceable_if_available("chat.complete")
    async def complete(self, message: str) -> str:
        greeting = self._local_fast_path(message)
        if greeting is not None:
            return greeting

        try:
            response = await self._llm._client.chat.completions.create(
                model=os.environ.get("CHAT_MODEL", self._llm._model),
                messages=[
                    {"role": "system", "content": _CHAT_SYSTEM_PROMPT},
                    {"role": "user", "content": message},
                ],
                timeout=20.0,
            )
        except Exception as exc:
            raise LLMError(str(exc)) from exc

        content = response.choices[0].message.content
        if content is None:
            raise LLMError("Chat model returned an empty response.")
        return content.strip()

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
        
        system_prompt = (
            "You are a helpful personal AI assistant. "
            "You just successfully executed a series of tools on the user's behalf based on their command. "
            "Given their original command and the raw JSON output from the tools, provide a brief, friendly, "
            "natural language response summarizing what was done. "
            "Do NOT include the raw JSON headers or format. "
            "Keep it concise and user-centric."
        )
        
        user_content = (
            f"Original command: {original_command}\n"
            f"Tool Execution Results:\n{json.dumps(results, indent=2, default=str)}"
        )
        
        try:
            response = await self._llm._client.chat.completions.create(
                model=os.environ.get("CHAT_MODEL", self._llm._model),
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_content},
                ],
                timeout=20.0,
            )
            content = response.choices[0].message.content
            if content:
                return content.strip()
        except Exception:
            pass # Fall back below if LLM fails
            
        return "Task completed successfully."
