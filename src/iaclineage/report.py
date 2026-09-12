"""Write simple HTML or JSON reports with per‑file scoping."""

from __future__ import annotations

import json
import os
import tempfile
from collections import defaultdict
from pathlib import Path
from importlib.resources import files
from html import escape

from .diagnostics import DiagnosticError
from .model import ConditionalRole, RepositoryIndex, TerraformField


def write_html_report(index: RepositoryIndex, output_path: Path) -> None:
    _write_report(index, output_path, "html")


def write_json_report(index: RepositoryIndex, output_path: Path) -> None:
    _write_report(index, output_path, "json")


def write_interactive_html_report(
    index: RepositoryIndex,
    output_path: Path,
    *,
    include_source_values: bool = False,
) -> None:
    _write_report(index, output_path, "interactive-html", include_source_values=include_source_values)


def _write_report(
    index: RepositoryIndex,
    output_path: Path,
    fmt: str,
    *,
    include_source_values: bool = False,
) -> None:
    if not output_path.parent.is_dir():
        raise DiagnosticError("IAC005", "output directory does not exist")

    if fmt == "html":
        content = _render_html(index)
    elif fmt == "json":
        content = _render_json(index)
    elif fmt == "interactive-html":
        content = _render_interactive_html(index, include_source_values=include_source_values)
    else:
        raise DiagnosticError("IAC008", f"Unsupported format: {fmt}")

    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=output_path.parent, delete=False
        ) as tmp:
            tmp_path = Path(tmp.name)
            tmp.write(content)
        os.replace(tmp_path, output_path)
    except OSError as error:
        raise DiagnosticError("IAC006", "report could not be written") from error
    finally:
        if tmp_path is not None and tmp_path.exists():
            tmp_path.unlink(missing_ok=True)


