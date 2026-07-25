from __future__ import annotations

from mlshorts.collectors.filters import apply_filters, evaluate
from mlshorts.config import FilterConfig
from mlshorts.models import ProductImage

CONFIG = FilterConfig()


def test_aprova_produto_com_nota_e_volume_altos(product_factory):
    assert evaluate(product_factory(), CONFIG).approved


def test_reprova_nota_abaixo_do_minimo(product_factory):
    result = evaluate(product_factory(rating=4.4), CONFIG)
    assert not result.approved
    assert "nota 4.4 < 4.5" in result.reasons


def test_reprova_sem_volume_de_vendas(product_factory):
    result = evaluate(product_factory(sold_quantity=10), CONFIG)
    assert not result.approved
    assert any("vendas" in reason for reason in result.reasons)


def test_reprova_imagem_de_baixa_resolucao(product_factory):
    low_res = [ProductImage(id="p", url="https://http2.mlstatic.com/p.jpg", width=300, height=300)]
    result = evaluate(product_factory(images=low_res), CONFIG)
    assert not result.approved
    assert any("imagem" in reason for reason in result.reasons)


def test_ordena_por_vendas_e_respeita_limite(product_factory):
    products = [
        product_factory(id="a", sold_quantity=600),
        product_factory(id="b", sold_quantity=9000),
        product_factory(id="c", rating=3.0),
    ]
    selected = apply_filters(products, CONFIG, limit=2)
    assert [product.id for product in selected] == ["b", "a"]
