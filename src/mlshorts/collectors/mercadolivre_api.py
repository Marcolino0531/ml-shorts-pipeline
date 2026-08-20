"""Coletor baseado na API oficial do Mercado Livre.

Fluxo: token (client_credentials) -> busca da categoria ordenada por mais vendidos ->
multiget de itens -> descricao + reviews de cada item. `/highlights` fica como fallback.
"""

from __future__ import annotations

import logging
import re
import time
from typing import Any

import httpx
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

from mlshorts.collectors.base import CollectorError
from mlshorts.collectors.stats import summarize_listings
from mlshorts.config import Secrets, get_secrets
from mlshorts.models import Product, ProductImage, Review

logger = logging.getLogger(__name__)

API_BASE = "https://api.mercadolibre.com"
TOKEN_URL = f"{API_BASE}/oauth/token"
ITEM_BATCH_SIZE = 20
SEARCH_PAGE_SIZE = 50
# a busca publica nao pagina alem de 1000 resultados
SEARCH_MAX_OFFSET = 1000
# `sold_quantity_desc` nao esta documentado em `available_sorts`, mas continua sendo aceito;
# quando a API recusa ou ignora, o coletor cai para a ordenacao padrao e avisa no log.
SOLD_SEARCH_SORT = "sold_quantity_desc"
DEFAULT_SEARCH_SORT = "relevance"
# destaques que apontam para o catalogo, nao para um anuncio: precisam do buy box
CATALOG_TYPES = frozenset({"PRODUCT", "USER_PRODUCT"})
_SIZE_RE = re.compile(r"^(\d+)x(\d+)$")


def _is_retryable(exc: BaseException) -> bool:
    """Repetir 404 nao ajuda: catalogo sem anuncio e item sem descricao respondem sempre igual."""
    if isinstance(exc, httpx.TransportError):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        return status == 429 or status >= 500
    return False


_retry_http = retry(
    retry=retry_if_exception(_is_retryable),
    wait=wait_exponential(multiplier=1, min=1, max=10),
    stop=stop_after_attempt(3),
    reraise=True,
)


