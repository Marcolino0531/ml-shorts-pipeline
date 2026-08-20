from __future__ import annotations

import httpx
import pytest
import respx

from mlshorts.collectors.base import CollectorError
from mlshorts.collectors.mercadolivre_api import API_BASE, MercadoLivreAPICollector
from mlshorts.collectors.ml_auth import MercadoLivreAuth
from mlshorts.config import Secrets
from mlshorts.env_file import set_env_value

TOKEN_URL = f"{API_BASE}/oauth/token"


def secrets(refresh_token: str | None = "refresh-1") -> Secrets:
    return Secrets(
        ml_client_id="id",
        ml_client_secret="secret",
        ml_refresh_token=refresh_token,
        ml_site_id="MLB",
    )


def token_response(access: str, refresh: str | None, expires_in: int = 3600) -> httpx.Response:
    payload: dict[str, object] = {"access_token": access, "expires_in": expires_in}
    if refresh is not None:
        payload["refresh_token"] = refresh
    return httpx.Response(200, json=payload)


@pytest.fixture
def env_file(tmp_path):
    path = tmp_path / ".env"
    path.write_text(
        "# Mercado Livre\nML_CLIENT_ID=id\nML_REFRESH_TOKEN=refresh-1\nML_SITE_ID=MLB\n",
        encoding="utf-8",
    )
    return path


@pytest.fixture
def client():
    with httpx.Client(base_url=API_BASE) as http_client:
        yield http_client


@respx.mock
def test_usa_grant_refresh_token_e_persiste_o_token_novo(client, env_file):
    route = respx.post(TOKEN_URL).mock(return_value=token_response("access-1", "refresh-2"))
    auth = MercadoLivreAuth(secrets(), env_file=env_file)

    assert auth.access_token(client) == "access-1"

    sent = dict(httpx.QueryParams(route.calls[0].request.content.decode()))
    assert sent == {
        "grant_type": "refresh_token",
        "client_id": "id",
        "client_secret": "secret",
        "refresh_token": "refresh-1",
    }
    assert auth.refresh_token == "refresh-2"
    # o token antigo foi invalidado na troca: o novo tem de sobreviver ao processo
    assert "ML_REFRESH_TOKEN=refresh-2" in env_file.read_text(encoding="utf-8")
    assert "ML_REFRESH_TOKEN=refresh-1" not in env_file.read_text(encoding="utf-8")


@respx.mock
def test_reusa_o_access_token_enquanto_valido_e_renova_com_o_ultimo_refresh(client, env_file):
    route = respx.post(TOKEN_URL).mock(
        side_effect=[
            token_response("access-1", "refresh-2"),
            token_response("access-2", "refresh-3"),
        ]
    )
    auth = MercadoLivreAuth(secrets(), env_file=env_file)

    assert auth.access_token(client) == "access-1"
    assert auth.access_token(client) == "access-1"
    assert route.call_count == 1

    # expiracao forcada: a segunda troca precisa usar o refresh token rotacionado
    auth.expires_at = 0.0
    assert auth.access_token(client) == "access-2"
    segundo = dict(httpx.QueryParams(route.calls[1].request.content.decode()))
    assert segundo["refresh_token"] == "refresh-2"
    assert "ML_REFRESH_TOKEN=refresh-3" in env_file.read_text(encoding="utf-8")


@respx.mock
def test_token_expirado_pela_margem_de_seguranca(client, env_file):
    route = respx.post(TOKEN_URL).mock(
        side_effect=[
            token_response("access-1", "refresh-2", expires_in=30),
            token_response("access-2", "refresh-3"),
        ]
    )
    auth = MercadoLivreAuth(secrets(), env_file=env_file)

    # expires_in menor que a margem de 60s: nao vale reaproveitar
    assert auth.access_token(client) == "access-1"
    assert auth.access_token(client) == "access-2"
    assert route.call_count == 2


@respx.mock
def test_resposta_sem_refresh_token_mantem_o_atual(client, env_file):
    respx.post(TOKEN_URL).mock(return_value=token_response("access-1", None))
    auth = MercadoLivreAuth(secrets(), env_file=env_file)

    assert auth.access_token(client) == "access-1"
    assert auth.refresh_token == "refresh-1"
    assert "ML_REFRESH_TOKEN=refresh-1" in env_file.read_text(encoding="utf-8")


