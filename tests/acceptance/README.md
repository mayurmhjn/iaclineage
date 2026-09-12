# Offline acceptance expectations

`scenarios.json` is reviewed against the synthetic fixture source and the current
Beta contract. It is not an analyzer-generated snapshot. Run it from this
repository using `scripts/validate_beta.py`; the sibling fixture checkout is
read-only and its scripts are never executed.

## Scope and source review

| Scenarios | Fixture evidence |
| --- | --- |
| basic-refs | `valid/basic-refs`: cross-file references, input/output member selection, orphan local/variable (B-01 through B-05) |
| local-deep, local-shared, local-outside, independent | `valid/local-*` and `valid/independent`: namespace-specific calls and bindings, sibling sources, independent projects (L-01 through L-06) |
| expressions | `valid/expressions`: direct, indexed, function, and literal-branch conditional expressions (S-01, S-02) |
| unresolved, missing-module, nonexistent-module-io, malformed, duplicate, cycles | Matching `invalid/` scenarios; missing declarations remain unresolved, duplicate addresses remain separate, parse recovery is diagnostic |
| privacy | `privacy/*.tf`: fabricated literal/key/index/heredoc/comment and HTML injection canaries (P-series) |
| conditional-roles, duplicate-ambiguity | Small supplemental inputs materialized in temporary storage; fill gaps in the existing suite without changing it |
| cache-* | Small synthetic installed layouts with current manifest keys; cover absent/malformed manifests, absent directories, source mismatch, invalid paths, and nested bindings |
| remote-git-installed | Optional check against an already-installed local label module; no download or freshness claim |
| conditional-evaluation | Explicitly unsupported; not counted as a passing scenario |

The external cache examples use placeholder keys such as `root;m` and do not
contain runnable root modules. The supplemental cache inputs use exact current
keys such as `m`, so a source-mismatch case actually reaches source comparison
rather than merely failing to find the manifest entry.

## Manifest rules

- `root` is relative to the supplied suite. Alternatively, `files` maps relative
  paths to reviewed synthetic text and is materialized in a temporary fixture.
- `expect` matches structural dictionaries in `entities`, `references`, `fields`,
  `conditionals`, or interactive `edges`. Each predicate must match exactly once
  unless `count` is specified. `absent` uses the same matching with default zero.
- `diagnostics` is the complete expected diagnostic-code list; the default is
  empty. Repeated codes count separately.
- `canaries` must be absent from actual structural HTML, JSON, and interactive
  reports. `source_canaries` must occur in decoded opted-in output, defaulting to
  `canaries`. The fixture's comment outside declarations is tested for structural
  absence only: declaration-level source does not include it.
- Index references use snake_case field names; interactive edges retain the
  report's camelCase names. Every scenario also checks binding direction against
  the index, project isolation, structural source exclusion, script escaping,
  and unchanged fixture inputs.
- Required cases must pass. Missing optional prerequisites are `blocked`, and
  explicitly unsupported cases remain visible. Any actual failure is nonzero,
  even in an optional case. Invalid invocation returns exit code 2.

All followed inputs must remain within the suite boundary (or the temporary
synthetic fixture). A sibling module outside an individual scenario is supported
when it remains inside the suite. The source-reader observation also protects
empty module files, which may produce no declarations. No application code is
modified to add validation hooks.

Only `results.json` and `summary.md` persist. Reports are generated outside the
scanned fixture in temporary storage and removed after checks, including opted-in
source. Input, manifest, and analyzer/runner code digests identify the tested
content; local source values and absolute fixture paths are not logged.
