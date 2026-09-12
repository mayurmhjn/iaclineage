from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path

import json
import pytest

from iaclineage import cli
from iaclineage.cli import main


FIXTURES = Path(__file__).parent / "fixtures" / "repository"


def test_scan_prints_only_relative_terraform_paths() -> None:
    output = StringIO()
    with redirect_stdout(output):
        exit_code = main(["scan", str(FIXTURES)])

    assert exit_code == 0
    assert output.getvalue() == "main.tf\nmodules/network/network.tf\n"


def test_find_shows_structural_snippets_and_direct_reference_evidence() -> None:
    fixture = Path(__file__).parent / "fixtures" / "references"
    output = StringIO()
    with redirect_stdout(output):
        exit_code = main(["find", str(fixture), "api"])

    assert exit_code == 0
    assert "resource.example_service.api  main.tf:3:1-5:2" in output.getvalue()
    assert 'resource "example_service" "api" { ... }' in output.getvalue()
    assert "-> resource.example_network.app (resolved, main.tf:4:16-4:38)" in output.getvalue()

def test_report_aggregates_a_container_of_projects(tmp_path: Path) -> None:
    project = tmp_path / "project_a"
    project.mkdir()
    (project / "main.tf").write_text('resource "x" "y" {}', encoding="utf-8")

    output_path = tmp_path / "r.html"
    errors = StringIO()
    with redirect_stderr(errors):
        exit_code = main(["report", str(tmp_path), "--output", str(output_path)])

    assert exit_code == 0
    assert "warning:" not in errors.getvalue()  # container works without a warning
    assert "project_a::resource.x.y" in output_path.read_text(encoding="utf-8")


def test_warns_only_when_no_terraform_is_present(tmp_path: Path) -> None:
    (tmp_path / "notes").mkdir()

    errors = StringIO()
    with redirect_stderr(errors):
        exit_code = main(["report", str(tmp_path), "--output", str(tmp_path / "r.html")])

    assert exit_code == 0
    assert "no Terraform (.tf) files were found" in errors.getvalue()


def test_report_writes_a_static_html_file(tmp_path: Path) -> None:
    fixture = Path(__file__).parent / "fixtures" / "references"
    output_path = tmp_path / "report.html"
    exit_code = main(["report", str(fixture), "--output", str(output_path)])

    assert exit_code == 0
    assert "IaCLineage report" in output_path.read_text(encoding="utf-8")


def test_report_writes_an_interactive_html_file(tmp_path: Path) -> None:
    fixture = Path(__file__).parent / "fixtures" / "references"
    output_path = tmp_path / "lineage.html"

    exit_code = main([
        "report", str(fixture), "--output", str(output_path),
        "--format", "interactive-html",
    ])

    assert exit_code == 0
    report = output_path.read_text(encoding="utf-8")
    assert "IaCLineage interactive report" in report
    assert "Focused lineage" in report


def test_include_source_values_requires_interactive_format(tmp_path: Path) -> None:
    fixture = Path(__file__).parent / "fixtures" / "references"
    errors = StringIO()
    with redirect_stderr(errors):
        exit_code = main([
            "report", str(fixture), "--output", str(tmp_path / "report.html"),
            "--include-source-values",
        ])

    assert exit_code == 2
    assert "IAC009" in errors.getvalue()


def test_empty_scan_reports_count_without_polluting_paths(tmp_path, capsys):
    assert main(["scan", str(tmp_path)]) == 0
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "Found 0 Terraform (.tf) files." in captured.err


def test_report_confirms_destination_and_counts(tmp_path, capsys):
    output = tmp_path / "report.html"
    assert main(["report", str(FIXTURES), "--output", str(output)]) == 0
    captured = capsys.readouterr()
    assert "[report] -> Indexing Terraform source;" in captured.err
    assert "Resolving static references" in captured.err
    assert "Writing html report" in captured.err
    assert "[report] OK Complete in " in captured.err
    assert str(output.resolve()) in captured.out
    assert "declarations" in captured.out
    assert "diagnostics" in captured.out


def test_find_json_stdout_remains_parseable(capsys):
    import json
    assert main(["find", str(FIXTURES), "missing", "--format", "json"]) == 1
    assert json.loads(capsys.readouterr().out)["error"] == "no match"


def test_find_text_output_writes_result_and_confirms(tmp_path, capsys):
    output = tmp_path / "find.txt"
    assert main(["find", str(FIXTURES), "missing", "--output", str(output)]) == 1
    assert "No entity found" in output.read_text()
    captured = capsys.readouterr()
    assert captured.out == ""
    assert str(output.resolve()) in captured.err


def test_version_is_available_without_a_repository(capsys):
    import pytest
    from iaclineage import __version__

    with pytest.raises(SystemExit) as result:
        main(["--version"])
    assert result.value.code == 0
    assert capsys.readouterr().out == f"iaclineage {__version__}\n"


def test_find_progress_explains_match_and_preserves_json(capsys):
    fixture = Path(__file__).parent / "fixtures" / "references"
    assert main(["find", str(fixture), "api", "--format", "json"]) == 0
    captured = capsys.readouterr()
    assert json.loads(captured.out)["match"]["address"] == "resource.example_service.api"
    assert "[find] -> Reading source #1: main.tf" in captured.err
    assert "3 declarations, 2 references (2 resolved, 0 unresolved, 0 ambiguous)" in captured.err
    assert "Searching 3 declarations for 'api'" in captured.err
    assert "Matched 1 declaration(s) by name" in captured.err
    assert "Complete in " in captured.err


