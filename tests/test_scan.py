from __future__ import annotations

from pathlib import Path
import pytest

from iaclineage.diagnostics import DiagnosticError
from iaclineage.scan import discover_terraform_files


FIXTURES = Path(__file__).parent / "fixtures" / "repository"


def test_discovers_sorted_files_and_excludes_internal_directories() -> None:
    assert discover_terraform_files(FIXTURES) == (
        Path("main.tf"),
        Path("modules/network/network.tf"),
    )


def test_rejects_a_missing_path_without_disclosing_the_path() -> None:
    with pytest.raises(DiagnosticError, match="IAC001: scan path does not exist"):
        discover_terraform_files(Path("missing-private-repository"))


def test_rejects_a_file_path(tmp_path: Path) -> None:
    file_path = tmp_path / "main.tf"
    file_path.write_text("resource \"x\" \"y\" {}", encoding="utf-8")
    with pytest.raises(DiagnosticError, match="IAC002: scan path must be a directory"):
        discover_terraform_files(file_path)


def test_progress_tracks_discovery_without_visiting_excluded_directories(tmp_path):
    for relative in ("a.tf", "child/b.tf", ".git/hidden.tf", ".terraform/modules/hidden.tf"):
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("", encoding="utf-8")
    events = []
    files = discover_terraform_files(tmp_path, progress=lambda event, path: events.append((event, path)))
    assert files == (Path("a.tf"), Path("child/b.tf"))
    assert {path for event, path in events if event == "directory"} == {Path("."), Path("child")}
    assert {path for event, path in events if event == "file"} == set(files)
    assert all(not path.is_absolute() for _, path in events)
