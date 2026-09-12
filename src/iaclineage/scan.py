"""Discover Terraform source files without executing repository content."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from .diagnostics import DiagnosticError

_EXCLUDED_DIRECTORY_NAMES = frozenset({".git", ".terraform"})


def discover_terraform_files(
    repository: Path, *, progress: Callable[[str, Path | None], None] | None = None,
) -> tuple[Path, ...]:
    """Return sorted ``.tf`` files under a local repository.

    Files inside Terraform's downloaded-module directory and Git metadata are
    deliberately excluded for this beta. Returned paths are relative to the
    supplied repository so output stays portable and does not expose a machine
    user's directory structure.
    """
    if not repository.exists():
        raise DiagnosticError("IAC001", "scan path does not exist")
    if not repository.is_dir():
        raise DiagnosticError("IAC002", "scan path must be a directory")

    discovered: list[Path] = []
    for directory, directories, filenames in repository.walk():
        directories[:] = [name for name in directories if name not in _EXCLUDED_DIRECTORY_NAMES]
        if progress:
            progress("directory", directory.relative_to(repository))
        for name in filenames:
            candidate = directory / name
            if candidate.match("*.tf") and candidate.is_file():
                relative_path = candidate.relative_to(repository)
                discovered.append(relative_path)
                if progress:
                    progress("file", relative_path)
    return tuple(sorted(discovered, key=lambda path: path.as_posix()))