@pytest.mark.parametrize("fmt, code", [("text", 1), ("json", 0)])
def test_find_multiple_matches_keep_exit_codes_and_explain_count(tmp_path, capsys, fmt, code):
    (tmp_path / "main.tf").write_text('variable "same" {}\noutput "same" { value = var.same }', encoding="utf-8")
    assert main(["find", str(tmp_path), "same", "--format", fmt]) == code
    captured = capsys.readouterr()
    assert "Matched 2 declaration(s) by name" in captured.err
    if fmt == "json":
        assert len(json.loads(captured.out)["matches"]) == 2
    else:
        assert "Refine your query" in captured.out
        assert "Finished without a unique match" in captured.err


def test_diagnostics_are_useful_bounded_and_source_safe(tmp_path, capsys):
    modules = '\n'.join(f'module "m{i}" {{ source = "SECRET_SOURCE_CANARY" }}' for i in range(6))
    (tmp_path / "main.tf").write_text(modules, encoding="utf-8")
    output = tmp_path / "report.json"
    assert main(["report", str(tmp_path), "--output", str(output), "--format", "json"]) == 0
    captured = capsys.readouterr()
    assert "6 diagnostics" in captured.err
    assert captured.err.count("warning: IAC103 at main.tf:") == 5
    assert "1 more diagnostics" in captured.err
    assert "SECRET_SOURCE_CANARY" not in captured.out + captured.err
    assert len(json.loads(output.read_text())["diagnostics"]) == 6


def test_excluded_sources_do_not_hide_empty_index_warning(tmp_path, capsys):
    cache = tmp_path / ".terraform"
    cache.mkdir()
    (cache / "main.tf").write_text('variable "ignored" {}', encoding="utf-8")
    assert main(["find", str(tmp_path), "ignored", "--format", "json"]) == 1
    captured = capsys.readouterr()
    assert "no Terraform (.tf) files were found" in captured.err
    assert "No declaration matches" in captured.err
    assert "Finished without a unique match" in captured.err
    assert json.loads(captured.out)["error"] == "no match"


def test_empty_source_file_is_read_without_false_empty_directory_warning(tmp_path, capsys):
    (tmp_path / "main.tf").write_text("", encoding="utf-8")
    assert main(["find", str(tmp_path), "missing"]) == 1
    captured = capsys.readouterr()
    assert "after 1 source reads" in captured.err
    assert "no Terraform (.tf) files were found" not in captured.err


def test_progress_is_throttled_but_counts_actual_work(monkeypatch, capsys):
    now = [0.0]
    monkeypatch.setattr(cli, "perf_counter", lambda: now[0])
    progress = cli._Progress("find")
    for number in range(100):
        progress("read", Path(f"file{number}.tf"))
    assert capsys.readouterr().err.count("Reading source") == 1
    now[0] = 1.5
    progress("read", Path("last.tf"))
    progress("resolve", None)
    captured = capsys.readouterr()
    assert "Reading source #101: last.tf (1.5s elapsed)" in captured.err
    assert "after 101 source reads" in captured.err


@pytest.mark.parametrize("error, code, message", [
    (PermissionError("SOURCE_CANARY"), 1, "Check source readability"),
    (RuntimeError("SOURCE_CANARY"), 1, "Unexpected RuntimeError"),
    (KeyboardInterrupt(), 130, "Cancelled after"),
])
def test_failures_never_claim_completion_or_expose_exception_source(monkeypatch, capsys, error, code, message):
    def fail(*args, **kwargs):
        raise error
    monkeypatch.setattr(cli, "index_repository", fail)
    assert main(["find", str(FIXTURES), "api", "--format", "json"]) == code
    captured = capsys.readouterr()
    assert captured.out == ""
    assert message in captured.err
    assert "Complete" not in captured.err
    assert "SOURCE_CANARY" not in captured.err


def test_expected_error_has_command_context_and_no_success(tmp_path, capsys):
    assert main(["scan", str(tmp_path / "missing")]) == 2
    captured = capsys.readouterr()
    assert "[scan] ERR Failed: IAC001: scan path does not exist" in captured.err
    assert "Complete" not in captured.err


def test_color_always_colors_progress_but_never_data(capsys):
    progress = cli._Progress("scan", "always")
    progress.message("Complete in 0.01s.")
    captured = capsys.readouterr()
    assert "\x1b[32m[scan]" in captured.err
    assert "\x1b[2mComplete in 0.01s.\x1b[0m" in captured.err


def test_color_always_colors_status_symbol(capsys):
    progress = cli._Progress("report", "always")
    progress.message("Summary: 3 declarations")
    captured = capsys.readouterr()
    assert "\x1b[32mOK\x1b[0m" in captured.err


def test_color_auto_is_disabled_for_redirected_output(monkeypatch):
    class Redirected:
        def isatty(self):
            return False
    monkeypatch.setattr(cli.sys, "stderr", Redirected())
    assert cli._Progress("scan", "auto").color is False


def test_no_color_environment_disables_auto(monkeypatch):
    monkeypatch.setenv("NO_COLOR", "1")
    class Terminal:
        def isatty(self):
            return True
    monkeypatch.setattr(cli.sys, "stderr", Terminal())
    assert cli._Progress("scan", "auto").color is False
