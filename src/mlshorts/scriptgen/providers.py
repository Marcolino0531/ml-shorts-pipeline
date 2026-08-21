"""Clientes de LLM que devolvem o roteiro ja estruturado.

OpenAI usa `response_format=json_schema` (JSON mode estrito); Anthropic usa tool calling com
`tool_choice` forcado. Ambos devolvem o mesmo dicionario `{"cenas": [...]}`.

Os modelos Claude mais novos recusam (400) `temperature` fora do padrao, entao o provider da
Anthropic simplesmente nao envia o parametro; a criatividade do roteiro vem do prompt.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Protocol

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from mlshorts.scriptgen.schema import (
    SCRIPT_JSON_SCHEMA,
    TOOL_DESCRIPTION,
    TOOL_NAME,
)

logger = logging.getLogger(__name__)

OPENAI_URL = "https://api.openai.com/v1/chat/completions"
ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"

DEFAULT_OPENAI_MODEL = "gpt-4o-mini"
DEFAULT_ANTHROPIC_MODEL = "claude-3-5-sonnet-latest"

ANTHROPIC_DEFAULT_TEMPERATURE = 1.0


class ScriptGenerationError(RuntimeError):
    """Resposta invalida ou indisponivel do provedor de LLM."""


class LLMProvider(Protocol):
    """Provedor capaz de devolver o roteiro estruturado."""

    name: str
    model: str

    def generate(self, system_prompt: str, user_prompt: str) -> dict[str, Any]:
        """Retorna o payload `{"cenas": [...]}` validado contra o schema do provedor."""
        ...


_retry_http = retry(
    retry=retry_if_exception_type((httpx.TransportError, httpx.HTTPStatusError)),
    wait=wait_exponential(multiplier=1, min=1, max=10),
    stop=stop_after_attempt(3),
    reraise=True,
)


class _BaseProvider:
    def __init__(
        self,
        api_key: str,
        model: str,
        client: httpx.Client | None = None,
        temperature: float = 0.8,
        max_tokens: int = 1200,
        timeout: float = 60.0,
    ) -> None:
        if not api_key:
            raise ScriptGenerationError(f"API key ausente para o provedor {self.name}.")
        self.api_key = api_key
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self._client = client or httpx.Client(timeout=timeout)
        self._owns_client = client is None

    name: str = "base"

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> _BaseProvider:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    @_retry_http
    def _post(self, url: str, payload: dict[str, Any], headers: dict[str, str]) -> dict[str, Any]:
        response = self._client.post(url, json=payload, headers=headers)
        response.raise_for_status()
        data: dict[str, Any] = response.json()
        return data


class OpenAIScriptProvider(_BaseProvider):
    """Geracao via Chat Completions com JSON schema estrito."""

    name = "openai"

    def __init__(self, api_key: str, model: str = DEFAULT_OPENAI_MODEL, **kwargs: Any) -> None:
        super().__init__(api_key=api_key, model=model, **kwargs)

    def generate(self, system_prompt: str, user_prompt: str) -> dict[str, Any]:
        payload = {
            "model": self.model,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": TOOL_NAME,
                    "description": TOOL_DESCRIPTION,
                    "strict": True,
                    "schema": SCRIPT_JSON_SCHEMA,
                },
            },
        }
        data = self._post(
            OPENAI_URL,
            payload,
            {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
        )
        try:
            message = data["choices"][0]["message"]
        except (KeyError, IndexError) as exc:
            raise ScriptGenerationError(f"Resposta inesperada da OpenAI: {data}") from exc
        if message.get("refusal"):
            raise ScriptGenerationError(f"OpenAI recusou a geracao: {message['refusal']}")
        return _parse_json(message.get("content"))


class AnthropicScriptProvider(_BaseProvider):
    """Geracao via Messages API com tool calling forcado."""

    name = "anthropic"

    def __init__(self, api_key: str, model: str = DEFAULT_ANTHROPIC_MODEL, **kwargs: Any) -> None:
        super().__init__(api_key=api_key, model=model, **kwargs)

    def generate(self, system_prompt: str, user_prompt: str) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "system": system_prompt,
            "messages": [{"role": "user", "content": user_prompt}],
            "tools": [
                {
                    "name": TOOL_NAME,
                    "description": TOOL_DESCRIPTION,
                    "input_schema": SCRIPT_JSON_SCHEMA,
                }
            ],
            "tool_choice": {"type": "tool", "name": TOOL_NAME},
        }
        if self.temperature == ANTHROPIC_DEFAULT_TEMPERATURE:
            payload["temperature"] = self.temperature
        else:
            logger.debug(
                "temperature=%s ignorada: %s so aceita o valor padrao (%s)",
                self.temperature,
                self.model,
                ANTHROPIC_DEFAULT_TEMPERATURE,
            )
        data = self._post(
            ANTHROPIC_URL,
            payload,
            {
                "x-api-key": self.api_key,
                "anthropic-version": ANTHROPIC_VERSION,
                "Content-Type": "application/json",
            },
        )
        for block in data.get("content", []):
            if block.get("type") == "tool_use" and block.get("name") == TOOL_NAME:
                tool_input = block.get("input")
                if not isinstance(tool_input, dict):
                    raise ScriptGenerationError(f"tool_use sem input valido: {block}")
                return tool_input
        raise ScriptGenerationError(f"Anthropic nao devolveu tool_use de {TOOL_NAME}: {data}")


def _parse_json(content: object) -> dict[str, Any]:
    if not isinstance(content, str) or not content.strip():
        raise ScriptGenerationError("Conteudo vazio na resposta do LLM.")
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError as exc:
        raise ScriptGenerationError(f"Resposta do LLM nao e JSON valido: {content[:200]}") from exc
    if not isinstance(parsed, dict):
        raise ScriptGenerationError(f"Esperado objeto JSON, recebido: {type(parsed).__name__}")
    return parsed
