---
name: iaclineage
description: Use when someone asks how Terraform declarations connect, such as what a resource, module, or output depends on, what a variable or local feeds, what changing an input would touch, or which variables, locals, or module outputs are unused. Also use when they mention iaclineage, Terraform lineage, or dependency tracing in .tf source.
license: Apache-2.0
---

# iaclineage

iaclineage reads Terraform `.tf` source on the local machine and indexes every
declaration and static reference across files and modules. It never runs
Terraform and needs no credentials or network access. Use it to answer
connection questions with evidence (addresses and `file:line:col` ranges)
instead of reading `.tf` files by hand.

## Before the first command

1. Run `iaclineage trace --help`. If `iaclineage` is missing, ask the user
   before installing it (`pip install iaclineage`, Python 3.12+). If only
   `trace` is missing, the release is too old; see `reference/setup.md`.
2. Pass the Terraform root module directory (the one with the root `.tf`
   files). A directory with no `.tf` files of its own is treated as a folder of
   separate projects, and every address gets a `<dir>::` prefix.
3. Pass `--color never` before the subcommand. Results go to stdout; progress
   and warnings go to stderr. Stderr shows only the first five diagnostics; the
   `diagnostics` list in `report --format json` has all of them.
4. If a diagnostic is `IAC103`, a remote module has not been downloaded. Ask the
   user to run `terraform get` in that root before trusting results that touch
   the module. Do not run it yourself without asking.

## Answering a lineage question

1. Find the exact address: `iaclineage find <root> <query>`. Queries can be a
   full address, `aws_vpc.main`, `var.env`, `module.net.aws_vpc.main`, or a bare
   name. Exit code `1` means zero or several matches; pick the exact address
   from the list and run it again. When the user names something loosely ("the
   VPC"), list every declaration with
   `iaclineage report <root> --format json --output <tmp>/index.json` and search
   the `entities[].address` values.
2. Trace it: `iaclineage trace <root> <address> --direction up` for what it
   uses, `down` for what uses it, `both` for both. Add `--format json` when you
   need to process the result and `--depth N` to limit hops.
3. Explain the chain in plain words, root inputs first. Follow each branch by
   its `field`, not by depth alone. Cite `file:line` for the hops that matter
   and name every unresolved target. iaclineage does not print literal values;
   to quote one, read the cited line in the `.tf` file.
4. For visual exploration, offer the interactive report:
   `iaclineage report <root> --format interactive-html --output <dir>/lineage.html`.
   Write it outside the scanned repository.

## Reading results correctly

| Output | Means | Does not mean |
| --- | --- | --- |
| `none`, or no hops | No static reference exists | The two are independent. Provider settings such as `region = var.region` reach every resource on that provider with no edge. |
| No `provider.*` hop on a resource | It uses the default configuration of its provider type, such as `provider.aws`; trace that address for its settings | It has no provider |
| A downstream hop | Reachable through references | The value will change. Only `terraform plan` shows that. |
| `module-input` hop | A module argument sets a child variable | A runtime value was evaluated |
| `field X` | The consumer argument or attribute holding the reference. At a module call it is the module argument. | |
| A `module-input` hop with no hop beyond it for that field | The module argument is a literal or has no references | Missing data |
| `module-output` hop | Something reads the whole module (`module.x`, `module.x[*].y`, `depends_on`), which reads all its outputs | |
| `unresolved` | The target declaration was not found: a typo, an undownloaded module (`IAC103`), or a provider alias passed through `providers` | The target does not exist in real infrastructure |
| `ambiguous` | Several declarations share the address; the tool does not pick one | |
| `unused` exit `1` | It found unused declarations | The command failed |

## Common mistakes

- Treating `none` as proof of independence. Say "no static reference" and
  mention the provider gap when the question is about region, account, or
  credentials.
- Mixing edge directions. `trace` always shows `A -> B` as "A uses B". In the
  `report --format json` and `find` output, module-input references run from
  the module call to the child variable.
- Predicting plan results (replace or update in place) as if iaclineage said
  so. Label that as your own Terraform knowledge.
- Deleting `unused` results without checking other roots that call the same
  module, `-var` flags, and `TF_VAR_` variables.
- Piping a command into `head` or `tail` and then reading `$?`. That is the
  exit code of the last command in the pipe.
- Using `--include-source-values` without the user's consent. The report can
  then contain secrets.

## Reference files

Read these only when you need the detail:

- `reference/commands.md`: every command, option, and matching rule
- `reference/output.md`: text and JSON shapes, address format, exit codes
- `reference/diagnostics.md`: `IAC` codes and what to do about each
- `reference/limits.md`: what the analysis does not see
- `reference/setup.md`: installing, downloading modules, privacy
