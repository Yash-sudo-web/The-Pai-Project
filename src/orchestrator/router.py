"""Message router for deciding between chat and tool orchestration."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal


RouteKind = Literal["chat", "tool_action", "clarify"]


@dataclass
class RouteDecision:
    kind: RouteKind
    reason: str


import json
import os

from src.orchestrator.llm import LLMClient

class MessageRouter:
    """Routes incoming messages to either direct chat or tool execution using an LLM."""

    def __init__(self, llm_client: LLMClient | None = None) -> None:
        self._llm = llm_client or LLMClient()
        self._system_prompt = (
            "You are the Routing Agent for a personal AI assistant. "
            "Your job is to analyze the user's input and decide the execution path.\n\n"
            "Return EXACTLY a JSON object matching this schema:\n"
            '{"kind": "<chat | tool_action | clarify>", "reason": "<brief explanation>"}\n\n'
            "Paths:\n"
            "1. 'chat': For greetings, conversational remarks, or general questions that DO NOT require personal data, "
            "taking action, or looking up state (e.g., 'hi', 'how are you', 'thank you', 'how does python work').\n"
            "2. 'tool_action': For ANY request that implies acting on or retrieving the user's personal data "
            "(e.g., logging meals, checking calories, tracking workouts, asking for summaries, managing tasks, "
            "asking for today's date/time, or controlling the device). Even if phrased as a conversational question "
            "(e.g., 'what did I eat?'), if it touches system state or personal tracking, route to tool_action.\n"
            "3. 'clarify': If the input is completely empty or hopelessly ambiguous.\n\n"
            "Do NOT include markdown fences, just returning JSON."
        )

    async def route(self, message: str) -> RouteDecision:
        text = message.strip()
        if not text:
            return RouteDecision(kind="clarify", reason="empty_message")

        try:
            content = await self._llm.raw_chat(
                messages=[
                    {"role": "system", "content": self._system_prompt},
                    {"role": "user", "content": text},
                ],
                temperature=0.0,
                timeout=15.0,
                model_override=os.environ.get("ROUTER_MODEL"),
            )
            
            if content:
                # Basic cleanup if the LLM hallucinated markdown code blocks
                if content.startswith("```json"):
                    content = content[7:]
                if content.endswith("```"):
                    content = content[:-3]
                
                data = json.loads(content.strip())
                kind = data.get("kind", "tool_action")
                reason = data.get("reason", "llm_routing")
                
                # Enforce valid kinds
                if kind not in ("chat", "tool_action", "clarify"):
                    kind = "tool_action"
                    
                return RouteDecision(kind=kind, reason=reason)
                
        except Exception:
            pass
            
        # Safe fallback if API fails
        return RouteDecision(kind="tool_action", reason="llm_fallback")
