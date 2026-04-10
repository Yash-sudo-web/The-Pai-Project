"""Retrieval layer backed by the notes table with optional vector fallback."""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import or_
from sqlalchemy.orm import Session

from src.memory.db import Note, SessionLocal
from src.types import RetrievedRecord


def _encode_metadata(metadata: dict[str, Any]) -> bytes | None:
    if not metadata:
        return None
    return json.dumps(metadata).encode("utf-8")


def _decode_metadata(payload: bytes | None) -> dict[str, Any]:
    if not payload:
        return {}
    try:
        raw = payload.decode("utf-8")
        data = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


class RetrievalLayer:
    """Stores notes and retrieves them via keyword or vector search."""

    def __init__(
        self,
        session_factory: type[SessionLocal] = SessionLocal,
        vector_store: Any | None = None,
        vector_db_enabled: bool | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._vector_store = vector_store
        env_enabled = os.environ.get("VECTOR_DB_ENABLED", "").lower() == "true"
        self._vector_db_enabled = env_enabled if vector_db_enabled is None else vector_db_enabled

    async def store(self, text: str, metadata: dict[str, Any] | None = None) -> str:
        """Persist a note and optionally mirror it into the vector store."""
        note_id = str(uuid.uuid4())
        note = Note(
            id=note_id,
            user_id="default_user",
            content=text,
            embedding=_encode_metadata(metadata or {}),
            created_at=datetime.now(tz=timezone.utc),
        )

        with self._session_factory() as session:
            session.add(note)
            session.commit()

        if self._vector_db_enabled and self._vector_store is not None:
            await self._vector_store.store(note_id=note_id, text=text, metadata=metadata or {})

        return note_id

    async def query(self, query: str, top_k: int = 5) -> list[RetrievedRecord]:
        """Return the most relevant notes for the query."""
        if self._vector_db_enabled and self._vector_store is not None:
            return await self._vector_store.query(query=query, top_k=top_k)
        return self._keyword_query(query=query, top_k=top_k)

    def _keyword_query(self, query: str, top_k: int) -> list[RetrievedRecord]:
        terms = [term.strip() for term in query.lower().split() if term.strip()]
        with self._session_factory() as session:
            statement = session.query(Note)
            if terms:
                filters = [Note.content.ilike(f"%{term}%") for term in terms]
                statement = statement.filter(or_(*filters))

            notes = statement.order_by(Note.created_at.desc()).limit(max(top_k * 3, top_k)).all()

        scored: list[RetrievedRecord] = []
        for note in notes:
            score = self._keyword_score(note.content, terms)
            if not terms:
                score = 1.0
            if score <= 0 and terms:
                continue
            scored.append(
                RetrievedRecord(
                    text=note.content,
                    score=score,
                    metadata=_decode_metadata(note.embedding),
                )
            )

        scored.sort(key=lambda item: item.score, reverse=True)
        return scored[:top_k]

    @staticmethod
    def _keyword_score(content: str, terms: list[str]) -> float:
        if not terms:
            return 0.0
        haystack = content.lower()
        matches = sum(haystack.count(term) for term in terms)
        return matches / len(terms)
