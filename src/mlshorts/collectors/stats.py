"""Agregacao dos numeros que a busca do Mercado Livre devolve por categoria.

Serve para decidir/registrar se uma vitrine vale a pena antes de gastar chamadas de detalhe:
quantos anuncios, quanto se vende e qual o ticket medio. Os valores monetarios sao somados em
centavos inteiros (`Decimal`) para nao acumular erro de ponto flutuante em listas longas.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

CENTS = Decimal("0.01")


@dataclass(frozen=True)
class CategoryStats:
    """Resumo monetario de uma pagina de resultados da busca."""

    listings: int
    total_sold_quantity: int
    average_price: float
    min_price: float
    max_price: float
    estimated_revenue: float
    """Soma de `preco x quantidade vendida` — estimativa de faturamento, nao valor oficial."""

    def as_log_line(self) -> str:
        return (
            f"{self.listings} anuncios, {self.total_sold_quantity} vendidos, "
            f"ticket medio R$ {self.average_price:.2f}, "
            f"faturamento estimado R$ {self.estimated_revenue:.2f}"
        )


def _money(value: object) -> Decimal:
    """Converte para Decimal em centavos; valor ausente ou invalido vale zero."""
    if value is None or isinstance(value, bool):
        return Decimal(0)
    try:
        amount = Decimal(str(value))
    except (ArithmeticError, ValueError):
        return Decimal(0)
    if amount < 0:
        return Decimal(0)
    return amount.quantize(CENTS, rounding=ROUND_HALF_UP)


def _quantity(value: object) -> int:
    if value is None or isinstance(value, bool):
        return 0
    try:
        quantity = int(Decimal(str(value)))
    except (ArithmeticError, ValueError):
        return 0
    return max(quantity, 0)


def summarize_listings(results: Iterable[dict[str, Any]]) -> CategoryStats:
    """Agrega precos e unidades vendidas dos anuncios devolvidos pela busca."""
    prices: list[Decimal] = []
    total_sold = 0
    revenue = Decimal(0)

    for result in results:
        # `/sites/{site}/search` usa `price`; os concorrentes do catalogo, `current_price`
        price = _money(result.get("price")) or _money(result.get("current_price"))
        # a busca ora devolve sold_quantity, ora so o agregado em sale_price/seller
        sold = _quantity(result.get("sold_quantity"))
        if price > 0:
            prices.append(price)
        total_sold += sold
        revenue += price * sold

    if not prices:
        return CategoryStats(0, total_sold, 0.0, 0.0, 0.0, float(revenue))

    total = sum(prices, Decimal(0))
    average = (total / len(prices)).quantize(CENTS, rounding=ROUND_HALF_UP)
    return CategoryStats(
        listings=len(prices),
        total_sold_quantity=total_sold,
        average_price=float(average),
        min_price=float(min(prices)),
        max_price=float(max(prices)),
        estimated_revenue=float(revenue.quantize(CENTS, rounding=ROUND_HALF_UP)),
    )
