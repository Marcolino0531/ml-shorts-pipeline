"""CLI do pipeline. Hoje expoe a etapa de coleta; as demais entram nas proximas fases."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from mlshorts.collectors.service import CollectionService
from mlshorts.config import load_settings
from mlshorts.logging_setup import setup_logging

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
def categories(config: ConfigOption = None) -> None:
    """Lista as categorias configuradas."""
    for item in load_settings(config).categories:
        console.print(f"{item.id}\t{item.name or ''}")


if __name__ == "__main__":
    app()
