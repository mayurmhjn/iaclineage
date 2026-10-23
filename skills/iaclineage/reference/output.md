# Output

## Addresses

| Declaration | Address |
| --- | --- |
| `resource "aws_vpc" "main"` | `resource.aws_vpc.main` |
| `data "aws_ami" "base"` | `data.aws_ami.base` |
| `variable "env"` | `variable.env` |
| `locals { name = ... }` | `local.name` |
| `output "id"` | `output.id` |
| `module "network"` | `module.network` |
| `provider "aws"`, with `alias = "west"` | `provider.aws`, `provider.aws.west` |
| `terraform`, `moved`, `import` blocks | `terraform`, `moved.1`, `import.1` (numbered per module) |
| `check "health"` | `check.health` |

Declarations inside a called module carry the call path:
`module.network.resource.aws_vpc.main`, `module.a.module.b.variable.x`.
When the scanned directory holds several projects, each address starts with
the project directory and `::`, such as `prod::resource.aws_vpc.main`.
Projects never link to each other.

Locations are `path:start_line:start_col-end_line:end_col`, 1-based, with the
path relative to the scanned directory.

Field paths name the argument or attribute that holds a reference:
`name`, `tags.Name`, `subnet_ids[0]`, `depends_on[0]`.

## find

Text: the address and location, a structural snippet such as
`resource "aws_vpc" "main" { ... }`, then `Outgoing references:` (`->`) and
`Incoming references (dependents):` (`<-`), each with resolution and location.

JSON:

```json
{"query": "aws_vpc.main", "match": {
  "address": "resource.aws_vpc.main", "kind": "resource",
  "location": "main.tf:1:1-3:2", "snippet": "resource \"aws_vpc\" \"main\" { ... }",
  "outgoing": [{"target": "variable.cidr", "resolution": "resolved", "source_location": "main.tf:2:16-2:24"}],
  "incoming": [{"source": "output.vpc_id", "resolution": "resolved", "source_location": "main.tf:6:11-6:26"}]}}
```

Several matches: `{"query": ..., "matches": [<match objects>]}`. No match:
`{"error": "no match", "query": ...}`. In `find`, a module call's outgoing list
includes its argument bindings to child variables (call to child).

## trace

Text:

```
variable.env  main.tf:2:1-2:18
Upstream (A -> B means A uses B):
  none
Downstream (A -> B means A uses B):
  1  local.name_prefix -> variable.env (reference, resolved, main.tf:12:24-12:31, field value)
  2  module.network -> local.name_prefix (reference, resolved, main.tf:19:12-19:29, field name)
  3  module.network.variable.name -> module.network (module-input, resolved, main.tf:19:3-19:7, field name)
```

Each hop line has the depth, `consumer -> dependency`, the kind, resolution,
location of the reference, and the field when there is one. Only requested
directions are printed.

JSON:

```json
{"query": "var.env", "address": "variable.env", "depth": null,
 "upstream": [],
 "downstream": [{"depth": 1, "consumer": "local.name_prefix", "dependency": "variable.env",
                 "kind": "reference", "resolution": "resolved",
                 "location": "main.tf:12:24-12:31", "field": "value"}]}
```

With zero or several matches, `trace` prints the same JSON as `find`:
`{"error": "no match", "query": ...}` or `{"query": ..., "matches": [...]}`.

`depth` is the `--depth` value or `null`. A direction that was not requested
has no key. `field` is `null` for `module-output` hops.

Hop kinds:

- `reference`: the consumer's expression names the dependency.
- `module-input`: a module argument sets a child variable. The child variable
  is the consumer and the module call is the dependency; `field` is the
  argument name and the location points at it.
- `module-output`: the module call reads one of its outputs, because something
  reads the whole module. The location points at the output block.

## unused

One line per finding: `<address> (<kind>) at <location>`. With no findings:
`No unused variables, locals, or module outputs found.`

## report --format json

```json
{"entities": [{"address": ..., "kind": ..., "location": ..., "path": ...,
               "snippet": ..., "fields": [...]}],
 "references": [{"source": ..., "target": ..., "resolution": ...,
                 "source_location": ..., "source_path": ...,
                 "usage": "subnet_id = module.network.subnet_ids [instance/key selection omitted]",
                 "conditionalRoles": [{"conditional": "<location>", "role": "condition|true|false"}]}],
 "diagnostics": [{"code": "IAC103", "message": ..., "location": ..., "path": ...}]}
```

`source` uses `target`. References have no `kind` key: module argument
bindings are the ones with `usage: null`, and they run
`module.x -> module.x.variable.y`, the reverse of `trace`. `usage` is
`<field> = <reference>`; index and splat parts are replaced by
`[instance/key selection omitted]` and surrounding functions are dropped, so it
is not the original expression.
Each field has `path`, `kind` (`literal`, `reference`, `expression`, `object`,
`tuple`, or `block`), `location`, `value` (always `null` in this format),
`children`, `contextReferences`, and `conditionals`, plus `provisionerType` on
provisioner blocks.

## Exit codes

| Code | Meaning |
| --- | --- |
| `0` | Success. `find` and `trace` found exactly one match. `unused` found nothing. |
| `1` | `find` or `trace` did not match exactly one declaration; `unused` found something; `--fail-on` matched; a file or directory could not be read or written (stderr names the error type, such as `PermissionError`); or an unexpected internal error |
| `2` | Invalid arguments, or an `IAC0xx` error such as a missing path or an unwritable report |
| `130` | Interrupted |

Read stderr to tell the `1` cases apart.
