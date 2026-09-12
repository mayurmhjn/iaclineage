"""Command-line entry point for IaCLineage."""

from __future__ import annotations

import argparse
import json
import os
import tempfile
import sys
from collections import Counter
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from time import perf_counter
from typing import Sequence

from . import __version__
from .diagnostics import DiagnosticError
from .hcl_source import index_repository
from .model import RepositoryIndex
from .report import write_html_report, write_interactive_html_report, write_json_report
from .scan import discover_terraform_files

_ANSI = {
    "cyan": "\x1b[36m",
    "green": "\x1b[32m",
    "yellow": "\x1b[33m",
    "red": "\x1b[31m",
    "dim": "\x1b[2m",
    "reset": "\x1b[0m",
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="iaclineage")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument(
        "--color", choices=["auto", "always", "never"], default="auto",
        help="color progress and diagnostics: auto for terminals (default), always, or never",
    )
    subcommands = parser.add_subparsers(dest="command", required=True)

    # scan
    scan_parser = subcommands.add_parser("scan", help="list Terraform source files below a repository path")
    scan_parser.add_argument("path", type=Path, help="local repository directory")

    # find
    find_parser = subcommands.add_parser("find", help="find a resource, module, or output by name or address")
    find_parser.add_argument("path", type=Path, help="local repository directory")
    find_parser.add_argument("query", help="exact address or declaration name (final segment)")
    find_parser.add_argument("--format", choices=["text", "json"], default="text", help="output format")
    find_parser.add_argument("--output", type=Path, help="write output to file instead of stdout")

    report_parser = subcommands.add_parser("report", help="write a report for one repository")
    report_parser.add_argument("path", type=Path, help="local repository directory")
    report_parser.add_argument("--output", required=True, type=Path, help="output file path")
    report_parser.add_argument(
        "--format", choices=["html", "json", "interactive-html"], default="html",
        help="output format (html, json, or interactive-html)"
    )
    report_parser.add_argument(
        "--include-source-values",
        action="store_true",
        help="include raw Terraform source in interactive report details (trusted users only)",
    )

    return parser


class _Progress:
    """Plain, flushed status lines; throttle work events without guessing totals."""

    def __init__(self, command: str, color: str = "auto"):
        self.command = command
        self.started = self.last_update = perf_counter()
        self.counts: Counter[str] = Counter()
        self.color = color == "always" or (color == "auto" and sys.stderr.isatty() and "NO_COLOR" not in os.environ)

    def message(self, text: str) -> None:
        prefix = f"[{self.command}]"
        if text.startswith(("Failed:", "Unexpected", "Cancelled")):
            marker, tone = "ERR", "red"
        elif text.startswith("warning:"):
            marker, tone = "WARN", "yellow"
        elif text.startswith(("Complete", "Found", "Wrote", "Summary:")):
            marker, tone = "OK", "green"
        else:
            marker, tone = "->", "cyan"
        if self.color:
            prefix = f"{_ANSI[tone]}{prefix}{_ANSI['reset']}"
            marker = f"{_ANSI[tone]}{marker}{_ANSI['reset']}"
            text = f"{_ANSI['dim']}{text}{_ANSI['reset']}"
        print(f"{prefix} {marker} {text}", file=sys.stderr, flush=True)

    def __call__(self, event: str, path: Path | None) -> None:
        self.counts[event] += 1
        if event == "resolve":
            self.message(f"Resolving static references after {self.counts['read']} source reads...")
            return
        now = perf_counter()
        first = event in {"directory", "read"} and self.counts[event] == 1
        if not first and now - self.last_update < 1:
            return
        self.last_update = now
        if event == "read":
            detail = f"Reading source #{self.counts['read']}: {path.as_posix()}"
        else:
            detail = f"Discovering: {self.counts['directory']} directories checked"
        self.message(f"{detail} ({now - self.started:.1f}s elapsed)")


