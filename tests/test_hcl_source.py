from __future__ import annotations

from pathlib import Path
import pytest

from iaclineage.hcl_source import index_repository


FIXTURES = Path(__file__).parent / "fixtures" / "references"
MODULE_REPO = Path(__file__).parent / "fixtures" / "module_repo"


def test_context_evidence_preserves_fields_and_spans_without_dependency_edges(tmp_path: Path) -> None:
    source = '''variable "name" {}
locals { dirs = [path.module, "${path.root}", path.cwd] }
resource "example" "app" {
  connection { host = self.public_ip }
  provisioner "remote-exec" {
    inline = ["é ${self.id} ${var.name} ${path.module}/SECRET_CANARY", self.tags["INDEX_CANARY"]]
    connection { host = "${self.private_ip}" }
  }
  provisioner "file" { source = "${path.module}/FILE_CANARY" }
}
'''
    (tmp_path / "main.tf").write_text(source, encoding="utf-8")
    index = index_repository(tmp_path)
    assert not index.diagnostics

    def contexts(fields):
        return [(f.path, ref) for f in fields for ref in f.context_references] + [
            item for f in fields for item in contexts(f.children)]

    resource = next(e for e in index.entities if e.kind == "resource")
    evidence = contexts(resource.fields)
    assert {(field, ref.traversal) for field, ref in evidence} == {
        ("connection[0].host", "self.public_ip"),
        ("provisioner[0].inline[0]", "self.id"),
        ("provisioner[0].inline[0]", "path.module"),
        ("provisioner[0].inline[1]", "self.tags"),
        ("provisioner[0].connection[0].host", "self.private_ip"),
        ("provisioner[1].source", "path.module"),
    }
    local = next(e for e in index.entities if e.kind == "local")
    assert [(field, ref.traversal) for field, ref in contexts(local.fields)] == [
        ("value[0]", "path.module"), ("value[1]", "path.root"), ("value[2]", "path.cwd"),
    ]
    for _, ref in evidence + contexts(local.fields):
        span = ref.source_range
        assert span.path == Path("main.tf")
        assert (tmp_path / "main.tf").read_bytes()[span.start_byte:span.end_byte].decode("utf-8") == ref.traversal
        assert "CANARY" not in repr(ref)
    assert [(r.source_field, r.target_address) for r in index.references] == [
        ("provisioner[0].inline[0]", "variable.name"),
    ]


def test_context_evidence_ignores_literals_unknown_paths_and_self_outside_scope(tmp_path: Path) -> None:
    (tmp_path / "main.tf").write_text('''
locals { invalid = [self.id, path.unknown, "path.module", "$${self.id}", each.key, count.index] }
resource "example" "app" {
  name = self.id
  connection_like { host = self.id }
  settings = { connection = { host = self.id } }
}
data "example" "app" { connection { host = self.id } }
''', encoding="utf-8")
    index = index_repository(tmp_path)
    assert not index.diagnostics

    def check(fields):
        for field in fields:
            assert not field.context_references
            check(field.children)

    for entity in index.entities:
        check(entity.fields)
    assert not index.references


def test_conditional_references_keep_three_occurrences_and_utf8_ranges(tmp_path: Path) -> None:
    source = ('variable "environment" {}\nvariable "context" {}\n'
              'locals { input = { note = "é", environment = '
              'var.environment == null ? var.context.environment : var.environment } }')
    (tmp_path / "main.tf").write_text(source, encoding="utf-8")
    index = index_repository(tmp_path)
    refs = sorted(index.references, key=lambda ref: ref.source_range.start_byte)
    assert [ref.traversal for ref in refs] == ["var.environment", "var.context.environment", "var.environment"]
    assert [ref.conditional_roles[0].role for ref in refs] == ["condition", "true", "false"]
    assert all(ref.certainty == "expression" and ref.resolution == "resolved" for ref in refs)
    assert all(ref.source_field == "value.environment" for ref in refs)
    for ref in refs:
        span = ref.source_range
        assert (tmp_path / "main.tf").read_bytes()[span.start_byte:span.end_byte].decode() == ref.traversal
        assert source.splitlines()[span.start_line - 1][span.start_column - 1:span.end_column - 1] == ref.traversal
    field = next(e for e in index.entities if e.address == "local.input").fields[0].children[1]
    assert len(field.conditionals) == 1
    assert [part.role for part in field.conditionals[0].parts] == ["condition", "true", "false"]


