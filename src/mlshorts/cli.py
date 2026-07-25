"""CLI do pipeline: coleta, roteiro, narracao, fila de publicacao e dashboard."""

from __future__ import annotations

import logging
import os
import subprocess
import sys
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from mlshorts.collectors.service import CollectionService
from mlshorts.config import load_settings
from mlshorts.dashboard.data import CONFIG_ENV_VAR
from mlshorts.logging_setup import setup_logging
from mlshorts.models import PublicationStatus
from mlshorts.publish import PublicationScheduler
from mlshorts.scriptgen import ScriptGenerationService
from mlshorts.tts import NarrationService

app = typer.Typer(help="Pipeline de videos curtos de produtos em alta do Mercado Livre.")
console = Console()

ConfigOption = Annotated[
    Path | None, typer.Option("--config", "-c", help="Caminho do settings.yaml.")
]
CategoryOption = Annotated[
    list[str] | None,
    typer.Option("--category", help="Restringe a coleta a estes IDs de categoria."),
]
SkipImagesOption = Annotated[bool, typer.Option("--skip-images", help="Nao baixar as imagens.")]
VerboseOption = Annotated[bool, typer.Option("--verbose", "-v")]
ProductsFileOption = Annotated[
    Path | None,
    typer.Option("--products-file", help="JSON de produtos; padrao e o mais recente em data/raw."),
]
ScriptsFileOption = Annotated[
    Path | None,
    typer.Option("--scripts-file", help="JSON de roteiros; padrao e o mais recente em data/out."),
]
ProductIdOption = Annotated[
    str | None, typer.Option("--product-id", help="Narra apenas este produto.")
]


@app.command()
def collect(
    config: ConfigOption = None,
    category: CategoryOption = None,
    skip_images: SkipImagesOption = False,
    verbose: VerboseOption = False,
) -> None:
    """Coleta produtos em alta, aplica os filtros e salva o JSON em data/raw."""
    setup_logging(logging.DEBUG if verbose else logging.INFO)
    settings = load_settings(config)
    if category:
        wanted = set(category)
        settings.categories = [item for item in settings.categories if item.id in wanted]
        if not settings.categories:
            raise typer.BadParameter(f"Nenhuma categoria de {sorted(wanted)} esta no settings.yaml")

    products = CollectionService(settings).collect(download_images=not skip_images)

    table = Table(title=f"{len(products)} produtos aprovados")
    table.add_column("ID")
    table.add_column("Titulo", max_width=48)
    table.add_column("Nota", justify="right")
    table.add_column("Vendas", justify="right")
    table.add_column("Preco", justify="right")
    for product in products:
        table.add_row(
            product.id,
            product.title,
            f"{product.rating:.1f}" if product.rating else "-",
            str(product.sold_quantity),
            f"R$ {product.price:,.2f}",
        )
    console.print(table)


@app.command()
def script(
    config: ConfigOption = None,
    products_file: ProductsFileOption = None,
    verbose: VerboseOption = False,
) -> None:
    """Gera os roteiros Viral Hook a partir do ultimo JSON de produtos coletados."""
    setup_logging(logging.DEBUG if verbose else logging.INFO)
    settings = load_settings(config)
    scripts = ScriptGenerationService(settings).run(products_file)

    for video_script in scripts:
        console.rule(f"{video_script.product_id} - {video_script.estimated_duration_seconds}s")
        for scene in video_script.scenes:
            console.print(f"[bold]{scene.role.value}[/bold]: {scene.narration}")
            console.print(f"  [dim]visual:[/dim] {scene.visual}")
    console.print(f"\n{len(scripts)} roteiros gerados.")


@app.command()
def narrate(
    config: ConfigOption = None,
    scripts_file: ScriptsFileOption = None,
    product_id: ProductIdOption = None,
    verbose: VerboseOption = False,
) -> None:
    """Gera a narracao (ElevenLabs) de cada cena e salva os audios em data/audio/."""
    setup_logging(logging.DEBUG if verbose else logging.INFO)
    settings = load_settings(config)
    tracks = NarrationService(settings).run(scripts_file, product_id=product_id)

    for track in tracks:
        table = Table(title=f"{track.product_id} - {track.total_duration_seconds:.1f}s")
        table.add_column("#")
        table.add_column("Bloco")
        table.add_column("Inicio", justify="right")
        table.add_column("Duracao", justify="right")
        table.add_column("Arquivo")
        for scene in track.scenes:
            table.add_row(
                str(scene.index),
                scene.role.value,
                f"{scene.start_seconds:.2f}s",
                f"{scene.duration_seconds:.2f}s",
                Path(scene.audio_path).name,
            )
        console.print(table)
    console.print(f"{len(tracks)} narracoes geradas.")


