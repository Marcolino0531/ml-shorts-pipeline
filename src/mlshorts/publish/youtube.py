"""Upload no YouTube (Data API v3) marcado como Shorts."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Protocol

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

from mlshorts.config import Secrets, YouTubeConfig, get_secrets
from mlshorts.models import QueuedPublication, VideoMetadata
from mlshorts.publish.errors import PublishError

logger = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]
TOKEN_URI = "https://oauth2.googleapis.com/token"
WATCH_URL = "https://www.youtube.com/watch?v={video_id}"


class YouTubeResource(Protocol):
    """Parte da API v3 que usamos; permite injetar um duplo nos testes."""

    def videos(self) -> YouTubeVideos: ...


class YouTubeVideos(Protocol):
    def insert(
        self, *, part: str, body: dict[str, object], media_body: object
    ) -> YouTubeRequest: ...


class YouTubeRequest(Protocol):
    def next_chunk(self) -> tuple[object | None, dict[str, object] | None]: ...


def build_credentials(secrets: Secrets) -> Credentials:
    if not secrets.has_youtube_credentials:
        raise PublishError(
            "YOUTUBE_CLIENT_ID/YOUTUBE_CLIENT_SECRET/YOUTUBE_REFRESH_TOKEN ausentes no .env"
        )
    # google-auth nao tem anotacoes no construtor de Credentials
    return Credentials(  # type: ignore[no-untyped-call]
        token=None,
        refresh_token=secrets.youtube_refresh_token,
        client_id=secrets.youtube_client_id,
        client_secret=secrets.youtube_client_secret,
        token_uri=TOKEN_URI,
        scopes=SCOPES,
    )


class YouTubePublisher:
    """Envia o MP4 vertical como Shorts (upload retomavel, em chunks)."""

    name = "youtube"

    def __init__(
        self,
        config: YouTubeConfig | None = None,
        secrets: Secrets | None = None,
        resource: YouTubeResource | None = None,
    ) -> None:
        self.config = config or YouTubeConfig()
        self.secrets = secrets or get_secrets()
        self._resource = resource

    @property
    def resource(self) -> YouTubeResource:
        if self._resource is None:
            self._resource = build(
                "youtube", "v3", credentials=build_credentials(self.secrets), cache_discovery=False
            )
        return self._resource

    def build_body(self, metadata: VideoMetadata) -> dict[str, object]:
        """`#Shorts` no titulo e na descricao e o que classifica o video no feed de Shorts."""
        config = self.config
        title = metadata.title
        if config.shorts_tag.lower() not in title.lower():
            title = f"{title} {config.shorts_tag}"
        description = metadata.description
        if config.shorts_tag.lower() not in description.lower():
            description = f"{description}\n{config.shorts_tag}"
        tags = [tag.lstrip("#") for tag in metadata.hashtags]
        return {
            "snippet": {
                "title": title[:100],
                "description": description[:5000],
                "tags": tags,
                "categoryId": config.category_id,
                "defaultLanguage": "pt-BR",
                "defaultAudioLanguage": "pt-BR",
            },
            "status": {
                "privacyStatus": config.privacy_status,
                "selfDeclaredMadeForKids": config.made_for_kids,
            },
        }

    def publish(self, item: QueuedPublication) -> str:
        metadata = item.metadata
        if metadata is None:
            raise PublishError(f"{item.id}: sem metadados (titulo/hashtags/link) para o YouTube")
        media_path = Path(item.media_path)
        if not media_path.exists():
            raise PublishError(f"{item.id}: video nao encontrado em {media_path}")

        media = MediaFileUpload(
            str(media_path),
            mimetype="video/mp4",
            chunksize=self.config.upload_chunk_size,
            resumable=True,
        )
        request = self.resource.videos().insert(
            part="snippet,status", body=self.build_body(metadata), media_body=media
        )

        response: dict[str, object] | None = None
        while response is None:
            status, response = request.next_chunk()
            if status is not None:
                logger.debug("Upload de %s em andamento", item.product_id)

        video_id = response.get("id")
        if not isinstance(video_id, str):
            raise PublishError(f"{item.id}: resposta do YouTube sem id: {response!r}")
        url = WATCH_URL.format(video_id=video_id)
        item.published_urls[self.name] = url
        logger.info("%s publicado no YouTube: %s", item.product_id, url)
        return url
