"""Postagem no TikTok pela Content Posting API (init -> upload -> status)."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from pathlib import Path

import httpx

from mlshorts.config import PublishingConfig, Secrets, TikTokConfig, get_secrets
from mlshorts.models import QueuedPublication
from mlshorts.publish.errors import PublishError
from mlshorts.publish.metadata import TIKTOK_CAPTION_LIMIT, MetadataBuilder

logger = logging.getLogger(__name__)

INIT_PATH = "/v2/post/publish/video/init/"
STATUS_PATH = "/v2/post/publish/status/fetch/"
VIDEO_URL = "https://www.tiktok.com/@me/video/{publish_id}"
TERMINAL_FAILURES = {"FAILED", "CANCELED"}


class TikTokPublisher:
    """Sobe o arquivo em um unico chunk e acompanha o `publish_id` ate o post ficar pronto."""

    name = "tiktok"

    def __init__(
        self,
        config: TikTokConfig | None = None,
        secrets: Secrets | None = None,
        client: httpx.Client | None = None,
        caption_builder: MetadataBuilder | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.config = config or TikTokConfig()
        self.secrets = secrets or get_secrets()
        self._client = client
        self.caption_builder = caption_builder or MetadataBuilder(PublishingConfig(), self.secrets)
        self._sleep = sleep

    @property
    def client(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(base_url=self.config.base_url, timeout=120.0)
        return self._client

    @property
    def headers(self) -> dict[str, str]:
        token = self.secrets.tiktok_access_token
        if not token:
            raise PublishError("TIKTOK_ACCESS_TOKEN ausente no .env")
        return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    def caption_for(self, item: QueuedPublication) -> str:
        metadata = item.metadata
        if metadata is None:
            raise PublishError(f"{item.id}: sem metadados (titulo/hashtags/link) para o TikTok")
        return self.caption_builder.caption(metadata, limit=TIKTOK_CAPTION_LIMIT)

    def _init_upload(self, item: QueuedPublication, size: int) -> tuple[str, str]:
        config = self.config
        payload = {
            "post_info": {
                "title": self.caption_for(item),
                "privacy_level": config.privacy_level,
                "disable_comment": config.disable_comment,
                "disable_duet": config.disable_duet,
                "disable_stitch": config.disable_stitch,
            },
            "source_info": {
                "source": "FILE_UPLOAD",
                "video_size": size,
                "chunk_size": size,
                "total_chunk_count": 1,
            },
        }
        response = self.client.post(INIT_PATH, json=payload, headers=self.headers)
        data = _payload(response, item.id)
        publish_id = data.get("publish_id")
        upload_url = data.get("upload_url")
        if not isinstance(publish_id, str) or not isinstance(upload_url, str):
            raise PublishError(f"{item.id}: resposta de init do TikTok incompleta: {data!r}")
        return publish_id, upload_url

    def _upload_file(self, upload_url: str, media_path: Path, size: int) -> None:
        headers = {
            "Content-Type": "video/mp4",
            "Content-Length": str(size),
            "Content-Range": f"bytes 0-{size - 1}/{size}",
        }
        response = self.client.put(upload_url, content=media_path.read_bytes(), headers=headers)
        if response.status_code >= 400:
            raise PublishError(f"Upload do TikTok falhou ({response.status_code}): {response.text}")

    def _wait_for_publish(self, publish_id: str) -> str:
        """A API e assincrona: `PUBLISH_COMPLETE` confirma que o video foi ao ar."""
        status = "PROCESSING"
        for attempt in range(self.config.status_poll_attempts):
            response = self.client.post(
                STATUS_PATH, json={"publish_id": publish_id}, headers=self.headers
            )
            data = _payload(response, publish_id)
            raw_status = data.get("status")
            status = raw_status if isinstance(raw_status, str) else "UNKNOWN"
            if status == "PUBLISH_COMPLETE":
                return status
            if status in TERMINAL_FAILURES:
                raise PublishError(f"TikTok recusou {publish_id}: {data!r}")
            if attempt < self.config.status_poll_attempts - 1:
                self._sleep(self.config.status_poll_seconds)
        raise PublishError(
            f"TikTok ainda em {status} apos {self.config.status_poll_attempts} tentativas"
        )

    def publish(self, item: QueuedPublication) -> str:
        media_path = Path(item.media_path)
        if not media_path.exists():
            raise PublishError(f"{item.id}: video nao encontrado em {media_path}")
        size = media_path.stat().st_size

        publish_id, upload_url = self._init_upload(item, size)
        self._upload_file(upload_url, media_path, size)
        self._wait_for_publish(publish_id)

        url = VIDEO_URL.format(publish_id=publish_id)
        item.published_urls[self.name] = url
        logger.info("%s publicado no TikTok (publish_id=%s)", item.product_id, publish_id)
        return url


def _payload(response: httpx.Response, context: str) -> dict[str, object]:
    """A API responde 200 com `error.code != ok`, entao o corpo precisa ser checado sempre."""
    if response.status_code >= 400:
        raise PublishError(f"TikTok respondeu {response.status_code} em {context}: {response.text}")
    body = response.json()
    error = body.get("error") or {}
    code = error.get("code")
    if code not in (None, "ok"):
        raise PublishError(f"TikTok recusou {context}: {error}")
    data = body.get("data") or {}
    if not isinstance(data, dict):
        raise PublishError(f"TikTok devolveu data invalido em {context}: {body!r}")
    return data
