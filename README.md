# IaCLineage

# IaCLineage 0.1.0b1 (public beta)
The project, Python package, and CLI name is `iaclineage`.

For installation and beta smoke checks, use the [tester guide](docs/tester-guide.md).
For Linux/macOS installation and native CI checks, see [platform support](docs/platform-support.md).

IaCLineage is a local-only tool for understanding one unfamiliar
Terraform source repository at a time. It uses parser-backed static analysis to
find Terraform declarations and their direct static references with evidence.

It never executes Terraform, OpenTofu, providers, repository-controlled shell
commands, or network calls. It does not ingest plans or state, contact cloud
services, upload data, or collect telemetry.

## Install and first report

Python 3.12 or newer is required; Python 3.12–3.14 is the CI target range.
After this beta is published to PyPI, install it in an isolated tool environment:

```sh
uv tool install --python 3.12 iaclineage==0.1.0b1
iaclineage --version
```

Alternatively, use `python -m venv .venv`, activate it, then run
`python -m pip install iaclineage==0.1.0b1`. See the
[tester guide](docs/tester-guide.md) for TestPyPI and platform-specific commands.
An exact version pin selects this pre-release; no `--pre` flag is needed.

Before scanning:

1. Make the Terraform `.tf` source and referenced local module folders available.
2. For remote or registry modules, run `terraform get` yourself in each Terraform
   root first. This requires Terraform and any module-download authentication;
   IaCLineage itself requires neither Terraform nor cloud credentials.
3. Keep the default `.terraform/modules` cache. Custom `TF_DATA_DIR` is unsupported.
   Missing modules produce `IAC103`; local source can still be inspected.
4. Choose an output path outside the scanned repository and followed modules.
5. Keep source values excluded unless you explicitly need a sensitive local report.

```sh
iaclineage scan /path/to/terraform-repository
iaclineage report /path/to/terraform-repository --format interactive-html --output /path/to/reports/lineage.html
```

Use real paths on your machine, then open `lineage.html` in Chrome or Edge.
Windows paths work too; quote paths containing spaces. No server, Node.js,
Terraform plan/apply, state file, or cloud access is needed.

## License and support

