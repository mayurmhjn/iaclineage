from __future__ import annotations

import json
from pathlib import Path

import pytest

from iaclineage.cli import main
from iaclineage.hcl_source import index_repository


@pytest.mark.parametrize("expression,expected", [
    ("[for sub in aws_subnet.example : sub.id]", ["aws_subnet.example"]),
    ("{for k, v in aws_subnet.example : k => v.id if v.enabled}", ["aws_subnet.example"]),
    ("[for sub in sub.collection : sub.id]", ["sub.collection"]),
    ("[for sub in aws_subnet.example : [for sub in sub.children : sub.id]]", ["aws_subnet.example"]),
    ("[for sub in aws_subnet.example : sub.items[var.key] if var.enabled]",
     ["aws_subnet.example", "var.key", "var.enabled"]),
    ("concat([for sub in aws_subnet.example : sub.id], [sub.id])", ["aws_subnet.example", "sub.id"]),
    ('[for sub in aws_subnet.example : "${sub.id}-${var.key}"]', ["aws_subnet.example", "var.key"]),
    ("[for var in var.items : var.id]", ["var.items"]),
])
def test_comprehension_lexical_scope(tmp_path: Path, expression: str, expected: list[str]) -> None:
    (tmp_path / "main.tf").write_text('locals { result = ' + expression + ' }', encoding="utf-8")
    index = index_repository(tmp_path)
    assert not index.diagnostics
    refs = sorted(index.references, key=lambda r: r.source_range.start_byte)
    assert [r.traversal for r in refs] == expected


@pytest.mark.parametrize("iterator", ["ingress", "item", "var"])
def test_dynamic_iterator_scope_keeps_collections_and_external_indices(tmp_path: Path, iterator: str) -> None:
    source = f'''variable "key" {{}}
resource "aws_subnet" "example" {{}}
resource "example" "app" {{
  dynamic "ingress" {{
    for_each = {iterator}.collection
    iterator = {iterator}
    labels = ["${{{iterator}.key}}"]
    content {{
      name = {iterator}.value[var.key]
      dynamic "nested" {{
        for_each = {iterator}.value.children
        iterator = {iterator}
        content {{ value = {iterator}.value }}
      }}
      subnet = aws_subnet.example.id
    }}
  }}
  outside = {iterator}.value
}}
'''
    (tmp_path / "main.tf").write_text(source, encoding="utf-8")
    index = index_repository(tmp_path)
    assert not index.diagnostics
    refs = sorted(index.references, key=lambda r: r.source_range.start_byte)
    expected = [f"{iterator}.collection"]
    if iterator != "var":
        expected.append("var.key")
    expected += ["aws_subnet.example.id", f"{iterator}.value"]
    assert [r.traversal for r in refs] == expected
    assert next(r for r in refs if r.traversal == "aws_subnet.example.id").resolution == "resolved"
    for ref in refs:
        span = ref.source_range
        assert (tmp_path / "main.tf").read_bytes()[span.start_byte:span.end_byte].decode() == ref.traversal


def test_default_dynamic_iterator_and_comprehension_share_scope(tmp_path: Path) -> None:
    (tmp_path / "main.tf").write_text('''resource "example" "app" {
  dynamic "ingress" {
    for_each = var.rules
    content { ports = [for port in ingress.value.ports : port.number + var.offset] }
  }
  after = ingress.value
}''', encoding="utf-8")
    index = index_repository(tmp_path)
    assert not index.diagnostics
    assert {r.traversal for r in index.references} == {"var.rules", "var.offset", "ingress.value"}


def test_custom_iterator_does_not_mask_default_name_or_sibling_scope(tmp_path: Path) -> None:
    (tmp_path / "main.tf").write_text('''resource "example" "app" {
  dynamic "ingress" {
    for_each = var.rules
    iterator = item
    content {
      external = ingress.value
      dynamic "nested" {
        for_each = item.value
        content { value = [nested.value, item.key] }
      }
      after = nested.value
    }
  }
}''', encoding="utf-8")
    index = index_repository(tmp_path)
    assert not index.diagnostics
    assert {r.traversal for r in index.references} == {"var.rules", "ingress.value", "nested.value"}


@pytest.mark.parametrize("declaration", ["", "terraform {}"])
def test_terraform_context_never_creates_entity_edges(tmp_path: Path, declaration: str) -> None:
    (tmp_path / "main.tf").write_text(declaration + '''
variable "name" {}
locals { x = [terraform.workspace, terraform.future, "${terraform.workspace}-${var.name}", path.module] }
''', encoding="utf-8")
    index = index_repository(tmp_path)
    assert not index.diagnostics
    assert [(r.target_address, r.resolution) for r in index.references] == [("variable.name", "resolved")]


