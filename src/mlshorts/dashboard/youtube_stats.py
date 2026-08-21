"""Engajamento dos videos ja publicados, lido em `videos?part=statistics` da Data API v3.

Sao dados so do proprio video (views, likes, comentarios). Cliques e vendas do link de
afiliado ficam de fora: o Mercado Livre nao oferece forma automatizada permitida de obte-los.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass

import httpx
from google.auth.transport.requests import Request as GoogleAuthRequest

from mlshorts.config import Secrets, get_secrets
from mlshorts.publish.youtube import build_credentials

logger = logging.getLogger(__name__)

VIDEOS_URL = "https://youtube.googleapis.com/youtube/v3/videos"
# a Data API aceita no maximo 50 ids por chamada
MAX_IDS_PER_CALL = 50
REQUEST_TIMEOUT = 10.0

# https://www.youtube.com/watch?v=<id>, /shorts/<id> e youtu.be/<id>
_VIDEO_ID_RE = re.compile(r"(?:v=|/shorts/|youtu\.be/)([A-Za-z0-9_-]{6,})")


@dataclass(frozen=True)
class VideoStatistics:
    """Contadores publicos do video; None quando a API nao devolve o campo."""

    views: int | None = None
    likes: int | None = None
    comments: int | None = None


def video_id_from_url(url: str) -> str | None:
    match = _VIDEO_ID_RE.search(url)
    return match.group(1) if match else None


class YouTubeStatsClient:
    """Consulta os contadores em lote, degradando para vazio em qualquer falha."""

    def __init__(
        self,
        secrets: Secrets | None = None,
        client: httpx.Client | None = None,
        access_token: Callable[[], str] | None = None,
    ) -> None:
        self.secrets = secrets or get_secrets()
        self._client = client or httpx.Client(timeout=REQUEST_TIMEOUT)
        self._access_token = access_token or self._oauth_access_token

    def _oauth_access_token(self) -> str:
        """Troca o refresh token de publicacao por um access token de leitura."""
        credentials = build_credentials(self.secrets)
        credentials.refresh(GoogleAuthRequest())  # type: ignore[no-untyped-call]
        token = credentials.token
        if not isinstance(token, str) or not token:
            raise httpx.HTTPError("YouTube nao devolveu access token")
        return token

    def fetch(self, video_ids: Iterable[str]) -> dict[str, VideoStatistics]:
        """Estatisticas por id; ids sem resposta simplesmente nao aparecem no dicionario."""
        unique = list(dict.fromkeys(video_id for video_id in video_ids if video_id))
        if not unique:
            return {}
        stats: dict[str, VideoStatistics] = {}
        for start in range(0, len(unique), MAX_IDS_PER_CALL):
            batch = unique[start : start + MAX_IDS_PER_CALL]
            try:
                stats.update(self._fetch_batch(batch))
            except (httpx.HTTPError, ValueError, KeyError) as exc:
                logger.warning(
                    "Estatisticas do YouTube indisponiveis para %d video(s) (%s): "
                    "o painel mostra '--'",
                    len(batch),
                    exc,
                )
        return stats

    def _fetch_batch(self, video_ids: Sequence[str]) -> dict[str, VideoStatistics]:
        params = {"part": "statistics", "id": ",".join(video_ids)}
        headers: dict[str, str] = {}
        # a API key evita depender do escopo do consentimento de upload; senao vai no OAuth
        if self.secrets.youtube_api_key:
            params["key"] = self.secrets.youtube_api_key
        else:
            headers["Authorization"] = f"Bearer {self._access_token()}"

        response = self._client.get(VIDEOS_URL, params=params, headers=headers)
        response.raise_for_status()
        payload = response.json()
        return {
            item["id"]: _statistics(item.get("statistics") or {})
            for item in payload.get("items") or []
            if isinstance(item.get("id"), str)
        }

    def close(self) -> None:
        self._client.close()


def _statistics(raw: dict[str, object]) -> VideoStatistics:
    return VideoStatistics(
        views=_to_int(raw.get("viewCount")),
        likes=_to_int(raw.get("likeCount")),
        comments=_to_int(raw.get("commentCount")),
    )


def _to_int(value: object) -> int | None:
    """A Data API devolve os contadores como string; campos ocultos vem ausentes."""
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        return None
    try:
        return int(value)
    except ValueError:
        return None
