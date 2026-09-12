"""Synthetic installed-module snapshots; these tests never invoke Terraform."""

import json
from pathlib import Path

import pytest

from iaclineage.hcl_source import index_repository
from iaclineage.report import write_interactive_html_report


REMOTE = "git::https://example.invalid/modules.git//app?ref=v1"


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def cache(root: Path, records: list[dict]) -> None:
    write(root / ".terraform/modules/modules.json", json.dumps({"Modules": records}))


def project(root: Path, source: str = REMOTE) -> None:
    write(root / "main.tf", f'module "app" {{ source = "{source}"\n name = var.name }}\n'
          'variable "name" {}\noutput "id" { value = module.app.id }\n')


def record(key: str = "app", source: str = REMOTE, directory: str = ".terraform/modules/app") -> dict:
    return {"Key": key, "Source": source, "Dir": directory}


def test_downloaded_git_module_resolves_inputs_outputs_and_keeps_source_private(tmp_path):
    root = tmp_path / "project"
    project(root)
    write(root / ".terraform/modules/app/main.tf", 'variable "name" {}\n'
          'output "id" { value = var.name }\nvariable "secret" { default = "CACHE_CANARY" }')
    cache(root, [record()])
    before = {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()}
    index = index_repository(root)
    refs = {(r.source_address, r.target_address, r.resolution) for r in index.references}
    assert ("module.app", "module.app.variable.name", "resolved") in refs
    assert ("output.id", "module.app.output.id", "resolved") in refs
    assert not index.diagnostics
    report = tmp_path / "report.html"
    write_interactive_html_report(index, report)
    assert "CACHE_CANARY" not in report.read_text(encoding="utf-8")
    assert ".terraform/modules/app/main.tf" in report.read_text(encoding="utf-8")
    write_interactive_html_report(index, report, include_source_values=True)
    assert "CACHE_CANARY" in report.read_text(encoding="utf-8")
    assert before == {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()}


def test_nested_remote_and_local_calls_use_root_manifest_keys(tmp_path):
    project(tmp_path)
    write(tmp_path / ".terraform/modules/app/main.tf", 'module "nested" { source = "vendor/network/aws" }\n'
          'module "local" { source = "./local" }')
    write(tmp_path / ".terraform/modules/app/local/main.tf", 'variable "local_input" {}')
    write(tmp_path / ".terraform/modules/app.nested/main.tf", 'variable "nested_input" {}')
    cache(tmp_path, [record(), record("app.nested", "registry.terraform.io/vendor/network/aws", ".terraform/modules/app.nested")])
    addresses = {e.address for e in index_repository(tmp_path).entities}
    assert "module.app.module.nested.variable.nested_input" in addresses
    assert "module.app.module.local.variable.local_input" in addresses


def test_each_independent_project_uses_its_own_manifest(tmp_path):
    for name in ("a", "b"):
        root = tmp_path / name
        project(root)
        write(root / ".terraform/modules/app/main.tf", f'variable "{name}" {{}}')
        cache(root, [record()])
    addresses = {e.address for e in index_repository(tmp_path).entities}
    assert "a::module.app.variable.a" in addresses
    assert "b::module.app.variable.b" in addresses
    assert "a::module.app.variable.b" not in addresses
    assert not any(address.startswith("a/.terraform") for address in addresses)


@pytest.mark.parametrize("failure", ["missing", "malformed", "duplicate", "source_changed", "directory_missing", "no_entry"])
def test_unusable_cache_stays_unresolved_with_safe_diagnostic(tmp_path, failure):
    project(tmp_path)
    write(tmp_path / ".terraform/modules/app/main.tf", 'variable "name" {}')
    if failure == "malformed":
        write(tmp_path / ".terraform/modules/modules.json", "INVALID_SECRET")
    elif failure == "duplicate":
        cache(tmp_path, [record(), record()])
    elif failure == "source_changed":
        cache(tmp_path, [record(source=REMOTE + "SECRET")])
    elif failure == "directory_missing":
        cache(tmp_path, [record(directory=".terraform/modules/absent")])
    elif failure == "no_entry":
        cache(tmp_path, [])
    index = index_repository(tmp_path)
    assert any(r.target_address == "module.app.output.id" and r.resolution == "unresolved" for r in index.references)
    assert [d.code for d in index.diagnostics] == ["IAC103"]
    assert "SECRET" not in index.diagnostics[0].message
    assert "example.invalid" not in index.diagnostics[0].message


@pytest.mark.parametrize("directory", ["../outside", "C:/outside", "//server/share", "C:outside"])
def test_manifest_cannot_redirect_remote_source_outside_default_cache(tmp_path, directory):
    project(tmp_path)
    cache(tmp_path, [record(directory=directory)])
    index = index_repository(tmp_path)
    assert [d.code for d in index.diagnostics] == ["IAC103"]
    assert not any(e.address.startswith("module.app.") for e in index.entities)


def test_local_child_remote_call_and_repeated_cached_directory_keep_call_identity(tmp_path):
    write(tmp_path / "main.tf", 'module "parent" { source = "./parent" }')
    write(tmp_path / "parent/main.tf", f'module "a" {{ source = "{REMOTE}" }}\nmodule "b" {{ source = "{REMOTE}" }}')
    write(tmp_path / ".terraform/modules/shared/main.tf", 'variable "name" {}')
    cache(tmp_path, [record("parent.a", directory=".terraform/modules/shared"), record("parent.b", directory=".terraform/modules/shared")])
    addresses = {e.address for e in index_repository(tmp_path).entities}
    assert "module.parent.module.a.variable.name" in addresses
    assert "module.parent.module.b.variable.name" in addresses