@pytest.mark.parametrize("expression,expected", [
    ('(!var.a || var.b == var.c) ? [var.d] : var.e', ["var.a", "var.b", "var.c", "var.d", "var.e"]),
    ('var.a ? "yes" : "no"', ["var.a"]),
    ('var.a + var.b', ["var.a", "var.b"]),
    ('var.a ? var.items["secret"].name : var.missing', ["var.a", "var.items.name [instance/key selection omitted]", "var.missing"]),
])
def test_operator_operands_are_individual_expression_evidence(tmp_path: Path, expression, expected) -> None:
    (tmp_path / "main.tf").write_text('locals { result = ' + expression + ' }', encoding="utf-8")
    refs = sorted(index_repository(tmp_path).references, key=lambda ref: ref.source_range.start_byte)
    assert [ref.traversal for ref in refs] == expected
    assert all(ref.certainty == "expression" for ref in refs)


def test_nested_and_multiple_conditionals_keep_ordered_context(tmp_path: Path) -> None:
    (tmp_path / "main.tf").write_text(
        'locals { result = concat(var.a ? (var.b ? [var.c] : []) : [], var.d ? [] : []) }', encoding="utf-8")
    index = index_repository(tmp_path)
    field = index.entities[0].fields[0]
    assert len(field.conditionals) == 3
    assert [len(c.context) for c in field.conditionals] == [0, 1, 0]
    ref = next(r for r in index.references if r.traversal == "var.c")
    assert [role.role for role in ref.conditional_roles] == ["true", "true"]
    assert ref.certainty == "expression"
    assert len({c.source_range.start_byte for c in field.conditionals}) == 3


def test_plain_object_assignment_stays_direct(tmp_path: Path) -> None:
    (tmp_path / "main.tf").write_text('locals { x = { name = var.name } }', encoding="utf-8")
    ref = index_repository(tmp_path).references[0]
    assert ref.certainty == "direct"
    assert ref.conditional_roles == ()


def test_conditional_roles_survive_installed_module_and_project_scopes(tmp_path: Path) -> None:
    import json

    for project in ("one", "two"):
        root = tmp_path / project
        cache = root / ".terraform" / "modules"
        child = cache / "label"
        child.mkdir(parents=True)
        (root / "main.tf").write_text('module "label" { source = "example/label/test" }', encoding="utf-8")
        (cache / "modules.json").write_text(json.dumps({"Modules": [
            {"Key": "label", "Source": "example/label/test", "Dir": ".terraform/modules/label"}
        ]}), encoding="utf-8")
        (child / "main.tf").write_text(
            'variable "flag" {}\noutput "x" { value = var.flag ? "a" : "b" }', encoding="utf-8")
    index = index_repository(tmp_path)
    assert len(index.references) == 2
    for ref in index.references:
        prefix = ref.source_address.split("::")[0]
        assert ref.target_address == prefix + "::module.label.variable.flag"
        assert ref.resolution == "resolved"
        assert [role.role for role in ref.conditional_roles] == ["condition"]
        assert ref.source_range.path.as_posix().startswith(prefix + "/.terraform/modules/label/")


def test_parse_recovery_does_not_claim_conditional_roles_or_direct_mapping(tmp_path: Path) -> None:
    (tmp_path / "main.tf").write_text('locals { x = var.a ? var.b : }', encoding="utf-8")
    index = index_repository(tmp_path)
    assert index.diagnostics
    assert all(ref.certainty == "expression" and not ref.conditional_roles for ref in index.references)
    assert not index.entities[0].fields[0].conditionals


def _references(index):
    return {
        (reference.source_address, reference.target_address, reference.resolution)
        for reference in index.references
    }


def test_indexes_supported_entities_with_exact_relative_source_ranges() -> None:
    index = index_repository(FIXTURES)

    assert [entity.address for entity in index.entities] == [
        "output.api_network",
        "resource.example_network.app",
        "resource.example_service.api",
    ]
    service = next(entity for entity in index.entities if entity.address == "resource.example_service.api")
    assert service.source_range.display() == "main.tf:3:1-5:2"
    assert service.snippet() == 'resource "example_service" "api" { ... }'


