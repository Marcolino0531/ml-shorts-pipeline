"""Coletor de fallback via Playwright, para quando nao ha credenciais de API.

Le a vitrine "Mais vendidos" da categoria e, em cada produto, extrai nota, quantidade de
avaliacoes, especificacoes da ficha tecnica e comentarios positivos.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from playwright.sync_api import Page, sync_playwright
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from pydantic import HttpUrl

from mlshorts.models import Product, ProductImage, Review

logger = logging.getLogger(__name__)

SITE_URL = "https://www.mercadolivre.com.br"
_INT_RE = re.compile(r"\d+")
_THUMB_SUFFIX_RE = re.compile(r"-[A-Z]\.(jpg|webp|png)$", re.IGNORECASE)


class MercadoLivreScraperCollector:
    """Coleta produtos em alta sem API, navegando pelo site publico."""

    name = "mercadolivre-scraper"

    def __init__(
        self,
        headless: bool = True,
        max_reviews: int = 8,
        max_images: int = 5,
        timeout_ms: int = 30_000,
    ) -> None:
        self.headless = headless
        self.max_reviews = max_reviews
        self.max_images = max_images
        self.timeout_ms = timeout_ms

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
        with self._page() as page:
            links = self._top_selling_links(page, category_id, limit)
            products: list[Product] = []
            for link in links:
                try:
                    products.append(self._scrape_product(page, link, category_id))
                except PlaywrightTimeoutError:
                    logger.warning("Timeout ao coletar %s", link)
            return products

    def _top_selling_links(self, page: Page, category_id: str, limit: int) -> list[str]:
        page.goto(f"{SITE_URL}/mais-vendidos/{category_id}", wait_until="domcontentloaded")
        anchors = page.locator("a.poly-component__title, a.ui-search-item__group__element")
        links: list[str] = []
        for index in range(min(anchors.count(), limit * 2)):
            href = anchors.nth(index).get_attribute("href")
            if href and href not in links:
                links.append(href.split("#")[0])
            if len(links) >= limit:
                break
        return links

    def _scrape_product(self, page: Page, url: str, category_id: str) -> Product:
        page.goto(url, wait_until="domcontentloaded")
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
            id=_extract_item_id(url) or url,
            title=title or "sem titulo",
            permalink=HttpUrl(url),
            category_id=category_id,
            price=price or 0.0,
            sold_quantity=sold_quantity or 0,
            rating=rating,
            reviews_total=reviews_total or 0,
            attributes=self._scrape_specs(page),
            images=self._scrape_images(page),
            positive_reviews=self._scrape_reviews(page),
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

    def _scrape_images(self, page: Page) -> list[ProductImage]:
        images: list[ProductImage] = []
        thumbs = page.locator(".ui-pdp-gallery__figure img, figure.ui-pdp-gallery__figure img")
        seen: set[str] = set()
        for index in range(thumbs.count()):
            src = thumbs.nth(index).get_attribute("src") or ""
            if not src.startswith("http"):
                continue
            hi_res = _to_high_resolution(src)
            if hi_res in seen:
                continue
            seen.add(hi_res)
            images.append(ProductImage(id=f"img-{index}", url=HttpUrl(hi_res)))
            if len(images) >= self.max_images:
                break
        return images

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


def _extract_item_id(url: str) -> str | None:
    match = re.search(r"(ML[A-Z]-?\d+)", url)
    return match.group(1).replace("-", "") if match else None


def _to_high_resolution(url: str) -> str:
    """Troca o sufixo de thumbnail (-I, -V, -O) pela variante em alta resolucao (-F)."""
    return _THUMB_SUFFIX_RE.sub(lambda m: f"-F.{m.group(1)}", url)


def scrape_product_payload(url: str, headless: bool = True) -> dict[str, Any]:
    """Helper de depuracao: retorna um unico produto como dict."""
    collector = MercadoLivreScraperCollector(headless=headless)
    with collector._page() as page:
        return collector._scrape_product(page, url, category_id="").model_dump(mode="json")
