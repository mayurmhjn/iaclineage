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


@pytest.mark.parametrize("fmt, code", [("text", 1), ("json", 1)])
def test_find_multiple_matches_use_consistent_exit_codes_and_explain_count(tmp_path, capsys, fmt, code):
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


def _write_tree(root: Path, files: dict[str, str]) -> None:
    for name, text in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


def test_unused_lists_unreferenced_variables_locals_and_child_outputs(tmp_path, capsys):
    _write_tree(tmp_path, {
        "main.tf": (
            'variable "used" {}\n'
            'variable "unused" {\n  validation {\n    condition = var.unused != ""\n'
            '    error_message = "empty"\n  }\n}\n'
            'locals {\n  kept = var.used\n  dropped = 1\n}\n'
            'module "app" {\n  source = "./app"\n  name = local.kept\n}\n'
            'output "root" { value = module.app.id }\n'
        ),
        "app/main.tf": (
            'variable "name" {}\n'
            'output "id" { value = 1 }\n'
            'output "extra" { value = 2 }\n'
        ),
    })

    assert main(["unused", str(tmp_path)]) == 1
    captured = capsys.readouterr()
    lines = captured.out.splitlines()
    assert lines == [
        "local.dropped (local) at main.tf:10:3-10:14",
        "module.app.output.extra (output) at app/main.tf:3:1-3:29",
        "module.app.variable.name (variable) at app/main.tf:1:1-1:19",
        "variable.unused (variable) at main.tf:2:1-7:2",
    ]
    assert "Summary: 4 unused declaration(s)" in captured.err


def test_unused_returns_zero_when_everything_is_referenced(tmp_path, capsys):
    _write_tree(tmp_path, {"main.tf": 'variable "name" {}\noutput "name" { value = var.name }\n'})

    assert main(["unused", str(tmp_path)]) == 0
    captured = capsys.readouterr()
    assert captured.out == "No unused variables, locals, or module outputs found.\n"
    assert "Complete" in captured.err


def test_unused_ignores_downloaded_module_declarations(tmp_path, capsys):
    source = "git::https://example.invalid/modules.git//app?ref=v1"
    _write_tree(tmp_path, {
        "main.tf": f'module "app" {{\n  source = "{source}"\n}}\n',
        ".terraform/modules/app/main.tf": 'variable "extra" {}\noutput "id" { value = 1 }\n',
        ".terraform/modules/modules.json": json.dumps(
            {"Modules": [{"Key": "app", "Source": source, "Dir": ".terraform/modules/app"}]}
        ),
    })

    assert main(["unused", str(tmp_path)]) == 0
    assert "module.app" not in capsys.readouterr().out


@pytest.mark.parametrize(("fail_on", "code"), [
    ("unresolved", 1), ("ambiguous", 0), ("unresolved,ambiguous", 1),
])
def test_report_fail_on_sets_exit_code_after_writing(tmp_path, capsys, fail_on, code):
    _write_tree(tmp_path / "src", {"main.tf": 'output "x" { value = var.missing }\n'})
    output = tmp_path / "report.json"

    assert main(["report", str(tmp_path / "src"), "--format", "json",
                 "--output", str(output), "--fail-on", fail_on]) == code
    assert output.exists()
    captured = capsys.readouterr()
    if code:
        assert "warning: 1 unresolved reference(s); failing because of --fail-on" in captured.err
        assert "Complete" not in captured.err


def test_report_fail_on_counts_ambiguous_references(tmp_path, capsys):
    _write_tree(tmp_path / "src", {
        "a.tf": 'variable "dup" {}\n',
        "b.tf": 'variable "dup" {}\noutput "x" { value = var.dup }\n',
    })

    assert main(["report", str(tmp_path / "src"), "--format", "json",
                 "--output", str(tmp_path / "r.json"), "--fail-on", "ambiguous"]) == 1
    assert "1 ambiguous reference(s)" in capsys.readouterr().err


def test_report_fail_on_rejects_unknown_states(tmp_path, capsys):
    with pytest.raises(SystemExit) as error:
        main(["report", str(tmp_path), "--output", str(tmp_path / "r.html"), "--fail-on", "resolved"])
    assert error.value.code == 2
    assert "invalid choice 'resolved'" in capsys.readouterr().err


