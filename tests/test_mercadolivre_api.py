from __future__ import annotations

import httpx
import pytest
import respx

from mlshorts.collectors.base import CollectorError
from mlshorts.collectors.mercadolivre_api import API_BASE, MercadoLivreAPICollector
from mlshorts.collectors.ml_auth import MercadoLivreAuth
from mlshorts.config import Secrets

SECRETS = Secrets(
    ml_client_id="id",
    ml_client_secret="secret",
    ml_refresh_token="refresh-1",
    ml_site_id="MLB",
)

ITEM_BODY = {
    "id": "MLB123",
    "title": "Fone Bluetooth",
    "permalink": "https://produto.mercadolivre.com.br/MLB-123",
    "category_id": "MLB1051",
    "price": 199.9,
    "currency_id": "BRL",
    "sold_quantity": 5000,
    "shipping": {"free_shipping": True},
    "attributes": [{"id": "BRAND", "name": "Marca", "value_name": "JBL"}],
    "pictures": [
        {"id": "p1", "secure_url": "https://http2.mlstatic.com/p1.jpg", "max_size": "1200x1200"}
    ],
    "reviews": {"rating_average": 4.7, "total": 812},
}


def search_page(ids: list[str], total: int, sort: str = "sold_quantity_desc") -> dict:
    return {
        "sort": {"id": sort},
        "available_sorts": [{"id": "relevance"}, {"id": "price_asc"}],
        "paging": {"total": total, "limit": len(ids)},
        "results": [
            {"id": item_id, "price": 189.9, "sold_quantity": 940, "seller": {"id": 1}}
            for item_id in ids
        ],
    }


@pytest.fixture
def collector():
    with httpx.Client(base_url=API_BASE) as client:
        yield MercadoLivreAPICollector(
            secrets=SECRETS,
            client=client,
            # nao deixa o teste reescrever o .env do projeto na rotacao do refresh token
            auth=MercadoLivreAuth(SECRETS, persist_refresh_token=False),
        )


@respx.mock
def test_collect_category_normaliza_produto(collector):
    respx.post(f"{API_BASE}/oauth/token").mock(
        return_value=httpx.Response(200, json={"access_token": "tok", "expires_in": 3600})
    )
    respx.get(f"{API_BASE}/sites/MLB/search").mock(
        return_value=httpx.Response(200, json=search_page([], total=0))
    )
    respx.get(f"{API_BASE}/highlights/MLB/category/MLB1051").mock(
        return_value=httpx.Response(
            200, json={"content": [{"id": "MLB123", "type": "ITEM"}, {"type": "PRODUCT"}]}
        )
    )
    respx.get(f"{API_BASE}/items").mock(
        return_value=httpx.Response(200, json=[{"code": 200, "body": ITEM_BODY}])
    )
    respx.get(f"{API_BASE}/items/MLB123/description").mock(
        return_value=httpx.Response(200, json={"plain_text": "Fone com cancelamento de ruido"})
    )
    respx.get(f"{API_BASE}/reviews/item/MLB123").mock(
        return_value=httpx.Response(
            200,
            json={
                "reviews": [
                    {"id": 1, "rate": 5, "content": "Excelente", "likes": 10},
                    {"id": 2, "rate": 2, "content": "Ruim", "likes": 30},
                    {"id": 3, "rate": 4, "content": "Bom custo", "likes": 3},
                ]
            },
        )
    )

    products = collector.collect_category("MLB1051", limit=10)

    assert len(products) == 1
    product = products[0]
    assert product.brand == "JBL"
    assert product.rating == 4.7
    assert product.reviews_total == 812
    assert product.images[0].width == 1200
    assert product.attributes["descricao"] == "Fone com cancelamento de ruido"
    assert [review.content for review in product.positive_reviews] == ["Excelente", "Bom custo"]


