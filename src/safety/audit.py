"""Audit log implementation for the Personal AI Assistant.

Provides append-only recording of every tool invocation and queryable
read access. No update or delete operations are exposed.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Callable

from sqlalchemy.orm import Session

from src.memory.db import AuditLog as AuditLogORM
from src.types import AuditEntry


class AuditLog:
    """Append-only audit log backed by the ``audit_log`` DB table.

    Only ``record()`` and ``query()`` are exposed — no update or delete.
    """

    def __init__(self, session_factory: Callable[[], Session]) -> None:
        self._session_factory = session_factory

    def record(self, entry: AuditEntry) -> None:
        """Insert a new audit entry and confirm the write before returning."""
        row = AuditLogORM(
            id=entry.id,
            timestamp=entry.timestamp,
            tool_name=entry.tool_name,
            domain=entry.domain.value,
            inputs=json.dumps(entry.inputs),
            output=json.dumps(entry.output) if entry.output is not None else None,
            error=entry.error,
            approval_status=entry.approval_status,
            session_id=entry.session_id,
        )
        with self._session_factory() as session:
            session.add(row)
            session.flush()   # confirm write within transaction
            session.commit()

    def query(
        self,
        time_range: tuple[datetime, datetime] | None = None,
        tool_name: str | None = None,
        domain: str | None = None,
    ) -> list[AuditEntry]:
        """Return audit entries matching the supplied filters.

        All filters are optional and combined with AND logic.
        """
        with self._session_factory() as session:
            q = session.query(AuditLogORM)

            if time_range is not None:
                start, end = time_range
                q = q.filter(AuditLogORM.timestamp >= start, AuditLogORM.timestamp <= end)

            if tool_name is not None:
                q = q.filter(AuditLogORM.tool_name == tool_name)

            if domain is not None:
                q = q.filter(AuditLogORM.domain == domain)

            rows = q.order_by(AuditLogORM.timestamp).all()

        return [self._row_to_entry(row) for row in rows]

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _row_to_entry(row: AuditLogORM) -> AuditEntry:
        from src.types import DomainName  # local import to avoid circular

        return AuditEntry(
            id=row.id,
            timestamp=row.timestamp,
            tool_name=row.tool_name,
            domain=DomainName(row.domain),
            inputs=json.loads(row.inputs),
            output=json.loads(row.output) if row.output is not None else None,
            error=row.error,
            approval_status=row.approval_status,
            session_id=row.session_id,
        )
