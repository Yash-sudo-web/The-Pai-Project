"""Chat session manager — daily session lifecycle, message persistence, and summarization.

Each user gets exactly one session per calendar day (IST — UTC+5:30).
When a new day starts, the previous session is automatically closed and
summarised by the LLM.  Recent session summaries are injected as context
into new conversations so the AI remembers cross-day interactions.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Iterable, Optional, Sequence

from src.context import DEFAULT_USER_ID, LOCAL_TZ
from src.memory.db import ChatMessage, ChatSession, SessionLocal

logger = logging.getLogger(__name__)

# Kept as a module-level alias for existing callers.
IST = LOCAL_TZ

# Maximum number of today's messages to include in the LLM context window
DEFAULT_CONTEXT_MESSAGE_LIMIT = 50

# Number of past daily summaries to inject as context
DEFAULT_SUMMARY_DAYS = 7

# Context is capped by size, not just message count: a busy day used to push
# several thousand tokens of history into every single turn.
DEFAULT_HISTORY_TOKEN_BUDGET = 1500
DEFAULT_SUMMARY_TOKEN_BUDGET = 800

# Rough tokens-per-character ratio for English prose. Good enough for
# budgeting; the cost of being slightly off is a few hundred tokens.
_CHARS_PER_TOKEN = 4


def _estimate_tokens(text: str) -> int:
    return max(1, len(text) // _CHARS_PER_TOKEN)


def _today_ist() -> date:
    """Return the current date in IST as a date object."""
    return datetime.now(IST).date()


@dataclass(frozen=True)
class SessionInfo:
    """Plain snapshot of a chat session — safe to use outside a DB session."""

    id: str
    user_id: str
    session_date: date
    status: str
    summary: str | None
    message_count: int


def _to_info(session: ChatSession) -> SessionInfo:
    return SessionInfo(
        id=session.id,
        user_id=session.user_id,
        session_date=session.session_date,
        status=session.status,
        summary=session.summary,
        message_count=session.message_count or 0,
    )


class ChatSessionManager:
    """Manages daily chat sessions, message persistence, and LLM summarisation.

    Parameters
    ----------
    session_factory:
        SQLAlchemy session factory (default: ``SessionLocal``).
    llm_summariser:
        An async callable ``(messages: list[dict]) -> str`` that produces a
        summary from a list of ``{"role": ..., "content": ...}`` dicts.
        If ``None``, summarisation is skipped.
    context_message_limit:
        Max messages from today's session to include in the context window.
    summary_days:
        Number of past daily summaries to inject as cross-day context.
    history_token_budget:
        Approximate token ceiling for injected same-day history.
    summary_token_budget:
        Approximate token ceiling for injected cross-day summaries.
    """

    def __init__(
        self,
        session_factory=SessionLocal,
        llm_summariser=None,
        context_message_limit: int = DEFAULT_CONTEXT_MESSAGE_LIMIT,
        summary_days: int = DEFAULT_SUMMARY_DAYS,
        history_token_budget: int = DEFAULT_HISTORY_TOKEN_BUDGET,
        summary_token_budget: int = DEFAULT_SUMMARY_TOKEN_BUDGET,
    ) -> None:
        self._session_factory = session_factory
        self._llm_summariser = llm_summariser
        self._context_message_limit = context_message_limit
        self._summary_days = summary_days
        self._history_token_budget = history_token_budget
        self._summary_token_budget = summary_token_budget

    # ------------------------------------------------------------------
    # Session lifecycle
    # ------------------------------------------------------------------

    async def get_or_create_session(self, user_id: str = DEFAULT_USER_ID) -> SessionInfo:
        """Return today's active session, creating it if necessary.

        If the most recent session belongs to a previous day, it is
        closed and summarised before the new session is created.

        Every query runs off the event loop: SQLAlchemy's sync API would
        otherwise stall the whole server (and any in-flight SSE stream) for
        the duration of each remote round trip.
        """
        existing = await asyncio.to_thread(self._fetch_today_session, user_id)
        if existing is not None:
            return existing

        # New day: close and summarise anything still open before starting.
        stale_ids = await asyncio.to_thread(self._fetch_stale_session_ids, user_id)
        for session_id in stale_ids:
            await self._close_session(session_id)

        return await asyncio.to_thread(self._create_today_session, user_id)

    def _fetch_today_session(self, user_id: str) -> SessionInfo | None:
        today = _today_ist()
        with self._session_factory() as db:
            session = (
                db.query(ChatSession)
                .filter(ChatSession.user_id == user_id, ChatSession.session_date == today)
                .first()
            )
            if session is None:
                return None
            # Reactivate if somehow closed mid-day
            if session.status == "closed":
                session.status = "active"
                session.closed_at = None
                db.commit()
                db.refresh(session)
            return _to_info(session)

    def _fetch_stale_session_ids(self, user_id: str) -> list[str]:
        today = _today_ist()
        with self._session_factory() as db:
            return [
                row.id
                for row in db.query(ChatSession)
                .filter(
                    ChatSession.user_id == user_id,
                    ChatSession.status == "active",
                    ChatSession.session_date < today,
                )
                .all()
            ]

    def _create_today_session(self, user_id: str) -> SessionInfo:
        with self._session_factory() as db:
            new_session = ChatSession(
                id=str(uuid.uuid4()),
                user_id=user_id,
                session_date=_today_ist(),
                status="active",
                message_count=0,
                created_at=datetime.now(IST),
            )
            db.add(new_session)
            db.commit()
            db.refresh(new_session)
            return _to_info(new_session)

    def get_session(self, session_id: str, user_id: str | None = None) -> SessionInfo | None:
        """Return a session by id, optionally asserting it belongs to *user_id*."""
        with self._session_factory() as db:
            query = db.query(ChatSession).filter(ChatSession.id == session_id)
            if user_id is not None:
                query = query.filter(ChatSession.user_id == user_id)
            session = query.first()
            return _to_info(session) if session is not None else None

    async def _close_session(self, session_id: str) -> None:
        """Close a session and generate its summary."""
        transcript = await asyncio.to_thread(self._fetch_transcript, session_id)

        summary: str | None = None
        if self._llm_summariser is not None and transcript:
            try:
                summary = await self._llm_summariser(transcript)
            except Exception:
                logger.exception("Failed to summarise session %s", session_id)

        await asyncio.to_thread(self._mark_closed, session_id, summary)

    def _fetch_transcript(self, session_id: str) -> list[dict]:
        with self._session_factory() as db:
            messages = (
                db.query(ChatMessage)
                .filter(ChatMessage.session_id == session_id)
                .order_by(ChatMessage.created_at.asc())
                .all()
            )
            return [{"role": m.role, "content": m.content} for m in messages]

    def _mark_closed(self, session_id: str, summary: str | None) -> None:
        with self._session_factory() as db:
            session = db.query(ChatSession).filter(ChatSession.id == session_id).first()
            if session is None:
                return
            session.status = "closed"
            session.closed_at = datetime.now(IST)
            if summary:
                session.summary = summary
                logger.info(
                    "Generated summary for session %s (date=%s): %d chars",
                    session_id,
                    session.session_date,
                    len(summary),
                )
            db.commit()

    # ------------------------------------------------------------------
    # Message persistence
    # ------------------------------------------------------------------

    def add_message(self, session_id: str, role: str, content: str) -> None:
        """Persist a single message. Prefer :meth:`add_messages` for a turn."""
        self.add_messages(session_id, [(role, content)])

    def add_messages(self, session_id: str, turns: Sequence[tuple[str, str]]) -> None:
        """Persist several messages and bump ``message_count`` in one transaction.

        A user/assistant turn used to cost two separate SELECT + UPDATE +
        INSERT + COMMIT cycles against a remote database; this collapses them
        into a single round trip.
        """
        if not turns:
            return

        now = datetime.now(IST)
        with self._session_factory() as db:
            db.add_all(
                ChatMessage(
                    id=str(uuid.uuid4()),
                    session_id=session_id,
                    role=role,
                    content=content,
                    # Preserve ordering when several rows land in the same tick.
                    created_at=now + timedelta(microseconds=index),
                )
                for index, (role, content) in enumerate(turns)
            )

            session = db.query(ChatSession).filter(ChatSession.id == session_id).first()
            if session is not None:
                session.message_count = (session.message_count or 0) + len(turns)

            db.commit()

    # ------------------------------------------------------------------
    # Retrieval
    # ------------------------------------------------------------------

    def get_messages(
        self, session_id: str, limit: Optional[int] = None
    ) -> list[dict]:
        """Return messages for a session as dicts, most recent last.

        Parameters
        ----------
        session_id:
            The session to retrieve messages for.
        limit:
            Max number of most-recent messages to return.  Defaults to
            ``self._context_message_limit``.
        """
        limit = limit or self._context_message_limit

        with self._session_factory() as db:
            messages = (
                db.query(ChatMessage)
                .filter(ChatMessage.session_id == session_id)
                .order_by(ChatMessage.created_at.desc())
                .limit(limit)
                .all()
            )
            # Reverse so oldest-first for LLM context
            messages.reverse()
            return [
                {
                    "id": m.id,
                    "role": m.role,
                    "content": m.content,
                    "created_at": m.created_at.isoformat() if m.created_at else None,
                }
                for m in messages
            ]

    def get_recent_summaries(
        self, user_id: str = DEFAULT_USER_ID, days: Optional[int] = None
    ) -> list[dict]:
        """Return summaries from the last N days (excluding today).

        Returns a list of ``{"date": "YYYY-MM-DD", "summary": "..."}`` dicts,
        oldest first.
        """
        days = days or self._summary_days
        today = _today_ist()
        cutoff = today - timedelta(days=days)

        with self._session_factory() as db:
            sessions = (
                db.query(ChatSession)
                .filter(
                    ChatSession.user_id == user_id,
                    ChatSession.session_date >= cutoff,
                    ChatSession.session_date < today,
                    ChatSession.summary.isnot(None),
                )
                .order_by(ChatSession.session_date.asc())
                .all()
            )
            return [
                {
                    "date": str(s.session_date),
                    "summary": s.summary,
                }
                for s in sessions
            ]

    def get_sessions_list(
        self, user_id: str = DEFAULT_USER_ID, limit: int = 30
    ) -> list[dict]:
        """Return a list of past sessions with metadata."""
        with self._session_factory() as db:
            sessions = (
                db.query(ChatSession)
                .filter(ChatSession.user_id == user_id)
                .order_by(ChatSession.session_date.desc())
                .limit(limit)
                .all()
            )
            return [
                {
                    "id": s.id,
                    "session_date": str(s.session_date),
                    "status": s.status,
                    "summary": s.summary,
                    "message_count": s.message_count,
                    "created_at": s.created_at.isoformat() if s.created_at else None,
                    "closed_at": s.closed_at.isoformat() if s.closed_at else None,
                }
                for s in sessions
            ]

    # ------------------------------------------------------------------
    # Summarisation
    # ------------------------------------------------------------------

    async def summarize_session(self, session_id: str) -> Optional[str]:
        """Manually trigger summarisation for a specific session."""
        if self._llm_summariser is None:
            logger.warning("No LLM summariser configured; skipping.")
            return None

        with self._session_factory() as db:
            session = db.query(ChatSession).filter(ChatSession.id == session_id).first()
            if session is None:
                logger.warning("Session %s not found", session_id)
                return None

            messages = (
                db.query(ChatMessage)
                .filter(ChatMessage.session_id == session_id)
                .order_by(ChatMessage.created_at.asc())
                .all()
            )
            if not messages:
                return None

            msg_dicts = [{"role": m.role, "content": m.content} for m in messages]
            summary = await self._llm_summariser(msg_dicts)
            session.summary = summary
            db.commit()
            return summary

    # ------------------------------------------------------------------
    # Context builder (for LLM injection)
    # ------------------------------------------------------------------

    def build_context_messages(
        self, user_id: str = DEFAULT_USER_ID, session_id: str | None = None
    ) -> list[dict]:
        """Build the context messages to inject before a new user message.

        Both sections are trimmed newest-first to a token budget, so an
        all-day conversation cannot grow the per-turn prompt without bound.

        Parameters
        ----------
        user_id:
            Owner of the conversation.
        session_id:
            Today's session id. Callers that already resolved it should pass
            it in; re-resolving costs an extra database round trip.
        """
        context: list[dict] = []

        summary_text = self._trim_summaries(self.get_recent_summaries(user_id))
        if summary_text:
            context.append(
                {
                    "role": "system",
                    "content": (
                        "Summary of recent conversations with the user from past days. "
                        "Use for context; do not repeat unless asked:\n\n" + summary_text
                    ),
                }
            )

        if session_id is not None:
            context.extend(self._trim_history(self.get_messages(session_id)))

        return context

    def _trim_summaries(self, summaries: Iterable[dict]) -> str:
        """Join summaries newest-first until the token budget is spent."""
        kept: list[str] = []
        budget = self._summary_token_budget
        for item in reversed(list(summaries)):
            line = f"• {item['date']}: {item['summary']}"
            cost = _estimate_tokens(line)
            if cost > budget:
                break
            budget -= cost
            kept.append(line)
        kept.reverse()
        return "\n".join(kept)

    def _trim_history(self, messages: Sequence[dict]) -> list[dict]:
        """Keep the most recent messages that fit the history token budget.

        Trimming stops at the first message that does not fit rather than
        skipping it, so the retained history stays contiguous.
        """
        kept: list[dict] = []
        budget = self._history_token_budget
        for message in reversed(messages):
            content = message.get("content") or ""
            cost = _estimate_tokens(content)
            if cost > budget:
                break
            budget -= cost
            kept.append({"role": message["role"], "content": content})

        if not kept and messages:
            # A single oversized message must not wipe the whole window —
            # keep the latest turn, truncated to the budget.
            latest = messages[-1]
            limit = self._history_token_budget * _CHARS_PER_TOKEN
            kept.append(
                {
                    "role": latest["role"],
                    "content": (latest.get("content") or "")[:limit] + " …[truncated]",
                }
            )

        kept.reverse()
        return kept
