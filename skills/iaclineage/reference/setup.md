# Setup

## Install

iaclineage needs Python 3.12 or newer. Ask the user before installing.

```sh
pip install iaclineage            # or: pipx install iaclineage / uv tool install iaclineage
iaclineage --version
```

`trace` and `unused` need version 0.1.0 or newer. Check for them with
`iaclineage trace --help`. If it fails, ask the user to upgrade with
`pip install --upgrade iaclineage`. Until the user
upgrades, use `find` for direct references and `report --format json` for the
full reference list.

## Remote modules

Registry and Git modules must be downloaded before they can be analyzed. In
each Terraform root, the user runs:

```sh
terraform get
```

That needs Terraform and any credentials for private modules. Keep the default
`.terraform/modules` cache. Local-only projects can skip this step. Ask before
running it, because it uses the network.

## Privacy

- Analysis is local. Nothing is sent anywhere.
- Reports leave out literal values and raw source unless
  `--include-source-values` is given, and that flag works only with
  `interactive-html`. They do include addresses, file paths, field names, and
  reference expressions such as `name = var.env`.
- Write reports outside the scanned repository and the modules it calls, so
  they are not committed or scanned later.
- Diagnostics and errors never print source text.
