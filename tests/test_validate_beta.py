"""The acceptance runner's tests are hermetic; no sibling checkout is required."""

import importlib.util
import json
from contextlib import contextmanager
from pathlib import Path

import pytest


spec = importlib.util.spec_from_file_location("validate_beta", Path(__file__).parents[1] / "scripts/validate_beta.py")
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


@pytest.fixture
def suite(tmp_path):
    root = tmp_path / "suite"
    root.mkdir()
    (root / "main.tf").write_text(
        'variable "flag" {}\nlocals { x = var.flag ? "SECRET_CANARY" : "other" }\n', encoding="utf-8")
    return root


def scenario(**overrides):
    return {"id": "example", "root": ".", "canaries": ["SECRET_CANARY"], "expect": {
        "references": [{"source": "local.x", "target": "variable.flag", "certainty": "expression", "roles": ["condition"]}],
        "conditionals": [{"address": "local.x", "field": "value", "roles": ["condition", "true", "false"]}],
    }, **overrides}


def test_runner_produces_repeatable_safe_results_without_changing_inputs(suite, tmp_path):
    manifest = tmp_path / "expectations.json"
    manifest.write_text(json.dumps([scenario()]), encoding="utf-8")
    before = runner.snapshot(suite)
    first = runner.run(suite, tmp_path / "first", manifest)
    second = runner.run(suite, tmp_path / "second", manifest)
    assert first["success"]
    assert first["results"] == second["results"]
    assert len(first["manifest_sha256"]) == 64
    assert runner.snapshot(suite) == before
    assert {p.name for p in (tmp_path / "first").iterdir()} == {"results.json", "summary.md"}
    assert "SECRET_CANARY" not in (tmp_path / "first/results.json").read_text(encoding="utf-8")


@pytest.mark.parametrize("change", ["edge", "role", "diagnostic", "privacy"])
def test_incorrect_expectations_and_privacy_fail(suite, tmp_path, change):
    case = scenario()
    if change == "edge":
        case["expect"]["references"][0]["target"] = "variable.wrong"
    elif change == "role":
        case["expect"]["references"][0]["roles"] = ["true"]
    elif change == "diagnostic":
        case["diagnostics"] = ["IAC101"]
    else:
        # This identifier intentionally appears in structural output.
        case["canaries"] = ["variable.flag"]
    result = runner.run_scenario(suite, tmp_path / "out", case)
    assert result["status"] == "fail"
    assert result["failures"]
    assert "SECRET_CANARY" not in json.dumps(result)


def test_missing_required_and_optional_fixtures_have_distinct_exit_policy(suite, tmp_path):
    manifest = tmp_path / "cases.json"
    optional = scenario(id="optional", root="missing", required=False)
    unsupported = {"id": "unsupported", "required": False, "unsupported": "Runtime evaluation is not supported."}
    manifest.write_text(json.dumps([optional, unsupported]), encoding="utf-8")
    result = runner.run(suite, tmp_path / "out", manifest)
    assert result["success"]
    assert [r["status"] for r in result["results"]] == ["blocked", "unsupported"]
    optional["required"] = True
    manifest.write_text(json.dumps([optional]), encoding="utf-8")
    assert not runner.run(suite, tmp_path / "out", manifest)["success"]


def test_outputs_inside_suite_are_rejected_before_writing(suite):
    with pytest.raises(runner.OutputPathError):
        runner.run(suite, suite / "artifacts")
    assert not (suite / "artifacts").exists()


@pytest.mark.parametrize("source", ['', 'output "x" { value = 1 }'])
def test_outputs_inside_followed_outside_module_are_rejected(suite, tmp_path, source):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "main.tf").write_text(source, encoding="utf-8")
    (suite / "main.tf").write_text('module "m" { source = "../outside" }', encoding="utf-8")
    manifest = tmp_path / "cases.json"
    manifest.write_text(json.dumps([scenario()]), encoding="utf-8")
    with pytest.raises(runner.OutputPathError):
        runner.run(suite, outside / "reports", manifest)
    assert not (outside / "reports").exists()


def test_input_mutation_is_a_failure(suite, tmp_path, monkeypatch):
    original = runner.check_reports

    def change_input(*args):
        result = original(*args)
        (suite / "extra.tf").write_text('locals { extra = 1 }', encoding="utf-8")
        return result

    monkeypatch.setattr(runner, "check_reports", change_input)
    result = runner.run_scenario(suite, tmp_path / "out", scenario())
    assert result["status"] == "fail"
    assert "fixture inputs changed during validation" in result["failures"]


@pytest.mark.parametrize("keys", [("source", "target"), ("sourceField", "targetField")],
                         ids=["direction", "field-mapping"])
