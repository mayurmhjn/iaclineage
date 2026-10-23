# Limits

iaclineage reports what the source says statically. State these limits when
they affect an answer.

- **No evaluation.** It does not run Terraform or read plan or state files.
  Values, `count`, `for_each`, and conditionals are not evaluated, so a trace
  shows possible paths, not which branch runs. Conditional roles (`condition`,
  `true`, `false`) are reported, but the selected branch is unknown.
- **Implicit provider edges are missing.** A resource, data source, or import
  with an explicit `provider = aws.west` links to `provider.aws.west`. One
  without `provider =` is not linked to the default configuration, and module
  `providers` maps are not followed. A setting such as `region = var.region` affects every
  resource on that provider even though no edge shows it.
- **Provider aliases in child modules.** Aliases a child receives through a
  `providers` map stay unresolved, so `--fail-on unresolved` can fail on valid
  code.
- **Computed attributes.** Resolving `aws_vpc.main.id` confirms the
  declaration exists, not what the provider returns.
- **Missing references are not proof.** No reference does not mean no runtime
  dependency.
- **Built-ins are not edges.** `count`, `each`, `self`, `path`, and
  `terraform.*` do not create references. Iterators of `dynamic` blocks and
  `for` expressions are local symbols.
- **Modules.** Local modules are followed, including paths outside the scanned
  directory. Remote modules are read only from `.terraform/modules` after
  `terraform get`. A custom `TF_DATA_DIR` is not supported. The cache is not
  checked for freshness or version constraints.
- **False `unresolved` references.** These are reported as unresolved even in
  valid code, so `--fail-on unresolved` fails on them: attribute names in
  `lifecycle { ignore_changes = [...] }`, the old address in a `moved` block's
  `from`, `ephemeral.*` references, and `data` sources declared inside a
  `check` block.
- **Override files.** Declarations in `*_override.tf` files are indexed as
  duplicates, so their address is ambiguous in `find` and `trace`, and
  references to them are `ambiguous`.
- **Not indexed:** `ephemeral` and `removed` blocks, `data` blocks nested in
  `check`, module directories that no call uses, remote state, `.tf.json`
  files, and `.tofu` files.
