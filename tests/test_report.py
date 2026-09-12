from __future__ import annotations

from pathlib import Path
import json
import re

from iaclineage.hcl_source import index_repository
from iaclineage.report import write_html_report, write_interactive_html_report


FIXTURES = Path(__file__).parent / "fixtures" / "references"


def test_json_paths_are_portable_including_references_and_diagnostics(tmp_path):
    from iaclineage.report import write_json_report

    project = tmp_path / "project space" / "módulo"
    project.mkdir(parents=True)
    (project / "main.tf").write_bytes(
        b'variable "input" {}\r\noutput "result" { value = var.input }\r\n'
    )
    (project / "broken.tf").write_bytes(b'output "broken" { value =\r\n')
    output = tmp_path / "report.json"
    write_json_report(index_repository(tmp_path), output)
    data = json.loads(output.read_text(encoding="utf-8"))
    for category, key in [("entities", "path"), ("references", "source_path"), ("diagnostics", "path")]:
        assert data[category]
        for item in data[category]:
            assert item[key].startswith("project space/módulo/")
            assert "\\" not in item[key]


def test_nested_blocks_preserve_identity_and_only_export_known_provisioner_types(tmp_path: Path) -> None:
    index = index_repository(FIXTURES.parent / "nested_blocks")
    assert not index.diagnostics
    entity = next(e for e in index.entities if e.kind == "resource")
    assert [f.path for f in entity.fields] == [
        "root_block_device[0]", "connection[0]", *[f"provisioner[{i}]" for i in range(5)], "arbitrary[0]",
    ]
    assert [f.provisioner_type for f in entity.fields[2:7]] == ["remote-exec", "remote-exec", "file", "local-exec", None]
    references = {(r.source_field, r.traversal) for r in index.references}
    assert ("root_block_device[0].volume_size", "var.size") in references
    assert ("connection[0].private_key", "var.key_path") in references
    assert ("provisioner[0].inline[0]", "var.hostname") in references
    assert ("provisioner[0].inline[1]", "var.username") in references
    assert ("provisioner[1].connection[0].user", "var.username") in references
    output = tmp_path / "report.html"
    write_interactive_html_report(index, output)
    safe = output.read_text(encoding="utf-8")
    assert "CANARY" not in safe
    fields = next(n for n in _payload(safe)["nodes"] if n["kind"] == "resource")["fields"]
    assert fields[2]["provisionerType"] == "remote-exec"
    assert "provisionerType" not in fields[6]
    assert fields[1]["children"][0]["contextReferences"] == [{
        "traversal": "self.public_ip", "location": entity.fields[1].children[0].context_references[0].source_range.display(),
    }]
    assert fields[3]["children"][0]["contextReferences"][0]["traversal"] == "path.module"
    assert len(_payload(safe)["edges"]) == 8
    write_interactive_html_report(index, output, include_source_values=True)
    opted = output.read_text(encoding="utf-8")
    assert "COMMAND_CANARY" in opted
    opted_fields = next(n for n in _payload(opted)["nodes"] if n["kind"] == "resource")["fields"]
    assert opted_fields[1]["children"][0]["contextReferences"] == fields[1]["children"][0]["contextReferences"]


def test_writes_structural_evidence_without_raw_fixture_values(tmp_path: Path) -> None:
    output_path = tmp_path / "report.html"
    write_html_report(index_repository(FIXTURES), output_path)
    report = output_path.read_text(encoding="utf-8")

    assert "resource.example_service.api" in report
    assert "resource.example_network.app" in report
    assert "resolved direct static reference at" in report
    assert 'resource &quot;example_service&quot; &quot;api&quot; { ... }' in report
    assert "not-printed-by-the-scanner" not in report