def test_unused_treats_whole_module_references_as_using_its_outputs(tmp_path, capsys):
    _write_tree(tmp_path, {
        "main.tf": 'module "app" {\n  source = "./app"\n}\noutput "all" { value = module.app }\n',
        "app/main.tf": 'output "id" { value = 1 }\n',
    })

    assert main(["unused", str(tmp_path)]) == 0
    assert "module.app.output.id" not in capsys.readouterr().out


_TRACE_FILES = {
    "main.tf": (
        'variable "name" {}\n'
        'variable "size" {}\n'
        'module "app" {\n'
        '  source   = "./app"\n'
        '  app_name = var.name\n'
        '  app_size = var.size\n'
        '}\n'
        'output "app_id" { value = module.app.id }\n'
    ),
    "app/main.tf": (
        'variable "app_name" {}\n'
        'variable "app_size" {}\n'
        'resource "aws_thing" "this" { name = var.app_name }\n'
        'output "id" { value = aws_thing.this.id }\n'
    ),
}


def _trace_json(root: Path, *arguments: str) -> dict:
    output = StringIO()
    with redirect_stdout(output):
        assert main(["trace", str(root), *arguments, "--format", "json"]) == 0
    return json.loads(output.getvalue())


def _hops(entries: list[dict]) -> list[tuple[int, str, str, str]]:
    return [(e["depth"], e["consumer"], e["dependency"], e["kind"]) for e in entries]


def test_trace_upstream_follows_only_the_bound_module_argument(tmp_path):
    _write_tree(tmp_path, _TRACE_FILES)

    result = _trace_json(tmp_path, "output.app_id", "--direction", "up")

    assert result["address"] == "output.app_id"
    assert "downstream" not in result
    assert _hops(result["upstream"]) == [
        (1, "output.app_id", "module.app.output.id", "reference"),
        (2, "module.app.output.id", "module.app.resource.aws_thing.this", "reference"),
        (3, "module.app.resource.aws_thing.this", "module.app.variable.app_name", "reference"),
        (4, "module.app.variable.app_name", "module.app", "module-input"),
        (5, "module.app", "variable.name", "reference"),
    ]


def test_trace_downstream_follows_only_the_matching_module_input(tmp_path):
    _write_tree(tmp_path, _TRACE_FILES)

    result = _trace_json(tmp_path, "variable.name", "--direction", "down")

    assert "upstream" not in result
    assert _hops(result["downstream"]) == [
        (1, "module.app", "variable.name", "reference"),
        (2, "module.app.variable.app_name", "module.app", "module-input"),
        (3, "module.app.resource.aws_thing.this", "module.app.variable.app_name", "reference"),
        (4, "module.app.output.id", "module.app.resource.aws_thing.this", "reference"),
        (5, "output.app_id", "module.app.output.id", "reference"),
    ]


def test_trace_depth_limits_hops_and_both_is_the_default(tmp_path):
    _write_tree(tmp_path, _TRACE_FILES)

    result = _trace_json(tmp_path, "module.app.resource.aws_thing.this", "--depth", "1")

    assert result["depth"] == 1
    assert _hops(result["upstream"]) == [
        (1, "module.app.resource.aws_thing.this", "module.app.variable.app_name", "reference"),
    ]
    assert _hops(result["downstream"]) == [
        (1, "module.app.output.id", "module.app.resource.aws_thing.this", "reference"),
    ]
    entry = result["upstream"][0]
    assert entry["resolution"] == "resolved"
    assert entry["location"] == "app/main.tf:3:38-3:50"
    assert entry["field"] == "name"


def test_trace_stops_at_unresolved_targets_and_cycles(tmp_path):
    _write_tree(tmp_path, {"main.tf": (
        'locals {\n  a = local.b\n  b = local.a\n  c = var.missing\n}\n'
        'output "x" { value = [local.a, local.c] }\n'
    )})

    result = _trace_json(tmp_path, "output.x", "--direction", "up")

    assert [(e["consumer"], e["dependency"], e["resolution"]) for e in result["upstream"]] == [
        ("output.x", "local.a", "resolved"),
        ("output.x", "local.c", "resolved"),
        ("local.a", "local.b", "resolved"),
        ("local.c", "variable.missing", "unresolved"),
        ("local.b", "local.a", "resolved"),
    ]


