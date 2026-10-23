"""Command-line entry point for IaCLineage."""

from __future__ import annotations

import argparse
import json
import os
import re
import tempfile
import sys
from collections import Counter, defaultdict
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from time import perf_counter
from typing import Sequence

from . import __version__
from .diagnostics import DiagnosticError
from .hcl_source import index_repository
from .model import RepositoryIndex, TerraformEntity
from .report import write_html_report, write_interactive_html_report, write_json_report
from .scan import discover_terraform_files

# Port of a module call that leads to its outputs rather than an argument.
_OUTPUTS = "<outputs>"

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
    find_parser.add_argument("query", help="full address, Terraform resource address (TYPE.NAME), or declaration name")
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
    report_parser.add_argument(
        "--fail-on", type=_resolution_states, default=(), metavar="STATES",
        help="exit 1 after writing the report if any reference is unresolved or ambiguous "
             "(comma-separated: unresolved, ambiguous)",
    )

    trace_parser = subcommands.add_parser("trace", help="trace what a declaration uses and what uses it")
    trace_parser.add_argument("path", type=Path, help="local repository directory")
    trace_parser.add_argument("query", help="full address, Terraform resource address (TYPE.NAME), or declaration name")
    trace_parser.add_argument(
        "--direction", choices=["up", "down", "both"], default="both",
        help="up follows what the declaration uses, down follows what uses it (default: both)",
    )
    trace_parser.add_argument(
        "--depth", type=_trace_depth, metavar="N", help="stop after N hops (default: all reachable)",
    )
    trace_parser.add_argument("--format", choices=["text", "json"], default="text", help="output format")
    trace_parser.add_argument("--output", type=Path, help="write output to file instead of stdout")

    unused_parser = subcommands.add_parser(
        "unused", help="list variables, locals, and module outputs that nothing references",
    )
    unused_parser.add_argument("path", type=Path, help="local repository directory")

    return parser


def _trace_depth(value: str) -> int:
    try:
        depth = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError("depth must be a whole number") from None
    if depth < 1:
        raise argparse.ArgumentTypeError("depth must be 1 or more")
    return depth


def _resolution_states(value: str) -> tuple[str, ...]:
    states = tuple(dict.fromkeys(state.strip() for state in value.split(",")))
    for state in states:
        if state not in {"unresolved", "ambiguous"}:
            raise argparse.ArgumentTypeError(
                f"invalid choice {state!r} (choose from 'unresolved', 'ambiguous')"
            )
    return states


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
            if args.command in {"find", "trace"}:
                progress.message(f"Searching {len(index.entities)} declarations for {args.query!r}...")
                run = _find if args.command == "find" else _trace
                if args.output and args.format == "text":
                    output = StringIO()
                    with redirect_stdout(output):
                        result = run(args, index, progress)
                    _write_output(output.getvalue(), args.output, args.command)
                else:
                    result = run(args, index, progress)
                progress.message(
                    f"Summary: {progress.counts['matches']} match(es); output format is {args.format}."
                )
            elif args.command == "unused":
                result = _unused(index, progress)
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
                result = _check_fail_on(index, args.fail_on, progress)
        if result == 0:
            outcome = "Complete"
        elif args.command in {"find", "trace"}:
            outcome = "Finished without a unique match"
        else:
            outcome = "Finished with findings"
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