def test_interactive_report_is_graph_first_and_hides_values_by_default(tmp_path: Path) -> None:
    fixture = tmp_path / "fixture"
    fixture.mkdir()
    (fixture / "main.tf").write_text(
        'variable "token" { default = "top-secret" }\n'
        'output "token" { value = var.token }\n',
        encoding="utf-8",
    )

    output_path = tmp_path / "lineage.html"
    write_interactive_html_report(index_repository(fixture), output_path)
    report = output_path.read_text(encoding="utf-8")

    assert "IaCLineage interactive report" in report
    assert "Focused lineage" in report
    assert 'id="browse-view"' in report
    assert 'id="object-view" hidden' in report
    assert "All reachable" in report
    assert "Reference evidence" in report
    assert 'id="report' + '-settings"' not in report
    assert 'id="report' + '-density"' not in report
    assert "Automatic depth" in report
    assert "Expand visible branches" in report
    assert 'id="lineage-view"' in report
    assert '<option value="graph" selected>Graph only</option>' in report
    assert 'id="lineage-table" aria-labelledby="connection-heading" hidden' in report
    assert "Center selected" in report
    assert "Graph + table" in report
    assert 'class="graph-legend"' in report
    assert "Selected declaration" in report
    assert "Expression boundary" in report
    assert "No declarations recovered" in report
    assert "No fields recovered" in report
    assert "status-badge" in report
    assert "align-items: start" in report
    assert "__REPORT_CSS__" not in report
    assert "__REPORT_JS__" not in report
    assert "__LINEAGE_DATA__" not in report
    assert '"usage": "value = var.token"' in report
    assert "top-secret" not in report
    assert '"sourceText": null' in report


def _payload(report: str) -> dict:
    match = re.search(r'<script id="lineage-data" type="application/json">(.*?)</script>', report, re.S)
    assert match
    return json.loads(match.group(1))


def test_conditional_metadata_is_structural_and_source_text_is_opt_in(tmp_path: Path) -> None:
    from iaclineage.report import _render_json

    fixture = tmp_path / "fixture"
    fixture.mkdir()
    (fixture / "main.tf").write_text(
        'locals { x = var.flag == "CONDITION_CANARY" /* COMMENT_CANARY */ ? '
        '"</script>TRUE_CANARY" : "FALSE_CANARY" }', encoding="utf-8")
    index = index_repository(fixture)
    output = tmp_path / "report.html"
    write_interactive_html_report(index, output)
    safe = output.read_text(encoding="utf-8")
    structural_json = _render_json(index)
    write_html_report(index, tmp_path / "static.html")
    static = (tmp_path / "static.html").read_text(encoding="utf-8")
    for canary in ("CONDITION_CANARY", "COMMENT_CANARY", "TRUE_CANARY", "FALSE_CANARY"):
        assert canary not in safe + structural_json + static
    data = _payload(safe)
    conditional = data["nodes"][0]["fields"][0]["conditionals"][0]
    assert [p["role"] for p in conditional["parts"]] == ["condition", "true", "false"]
    assert all(p["value"] is None for p in conditional["parts"])
    assert data["edges"][0]["conditionalRoles"] == [{"conditional": conditional["id"], "role": "condition"}]
    write_interactive_html_report(index, output, include_source_values=True)
    opted = output.read_text(encoding="utf-8")
    assert opted.count("</script>") == 2
    parts = _payload(opted)["nodes"][0]["fields"][0]["conditionals"][0]["parts"]
    assert "CONDITION_CANARY" in parts[0]["value"]
    assert parts[1]["value"] == '"</script>TRUE_CANARY"'


def test_interactive_binding_direction_and_project_identity(tmp_path: Path) -> None:
    fixture = Path(__file__).parent / "fixtures" / "module_repo"
    output = tmp_path / "report.html"
    write_interactive_html_report(index_repository(fixture), output)
    data = _payload(output.read_text(encoding="utf-8"))
    binding = next(edge for edge in data["edges"] if edge["kind"] == "module-input")
    assert (binding["source"], binding["sourceField"]) == ("module.app.variable.app_name", "value")
    assert (binding["target"], binding["targetField"]) == ("module.app", "app_name")
    assert {node["project"] for node in data["nodes"]} == {"(root)"}


