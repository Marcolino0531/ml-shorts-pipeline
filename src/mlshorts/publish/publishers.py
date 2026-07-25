"""Monta o destino da publicacao a partir de `publishing.platforms`."""

from __future__ import annotations

import logging

from mlshorts.config import PublishingConfig, Secrets, get_secrets
from mlshorts.models import QueuedPublication
from mlshorts.publish.errors import PublishError
from mlshorts.publish.scheduler import DryRunPublisher, Publisher
from mlshorts.publish.tiktok import TikTokPublisher
from mlshorts.publish.youtube import YouTubePublisher

logger = logging.getLogger(__name__)


class MultiPublisher:
    """Publica o mesmo video em varias redes; so falha se nenhuma delas aceitar."""

    def __init__(self, publishers: list[Publisher]) -> None:
        if not publishers:
            raise ValueError("MultiPublisher precisa de ao menos um destino")
        self.publishers = publishers
        self.name = "+".join(publisher.name for publisher in publishers)

    def publish(self, item: QueuedPublication) -> None:
        errors: list[str] = []
        for publisher in self.publishers:
            try:
                publisher.publish(item)
            except Exception as exc:  # noqa: BLE001 - uma rede fora do ar nao anula as outras
                logger.error("%s falhou em %s: %s", publisher.name, item.product_id, exc)
                errors.append(f"{publisher.name}: {exc}")
        if len(errors) == len(self.publishers):
            raise PublishError("; ".join(errors))
        if errors:
            item.error = "; ".join(errors)


def build_publisher(
    config: PublishingConfig, secrets: Secrets | None = None, dry_run: bool = False
) -> Publisher:
    """`dry_run` ignora as redes configuradas: serve para testar a fila sem postar nada."""
    if dry_run:
        return DryRunPublisher()

    resolved = secrets or get_secrets()
    publishers: list[Publisher] = []
    for platform in config.platforms:
        normalized = platform.lower()
        if normalized == "dry-run":
            publishers.append(DryRunPublisher())
        elif normalized == "youtube":
            publishers.append(YouTubePublisher(config.youtube, resolved))
        elif normalized == "tiktok":
            publishers.append(TikTokPublisher(config.tiktok, resolved))
        else:
            raise ValueError(f"Plataforma de publicacao desconhecida: {platform}")

    if not publishers:
        raise ValueError("publishing.platforms esta vazio no settings.yaml")
    if len(publishers) == 1:
        return publishers[0]
    return MultiPublisher(publishers)
