"""Medicao da duracao real dos audios gerados (base da sincronia no FFmpeg)."""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
from pathlib import Path
from typing import Protocol

logger = logging.getLogger(__name__)


class DurationProbe(Protocol):
    """Mede a duracao, em segundos, de um arquivo de audio."""

    def __call__(self, path: Path) -> float: ...


class FFprobeDurationProbe:
    """Le a duracao exata com `ffprobe` (mesma dependencia usada na montagem do video)."""

    def __init__(self, binary: str = "ffprobe") -> None:
        self.binary = binary

    def __call__(self, path: Path) -> float:
        if shutil.which(self.binary) is None:
            raise RuntimeError(f"{self.binary} nao encontrado: instale o FFmpeg")
        result = subprocess.run(
            [
                self.binary,
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "json",
                str(path),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            raise RuntimeError(f"ffprobe falhou em {path}: {result.stderr.strip()}")
        payload = json.loads(result.stdout or "{}")
        duration = payload.get("format", {}).get("duration")
        if duration is None:
            raise RuntimeError(f"ffprobe nao reportou duracao para {path}")
        return round(float(duration), 3)