def _unique_match(args: argparse.Namespace, index: RepositoryIndex, progress: _Progress) -> TerraformEntity | None:
    """Return the single declaration matching the query, or report why there is none."""
    query = args.query

    exact_matches = [e for e in index.entities if e.address == query]
    canonical_matches = [
        e for e in index.entities
        if e.kind == "resource" and re.sub(r"resource\.(?=[^.]+\.[^.]+$)", "", e.address) == query
    ]
    segment_matches = [
        e for e in index.entities
        if e.address.rsplit(".", maxsplit=1)[-1] == query
        or e.kind == "provider" and re.sub(
            r"^(?:module\.[^.]+\.)*provider\.", "", e.address.rpartition("::")[2]
        ).split(".")[0] == query
    ]

    if exact_matches:
        matches = exact_matches
    elif canonical_matches:
        matches = canonical_matches
    else:
        matches = segment_matches
    progress.counts["matches"] = len(matches)

    if not matches:
        progress.message(f"No declaration matches {query!r}; try an exact address or its final name.")
        if args.format == "json":
            result = {"error": "no match", "query": query}
            _write_output(json.dumps(result, indent=2), args.output, args.command)
        else:
            print(f"No entity found matching '{query}'.")
        return None

    match_kind = "exact address" if exact_matches else "Terraform address" if canonical_matches else "name"
    progress.message(f"Matched {len(matches)} declaration(s) by {match_kind}.")
    if len(matches) > 1:
        if args.format == "text":
            print(f"Multiple entities match '{query}':")
            for e in matches:
                print(f"  {e.address} ({e.kind}) at {e.source_range.display()}")
            print("Refine your query to an exact address.")
            return None
        else:
            result = {"query": query, "matches": []}
            for e in matches:
                result["matches"].append(_entity_dict(e, index))
            _write_output(json.dumps(result, indent=2), args.output, args.command)
            return None

    return matches[0]


def _find(args: argparse.Namespace, index: RepositoryIndex, progress: _Progress) -> int:
    query = args.query
    entity = _unique_match(args, index, progress)
    if entity is None:
        return 1

    if args.format == "json":
        result = {"query": query, "match": _entity_dict(entity, index)}
        _write_output(json.dumps(result, indent=2), args.output, args.command)
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


def _trace(args: argparse.Namespace, index: RepositoryIndex, progress: _Progress) -> int:
    entity = _unique_match(args, index, progress)
    if entity is None:
        return 1
    directions = {"up": ("upstream",), "down": ("downstream",), "both": ("upstream", "downstream")}[args.direction]
    result = {"query": args.query, "address": entity.address, "depth": args.depth}
    for direction in directions:
        result[direction] = _trace_hops(index, entity.address, direction == "upstream", args.depth)
    progress.message(
        "Traced " + ", ".join(f"{len(result[direction])} {direction} hop(s)" for direction in directions) + "."
    )

    if args.format == "json":
        _write_output(json.dumps(result, indent=2), args.output, args.command)
        return 0
    print(f"{entity.address}  {entity.source_range.display()}")
    for direction in directions:
        print(f"{direction.capitalize()} (A -> B means A uses B):")
        for hop in result[direction]:
            print(f"  {hop['depth']}  {hop['consumer']} -> {hop['dependency']} "
                  f"({hop['kind']}, {hop['resolution']}, {hop['location']})")
        if not result[direction]:
            print("  none")
    return 0


def _trace_hops(index: RepositoryIndex, start: str, upstream: bool, max_depth: int | None) -> list[dict]:
    """Walk references breadth-first from ``start``, listing each hop once."""
    modules = {entity.address for entity in index.entities if entity.kind == "module"}
    # Each edge is (consumer, dependency, kind, resolution, location, call, port).
    # Edges point from consumer to dependency, as in the interactive report, so
    # module-input bindings (stored call-to-child) are flipped. `call` and `port`
    # name the module call and argument an edge passes through, if any.
    edges = []
    whole_module_reads = set()
    for reference in index.references:
        if reference.source_address == reference.target_address:
            continue  # a variable's own validation block
        if reference.kind == "module-input":
            consumer, dependency = reference.target_address, reference.source_address
        else:
            consumer, dependency = reference.source_address, reference.target_address
            if dependency in modules:
                whole_module_reads.add(dependency)
        call = reference.source_address if reference.source_address in modules else None
        port = re.split(r"[.\[]", reference.source_field, maxsplit=1)[0] if call else None
        edges.append((consumer, dependency, reference.kind, reference.resolution,
                      reference.source_range.display(), call, port))
    # Reading a whole module object (module.app, module.app[*].id, depends_on)
    # reads its outputs, so link those calls to their outputs.
    for entity in index.entities:
        call, _, _ = entity.address.rpartition(".output.")
        if entity.kind == "output" and call in whole_module_reads:
            edges.append((call, entity.address, "module-output", "resolved",
                          entity.source_range.display(), call, _OUTPUTS))

    adjacent = defaultdict(list)
    for edge in edges:
        consumer, dependency = edge[0], edge[1]
        adjacent[consumer if upstream else dependency].append(edge)

    hops = []
    listed = set()
    # A module call reached through one argument continues only through that
    # argument, and one reached from its outputs continues only to readers of
    # the whole module, so one input does not pull in every other input.
    seen = {(start, None)}
    frontier = [(start, None)]
    depth = 0
    while frontier and (max_depth is None or depth < max_depth):
        depth += 1
        next_frontier = []
        for address, via in frontier:
            for edge in adjacent[address]:
                consumer, dependency, kind, resolution, location, call, port = edge
                if edge in listed or (via is not None and call == address and port != via):
                    continue
                listed.add(edge)
                hops.append({
                    "depth": depth,
                    "consumer": consumer,
                    "dependency": dependency,
                    "kind": kind,
                    "resolution": resolution,
                    "location": location,
                })
                there = dependency if upstream else consumer
                state = (there, port if call == there else None)
                if state not in seen:
                    seen.add(state)
                    next_frontier.append(state)
        frontier = next_frontier
    return hops

