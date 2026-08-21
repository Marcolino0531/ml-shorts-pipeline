"""Nomes das categorias do Mercado Livre, para conferir a categoria de uma oferta.

A vitrine `/ofertas` mistura categorias e nao expoe o id da categoria em cada card — o que da
para comparar e o caminho de categorias (breadcrumb) da pagina do anuncio. `/categories/{id}` e
publico (nao exige token, diferente de `/items` e `/sites/{site}/search`), entao serve para
traduzir o id configurado no settings.yaml nos nomes que aparecem no breadcrumb.
"""

from __future__ import annotations

import logging
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass

import httpx

logger = logging.getLogger(__name__)

CATEGORIES_URL = "https://api.mercadolibre.com/categories"
REQUEST_TIMEOUT = 10.0


def _normalize(value: str) -> str:
    """Sem acento, sem caixa e sem espaco sobrando: o breadcrumb varia na acentuacao."""
    folded = unicodedata.normalize("NFKD", value.strip().lower())
    return "".join(char for char in folded if not unicodedata.combining(char))


@dataclass(frozen=True)
class CategoryMatcher:
    """Aceita uma oferta cujo breadcrumb passe pela categoria configurada."""

    category_id: str
    names: frozenset[str]

    def matches(self, breadcrumb: Iterable[str]) -> bool:
        entries = [_normalize(entry) for entry in breadcrumb if entry.strip()]
        if not entries:
            # sem breadcrumb nao ha como decidir; quem chama trata esse caso
            return False
        if not self.names:
            # categoria nao resolvida: melhor coletar de mais do que voltar vazio
            return True
        return any(entry in self.names for entry in entries)


def fetch_category_matcher(category_id: str, client: httpx.Client | None = None) -> CategoryMatcher:
    """Nome da categoria e das filhas diretas (o breadcrumb pode pular niveis)."""
    owns_client = client is None
    client = client or httpx.Client(timeout=REQUEST_TIMEOUT)
    try:
        response = client.get(f"{CATEGORIES_URL}/{category_id}")
        response.raise_for_status()
        payload = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning(
            "Nao foi possivel resolver a categoria %s (%s): filtro de categoria desligado",
            category_id,
            exc,
        )
        return CategoryMatcher(category_id, frozenset())
    finally:
        if owns_client:
            client.close()

    names = {_normalize(str(payload.get("name") or ""))}
    for child in payload.get("children_categories") or []:
        name = child.get("name")
        if name:
            names.add(_normalize(str(name)))
    names.discard("")
    return CategoryMatcher(category_id, frozenset(names))
