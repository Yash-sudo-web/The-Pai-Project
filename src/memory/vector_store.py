"""Optional Chroma-backed vector store wrapper."""

from __future__ import annotations

import os
from typing import Any, Callable

from src.types import RetrievedRecord


class VectorStoreUnavailableError(RuntimeError):
    """Raised when vector search is enabled but the backend is unavailable."""


class VectorStore:
    """A thin wrapper around ChromaDB with injectable embeddings."""

    def __init__(
        self,
        collection: Any | None = None,
        embedding_fn: Callable[[str], list[float]] | None = None,
    ) -> None:
        self._embedding_fn = embedding_fn or self._default_embed
        if collection is not None:
            self._collection = collection
            return

        try:
            import chromadb  # type: ignore
        except ImportError as exc:  # pragma: no cover - optional dependency path
            raise VectorStoreUnavailableError("chromadb is not installed") from exc

        client = chromadb.Client()
        self._collection = client.get_or_create_collection(name="personal_ai_notes")

    async def store(self, note_id: str, text: str, metadata: dict[str, Any]) -> None:
        embedding = self._embedding_fn(text)
        self._collection.upsert(
            ids=[note_id],
            documents=[text],
            metadatas=[metadata],
            embeddings=[embedding],
        )

    async def query(self, query: str, top_k: int = 5) -> list[RetrievedRecord]:
        embedding = self._embedding_fn(query)
        results = self._collection.query(
            query_embeddings=[embedding],
            n_results=top_k,
        )
        documents = results.get("documents", [[]])[0]
        metadatas = results.get("metadatas", [[]])[0]
        distances = results.get("distances", [[]])[0]
        records: list[RetrievedRecord] = []
        for document, metadata, distance in zip(documents, metadatas, distances):
            records.append(
                RetrievedRecord(
                    text=document,
                    metadata=metadata or {},
                    score=max(0.0, 1.0 - float(distance)),
                )
            )
        return records

    @staticmethod
    def _default_embed(text: str) -> list[float]:
        try:
            from openai import OpenAI
        except ImportError as exc:  # pragma: no cover - dependency guard
            raise VectorStoreUnavailableError("openai package is unavailable") from exc

        client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
        response = client.embeddings.create(
            model=os.environ.get("OPENAI_EMBEDDING_MODEL", "text-embedding-3-small"),
            input=text,
        )
        return list(response.data[0].embedding)