def test_interactive_binding_direction_is_checked(tmp_path, monkeypatch, keys):
    original = runner.write_interactive_html_report

    def reverse_binding(index, path, **kwargs):
        original(index, path, **kwargs)
        text = path.read_text(encoding="utf-8")
        data = runner.ReportData(text)
        bindings = [edge for edge in data.payload["edges"] if edge["kind"] == "module-input"]
        assert len(bindings) == 1, "fixture must produce exactly one module-input edge"
        binding = bindings[0]
        first, second = keys
        assert binding[first] != binding[second], "mutation must change the binding"
        binding[first], binding[second] = binding[second], binding[first]
        # Edit the embedded payload independently of JSON spacing/key order.
        payload = "".join(data.chunks)
        assert text.count(payload) == 1
        changed = json.dumps(data.payload, ensure_ascii=False).replace("<", "\\u003c")
        text = text.replace(payload, changed, 1)
        path.write_text(text, encoding="utf-8")

    case = {"id": "binding", "files": {
        "main.tf": 'module "m" { source = "./child"\n x = 1 }',
        "child/main.tf": 'variable "x" {}',
    }, "expect": {"entities": [{"address": "module.m.variable.x"}]}}
    baseline = runner.run_scenario(tmp_path, tmp_path.parent / "out", case)
    assert baseline["status"] == "pass", baseline
    monkeypatch.setattr(runner, "write_interactive_html_report", reverse_binding)
    result = runner.run_scenario(tmp_path, tmp_path.parent / "out", case)
    assert result["status"] == "fail"
    assert "interactive report: reference direction or field mapping differs from index" in result["failures"]


@pytest.mark.parametrize("generated", [True, False], ids=["temporary-fixture", "existing-suite"])
def test_resolved_sources_stay_inside_aliased_boundary(tmp_path, monkeypatch, generated):
    original = runner.tempfile.TemporaryDirectory

    @contextmanager
    def aliased_temporary_directory(**kwargs):
        with original(**kwargs) as temporary:
            alias = Path(temporary) / "alias"
            alias.mkdir()
            yield str(alias / "..")

    suite = tmp_path / "suite"
    suite.mkdir()
    (suite / "alias").mkdir()
    source = 'variable "x" {}'
    (suite / "main.tf").write_text(source, encoding="utf-8")
    case = {"id": "aliased", "expect": {"entities": [{"address": "variable.x"}]}}
    if generated:
        case["files"] = {"main.tf": source}
    else:
        case["root"] = "."
    monkeypatch.setattr(runner.tempfile, "TemporaryDirectory", aliased_temporary_directory)
    result = runner.run_scenario(suite / "alias" / "..", tmp_path / "out", case)
    assert result["status"] == "pass", result


def test_followed_source_outside_boundary_remains_blocked(tmp_path):
    suite = tmp_path / "suite"
    suite.mkdir()
    (suite / "alias").mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "main.tf").write_text('variable "x" {}', encoding="utf-8")
    (suite / "main.tf").write_text('module "m" { source = "../outside" }', encoding="utf-8")
    result = runner.run_scenario(suite / "alias" / "..", tmp_path / "out",
                                 {"id": "outside", "root": "."})
    assert result["status"] == "blocked"
    assert result["reason"] == "followed source is outside the fingerprinted fixture boundary"


def test_materialization_cannot_escape_temporary_fixture(tmp_path):
    with pytest.raises(ValueError):
        runner.run_scenario(tmp_path, tmp_path.parent / "out", {"id": "escape", "files": {"../escape.tf": ""}})


def test_empty_manifest_unknown_selection_and_forbidden_edges_fail(tmp_path):
    manifest = tmp_path / "cases.json"
    manifest.write_text("[]", encoding="utf-8")
    with pytest.raises(ValueError):
        runner.run(tmp_path / "suite", tmp_path / "out", manifest)
    manifest.write_text(json.dumps([scenario()]), encoding="utf-8")
    with pytest.raises(ValueError):
        runner.run(tmp_path / "suite", tmp_path / "out", manifest, ["typo"])
    assert runner.check_expectations({"references": [{"source": "a", "target": "b"}]},
                                     {"absent": {"references": [{"source": "a", "target": "b"}]}})


def test_source_in_exception_is_not_written_to_summary(suite, tmp_path, monkeypatch):
    def broken(_):
        raise RuntimeError("SECRET_CANARY from source")
    monkeypatch.setattr(runner, "index_repository", broken)
    manifest = tmp_path / "cases.json"
    manifest.write_text(json.dumps([scenario()]), encoding="utf-8")
    result = runner.run(suite, tmp_path / "out", manifest)
    assert not result["success"]
    assert result["results"][0]["failures"] == ["validation error: RuntimeError"]
    assert "SECRET_CANARY" not in (tmp_path / "out/summary.md").read_text(encoding="utf-8")


def test_cli_exit_codes_and_selection(suite, tmp_path, monkeypatch):
    manifest = tmp_path / "cases.json"
    manifest.write_text(json.dumps([scenario(), scenario(id="missing", root="absent")]), encoding="utf-8")
    monkeypatch.setattr(runner, "MANIFEST", manifest)
    args = ["--suite-root", str(suite), "--output-dir", str(tmp_path / "out")]
    assert runner.main(args + ["--scenario", "example"]) == 0
    assert runner.main(args) == 1
    assert runner.main(args + ["--scenario", "typo"]) == 2
