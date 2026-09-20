# Interactive report


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