def test_trace_text_output_lists_hops_by_direction(tmp_path, capsys):
    _write_tree(tmp_path, _TRACE_FILES)

    assert main(["trace", str(tmp_path), "app_name", "--depth", "1"]) == 0
    assert capsys.readouterr().out.splitlines() == [
        "module.app.variable.app_name  app/main.tf:1:1-1:23",
        "Upstream (A -> B means A uses B):",
        "  1  module.app.variable.app_name -> module.app (module-input, resolved, main.tf:5:3-5:11, field app_name)",
        "Downstream (A -> B means A uses B):",
        "  1  module.app.resource.aws_thing.this -> module.app.variable.app_name (reference, resolved, app/main.tf:3:38-3:50, field name)",
    ]


def test_trace_reports_when_there_are_no_references(tmp_path, capsys):
    _write_tree(tmp_path, {"main.tf": 'variable "lonely" {}\n'})

    assert main(["trace", str(tmp_path), "lonely"]) == 0
    assert capsys.readouterr().out.splitlines() == [
        "variable.lonely  main.tf:1:1-1:21",
        "Upstream (A -> B means A uses B):",
        "  none",
        "Downstream (A -> B means A uses B):",
        "  none",
    ]


@pytest.mark.parametrize(("query", "message"), [
    ("nothing", "No entity found matching 'nothing'."),
    ("dup", "Multiple entities match 'dup':"),
])
def test_trace_needs_a_unique_match(tmp_path, capsys, query, message):
    _write_tree(tmp_path, {"main.tf": 'variable "dup" {}\nlocals {\n  dup = 1\n}\n'})

    assert main(["trace", str(tmp_path), query]) == 1
    assert message in capsys.readouterr().out


def test_trace_writes_json_to_output_file(tmp_path):
    _write_tree(tmp_path / "src", _TRACE_FILES)
    output = tmp_path / "trace.json"

    assert main(["trace", str(tmp_path / "src"), "variable.size", "--format", "json",
                 "--output", str(output)]) == 0
    result = json.loads(output.read_text(encoding="utf-8"))
    assert result["query"] == "variable.size"
    assert result["depth"] is None
    assert result["upstream"] == []
    assert _hops(result["downstream"])[:2] == [
        (1, "module.app", "variable.size", "reference"),
        (2, "module.app.variable.app_size", "module.app", "module-input"),
    ]


@pytest.mark.parametrize(("depth", "message"), [
    ("0", "depth must be 1 or more"), ("two", "depth must be a whole number"),
])
def test_trace_rejects_invalid_depth(tmp_path, capsys, depth, message):
    with pytest.raises(SystemExit) as error:
        main(["trace", str(tmp_path), "x", "--depth", depth])
    assert error.value.code == 2
    assert message in capsys.readouterr().err


def test_trace_lists_each_reference_once_when_a_module_is_reached_twice(tmp_path):
    _write_tree(tmp_path, {
        "main.tf": (
            'locals {\n  shared = 1\n}\n'
            'module "app" {\n  source = "./app"\n  first = local.shared\n  second = local.shared\n}\n'
            'output "all" { value = module.app }\n'
        ),
        "app/main.tf": 'variable "first" {}\nvariable "second" {}\n',
    })

    result = _trace_json(tmp_path, "local.shared", "--direction", "down")

    assert _hops(result["downstream"]) == [
        (1, "module.app", "local.shared", "reference"),
        (1, "module.app", "local.shared", "reference"),
        (2, "module.app.variable.first", "module.app", "module-input"),
        (2, "output.all", "module.app", "reference"),
        (2, "module.app.variable.second", "module.app", "module-input"),
    ]


_SHARED_MODULE = (
    'variable "v" {}\n'
    'resource "aws_y" "y" { name = var.v }\n'
    'output "out" { value = aws_y.y.name }\n'
    'output "out2" { value = aws_y.y.id }\n'
)


def test_unused_reports_shared_module_declarations_only_when_every_call_ignores_them(tmp_path, capsys):
    _write_tree(tmp_path, {
        "main.tf": (
            'module "a" {\n  source = "./mod"\n  v = "1"\n}\n'
            'module "b" {\n  source = "./mod"\n  v = "2"\n}\n'
            'output "from_a" { value = module.a.out }\n'
        ),
        "mod/main.tf": _SHARED_MODULE,
    })

    assert main(["unused", str(tmp_path)]) == 1
    assert capsys.readouterr().out.splitlines() == [
        "module.a.output.out2 (output) at mod/main.tf:4:1-4:37",
        "module.b.output.out2 (output) at mod/main.tf:4:1-4:37",
    ]


