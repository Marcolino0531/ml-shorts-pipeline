from __future__ import annotations

import httpx
import respx

from mlshorts.collectors.images import download_product_images


@respx.mock
def test_baixa_imagens_e_preenche_local_path(tmp_path, product_factory):
    respx.get("https://http2.mlstatic.com/p1.jpg").mock(
        return_value=httpx.Response(200, content=b"binario", headers={"content-type": "image/jpeg"})
    )
    product = product_factory()

    paths = download_product_images(product, tmp_path)

    assert paths == [tmp_path / "MLB123" / "00.jpg"]
    assert paths[0].read_bytes() == b"binario"
    assert product.images[0].local_path == str(paths[0])


@respx.mock
def test_ignora_imagem_com_erro_http(tmp_path, product_factory):
    respx.get("https://http2.mlstatic.com/p1.jpg").mock(return_value=httpx.Response(500))
    assert download_product_images(product_factory(), tmp_path) == []