Fully open source under [Apache License 2.0](LICENSE), including commercial use
subject to its terms. Copyright 2026 Mayur Mahajan; see [NOTICE](NOTICE).
This is a first public beta; static relationships are not Terraform runtime results.
Security fixes target the latest released version only. Report vulnerabilities
through [GitHub Private Vulnerability Reporting](https://github.com/mayurmhjn/iaclineage/security/advisories/new),
never public Issues. Use [Issues](https://github.com/mayurmhjn/iaclineage/issues)
for non-security bugs, with synthetic reproductions and the installed version.
See [CONTRIBUTING.md](CONTRIBUTING.md) and [release steps](docs/releasing.md).

## What works today

- Recursively discover `.tf` files, excluding `.git` and `.terraform`.
- Index top-level `terraform`, `provider`, `resource`, `data`, `module`,
  `output`, `variable`, `locals`, `moved`, `import`, `check`, and
  `provider_meta` blocks.
- Model the repository as a Terraform *module graph*: the scanned directory is
  the root module, and each `module "name"` call with a local `source`
  (`./` or `../`, including paths outside the scanned root) is followed and
  indexed under the `module.name.` address prefix.
- Read already-downloaded remote and registry modules through each root
  project's `.terraform/modules/modules.json`. Nested calls keep their module
  namespace. IaCLineage never downloads modules or invokes Terraform.
- Index a container of independent projects. If the scanned directory has no
  `.tf` files of its own, each discovered root module beneath it is indexed
  under its own `<relative-path>::` namespace, and references never cross from
  one project into another.
- Trace inputs and outputs across local and downloaded modules:
  - `module.name.OUTPUT` references resolve to the child module's
    `output "OUTPUT"` (a missing output is honestly `unresolved`).
  - each call argument is bound to the child's `variable` of the same name.
- Find a declaration by exact address or final name.
- Show a repository-relative, exact source range and a structural snippet.
- Show direct static references, both outgoing and incoming (dependents), and
  whether a target was found in the scanned module graph.
- Write a self-contained static HTML, JSON, or interactive HTML report using a
  same-directory temporary file followed by `os.replace()`.
- Show a visible diagnostic when HCL parse recovery was used (`IAC101`) or when
  a local module `source` directory cannot be found (`IAC102`).
- Report `IAC103` when a remote module's installed manifest entry is absent,
  invalid, mismatched, or points outside the supported local cache.

Structural snippets deliberately exclude attribute values, strings, comments,
and heredocs. Standard HTML and JSON reports remain structural-only. Interactive
reports include raw source only when the user explicitly supplies
`--include-source-values`; that trusted-user option can expose strings,
comments, heredocs, and secrets in the selected declaration's details.

Interactive reports show nested blocks as compact expandable groups. Provisioners
retain source order and display known built-in types (`file`, `local-exec`, and
`remote-exec`); arbitrary quoted labels remain omitted. Select a block or its
reference-count link to trace its fields together, or expand it to trace one field.
Connection groups distinguish resource settings from provisioner settings. Field
search also matches provisioner types. Expand a **Context** entry in Fields or
Reference evidence to explain `self.<attribute>`, `path.module`, `path.root`, or
`path.cwd` and see its source location. Context entries do not add dependency edges
or evaluate attribute/directory values. Scripts and file expressions are never run
or loaded. These improvements require reports regenerated from updated source;
they are available in this public beta release.
The current public beta also provides a keyboard-accessible **Copy location**
action for field, Context, and reference evidence. It copies only the
repository-relative path and line range, works in local `file://` reports without
network access, and never copies source values.

## Development setup (contributors only)

- The compatibility workflow validates Windows, Linux, and macOS. See the
  platform guide for the current verification scope.
- Python 3.12 or newer.
- [uv](https://docs.astral.sh/uv/).

From this directory:

```powershell
uv sync --group dev
uv run --group dev pytest --cov=iaclineage --cov-branch --cov-fail-under=85
```

`uv.lock` is committed project state and should be updated whenever an approved
dependency changes. `.venv/` and `*.egg-info/` are generated local artifacts
and are ignored by Git.

CI builds one candidate wheel for all Python 3.12–3.14 jobs on Windows, Ubuntu,
and macOS. The wheel, SHA-256 checksum, and Python test/coverage results are
retained for 14 days, including results from failed tests. See
[CI failure diagnosis](docs/platform-support.md#diagnose-a-ci-failure) for the
artifact names and local reproduction command.

After all platform tests pass, CI packages that same wheel with tester guides,
synthetic examples, provenance, and checksums into a `beta-kit-<commit>-attempt<N>`
artifact. See [tester kit packaging](docs/platform-support.md#package-a-tester-kit)
for download and local assembly instructions.

## Commands

Use `iaclineage --version` to identify the installed candidate.

### Remote modules already downloaded locally

Run `terraform get` yourself in the Terraform root to download its modules,
then run the usual IaCLineage report command. Full `terraform init` also
downloads modules, but additionally initializes other Terraform components;
it is not required just for module-source inspection. IaCLineage does not run
either command. Git/SSH authentication remains Terraform's responsibility.

```powershell
terraform -chdir="D:\path\to\terraform-repository" get
iaclineage report D:\path\to\terraform-repository `
  --output .\lineage-remote.html --format interactive-html
```

The scanner reads only manifest-selected cached modules, not every directory
under `.terraform`. It supports the default root-relative cache paths under
`.terraform/modules`, including Git subdirectories and public/private registry
modules when the installed source matches the call. Public registry addresses
with or without `registry.terraform.io/` match. Other source-address spellings
must match exactly; unrecognized normalization stays unresolved.

This inspects the **installed source snapshot**. It does not evaluate version
constraints, verify remote revisions or cache freshness, or select/download a
version. Refresh module downloads after changing sources, versions, or refs.
Custom `TF_DATA_DIR` locations and absolute manifest paths are not supported.
Terraform's manifest is an internal format; invalid formats produce `IAC103`
rather than guessed relationships. Cached source values follow the same
structural-default and explicit `--include-source-values` rules as local files.

See [Terraform get](https://developer.hashicorp.com/terraform/cli/commands/get)
and [Terraform init](https://developer.hashicorp.com/terraform/cli/commands/init).

### Scan, find, and report

```powershell
# List included Terraform files.
iaclineage scan D:\path\to\terraform-repository

# Find a declaration by address or final name (text or JSON output).
iaclineage find D:\path\to\terraform-repository api
iaclineage find D:\path\to\terraform-repository api --format json

# Create a source-safe static report (HTML or JSON).
iaclineage report D:\path\to\terraform-repository `
  --output .\iaclineage-report.html
iaclineage report D:\path\to\terraform-repository `
  --output .\iaclineage-report.json --format json

# Create an offline interactive report with a searchable table and focused
# upstream/downstream lineage view.
iaclineage report D:\path\to\terraform-repository `
  --output .\iaclineage-report.html --format interactive-html

# Include raw Terraform strings, comments, heredocs, and secrets only for a
# trusted local report. The generated file displays a sensitive-content warning.
iaclineage report D:\path\to\terraform-repository `
  --output .\iaclineage-sensitive.html --format interactive-html `
  --include-source-values

# Point at a folder of several projects to aggregate them (per-project namespaces).
iaclineage report D:\path\to\projects-folder --output .\report.html
```

CLI operations print progress to stderr. Scan reports a file count (including
zero); report confirms the destination and declaration/reference/diagnostic counts.
Find confirms file output, and `--output` supports both text and JSON results.
Scan paths and find JSON on stdout remain suitable for piping.
Progress and warnings use terminal colors automatically when stderr is interactive;
use `--color always` or `--color never` to override that choice. Colors are never
added to scan paths, find results, or JSON output.

An address currently uses the canonical form `resource.TYPE.NAME`, `module.NAME`,
or `output.NAME`. Child-module declarations are prefixed with the call, for
example `module.app.output.id`. When a container of projects is scanned, every
address is additionally prefixed with its project namespace, for example
`project_a::resource.aws_instance.web`. Source ranges use 1-based line and
column positions; their end position is exclusive.

## Developer checks

The Python suite covers discovery, exclusions, safe
diagnostics, parse recovery, source ranges, direct-reference resolution, local
module input/output tracing, installed remote-module caches, multi-project
isolation, safe HTML rendering, and CLI behavior. Run:

```powershell
uv run --group dev pytest
```

The interactive lineage traversal also has focused tests using Node's standard
test runner (Node is only needed to run these developer tests, not the report):

```powershell
node --test tests/test_lineage_ui.cjs
```

On Windows, run browser regressions with installed Microsoft Edge and Node.js/npm
(developer tools only). Linux/macOS use Playwright Chromium; setup is in the
[platform guide](docs/platform-support.md):

```powershell
npm ci --ignore-scripts
npm run test:browser
```

Playwright is pinned in `package-lock.json`. This command generates temporary
structural reports, runs the browser scripts, and cleans up its own reports.
On Windows no browser download is required. No `NODE_PATH` override is needed. See
the [tester guide](docs/tester-guide.md) for beta smoke checks and setup.

Optional maintainer check: run the offline acceptance matrix against a separately supplied synthetic suite. That external suite is not bundled; it is not required to install or use the CLI, or run the normal Python and browser tests:

```powershell
uv run python scripts/validate_beta.py `
  --suite-root D:\path\to\optional-synthetic-suite `
  --output-dir .\artifacts\beta-validation
```

Use `--scenario expressions` (repeatable) to select cases. The runner uses the
standard library and existing analyzer/report APIs; it never runs fixture scripts,
Terraform, or downloads. Required failures or missing inputs return nonzero.
Optional missing downloads are reported as blocked. Only structural results and
provenance persist in `results.json` and `summary.md`; generated reports are
checked in temporary storage and removed. See the
[reviewed expectations](tests/acceptance/README.md) for scope and manifest rules.

## Interactive report

`report --format interactive-html` writes one self-contained HTML file. Open
it locally in a browser to search declarations, filter projects and Terraform
kinds, and select a declaration from the paginated table. The inspector contains
Overview, Fields, References, Diagnostics, and Source tabs. Fields include
nested blocks, object members, and tuple items; select a field to narrow its
upstream/downstream trace. **Filter fields** searches field names and nested paths
within the selected declaration. Matching descendants retain expanded parent
context. Clear restores your previous expansion state; typing does not change the
active trace. Switching declarations clears the field search. The evidence panel identifies both endpoints, the
consuming field, referenced attribute, source range, and resolution state.

Use Upstream, Downstream, or Both and a hop depth, or All reachable to follow
matching field paths across local and downloaded module inputs and outputs.
Module bindings
appear explicitly. A diagram starts with at most 80 endpoints and offers more
when needed. Grouped cards retain individual field connections and branch
controls. Numbered connections synchronize selection with the evidence table;
the View selector offers graph, table, or both. Center selected returns to the
chosen declaration. Narrow diagrams scroll; the optional shrink action is
bounded to 75–100%. Missing and ambiguous targets stay visible; an ambiguous
declaration address is never silently resolved to one candidate.

The header's **Hide .terraform content** filter applies throughout the interactive
report: declaration lists, fields, references, diagnostics, counts, and lineage.
It hides installed-cache declarations and their connections until unchecked;
it does not remove data from the generated file. Other declaration filters remain
available in the sidebar. **Hide declarations / Show declarations** reclaims that
sidebar space. Drag the right edge of Declarations or the left edge of Reference
evidence to resize their widths. When evidence sits below the workspace, drag its
bottom edge to resize its height. Focus a divider and use arrow keys, or Home/End,
to resize with the keyboard; double-click to reset. Sizes persist while navigating
the open report and reset on reload. **Maximize lineage** expands the trace workspace; **Restore layout**
or Escape returns to the inspector. Source filtering and sidebar visibility persist
while navigating within the open report. Conditional evidence starts collapsed and can be expanded per field.
Desktop layouts are checked from 1024×768 through 2560×1440; mobile is outside the
target scope. Regenerate existing reports to receive updated controls.

The declaration list appears in the sidebar while inspecting an object; browsing
uses the main table. More trace controls groups expansion, centering, shrinking,
and reset. Back to trace returns keyboard focus to the selected connection.
Supporting evidence is expandable; unresolved and expression-limit explanations
start open. The optional `tests/report_ui_smoke.cjs` browser check verifies these
interactions with Playwright and an installed browser; its header gives
the report-generation and test commands.

Expand all details in Focused lineage opens the Details sections of every
currently displayed graph card; the same button then collapses them. Individual
Details toggles remain available, and their state is retained when the graph
rerenders. This does not expand additional branches or increase trace depth.
Trace whole declaration appears after selecting a field. It removes that field
filter while preserving direction, depth, and branch controls. To try it, generate
an interactive report from `tests/fixtures/ui_details`, select
`resource.example_service.demo`, then select `name`: its upstream connection is
`variable.first`. Trace whole declaration restores the `alias` connection to
`variable.second` too. `tests/report_details_smoke.cjs` verifies this workflow and
bulk/individual Details toggles in Edge; setup and invocation are in its header.
`tests/report_desktop_smoke.cjs` uses the same report fixture and verifies global
cache filtering, sidebar visibility, maximization, Escape focus, and desktop overflow.

These are source-reference paths, not evaluated value flows. Traces stop at
expression/instance-selection boundaries and at referenced attributes with no
source-defined continuation. Resolving a resource declaration does not verify
its provider-computed attributes. An absent static reference is not proof of
runtime independence.

Conditional expressions show **Condition**, **When true**, and **When false**
in the Fields inspector, including nested conditionals and parts with no static
references. Comparison and unary operands are indexed individually. References
and connection evidence distinguish condition inputs from possible result inputs;
repeated uses of one variable retain their separate roles and source locations.
**Selected branch: Unknown** means the expression was not evaluated. Resolving a
declaration or including source values does not select a branch, and traces still
stop at expression boundaries. Conditional text and branch literals follow the
same source-value opt-in as other fields. Structural JSON includes conditional
roles and ranges without raw values.

Raw Terraform values are excluded by default. Use `--include-source-values`
only for a trusted local output: strings, comments, heredocs (multi-line
strings), and secrets may then appear in the selected declaration's details.
Literal map keys use positional labels in structural mode. The complete source
and field expressions are included only in opted-in reports. Keep generated
reports outside scanned repositories.

The browser assets live in `src/iaclineage/ui/`; Python's standard
`importlib.resources` embeds them into the report. Native HTML controls, DOM,
and SVG provide the interface without a framework, CDN, server, or extra
runtime dependency.

## Intentional beta limits

- Only `.tf` source is scanned. `.terraform` is excluded from general discovery;
  downloaded modules are read only through matching installed-manifest entries.
- Remote modules are never fetched. Missing/unusable cached source remains
  unresolved. Installed snapshots do not prove cache freshness or satisfaction
  of version constraints; custom `TF_DATA_DIR` is not supported.
- Direct static references only. Dynamic evaluation, Terraform execution,
  provider semantics, and remote state resolution are out of scope.
- No plan/state JSON, database/cache, authentication, collaboration, or service.
- Hardened defenses for unusual symlink/junction races are not yet included.

See [the changelog](CHANGELOG.md), [platform guide](docs/platform-support.md), and
[security policy](SECURITY.md) for release information.