@respx.mock
def test_busca_por_categoria_pagina_e_devolve_anuncios(collector):
    """Fonte principal: a busca devolve anuncios direto, sem passar por catalogo/buy box."""
    respx.post(f"{API_BASE}/oauth/token").mock(
        return_value=httpx.Response(200, json={"access_token": "tok", "expires_in": 3600})
    )
    search = respx.get(f"{API_BASE}/sites/MLB/search").mock(
        side_effect=[
            httpx.Response(
                200, json=search_page([f"MLB{index}" for index in range(50)], total=500)
            ),
            httpx.Response(200, json=search_page(["MLB50", "MLB51"], total=500)),
        ]
    )

    item_ids = collector.search_item_ids("MLB1618", limit=52)

    assert item_ids[:2] == ["MLB0", "MLB1"]
    assert len(item_ids) == 52
    first, second = (call.request.url.params for call in search.calls)
    assert (first["sort"], first["limit"], first["offset"]) == ("sold_quantity_desc", "50", "0")
    # segunda pagina pede apenas o que falta para o limite
    assert (second["limit"], second["offset"]) == ("2", "50")
    assert first["category"] == "MLB1618"


@respx.mock
def test_busca_para_quando_a_categoria_acaba(collector):
    respx.post(f"{API_BASE}/oauth/token").mock(
        return_value=httpx.Response(200, json={"access_token": "tok", "expires_in": 3600})
    )
    search = respx.get(f"{API_BASE}/sites/MLB/search").mock(
        return_value=httpx.Response(200, json=search_page(["MLB1", "MLB2"], total=2))
    )

    assert collector.search_item_ids("MLB1618", limit=50) == ["MLB1", "MLB2"]
    assert search.call_count == 1


@respx.mock
def test_sort_recusado_cai_para_relevance(collector):
    respx.post(f"{API_BASE}/oauth/token").mock(
        return_value=httpx.Response(200, json={"access_token": "tok", "expires_in": 3600})
    )
    search = respx.get(f"{API_BASE}/sites/MLB/search").mock(
        side_effect=[
            httpx.Response(400, json={"message": "invalid sort"}),
            httpx.Response(200, json=search_page(["MLB1"], total=1, sort="relevance")),
        ]
    )

    assert collector.search_item_ids("MLB1618", limit=10) == ["MLB1"]
    assert [call.request.url.params["sort"] for call in search.calls] == [
        "sold_quantity_desc",
        "relevance",
    ]
    # a proxima categoria ja nasce com a ordenacao aceita
    assert collector.search_sort == "relevance"


@respx.mock
def test_sort_ignorado_silenciosamente_e_registrado(collector, caplog):
    respx.post(f"{API_BASE}/oauth/token").mock(
        return_value=httpx.Response(200, json={"access_token": "tok", "expires_in": 3600})
    )
    respx.get(f"{API_BASE}/sites/MLB/search").mock(
        return_value=httpx.Response(200, json=search_page(["MLB1"], total=1, sort="relevance"))
    )

    with caplog.at_level("WARNING"):
        assert collector.search_item_ids("MLB1618", limit=10) == ["MLB1"]

    assert "ignorou sort=sold_quantity_desc" in caplog.text
    assert collector.search_sort == "relevance"


@respx.mock
def test_collect_category_usa_a_busca_e_dispensa_o_catalogo(collector):
    respx.post(f"{API_BASE}/oauth/token").mock(
        return_value=httpx.Response(200, json={"access_token": "tok", "expires_in": 3600})
    )
    respx.get(f"{API_BASE}/sites/MLB/search").mock(
        return_value=httpx.Response(200, json=search_page(["MLB123"], total=1))
    )
    highlights = respx.get(f"{API_BASE}/highlights/MLB/category/MLB1618").mock(
        return_value=httpx.Response(200, json={"content": []})
    )
    respx.get(f"{API_BASE}/items").mock(
        return_value=httpx.Response(200, json=[{"code": 200, "body": ITEM_BODY}])
    )
    respx.get(f"{API_BASE}/items/MLB123/description").mock(
        return_value=httpx.Response(200, json={"plain_text": "Panela antiaderente"})
    )
    respx.get(f"{API_BASE}/reviews/item/MLB123").mock(
        return_value=httpx.Response(200, json={"reviews": []})
    )

    products = collector.collect_category("MLB1618", limit=10)

    assert [product.id for product in products] == ["MLB123"]
    assert not highlights.called