def main(arguments: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(arguments)
    progress = _Progress(args.command, args.color)
    try:
        result = 0
        if args.command == "scan":
            progress.message("Discovering Terraform files; excluding .git and .terraform directories...")
            files = discover_terraform_files(args.path, progress=progress)
            for path in files:
                print(path.as_posix())
            progress.message(f"Found {len(files)} Terraform (.tf) files.")
            progress.message(
                f"Summary: {len(files)} files included; {progress.counts['directory']} directories checked."
            )
        else:
            if args.command == "report" and args.include_source_values and args.format != "interactive-html":
                raise DiagnosticError(
                    "IAC009",
                    "--include-source-values requires --format interactive-html",
                )
            progress.message("Indexing Terraform source; following local and already-installed modules...")
            index = index_repository(args.path, progress=progress)
            _summarize_index(index, progress)
            if args.command == "find":
                progress.message(f"Searching {len(index.entities)} declarations for {args.query!r}...")
                if args.output and args.format == "text":
                    output = StringIO()
                    with redirect_stdout(output):
                        result = _find(args, index, progress)
                    _write_output(output.getvalue(), args.output)
                else:
                    result = _find(args, index, progress)
                progress.message(
                    f"Summary: {progress.counts['matches']} match(es); output format is {args.format}."
                )
            else:
                progress.message(f"Writing {args.format} report to {args.output.resolve()}...")
                if args.include_source_values:
                    progress.message("Source values enabled: the report may contain secrets from the source.")
                if args.format == "html":
                    write_html_report(index, args.output)
                elif args.format == "json":
                    write_json_report(index, args.output)
                else:
                    write_interactive_html_report(
                        index, args.output, include_source_values=args.include_source_values
                    )
                print(
                    f"Wrote {args.format} report to {args.output.resolve()} "
                    f"({len(index.entities)} declarations, {len(index.references)} references, "
                    f"{len(index.diagnostics)} diagnostics)."
                )
                progress.message(
                    f"Summary: {len(index.entities)} declarations, {len(index.references)} references, "
                    f"{len(index.diagnostics)} diagnostics; {args.output.stat().st_size:,} bytes written."
                )
        outcome = "Complete" if result == 0 else "Finished without a unique match"
        progress.message(f"{outcome} in {perf_counter() - progress.started:.2f}s.")
        return result
    except DiagnosticError as error:
        progress.message(f"Failed: {error}")
        return 2
    except OSError as error:
        progress.message(f"Failed: {type(error).__name__}. Check source readability and the output directory's existence and permissions.")
        return 1
    except Exception as error:
        # Parser/library exceptions may contain source values; expose only type.
        progress.message(f"Unexpected {type(error).__name__}; command could not finish. Report this with the command and a synthetic example.")
        return 1
    except KeyboardInterrupt:
        progress.message(f"Cancelled after {perf_counter() - progress.started:.2f}s.")
        return 130


def _summarize_index(index: RepositoryIndex, progress: _Progress) -> None:
    resolutions = Counter(reference.resolution for reference in index.references)
    detail = ", ".join(f"{resolutions[state]} {state}" for state in ("resolved", "unresolved", "ambiguous"))
    progress.message(f"Indexed {len(index.entities)} declarations, {len(index.references)} references ({detail}), {len(index.diagnostics)} diagnostics.")
    if not progress.counts["read"]:
        progress.message("warning: no Terraform (.tf) files were found in the included source directories.")
    for diagnostic in index.diagnostics[:5]:
        progress.message(f"warning: {diagnostic.code} at {diagnostic.source_range.display()}: {diagnostic.message}")
    if len(index.diagnostics) > 5:
        progress.message(f"{len(index.diagnostics) - 5} more diagnostics; generate a report to inspect all locations.")


def _find(args: argparse.Namespace, index: RepositoryIndex, progress: _Progress) -> int:
    query = args.query

    exact_matches = [e for e in index.entities if e.address == query]
    segment_matches = [e for e in index.entities if e.address.rsplit(".", maxsplit=1)[-1] == query]

    if exact_matches:
        matches = exact_matches
    else:
        matches = segment_matches
    progress.counts["matches"] = len(matches)

    if not matches:
        progress.message(f"No declaration matches {query!r}; try an exact address or its final name.")
        if args.format == "json":
            result = {"error": "no match", "query": query}
            _write_output(json.dumps(result, indent=2), args.output)
        else:
            print(f"No entity found matching '{query}'.")
        return 1

    progress.message(f"Matched {len(matches)} declaration(s) by {'exact address' if exact_matches else 'name'}.")
    if len(matches) > 1 and not exact_matches:
        if args.format == "text":
            print(f"Multiple entities match '{query}':")
            for e in matches:
                print(f"  {e.address} ({e.kind}) at {e.source_range.display()}")
            print("Refine your query to an exact address.")
            return 1
        else:
            result = {"query": query, "matches": []}
            for e in matches:
                result["matches"].append(_entity_dict(e, index))
            _write_output(json.dumps(result, indent=2), args.output)
            return 0

    entity = matches[0]

    if args.format == "json":
        result = {"query": query, "match": _entity_dict(entity, index)}
        _write_output(json.dumps(result, indent=2), args.output)
        return 0
    else:
        print(f"{entity.address}  {entity.source_range.display()}")
        print(f"  {entity.snippet()}")

        outgoing = [r for r in index.references if r.source_address == entity.address]
        if outgoing:
            print("  Outgoing references:")
            for r in outgoing:
                print(f"    -> {r.target_address} ({r.resolution}, {r.source_range.display()})")
        else:
            print("  Outgoing references: none")

        incoming = [r for r in index.references if r.target_address == entity.address]
        if incoming:
            print("  Incoming references (dependents):")
            for r in incoming:
                print(f"    <- {r.source_address} ({r.resolution}, {r.source_range.display()})")
        else:
            print("  Incoming references: none")
        return 0


def _entity_dict(entity, index):
    return {
        "address": entity.address,
        "kind": entity.kind,
        "location": entity.source_range.display(),
        "snippet": entity.snippet(),
        "outgoing": [
            {
                "target": r.target_address,
                "resolution": r.resolution,
                "source_location": r.source_range.display(),
            }
            for r in index.references if r.source_address == entity.address
        ],
        "incoming": [
            {
                "source": r.source_address,
                "resolution": r.resolution,
                "source_location": r.source_range.display(),
            }
            for r in index.references if r.target_address == entity.address
        ],
    }


def _write_output(content: str, output_path: Path | None) -> None:
    if output_path:
        print(f"[find] Writing find result to {output_path.resolve()}...", file=sys.stderr, flush=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=output_path.parent, delete=False) as stream:
                temporary = Path(stream.name)
                stream.write(content)
            os.replace(temporary, output_path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        print(f"[find] Wrote find result to {output_path.resolve()}.", file=sys.stderr, flush=True)
    else:
        print(content)
