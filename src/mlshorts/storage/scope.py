"""Escopo da execucao atual: os ids do `data/raw/products-*.json` mais recente.

As etapas gravam artefatos que ficam para sempre em `data/` (`scripts-*.json`,
`data/audio/<id>/narration.json`, `data/video/<id>.mp4`), entao processar tudo o que existe
reprocessa e reenfileira rodadas antigas. Cada comando solto usa este escopo por padrao.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from mlshorts.storage.paths import Paths

logger = logging.getLogger(__name__)


def latest_products_file(paths: Paths) -> Path:
    files = sorted(paths.raw.glob("products-*.json"))
    if not files:
        raise FileNotFoundError(
            f"Nenhum products-*.json em {paths.raw}: rode `mlshorts collect` antes."
        )
    return files[-1]


def product_ids_in(path: Path) -> set[str]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError(f"{path} nao tem uma lista de produtos")
    found: set[str] = set()
    for entry in payload:
        if isinstance(entry, dict):
            value = entry.get("id")
            if isinstance(value, str) and value:
                found.add(value)
    return found


def current_product_ids(paths: Paths, products_file: Path | None = None) -> set[str]:
    """Ids da coleta desta execucao; erro claro se nao houver coleta para delimitar o escopo."""
    source = products_file or latest_products_file(paths)
    ids = product_ids_in(source)
    if not ids:
        raise ValueError(f"{source} nao tem nenhum produto: nada a processar nesta execucao")
    logger.info("Escopo desta execucao (%s): %s", source.name, ", ".join(sorted(ids)))
    return ids