def test_fields_are_structural_and_opted_in_source_cannot_end_script(tmp_path: Path) -> None:
    fixture = tmp_path / "fixture"
    fixture.mkdir()
    secret = '</script><script>alert("CANARY")</script>'
    (fixture / "main.tf").write_text(
        'variable "items" {}\nresource "example" "x" {\n'
        '  name = var.items["INDEX_SECRET"].name\n'
        '  tags = { Name = "VALUE_SECRET", "KEY_SECRET" = "another-secret" }\n'
        '  notes = <<EOF\n' + secret + '\nEOF\n}\n', encoding="utf-8",
    )
    index = index_repository(fixture)
    output = tmp_path / "report.html"
    write_interactive_html_report(index, output)
    safe = output.read_text(encoding="utf-8")
    for value in ("INDEX_SECRET", "VALUE_SECRET", "KEY_SECRET", "another-secret", "CANARY"):
        assert value not in safe
    payload = _payload(safe)
    resource = next(node for node in payload["nodes"] if node["kind"] == "resource")
    assert [field["path"] for field in resource["fields"]] == ["name", "tags", "notes"]
    assert all(field["value"] is None for field in resource["fields"])
    assert resource["fields"][1]["children"][0]["path"] == "tags.Name"
    assert payload["edges"][0]["certainty"] == "expression"
    write_interactive_html_report(index, output, include_source_values=True)
    opted_in = output.read_text(encoding="utf-8")
    assert secret not in opted_in
    assert opted_in.count("</script>") == 2
    assert secret in next(node for node in _payload(opted_in)["nodes"] if node["kind"] == "resource")["sourceText"]


def test_repeated_addresses_keep_separate_inspectors_and_ambiguous_targets(tmp_path: Path) -> None:
    fixture = tmp_path / "fixture"
    fixture.mkdir()
    (fixture / "main.tf").write_text(
        'resource "example" "same" { first = 1 }\n'
        'resource "example" "same" { second = 2 }\n'
        'output "test" { value = example.same.id }\n', encoding="utf-8",
    )
    output = tmp_path / "report.html"
    write_interactive_html_report(index_repository(fixture), output)
    data = _payload(output.read_text(encoding="utf-8"))
    nodes = [node for node in data["nodes"] if node["kind"] == "resource"]
    assert nodes[0]["id"] != nodes[1]["id"]
    assert nodes[0]["fields"][0]["path"] == "first"
    assert nodes[1]["fields"][0]["path"] == "second"
    assert data["edges"][0]["resolution"] == "ambiguous"


def test_interactive_report_shows_values_only_when_opted_in(tmp_path: Path) -> None:
    fixture = tmp_path / "fixture"
    fixture.mkdir()
    (fixture / "main.tf").write_text(
        'variable "token" { default = "top-secret" }\n', encoding="utf-8"
    )

    output_path = tmp_path / "lineage.html"
    write_interactive_html_report(
        index_repository(fixture), output_path, include_source_values=True
    )
    report = output_path.read_text(encoding="utf-8")

    assert "Sensitive content enabled." in report
    assert "top-secret" in report


def test_interactive_report_groups_shared_modules_by_source_path(tmp_path: Path) -> None:
    common = tmp_path / "common" / "modules" / "network"
    project = tmp_path / "project_a"
    common.mkdir(parents=True)
    project.mkdir()
    (common / "main.tf").write_text('output "id" { value = "network-id" }\n', encoding="utf-8")
    (project / "main.tf").write_text(
        'module "network" { source = "../common/modules/network" }\n'
        'output "network_id" { value = module.network.id }\n',
        encoding="utf-8",
    )

    output_path = tmp_path / "lineage.html"
    write_interactive_html_report(index_repository(tmp_path), output_path)

    assert '"group": "common / modules / network"' in output_path.read_text(encoding="utf-8")