def test_provider_aliases_bindings_and_duplicate_targets(tmp_path: Path) -> None:
    (tmp_path / "main.tf").write_text('''provider "aws" {}
provider "aws" { alias = "west" }
provider "aws" { alias = "duplicate" }
provider "aws" { alias = "duplicate" }
resource "aws_vpc" "main" { provider = aws.west }
data "aws_region" "current" { provider = aws }
resource "aws_vpc" "missing" { provider = aws.missing }
resource "aws_vpc" "duplicate" { provider = aws.duplicate }
resource "aws_vpc" "attribute" { settings { provider = var.config } }
import {
  to = aws_vpc.main
  id = "example"
  provider = aws.west
}
''', encoding="utf-8")
    index = index_repository(tmp_path)
    assert not index.diagnostics
    providers = [e for e in index.entities if e.kind == "provider"]
    assert [e.address for e in providers] == [
        "provider.aws", "provider.aws.duplicate", "provider.aws.duplicate", "provider.aws.west",
    ]
    assert all(e.snippet() == 'provider "aws" { ... }' for e in providers)
    refs = {(r.source_address, r.target_address): r for r in index.references}
    for address, provider, status in [
        ("resource.aws_vpc.main", "aws.west", "resolved"),
        ("data.aws_region.current", "aws", "resolved"),
        ("import.1", "aws.west", "resolved"),
        ("resource.aws_vpc.missing", "aws.missing", "unresolved"),
        ("resource.aws_vpc.duplicate", "aws.duplicate", "ambiguous"),
    ]:
        ref = refs[address, "provider." + provider]
        assert ref.resolution == status
        assert ref.source_field == "provider"
        assert ref.target_field == ""
        assert ref.traversal == provider
    assert ("resource.aws_vpc.attribute", "variable.config") in refs


@pytest.mark.parametrize("fmt", ["text", "json"])
@pytest.mark.parametrize("query", ["aws_vpc.main", "resource.aws_vpc.main", "main"])
def test_find_resource_addresses(tmp_path: Path, capsys, fmt: str, query: str) -> None:
    (tmp_path / "main.tf").write_text('resource "aws_vpc" "main" {}', encoding="utf-8")
    assert main(["find", str(tmp_path), query, "--format", fmt]) == 0
    assert "resource.aws_vpc.main" in capsys.readouterr().out


@pytest.mark.parametrize("fmt", ["text", "json"])
@pytest.mark.parametrize("query", ["aws", "provider.aws.duplicate"])
def test_find_providers_retains_every_match(tmp_path: Path, capsys, fmt: str, query: str) -> None:
    (tmp_path / "main.tf").write_text('''provider "aws" {}
provider "aws" { alias = "duplicate" }
provider "aws" { alias = "duplicate" }
''', encoding="utf-8")
    assert main(["find", str(tmp_path), query, "--format", fmt]) == 1
    output = capsys.readouterr().out
    if fmt == "json":
        assert len(json.loads(output)["matches"]) == (3 if query == "aws" else 2)
    else:
        assert output.count("provider.aws.duplicate (provider)") == 2


def test_namespaced_resource_and_provider_addresses(tmp_path: Path, capsys) -> None:
    root = tmp_path / "resource.project"
    child = root / "child"
    child.mkdir(parents=True)
    (root / "main.tf").write_text('module "provider" { source = "./child" }', encoding="utf-8")
    (child / "main.tf").write_text('''provider "provider" { alias = "west" }
resource "resource" "main" { provider = provider.west }
''', encoding="utf-8")
    index = index_repository(tmp_path)
    prefix = "resource.project::module.provider."
    assert [(r.target_address, r.resolution) for r in index.references] == [(prefix + "provider.provider.west", "resolved")]
    assert next(e for e in index.entities if e.kind == "provider").snippet() == 'provider "provider" { ... }'
    assert main(["find", str(tmp_path), prefix + "resource.main", "--format", "json"]) == 0
    assert json.loads(capsys.readouterr().out)["match"]["address"] == prefix + "resource.resource.main"


@pytest.mark.parametrize("fmt", ["text", "json"])
def test_duplicate_find_output_file_preserves_status(tmp_path: Path, capsys, fmt: str) -> None:
    (tmp_path / "main.tf").write_text('provider "aws" {}\nprovider "aws" {}', encoding="utf-8")
    output = tmp_path / "matches.txt"
    assert main(["find", str(tmp_path), "provider.aws", "--format", fmt, "--output", str(output)]) == 1
    assert capsys.readouterr().out == ""
    assert output.read_text(encoding="utf-8").count("provider.aws") >= 2


def test_report_json_contains_only_external_dependencies(tmp_path: Path, capsys) -> None:
    (tmp_path / "main.tf").write_text('''provider "aws" { alias = "west" }
variable "rules" {}
resource "aws_vpc" "main" {
  provider = aws.west
  name = terraform.workspace
  dynamic "ingress" {
    for_each = var.rules
    content { ports = [for port in ingress.value.ports : port.number] }
  }
}''', encoding="utf-8")
    output = tmp_path / "report.json"
    assert main(["report", str(tmp_path), "--format", "json", "--output", str(output)]) == 0
    data = json.loads(output.read_text(encoding="utf-8"))
    assert not data["diagnostics"]
    assert {(r["target"], r["resolution"]) for r in data["references"]} == {
        ("provider.aws.west", "resolved"), ("variable.rules", "resolved"),
    }
