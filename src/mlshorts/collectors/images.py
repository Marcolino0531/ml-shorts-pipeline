"""Download das imagens em alta resolucao dos produtos aprovados."""

from __future__ import annotations

import logging
from pathlib import Path

import httpx

from mlshorts.models import Product

logger = logging.getLogger(__name__)

_EXTENSIONS = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}


def download_product_images(
    product: Product, output_dir: Path, client: httpx.Client | None = None
) -> list[Path]:
    """Baixa as imagens do produto em `output_dir/<product_id>/` e preenche `local_path`."""
    target_dir = output_dir / product.id
    target_dir.mkdir(parents=True, exist_ok=True)
    http = client or httpx.Client(timeout=30.0, follow_redirects=True)
    paths: list[Path] = []
    try:
        for index, image in enumerate(product.images):
            try:
                response = http.get(str(image.url))
                response.raise_for_status()
            except httpx.HTTPError as exc:
                logger.warning("Falha ao baixar imagem %s: %s", image.url, exc)
                continue
            extension = _EXTENSIONS.get(response.headers.get("content-type", ""), ".jpg")
            path = target_dir / f"{index:02d}{extension}"
            path.write_bytes(response.content)
            image.local_path = str(path)
            paths.append(path)
    finally:
        if client is None:
            http.close()
    return paths
