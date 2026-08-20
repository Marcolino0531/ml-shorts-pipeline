from __future__ import annotations

from mlshorts.collectors.stats import summarize_listings


def listing(price: object, sold: object = 0) -> dict[str, object]:
    return {"id": "MLB1", "price": price, "sold_quantity": sold}


def test_agrega_preco_e_quantidade_vendida():
    stats = summarize_listings(
        [
            listing(189.9, 940),
            listing(59.9, 12),
            listing(1299.0, 3),
        ]
    )

    assert stats.listings == 3
    assert stats.total_sold_quantity == 955
    # (189.90 + 59.90 + 1299.00) / 3
    assert stats.average_price == 516.27
    assert (stats.min_price, stats.max_price) == (59.9, 1299.0)
    # 189.90*940 + 59.90*12 + 1299.00*3 = 178506 + 718.80 + 3897
    assert stats.estimated_revenue == 183121.8


def test_soma_em_centavos_nao_acumula_erro_de_float():
    stats = summarize_listings([listing(0.07, 1) for _ in range(100)])

    # 0.07 * 100 em float daria 7.000000000000001
    assert stats.estimated_revenue == 7.0
    assert stats.average_price == 0.07


def test_ignora_precos_ausentes_ou_invalidos_sem_zerar_a_media():
    stats = summarize_listings(
        [
            listing(100.0, 2),
            listing(None, 5),
            listing("nao-numerico", 1),
            listing(-50.0, 4),
            {"id": "MLB9"},
        ]
    )

    # so o primeiro anuncio tem preco valido: a media nao e diluida pelos zeros
    assert stats.listings == 1
    assert stats.average_price == 100.0
    assert stats.estimated_revenue == 200.0
    # a quantidade vendida continua sendo somada mesmo sem preco
    assert stats.total_sold_quantity == 12


def test_preco_em_string_e_quantidade_fracionaria():
    stats = summarize_listings([listing("249.999", "10.9"), listing(True, True)])

    # arredonda para centavos e trunca a quantidade; booleanos nao contam como numero
    assert stats.average_price == 250.0
    assert stats.total_sold_quantity == 10
    assert stats.estimated_revenue == 2500.0


def test_agrega_concorrentes_do_catalogo_por_current_price():
    """`/products/{id}/items` nao tem `price`, e sim `current_price`."""
    stats = summarize_listings(
        [
            {"item_id": "MLB777", "current_price": 149.9, "sold_quantity": 10, "status": "active"},
            {"item_id": "MLB666", "current_price": 129.9, "status": "active"},
            {"item_id": "MLB555", "status": "closed"},
        ]
    )

    assert stats.listings == 2
    assert (stats.min_price, stats.max_price) == (129.9, 149.9)
    assert stats.average_price == 139.9
    assert stats.estimated_revenue == 1499.0


def test_lista_vazia():
    stats = summarize_listings([])

    assert (stats.listings, stats.total_sold_quantity) == (0, 0)
    assert (stats.average_price, stats.estimated_revenue) == (0.0, 0.0)


def test_linha_de_log_resume_os_valores():
    stats = summarize_listings([listing(189.9, 940), listing(59.9, 12)])

    assert stats.as_log_line() == (
        "2 anuncios, 952 vendidos, ticket medio R$ 124.90, faturamento estimado R$ 179224.80"
    )
