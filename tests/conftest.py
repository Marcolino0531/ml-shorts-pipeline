from __future__ import annotations

import pytest

from mlshorts.models import Product, ProductImage


@pytest.fixture
def product_factory():
    def _make(**overrides: object) -> Product:
        data: dict[str, object] = {
            "id": "MLB123",
            "title": "Fone Bluetooth",
            "permalink": "https://produto.mercadolivre.com.br/MLB-123",
            "category_id": "MLB1051",
            "price": 199.9,
            "sold_quantity": 1500,
            "rating": 4.8,
            "reviews_total": 320,
            "images": [
                ProductImage(
                    id="p1", url="https://http2.mlstatic.com/p1.jpg", width=1200, height=1200
                )
            ],
        }
        data.update(overrides)
        return Product.model_validate(data)

    return _make