def test_unused_does_not_treat_depends_on_or_moved_as_reading_module_outputs(tmp_path, capsys):
    _write_tree(tmp_path, {
        "main.tf": (
            'module "a" {\n  source = "./mod"\n  v = "1"\n}\n'
            'output "from_a" { value = module.a.out }\n'
            'resource "aws_x" "r" {\n  depends_on = [module.a]\n}\n'
            'moved {\n  from = module.old\n  to = module.a\n}\n'
        ),
        "mod/main.tf": _SHARED_MODULE,
    })

    assert main(["unused", str(tmp_path)]) == 1
    assert capsys.readouterr().out.splitlines() == [
        "module.a.output.out2 (output) at mod/main.tf:4:1-4:37",
    ]


_WHOLE_MODULE_FILES = {
    "main.tf": (
        'module "b" {\n  source = "./mod"\n  count = 2\n  v = "x"\n}\n'
        'output "whole" { value = module.b }\n'
        'output "splat" { value = module.b[*].out2 }\n'
    ),
    "mod/main.tf": _SHARED_MODULE,
}


def test_trace_downstream_continues_from_outputs_to_whole_module_readers(tmp_path):
    _write_tree(tmp_path, _WHOLE_MODULE_FILES)

    result = _trace_json(tmp_path, "module.b.resource.aws_y.y", "--direction", "down")

    assert _hops(result["downstream"]) == [
        (1, "module.b.output.out", "module.b.resource.aws_y.y", "reference"),
        (1, "module.b.output.out2", "module.b.resource.aws_y.y", "reference"),
        (2, "module.b", "module.b.output.out", "module-output"),
        (2, "module.b", "module.b.output.out2", "module-output"),
        (3, "output.splat", "module.b", "reference"),
        (3, "output.whole", "module.b", "reference"),
    ]


def test_trace_upstream_from_a_whole_module_reference_enters_its_outputs(tmp_path):
    _write_tree(tmp_path, _WHOLE_MODULE_FILES)

    result = _trace_json(tmp_path, "output.whole", "--direction", "up")

    assert _hops(result["upstream"]) == [
        (1, "output.whole", "module.b", "reference"),
        (2, "module.b", "module.b.output.out", "module-output"),
        (2, "module.b", "module.b.output.out2", "module-output"),
        (3, "module.b.output.out", "module.b.resource.aws_y.y", "reference"),
        (3, "module.b.output.out2", "module.b.resource.aws_y.y", "reference"),
        (4, "module.b.resource.aws_y.y", "module.b.variable.v", "reference"),
        (5, "module.b.variable.v", "module.b", "module-input"),
    ]
    assert result["upstream"][1]["location"] == "mod/main.tf:3:1-3:38"
    assert result["upstream"][1]["field"] is None


def test_trace_skips_a_variable_validating_itself(tmp_path, capsys):
    _write_tree(tmp_path, {"main.tf": (
        'variable "x" {\n  validation {\n    condition = var.x != ""\n'
        '    error_message = "empty"\n  }\n}\n'
    )})

    result = _trace_json(tmp_path, "variable.x")

    assert result["upstream"] == [] and result["downstream"] == []


def test_report_fail_on_ignores_repeated_states(tmp_path, capsys):
    _write_tree(tmp_path / "src", {"main.tf": 'output "x" { value = var.missing }\n'})

    assert main(["report", str(tmp_path / "src"), "--format", "json", "--output", str(tmp_path / "r.json"),
                 "--fail-on", "unresolved,unresolved"]) == 1
    assert "warning: 1 unresolved reference(s); failing" in capsys.readouterr().err


@pytest.mark.parametrize(("command", "query"), [
    ("find", "var.name"), ("trace", "var.name"), ("find", "prod::var.name"),
])
def test_queries_accept_terraform_var_syntax(tmp_path, capsys, command, query):
    _write_tree(tmp_path / "prod", {"main.tf": 'variable "name" {}\noutput "x" { value = var.name }\n'})
    root = tmp_path if query.startswith("prod::") else tmp_path / "prod"

    assert main([command, str(root), query, "--format", "json"]) == 0
    result = json.loads(capsys.readouterr().out)
    address = result["match"]["address"] if command == "find" else result["address"]
    assert address.endswith("variable.name")
