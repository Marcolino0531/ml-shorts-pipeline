from __future__ import annotations

import pytest

from mlshorts.collectors.mercadolivre_scraper import (
    _extract_item_id,
    _to_float,
    _to_high_resolution,
    _to_int,
)


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://http2.mlstatic.com/D_123-I.jpg", "https://http2.mlstatic.com/D_123-F.jpg"),
        ("https://http2.mlstatic.com/D_123-V.webp", "https://http2.mlstatic.com/D_123-F.webp"),
        ("https://http2.mlstatic.com/D_123-F.jpg", "https://http2.mlstatic.com/D_123-F.jpg"),
    ],
)
def test_converte_thumb_para_alta_resolucao(url, expected):
    assert _to_high_resolution(url) == expected


def test_extrai_id_do_item_da_url():
    assert _extract_item_id("https://produto.mercadolivre.com.br/MLB-4567-fone") == "MLB4567"
    assert _extract_item_id("https://www.mercadolivre.com.br/p/algum-produto") is None


def test_converte_numeros_em_formato_brasileiro():
    assert _to_int("+5.000 vendidos") == 5000
    assert _to_float("4,7") == 4.7
    assert _to_int("sem numero") is None
