"""Chat session manager — daily session lifecycle, message persistence, and summarization.

Each user gets exactly one session per calendar day (IST — UTC+5:30).
When a new day starts, the previous session is automatically closed and
summarised by the LLM.  Recent session summaries are injected as context
into new conversations so the AI remembers cross-day interactions.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy.orm import Session

from src.memory.db import ChatMessage, ChatSession, SessionLocal

logger = logging.getLogger(__name__)

# IST = UTC + 5:30
IST = timezone(timedelta(hours=5, minutes=30))

# Maximum number of today's messages to include in the LLM context window
DEFAULT_CONTEXT_MESSAGE_LIMIT = 50

# Number of past daily summaries to inject as context
DEFAULT_SUMMARY_DAYS = 7


def _today_ist() -> datetime:
    """Return the current date in IST as a date object."""
    return datetime.now(IST).date()


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
    """

    def __init__(
        self,
        session_factory=SessionLocal,
        llm_summariser=None,
        context_message_limit: int = DEFAULT_CONTEXT_MESSAGE_LIMIT,
        summary_days: int = DEFAULT_SUMMARY_DAYS,
    ) -> None:
        self._session_factory = session_factory
        self._llm_summariser = llm_summariser
        self._context_message_limit = context_message_limit
        self._summary_days = summary_days

    # ------------------------------------------------------------------
    # Session lifecycle
    # ------------------------------------------------------------------

    async def get_or_create_session(self, user_id: str = "default_user") -> ChatSession:
        """Return today's active session, creating it if necessary.

        If the most recent session belongs to a previous day, it is
        closed and summarised before the new session is created.
        """
        today = _today_ist()

        with self._session_factory() as db:
            # Look for today's session first
            session = (
                db.query(ChatSession)
                .filter(ChatSession.user_id == user_id, ChatSession.session_date == today)
                .first()
            )
            if session is not None:
                # Reactivate if somehow closed mid-day
                if session.status == "closed":
                    session.status = "active"
                    session.closed_at = None
                    db.commit()
                    db.refresh(session)
                return self._detach(session)

            # Close any previous active sessions for this user
            previous_sessions = (
                db.query(ChatSession)
                .filter(
                    ChatSession.user_id == user_id,
                    ChatSession.status == "active",
                    ChatSession.session_date < today,
                )
                .all()
            )
            for prev in previous_sessions:
                await self._close_session(db, prev)

            # Create today's session
            new_session = ChatSession(
                id=str(uuid.uuid4()),
                user_id=user_id,
                session_date=today,
                status="active",
                message_count=0,
                created_at=datetime.now(IST),
            )
            db.add(new_session)
            db.commit()
            db.refresh(new_session)
            return self._detach(new_session)

    async def _close_session(self, db: Session, session: ChatSession) -> None:
        """Close a session and generate its summary."""
        session.status = "closed"
        session.closed_at = datetime.now(IST)

        # Generate summary if we have an LLM summariser and messages
        if self._llm_summariser is not None and session.message_count > 0:
            try:
                messages = (
                    db.query(ChatMessage)
                    .filter(ChatMessage.session_id == session.id)
                    .order_by(ChatMessage.created_at.asc())
                    .all()
                )
                msg_dicts = [{"role": m.role, "content": m.content} for m in messages]
                summary = await self._llm_summariser(msg_dicts)
                session.summary = summary
                logger.info(
                    "Generated summary for session %s (date=%s): %d chars",
                    session.id,
                    session.session_date,
                    len(summary),
                )
            except Exception:
                logger.exception("Failed to summarise session %s", session.id)

        db.commit()

    # ------------------------------------------------------------------
    # Message persistence
    # ------------------------------------------------------------------

    def add_message(
        self, session_id: str, role: str, content: str
    ) -> ChatMessage:
        """Persist a message and increment the session's message_count."""
        with self._session_factory() as db:
            message = ChatMessage(
                id=str(uuid.uuid4()),
                session_id=session_id,
                role=role,
                content=content,
                created_at=datetime.now(IST),
            )
            db.add(message)

            # Increment message count
            session = db.query(ChatSession).filter(ChatSession.id == session_id).first()
            if session is not None:
                session.message_count = (session.message_count or 0) + 1

            db.commit()
            db.refresh(message)
            return self._detach(message)

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
            query = (
                db.query(ChatMessage)
                .filter(ChatMessage.session_id == session_id)
                .order_by(ChatMessage.created_at.desc())
                .limit(limit)
            )
            messages = query.all()
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
        self, user_id: str = "default_user", days: Optional[int] = None
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
        self, user_id: str = "default_user", limit: int = 30
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

    async def build_context_messages(
        self, user_id: str = "default_user"
    ) -> list[dict]:
        """Build the context messages to inject before a new user message.

        Returns a list of ``{"role": "system", "content": "..."}`` dicts
        containing:
        1. Summaries of recent past sessions.
        2. Today's conversation history so far.
        """
        context: list[dict] = []

        # 1. Past session summaries
        summaries = self.get_recent_summaries(user_id)
        if summaries:
            summary_text = "\n".join(
                f"• {s['date']}: {s['summary']}" for s in summaries
            )
            context.append({
                "role": "system",
                "content": (
                    "Here is a summary of your recent conversations with the user "
                    "from past days. Use this for context but do not repeat it "
                    "unless asked:\n\n" + summary_text
                ),
            })

        # 2. Today's messages
        session = await self.get_or_create_session(user_id)
        today_messages = self.get_messages(session.id)
        for msg in today_messages:
            context.append({"role": msg["role"], "content": msg["content"]})

        return context

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _detach(obj):
        """Expunge an ORM object from its session so it can be used outside."""
        from sqlalchemy.orm import make_transient

        try:
            session = obj._sa_instance_state.session
            if session is not None:
                session.expunge(obj)
            make_transient(obj)
        except Exception:
            pass
        return obj
