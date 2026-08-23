"""Gera uma imagem por bloco do roteiro na API de imagens da OpenAI.

Cada cena traz em `instrucao_visual` a descricao da imagem pretendida ("corte seco mostrando
pote de plastico velho manchado..."). Esse texto vira o prompt de geracao, traduzido para ingles
porque o modelo de imagem responde melhor nesse idioma, e o PNG resultante entra na renderizacao
no lugar da foto real do produto. Falha na geracao de um bloco levanta SceneImageError para quem
chamou decidir o fallback, sem derrubar o video inteiro.
"""

from __future__ import annotations

import base64
import logging
from pathlib import Path
from typing import Any, Protocol

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from mlshorts.config import ImageGenConfig, Secrets

logger = logging.getLogger(__name__)

IMAGES_URL = "https://api.openai.com/v1/images/generations"
CHAT_URL = "https://api.openai.com/v1/chat/completions"

TRANSLATION_SYSTEM_PROMPT = (
    "You rewrite Brazilian Portuguese shot descriptions from short-form video scripts as "
    "concise English image-generation prompts. Keep every concrete visual element (subject, "
    "framing, camera move, mood) and drop editing jargon. Answer with the prompt only, in one "
    "sentence, without quotes or explanations."
)


class SceneImageError(RuntimeError):
    """Nao foi possivel gerar a imagem daquele bloco."""


class SceneImageProvider(Protocol):
    """Devolve o arquivo de imagem da cena ou levanta SceneImageError."""

    def generate(self, visual: str, output_path: Path) -> Path: ...


def build_image_prompt(visual: str, config: ImageGenConfig) -> str:
    """Junta o estilo fixo, a descricao da cena e as restricoes, no limite de caracteres."""
    description = " ".join(visual.split())
    if not description:
        raise SceneImageError("descricao visual vazia")
    prompt = f"{config.style_prompt} Scene: {description} {config.negative_prompt}"
    return prompt[: config.max_prompt_chars].strip()


_retry_transport = retry(
    retry=retry_if_exception_type(httpx.TransportError),
    wait=wait_exponential(multiplier=1, min=1, max=8),
    stop=stop_after_attempt(3),
    reraise=True,
)


class SceneImageGenerator:
    """Cliente da API de imagens: um PNG vertical por bloco, gravado em disco."""

    def __init__(
        self,
        api_key: str,
        config: ImageGenConfig | None = None,
        client: httpx.Client | None = None,
    ) -> None:
        self.config = config or ImageGenConfig()
        if not api_key:
            raise SceneImageError("OPENAI_API_KEY ausente: sem chave nao ha geracao de imagem.")
        self.api_key = api_key
        self._client = client or httpx.Client(timeout=self.config.timeout_seconds)
        self._owns_client = client is None

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> SceneImageGenerator:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    @property
    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}

    def prompt_for(self, visual: str) -> str:
        """Traduz o visual quando configurado; traducao indisponivel nao impede a geracao."""
        description = visual
        if self.config.translate_prompts:
            try:
                description = self._translate(visual)
            except Exception as exc:  # noqa: BLE001 - o texto em portugues ainda serve de prompt
                logger.warning(
                    "Traducao do visual falhou (%s): gerando com o texto em portugues", exc
                )
                description = visual
        return build_image_prompt(description, self.config)

    def generate(self, visual: str, output_path: Path) -> Path:
        if self.config.reuse_existing and output_path.exists() and output_path.stat().st_size > 0:
            logger.debug("Imagem ja gerada, reaproveitando %s", output_path)
            return output_path

        prompt = self.prompt_for(visual)
        payload = {
            "model": self.config.model,
            "prompt": prompt,
            "size": self.config.size,
            "quality": self.config.quality,
            "n": 1,
        }
        try:
            content = self._request_image(payload)
        except httpx.HTTPStatusError as exc:
            raise SceneImageError(
                f"{self.config.model} respondeu {exc.response.status_code}: "
                f"{exc.response.text.strip()[:200]}"
            ) from exc
        except httpx.HTTPError as exc:
            raise SceneImageError(f"falha de rede na geracao de imagem: {exc}") from exc

        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(content)
        logger.info("Imagem gerada em %s (%d KB)", output_path, len(content) // 1024)
        return output_path

    @_retry_transport
    def _request_image(self, payload: dict[str, Any]) -> bytes:
        response = self._client.post(IMAGES_URL, headers=self._headers, json=payload)
        response.raise_for_status()
        return self._decode(response.json())

    def _decode(self, body: dict[str, Any]) -> bytes:
        data = body.get("data") or []
        if not data:
            raise SceneImageError("resposta da API de imagens sem `data`")
        encoded = data[0].get("b64_json")
        if encoded:
            return base64.b64decode(encoded)
        url = data[0].get("url")
        if not url:
            raise SceneImageError("resposta da API de imagens sem `b64_json` nem `url`")
        download = self._client.get(url)
        download.raise_for_status()
        return download.content

    @_retry_transport
    def _translate(self, visual: str) -> str:
        response = self._client.post(
            CHAT_URL,
            headers=self._headers,
            json={
                "model": self.config.translation_model,
                "messages": [
                    {"role": "system", "content": TRANSLATION_SYSTEM_PROMPT},
                    {"role": "user", "content": visual},
                ],
            },
        )
        response.raise_for_status()
        choices = response.json().get("choices") or []
        text = (choices[0].get("message", {}).get("content") or "").strip() if choices else ""
        if not text:
            raise SceneImageError("traducao vazia")
        return text


def build_scene_image_generator(
    config: ImageGenConfig, secrets: Secrets
) -> SceneImageProvider | None:
    """None quando a etapa esta desligada ou sem chave: a renderizacao usa as fotos reais."""
    if not config.enabled:
        return None
    if not secrets.openai_api_key:
        logger.warning(
            "OPENAI_API_KEY ausente: renderizando com as fotos reais do produto em vez das "
            "imagens geradas."
        )
        return None
    return SceneImageGenerator(secrets.openai_api_key, config)