class MercadoLivreAPICollector:
    """Coleta produtos em alta usando a busca da categoria, com `/highlights` como fallback."""

    name = "mercadolivre-api"

    def __init__(
        self,
        secrets: Secrets | None = None,
        client: httpx.Client | None = None,
        max_reviews: int = 8,
        max_images: int = 5,
        search_sort: str = SOLD_SEARCH_SORT,
    ) -> None:
        self.secrets = secrets or get_secrets()
        self._client = client or httpx.Client(base_url=API_BASE, timeout=20.0)
        self._owns_client = client is None
        self.max_reviews = max_reviews
        self.max_images = max_images
        self.search_sort = search_sort
        self._token: str | None = None
        self._token_expires_at: float = 0.0

    def __enter__(self) -> MercadoLivreAPICollector:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    # ------------------------------------------------------------------ auth

    def _access_token(self) -> str:
        if self._token and time.time() < self._token_expires_at:
            return self._token
        if not self.secrets.has_ml_credentials:
            raise CollectorError(
                "ML_CLIENT_ID/ML_CLIENT_SECRET ausentes: use o coletor de scraping."
            )
        response = self._client.post(
            TOKEN_URL,
            data={
                "grant_type": "client_credentials",
                "client_id": self.secrets.ml_client_id,
                "client_secret": self.secrets.ml_client_secret,
            },
            headers={"Accept": "application/json"},
        )
        if response.status_code >= 400:
            raise CollectorError(f"Falha ao autenticar no Mercado Livre: {response.text}")
        payload = response.json()
        self._token = str(payload["access_token"])
        # margem de 60s para evitar corrida com a expiracao
        self._token_expires_at = time.time() + float(payload.get("expires_in", 21600)) - 60
        return self._token

    @_retry_http
    def _get(self, path: str, **params: Any) -> Any:
        response = self._client.get(
            path,
            params=params or None,
            headers={"Authorization": f"Bearer {self._access_token()}"},
        )
        response.raise_for_status()
        return response.json()

    # -------------------------------------------------------------- coleta

    def collect_category(self, category_id: str, limit: int) -> list[Product]:
        item_ids = self.search_item_ids(category_id, limit)
        if not item_ids:
            # `/highlights` como plano B: cobre vitrines curadas que a busca nao ordena bem
            logger.info("Busca vazia em %s: tentando /highlights", category_id)
            item_ids = self.highlight_item_ids(category_id, limit)
        if not item_ids:
            logger.warning("Nenhum anuncio encontrado para a categoria %s", category_id)
            return []
        products = self.fetch_items(item_ids)
        for product in products:
            product.attributes.setdefault("descricao", self.fetch_description(product.id))
            product.positive_reviews = self.fetch_positive_reviews(product.id)
        return products

    def search_item_ids(self, category_id: str, limit: int) -> list[str]:
        """Anuncios da categoria em `/sites/{site}/search`, do mais vendido para o menos.

        Diferente de `/highlights`, a busca devolve anuncios (`MLB<numero>`) direto — sem a etapa
        de catalogo, que exige `buy_box_winner` e so vem preenchido para token de vendedor.
        """
        ids: list[str] = []
        sort = self.search_sort
        offset = 0
        while len(ids) < limit and offset < SEARCH_MAX_OFFSET:
            page_size = min(SEARCH_PAGE_SIZE, limit - len(ids))
            payload = self._search_page(category_id, sort, offset, page_size)
            if payload is None:
                return []
            sort = self._effective_sort(payload, sort, category_id)
            results = payload.get("results") or []
            if not results:
                break
            stats = summarize_listings(results)
            logger.info("Busca %s (offset %s): %s", category_id, offset, stats.as_log_line())
            ids.extend(str(result["id"]) for result in results if result.get("id"))
            paging = payload.get("paging") or {}
            offset += len(results)
            if offset >= int(paging.get("total") or 0):
                break
        return ids[:limit]

    def _search_page(
        self, category_id: str, sort: str, offset: int, limit: int
    ) -> dict[str, Any] | None:
        """Uma pagina da busca; cai para `relevance` se a API recusar a ordenacao pedida."""
        try:
            payload = self._get(
                f"/sites/{self.secrets.ml_site_id}/search",
                category=category_id,
                sort=sort,
                offset=offset,
                limit=limit,
            )
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 400 and sort != DEFAULT_SEARCH_SORT:
                logger.warning(
                    "Ordenacao %s recusada pela API: repetindo com %s", sort, DEFAULT_SEARCH_SORT
                )
                self.search_sort = DEFAULT_SEARCH_SORT
                return self._search_page(category_id, DEFAULT_SEARCH_SORT, offset, limit)
            logger.warning("Busca falhou em %s: %s", category_id, exc)
            return None
        return payload if isinstance(payload, dict) else None

    def _effective_sort(self, payload: dict[str, Any], requested: str, category_id: str) -> str:
        """A API aceita o parametro e ignora silenciosamente sorts descontinuados."""
        applied = str((payload.get("sort") or {}).get("id") or requested)
        if applied != requested:
            available = [
                str(entry.get("id")) for entry in payload.get("available_sorts") or [] if entry
            ]
            logger.warning(
                "%s ignorou sort=%s (aplicou %s); disponiveis: %s",
                category_id,
                requested,
                applied,
                ", ".join(available) or "nao informados",
            )
            self.search_sort = applied
        return applied

    def highlight_item_ids(self, category_id: str, limit: int) -> list[str]:
        """Resolve os destaques em ids de anuncio (`/items`).

        `/highlights` devolve tres tipos: `ITEM` ja e um anuncio, mas `PRODUCT` e
        `USER_PRODUCT` sao produtos de catalogo e dao 404 em `/items` — para esses e preciso
        descobrir o anuncio vencedor do buy box. Categorias como MLB1618 (Cozinha) sao quase
        inteiramente catalogo, e sem essa resolucao voltavam vazias.
        """
        payload = self._get(f"/highlights/{self.secrets.ml_site_id}/category/{category_id}")
        content = payload.get("content", []) if isinstance(payload, dict) else []

        ids: list[str] = []
        seen: set[str] = set()
        for entry in content:
            entry_id = entry.get("id")
            entry_type = entry.get("type")
            if not entry_id:
                continue
            if entry_type == "ITEM":
                item_id: str | None = str(entry_id)
            elif entry_type in CATALOG_TYPES:
                item_id = self.buy_box_item_id(str(entry_id))
            else:
                logger.debug("Destaque de tipo %s ignorado (%s)", entry_type, entry_id)
                continue
            # o mesmo anuncio pode vencer o buy box de mais de um produto de catalogo
            if item_id and item_id not in seen:
                seen.add(item_id)
                ids.append(item_id)
            if len(ids) >= limit:
                break
        return ids

    def buy_box_item_id(self, product_id: str) -> str | None:
        """Anuncio vencedor do buy box de um produto de catalogo."""
        try:
            payload = self._get(f"/products/{product_id}")
        except httpx.HTTPStatusError as exc:
            logger.warning("Produto de catalogo %s indisponivel: %s", product_id, exc)
            return None
        winner = payload.get("buy_box_winner") or {}
        item_id = winner.get("item_id")
        if not item_id:
            logger.info("Catalogo %s sem vencedor de buy box: ignorado", product_id)
            return None
        return str(item_id)

    def fetch_items(self, item_ids: list[str]) -> list[Product]:
        products: list[Product] = []
        for start in range(0, len(item_ids), ITEM_BATCH_SIZE):
            batch = item_ids[start : start + ITEM_BATCH_SIZE]
            payload = self._get("/items", ids=",".join(batch))
            for entry in payload:
                if entry.get("code") != 200:
                    logger.warning(
                        "Item %s indisponivel (code=%s)",
                        entry.get("body", {}).get("id"),
                        entry.get("code"),
                    )
                    continue
                products.append(self._parse_item(entry["body"]))
        return products

    def fetch_description(self, item_id: str) -> str:
        try:
            payload = self._get(f"/items/{item_id}/description")
        except httpx.HTTPStatusError as exc:
            logger.debug("Sem descricao para %s: %s", item_id, exc)
            return ""
        text = payload.get("plain_text") or payload.get("text") or ""
        return str(text).strip()

    def fetch_positive_reviews(self, item_id: str) -> list[Review]:
        try:
            payload = self._get(f"/reviews/item/{item_id}", limit=50)
        except httpx.HTTPStatusError as exc:
            logger.debug("Sem reviews via API para %s: %s", item_id, exc)
            return []
        reviews = [
            Review.model_validate(
                {
                    "id": str(raw.get("id")),
                    "rate": int(raw.get("rate", 0)),
                    "title": raw.get("title"),
                    "content": (raw.get("content") or "").strip(),
                    "likes": int(raw.get("likes", 0)),
                    "date_created": raw.get("date_created"),
                }
            )
            for raw in payload.get("reviews", [])
            if raw.get("content")
        ]
        positives = [review for review in reviews if review.rate >= 4]
        positives.sort(key=lambda review: (review.likes, review.rate), reverse=True)
        return positives[: self.max_reviews]

    # -------------------------------------------------------------- parsing

    def _parse_item(self, body: dict[str, Any]) -> Product:
        attributes: dict[str, str] = {}
        for attribute in body.get("attributes", []):
            name = attribute.get("name")
            value = attribute.get("value_name")
            if name and value:
                attributes[str(name)] = str(value)

        reviews_block = body.get("reviews") or {}
        rating = reviews_block.get("rating_average")

        return Product(
            id=body["id"],
            title=body["title"],
            permalink=body["permalink"],
            category_id=body.get("category_id", ""),
            price=float(body.get("price") or 0.0),
            currency_id=body.get("currency_id", "BRL"),
            sold_quantity=int(body.get("sold_quantity") or attributes.get("sold_quantity") or 0),
            rating=float(rating) if rating is not None else None,
            reviews_total=int(reviews_block.get("total") or 0),
            free_shipping=bool((body.get("shipping") or {}).get("free_shipping")),
            brand=attributes.get("Marca"),
            attributes=attributes,
            images=self._parse_images(body.get("pictures", [])),
            source=self.name,
        )

    def _parse_images(self, pictures: list[dict[str, Any]]) -> list[ProductImage]:
        images: list[ProductImage] = []
        for picture in pictures[: self.max_images]:
            width, height = _parse_size(picture.get("max_size") or picture.get("size") or "")
            url = picture.get("secure_url") or picture.get("url")
            if not url:
                continue
            images.append(
                ProductImage(id=str(picture.get("id", "")), url=url, width=width, height=height)
            )
        return images


def _parse_size(size: str) -> tuple[int, int]:
    match = _SIZE_RE.match(size)
    if not match:
        return (0, 0)
    return (int(match.group(1)), int(match.group(2)))
