"""Daily session lifecycle, batched writes, and the context token budget."""

from __future__ import annotations

import datetime
import uuid

import pytest

from src.memory.chat_sessions import IST, ChatSessionManager
from src.memory.db import ChatMessage, ChatSession, SessionLocal


@pytest.fixture
def manager():
    async def summariser(messages):
        return f"Summary of {len(messages)} messages."

    return ChatSessionManager(llm_summariser=summariser)


def _seed_session(user_id: str, day_offset: int, messages: list[tuple[str, str]]):
    """Insert a session on a past day with the given messages."""
    session_id = str(uuid.uuid4())
    day = datetime.datetime.now(IST).date() + datetime.timedelta(days=day_offset)
    with SessionLocal() as db:
        db.add(
            ChatSession(
                id=session_id,
                user_id=user_id,
                session_date=day,
                status="active",
                message_count=len(messages),
                created_at=datetime.datetime.now(IST),
            )
        )
        db.add_all(
            ChatMessage(
                id=str(uuid.uuid4()),
                session_id=session_id,
                role=role,
                content=content,
                created_at=datetime.datetime.now(IST) + datetime.timedelta(microseconds=i),
            )
            for i, (role, content) in enumerate(messages)
        )
        db.commit()
    return session_id


class TestSessionLifecycle:
    async def test_creates_one_session_per_day(self, manager):
        first = await manager.get_or_create_session("u1")
        second = await manager.get_or_create_session("u1")
        assert first.id == second.id
        assert first.session_date == datetime.datetime.now(IST).date()

    async def test_sessions_are_per_user(self, manager):
        a = await manager.get_or_create_session("u1")
        b = await manager.get_or_create_session("u2")
        assert a.id != b.id

    async def test_previous_day_is_closed_and_summarised(self, manager):
        old_id = _seed_session("u1", -1, [("user", "did squats"), ("assistant", "nice")])

        new_session = await manager.get_or_create_session("u1")

        assert new_session.id != old_id
        with SessionLocal() as db:
            old = db.query(ChatSession).filter(ChatSession.id == old_id).first()
        assert old.status == "closed"
        assert old.closed_at is not None
        assert old.summary == "Summary of 2 messages."

    async def test_summariser_failure_still_closes_the_session(self):
        async def broken(messages):
            raise RuntimeError("LLM down")

        manager = ChatSessionManager(llm_summariser=broken)
        old_id = _seed_session("u1", -1, [("user", "hi")])

        await manager.get_or_create_session("u1")

        with SessionLocal() as db:
            old = db.query(ChatSession).filter(ChatSession.id == old_id).first()
        assert old.status == "closed" and old.summary is None

    async def test_returns_a_detached_snapshot_not_an_orm_row(self, manager):
        session = await manager.get_or_create_session("u1")
        # A frozen dataclass — no lazy loads, no make_transient trickery.
        assert not hasattr(session, "_sa_instance_state")
        with pytest.raises(Exception):
            session.id = "mutated"  # type: ignore[misc]


class TestMessagePersistence:
    async def test_turn_is_written_in_one_batch_and_ordered(self, manager):
        session = await manager.get_or_create_session("u1")
        manager.add_messages(session.id, [("user", "hello"), ("assistant", "hi there")])

        messages = manager.get_messages(session.id)
        assert [m["role"] for m in messages] == ["user", "assistant"]
        assert [m["content"] for m in messages] == ["hello", "hi there"]

        with SessionLocal() as db:
            row = db.query(ChatSession).filter(ChatSession.id == session.id).first()
        assert row.message_count == 2

    async def test_empty_batch_is_a_no_op(self, manager):
        session = await manager.get_or_create_session("u1")
        manager.add_messages(session.id, [])
        assert manager.get_messages(session.id) == []

    async def test_get_messages_returns_the_newest_within_the_limit(self, manager):
        session = await manager.get_or_create_session("u1")
        manager.add_messages(session.id, [("user", f"m{i}") for i in range(10)])

        recent = manager.get_messages(session.id, limit=3)
        assert [m["content"] for m in recent] == ["m7", "m8", "m9"]


class TestOwnership:
    async def test_get_session_scopes_by_user(self, manager):
        session = await manager.get_or_create_session("u1")
        assert manager.get_session(session.id, "u1") is not None
        assert manager.get_session(session.id, "u2") is None
        assert manager.get_session("no-such-id", "u1") is None


class TestContextBudget:
    async def test_history_is_capped_by_tokens(self, manager):
        session = await manager.get_or_create_session("u1")
        manager.add_messages(session.id, [("user", "x" * 400) for _ in range(20)])

        manager._history_token_budget = 100  # ~400 chars, so ~1 message fits
        context = manager.build_context_messages("u1", session_id=session.id)
        history = [m for m in context if m["role"] != "system"]

        assert 0 < len(history) < 20

    async def test_a_single_oversized_message_is_truncated_not_dropped(self, manager):
        session = await manager.get_or_create_session("u1")
        manager.add_messages(session.id, [("user", "y" * 40_000)])

        manager._history_token_budget = 50
        context = manager.build_context_messages("u1", session_id=session.id)
        history = [m for m in context if m["role"] != "system"]

        assert len(history) == 1, "the latest turn must survive"
        assert history[0]["content"].endswith("[truncated]")
        assert len(history[0]["content"]) < 1000

    async def test_summaries_are_injected_as_a_system_message(self, manager):
        _seed_session("u1", -1, [("user", "hi")])
        await manager.get_or_create_session("u1")  # closes and summarises yesterday

        session = await manager.get_or_create_session("u1")
        context = manager.build_context_messages("u1", session_id=session.id)

        assert context and context[0]["role"] == "system"
        assert "Summary of" in context[0]["content"]

    async def test_no_summaries_means_no_system_message(self, manager):
        session = await manager.get_or_create_session("u1")
        context = manager.build_context_messages("u1", session_id=session.id)
        assert [m for m in context if m["role"] == "system"] == []