def test_extracts_only_direct_static_traversals_and_resolves_local_targets() -> None:
    index = index_repository(FIXTURES)

    assert [(reference.source_address, reference.target_address, reference.resolution) for reference in index.references] == [
        ("output.api_network", "resource.example_service.api", "resolved"),
        ("resource.example_service.api", "resource.example_network.app", "resolved"),
    ]
    assert [reference.usage for reference in index.references] == [
        "value = example_service.api.network_id",
        "network_id = example_network.app.id",
    ]


def test_parse_recovery_is_visible_without_retaining_source_content(tmp_path: Path) -> None:
    source = tmp_path / "broken.tf"
    source.write_text('resource "example" "bad" { secret = "CANARY"', encoding="utf-8")
    index = index_repository(tmp_path)

    assert len(index.diagnostics) == 1
    assert index.diagnostics[0].code == "IAC101"
    assert "CANARY" not in index.diagnostics[0].message


def test_local_module_children_are_namespaced_under_the_call() -> None:
    index = index_repository(MODULE_REPO)
    addresses = {entity.address for entity in index.entities}

    assert {
        "variable.name",
        "output.app_id",
        "output.missing",
        "module.app",
        "module.app.variable.app_name",
        "module.app.resource.aws_thing.this",
        "module.app.output.id",
    } <= addresses
    # The child variable must not leak into the root namespace.
    assert "variable.app_name" not in addresses


def test_module_output_reference_resolves_to_the_child_output() -> None:
    references = _references(index_repository(MODULE_REPO))

    assert ("output.app_id", "module.app.output.id", "resolved") in references
    # A reference to a non-existent output is honestly reported as unresolved.
    assert ("output.missing", "module.app.output.does_not_exist", "unresolved") in references


def test_module_input_binding_maps_the_call_argument_to_the_child_variable() -> None:
    references = _references(index_repository(MODULE_REPO))

    # The call sets the child input variable, and the value flows from the root.
    assert ("module.app", "module.app.variable.app_name", "resolved") in references
    assert ("module.app", "variable.name", "resolved") in references
    # Inside the child, the resource reads its own input variable.
    assert (
        "module.app.resource.aws_thing.this",
        "module.app.variable.app_name",
        "resolved",
    ) in references


def test_follows_local_module_source_outside_the_scanned_root(tmp_path: Path) -> None:
    root = tmp_path / "root"
    shared = tmp_path / "shared"
    root.mkdir(parents=True)
    shared.mkdir(parents=True)
    (root / "main.tf").write_text(
        'module "net" {\n  source   = "../shared"\n  vpc_cidr = "10.0.0.0/16"\n}\n'
        'output "vpc" {\n  value = module.net.vpc_id\n}\n',
        encoding="utf-8",
    )
    (shared / "main.tf").write_text(
        'variable "vpc_cidr" {\n  type = string\n}\n'
        'output "vpc_id" {\n  value = var.vpc_cidr\n}\n',
        encoding="utf-8",
    )

    index = index_repository(root)
    addresses = {entity.address for entity in index.entities}
    references = _references(index)

    assert "module.net.output.vpc_id" in addresses
    assert "module.net.variable.vpc_cidr" in addresses
    assert ("output.vpc", "module.net.output.vpc_id", "resolved") in references
    assert ("module.net", "module.net.variable.vpc_cidr", "resolved") in references


def test_missing_local_module_source_reports_a_diagnostic(tmp_path: Path) -> None:
    (tmp_path / "main.tf").write_text(
        'module "gone" {\n  source = "./does-not-exist"\n}\n', encoding="utf-8"
    )
    index = index_repository(tmp_path)

    assert [d.code for d in index.diagnostics] == ["IAC102"]


def test_moved_and_import_addresses_are_unique_across_files(tmp_path: Path) -> None:
    (tmp_path / "a.tf").write_text(
        'moved {\n  from = a.x\n  to = a.y\n}\n'
        'import {\n  to = a.z\n  id = "1"\n}\n',
        encoding="utf-8",
    )
    (tmp_path / "b.tf").write_text(
        'moved {\n  from = b.x\n  to = b.y\n}\n'
        'import {\n  to = b.z\n  id = "2"\n}\n',
        encoding="utf-8",
    )
    addresses = sorted(e.address for e in index_repository(tmp_path).entities)

    assert addresses == ["import.1", "import.2", "moved.1", "moved.2"]


