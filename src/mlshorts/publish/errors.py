"""Erro comum dos publishers de rede social."""

from __future__ import annotations


class PublishError(RuntimeError):
    """Falha ao enviar o video para a rede."""
