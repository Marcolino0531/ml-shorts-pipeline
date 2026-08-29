"""Gerador oficial de link (Central de Afiliados) e o fallback na tag manual."""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import AbstractContextManager, contextmanager
from pathlib import Path

import pytest
from playwright.sync_api import Error as PlaywrightError

from mlshorts.config import AffiliateConfig
from mlshorts.publish.affiliate import AffiliateLinkBuilder, LinkCache, LinkPage, PageFactory

PERMALINK = "https://produto.mercadolivre.com.br/MLB-123-pote-hermetico"
SHORT_LINK = "https://mercadolivre.com/sec/2AbC3dE"
GENERATOR_URL = "https://www.mercadolivre.com.br/afiliados/linkbuilder"


class FakePage:
    """Pagina de mentira com o campo, o botao e o link aparecendo depois de N leituras."""

    def __init__(self, *, renders_before_link: int = 0, url: str = GENERATOR_URL) -> None:
        self.url = url
        self.renders_before_link = renders_before_link
        self.visited: list[str] = []
        self.filled: list[tuple[str, str]] = []
        self.clicked: list[str] = []
        self.waits = 0
        self._reads = 0

    def goto(self, url: str) -> None:
        self.visited.append(url)

    def fill(self, selector: str, value: str) -> None:
        self.filled.append((selector, value))

    def click(self, selector: str) -> None:
        self.clicked.append(selector)

    def query_selector(self, selector: str) -> object | None:
        present = {"textarea[name='urls']", "button:has-text('Gerar')"}
        return object() if selector in present else None

    def wait_for_timeout(self, timeout: float) -> None:
        self.waits += 1

    def content(self) -> str:
        self._reads += 1
        if self._reads > self.renders_before_link:
            return f"<div class='result'><a href='{SHORT_LINK}'>{SHORT_LINK}</a></div>"
        return "<div class='result'>Gerando...</div>"


def factory_for(page: LinkPage) -> PageFactory:
    @contextmanager
    def open_page() -> Iterator[LinkPage]:
        yield page

    return open_page


def make_config(tmp_path: Path, **overrides: object) -> AffiliateConfig:
    defaults: dict[str, object] = {
        "generator_url": GENERATOR_URL,
        "session_state_path": str(tmp_path / "ml_session.json"),
        "cache_path": str(tmp_path / "affiliate-links.json"),
        "timeout_ms": 2000,
    }
    defaults.update(overrides)
    return AffiliateConfig(**defaults)  # type: ignore[arg-type]


def with_session(tmp_path: Path, **overrides: object) -> AffiliateConfig:
    config = make_config(tmp_path, **overrides)
    Path(config.session_state_path).write_text('{"cookies": []}', encoding="utf-8")
    return config


def test_gera_link_curto_colando_o_permalink_no_campo(tmp_path: Path) -> None:
    builder = AffiliateLinkBuilder(make_config(tmp_path))
    page = FakePage(renders_before_link=1)

    short_link = builder.generate_on_page(page, PERMALINK)

    assert short_link == SHORT_LINK
    assert page.visited == [GENERATOR_URL]
    assert page.filled == [("textarea[name='urls']", PERMALINK)]
    assert page.clicked == ["button:has-text('Gerar')"]
    assert page.waits == 1


def test_link_que_nunca_aparece_estoura_o_timeout(tmp_path: Path) -> None:
    builder = AffiliateLinkBuilder(make_config(tmp_path, timeout_ms=1000))
    page = FakePage(renders_before_link=1000)

    with pytest.raises(RuntimeError, match="nenhum link curto"):
        builder.generate_on_page(page, PERMALINK)


def test_tela_de_login_no_meio_do_fluxo_marca_sessao_expirada(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    page = FakePage(url="https://www.mercadolivre.com/jms/mlb/lgz/login?go=afiliados")
    builder = AffiliateLinkBuilder(with_session(tmp_path), page_factory=factory_for(page))

    with caplog.at_level(logging.WARNING):
        assert builder.short_link(PERMALINK) is None
        # sessao invalida: nao adianta abrir o navegador de novo no mesmo processo
        assert builder.short_link(PERMALINK) is None

    assert "expirou" in caplog.text
    assert "affiliate-login" in caplog.text
    assert page.visited == [GENERATOR_URL]


def test_sem_sessao_salva_avisa_e_devolve_none(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    builder = AffiliateLinkBuilder(make_config(tmp_path))

    with caplog.at_level(logging.WARNING):
        assert builder.short_link(PERMALINK) is None

    assert "affiliate-login" in caplog.text


def test_falha_do_playwright_devolve_none_sem_quebrar(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    def explode() -> AbstractContextManager[LinkPage]:
        raise PlaywrightError("Executable doesn't exist")

    builder = AffiliateLinkBuilder(with_session(tmp_path), page_factory=explode)

    with caplog.at_level(logging.WARNING):
        assert builder.short_link(PERMALINK) is None

    assert "tag manual" in caplog.text


def test_cache_evita_gerar_o_mesmo_link_duas_vezes(tmp_path: Path) -> None:
    config = with_session(tmp_path)
    cache = LinkCache(Path(config.cache_path))
    cache.put(PERMALINK, SHORT_LINK)

    def no_browser() -> AbstractContextManager[LinkPage]:
        raise AssertionError("nao deveria abrir o navegador com o link em cache")

    builder = AffiliateLinkBuilder(config, page_factory=no_browser)

    assert builder.short_link(PERMALINK) == SHORT_LINK
    # cache lido do disco por uma instancia nova: nada de navegador
    assert LinkCache(Path(config.cache_path)).get(PERMALINK) == SHORT_LINK


def test_desabilitado_nao_tenta_a_central(tmp_path: Path) -> None:
    builder = AffiliateLinkBuilder(with_session(tmp_path, enabled=False))

    assert builder.short_link(PERMALINK) is None
