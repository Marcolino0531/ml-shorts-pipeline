from __future__ import annotations

import base64
import json

import httpx
import pytest
import respx

from mlshorts.config import ImageGenConfig, Secrets, Settings
from mlshorts.models import SceneAudio, SceneRole, ScriptAudio
from mlshorts.storage.paths import Paths
from mlshorts.video.scene_images import (
    CHAT_URL,
    IMAGES_URL,
    SceneImageError,
    SceneImageGenerator,
    build_image_prompt,
    build_scene_image_generator,
)
from mlshorts.video.service import RenderService

VISUAL = "Corte seco mostrando pote de plastico velho manchado, zoom in rapido no problema"
PNG = b"\x89PNG\r\n\x1a\nfake"


def image_response() -> httpx.Response:
    return httpx.Response(200, json={"data": [{"b64_json": base64.b64encode(PNG).decode("ascii")}]})


def translation_response(text: str) -> httpx.Response:
    return httpx.Response(200, json={"choices": [{"message": {"content": text}}]})


def make_track(tmp_path, visuals: dict[SceneRole, str | None]) -> ScriptAudio:
    scenes: list[SceneAudio] = []
    cursor = 0.0
    for index, (role, visual) in enumerate(visuals.items()):
        audio = tmp_path / f"{index:02d}.mp3"
        audio.write_bytes(b"fake")
        scenes.append(
            SceneAudio(
                index=index,
                role=role,
                text=f"fala {index}",
                visual=visual,
                audio_path=str(audio),
                duration_seconds=3.0,
                start_seconds=round(cursor, 3),
            )
        )
        cursor += 3.0
    return ScriptAudio(
        product_id="MLB123",
        voice_id="voice",
        model_id="eleven_multilingual_v2",
        scenes=scenes,
    )


def test_prompt_junta_estilo_visual_e_restricoes():
    config = ImageGenConfig()

    prompt = build_image_prompt("  Close  no  produto\nna bancada ", config)

    assert prompt.startswith(config.style_prompt)
    # o visual entra normalizado, sem quebras nem espacos duplicados
    assert "Scene: Close no produto na bancada" in prompt
    assert prompt.endswith(config.negative_prompt)


def test_prompt_respeita_o_limite_de_caracteres():
    config = ImageGenConfig(max_prompt_chars=60)

    prompt = build_image_prompt(VISUAL, config)

    assert len(prompt) <= 60


def test_prompt_recusa_visual_vazio():
    with pytest.raises(SceneImageError):
        build_image_prompt("   ", ImageGenConfig())


@respx.mock
def test_visual_e_traduzido_para_ingles_antes_de_gerar(tmp_path):
    traducao = "Hard cut on a stained old plastic container, fast zoom in on the problem"
    chat = respx.post(CHAT_URL).mock(return_value=translation_response(traducao))
    images = respx.post(IMAGES_URL).mock(return_value=image_response())
    generator = SceneImageGenerator("key", ImageGenConfig())

    path = generator.generate(VISUAL, tmp_path / "gancho.png")

    assert json.loads(chat.calls.last.request.content)["messages"][-1]["content"] == VISUAL
    payload = json.loads(images.calls.last.request.content)
    assert traducao in payload["prompt"]
    assert payload["model"] == "gpt-image-1-mini"
    assert payload["quality"] == "low"
    assert payload["size"] == "1024x1536"
    assert path.read_bytes() == PNG


@respx.mock
def test_traducao_indisponivel_gera_com_o_texto_em_portugues(tmp_path):
    respx.post(CHAT_URL).mock(return_value=httpx.Response(500, text="boom"))
    images = respx.post(IMAGES_URL).mock(return_value=image_response())

    SceneImageGenerator("key", ImageGenConfig()).generate(VISUAL, tmp_path / "gancho.png")

    assert VISUAL in json.loads(images.calls.last.request.content)["prompt"]


@respx.mock
def test_falha_na_geracao_virou_erro_de_cena(tmp_path):
    respx.post(IMAGES_URL).mock(return_value=httpx.Response(400, text="invalid_request"))
    generator = SceneImageGenerator("key", ImageGenConfig(translate_prompts=False))

    with pytest.raises(SceneImageError, match="400"):
        generator.generate(VISUAL, tmp_path / "gancho.png")

    assert not (tmp_path / "gancho.png").exists()