@respx.mock
def test_busca_vazia_cai_para_highlights(collector):
    respx.post(f"{API_BASE}/oauth/token").mock(
        return_value=httpx.Response(200, json={"access_token": "tok", "expires_in": 3600})
    )
    respx.get(f"{API_BASE}/sites/MLB/search").mock(
        return_value=httpx.Response(200, json=search_page([], total=0))
    )
    highlights = respx.get(f"{API_BASE}/highlights/MLB/category/MLB1618").mock(
        return_value=httpx.Response(200, json={"content": [{"id": "MLB123", "type": "ITEM"}]})
    )
    respx.get(f"{API_BASE}/items").mock(
        return_value=httpx.Response(200, json=[{"code": 200, "body": ITEM_BODY}])
    )
    respx.get(f"{API_BASE}/items/MLB123/description").mock(return_value=httpx.Response(404))
    respx.get(f"{API_BASE}/reviews/item/MLB123").mock(return_value=httpx.Response(404))

    assert [p.id for p in collector.collect_category("MLB1618", limit=10)] == ["MLB123"]
    assert highlights.called


@respx.mock
def test_categoria_de_catalogo_resolve_o_buy_box(collector):
    """MLB1618 (Cozinha) devolve quase so PRODUCT/USER_PRODUCT: sem o buy box, vinha vazia."""
    respx.post(f"{API_BASE}/oauth/token").mock(
        return_value=httpx.Response(200, json={"access_token": "tok", "expires_in": 3600})
    )
    respx.get(f"{API_BASE}/highlights/MLB/category/MLB1618").mock(
        return_value=httpx.Response(
            200,
            json={
                "content": [
                    {"id": "MLB20001", "type": "PRODUCT"},
                    {"id": "MLB20002", "type": "USER_PRODUCT"},
                    # mesmo anuncio vencedor do MLB20001: nao deve duplicar
                    {"id": "MLB20003", "type": "PRODUCT"},
                    {"id": "MLB20004", "type": "PRODUCT"},  # sem buy box e sem concorrentes
                    {"id": "MLB20005", "type": "PRODUCT"},  # 404
                    {"type": "PRODUCT"},  # entrada sem id
                    {"id": "MLB999", "type": "ITEM"},
                ]
            },
        )
    )
    respx.get(f"{API_BASE}/products/MLB20001").mock(
        return_value=httpx.Response(200, json={"buy_box_winner": {"item_id": "MLB111"}})
    )
    respx.get(f"{API_BASE}/products/MLB20002").mock(
        return_value=httpx.Response(200, json={"buy_box_winner": {"item_id": "MLB222"}})
    )
    respx.get(f"{API_BASE}/products/MLB20003").mock(
        return_value=httpx.Response(200, json={"buy_box_winner": {"item_id": "MLB111"}})
    )
    respx.get(f"{API_BASE}/products/MLB20004").mock(
        return_value=httpx.Response(200, json={"buy_box_winner": None})
    )
    respx.get(f"{API_BASE}/products/MLB20004/items").mock(
        return_value=httpx.Response(200, json={"results": []})
    )
    respx.get(f"{API_BASE}/products/MLB20005").mock(return_value=httpx.Response(404))
    respx.get(f"{API_BASE}/products/MLB20005/items").mock(return_value=httpx.Response(404))

    item_ids = collector.highlight_item_ids("MLB1618", limit=10)

    assert item_ids == ["MLB111", "MLB222", "MLB999"]


