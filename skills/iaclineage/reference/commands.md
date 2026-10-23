# Commands

All commands take a local directory as `<root>`. Global options go before the
subcommand:

```sh
iaclineage --version
iaclineage --color never <command> ...   # auto (default), always, or never
```

`NO_COLOR` in the environment also turns color off in `auto` mode.

## scan

```sh
iaclineage scan <root>
```

Prints every `.tf` file below `<root>`, one relative path per line, sorted.
`.git` and `.terraform` directories are skipped. It does not parse anything.

## find

```sh
iaclineage find <root> <query> [--format text|json] [--output FILE]
```

Looks up one declaration and lists its direct incoming and outgoing references.
Matching runs in this order, and the first rule with any match wins:

1. Exact address, such as `resource.aws_vpc.main` or `prod::output.id`.
   `var.NAME` is accepted for `variable.NAME` at the start or after a `<dir>::`
   prefix. Inside a module, use `module.x.variable.NAME`.
2. Terraform resource address without the `resource.` part, such as
   `aws_vpc.main` (root only) or `module.network.aws_vpc.main`.
3. Final name segment, such as `main`. For providers, the provider name
   (`aws`) matches every configuration of it, aliases included.

One match exits `0`. Zero or several matches exit `1` and list the candidates.
`--output` writes the result to a file in the same format. Duplicate addresses,
for example from `*_override.tf` files, always give several matches and cannot
be told apart. For a variable with a `validation` block, `find` lists its
reference to itself as both outgoing and incoming.

## trace

```sh
iaclineage trace <root> <query> [--direction up|down|both] [--depth N]
                 [--format text|json] [--output FILE]
```

Uses the same matching as `find` and needs exactly one match (otherwise exit
`1`, as with `find`). Then it walks references breadth first:

- `up` follows what the declaration uses, `down` what uses it. `both` is the
  default and prints both lists.
- `--depth N` stops after N hops (N is 1 or more). Without it the walk goes
  until nothing new is reachable.
- A declaration is expanded once (a module call once per argument it is
  reached through). A cycle shows its closing hop and stops.
- Each reference is listed once, at the first depth it is reached.
- At a module call reached through one argument, the walk continues only
  through that argument and to readers of the whole module. It does not pull
  in the module's other inputs, nor its `count`, `for_each`, or `depends_on`
  references.
- A reference to the whole module object (`module.x`, `module.x[*].out`,
  `depends_on = [module.x]`, or a `moved` block) adds `module-output` hops from
  the call to each of its outputs. Going upstream from such a reader enters the
  call through every argument, because the whole object depends on all of
  them.
- A variable's reference to itself inside its own `validation` block is
  skipped.
- Unresolved targets end their branch.

The walk covers whole declarations. To narrow lineage to a single attribute,
use the interactive report, which can follow one field.

## unused

```sh
iaclineage unused <root>
```

Lists variables, locals, and child-module outputs that no expression reads.
Exit `1` when it finds any, `0` when it finds none. Rules:

- A module argument that sets a child variable is not a read. A child variable
  set by the caller but never read inside the module is unused.
- A variable's own `validation` block is not a read.
- Reading the whole module object (`module.x`, `module.x[*].out`) reads every
  output. `depends_on` and `moved` blocks do not.
- When several calls use one module source, a declaration in it is reported
  only if no call uses it. Each call's instance is then listed.
- Root module outputs are skipped, because they are read from outside the
  configuration.
- Declarations inside downloaded modules under `.terraform` are skipped.
- Only the root and the modules it calls are read. A module directory that no
  call uses is not checked, even though `scan` lists its files.

## report

```sh
iaclineage report <root> --output FILE [--format html|json|interactive-html]
                  [--include-source-values] [--fail-on STATES]
```

- `html` (default): a static summary page.
- `json`: the full index (entities, references, diagnostics). See `output.md`.
- `interactive-html`: one offline HTML file with search, filters, field-level
  lineage, and source ranges. Open it in Chrome, Chromium, or Edge.
- `--include-source-values`: adds raw source text to an `interactive-html`
  report. Other formats reject it with `IAC009`. The file can then contain
  secrets, comments, and literal values. Use it only when the user asks.
- `--fail-on unresolved,ambiguous`: writes the report, then exits `1` if any
  reference has one of the listed resolution states. Repeated states are
  ignored; other values are rejected with exit `2`.

The output directory must already exist (`IAC005` otherwise). Keep reports
outside the scanned repository and the modules it calls.
