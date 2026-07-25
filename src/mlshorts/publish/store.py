"""Persistencia da fila de publicacao e do historico por nicho.

Dois backends com o mesmo contrato: JSON (simples, versionavel) e SQLite (concorrencia e
consultas). O historico e a propria fila: um item publicado guarda `published_at`, que e o que
define quando o nicho fica livre de novo.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol

from mlshorts.models import PublicationStatus, QueuedPublication

logger = logging.getLogger(__name__)


class PublicationStore(Protocol):
    """Fila de publicacoes + historico da ultima postagem de cada nicho."""

    def add(self, item: QueuedPublication) -> QueuedPublication: ...

    def update(self, item: QueuedPublication) -> QueuedPublication: ...

    def get(self, item_id: str) -> QueuedPublication | None: ...

    def list_all(self, status: PublicationStatus | None = None) -> list[QueuedPublication]: ...

    def pending(self) -> list[QueuedPublication]:
        """Pendentes ordenados por `scheduled_for` (mais antigo primeiro)."""
        ...

    def due(self, now: datetime) -> list[QueuedPublication]: ...

    def last_published_at(self, niche: str) -> datetime | None: ...


def _sorted_pending(items: list[QueuedPublication]) -> list[QueuedPublication]:
    pending = [item for item in items if item.status is PublicationStatus.PENDING]
    pending.sort(key=lambda item: (item.scheduled_for, item.created_at))
    return pending


class JsonPublicationStore:
    """Backend em arquivo JSON unico."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def _load(self) -> list[QueuedPublication]:
        if not self.path.exists():
            return []
        raw = json.loads(self.path.read_text(encoding="utf-8") or "[]")
        return [QueuedPublication.model_validate(entry) for entry in raw]

    def _save(self, items: list[QueuedPublication]) -> None:
        payload = [item.model_dump(mode="json") for item in items]
        self.path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def add(self, item: QueuedPublication) -> QueuedPublication:
        items = self._load()
        items.append(item)
        self._save(items)
        return item

    def update(self, item: QueuedPublication) -> QueuedPublication:
        items = self._load()
        for index, existing in enumerate(items):
            if existing.id == item.id:
                items[index] = item
                self._save(items)
                return item
        raise KeyError(f"Publicacao {item.id} nao esta na fila")

    def get(self, item_id: str) -> QueuedPublication | None:
        return next((item for item in self._load() if item.id == item_id), None)

    def list_all(self, status: PublicationStatus | None = None) -> list[QueuedPublication]:
        items = self._load()
        return [item for item in items if status is None or item.status is status]

    def pending(self) -> list[QueuedPublication]:
        return _sorted_pending(self._load())

    def due(self, now: datetime) -> list[QueuedPublication]:
        return [item for item in self.pending() if item.is_due(now)]

    def last_published_at(self, niche: str) -> datetime | None:
        published = [
            item.published_at
            for item in self._load()
            if item.niche == niche
            and item.status is PublicationStatus.PUBLISHED
            and item.published_at is not None
        ]
        return max(published) if published else None


_SCHEMA = """
CREATE TABLE IF NOT EXISTS publications (
    id TEXT PRIMARY KEY,
    payload TEXT NOT NULL,
    niche TEXT NOT NULL,
    status TEXT NOT NULL,
    scheduled_for TEXT NOT NULL,
    published_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_publications_niche_status ON publications (niche, status);
CREATE INDEX IF NOT EXISTS idx_publications_status_schedule ON publications (status, scheduled_for);
"""


class SqlitePublicationStore:
    """Backend em SQLite; o modelo completo fica serializado na coluna `payload`."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.executescript(_SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, isolation_level=None)
        connection.row_factory = sqlite3.Row
        return connection

    @staticmethod
    def _row_to_item(row: sqlite3.Row) -> QueuedPublication:
        return QueuedPublication.model_validate_json(row["payload"])

    def _write(self, item: QueuedPublication) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO publications (id, payload, niche, status, scheduled_for, published_at)
                VALUES (:id, :payload, :niche, :status, :scheduled_for, :published_at)
                ON CONFLICT(id) DO UPDATE SET
                    payload = excluded.payload,
                    niche = excluded.niche,
                    status = excluded.status,
                    scheduled_for = excluded.scheduled_for,
                    published_at = excluded.published_at
                """,
                {
                    "id": item.id,
                    "payload": item.model_dump_json(),
                    "niche": item.niche,
                    "status": item.status.value,
                    "scheduled_for": _utc_iso(item.scheduled_for),
                    "published_at": _utc_iso(item.published_at) if item.published_at else None,
                },
            )

    def add(self, item: QueuedPublication) -> QueuedPublication:
        self._write(item)
        return item

    def update(self, item: QueuedPublication) -> QueuedPublication:
        if self.get(item.id) is None:
            raise KeyError(f"Publicacao {item.id} nao esta na fila")
        self._write(item)
        return item

    def get(self, item_id: str) -> QueuedPublication | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload FROM publications WHERE id = ?", (item_id,)
            ).fetchone()
        return self._row_to_item(row) if row else None

    def list_all(self, status: PublicationStatus | None = None) -> list[QueuedPublication]:
        query = "SELECT payload FROM publications"
        params: tuple[str, ...] = ()
        if status is not None:
            query += " WHERE status = ?"
            params = (status.value,)
        query += " ORDER BY scheduled_for"
        with self._connect() as connection:
            rows = connection.execute(query, params).fetchall()
        return [self._row_to_item(row) for row in rows]

    def pending(self) -> list[QueuedPublication]:
        return _sorted_pending(self.list_all(PublicationStatus.PENDING))

    def due(self, now: datetime) -> list[QueuedPublication]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT payload FROM publications
                WHERE status = ? AND scheduled_for <= ?
                ORDER BY scheduled_for
                """,
                (PublicationStatus.PENDING.value, _utc_iso(now)),
            ).fetchall()
        return [self._row_to_item(row) for row in rows]

    def last_published_at(self, niche: str) -> datetime | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT MAX(published_at) AS last FROM publications
                WHERE niche = ? AND status = ? AND published_at IS NOT NULL
                """,
                (niche, PublicationStatus.PUBLISHED.value),
            ).fetchone()
        return _parse_datetime(row["last"]) if row and row["last"] else None


def _utc_iso(value: datetime) -> str:
    """Normaliza para UTC: as colunas sao comparadas como texto no SQL."""
    aware = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return aware.astimezone(timezone.utc).isoformat()


def _parse_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def build_store(backend: str, path: Path) -> PublicationStore:
    normalized = backend.lower()
    if normalized == "json":
        return JsonPublicationStore(path)
    if normalized == "sqlite":
        return SqlitePublicationStore(path)
    raise ValueError(f"Backend de publicacao desconhecido: {backend}")