@app.command("queue-add")
def queue_add(
    product_id: Annotated[str, typer.Option("--product-id", help="ID do produto no ML.")],
    niche: Annotated[str, typer.Option("--niche", help="Nicho/categoria da conta.")],
    media: Annotated[Path, typer.Option("--media", help="Caminho do MP4 vertical.")],
    config: ConfigOption = None,
    verbose: VerboseOption = False,
) -> None:
    """Envia um video para publicacao: publica agora ou agenda se o nicho estiver bloqueado."""
    setup_logging(logging.DEBUG if verbose else logging.INFO)
    settings = load_settings(config)
    scheduler = PublicationScheduler.from_settings(settings)
    item = scheduler.submit(product_id=product_id, niche=niche, media_path=str(media))

    if item.status is PublicationStatus.PUBLISHED:
        console.print(f"[green]Publicado agora[/green]: {item.id}")
    else:
        console.print(
            f"[yellow]Agendado[/yellow]: {item.id} para {item.scheduled_for.isoformat()} "
            f"(intervalo de {settings.publishing.interval_for(niche)}h no nicho {niche})"
        )


@app.command("publish-queue")
def publish_queue(
    config: ConfigOption = None,
    limit: Annotated[
        int | None, typer.Option("--limit", help="Maximo de videos nesta rodada.")
    ] = None,
    verbose: VerboseOption = False,
) -> None:
    """Comando periodico (cron): publica os videos da fila que ja podem ir ao ar."""
    setup_logging(logging.DEBUG if verbose else logging.INFO)
    settings = load_settings(config)
    scheduler = PublicationScheduler.from_settings(settings)
    published = scheduler.process_due(limit=limit)

    for item in published:
        console.print(f"[green]Publicado[/green] {item.product_id} ({item.niche}) - {item.id}")
    console.print(f"{len(published)} publicados; {len(scheduler.store.pending())} ainda na fila.")


@app.command("queue-list")
def queue_list(
    config: ConfigOption = None,
    status: Annotated[
        PublicationStatus | None, typer.Option("--status", help="Filtra por status.")
    ] = None,
) -> None:
    """Mostra a fila de publicacao."""
    settings = load_settings(config)
    scheduler = PublicationScheduler.from_settings(settings)

    table = Table(title="Fila de publicacao")
    table.add_column("ID")
    table.add_column("Produto")
    table.add_column("Nicho")
    table.add_column("Status")
    table.add_column("Agendado para")
    for item in scheduler.store.list_all(status):
        table.add_row(
            item.id[:8],
            item.product_id,
            item.niche,
            item.status.value,
            item.scheduled_for.isoformat(timespec="minutes"),
        )
    console.print(table)


@app.command()
def dashboard(
    config: ConfigOption = None,
    port: Annotated[int, typer.Option("--port", help="Porta do servidor Streamlit.")] = 8501,
    host: Annotated[str, typer.Option("--host", help="Endereco de escuta.")] = "localhost",
) -> None:
    """Sobe o dashboard Streamlit com as abas de produtos, roteiros, midia e fila."""
    from mlshorts.dashboard import APP_PATH

    env = dict(os.environ)
    env.setdefault("STREAMLIT_BROWSER_GATHER_USAGE_STATS", "false")
    if config:
        env[CONFIG_ENV_VAR] = str(config.resolve())

    command = [
        sys.executable,
        "-m",
        "streamlit",
        "run",
        str(APP_PATH),
        "--server.port",
        str(port),
        "--server.address",
        host,
        # headless evita o prompt de e-mail do Streamlit no primeiro uso
        "--server.headless",
        "true",
    ]
    console.print(f"Dashboard em [bold]http://{host}:{port}[/bold] (Ctrl+C para parar)")
    try:
        raise SystemExit(subprocess.call(command, env=env))
    except FileNotFoundError as exc:  # streamlit ausente
        raise typer.Exit(code=1) from exc


@app.command()
def categories(config: ConfigOption = None) -> None:
    """Lista as categorias configuradas."""
    for item in load_settings(config).categories:
        console.print(f"{item.id}\t{item.name or ''}")


if __name__ == "__main__":
    app()