def _render_html(index: RepositoryIndex) -> str:
    rows = []
    for entity in index.entities:
        # Filter references by both address and path (per‑file scoping)
        refs = [
            r for r in index.references
            if r.source_address == entity.address
            and r.source_range.path == entity.source_range.path
        ]
        ref_list = "".join(
            f"<li><code>{escape(r.target_address)}</code> "
            f"<span class='{r.resolution}'>({r.resolution} direct static reference "
            f"at {escape(r.source_range.display())})</span></li>"
            for r in refs
        )
        ref_display = f"<ul>{ref_list}</ul>" if ref_list else "<span class='none'>(none)</span>"
        rows.append(f"""
        <tr>
            <td><code>{escape(entity.address)}</code></td>
            <td><span class="kind">{escape(entity.kind)}</span></td>
            <td><code>{escape(entity.source_range.display())}</code></td>
            <td><code>{escape(entity.snippet())}</code></td>
            <td>{ref_display}</td>
        </tr>
        """)

    table_body = "".join(rows)

    return f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <title>IaCLineage report</title>
    <style>
        body {{ font-family: system-ui, sans-serif; margin: 2rem; }}
        table {{ border-collapse: collapse; width: 100%; }}
        th, td {{ border: 1px solid #ddd; padding: 0.5rem; text-align: left; vertical-align: top; }}
        th {{ background: #f4f4f4; }}
        .kind {{ display: inline-block; padding: 0.2rem 0.5rem; border-radius: 4px; background: #e0e0e0; font-size: 0.8rem; }}
        .resolved {{ color: #2e7d32; }}
        .unresolved {{ color: #c62828; }}
        .location {{ color: #666; font-size: 0.8rem; }}
        .none {{ color: #999; font-style: italic; }}
        ul {{ margin: 0; padding-left: 1.2rem; }}
        li {{ margin: 0.2rem 0; }}
    </style>
</head>
<body>
    <h1>IaCLineage report</h1>
    <p>Static source evidence only. No Terraform/OpenTofu execution performed.</p>
    <p><strong>{len(index.entities)}</strong> entities · <strong>{len(index.references)}</strong> references</p>
    <table>
        <thead>
            <tr><th>Address</th><th>Kind</th><th>Location</th><th>Snippet</th><th>Outgoing References (per file)</th></tr>
        </thead>
        <tbody>
            {table_body}
        </tbody>
    </table>
    <hr>
    <h2>Diagnostics</h2>
    <ul>
    {"".join(f"<li><code>{d.code}</code>: {escape(d.message)} at {escape(d.source_range.display())}</li>" for d in index.diagnostics) or "<li>None</li>"}
    </ul>
</body>
</html>"""


def _role_data(role: ConditionalRole) -> dict:
    return {"conditional": role.conditional_range.display(), "role": role.role}


def _field_data(field: TerraformField, include_values: bool) -> dict:
    return {
        "path": field.path,
        "kind": field.kind,
        **({"provisionerType": field.provisioner_type} if field.provisioner_type else {}),
        "location": field.source_range.display(),
        "value": field.value if include_values else None,
        "children": [_field_data(child, include_values) for child in field.children],
        "contextReferences": [
            {"traversal": ref.traversal, "location": ref.source_range.display()}
            for ref in field.context_references
        ],
        "conditionals": [
            {
                "id": conditional.source_range.display(),
                "context": [_role_data(role) for role in conditional.context],
                "parts": [
                    {"role": part.role, "location": part.source_range.display(),
                     "value": part.value if include_values else None}
                    for part in conditional.parts
                ],
            }
            for conditional in field.conditionals
        ],
    }


def _render_interactive_html(index: RepositoryIndex, *, include_source_values: bool) -> str:
    """Embed packaged browser assets and safely encoded source evidence."""
    by_address = defaultdict(list)
    for entity in index.entities:
        by_address[entity.address].append(entity)

    def node_id(entity):
        if len(by_address[entity.address]) == 1:
            return entity.address
        return entity.address + "#" + entity.source_range.display()

    edges = []
    for i, reference in enumerate(index.references):
        binding = reference.kind == "module-input"
        # Duplicate addresses remain individually inspectable. A target with
        # multiple candidates is never guessed.
        sources = by_address.get(reference.source_address, ())
        source = next((entity for entity in sources
                       if entity.source_range.path == reference.source_range.path
                       and entity.source_range.start_byte <= reference.source_range.start_byte
                       and reference.source_range.end_byte <= entity.source_range.end_byte), None)
        source_id = node_id(source) if source else reference.source_address
        targets = by_address.get(reference.target_address, ())
        target_id = node_id(targets[0]) if len(targets) == 1 else reference.target_address
        resolution = "ambiguous" if len(targets) > 1 else reference.resolution
        # All interactive edges point from consumer to dependency. Legacy
        # index bindings retain their call-to-child direction for CLI/JSON.
        edges.append({
            "id": i,
            "source": target_id if binding else source_id,
            "target": source_id if binding else target_id,
            "sourceField": reference.target_field if binding else reference.source_field,
            "targetField": reference.source_field if binding else reference.target_field,
            "resolution": resolution,
            "location": reference.source_range.display(),
            "usage": reference.usage,
            "traversal": reference.traversal,
            "kind": reference.kind,
            "certainty": reference.certainty,
            "conditionalRoles": [_role_data(role) for role in reference.conditional_roles],
        })
    data = {
        "includeSourceValues": include_source_values,
        "nodes": [
            {
                "id": node_id(entity),
                "address": entity.address,
                "kind": entity.kind,
                "location": entity.source_range.display(),
                "path": entity.source_range.path.as_posix(),
                "snippet": entity.snippet(),
                "project": entity.address.split("::", 1)[0] if "::" in entity.address else "(root)",
                "group": _group_for_path(entity.source_range.path),
                "sourceText": entity.source_text if include_source_values else None,
                "fields": [_field_data(field, include_source_values) for field in entity.fields],
            }
            for entity in index.entities
        ],
        "edges": edges,
        "diagnostics": [
            {
                "code": diagnostic.code,
                "message": diagnostic.message,
                "location": diagnostic.source_range.display(),
                "path": diagnostic.source_range.path.as_posix(),
            }
            for diagnostic in index.diagnostics
        ],
    }
    # Escaping every '<' also prevents script/comment injection in opted-in
    # source. Payload insertion is last so source cannot expand asset markers.
    safe_data = json.dumps(data, ensure_ascii=False).replace("<", "\\u003c")
    assets = files("iaclineage").joinpath("ui")
    template = assets.joinpath("report.html").read_text(encoding="utf-8")
    template = template.replace("__REPORT_CSS__", assets.joinpath("report.css").read_text(encoding="utf-8"))
    template = template.replace("__REPORT_JS__", assets.joinpath("report.js").read_text(encoding="utf-8"))
    return template.replace("__LINEAGE_DATA__", safe_data)


def _group_for_path(path: Path) -> str:
    parts = path.as_posix().split("/")[:-1]
    return " / ".join(parts) if parts else "(root)"


def _render_json(index: RepositoryIndex) -> str:
    # Include path information for each entity and reference
    data = {
        "entities": [
            {
                "address": e.address,
                "kind": e.kind,
                "location": e.source_range.display(),
                "path": e.source_range.path.as_posix(),
                "snippet": e.snippet(),
                "fields": [_field_data(field, False) for field in e.fields],
            }
            for e in index.entities
        ],
        "references": [
            {
                "source": r.source_address,
                "target": r.target_address,
                "resolution": r.resolution,
                "source_location": r.source_range.display(),
                "source_path": r.source_range.path.as_posix(),
                "usage": r.usage,
                "conditionalRoles": [_role_data(role) for role in r.conditional_roles],
            }
            for r in index.references
        ],
        "diagnostics": [
            {
                "code": d.code,
                "message": d.message,
                "location": d.source_range.display(),
                "path": d.source_range.path.as_posix(),
            }
            for d in index.diagnostics
        ],
    }
    return json.dumps(data, indent=2)
