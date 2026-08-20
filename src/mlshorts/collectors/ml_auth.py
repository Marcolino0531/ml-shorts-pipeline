"""Autenticacao na API do Mercado Livre pelo fluxo de refresh token.

`client_credentials` nao da acesso a `/sites/{site}/search`, `/items` e afins: a API exige um
token com usuario por tras. Entao o token de acesso e renovado a partir do `ML_REFRESH_TOKEN`
gerado uma vez no consentimento (Authorization Code). Cada troca invalida o refresh token usado
e devolve outro, que e gravado de volta no `.env` para a execucao seguinte continuar sozinha.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path

import httpx

from mlshorts.collectors.base import CollectorError
from mlshorts.config import Secrets
from mlshorts.env_file import set_env_value

logger = logging.getLogger(__name__)

TOKEN_PATH = "/oauth/token"
REFRESH_TOKEN_ENV = "ML_REFRESH_TOKEN"
# margem para nao usar um token que expira no meio da coleta
EXPIRY_MARGIN_SECONDS = 60.0
DEFAULT_EXPIRES_IN = 21600.0


class MercadoLivreAuth:
    """Guarda o token de acesso em memoria e persiste a rotacao do refresh token."""

    def __init__(
        self,
        secrets: Secrets,
        env_file: Path | None = None,
        persist_refresh_token: bool = True,
    ) -> None:
        self.secrets = secrets
        self.env_file = env_file
        self.persist_refresh_token = persist_refresh_token
        self.refresh_token = secrets.ml_refresh_token
        self._access_token: str | None = None
        self.expires_at = 0.0

    def access_token(self, client: httpx.Client) -> str:
        if self._access_token and time.time() < self.expires_at:
            return self._access_token
        return self.refresh(client)

    def refresh(self, client: httpx.Client) -> str:
        """Troca o refresh token por um access token novo (e por um refresh token novo)."""
        if not (self.secrets.ml_client_id and self.secrets.ml_client_secret):
            raise CollectorError(
                "ML_CLIENT_ID/ML_CLIENT_SECRET ausentes: use o coletor de scraping."
            )
        if not self.refresh_token:
            raise CollectorError(
                f"{REFRESH_TOKEN_ENV} ausente: a API do ML exige token de usuario. "
                "Gere o refresh token pelo consentimento (Authorization Code) e salve no .env."
            )

        response = client.post(
            TOKEN_PATH,
            data={
                "grant_type": "refresh_token",
                "client_id": self.secrets.ml_client_id,
                "client_secret": self.secrets.ml_client_secret,
                "refresh_token": self.refresh_token,
            },
            headers={"Accept": "application/json"},
        )
        if response.status_code >= 400:
            raise CollectorError(
                "Falha ao renovar o token do Mercado Livre "
                f"(HTTP {response.status_code}): {response.text}. "
                f"Se o {REFRESH_TOKEN_ENV} foi invalidado, refaca o consentimento."
            )

        payload = response.json()
        self._access_token = str(payload["access_token"])
        self.expires_at = (
            time.time() + float(payload.get("expires_in") or DEFAULT_EXPIRES_IN)
        ) - EXPIRY_MARGIN_SECONDS
        self._rotate(payload.get("refresh_token"))
        return self._access_token

    def _rotate(self, new_refresh_token: object) -> None:
        """O refresh token usado ja nao vale mais: guarda o novo antes de perder o processo."""
        if not new_refresh_token or not isinstance(new_refresh_token, str):
            logger.warning(
                "Renovacao sem refresh_token novo: mantendo o atual, que pode ja estar invalido"
            )
            return
        if new_refresh_token == self.refresh_token:
            return
        self.refresh_token = new_refresh_token
        self.secrets.ml_refresh_token = new_refresh_token
        if not self.persist_refresh_token:
            return
        try:
            path = set_env_value(REFRESH_TOKEN_ENV, new_refresh_token, self.env_file)
        except OSError as exc:
            # nao aborta a coleta: o token em memoria vale para esta execucao
            logger.error(
                "Nao foi possivel gravar o novo %s (%s): a proxima execucao vai falhar na "
                "autenticacao ate o valor ser atualizado manualmente",
                REFRESH_TOKEN_ENV,
                exc,
            )
            return
        logger.info("Novo %s gravado em %s", REFRESH_TOKEN_ENV, path)
