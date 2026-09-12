"""Offline developer acceptance checks; never execute fixture-controlled code."""

from __future__ import annotations

import argparse
from collections import Counter
import hashlib
from html.parser import HTMLParser
from importlib.metadata import version
import json
import os
from pathlib import Path
import platform
import re
import subprocess
import tempfile
from unittest.mock import patch

from iaclineage import hcl_source
from iaclineage.hcl_source import index_repository
from iaclineage.report import write_html_report, write_interactive_html_report, write_json_report

REPO = Path(__file__).resolve().parents[1]
MANIFEST = REPO / "tests/acceptance/scenarios.json"


class OutputPathError(ValueError):
    """A validation run must not create artifacts in scanned source."""


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def snapshot(root: Path) -> dict[str, str]:
    """Fingerprint source and manifests, including installed caches, without logging values."""
    result = {}
    for path in sorted(root.rglob("*")):
        if ".git" in path.relative_to(root).parts or not path.is_file():
            continue
        if path.suffix == ".tf" or path.name == "modules.json":
            if not path.resolve().is_relative_to(root.resolve()):
                raise ValueError("fixture input escapes the suite")
            with path.open("rb") as stream:
                result[path.relative_to(root).as_posix()] = hashlib.file_digest(stream, "sha256").hexdigest()
    return result


def contained(root: Path, relative: str) -> Path:
    path = (root / relative).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValueError("fixture path escapes its root")
    return path


def protect_output(output: Path, roots: list[Path]) -> None:
    if any(output.resolve().is_relative_to(root.resolve()) for root in roots):
        raise OutputPathError("output is inside a fixture or followed module directory")


class ReportData(HTMLParser):
    """Read the embedded JSON using the standard HTML parser, not a browser."""

    def __init__(self, text: str):
        super().__init__()
        self.active = False
        self.chunks: list[str] = []
        self.scripts = 0
        self.feed(text)
        self.payload = json.loads("".join(self.chunks))

    def handle_starttag(self, tag, attrs):
        if tag == "script":
            self.scripts += 1
            self.active = dict(attrs).get("id") == "lineage-data"

    def handle_endtag(self, tag):
        if tag == "script":
            self.active = False

    def handle_data(self, data):
        if self.active:
            self.chunks.append(data)


def facts(index, payload: dict) -> dict[str, list[dict]]:
    result = {"entities": [], "references": [], "fields": [], "conditionals": [], "edges": payload["edges"]}

    def fields(address, items):
        for field in items:
            result["fields"].append({"address": address, "path": field.path, "kind": field.kind})
            for conditional in field.conditionals:
                result["conditionals"].append({
                    "address": address, "field": field.path,
                    "roles": [part.role for part in conditional.parts],
                    "context": [role.role for role in conditional.context],
                })
            fields(address, field.children)

    for entity in index.entities:
        result["entities"].append({"address": entity.address, "kind": entity.kind,
                                   "path": entity.source_range.path.as_posix()})
        fields(entity.address, entity.fields)
    for ref in index.references:
        result["references"].append({
            "source": ref.source_address, "target": ref.target_address,
            "source_field": ref.source_field, "target_field": ref.target_field,
            "kind": ref.kind, "certainty": ref.certainty, "resolution": ref.resolution,
            "roles": [role.role for role in ref.conditional_roles],
            "path": ref.source_range.path.as_posix(), "location": ref.source_range.display(),
        })
    return result


def check_expectations(actual: dict, scenario: dict) -> list[str]:
    failures = []
    for section, default_count in (("expect", 1), ("absent", 0)):
        for category, expectations in scenario.get(section, {}).items():
            for number, expectation in enumerate(expectations, 1):
                wanted = expectation.get("count", default_count)
                count = sum(all(item.get(key) == value for key, value in expectation.items() if key != "count")
                            for item in actual[category])
                if count != wanted:
                    failures.append(f"{section}.{category}[{number}]: expected {wanted} matches, got {count}")
    return failures


