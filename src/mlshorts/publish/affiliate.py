"""Link oficial de afiliado, gerado na Central de Afiliados com a sessao logada do Playwright.

O link montado a mao (`?matt_word=<tag>`) identifica a venda, mas nao entra no rastreio de cliques
do painel do ML — so o link curto (`mercadolivre.com/sec/...`) devolvido pelo gerador da Central
conta. Aqui o gerador e automatizado no mesmo Playwright que ja raspa a vitrine: a sessao fica
salva em disco (`mlshorts affiliate-login` grava), cada permalink resolvido e cacheado e qualquer
falha (sessao expirada, layout novo, timeout) devolve `None` para quem chamou cair no `matt_word`.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager
from pathlib import Path
from typing import Protocol, TypeVar

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Page, sync_playwright
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from mlshorts.config import AffiliateConfig

logger = logging.getLogger(__name__)

_T = TypeVar("_T")

SHORT_LINK_RE = re.compile(r"https://(?:www\.)?mercadolivre\.com(?:\.br)?/sec/[A-Za-z0-9]+")
# a jornada de login do ML tem varios caminhos sob /lgz/ (/lgz/login, /lgz/msl/login/<blob>...)
LOGIN_URL_MARKERS = ("/lgz/", "/login")
POLL_INTERVAL_MS = 500
SESSION_SETTLE_MS = 2_000
# rotulos que a Central usa no campo e no botao; o primeiro que existir na pagina e usado
URL_FIELD_SELECTORS = (
    "textarea[name='urls']",
    "textarea",
    "input[name='url']",
    "input[type='url']",
    "input[type='text']",
)
GENERATE_BUTTON_SELECTORS = (
    "button:has-text('Gerar')",
    "button:has-text('Criar')",
    "button[type='submit']",
)


PageFactory = Callable[[], AbstractContextManager["LinkPage"]]


class LinkGenerator(Protocol):
    """Quem sabe transformar um permalink no link curto oficial (None = usar a tag manual)."""

    def short_link(self, permalink: str) -> str | None: ...


class SessionExpired(RuntimeError):
    """A Central respondeu com a tela de login: a sessao salva precisa ser refeita."""


class LinkPage(Protocol):
    """O pedaco da API do Playwright usado aqui (facilita testar sem subir navegador)."""

    @property
    def url(self) -> str: ...

    def goto(self, url: str) -> object | None: ...

    def fill(self, selector: str, value: str) -> None: ...

    def click(self, selector: str) -> None: ...

    def query_selector(self, selector: str) -> object | None: ...

    def wait_for_timeout(self, timeout: float) -> None: ...

    def content(self) -> str: ...


class LinkCache:
    """Permalink -> link curto ja gerado: nao gasta uma visita a Central por render."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._entries: dict[str, str] | None = None

    def _load(self) -> dict[str, str]:
        if self._entries is None:
            try:
                raw = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                raw = {}
            self._entries = {
                key: value for key, value in raw.items() if isinstance(value, str) and value
            }
        return self._entries

    def get(self, permalink: str) -> str | None:
        return self._load().get(permalink)

    def put(self, permalink: str, short_link: str) -> None:
        entries = self._load()
        entries[permalink] = short_link
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(entries, ensure_ascii=False, indent=2), encoding="utf-8")