@respx.mock
def test_imagem_ja_gerada_e_reaproveitada(tmp_path):
    route = respx.post(IMAGES_URL).mock(return_value=image_response())
    existente = tmp_path / "gancho.png"
    existente.write_bytes(PNG)

    path = SceneImageGenerator("key", ImageGenConfig()).generate(VISUAL, existente)

    assert path == existente
    assert not route.called


def test_generator_desligado_ou_sem_chave_nao_e_construido():
    sem_chave = Secrets(_env_file=None, openai_api_key=None)

    assert build_scene_image_generator(ImageGenConfig(enabled=False), sem_chave) is None
    assert build_scene_image_generator(ImageGenConfig(), sem_chave) is None
    generator = build_scene_image_generator(
        ImageGenConfig(), Secrets(_env_file=None, openai_api_key="key")
    )
    assert generator is not None


class FlakyGenerator:
    """Gera todos os blocos, menos o `prova_social`, que sempre falha."""

    def __init__(self) -> None:
        self.visuais: list[str] = []

    def generate(self, visual, output_path):
        self.visuais.append(visual)
        if output_path.stem == SceneRole.PROVA_SOCIAL.value:
            raise SceneImageError("400 invalid_request")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(PNG)
        return output_path


def test_bloco_que_falha_cai_na_foto_real_e_os_outros_usam_a_imagem_gerada(tmp_path):
    paths = Paths(tmp_path)
    paths.ensure()
    foto = paths.images / "MLB123" / "0.jpg"
    foto.parent.mkdir(parents=True, exist_ok=True)
    foto.write_bytes(b"jpg")
    track = make_track(tmp_path, {role: f"visual do {role.value}" for role in SceneRole})
    generator = FlakyGenerator()
    service = RenderService(Settings(), paths=paths, image_generator=generator)

    images = service.scene_images(track, [foto])

    gerado = paths.images / "gerado" / "MLB123"
    assert images == [
        gerado / "gancho.png",
        gerado / "apresentacao.png",
        foto,  # unico bloco que caiu na foto real do produto
        gerado / "cta.png",
    ]
    assert generator.visuais == [f"visual do {role.value}" for role in SceneRole]


def test_visual_ausente_no_manifesto_vem_do_scripts_json(tmp_path):
    paths = Paths(tmp_path)
    paths.ensure()
    (paths.out / "scripts-20240101T000000Z.json").write_text(
        json.dumps(
            [
                {
                    "product_id": "MLB123",
                    "estimated_duration_seconds": 12.0,
                    "scenes": [
                        {
                            "bloco": role.value,
                            "fala_narrador": "fala",
                            "instrucao_visual": f"visual salvo do {role.value}",
                        }
                        for role in SceneRole
                    ],
                }
            ]
        ),
        encoding="utf-8",
    )
    track = make_track(tmp_path, {role: None for role in SceneRole})
    service = RenderService(Settings(), paths=paths, image_generator=FlakyGenerator())

    visuais = service.scene_visuals(track)

    assert visuais[SceneRole.GANCHO] == "visual salvo do gancho"
    assert len(visuais) == len(SceneRole)


def test_sem_generator_a_renderizacao_usa_as_fotos_do_produto(tmp_path):
    paths = Paths(tmp_path)
    paths.ensure()
    fotos = []
    for index in range(2):
        foto = paths.images / "MLB123" / f"{index}.jpg"
        foto.parent.mkdir(parents=True, exist_ok=True)
        foto.write_bytes(b"jpg")
        fotos.append(foto)
    track = make_track(tmp_path, {role: f"visual do {role.value}" for role in SceneRole})
    service = RenderService(
        Settings(imagegen=ImageGenConfig(enabled=False)),
        paths=paths,
        secrets=Secrets(_env_file=None, openai_api_key=None),
    )

    assert service.image_generator is None
    assert service.scene_images(track, fotos) == [fotos[0], fotos[1], fotos[0], fotos[1]]