def _check_fail_on(index: RepositoryIndex, states: tuple[str, ...], progress: _Progress) -> int:
    counts = Counter(reference.resolution for reference in index.references)
    failures = [f"{counts[state]} {state} reference(s)" for state in states if counts[state]]
    if not failures:
        return 0
    progress.message(f"warning: {', '.join(failures)}; failing because of --fail-on.")
    return 1


def _unused(index: RepositoryIndex, progress: _Progress) -> int:
    # Module-input bindings set a child variable but do not read it, and a
    # variable's own validation block does not count as a use.
    reads = [
        reference for reference in index.references
        if reference.kind == "reference" and reference.source_address != reference.target_address
    ]
    used = {reference.target_address for reference in reads}
    # A whole module object, such as `module.app` or `module.app[*].id`, reads
    # every output. depends_on and moved blocks only order or rename it.
    kinds = {entity.address: entity.kind for entity in index.entities}
    whole_modules = tuple(
        f"{reference.target_address}.output." for reference in reads
        if kinds.get(reference.target_address) == "module"
        and kinds.get(reference.source_address) != "moved"
        and not re.fullmatch(r"depends_on(\[\d+\])?", reference.source_field)
    )
    candidates = [
        entity for entity in index.entities
        if entity.kind in {"variable", "local", "output"}
        and ".terraform" not in entity.source_range.path.parts
        # Root outputs are read from outside the configuration.
        and not (entity.kind == "output" and entity.address.rpartition("::")[2].startswith("output."))
    ]
    # A module called more than once has one declaration per call; it is unused
    # only if no call uses it.
    used_declarations = {
        (entity.source_range.path, entity.source_range.start_byte) for entity in candidates
        if entity.address in used or entity.address.startswith(whole_modules)
    }
    unused = [
        entity for entity in candidates
        if (entity.source_range.path, entity.source_range.start_byte) not in used_declarations
    ]
    for entity in unused:
        print(f"{entity.address} ({entity.kind}) at {entity.source_range.display()}")
    if not unused:
        print("No unused variables, locals, or module outputs found.")
    progress.message(f"Summary: {len(unused)} unused declaration(s); downloaded modules and root outputs are skipped.")
    return 1 if unused else 0

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


def _write_output(content: str, output_path: Path | None, command: str) -> None:
    if output_path:
        print(f"[{command}] Writing {command} result to {output_path.resolve()}...", file=sys.stderr, flush=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=output_path.parent, delete=False) as stream:
                temporary = Path(stream.name)
                stream.write(content)
            os.replace(temporary, output_path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        print(f"[{command}] Wrote {command} result to {output_path.resolve()}.", file=sys.stderr, flush=True)
    else:
        print(content)
