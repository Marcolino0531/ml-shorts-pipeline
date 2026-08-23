"""Configuracao unica de logging para CLI e modulos."""

from __future__ import annotations

import logging
from pathlib import Path

from rich.logging import RichHandler

_CONFIGURED = False

FILE_FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"


def setup_logging(level: int = logging.INFO, log_file: Path | None = None) -> None:
    global _CONFIGURED
    if _CONFIGURED:
        return
    handlers: list[logging.Handler] = [RichHandler(rich_tracebacks=True, show_path=False)]
    if log_file is not None:
        # append: o arquivo guarda o historico de todas as execucoes do timer
        log_file.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        file_handler.setFormatter(logging.Formatter(FILE_FORMAT))
        handlers.append(file_handler)
    logging.basicConfig(
        level=level,
        format="%(message)s",
        datefmt="%H:%M:%S",
        handlers=handlers,
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    _CONFIGURED = True
