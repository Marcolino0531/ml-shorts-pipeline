"""Etapa 5: publicacao com controle de ritmo (intervalo minimo por nicho) e fila agendada.

Os metadados (titulo, descricao, hashtags, link de afiliado) entram na proxima fase:
    build_metadata(product: Product, script: VideoScript, media_path: Path) -> VideoMetadata
"""

from mlshorts.publish.scheduler import DryRunPublisher, PublicationScheduler, Publisher
from mlshorts.publish.store import (
    JsonPublicationStore,
    PublicationStore,
    SqlitePublicationStore,
    build_store,
)

__all__ = [
    "DryRunPublisher",
    "JsonPublicationStore",
    "PublicationScheduler",
    "PublicationStore",
    "Publisher",
    "SqlitePublicationStore",
    "build_store",
]