def check_reports(index, directory: Path, scenario: dict) -> tuple[dict, list[str]]:
    failures = []
    write_html_report(index, directory / "structural.html")
    write_json_report(index, directory / "structural.json")
    write_interactive_html_report(index, directory / "interactive.html")
    write_interactive_html_report(index, directory / "source.html", include_source_values=True)
    for name in ("structural.html", "structural.json", "interactive.html"):
        text = (directory / name).read_text(encoding="utf-8")
        if any(canary in text for canary in scenario.get("canaries", [])):
            failures.append(f"{name}: privacy canary exposed")
    safe = ReportData((directory / "interactive.html").read_text(encoding="utf-8"))
    opted = ReportData((directory / "source.html").read_text(encoding="utf-8"))
    if safe.scripts != 2 or opted.scripts != 2:
        failures.append("interactive report: unexpected script element count")
    decoded = json.dumps(opted.payload, ensure_ascii=False)
    if any(canary not in decoded for canary in scenario.get("source_canaries", scenario.get("canaries", []))):
        failures.append("opted-in report: expected canary missing")

    def hidden(fields):
        return all(field["value"] is None and hidden(field["children"]) and
                   all(part["value"] is None for c in field.get("conditionals", []) for part in c["parts"])
                   for field in fields)

    if any(node["sourceText"] is not None or not hidden(node["fields"]) for node in safe.payload["nodes"]):
        failures.append("structural interactive report: raw source present")
    addresses = {node["id"]: node["address"] for node in safe.payload["nodes"]}
    observed = Counter((addresses.get(edge["source"], edge["source"]), addresses.get(edge["target"], edge["target"]),
                        edge["sourceField"], edge["targetField"], edge["kind"], edge["certainty"])
                       for edge in safe.payload["edges"])
    expected = Counter()
    for ref in index.references:
        source, target, source_field, target_field = ref.source_address, ref.target_address, ref.source_field, ref.target_field
        if ref.kind == "module-input":
            source, target, source_field, target_field = target, source, target_field, source_field
        expected[source, target, source_field, target_field, ref.kind, ref.certainty] += 1
        if source.split("::")[0] != target.split("::")[0] and "::" in source:
            failures.append("reference crosses independent project namespaces")
    if expected != observed:
        failures.append("interactive report: reference direction or field mapping differs from index")
    return safe.payload, failures


def run_scenario(suite: Path, output: Path, scenario: dict) -> dict:
    result = {"id": scenario["id"], "required": scenario.get("required", True), "status": "blocked", "failures": []}
    if scenario.get("unsupported"):
        return {**result, "status": "unsupported", "reason": scenario["unsupported"]}
    with tempfile.TemporaryDirectory(prefix="iaclineage-acceptance-") as temporary:
        root = Path(temporary) / "fixture" if "files" in scenario else contained(suite, scenario["root"])
        for relative, content in scenario.get("files", {}).items():
            destination = contained(root, relative)
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(content, encoding="utf-8")
        if not root.is_dir() or any(not contained(root, p).exists() for p in scenario.get("prerequisites", [])):
            return {**result, "reason": "fixture root or prerequisite missing"}
        # Source reads are resolved below; compare against the same canonical
        # boundary (Windows short paths and macOS /var aliases may differ).
        boundary = (root if "files" in scenario else suite).resolve()
        before = snapshot(boundary)
        # Observe the existing reader: empty/recovered files may produce no
        # entities, but their module directories still need write protection.
        with patch.object(hcl_source, "_read_utf8_source", wraps=hcl_source._read_utf8_source) as reads:
            index = index_repository(root)
        source_paths = [call.args[0].resolve() for call in reads.call_args_list]
        protect_output(output, [suite, root, *(path.parent for path in source_paths)])
        if any(not path.is_relative_to(boundary) for path in source_paths):
            return {**result, "reason": "followed source is outside the fingerprinted fixture boundary"}
        # Reports are first generated in a disposable directory, so failed privacy
        # checks cannot leave an exposed report in the requested artifacts folder.
        reports = Path(temporary) / "reports"
        reports.mkdir()
        payload, failures = check_reports(index, reports, scenario)
        failures.extend(check_expectations(facts(index, payload), scenario))
        if sorted(d.code for d in index.diagnostics) != sorted(scenario.get("diagnostics", [])):
            failures.append("diagnostic codes differ: " + ", ".join(sorted(d.code for d in index.diagnostics)))
        after = snapshot(boundary)
        if before != after:
            failures.append("fixture inputs changed during validation")
        return {**result, "status": "fail" if failures else "pass", "failures": failures,
                "inputs_sha256": digest(json.dumps(before, sort_keys=True).encode()),
                "entities": len(index.entities), "references": len(index.references)}