@respx.mock
def test_refresh_token_invalido_explica_o_que_fazer(client, env_file):
    respx.post(TOKEN_URL).mock(return_value=httpx.Response(400, json={"error": "invalid_grant"}))
    auth = MercadoLivreAuth(secrets(), env_file=env_file)

    with pytest.raises(CollectorError) as exc:
        auth.access_token(client)

    assert "invalid_grant" in str(exc.value)
    assert "consentimento" in str(exc.value)


@respx.mock
def test_sem_refresh_token_nao_chama_a_api(client):
    route = respx.post(TOKEN_URL)
    auth = MercadoLivreAuth(secrets(refresh_token=None))

    with pytest.raises(CollectorError, match="ML_REFRESH_TOKEN"):
        auth.access_token(client)
    assert not route.called


@respx.mock
def test_falha_ao_gravar_o_env_nao_derruba_a_coleta(client, env_file, caplog, monkeypatch):
    respx.post(TOKEN_URL).mock(return_value=token_response("access-1", "refresh-2"))

    def sem_permissao(*_args: object, **_kwargs: object) -> None:
        raise PermissionError("read-only file system")

    monkeypatch.setattr("mlshorts.collectors.ml_auth.set_env_value", sem_permissao)
    auth = MercadoLivreAuth(secrets(), env_file=env_file)

    with caplog.at_level("ERROR"):
        assert auth.access_token(client) == "access-1"

    assert auth.refresh_token == "refresh-2"
    assert "ML_REFRESH_TOKEN" in caplog.text


@respx.mock
def test_collector_autentica_por_refresh_token_e_agrega_o_resultado(env_file, caplog):
    """O coletor passa a depender do token de usuario, e os valores da busca sao somados."""
    respx.post(TOKEN_URL).mock(return_value=token_response("access-1", "refresh-2"))
    search = respx.get(f"{API_BASE}/sites/MLB/search").mock(
        return_value=httpx.Response(
            200,
            json={
                "sort": {"id": "sold_quantity_desc"},
                "paging": {"total": 2},
                "results": [
                    {"id": "MLB123", "price": 99.9, "sold_quantity": 10},
                    {"id": "MLB456", "price": 200.1, "sold_quantity": 5},
                ],
            },
        )
    )

    with httpx.Client(base_url=API_BASE) as http_client:
        collector = MercadoLivreAPICollector(
            secrets=secrets(),
            client=http_client,
            auth=MercadoLivreAuth(secrets(), env_file=env_file),
        )
        with caplog.at_level("INFO"):
            assert collector.search_item_ids("MLB1618", limit=5) == ["MLB123", "MLB456"]

    assert search.calls[0].request.headers["Authorization"] == "Bearer access-1"
    # 99.90*10 + 200.10*5 = 999 + 1000.50; ticket medio (99.90+200.10)/2
    assert "15 vendidos" in caplog.text
    assert "ticket medio R$ 150.00" in caplog.text
    assert "faturamento estimado R$ 1999.50" in caplog.text


def test_set_env_value_preserva_comentarios_e_outras_variaveis(env_file):
    set_env_value("ML_REFRESH_TOKEN", "refresh-9", env_file)
    set_env_value("NOVA_CHAVE", "valor com espaco", env_file)

    assert env_file.read_text(encoding="utf-8").splitlines() == [
        "# Mercado Livre",
        "ML_CLIENT_ID=id",
        "ML_REFRESH_TOKEN=refresh-9",
        "ML_SITE_ID=MLB",
        'NOVA_CHAVE="valor com espaco"',
    ]
    assert env_file.stat().st_mode & 0o777 == 0o600


def test_set_env_value_cria_arquivo_ausente(tmp_path):
    path = tmp_path / "novo" / ".env"

    set_env_value("ML_REFRESH_TOKEN", "refresh-1", path)

    assert path.read_text(encoding="utf-8") == "ML_REFRESH_TOKEN=refresh-1\n"


def test_variavel_exportada_no_ambiente_gera_aviso(env_file, monkeypatch, caplog):
    monkeypatch.setenv("ML_REFRESH_TOKEN", "refresh-antigo")

    with caplog.at_level("WARNING"):
        set_env_value("ML_REFRESH_TOKEN", "refresh-novo", env_file)

    assert "tambem esta definido no ambiente" in caplog.text
