"""Convencoes de diretorio para os artefatos de cada etapa do pipeline."""

from __future__ import annotations

from pathlib import Path

from mlshorts.config import DATA_DIR


class Paths:
    def __init__(self, root: Path | None = None) -> None:
        self.root = root or DATA_DIR

    @property
    def raw(self) -> Path:
        return self.root / "raw"

    @property
    def images(self) -> Path:
        return self.root / "images"

    @property
    def audio(self) -> Path:
        return self.root / "audio"

    @property
    def video(self) -> Path:
        return self.root / "video"

    @property
    def out(self) -> Path:
        return self.root / "out"

    def ensure(self) -> None:
        for path in (self.raw, self.images, self.audio, self.video, self.out):
            path.mkdir(parents=True, exist_ok=True)
