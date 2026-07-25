"""Metadados da postagem: titulo, descricao, hashtags do nicho e link de afiliado do ML."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from mlshorts.config import PublishingConfig, Secrets, get_secrets
from mlshorts.models import Product, VideoMetadata, VideoScript
from mlshorts.storage.paths import Paths

logger = logging.getLogger(__name__)

YOUTUBE_TITLE_LIMIT = 100
TIKTOK_CAPTION_LIMIT = 2200


class MetadataBuilder:
    """Monta o texto da publicacao a partir do produto coletado e do roteiro gerado."""

    def __init__(self, config: PublishingConfig, secrets: Secrets | None = None) -> None:
        self.config = config
        self.secrets = secrets or get_secrets()

    def hashtags_for(self, niche: str) -> list[str]:
        return self.config.hashtags_for(niche)

    def affiliate_link(self, permalink: str) -> str:
        """Adiciona a tag de afiliado ao permalink sem duplicar parametros existentes."""
        tag = self.secrets.ml_affiliate_tag
        if not tag:
            logger.warning("ML_AFFILIATE_TAG ausente: publicando o link sem tag de afiliado")
            return permalink
        parts = urlparse(permalink)
        query = dict(parse_qsl(parts.query))
        query[self.config.affiliate_param] = tag
        return urlunparse(parts._replace(query=urlencode(query)))

    def title_for(self, product: Product, script: VideoScript | None = None) -> str:
        """Prefere o gancho do roteiro; sem roteiro usa o titulo do produto."""
        hook = script.scenes[0].narration.strip() if script and script.scenes else ""
        base = hook or product.title.strip()
        shorts_tag = self.config.youtube.shorts_tag
        room = YOUTUBE_TITLE_LIMIT - len(shorts_tag) - 1
        if len(base) > room:
            base = base[: room - 1].rstrip(" ,.;:-") + "…"
        return f"{base} {shorts_tag}"

    def build(
        self,
        product: Product,
        niche: str,
        media_path: str | Path | None = None,
        script: VideoScript | None = None,
    ) -> VideoMetadata:
        hashtags = self.hashtags_for(niche)
        link = self.affiliate_link(str(product.permalink))
        lines = [product.title.strip()]
        if product.rating is not None:
            lines.append(f"⭐ {product.rating:.1f} com {product.reviews_total} avaliacoes")
        if product.sold_quantity:
            lines.append(f"🔥 {product.sold_quantity}+ vendidos no Mercado Livre")
        lines += ["", f"🛒 Compre aqui: {link}", ""]
        if hashtags:
            lines.append(" ".join(hashtags))
        lines.append("\n#anuncio #publi - link de afiliado")

        return VideoMetadata(
            product_id=product.id,
            title=self.title_for(product, script),
            description="\n".join(lines),
            hashtags=hashtags,
            affiliate_link=link,
            media_path=str(media_path) if media_path else None,
        )

    def caption(self, metadata: VideoMetadata, limit: int = TIKTOK_CAPTION_LIMIT) -> str:
        """Legenda de uma linha (TikTok): titulo + link + hashtags, cortada no limite da rede."""
        parts = [metadata.title, metadata.affiliate_link, " ".join(metadata.hashtags)]
        caption = " ".join(part for part in parts if part).strip()
        return caption if len(caption) <= limit else caption[: limit - 1].rstrip() + "…"


class MetadataService:
    """Recupera o produto (e o roteiro) nos artefatos de `data/` para montar os metadados."""

    def __init__(
        self,
        config: PublishingConfig,
        paths: Paths | None = None,
        builder: MetadataBuilder | None = None,
    ) -> None:
        self.config = config
        self.paths = paths or Paths()
        self.builder = builder or MetadataBuilder(config)

    def _latest(self, directory: Path, pattern: str) -> Path | None:
        files = sorted(directory.glob(pattern))
        return files[-1] if files else None

    def find_product(self, product_id: str) -> Product | None:
        latest = self._latest(self.paths.raw, "products-*.json")
        if latest is None:
            return None
        for entry in json.loads(latest.read_text(encoding="utf-8")):
            if entry.get("id") == product_id:
                return Product.model_validate(entry)
        return None

    def find_script(self, product_id: str) -> VideoScript | None:
        latest = self._latest(self.paths.out, "scripts-*.json")
        if latest is None:
            return None
        for entry in json.loads(latest.read_text(encoding="utf-8")):
            if entry.get("product_id") == product_id:
                return VideoScript.model_validate(entry)
        return None

    def build_for(
        self, product_id: str, niche: str, media_path: str | Path | None = None
    ) -> VideoMetadata | None:
        product = self.find_product(product_id)
        if product is None:
            logger.warning("Produto %s nao esta no ultimo products-*.json", product_id)
            return None
        return self.builder.build(
            product, niche, media_path=media_path, script=self.find_script(product_id)
        )
