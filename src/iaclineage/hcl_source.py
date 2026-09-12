"""Parser-backed Terraform structural indexing with source evidence.

The repository is indexed as a Terraform *module graph* rather than a single
flat namespace. The scanned directory is the root module (its ``.tf`` files,
non-recursively). Each ``module "name"`` call whose ``source`` is a local
relative path (``./`` or ``../``) is followed and indexed under the
``module.name.`` address prefix, even when the source directory lives outside
the scanned root. Registry and remote modules are read from the root project's
installed-module manifest when available. They are never fetched by this tool.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from tree_sitter import Language, Node, Parser
import tree_sitter_hcl

from .diagnostics import DiagnosticError
from .installed_modules import InstalledModules
from .model import ParseDiagnostic, RepositoryIndex, SourceRange, StaticReference, TerraformEntity, TerraformField
from .model import ConditionalExpression, ConditionalPart, ConditionalRole, ContextReference

_PARSER = Parser(Language(tree_sitter_hcl.language()))
_SUPPORTED_BLOCKS = frozenset({
    "terraform",       # version constraints, backend, required providers
    "provider",        # provider configuration
    "resource",        # managed resources
    "data",            # data sources
    "module",          # module calls
    "output",          # output values
    "variable",        # input variables
    "locals",          # local values
    "moved",           # refactoring address mappings (Terraform 1.1+)
    "import",          # import existing resources (Terraform 1.5+)
    "check",           # health checks (Terraform 1.5+)
    "provider_meta",   # provider-specific metadata (rarely used, but valid)
})

# How many string labels are expected for each block type.
_EXPECTED_LABELS = {
    "resource": 2,
    "data": 2,
    "variable": 1,
    "output": 1,
    "module": 1,
    "provider": 1,
    "check": 1,
    "provider_meta": 1,
    "terraform": 0,
    "locals": 0,
    "moved": 0,    # indexed with a synthetic per-module counter
    "import": 0,   # indexed with a synthetic per-module counter
}

# Module-call arguments that configure the call itself rather than binding a
# child input variable.
_MODULE_META_ARGS = frozenset({
    "source", "version", "count", "for_each", "providers", "provider",
    "depends_on", "lifecycle",
})

# Attribute values that must not be treated as entity references. ``provider``
# and ``providers`` point at provider configurations, not resources.
_SKIP_REFERENCE_ATTRS = frozenset({"provider", "providers"})

# Traversal roots that are language built-ins, not references to declarations.
_NON_REFERENCE_ROOTS = frozenset({"count", "each", "self", "path"})

_EXCLUDED_DIRECTORY_NAMES = frozenset({".git", ".terraform"})

# Separator between a project namespace and an address in multi-project mode.
_PROJECT_SEPARATOR = "::"


@dataclass(frozen=True)
class _Traversal:
    """A traversal occurrence, including operands without an expression wrapper."""

    named_children: tuple[Node, ...]
    parent: Node

    @property
    def start_byte(self) -> int:
        return self.named_children[0].start_byte

    @property
    def end_byte(self) -> int:
        return self.named_children[-1].end_byte


def index_repository(
    repository: Path, *, progress: Callable[[str, Path | None], None] | None = None,
) -> RepositoryIndex:
    """Index the module graph rooted at ``repository``.

    If ``repository`` is a Terraform root module (it has ``.tf`` files of its
    own) it is indexed as a single module graph. Otherwise it is treated as a
    container of independent projects: each discovered root module is indexed
    under its own ``<relative-path>::`` namespace so unrelated projects never
    cross-link.
    """
    if not repository.exists():
        raise DiagnosticError("IAC001", "scan path does not exist")
    if not repository.is_dir():
        raise DiagnosticError("IAC002", "scan path must be a directory")

    entities: list[TerraformEntity] = []
    diagnostics: list[ParseDiagnostic] = []
    # (source_address, scope_prefix, display_path, source_bytes, expression_node)
    expressions: list[tuple[str, str, Path, bytes, _Traversal]] = []
    # Concrete call -> child-variable input bindings, resolved after indexing.
    bindings: list[tuple[str, str, SourceRange]] = []

    if any(repository.glob("*.tf")):
        modules = [(repository, "")]
    else:
        modules = [
            (root, f"{Path(os.path.relpath(root, repository)).as_posix()}{_PROJECT_SEPARATOR}")
            for root in _discover_project_roots(repository, progress=progress)
        ]

    for module_dir, prefix in modules:
        _index_module(
            module_dir, prefix, repository,
            entities, diagnostics, expressions, bindings, (), InstalledModules(module_dir), progress,
        )

    known_addresses = {entity.address for entity in entities}
    if progress:
        progress("resolve", None)
    references = _extract_references(expressions, known_addresses, entities)
    references.extend(
        StaticReference(source_address, target_address, source_range,
                        "resolved" if target_address in known_addresses else "unresolved",
                        source_field=target_address.rsplit(".", 1)[-1], target_field="value",
                        kind="module-input")
        for source_address, target_address, source_range in bindings
    )
    return RepositoryIndex(
        entities=tuple(sorted(entities, key=lambda entity: entity.address)),
        references=tuple(sorted(
            references,
            key=lambda reference: (
                reference.source_address,
                reference.target_address,
                reference.source_range.start_byte,
            ),
        )),
        diagnostics=tuple(diagnostics),
    )


def _discover_project_roots(
    container: Path, *, progress: Callable[[str, Path | None], None] | None = None,
) -> list[Path]:
    """Find the shallowest Terraform root modules beneath ``container``.

    A directory is a root module when it holds ``.tf`` files directly. Once found
    it is not descended into, so its own child modules (e.g. ``modules/app``)
    stay part of that project rather than becoming separate projects.
    """
    roots: list[Path] = []

    def walk(directory: Path) -> None:
        if progress:
            progress("directory", directory.relative_to(container))
        if any(directory.glob("*.tf")):
            roots.append(directory)
            return
        for child in sorted(directory.iterdir()):
            if child.is_dir() and child.name not in _EXCLUDED_DIRECTORY_NAMES:
                walk(child)

    for child in sorted(container.iterdir()):
        if child.is_dir() and child.name not in _EXCLUDED_DIRECTORY_NAMES:
            walk(child)
    return roots


def _index_module(
    module_dir: Path,
    prefix: str,
    repo_root: Path,
    entities: list[TerraformEntity],
    diagnostics: list[ParseDiagnostic],
    expressions: list[tuple[str, str, Path, bytes, _Traversal]],
    bindings: list[tuple[str, str, SourceRange]],
    ancestor_dirs: tuple[Path, ...],
    installed: InstalledModules,
    progress: Callable[[str, Path | None], None] | None = None,
) -> None:
    """Index one module directory and follow local or already-installed calls."""
    real_dir = module_dir.resolve()
    if real_dir in ancestor_dirs:
        return  # module cycle; stop rather than recurse forever
    next_ancestors = ancestor_dirs + (real_dir,)

    # Synthetic moved/import counters are shared across the module's files so
    # that addresses stay unique within the module.
    counters: dict[str, int] = {}

    for tf_file in sorted(module_dir.glob("*.tf")):
        if not tf_file.is_file():
            continue
        display_path = _display_path(tf_file, repo_root)
        if progress:
            progress("read", display_path)
        source = _read_utf8_source(tf_file)
        tree = _PARSER.parse(source)
        if tree.root_node.has_error:
            diagnostics.append(
                ParseDiagnostic(
                    "IAC101",
                    "HCL parse recovery was used; results from this file may be incomplete",
                    _range_from_node(display_path, source, tree.root_node),
                )
            )
        for block in _top_level_blocks(tree.root_node):
            _process_block(
                block, prefix, display_path, source, module_dir, repo_root,
                counters, entities, diagnostics, expressions, bindings, next_ancestors, installed, progress,
            )


def _process_block(
    block: Node,
    prefix: str,
    display_path: Path,
    source: bytes,
    module_dir: Path,
    repo_root: Path,
    counters: dict[str, int],
    entities: list[TerraformEntity],
    diagnostics: list[ParseDiagnostic],
    expressions: list[tuple[str, str, Path, bytes, _Traversal]],
    bindings: list[tuple[str, str, SourceRange]],
    ancestor_dirs: tuple[Path, ...],
    installed: InstalledModules,
    progress: Callable[[str, Path | None], None] | None = None,
) -> None:
    parts = tuple(child for child in block.named_children if child.type in {"identifier", "string_lit"})
    if not parts:
        return
    block_kind = _node_text(parts[0], source)
    if block_kind not in _SUPPORTED_BLOCKS:
        return

    if block_kind == "locals":
        _add_locals(prefix, display_path, source, block, entities, expressions)
        return

    if block_kind in {"moved", "import"}:
        count = counters.get(block_kind, 0) + 1
        counters[block_kind] = count
        address = f"{prefix}{block_kind}.{count}"
        _add_entity(block_kind, address, display_path, source, block, entities, expressions, prefix)
        return

    if block_kind == "terraform":
        _add_entity("terraform", f"{prefix}terraform", display_path, source, block, entities, expressions, prefix)
        return

    # Blocks with labels (resource, data, variable, output, module, provider, ...).
    expected = _EXPECTED_LABELS.get(block_kind, 0)
    labels = tuple(_string_label(part, source) for part in parts[1:])
    if len(labels) != expected or any(label is None for label in labels):
        return
    address = prefix + ".".join((block_kind, *(label for label in labels if label is not None)))

    if block_kind == "module":
        _add_module_call(
            address, prefix, display_path, source, block, module_dir, repo_root,
            entities, diagnostics, expressions, bindings, ancestor_dirs, installed, progress,
        )
        return

    _add_entity(block_kind, address, display_path, source, block, entities, expressions, prefix)


def _add_entity(
    kind: str,
    address: str,
    display_path: Path,
    source: bytes,
    block: Node,
    entities: list[TerraformEntity],
    expressions: list[tuple[str, str, Path, bytes, _Traversal]],
    prefix: str,
) -> None:
    entities.append(_entity_from_node(kind, address, display_path, source, block))
    for expression in _reference_expressions(block, source):
        expressions.append((address, prefix, display_path, source, expression))


def _entity_from_node(
    kind: str,
    address: str,
    display_path: Path,
    source: bytes,
    node: Node,
) -> TerraformEntity:
    """Keep source text in memory for explicitly opted-in report output only."""
    return TerraformEntity(
        kind,
        address,
        _range_from_node(display_path, source, node),
        _node_text(node, source),
        _source_fields(node, display_path, source),
    )


def _add_locals(
    prefix: str,
    display_path: Path,
    source: bytes,
    block: Node,
    entities: list[TerraformEntity],
    expressions: list[tuple[str, str, Path, bytes, _Traversal]],
) -> None:
    body = next((child for child in block.named_children if child.type == "body"), None)
    if body is None:
        return
    for attr in body.named_children:
        if attr.type != "attribute":
            continue
        name_node = next((child for child in attr.named_children if child.type == "identifier"), None)
        if name_node is None:
            continue
        address = f"{prefix}local.{_node_text(name_node, source)}"
        entities.append(
            TerraformEntity(
                "local", address, _range_from_node(display_path, source, attr),
                _node_text(attr, source),
                _source_fields(attr, display_path, source),
            )
        )
        value_expr = next((child for child in attr.named_children if child.type == "expression"), None)
        if value_expr is not None:
            for expression in _reference_expressions(value_expr, source):
                expressions.append((address, prefix, display_path, source, expression))


def _add_module_call(
    address: str,
    prefix: str,
    display_path: Path,
    source: bytes,
    block: Node,
    module_dir: Path,
    repo_root: Path,
    entities: list[TerraformEntity],
    diagnostics: list[ParseDiagnostic],
    expressions: list[tuple[str, str, Path, bytes, _Traversal]],
    bindings: list[tuple[str, str, SourceRange]],
    ancestor_dirs: tuple[Path, ...],
    installed: InstalledModules,
    progress: Callable[[str, Path | None], None] | None = None,
) -> None:
    entities.append(_entity_from_node("module", address, display_path, source, block))
    # Argument values are references evaluated in the *calling* module's scope.
    for expression in _reference_expressions(block, source):
        expressions.append((address, prefix, display_path, source, expression))

    child_prefix = f"{address}."
    # Each non-meta argument binds a child input variable.
    for name, name_node in _module_input_arguments(block, source):
        target = f"{child_prefix}variable.{name}"
        bindings.append((address, target, _range_from_node(display_path, source, name_node)))

    source_value = _attribute_string_value(block, source, "source")
    if source_value is None:
        return
    if source_value.startswith(("./", "../")):
        child_dir = module_dir / source_value
    else:
        child_dir, problem = installed.resolve(address, source_value)
        if child_dir is None:
            diagnostics.append(ParseDiagnostic("IAC103", problem, _range_from_node(display_path, source, block)))
            return
    if not child_dir.is_dir():
        diagnostics.append(
            ParseDiagnostic(
                "IAC102",
                "local module source directory was not found; its outputs cannot be verified",
                _range_from_node(display_path, source, block),
            )
        )
        return
    _index_module(
        child_dir, child_prefix, repo_root,
        entities, diagnostics, expressions, bindings, ancestor_dirs, installed, progress,
    )


def _module_input_arguments(block: Node, source: bytes) -> list[tuple[str, Node]]:
    body = next((child for child in block.named_children if child.type == "body"), None)
    if body is None:
        return []
    arguments: list[tuple[str, Node]] = []
    for attr in body.named_children:
        if attr.type != "attribute":
            continue
        name_node = next((child for child in attr.named_children if child.type == "identifier"), None)
        if name_node is None:
            continue
        name = _node_text(name_node, source)
        if name in _MODULE_META_ARGS:
            continue
        arguments.append((name, name_node))
    return arguments


def _attribute_string_value(block: Node, source: bytes, attr_name: str) -> str | None:
    body = next((child for child in block.named_children if child.type == "body"), None)
    if body is None:
        return None
    for attr in body.named_children:
        if attr.type != "attribute":
            continue
        name_node = next((child for child in attr.named_children if child.type == "identifier"), None)
        if name_node is None or _node_text(name_node, source) != attr_name:
            continue
        value_node = next((child for child in attr.named_children if child.type == "expression"), None)
        if value_node is None:
            return None
        try:
            value = json.loads(_node_text(value_node, source))
        except json.JSONDecodeError:
            return None
        return value if isinstance(value, str) else None
    return None


def _read_utf8_source(path: Path) -> bytes:
    try:
        source = path.read_bytes()
        source.decode("utf-8")
    except UnicodeDecodeError as error:
        raise DiagnosticError("IAC003", "Terraform source must be UTF-8") from error
    except OSError as error:
        raise DiagnosticError("IAC004", "Terraform source could not be read") from error
    return source


def _top_level_blocks(root: Node) -> tuple[Node, ...]:
    bodies = tuple(child for child in root.named_children if child.type == "body")
    if not bodies:
        return ()
    return tuple(child for child in bodies[0].named_children if child.type == "block")


def _string_label(node: Node, source: bytes) -> str | None:
    try:
        value = json.loads(_node_text(node, source))
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, str) else None


def _extract_references(
    expressions: list[tuple[str, str, Path, bytes, _Traversal]], known_addresses: set[str],
    entities: list[TerraformEntity],
) -> list[StaticReference]:
    references: list[StaticReference] = []
    seen: set[tuple[str, str, Path, int, int]] = set()
    fields_by_source: dict[tuple[str, Path], tuple[TerraformField, ...]] = {}
    for entity in entities:
        key = (entity.address, entity.source_range.path)
        fields_by_source[key] = fields_by_source.get(key, ()) + entity.fields
    for source_address, scope_prefix, display_path, source, expression in expressions:
        traversal = _static_traversal(expression, source)
        if traversal is None:
            continue
        target_local, reference_node = traversal
        target_address = scope_prefix + target_local
        resolution = "resolved" if target_address in known_addresses else "unresolved"
        key = (source_address, target_address, display_path, reference_node.start_byte, reference_node.end_byte)
        if key in seen:
            continue
        seen.add(key)
        source_field = _containing_field(fields_by_source.get((source_address, display_path), ()), reference_node)
        traversal_text, target_field, certainty = _reference_evidence(reference_node, source)
        references.append(
            StaticReference(
                source_address,
                target_address,
                _range_from_node(display_path, source, reference_node),
                resolution,
                f"{source_field} = {traversal_text}" if source_field else traversal_text,
                source_field, target_field, traversal_text, "reference", certainty,
                _conditional_context(reference_node, display_path, source),
            )
        )
    return references


def _containing_field(fields: tuple[TerraformField, ...], node: Node | _Traversal) -> str:
    for field in fields:
        span = field.source_range
        if span.start_byte <= node.start_byte and node.end_byte <= span.end_byte:
            return _containing_field(field.children, node) or field.path
    return ""


def _reference_evidence(node: Node | _Traversal, source: bytes) -> tuple[str, str, str]:
    """Keep traversal identifiers, never index literals or surrounding strings."""
    parts: list[str] = []
    indexed = False
    for child in node.named_children:
        if child.type == "variable_expr":
            parts.extend(_node_text(n, source) for n in child.named_children if n.type == "identifier")
        elif child.type == "get_attr":
            parts.append(_node_text(child.named_children[0], source))
        elif child.type in {"index", "splat"} or "splat" in child.type:
            indexed = True
    root_size = 3 if parts and parts[0] in {"module", "data"} else 2
    value_root = bool(parts and parts[0] in {"var", "local", "module"})
    target_field = ".".join((["value"] if value_root else []) + parts[root_size:])
    # Do not present an indexed/splat traversal as an exact field mapping.
    text = ".".join(parts) + (" [instance/key selection omitted]" if indexed else "")
    parent = node.parent
    object_value = parent.child_by_field_name("val") if parent is not None and parent.type == "object_elem" else None
    direct = parent is not None and (parent.type in {"attribute", "tuple"} or
                                    object_value is not None and object_value.start_byte == node.start_byte
                                    and object_value.end_byte == node.end_byte)
    ancestor = parent
    while ancestor is not None:
        if ancestor.type in {"conditional", "function_call", "binary_operation", "unary_operation"} or ancestor.has_error:
            direct = False
        if ancestor.type == "block":
            name = next((n for n in ancestor.named_children if n.type == "identifier"), None)
            if name is not None and _node_text(name, source) == "dynamic":
                direct = False
        ancestor = ancestor.parent
    return text, target_field, "direct" if direct and not indexed else "expression"


def _conditional_parts(node: Node) -> tuple[tuple[str, Node], ...]:
    parts = tuple(child for child in node.named_children if child.type == "expression")
    # Recovered syntax must not produce invented branch assignments.
    if node.has_error or len(parts) != 3:
        return ()
    return tuple(zip(("condition", "true", "false"), parts))


def _conditional_context(node: Node | _Traversal, path: Path, source: bytes) -> tuple[ConditionalRole, ...]:
    roles = []
    ancestor = node.parent
    while ancestor is not None:
        if ancestor.type == "conditional":
            for role, part in _conditional_parts(ancestor):
                if part.start_byte <= node.start_byte and node.end_byte <= part.end_byte:
                    roles.append(ConditionalRole(_range_from_node(path, source, ancestor), role))
        ancestor = ancestor.parent
    return tuple(reversed(roles))


def _conditionals(expression: Node, path: Path, source: bytes) -> tuple[ConditionalExpression, ...]:
    result = []

    def walk(node: Node) -> None:
        if node.type == "conditional" and (parts := _conditional_parts(node)):
            result.append(ConditionalExpression(
                _range_from_node(path, source, node),
                tuple(ConditionalPart(role, _range_from_node(path, source, part), _node_text(part, source))
                      for role, part in parts),
                _conditional_context(node, path, source),
            ))
        for child in node.named_children:
            walk(child)

    walk(expression)
    return tuple(result)


def _context_references(expression: Node, path: Path, source: bytes) -> tuple[ContextReference, ...]:
    """Capture contextual roots without evaluating values or creating graph edges."""
    blocks: list[str] = []
    ancestor = expression.parent
    while ancestor is not None:
        if ancestor.type == "block":
            identifier = next((n for n in ancestor.named_children if n.type == "identifier"), None)
            blocks.append(_node_text(identifier, source) if identifier is not None else "")
        ancestor = ancestor.parent
    allow_self = tuple(reversed(blocks)) in {
        ("resource", "connection"), ("resource", "provisioner"),
        ("resource", "provisioner", "connection"),
    }
    result: list[ContextReference] = []
    for traversal in _reference_expressions(expression, source):
        parts = traversal.named_children
        if len(parts) < 2 or parts[1].type != "get_attr":
            continue
        root = _node_text(parts[0], source)
        attribute = next((n for n in parts[1].named_children if n.type == "identifier"), None)
        if attribute is None:
            continue
        name = _node_text(attribute, source)
        if (root == "self" and allow_self) or (root == "path" and name in {"module", "root", "cwd"}):
            # Only the contextual prefix is evidence; index literals and suffixes
            # are neither retained nor interpreted as evaluated attributes.
            prefix = _Traversal(tuple(parts[:2]), traversal.parent)
            result.append(ContextReference(f"{root}.{name}", _range_from_node(path, source, prefix)))
    return tuple(result)


def _source_fields(node: Node, path: Path, source: bytes) -> tuple[TerraformField, ...]:
    """Read source structure from the existing HCL tree without evaluating it.

    Quoted/computed map keys and nested-block labels are literal values: use
    positional names in structural mode. Only known built-in provisioner types
    are retained as structural metadata. Opted-in source still contains labels.
    """
    def field(name: str, expression: Node, span: Node) -> TerraformField:
        children: list[TerraformField] = []
        collection = next((n for n in expression.named_children if n.type == "collection_value"), None)
        kind = "expression"
        if collection is not None and collection.named_children:
            container = collection.named_children[0]
            kind = "object" if container.type == "object" else "tuple"
            for i, element in enumerate(n for n in container.named_children if n.type in {"object_elem", "expression"}):
                if element.type == "object_elem":
                    key = element.child_by_field_name("key")
                    value = element.child_by_field_name("val")
                    key_text = _node_text(key, source) if key is not None else ""
                    # Bare identifier keys are field names; literal keys are not exported.
                    simple = key_text.replace("-", "_").isidentifier()
                    child_name = f"{name}.{key_text}" if simple else f"{name}[key {i + 1}]"
                    if value is not None:
                        children.append(field(child_name, value, element))
                else:
                    children.append(field(f"{name}[{i}]", element, element))
        elif expression.named_children and expression.named_children[0].type == "literal_value":
            kind = "literal"
        elif _static_traversal(expression, source) is not None:
            kind = "reference"
        return TerraformField(name, kind, _range_from_node(path, source, span),
                              _node_text(expression, source), tuple(children),
                              _conditionals(expression, path, source) if not children else (),
                              context_references=_context_references(expression, path, source) if not children else ())

    def body_fields(body: Node, prefix: str = "") -> tuple[TerraformField, ...]:
        result: list[TerraformField] = []
        counters: dict[str, int] = {}
        for child in body.named_children:
            if child.type == "attribute":
                name = next((n for n in child.named_children if n.type == "identifier"), None)
                expression = next((n for n in child.named_children if n.type == "expression"), None)
                if name is not None and expression is not None:
                    result.append(field(prefix + _node_text(name, source), expression, child))
            elif child.type == "block":
                name = next((n for n in child.named_children if n.type == "identifier"), None)
                nested = next((n for n in child.named_children if n.type == "body"), None)
                if name is None:
                    continue
                label = _node_text(name, source)
                counters[label] = counters.get(label, 0) + 1
                block_path = f"{prefix}{label}[{counters[label] - 1}]"
                provisioner_type = None
                if label == "provisioner" and not prefix and _node_text(node.named_children[0], source) == "resource":
                    labels = [n for n in child.named_children if n.type == "string_lit"]
                    if len(labels) == 1:
                        provisioner_type = {f'"{kind}"': kind for kind in ("file", "local-exec", "remote-exec")}.get(
                            _node_text(labels[0], source))
                result.append(TerraformField(block_path, "block", _range_from_node(path, source, child),
                                             children=body_fields(nested, block_path + ".") if nested else (),
                                             provisioner_type=provisioner_type))
        return tuple(result)

    if node.type == "attribute":  # Each local is a declaration with one value.
        expression = next((n for n in node.named_children if n.type == "expression"), None)
        return (field("value", expression, node),) if expression else ()
    body = next((n for n in node.named_children if n.type == "body"), None)
    return body_fields(body) if body else ()


def _static_traversal(expression: Node | _Traversal, source: bytes) -> tuple[str, Node | _Traversal] | None:
    """Return the scope-local target address for a direct traversal, if any."""
    if _is_provider_argument(expression, source):
        return None
    children = expression.named_children
    if not children or children[0].type != "variable_expr":
        return None
    base_parts = tuple(_node_text(node, source) for node in children[0].named_children if node.type == "identifier")
    attributes = tuple(
        _node_text(node.named_children[0], source)
        for node in children[1:]
        if node.type == "get_attr" and node.named_children and node.named_children[0].type == "identifier"
    )
    parts = (*base_parts, *attributes)
    if len(parts) < 2 or parts[0] in _NON_REFERENCE_ROOTS:
        return None
    if parts[0] == "var":
        return ("variable." + parts[1], expression)
    if parts[0] == "module" and len(parts) >= 3:
        # A parent reference module.NAME.OUT points at the child module's output.
        return (f"module.{parts[1]}.output.{parts[2]}", expression)
    if parts[0] in {"module", "local"}:
        return (".".join(parts[:2]), expression)
    if parts[0] == "data" and len(parts) >= 3:
        return (".".join(parts[:3]), expression)
    if parts[0] == "terraform":
        return ("terraform", expression)
    return ("resource." + ".".join(parts[:2]), expression)


def _is_provider_argument(expression: Node | _Traversal, source: bytes) -> bool:
    parent = expression.parent
    if parent is None or parent.type != "attribute":
        return False
    name_node = next((child for child in parent.named_children if child.type == "identifier"), None)
    return name_node is not None and _node_text(name_node, source) in _SKIP_REFERENCE_ATTRS


def _reference_expressions(node: Node, source: bytes) -> tuple[_Traversal, ...]:
    """Collect each traversal once, including comparison and unary operands."""
    result: list[_Traversal] = []

    def walk(current: Node) -> None:
        if current.type == "attribute":
            name_node = next((child for child in current.named_children if child.type == "identifier"), None)
            if name_node is not None and _node_text(name_node, source) in _SKIP_REFERENCE_ATTRS:
                return
        if current.type == "variable_expr" and current.parent is not None:
            container = current.parent
            parts = [current]
            sibling = current.next_named_sibling
            while sibling is not None and (sibling.type in {"get_attr", "index"} or "splat" in sibling.type):
                parts.append(sibling)
                sibling = sibling.next_named_sibling
            # Expression wrappers are transparent only for ordinary traversals.
            parent = container.parent if container.type == "expression" else container
            result.append(_Traversal(tuple(parts), parent or container))
        for child in current.named_children:
            walk(child)

    walk(node)
    return tuple(result)


def _display_path(tf_file: Path, repo_root: Path) -> Path:
    """Return a portable path for display, relative to the scanned root.

    Files outside the root (followed local modules) render with ``..`` segments
    rather than an absolute path that would expose the machine layout.
    """
    return Path(os.path.relpath(tf_file, repo_root))


def _range_from_node(relative_path: Path, source: bytes, node: Node | _Traversal) -> SourceRange:
    text_before_start = source[: node.start_byte].decode("utf-8")
    text_before_end = source[: node.end_byte].decode("utf-8")
    start_line, start_column = _display_position(text_before_start)
    end_line, end_column = _display_position(text_before_end)
    return SourceRange(relative_path, start_line, start_column, end_line, end_column, node.start_byte, node.end_byte)


def _display_position(text: str) -> tuple[int, int]:
    lines = text.splitlines(keepends=True)
    if not lines:
        return (1, 1)
    if text.endswith(("\n", "\r")):
        return (len(lines) + 1, 1)
    return (len(lines), len(lines[-1]) + 1)


def _node_text(node: Node, source: bytes) -> str:
    return source[node.start_byte : node.end_byte].decode("utf-8")
