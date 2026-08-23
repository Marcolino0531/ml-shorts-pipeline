"""Historico de produtos que o pipeline ja processou, para nao repetir a mesma oferta.

Nao existe estrutura nova: a fonte da verdade sao os artefatos que cada etapa ja grava
(`data/out/scripts-*.json`, `data/out/narration-*.json`, `data/video/*.mp4`) mais a fila de
publicacao (`data/out/publications.sqlite3`, ou o JSON equivalente).
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from mlshorts.config import PROJECT_ROOT, PublishingConfig
from mlshorts.publish.store import build_store
from mlshorts.storage.paths import Paths

logger = logging.getLogger(__name__)


def _ids_from_json(path: Path, key: str = "product_id") -> set[str]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8") or "[]")
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("Ignorando %s no historico: %s", path.name, exc)
        return set()
    if not isinstance(payload, list):
        return set()
    found: set[str] = set()
    for entry in payload:
        if isinstance(entry, dict):
            value = entry.get(key)
            if isinstance(value, str) and value:
                found.add(value)
    return found


class ProcessedProducts:
    """IDs ja usados em alguma etapa de uma execucao anterior."""

    def __init__(self, paths: Paths | None = None, publishing: PublishingConfig | None = None):
        self.paths = paths or Paths()
        self.publishing = publishing

    def _from_artifacts(self) -> set[str]:
        found: set[str] = set()
        for pattern in ("scripts-*.json", "narration-*.json"):
            for path in self.paths.out.glob(pattern):
                found |= _ids_from_json(path)
        for video in self.paths.video.glob("*.mp4"):
            found.add(video.stem)
        return found

    def _from_queue(self) -> set[str]:
        if self.publishing is None:
            return set()
        path = PROJECT_ROOT / self.publishing.queue_path
        if not path.exists():
            return set()
        try:
            store = build_store(self.publishing.backend, path)
            # qualquer status conta: pendente, publicado ou falho ja consumiu a oferta
            return {item.product_id for item in store.list_all()}
        except Exception as exc:  # noqa: BLE001 - historico incompleto nao pode derrubar a coleta
            logger.warning("Nao foi possivel ler a fila de publicacao (%s): %s", path, exc)
            return set()

    def ids(self) -> set[str]:
        found = self._from_artifacts() | self._from_queue()
        logger.debug("Historico: %d ofertas ja processadas", len(found))
        return found
