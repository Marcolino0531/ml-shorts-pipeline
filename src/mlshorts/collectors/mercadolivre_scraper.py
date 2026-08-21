"""Coletor via Playwright, hoje a fonte viavel de descoberta de produtos.

A API oficial responde 403 em `/items` e `/sites/{site}/search` sem nivel de parceiro aprovado,
mas a vitrine publica `/ofertas` abre normalmente com User-Agent de navegador. O fluxo le os cards
de `/ofertas?category=<id>` (rolando a pagina e paginando ate juntar candidatos suficientes),
descarta as ofertas cujo caminho de categoria nao passa pela categoria configurada — a vitrine
mistura categorias — e completa cada anuncio com avaliacoes, ficha tecnica e comentarios.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

from playwright.sync_api import Page, sync_playwright
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from pydantic import HttpUrl

from mlshorts.collectors.ml_categories import CategoryMatcher, fetch_category_matcher
from mlshorts.models import Product, ProductImage, Review

logger = logging.getLogger(__name__)

SITE_URL = "https://www.mercadolivre.com.br"
OFFERS_PATH = "/ofertas"
# a vitrine mistura categorias e nem todo anuncio passa no filtro, entao busca-se folga
CANDIDATE_FACTOR = 3
MAX_OFFER_PAGES = 12
SCROLL_STEPS = 10
SCROLL_PIXELS = 2_000
SCROLL_PAUSE_MS = 400
# depois de tantas paginas de anuncio bloqueadas seguidas, para de tentar o enriquecimento
PDP_BLOCK_LIMIT = 3

_INT_RE = re.compile(r"\d+")
_THUMB_SUFFIX_RE = re.compile(r"-[A-Z]{1,2}\.(jpg|webp|png)$", re.IGNORECASE)
# a thumb do card (D_Q_NP_2X_...) tem 448px; a variante D_NQ_NP_2X_...-F passa de 1000px
_IMAGE_PREFIX_RE = re.compile(r"/D_[A-Z0-9_]*?(?=\d+-ML)")
_ITEM_ID_RE = re.compile(r"(ML[A-Z]-?\d+)")
_WID_RE = re.compile(r"[?&#]wid=(ML[A-Z]\d+)")
_PRICE_RE = re.compile(r"(\d+(?:\.\d{3})*)\s*rea(?:l|is)(?:\s*com\s*(\d{1,2})\s*centavo)?")
# a nota vem antes do "|" no resumo do card; ancorada para nao confundir com "250mil vendidos"
_RATING_RE = re.compile(r"^\D*(\d(?:[.,]\d)?)(?=\s|\||$)")
_SOLD_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*(mil|mi)?\s*(?:produtos\s*)?vendidos", re.IGNORECASE)

_CARDS_JS = """
() => Array.from(document.querySelectorAll('div.poly-card')).map((card) => {
  const link = card.querySelector('a.poly-component__title');
  const picture = card.querySelector('img.poly-component__picture');
  const price = card.querySelector('.poly-price__current .andes-money-amount');
  const review = card.querySelector('.poly-component__review-compacted');
  return {
    url: link ? link.href : '',
    title: link ? (link.textContent || '').trim() : '',
    image: picture
      ? (picture.getAttribute('src') || picture.getAttribute('data-src') || '')
      : '',
    price: price
      ? (price.getAttribute('aria-label') || (price.textContent || ''))
      : '',
    review: review ? (review.textContent || '').trim() : '',
  };
})
"""

_MEASURE_JS = """
(urls) => Promise.all(urls.map((url) => new Promise((resolve) => {
  const image = new Image();
  image.onload = () => resolve([image.naturalWidth, image.naturalHeight]);
  image.onerror = () => resolve([0, 0]);
  image.src = url;
})))
"""


@dataclass(frozen=True)
class OfferCard:
    """Dados que o proprio card da vitrine entrega, antes de abrir o anuncio."""

    item_id: str
    title: str
    url: str
    price: float
    image_url: str | None
    rating: float | None
    sold_quantity: int


class MercadoLivreScraperCollector:
    """Coleta produtos em oferta sem API, navegando pelo site publico."""

    name = "mercadolivre-scraper"

    def __init__(
        self,
        headless: bool = True,
        max_reviews: int = 8,
        max_images: int = 5,
        timeout_ms: int = 30_000,
        category_matcher: CategoryMatcher | None = None,
    ) -> None:
        self.headless = headless
        self.max_reviews = max_reviews
        self.max_images = max_images
        self.timeout_ms = timeout_ms
        self.category_matcher = category_matcher
        self._blocked_pdps = 0

    @contextmanager
    def _page(self) -> Iterator[Page]:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=self.headless)
            context = browser.new_context(
                locale="pt-BR",
                viewport={"width": 1440, "height": 900},
                user_agent=(
                    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/125.0 Safari/537.36"
                ),
            )
            page = context.new_page()
            page.set_default_timeout(self.timeout_ms)
            try:
                yield page
            finally:
                context.close()
                browser.close()

    def collect_category(self, category_id: str, limit: int) -> list[Product]:
        matcher = self.category_matcher or fetch_category_matcher(category_id)
        with self._page() as page:
            cards = self._offer_cards(page, category_id, limit * CANDIDATE_FACTOR)
            logger.info("Ofertas em %s: %d candidatas", category_id, len(cards))
            products: list[Product] = []
            for card in cards:
                try:
                    product = self._scrape_product(page, card, category_id, matcher)
                except PlaywrightTimeoutError:
                    logger.warning("Timeout ao coletar %s", card.url)
                    continue
                if product is not None:
                    products.append(product)
                if len(products) >= limit:
                    break
            return products

    def _offer_cards(self, page: Page, category_id: str, wanted: int) -> list[OfferCard]:
        """Cards da vitrine, rolando e paginando ate juntar `wanted` ofertas distintas."""
        cards: dict[str, OfferCard] = {}
        for page_number in range(1, MAX_OFFER_PAGES + 1):
            page.goto(
                f"{SITE_URL}{OFFERS_PATH}?category={category_id}&page={page_number}",
                wait_until="domcontentloaded",
            )
            self._load_lazy_cards(page)
            new_cards = 0
            for card in _parse_offer_cards(_card_payloads(page)):
                if card.item_id not in cards:
                    cards[card.item_id] = card
                    new_cards += 1
            if not new_cards or len(cards) >= wanted:
                break
        return list(cards.values())[:wanted]

    def _load_lazy_cards(self, page: Page) -> None:
        """A vitrine carrega os cards conforme a rolagem; para quando o total nao cresce mais."""
        previous = -1
        for _ in range(SCROLL_STEPS):
            total = page.locator("div.poly-card").count()
            if total == previous:
                return
            previous = total
            page.mouse.wheel(0, SCROLL_PIXELS)
            page.wait_for_timeout(SCROLL_PAUSE_MS)

    def _scrape_product(
        self, page: Page, card: OfferCard, category_id: str, matcher: CategoryMatcher
    ) -> Product | None:
        """Completa o card com os dados do anuncio; `None` se a oferta e de outra categoria.

        A pagina do anuncio costuma responder com a tela de seguranca (captcha) para IP de
        datacenter — nesse caso o card sozinho ja traz titulo, preco, nota, vendas e imagem, e o
        enriquecimento e abandonado depois de algumas tentativas seguidas sem sucesso.
        """
        if self._blocked_pdps >= PDP_BLOCK_LIMIT:
            return self._product_from_card(page, card, category_id)

        page.goto(card.url, wait_until="domcontentloaded")
        if page.locator("h1.ui-pdp-title").count() == 0:
            self._blocked_pdps += 1
            if self._blocked_pdps == PDP_BLOCK_LIMIT:
                logger.warning(
                    "Pagina de anuncio bloqueada %d vezes: seguindo apenas com os dados da vitrine",
                    self._blocked_pdps,
                )
            return self._product_from_card(page, card, category_id)

        self._blocked_pdps = 0
        breadcrumb = _breadcrumb(page)
        if not _passes_category(breadcrumb, matcher):
            logger.debug(
                "Oferta %s fora de %s (%s): descartada",
                card.item_id,
                category_id,
                " > ".join(breadcrumb),
            )
            return None

        title = _text(page, "h1.ui-pdp-title")
        rating = _to_float(
            _text(page, "#reviews_capability_v3 .ui-review-capability__rating__average")
        )
        reviews_total = _to_int(
            _text(page, ".ui-review-capability__header__amount, .total-opinion")
        )
        price = _to_float(_text(page, ".ui-pdp-price__second-line .andes-money-amount__fraction"))
        sold_quantity = _to_int(_text(page, ".ui-pdp-subtitle"))

        return Product(
            id=card.item_id,
            title=title or card.title,
            permalink=HttpUrl(card.url),
            category_id=category_id,
            price=price or card.price,
            sold_quantity=sold_quantity or card.sold_quantity,
            rating=rating if rating is not None else card.rating,
            reviews_total=reviews_total or 0,
            attributes=self._scrape_specs(page),
            images=self._scrape_images(page, card),
            positive_reviews=self._scrape_reviews(page),
            source=self.name,
        )

    def _product_from_card(self, page: Page, card: OfferCard, category_id: str) -> Product:
        """Produto so com o que a vitrine mostra (a categoria ja vem filtrada pela URL)."""
        return Product(
            id=card.item_id,
            title=card.title,
            permalink=HttpUrl(card.url),
            category_id=category_id,
            price=card.price,
            sold_quantity=card.sold_quantity,
            rating=card.rating,
            reviews_total=0,
            attributes={},
            images=self._scrape_images(page, card),
            positive_reviews=[],
            source=self.name,
        )

    def _scrape_specs(self, page: Page) -> dict[str, str]:
        specs: dict[str, str] = {}
        rows = page.locator(".andes-table__row")
        for index in range(min(rows.count(), 30)):
            row = rows.nth(index)
            key = (row.locator("th").first.text_content() or "").strip()
            value = (row.locator("td").first.text_content() or "").strip()
            if key and value:
                specs[key] = value
        return specs

    def _scrape_images(self, page: Page, card: OfferCard) -> list[ProductImage]:
        urls: list[str] = []
        if card.image_url:
            urls.append(_to_high_resolution(card.image_url))
        thumbs = page.locator(".ui-pdp-gallery__figure img")
        for index in range(thumbs.count()):
            src = thumbs.nth(index).get_attribute("src") or ""
            if not src.startswith("http"):
                continue
            hi_res = _to_high_resolution(src)
            if hi_res not in urls:
                urls.append(hi_res)
            if len(urls) >= self.max_images:
                break

        sizes = _measure_images(page, urls)
        return [
            ProductImage(id=f"img-{index}", url=HttpUrl(url), width=width, height=height)
            for index, (url, (width, height)) in enumerate(zip(urls, sizes, strict=True))
        ]

    def _scrape_reviews(self, page: Page) -> list[Review]:
        reviews: list[Review] = []
        comments = page.locator(
            "[data-testid='comment-component'], .ui-review-capability-comments__comment"
        )
        for index in range(comments.count()):
            comment = comments.nth(index)
            content = (comment.text_content() or "").strip()
            if not content:
                continue
            rate = _to_int(
                comment.locator("[data-testid='comment-rating']").first.get_attribute("aria-label")
                or ""
            )
            reviews.append(Review(id=f"scraped-{index}", rate=rate or 5, content=content, likes=0))
            if len(reviews) >= self.max_reviews:
                break
        return [review for review in reviews if review.rate >= 4]


def _card_payloads(page: Page) -> list[dict[str, str]]:
    """Le todos os cards em uma unica ida ao browser, ja como texto puro."""
    raw = page.evaluate(_CARDS_JS)
    if not isinstance(raw, list):
        return []
    payloads: list[dict[str, str]] = []
    for entry in raw:
        if isinstance(entry, dict):
            payloads.append({str(key): str(value or "") for key, value in entry.items()})
    return payloads


def _measure_images(page: Page, urls: list[str]) -> list[tuple[int, int]]:
    """Dimensoes reais das imagens (o filtro de largura minima depende disso)."""
    if not urls:
        return []
    try:
        raw = page.evaluate(_MEASURE_JS, urls)
    except PlaywrightTimeoutError:
        logger.debug("Nao foi possivel medir as imagens de %s", urls[0])
        return [(0, 0)] * len(urls)
    sizes: list[tuple[int, int]] = []
    for entry in raw if isinstance(raw, list) else []:
        if isinstance(entry, list) and len(entry) == 2:
            sizes.append((_as_int(entry[0]), _as_int(entry[1])))
        else:
            sizes.append((0, 0))
    sizes.extend([(0, 0)] * (len(urls) - len(sizes)))
    return sizes[: len(urls)]


def _as_int(value: object) -> int:
    return int(value) if isinstance(value, (int, float)) else 0


def _parse_offer_cards(payloads: list[dict[str, str]]) -> list[OfferCard]:
    cards: list[OfferCard] = []
    for payload in payloads:
        card = _offer_card(payload)
        if card is not None:
            cards.append(card)
    return cards


def _offer_card(payload: dict[str, str]) -> OfferCard | None:
    """Converte o card cru em `OfferCard`; `None` quando falta link, id ou titulo."""
    url = payload.get("url", "").strip()
    title = payload.get("title", "").strip()
    item_id = _extract_item_id(url)
    if not url or not title or not item_id:
        return None
    rating, sold_quantity = _rating_and_sold(payload.get("review", ""))
    image = payload.get("image", "").strip()
    return OfferCard(
        item_id=item_id,
        title=title,
        url=url,
        price=_price_from_card(payload.get("price", "")) or 0.0,
        image_url=image if image.startswith("http") else None,
        rating=rating,
        sold_quantity=sold_quantity,
    )


def _passes_category(breadcrumb: list[str], matcher: CategoryMatcher) -> bool:
    """Sem breadcrumb nao ha como conferir: mantem a oferta (a URL ja filtra a categoria)."""
    if not breadcrumb:
        return True
    return matcher.matches(breadcrumb)


def _breadcrumb(page: Page) -> list[str]:
    try:
        entries = page.locator(
            ".andes-breadcrumb__link, .andes-breadcrumb__item"
        ).all_text_contents()
    except PlaywrightTimeoutError:
        return []
    return [entry.strip() for entry in entries if entry.strip()]


def _text(page: Page, selector: str) -> str:
    locator = page.locator(selector).first
    try:
        return (locator.text_content(timeout=3_000) or "").strip()
    except PlaywrightTimeoutError:
        return ""


def _to_int(value: str) -> int | None:
    digits = "".join(_INT_RE.findall(value.replace(".", "")))
    return int(digits) if digits else None


def _to_float(value: str) -> float | None:
    cleaned = value.replace(".", "").replace(",", ".")
    match = re.search(r"\d+(\.\d+)?", cleaned)
    return float(match.group()) if match else None


def _price_from_card(value: str) -> float | None:
    """Le o preco do card, que vem como "93 reais com 90 centavos" no aria-label."""
    match = _PRICE_RE.search(value)
    if match:
        reais = int(match.group(1).replace(".", ""))
        centavos = int(match.group(2)) if match.group(2) else 0
        return reais + centavos / 100
    return _to_float(value)


def _rating_and_sold(value: str) -> tuple[float | None, int]:
    """Le "4.9 | +250mil vendidos" (nota e unidades) do resumo de avaliacoes do card."""
    rating: float | None = None
    rating_match = _RATING_RE.search(value)
    if rating_match:
        candidate = float(rating_match.group(1).replace(",", "."))
        if 0 < candidate <= 5:
            rating = candidate

    sold = 0
    sold_match = _SOLD_RE.search(value)
    if sold_match:
        amount = float(sold_match.group(1).replace(".", "").replace(",", "."))
        multiplier = {"mil": 1_000, "mi": 1_000_000}.get((sold_match.group(2) or "").lower(), 1)
        sold = int(amount * multiplier)
    return rating, sold


def _extract_item_id(url: str) -> str | None:
    """Prefere o `wid` (anuncio concreto) ao id de catalogo que aparece no caminho `/p/MLB...`."""
    wid = _WID_RE.search(url)
    if wid:
        return wid.group(1)
    match = _ITEM_ID_RE.search(url)
    return match.group(1).replace("-", "") if match else None


def _to_high_resolution(url: str) -> str:
    """Troca a thumb do card pela variante grande (prefixo D_NQ_NP_2X_ e sufixo -F)."""
    upgraded = _IMAGE_PREFIX_RE.sub("/D_NQ_NP_2X_", url)
    return _THUMB_SUFFIX_RE.sub(lambda m: f"-F.{m.group(1)}", upgraded)


def scrape_product_payload(url: str, headless: bool = True) -> dict[str, Any]:
    """Helper de depuracao: retorna um unico produto como dict."""
    collector = MercadoLivreScraperCollector(headless=headless)
    card = OfferCard(
        item_id=_extract_item_id(url) or url,
        title="",
        url=url,
        price=0.0,
        image_url=None,
        rating=None,
        sold_quantity=0,
    )
    with collector._page() as page:
        product = collector._scrape_product(
            page, card, category_id="", matcher=CategoryMatcher("", frozenset())
        )
    return product.model_dump(mode="json") if product else {}
