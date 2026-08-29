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
LOGIN_URL = "https://www.mercadolivre.com/jms/mlb/lgz/login?go=afiliados"


class FakePage:
    """Pagina de mentira com o campo, o botao e o link aparecendo depois de N leituras."""

    def __init__(
        self,
        *,
        renders_before_link: int = 0,
        polls_before_field: int = 0,
        waits_on_login: int = 0,
        navigations: int = 0,
        url: str = GENERATOR_URL,
    ) -> None:
        self.navigations = navigations
        self.url = LOGIN_URL if waits_on_login else url
        self.logged_in_url = url
        self.renders_before_link = renders_before_link
        self.polls_before_field = polls_before_field
        self.waits_on_login = waits_on_login
        self.visited: list[str] = []
        self.filled: list[tuple[str, str]] = []
        self.clicked: list[str] = []
        self.waits = 0
        self._reads = 0
        self._field_polls = 0

    def goto(self, url: str) -> None:
        self.visited.append(url)

    def fill(self, selector: str, value: str) -> None:
        self.filled.append((selector, value))

    def click(self, selector: str) -> None:
        self.clicked.append(selector)

    def _navigating(self) -> None:
        """Playwright derruba o contexto quando a pagina navega no meio da leitura."""
        if self.navigations > 0:
            self.navigations -= 1
            raise PlaywrightError(
                "Execution context was destroyed, most likely because of a navigation"
            )

    def query_selector(self, selector: str) -> object | None:
        self._navigating()
        if selector == "textarea[name='urls']":
            self._field_polls += 1
            return object() if self._field_polls > self.polls_before_field else None
        return object() if selector == "button:has-text('Gerar')" else None

    def wait_for_timeout(self, timeout: float) -> None:
        self.waits += 1
        if self.waits >= self.waits_on_login:
            self.url = self.logged_in_url

    def content(self) -> str:
        self._navigating()
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


def test_login_espera_o_gerador_aparecer_e_nao_so_a_url(tmp_path: Path) -> None:
    """`generator_url` nao casa com /lgz/login nem antes do login: quem manda e o campo na tela."""
    builder = AffiliateLinkBuilder(make_config(tmp_path))
    page = FakePage(polls_before_field=3)

    field = builder.wait_for_generator(page, 5000, abort_on_login=False)

    assert field == "textarea[name='urls']"
    assert page.waits == 3


def test_login_manual_espera_sair_da_tela_de_login(tmp_path: Path) -> None:
    """A tela de login tem campos de texto que casam com os seletores: URL tambem conta."""
    builder = AffiliateLinkBuilder(make_config(tmp_path))
    page = FakePage(waits_on_login=4)

    assert builder.wait_for_generator(page, 5000, abort_on_login=False)
    assert page.waits == 4


def test_navegacao_no_meio_da_checagem_nao_derruba_o_login(tmp_path: Path) -> None:
    """Redirecionamento pos-login destroi o contexto: e "ainda nao pronto", nao erro fatal."""
    builder = AffiliateLinkBuilder(make_config(tmp_path))
    page = FakePage(navigations=3)

    assert builder.wait_for_generator(page, 5000, abort_on_login=False)
    assert page.waits == 3


def test_navegacao_no_meio_da_espera_do_link_nao_derruba_a_geracao(tmp_path: Path) -> None:
    builder = AffiliateLinkBuilder(make_config(tmp_path))
    page = FakePage(navigations=2)

    assert builder.generate_on_page(page, PERMALINK) == SHORT_LINK


def test_reconhece_a_tela_de_login_com_url_ofuscada(tmp_path: Path) -> None:
    """O ML manda para /jms/mlb/lgz/msl/login/<blob>, que nao casa com '/lgz/login'."""
    page = FakePage(url="https://www.mercadolivre.com/jms/mlb/lgz/msl/login/H4sIAAAAAAAEA1VQ")
    builder = AffiliateLinkBuilder(with_session(tmp_path), page_factory=factory_for(page))

    assert builder.short_link(PERMALINK) is None


def test_login_que_nunca_conclui_estoura_o_timeout(tmp_path: Path) -> None:
    builder = AffiliateLinkBuilder(make_config(tmp_path))
    page = FakePage(waits_on_login=10_000)

    with pytest.raises(RuntimeError, match="campo de URL do gerador"):
        builder.wait_for_generator(page, 1000, abort_on_login=False)


def test_gerador_espera_a_pagina_carregar_antes_de_colar(tmp_path: Path) -> None:
    builder = AffiliateLinkBuilder(make_config(tmp_path))
    page = FakePage(polls_before_field=2)

    assert builder.generate_on_page(page, PERMALINK) == SHORT_LINK
    assert page.filled == [("textarea[name='urls']", PERMALINK)]


def test_desabilitado_nao_tenta_a_central(tmp_path: Path) -> None:
    builder = AffiliateLinkBuilder(with_session(tmp_path, enabled=False))

    assert builder.short_link(PERMALINK) is None