class AffiliateLinkBuilder:
    """Gera o link curto oficial de cada permalink na Central de Afiliados."""

    def __init__(
        self,
        config: AffiliateConfig,
        cache: LinkCache | None = None,
        page_factory: PageFactory | None = None,
    ) -> None:
        self.config = config
        self.cache = cache or LinkCache(Path(config.cache_path))
        self.page_factory = page_factory or self._browser_page
        self._unavailable = False

    @property
    def session_file(self) -> Path:
        return Path(self.config.session_state_path)

    def short_link(self, permalink: str) -> str | None:
        """Link curto do permalink, ou `None` quando quem chamou deve usar a tag manual."""
        if not self.config.enabled or self._unavailable:
            return None
        cached = self.cache.get(permalink)
        if cached:
            return cached
        if not self.session_file.exists():
            logger.warning(
                "Sessao da Central de Afiliados ausente em %s: rode `mlshorts affiliate-login`. "
                "Publicando com a tag manual, sem rastreio de cliques.",
                self.session_file,
            )
            self._unavailable = True
            return None
        try:
            with self.page_factory() as page:
                short_link = self.generate_on_page(page, permalink)
        except SessionExpired:
            logger.warning(
                "Sessao da Central de Afiliados expirou: rode `mlshorts affiliate-login` de novo. "
                "Publicando com a tag manual, sem rastreio de cliques."
            )
            self._unavailable = True
            return None
        except (PlaywrightTimeoutError, PlaywrightError, RuntimeError) as exc:
            logger.warning("Gerador de link de afiliado falhou (%s): usando a tag manual", exc)
            return None
        self.cache.put(permalink, short_link)
        return short_link

    def generate_on_page(self, page: LinkPage, permalink: str) -> str:
        """Cola o permalink no gerador e devolve o `mercadolivre.com/sec/...` da resposta."""
        page.goto(self.config.generator_url)
        field = self.wait_for_generator(page, self.config.timeout_ms, abort_on_login=True)
        page.fill(field, permalink)
        page.click(_first_present(page, GENERATE_BUTTON_SELECTORS, "botao de gerar"))
        return self._await_short_link(page)

    def wait_for_generator(self, page: LinkPage, timeout_ms: int, *, abort_on_login: bool) -> str:
        """Espera o gerador na tela: fora do login **e** com o campo de URL presente.

        Nenhum dos dois sinais basta sozinho. A URL do gerador nao casa com `/lgz/login` nem
        antes do login (e o redirecionamento ainda nem aconteceu quando a espera comeca), e a
        propria tela de login tem campos de texto que casam com `URL_FIELD_SELECTORS`.

        Com `abort_on_login`, cair no login e sessao expirada; sem ele (login manual), e so
        esperar o usuario terminar.
        """
        waited = 0
        while True:
            if abort_on_login:
                self._ensure_logged_in(page)
            if not _is_login_url(page.url):
                selector = _while_navigating(
                    lambda: _first_present_or_none(page, URL_FIELD_SELECTORS), None
                )
                if selector is not None:
                    return selector
            if waited >= timeout_ms:
                raise RuntimeError("campo de URL do gerador nao apareceu na Central de Afiliados")
            page.wait_for_timeout(POLL_INTERVAL_MS)
            waited += POLL_INTERVAL_MS

    def _await_short_link(self, page: LinkPage) -> str:
        """A Central preenche o link por JS depois do clique: le o HTML ate ele aparecer."""
        deadline = self.config.timeout_ms
        waited = 0
        while True:
            self._ensure_logged_in(page)
            html = _while_navigating(page.content, "")
            match = SHORT_LINK_RE.search(html)
            if match is not None:
                return match.group(0)
            if waited >= deadline:
                raise RuntimeError("a Central nao devolveu nenhum link curto")
            page.wait_for_timeout(POLL_INTERVAL_MS)
            waited += POLL_INTERVAL_MS

    def _ensure_logged_in(self, page: LinkPage) -> None:
        if _is_login_url(page.url):
            raise SessionExpired(page.url)

    @contextmanager
    def _browser_page(self) -> Iterator[Page]:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=self.config.headless)
            context = browser.new_context(
                locale="pt-BR",
                storage_state=str(self.session_file),
                viewport={"width": 1440, "height": 900},
            )
            page = context.new_page()
            page.set_default_timeout(self.config.timeout_ms)
            try:
                yield page
            finally:
                context.close()
                browser.close()

    def save_session(self, login_timeout_ms: int) -> Path:
        """Abre a Central com janela visivel e grava os cookies assim que o login concluir."""
        target = self.session_file
        target.parent.mkdir(parents=True, exist_ok=True)
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=False)
            context = browser.new_context(locale="pt-BR", viewport={"width": 1440, "height": 900})
            page = context.new_page()
            page.goto(self.config.generator_url)
            # a janela fica aberta esperando o login manual: o gerador na tela e o sinal de pronto
            self.wait_for_generator(page, login_timeout_ms, abort_on_login=False)
            page.wait_for_timeout(SESSION_SETTLE_MS)  # deixa os ultimos cookies do login assentarem
            context.storage_state(path=str(target))
            context.close()
            browser.close()
        return target


def _while_navigating(read: Callable[[], _T], pending: _T) -> _T:
    """Le a pagina tolerando navegacao no meio da leitura.

    Enquanto o login redireciona, o Playwright derruba o contexto de execucao no meio do
    `query_selector`/`content`; isso e "ainda nao pronto", nao falha do comando.
    """
    try:
        return read()
    except PlaywrightError as exc:
        logger.debug("leitura durante navegacao ignorada: %s", exc)
        return pending


def _is_login_url(url: str) -> bool:
    """A tela de login tambem tem campos de texto, entao ela precisa ser reconhecida pela URL."""
    return any(marker in url for marker in LOGIN_URL_MARKERS)


def _first_present_or_none(page: LinkPage, selectors: tuple[str, ...]) -> str | None:
    for selector in selectors:
        if page.query_selector(selector) is not None:
            return selector
    return None


def _first_present(page: LinkPage, selectors: tuple[str, ...], what: str) -> str:
    selector = _first_present_or_none(page, selectors)
    if selector is None:
        raise RuntimeError(f"{what} nao encontrado na Central de Afiliados")
    return selector
