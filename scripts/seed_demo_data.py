"""Popula data/ com artefatos ficticios para conferir o dashboard sem gastar API.

Uso: python scripts/seed_demo_data.py
"""

from __future__ import annotations

import json
import subprocess
from datetime import datetime, timezone

from mlshorts.config import load_settings
from mlshorts.models import (
    Product,
    ProductImage,
    Review,
    Scene,
    SceneAudio,
    SceneRole,
    ScriptAudio,
    VideoScript,
)
from mlshorts.publish.scheduler import PublicationScheduler
from mlshorts.storage.paths import Paths

STAMP = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
FALAS = {
    SceneRole.GANCHO: "Esse fone custa menos de duzentos reais e tem nota 4.8.",
    SceneRole.APRESENTACAO: "Bluetooth 5.3, cancelamento de ruido e 40 horas de bateria.",
    SceneRole.PROVA_SOCIAL: "Mais de novecentas unidades vendidas e cento e vinte avaliacoes.",
    SceneRole.CTA: "O link com o cupom esta na descricao deste video.",
}


def make_product() -> Product:
    return Product(
        id="MLB123DEMO",
        title="Fone de Ouvido Bluetooth 5.3 com Cancelamento de Ruido",
        permalink="https://produto.mercadolivre.com.br/MLB123DEMO",
        category_id="MLB1051",
        category_name="Celulares e Telefones",
        price=189.9,
        sold_quantity=940,
        rating=4.8,
        reviews_total=127,
        brand="SoundMax",
        attributes={"Bluetooth": "5.3", "Bateria": "40h", "Peso": "45g"},
        images=[ProductImage(id="img-0", url="https://http2.mlstatic.com/D_0-F.jpg", width=1200)],
        positive_reviews=[
            Review(
                id="r1", rate=5, content="Bateria dura muito mesmo, chegou em dois dias.", likes=12
            ),
            Review(
                id="r2", rate=5, content="Cancelamento de ruido surpreendente pelo preco.", likes=8
            ),
        ],
    )


def make_script(product: Product) -> VideoScript:
    scenes = [
        Scene(
            bloco=role.value,
            fala_narrador=fala,
            instrucao_visual=f"imagem do produto, {role.value}",
        )
        for role, fala in FALAS.items()
    ]
    return VideoScript(
        product_id=product.id,
        scenes=scenes,
        estimated_duration_seconds=32.0,
        provider="openai",
        model="gpt-4o-mini",
    )


def make_audio(paths: Paths, script: VideoScript) -> ScriptAudio:
    directory = paths.audio / script.product_id
    directory.mkdir(parents=True, exist_ok=True)
    scenes: list[SceneAudio] = []
    cursor = 0.0
    for index, scene in enumerate(script.scenes):
        duration = 3.0 + index
        path = directory / f"{index:02d}-{scene.role.value}.mp3"
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-v",
                "error",
                "-f",
                "lavfi",
                "-i",
                "anullsrc=r=44100:cl=mono",
                "-t",
                str(duration),
                str(path),
            ],
            check=True,
        )
        scenes.append(
            SceneAudio(
                index=index,
                role=scene.role,
                text=scene.narration,
                audio_path=str(path),
                duration_seconds=duration,
                start_seconds=round(cursor, 3),
            )
        )
        cursor += duration + 0.25
    audio = ScriptAudio(
        product_id=script.product_id,
        voice_id="demo-voice",
        model_id="eleven_multilingual_v2",
        scenes=scenes,
        pause_between_scenes_seconds=0.25,
    )
    payload = audio.model_dump(mode="json")
    payload["total_duration_seconds"] = round(audio.total_duration_seconds, 3)
    (directory / "narration.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return audio


def main() -> None:
    settings = load_settings()
    paths = Paths()
    paths.ensure()

    product = make_product()
    (paths.raw / f"products-{STAMP}.json").write_text(
        json.dumps([product.model_dump(mode="json")], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (paths.images / product.id).mkdir(parents=True, exist_ok=True)

    script = make_script(product)
    (paths.out / f"scripts-{STAMP}.json").write_text(
        json.dumps([script.model_dump(mode="json", by_alias=True)], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    audio = make_audio(paths, script)
    (paths.out / f"narration-{STAMP}.json").write_text(
        json.dumps([audio.model_dump(mode="json")], ensure_ascii=False, indent=2), encoding="utf-8"
    )

    scheduler = PublicationScheduler.from_settings(settings)
    scheduler.submit(product.id, "Celulares e Telefones", f"data/video/{product.id}.mp4")
    scheduler.submit("MLB456DEMO", "Celulares e Telefones", "data/video/MLB456DEMO.mp4")
    print(f"Demo pronta: {len(scheduler.store.list_all())} itens na fila")


if __name__ == "__main__":
    main()
