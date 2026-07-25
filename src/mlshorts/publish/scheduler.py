"""Controle de tempo entre publicacoes: enfileira agora, publica no horario certo."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Protocol

from mlshorts.config import PROJECT_ROOT, PublishingConfig, Settings
from mlshorts.models import PublicationStatus, QueuedPublication, VideoMetadata
from mlshorts.publish.store import PublicationStore, build_store

logger = logging.getLogger(__name__)


class Publisher(Protocol):
    """Destino final do video (rede social, webhook, pasta de saida...)."""

    name: str

    def publish(self, item: QueuedPublication) -> None: ...


class DryRunPublisher:
    """Publisher padrao: apenas registra a publicacao (integracao real fica na proxima fase)."""

    name = "dry-run"

    def publish(self, item: QueuedPublication) -> None:
        logger.info(
            "[dry-run] publicando %s (%s): %s", item.product_id, item.niche, item.media_path
        )


def _utc(value: datetime | None = None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


class PublicationScheduler:
    """Garante o intervalo minimo entre publicacoes do mesmo nicho."""

    def __init__(
        self,
        store: PublicationStore,
        config: PublishingConfig | None = None,
        publisher: Publisher | None = None,
    ) -> None:
        self.store = store
        self.config = config or PublishingConfig()
        self.publisher = publisher or DryRunPublisher()

    @classmethod
    def from_settings(
        cls,
        settings: Settings,
        publisher: Publisher | None = None,
        queue_path: Path | None = None,
    ) -> PublicationScheduler:
        config = settings.publishing
        path = queue_path or (PROJECT_ROOT / config.queue_path)
        return cls(build_store(config.backend, path), config=config, publisher=publisher)

    # ------------------------------------------------------------------ tempo

    def interval_for(self, niche: str) -> timedelta:
        return timedelta(hours=self.config.interval_for(niche))

    def next_slot(self, niche: str, now: datetime | None = None) -> datetime:
        """Primeiro instante em que o nicho pode receber uma nova publicacao."""
        moment = _utc(now)
        last = self.store.last_published_at(niche)
        if last is None:
            return moment
        return max(moment, _utc(last) + self.interval_for(niche))

    def is_blocked(self, niche: str, now: datetime | None = None) -> bool:
        moment = _utc(now)
        return self.next_slot(niche, moment) > moment

    # ------------------------------------------------------------------- fila

    def submit(
        self,
        product_id: str,
        niche: str,
        media_path: str,
        metadata: VideoMetadata | None = None,
        now: datetime | None = None,
    ) -> QueuedPublication:
        """Publica imediatamente se o nicho estiver livre; caso contrario agenda."""
        moment = _utc(now)
        scheduled_for = self._first_free_slot(niche, moment)
        item = self.store.add(
            QueuedPublication(
                product_id=product_id,
                niche=niche,
                media_path=media_path,
                metadata=metadata,
                scheduled_for=scheduled_for,
            )
        )
        if self.config.require_approval:
            logger.info("%s aguardando aprovacao manual no dashboard", item.id)
            return item
        if scheduled_for <= moment:
            return self._publish(item, moment)
        logger.info(
            "Nicho %s bloqueado ate %s: %s ficou pendente na fila",
            niche,
            scheduled_for.isoformat(),
            item.id,
        )
        return item

    def _first_free_slot(self, niche: str, now: datetime) -> datetime:
        """Considera tambem os itens ja agendados do nicho, para nao empilhar no mesmo horario."""
        slot = self.next_slot(niche, now)
        interval = self.interval_for(niche)
        for pending in self.store.pending():
            if pending.niche != niche:
                continue
            if pending.scheduled_for >= slot - interval:
                slot = max(slot, _utc(pending.scheduled_for) + interval)
        return slot

    def approve(self, item_id: str, now: datetime | None = None) -> QueuedPublication:
        """Libera o item na aprovacao manual; publica na hora se o horario dele ja passou."""
        moment = _utc(now)
        item = self.store.get(item_id)
        if item is None:
            raise KeyError(f"Item de publicacao inexistente: {item_id}")
        if item.status is not PublicationStatus.PENDING:
            raise ValueError(f"{item_id} nao esta pendente (status={item.status.value})")
        item.approved_at = moment
        item = self.store.update(item)
        if item.scheduled_for <= moment and self.next_slot(item.niche, moment) <= moment:
            return self._publish(item, moment)
        return item

    def cancel(self, item_id: str) -> QueuedPublication:
        item = self.store.get(item_id)
        if item is None:
            raise KeyError(f"Item de publicacao inexistente: {item_id}")
        item.status = PublicationStatus.CANCELLED
        return self.store.update(item)

    def awaiting_approval(self) -> list[QueuedPublication]:
        if not self.config.require_approval:
            return []
        return [item for item in self.store.pending() if item.approved_at is None]

    def process_due(
        self, now: datetime | None = None, limit: int | None = None
    ) -> list[QueuedPublication]:
        """Publica os itens vencidos respeitando o intervalo; reagenda o que ainda esta preso."""
        moment = _utc(now)
        max_items = self.config.max_per_run if limit is None else limit
        published: list[QueuedPublication] = []

        for item in self.store.due(moment):
            if len(published) >= max_items:
                break
            if self.config.require_approval and item.approved_at is None:
                logger.info("%s vencido mas sem aprovacao manual: mantido na fila", item.id)
                continue
            slot = self.next_slot(item.niche, moment)
            if slot > moment:
                item.scheduled_for = slot
                self.store.update(item)
                logger.info("%s reagendado para %s", item.id, slot.isoformat())
                continue
            published.append(self._publish(item, moment))
        return published

    def _publish(self, item: QueuedPublication, moment: datetime) -> QueuedPublication:
        try:
            self.publisher.publish(item)
        except Exception as exc:  # noqa: BLE001 - falha de rede nao pode derrubar a fila
            item.status = PublicationStatus.FAILED
            item.error = str(exc)
            logger.error("Falha ao publicar %s: %s", item.id, exc)
        else:
            item.status = PublicationStatus.PUBLISHED
            item.published_at = moment
            item.error = None
        return self.store.update(item)
