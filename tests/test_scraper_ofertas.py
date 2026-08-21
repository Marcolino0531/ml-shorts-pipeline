"""Extracao dos cards de /ofertas, filtro de categoria e paginacao (sem browser real)."""

from __future__ import annotations

import httpx
import pytest
import respx

from mlshorts.collectors.mercadolivre_scraper import (
    OFFERS_PATH,
    MercadoLivreScraperCollector,
    _extract_item_id,
    _offer_card,
    _parse_offer_cards,
    _passes_category,
    _price_from_card,
    _rating_and_sold,
    _to_high_resolution,
)
from mlshorts.collectors.ml_categories import (
    CATEGORIES_URL,
    CategoryMatcher,
    fetch_category_matcher,
)

CARD_URL = (
    "https://www.mercadolivre.com.br/kit-10-potes-hermeticos/p/MLB53222689"
    "?pdp_filters=deal%3AMLB779362-1#polycard_client=offers&position=1"
    "&wid=MLB5574851656&sid=offers"
)


def card_payload(**overrides: str) -> dict[str, str]:
    payload = {
        "url": CARD_URL,
        "title": "Kit 10 Potes Hermeticos Vidro 640ml",
        "image": "https://http2.mlstatic.com/D_Q_NP_2X_942823-MLA113526261849_062026-AB.webp",
        "price": "93 reais com 90 centavos",
        "review": "4.9 | +250mil vendidos",
    }
    payload.update(overrides)
    return payload


class FakeLocator:
    def __init__(self, total: int) -> None:
        self._total = total

    def count(self) -> int:
        return self._total


class FakePage:
    """Simula o minimo da API do Playwright usado na leitura da vitrine."""

    def __init__(self, pages: list[list[dict[str, str]]]) -> None:
        self._pages = pages
        self.visited: list[str] = []
        self.scrolls = 0
        self._current: list[dict[str, str]] = []

    def goto(self, url: str, wait_until: str = "load") -> None:
        self.visited.append(url)
        index = len(self.visited) - 1
        self._current = self._pages[index] if index < len(self._pages) else []

    def evaluate(self, script: str, arg: object = None) -> list[dict[str, str]]:
        return self._current

    def locator(self, selector: str) -> FakeLocator:
        return FakeLocator(len(self._current))

    @property
    def mouse(self) -> FakePage:
        return self

    def wheel(self, delta_x: int, delta_y: int) -> None:
        self.scrolls += 1

    def wait_for_timeout(self, timeout: float) -> None:
        return None


def test_extrai_titulo_preco_imagem_e_id_do_anuncio():
    card = _offer_card(card_payload())

    assert card is not None
    # o link e de catalogo (/p/MLB53222689): o anuncio real esta no wid
    assert card.item_id == "MLB5574851656"
    assert card.title == "Kit 10 Potes Hermeticos Vidro 640ml"
    assert card.price == pytest.approx(93.90)
    assert card.rating == pytest.approx(4.9)
    assert card.sold_quantity == 250_000
    assert card.image_url is not None
    assert _to_high_resolution(card.image_url).endswith(
        "D_NQ_NP_2X_942823-MLA113526261849_062026-F.webp"
    )


def test_descarta_card_sem_link_ou_titulo():
    assert _offer_card(card_payload(url="")) is None
    assert _offer_card(card_payload(title="")) is None
    assert _offer_card(card_payload(url="https://www.mercadolivre.com.br/ofertas")) is None


def test_card_sem_avaliacao_nao_inventa_nota():
    card = _offer_card(card_payload(review=""))

    assert card is not None
    assert card.rating is None
    assert card.sold_quantity == 0


@pytest.mark.parametrize(
    ("review", "expected"),
    [
        ("4.9 | +250mil vendidos", (4.9, 250_000)),
        ("5 | +1mi vendidos", (5.0, 1_000_000)),
        ("4,7 | 1.250 vendidos", (4.7, 1250)),
        ("+500 vendidos", (None, 500)),
    ],
)
def test_le_nota_e_vendas_do_resumo_do_card(review, expected):
    assert _rating_and_sold(review) == expected


def test_le_preco_do_aria_label_com_milhar():
    assert _price_from_card("1.499 reais com 5 centavos") == pytest.approx(1499.05)
    assert _price_from_card("343 reais") == pytest.approx(343.0)


def test_extrai_id_de_anuncio_individual_sem_wid():
    assert _extract_item_id("https://produto.mercadolivre.com.br/MLB-4567-fone") == "MLB4567"


def test_filtro_de_categoria_descarta_outra_categoria():
    matcher = CategoryMatcher("MLB1618", frozenset({"cozinha", "utensilios de cozinha"}))

    assert _passes_category(["Mercado Livre", "Casa", "Cozinha", "Potes"], matcher) is True
    assert _passes_category(["Mercado Livre", "Utensílios de Cozinha"], matcher) is True
    assert _passes_category(["Mercado Livre", "Eletrônicos", "Smart TV"], matcher) is False


def test_sem_breadcrumb_ou_sem_categoria_resolvida_mantem_a_oferta():
    matcher = CategoryMatcher("MLB1618", frozenset({"cozinha"}))
    assert _passes_category([], matcher) is True

    nao_resolvida = CategoryMatcher("MLB1618", frozenset())
    assert _passes_category(["Eletrônicos"], nao_resolvida) is True


@respx.mock
def test_resolve_nomes_da_categoria_e_das_filhas():
    respx.get(f"{CATEGORIES_URL}/MLB1618").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": "MLB1618",
                "name": "Cozinha",
                "children_categories": [{"id": "MLB1619", "name": "Utensílios de Cozinha"}],
            },
        )
    )

    matcher = fetch_category_matcher("MLB1618")

    assert matcher.names == frozenset({"cozinha", "utensilios de cozinha"})


@respx.mock
def test_categoria_indisponivel_nao_derruba_a_coleta():
    respx.get(f"{CATEGORIES_URL}/MLB1618").mock(return_value=httpx.Response(500))

    matcher = fetch_category_matcher("MLB1618")

    assert matcher.names == frozenset()
    assert matcher.matches(["Qualquer"]) is True


def test_pagina_a_vitrine_ate_juntar_candidatos_suficientes():
    collector = MercadoLivreScraperCollector()
    primeira = [card_payload(url=CARD_URL.replace("MLB5574851656", f"MLB100{i}")) for i in range(2)]
    segunda = [card_payload(url=CARD_URL.replace("MLB5574851656", f"MLB200{i}")) for i in range(2)]
    page = FakePage([primeira, segunda])

    cards = collector._offer_cards(page, "MLB1618", wanted=4)

    assert [card.item_id for card in cards] == ["MLB1000", "MLB1001", "MLB2000", "MLB2001"]
    assert page.visited == [
        f"https://www.mercadolivre.com.br{OFFERS_PATH}?category=MLB1618&page=1",
        f"https://www.mercadolivre.com.br{OFFERS_PATH}?category=MLB1618&page=2",
    ]


def test_para_de_paginar_quando_nao_ha_oferta_nova():
    collector = MercadoLivreScraperCollector()
    repetida = [card_payload()]
    page = FakePage([repetida, repetida, repetida])

    cards = collector._offer_cards(page, "MLB1618", wanted=10)

    assert [card.item_id for card in cards] == ["MLB5574851656"]
    assert len(page.visited) == 2  # a segunda pagina nao trouxe nada novo


def test_ignora_cards_invalidos_ao_montar_a_lista():
    cards = _parse_offer_cards([card_payload(), card_payload(url=""), {}])

    assert len(cards) == 1
