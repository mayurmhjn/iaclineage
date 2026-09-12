"""Safe, stable diagnostics for command-line boundaries."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DiagnosticError(Exception):
    """An expected user-facing failure without source-content disclosure."""

    code: str
    message: str

    def __str__(self) -> str:
        return f"{self.code}: {self.message}"