@respx.mock
def test_catalogo_sem_buy_box_usa_o_concorrente_mais_barato(collector):
    """`buy_box_winner` nulo e a regra, nao a excecao: `/products/{id}/items` salva o produto."""
    respx.post(f"{API_BASE}/oauth/token").mock(
        return_value=httpx.Response(200, json={"access_token": "tok", "expires_in": 3600})
    )
    respx.get(f"{API_BASE}/highlights/MLB/category/MLB1618").mock(
        return_value=httpx.Response(
            200,
            json={
                "content": [
                    {"id": "MLB6309815", "type": "PRODUCT"},
                    {"id": "MLB6309816", "type": "USER_PRODUCT"},
                ]
            },
        )
    )
    for product_id in ("MLB6309815", "MLB6309816"):
        respx.get(f"{API_BASE}/products/{product_id}").mock(
            return_value=httpx.Response(200, json={"buy_box_winner": None})
        )
    respx.get(f"{API_BASE}/products/MLB6309815/items").mock(
        return_value=httpx.Response(
            200,
            json={
                "results": [
                    {"item_id": "MLB777", "current_price": 149.9, "status": "active"},
                    {"item_id": "MLB666", "current_price": 129.9, "status": "active"},
                    {"item_id": "MLB555", "current_price": 99.9, "status": "paused"},
                ]
            },
        )
    )
    # unico anuncio, sem status: aproveitado do mesmo jeito
    respx.get(f"{API_BASE}/products/MLB6309816/items").mock(
        return_value=httpx.Response(200, json={"results": [{"item_id": "MLB888"}]})
    )

    assert collector.highlight_item_ids("MLB1618", limit=10) == ["MLB666", "MLB888"]


@respx.mock
def test_catalogo_sem_anuncio_disponivel_e_descartado(collector):
    respx.post(f"{API_BASE}/oauth/token").mock(
        return_value=httpx.Response(200, json={"access_token": "tok", "expires_in": 3600})
    )
    respx.get(f"{API_BASE}/highlights/MLB/category/MLB1618").mock(
        return_value=httpx.Response(
            200, json={"content": [{"id": "MLB6309815", "type": "PRODUCT"}]}
        )
    )
    respx.get(f"{API_BASE}/products/MLB6309815").mock(
        return_value=httpx.Response(200, json={"buy_box_winner": None})
    )
    respx.get(f"{API_BASE}/products/MLB6309815/items").mock(
        return_value=httpx.Response(
            200, json={"results": [{"item_id": "MLB555", "status": "closed"}]}
        )
    )

    assert collector.highlight_item_ids("MLB1618", limit=10) == []


@respx.mock
def test_collect_category_resolve_catalogo_sem_buy_box_ate_o_produto(collector):
    """Ponta a ponta do fallback: preco e vendas agregados vem do anuncio escolhido."""
    respx.post(f"{API_BASE}/oauth/token").mock(
        return_value=httpx.Response(200, json={"access_token": "tok", "expires_in": 3600})
    )
    respx.get(f"{API_BASE}/sites/MLB/search").mock(
        return_value=httpx.Response(200, json=search_page([], total=0))
    )
    respx.get(f"{API_BASE}/highlights/MLB/category/MLB1618").mock(
        return_value=httpx.Response(
            200, json={"content": [{"id": "MLB6309815", "type": "PRODUCT"}]}
        )
    )
    respx.get(f"{API_BASE}/products/MLB6309815").mock(
        return_value=httpx.Response(200, json={"buy_box_winner": None})
    )
    respx.get(f"{API_BASE}/products/MLB6309815/items").mock(
        return_value=httpx.Response(
            200,
            json={
                "results": [
                    {"item_id": "MLB999", "current_price": 259.9, "status": "active"},
                    {"item_id": "MLB123", "current_price": 199.9, "status": "active"},
                ]
            },
        )
    )
    items = respx.get(f"{API_BASE}/items").mock(
        return_value=httpx.Response(200, json=[{"code": 200, "body": ITEM_BODY}])
    )
    respx.get(f"{API_BASE}/items/MLB123/description").mock(
        return_value=httpx.Response(200, json={"plain_text": "Panela antiaderente"})
    )
    respx.get(f"{API_BASE}/reviews/item/MLB123").mock(
        return_value=httpx.Response(200, json={"reviews": []})
    )

    products = collector.collect_category("MLB1618", limit=10)

    assert items.calls[0].request.url.params["ids"] == "MLB123"
    assert [(product.id, product.price, product.sold_quantity) for product in products] == [
        ("MLB123", 199.9, 5000)
    ]