def atomic_write(path: Path, text: str) -> None:
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(text)
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def run(suite: Path, output: Path, manifest: Path = MANIFEST, selected: list[str] | None = None) -> dict:
    suite, output = suite.resolve(), output.resolve()
    protect_output(output, [suite])
    raw = manifest.read_bytes()
    scenarios = json.loads(raw)
    if not isinstance(scenarios, list) or not scenarios:
        raise ValueError("manifest must contain scenarios")
    for scenario in scenarios:
        if not scenario.get("unsupported") and not any(scenario.get("expect", {}).values()):
            raise ValueError("scenario must have reviewed expectations")
    ids = [scenario["id"] for scenario in scenarios]
    if len(set(ids)) != len(ids) or any(not re.fullmatch(r"[a-z0-9-]+", item) for item in ids):
        raise ValueError("scenario IDs must be unique lowercase names")
    if selected and set(selected) - set(ids):
        raise ValueError("unknown scenario selection")
    results = []
    for scenario in scenarios:
        if selected and scenario["id"] not in selected:
            continue
        try:
            results.append(run_scenario(suite, output, scenario))
        except OutputPathError:
            raise
        except Exception as error:
            # Exception messages may include source text or absolute local paths.
            results.append({"id": scenario["id"], "required": scenario.get("required", True),
                            "status": "fail", "failures": ["validation error: " + type(error).__name__]})
    def git(*args):
        try:
            process = subprocess.run(["git", *args], cwd=REPO, capture_output=True, text=True, check=True)
            return process.stdout.strip()
        except (OSError, subprocess.CalledProcessError):
            return None
    status = git("status", "--porcelain")
    code = {path.relative_to(REPO).as_posix(): digest(path.read_bytes())
            for path in sorted((REPO / "src/iaclineage").rglob("*"))
            if path.suffix in {".py", ".js", ".css", ".html"}}
    code["scripts/validate_beta.py"] = digest(Path(__file__).read_bytes())
    document = {"revision": git("rev-parse", "HEAD"), "dirty": None if status is None else bool(status),
                "python": platform.python_version(), "platform": platform.system(),
                "versions": {name: version(name) for name in ("iaclineage", "tree-sitter", "tree-sitter-hcl")},
                "code_sha256": digest(json.dumps(code, sort_keys=True).encode()),
                "manifest_sha256": digest(raw), "results": results}
    document["success"] = not any(r["status"] == "fail" or r["required"] and r["status"] != "pass" for r in results)
    output.mkdir(parents=True, exist_ok=True)
    for name in ("results.json", "summary.md"):
        protect_output(output / name, [suite])
    atomic_write(output / "results.json", json.dumps(document, indent=2) + "\n")
    lines = ["# Beta acceptance results", "", f"Revision: {document['revision']}; dirty: {document['dirty']}", "",
             "Required acceptance: " + ("PASS" if document["success"] else "FAIL"), "",
             "Generated reports, including opted-in source, were checked in temporary storage and removed.", "",
             "| Scenario | Status | Details |", "| --- | --- | --- |"]
    for result in results:
        detail = "; ".join(result.get("failures", [])) or result.get("reason", "")
        lines.append(f"| {result['id']} | {result['status']} | {detail} |")
    atomic_write(output / "summary.md", "\n".join(lines) + "\n")
    return document


def main(arguments=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite-root", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--scenario", action="append")
    args = parser.parse_args(arguments)
    try:
        document = run(args.suite_root, args.output_dir, manifest=MANIFEST, selected=args.scenario)
    except (OSError, ValueError) as error:
        print("Validation could not start: " + type(error).__name__)
        return 2
    counts = Counter(result["status"] for result in document["results"])
    print("; ".join(f"{counts[status]} {status}" for status in ("pass", "fail", "blocked", "unsupported")))
    return 0 if document["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
