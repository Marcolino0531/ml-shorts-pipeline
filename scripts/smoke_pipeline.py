"""Health check do pipeline: roda coleta -> roteiro -> tts -> render -> publish em modo simulado.

Usa os servicos de verdade (mesmas classes da producao), trocando apenas o que custa dinheiro ou
depende de rede: o coletor do Mercado Livre, o LLM e a ElevenLabs. FFmpeg, ffprobe, a fila de
publicacao e os metadados rodam de verdade, entao uma falha aqui e uma falha real de integracao.

Uso:
    python scripts/smoke_pipeline.py                 # tudo em data/smoke/ (descartavel)
    python scripts/smoke_pipeline.py --keep          # nao apaga os artefatos no fim
    python scripts/smoke_pipeline.py --data-dir data # roda sobre a pasta data/ real
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from mlshorts.config import Settings, load_settings
from mlshorts.logging_setup import setup_logging
from mlshorts.models import (
    Product,
    ProductImage,
    PublicationStatus,
    Review,
    SceneRole,
    VideoScript,
)
from mlshorts.publish import DryRunPublisher, MetadataBuilder, PublicationScheduler
from mlshorts.publish.store import JsonPublicationStore
from mlshorts.scriptgen import ScriptGenerator
from mlshorts.storage.paths import Paths
from mlshorts.tts import NarrationGenerator
from mlshorts.video import VideoRenderer

STAMP = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
NICHE = "Smoke"
FALAS = {
    SceneRole.GANCHO: "Esse fone custa menos de duzentos reais e tem nota quatro e oito.",
    SceneRole.APRESENTACAO: "Bluetooth cinco ponto tres, cancelamento de ruido e quarenta horas.",
    SceneRole.PROVA_SOCIAL: "Mais de novecentas unidades vendidas e cento e vinte avaliacoes.",
    SceneRole.CTA: "O link com o cupom esta na descricao deste video.",
}


class StubCollector:
    """Simula a coleta: um produto aprovado + uma imagem em alta resolucao gerada pelo FFmpeg."""

    def __init__(self, paths: Paths) -> None:
        self.paths = paths

    def collect(self) -> Product:
        product = Product(
            id="MLBSMOKE",
            title="Fone de Ouvido Bluetooth 5.3 com Cancelamento de Ruido",
            permalink="https://produto.mercadolivre.com.br/MLBSMOKE",
            category_id="MLB1051",
            category_name="Celulares e Telefones",
            price=189.9,
            sold_quantity=940,
            rating=4.8,
            reviews_total=127,
            brand="SoundMax",
            attributes={"Bluetooth": "5.3", "Bateria": "40h"},
            images=[
                ProductImage(id="img-0", url="https://http2.mlstatic.com/D_0-F.jpg", width=1200)
            ],
            positive_reviews=[
                Review(id="r1", rate=5, content="Bateria dura muito mesmo.", likes=12),
            ],
        )
        target = self.paths.images / product.id
        target.mkdir(parents=True, exist_ok=True)
        for index, color in enumerate(("0x1E88E5", "0xF4511E")):
            _ffmpeg(
                ["-f", "lavfi", "-i", f"color=c={color}:s=1000x1000", "-frames:v", "1"],
                target / f"{index}.png",
            )
        (self.paths.raw / f"products-{STAMP}.json").write_text(
            json.dumps([product.model_dump(mode="json")], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return product


class StubLLM:
    """Devolve o mesmo payload que o OpenAI/Claude devolveria (4 cenas Viral Hook)."""

    name = "smoke"
    model = "stub"

    def generate(self, system_prompt: str, user_prompt: str) -> dict[str, object]:
        return {
            "cenas": [
                {
                    "bloco": role.value,
                    "fala_narrador": fala,
                    "instrucao_visual": f"imagem do produto, {role.value}",
                }
                for role, fala in FALAS.items()
            ]
        }


class StubTTS:
    """Gera silencio no lugar da ElevenLabs; a duracao ainda e medida pelo ffprobe de verdade."""

    voice_id = "smoke-voice"
    model_id = "eleven_multilingual_v2"

    def synthesize(self, text: str, output_path: Path) -> Path:
        # ~0.4s por palavra: aproxima o ritmo real da narracao em pt-BR
        seconds = max(round(len(text.split()) * 0.4, 2), 1.0)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        _ffmpeg(["-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono", "-t", str(seconds)], output_path)
        return output_path


def _ffmpeg(args: list[str], output: Path) -> None:
    subprocess.run(["ffmpeg", "-y", "-v", "error", *args, str(output)], check=True)


def _require_binaries() -> None:
    missing = [name for name in ("ffmpeg", "ffprobe") if shutil.which(name) is None]
    if missing:
        raise SystemExit(f"Faltando no PATH: {', '.join(missing)} (instale o FFmpeg)")


def run(settings: Settings, paths: Paths) -> list[tuple[str, str]]:
    """Executa as cinco etapas em sequencia e devolve (etapa, resumo) de cada uma."""
    results: list[tuple[str, str]] = []
    paths.ensure()

    started = time.perf_counter()
    product = StubCollector(paths).collect()
    results.append(("collect", f"{product.id} - nota {product.rating} - 2 imagens"))

    script: VideoScript = ScriptGenerator(
        StubLLM(), settings.scriptgen, settings.video.max_duration_seconds
    ).generate(product)
    (paths.out / f"scripts-{STAMP}.json").write_text(
        json.dumps([script.model_dump(mode="json", by_alias=True)], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    results.append(
        ("script", f"{len(script.scenes)} cenas - {script.estimated_duration_seconds}s estimados")
    )

    track = NarrationGenerator(StubTTS(), settings.tts).generate(script, paths.audio)
    results.append(
        ("tts", f"{len(track.scenes)} audios - {track.total_duration_seconds:.2f}s medidos")
    )

    output = paths.video / f"{product.id}.mp4"
    images = sorted((paths.images / product.id).glob("*.png"))
    VideoRenderer(settings.video).render(track, images, output)
    width, height, duration = _probe(output)
    if (width, height) != (settings.video.width, settings.video.height):
        raise SystemExit(f"Render saiu em {width}x{height}, esperado 1080x1920")
    results.append(
        ("render", f"{width}x{height} - {duration:.2f}s - {output.stat().st_size // 1024}KB")
    )

    metadata = MetadataBuilder(settings.publishing).build(
        product, NICHE, media_path=output, script=script
    )
    scheduler = PublicationScheduler(
        JsonPublicationStore(paths.out / "smoke-queue.json"),
        settings.publishing.model_copy(update={"require_approval": False}),
        publisher=DryRunPublisher(),
    )
    item = scheduler.submit(product.id, NICHE, str(output), metadata=metadata)
    if item.status is not PublicationStatus.PUBLISHED:
        raise SystemExit(f"Publicacao simulada terminou em {item.status.value}: {item.error}")
    results.append(
        ("publish", f"{item.status.value} (dry-run) - {len(metadata.hashtags)} hashtags")
    )

    results.append(("total", f"{time.perf_counter() - started:.1f}s"))
    return results


def _probe(video: Path) -> tuple[int, int, float]:
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height:format=duration",
            "-of",
            "json",
            str(video),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    info = json.loads(result.stdout)
    stream = info["streams"][0]
    return int(stream["width"]), int(stream["height"]), float(info["format"]["duration"])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", default="data/smoke", help="Onde gravar os artefatos.")
    parser.add_argument("--keep", action="store_true", help="Nao apagar os artefatos no fim.")
    parser.add_argument("--config", default=None, help="Caminho do settings.yaml.")
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()

    setup_logging(10 if args.verbose else 30)
    _require_binaries()

    data_dir = Path(args.data_dir)
    settings = load_settings(Path(args.config) if args.config else None)
    try:
        results = run(settings, Paths(data_dir))
    except Exception as exc:  # noqa: BLE001 - o resumo importa mais que o traceback aqui
        print(f"\n\033[1;31mFALHOU\033[0m: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    finally:
        if not args.keep and data_dir.exists() and data_dir.name == "smoke":
            shutil.rmtree(data_dir)

    width = max(len(stage) for stage, _ in results)
    print()
    for stage, summary in results:
        print(f"  \033[1;32mok\033[0m {stage.ljust(width)}  {summary}")
    print("\n\033[1;32mPipeline saudavel\033[0m (modo simulado: sem chamadas ao ML/LLM/ElevenLabs)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