@respx.mock
def test_limite_para_de_resolver_o_catalogo_ao_atingir_o_maximo(collector):
    respx.post(f"{API_BASE}/oauth/token").mock(
        return_value=httpx.Response(200, json={"access_token": "tok", "expires_in": 3600})
    )
    respx.get(f"{API_BASE}/highlights/MLB/category/MLB1618").mock(
        return_value=httpx.Response(
            200,
            json={"content": [{"id": f"MLB2000{index}", "type": "PRODUCT"} for index in range(5)]},
        )
    )
    routes = [
        respx.get(f"{API_BASE}/products/MLB2000{index}").mock(
            return_value=httpx.Response(200, json={"buy_box_winner": {"item_id": f"MLB{index}"}})
        )
        for index in range(5)
    ]

    assert collector.highlight_item_ids("MLB1618", limit=2) == ["MLB0", "MLB1"]
    # nao gasta chamada de catalogo depois de completar o limite
    assert [route.called for route in routes] == [True, True, False, False, False]


@respx.mock
def test_collect_category_de_catalogo_traz_o_produto_completo(collector):
    respx.post(f"{API_BASE}/oauth/token").mock(
        return_value=httpx.Response(200, json={"access_token": "tok", "expires_in": 3600})
    )
    respx.get(f"{API_BASE}/sites/MLB/search").mock(
        return_value=httpx.Response(200, json=search_page([], total=0))
    )
    respx.get(f"{API_BASE}/highlights/MLB/category/MLB1618").mock(
        return_value=httpx.Response(200, json={"content": [{"id": "MLB20001", "type": "PRODUCT"}]})
    )
    respx.get(f"{API_BASE}/products/MLB20001").mock(
        return_value=httpx.Response(200, json={"buy_box_winner": {"item_id": "MLB123"}})
    )
    items = respx.get(f"{API_BASE}/items").mock(
        return_value=httpx.Response(200, json=[{"code": 200, "body": ITEM_BODY}])
    )
    respx.get(f"{API_BASE}/items/MLB123/description").mock(
        return_value=httpx.Response(200, json={"plain_text": "Panela antiaderente"})
    )
    respx.get(f"{API_BASE}/reviews/item/MLB123").mock(
        return_value=httpx.Response(200, json={"reviews": []})
    )

    products = collector.collect_category("MLB1618", limit=10)

    assert [product.id for product in products] == ["MLB123"]
    assert items.calls[0].request.url.params["ids"] == "MLB123"


@respx.mock
def test_sem_credenciais_falha_com_collector_error():
    collector = MercadoLivreAPICollector(secrets=Secrets())
    with pytest.raises(CollectorError):
        collector.highlight_item_ids("MLB1051", 5)


@respx.mock
def test_descricao_ausente_nao_quebra_a_coleta(collector):
    respx.post(f"{API_BASE}/oauth/token").mock(
        return_value=httpx.Response(200, json={"access_token": "tok", "expires_in": 3600})
    )
    respx.get(f"{API_BASE}/items/MLB123/description").mock(return_value=httpx.Response(404))
    assert collector.fetch_description("MLB123") == ""