def test_container_of_projects_is_namespaced_without_cross_linking(tmp_path: Path) -> None:
    # Two independent projects that happen to share a resource address.
    for name in ("app_one", "app_two"):
        project = tmp_path / name
        project.mkdir()
        (project / "main.tf").write_text(
            'resource "aws_vpc" "main" {\n  cidr_block = "10.0.0.0/16"\n}\n'
            'output "vpc" {\n  value = aws_vpc.main.id\n}\n',
            encoding="utf-8",
        )

    index = index_repository(tmp_path)
    addresses = {entity.address for entity in index.entities}

    assert "app_one::resource.aws_vpc.main" in addresses
    assert "app_two::resource.aws_vpc.main" in addresses
    # Every reference stays inside its own project namespace.
    for reference in index.references:
        assert reference.source_address.split("::")[0] == reference.target_address.split("::")[0]


def test_shared_module_directories_are_discovered_as_projects(tmp_path: Path) -> None:
    # A container whose projects live under nested directories.
    net = tmp_path / "common" / "modules" / "network"
    net.mkdir(parents=True)
    (net / "main.tf").write_text('variable "cidr" {\n  type = string\n}\n', encoding="utf-8")

    index = index_repository(tmp_path)
    addresses = {entity.address for entity in index.entities}

    assert "common/modules/network::variable.cidr" in addresses


def test_provider_and_builtin_arguments_are_not_treated_as_references(tmp_path: Path) -> None:
    (tmp_path / "main.tf").write_text(
        'import {\n  to = aws_instance.web\n  id = "i-1"\n  provider = aws.eu\n}\n'
        'resource "aws_instance" "web" {\n  count = var.n\n  name = "web-${count.index}"\n}\n'
        'variable "n" {\n  type = number\n}\n',
        encoding="utf-8",
    )
    references = _references(index_repository(tmp_path))
    targets = {target for _, target, _ in references}

    # The provider meta-argument aws.eu must not become a resource reference.
    assert "resource.aws.eu" not in targets
    # count.index is a language built-in, not a reference to a declaration.
    assert "resource.count.index" not in targets
    # Real references are still captured.
    assert ("resource.aws_instance.web", "variable.n", "resolved") in references


def test_nested_fields_and_repeated_blocks_keep_reference_locations(tmp_path: Path) -> None:
    (tmp_path / "main.tf").write_text(
        'variable "port" {}\n'
        'locals { settings = { port = var.port } }\n'
        'resource "example" "app" {\n'
        '  ingress { port = local.settings.port }\n'
        '  ingress { port = var.port }\n'
        '  ports = [var.port, 443]\n'
        '}\n', encoding="utf-8",
    )
    index = index_repository(tmp_path)
    resource = next(entity for entity in index.entities if entity.kind == "resource")
    assert [field.path for field in resource.fields] == ["ingress[0]", "ingress[1]", "ports"]
    assert resource.fields[0].children[0].path == "ingress[0].port"
    refs = [ref for ref in index.references if ref.source_address == resource.address]
    assert [(ref.source_field, ref.target_field) for ref in refs] == [
        ("ingress[0].port", "value.port"), ("ingress[1].port", "value"), ("ports[0]", "value"),
    ]
    local = next(entity for entity in index.entities if entity.kind == "local")
    assert local.fields[0].children[0].path == "value.port"
    assert local.source_range.start_line == 2


def test_expression_reference_is_evidence_not_exact_value_mapping(tmp_path: Path) -> None:
    (tmp_path / "main.tf").write_text(
        'variable "name" {}\n'
        'output "name" { value = upper(var.name) }\n', encoding="utf-8",
    )
    reference = index_repository(tmp_path).references[0]
    assert reference.source_field == "value"
    assert reference.traversal == "var.name"
    assert reference.certainty == "expression"


def test_dynamic_block_reference_is_not_an_exact_field_chain(tmp_path: Path) -> None:
    (tmp_path / "main.tf").write_text(
        'variable "ports" {}\nvariable "name" {}\n'
        'resource "example" "x" {\n'
        ' dynamic "ingress" {\n'
        '  for_each = var.ports\n'
        '  content { name = var.name }\n'
        ' }\n}\n', encoding="utf-8",
    )
    index = index_repository(tmp_path)
    assert len(index.references) == 2
    assert all(reference.certainty == "expression" for reference in index.references)
    assert index.references[1].source_field == "dynamic[0].for_each" or index.references[0].source_field == "dynamic[0].for_each"
