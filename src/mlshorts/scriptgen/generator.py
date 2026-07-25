"""Etapa 2: transforma os produtos coletados em roteiros de ate 45 segundos."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from mlshorts.config import ScriptGenConfig, Secrets, Settings, get_secrets
from mlshorts.models import Product, Scene, SceneRole, VideoScript
from mlshorts.scriptgen.prompts import build_system_prompt, build_user_prompt
from mlshorts.scriptgen.providers import (
    AnthropicScriptProvider,
    LLMProvider,
    OpenAIScriptProvider,
    ScriptGenerationError,
)
from mlshorts.storage.paths import Paths

logger = logging.getLogger(__name__)

_ROLE_ORDER = {role: index for index, role in enumerate(SceneRole)}


class ScriptGenerator:
    """Gera um `VideoScript` por produto, no formato Viral Hook."""

    def __init__(
        self,
        provider: LLMProvider,
        config: ScriptGenConfig | None = None,
        max_duration_seconds: int = 45,
    ) -> None:
        self.provider = provider
        self.config = config or ScriptGenConfig()
        self.max_duration_seconds = max_duration_seconds

    @property
    def max_words(self) -> int:
        return int(self.max_duration_seconds * self.config.words_per_second)

    def generate(self, product: Product) -> VideoScript:
        system_prompt = build_system_prompt(self.max_duration_seconds, self.max_words)
        user_prompt = build_user_prompt(product)

        payload = self.provider.generate(system_prompt, user_prompt)
        scenes = self._parse_scenes(payload)
        duration = self._estimate_duration(scenes)

        if duration > self.max_duration_seconds and self.config.retry_when_too_long:
            logger.info(
                "Roteiro de %s ficou com %.1fs (limite %ds): pedindo versao mais curta",
                product.id,
                duration,
                self.max_duration_seconds,
            )
            retry_payload = self.provider.generate(
                system_prompt, f"{user_prompt}\n\n{self._shorten_instruction(scenes)}"
            )
            retry_scenes = self._parse_scenes(retry_payload)
            if self._estimate_duration(retry_scenes) < duration:
                scenes = retry_scenes
                duration = self._estimate_duration(scenes)

        if duration > self.max_duration_seconds:
            logger.warning(
                "Roteiro de %s permanece em %.1fs acima do limite de %ds",
                product.id,
                duration,
                self.max_duration_seconds,
            )

        return VideoScript(
            product_id=product.id,
            scenes=scenes,
            estimated_duration_seconds=round(duration, 1),
            provider=self.provider.name,
            model=self.provider.model,
        )

    def _shorten_instruction(self, scenes: list[Scene]) -> str:
        total = sum(scene.word_count for scene in scenes)
        return (
            f"A versao anterior tinha {total} palavras e ficou longa demais. "
            f"Reescreva o roteiro inteiro com no maximo {self.max_words} palavras somando as "
            "quatro falas, mantendo o gancho forte e a mesma estrutura."
        )

    def _parse_scenes(self, payload: dict[str, Any]) -> list[Scene]:
        raw_scenes = payload.get("cenas")
        if not isinstance(raw_scenes, list) or not raw_scenes:
            raise ScriptGenerationError(f"Payload sem a lista 'cenas': {payload}")
        try:
            scenes = [Scene.model_validate(raw) for raw in raw_scenes]
        except ValidationError as exc:
            raise ScriptGenerationError(f"Cena invalida na resposta do LLM: {exc}") from exc

        roles = {scene.role for scene in scenes}
        missing = [role.value for role in SceneRole if role not in roles]
        if missing:
            raise ScriptGenerationError(f"Roteiro sem os blocos obrigatorios: {missing}")
        scenes.sort(key=lambda scene: _ROLE_ORDER[scene.role])
        return scenes

    def _estimate_duration(self, scenes: list[Scene]) -> float:
        words = sum(scene.word_count for scene in scenes)
        return words / self.config.words_per_second


class ScriptGenerationService:
    """Le os produtos de `data/raw`, gera os roteiros e salva em `data/out`."""

    def __init__(
        self,
        settings: Settings,
        paths: Paths | None = None,
        secrets: Secrets | None = None,
        provider: LLMProvider | None = None,
    ) -> None:
        self.settings = settings
        self.paths = paths or Paths()
        self.secrets = secrets or get_secrets()
        self.generator = ScriptGenerator(
            provider or build_provider(self.settings.scriptgen, self.secrets),
            config=self.settings.scriptgen,
            max_duration_seconds=self.settings.video.max_duration_seconds,
        )

    def latest_products_file(self) -> Path:
        files = sorted(self.paths.raw.glob("products-*.json"))
        if not files:
            raise FileNotFoundError(
                f"Nenhum products-*.json em {self.paths.raw}: rode `mlshorts collect` antes."
            )
        return files[-1]

    def load_products(self, path: Path | None = None) -> list[Product]:
        source = path or self.latest_products_file()
        raw = json.loads(source.read_text(encoding="utf-8"))
        return [Product.model_validate(item) for item in raw]

    def run(self, products_file: Path | None = None) -> list[VideoScript]:
        self.paths.ensure()
        scripts: list[VideoScript] = []
        for product in self.load_products(products_file):
            try:
                scripts.append(self.generator.generate(product))
            except ScriptGenerationError as exc:
                logger.error("Falha ao gerar roteiro de %s: %s", product.id, exc)
        self._persist(scripts)
        return scripts

    def _persist(self, scripts: list[VideoScript]) -> Path:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        path = self.paths.out / f"scripts-{stamp}.json"
        payload = [script.model_dump(mode="json", by_alias=True) for script in scripts]
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        logger.info("Roteiros salvos em %s", path)
        return path


def build_provider(config: ScriptGenConfig, secrets: Secrets | None = None) -> LLMProvider:
    """Instancia o provedor configurado em `settings.yaml` + `.env`."""
    secrets = secrets or get_secrets()
    provider_name = (config.provider or secrets.script_provider).lower()
    if provider_name == "openai":
        if not secrets.openai_api_key:
            raise ScriptGenerationError("OPENAI_API_KEY ausente no .env")
        return OpenAIScriptProvider(
            api_key=secrets.openai_api_key,
            model=config.model_openai,
            temperature=config.temperature,
        )
    if provider_name in {"anthropic", "claude"}:
        if not secrets.anthropic_api_key:
            raise ScriptGenerationError("ANTHROPIC_API_KEY ausente no .env")
        return AnthropicScriptProvider(
            api_key=secrets.anthropic_api_key,
            model=config.model_anthropic,
            temperature=config.temperature,
        )
    raise ScriptGenerationError(f"Provedor de roteiro desconhecido: {provider_name}")
