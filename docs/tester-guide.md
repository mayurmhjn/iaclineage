# IaCLineage beta 0.1.0b2

IaCLineage analyzes local Terraform source without executing Terraform or making
network calls. Installation and your own module downloads may require network access.

## Install from TestPyPI

These commands require the 0.1.0b2 candidate to be uploaded to TestPyPI first.
Run them in a scratch directory outside your Terraform checkout.
Install dependencies from PyPI, then the exact candidate only from TestPyPI.
This avoids mixing package sources with `--extra-index-url`.

Linux/macOS:

```sh
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python --index-url https://pypi.org/simple tree-sitter==0.26.0 tree-sitter-hcl==1.2.0 pyyaml==6.0.3
uv pip install --python .venv/bin/python --index-url https://test.pypi.org/simple --no-deps iaclineage==0.1.0b2
.venv/bin/iaclineage --version
```

Windows PowerShell:

```powershell
uv venv --python 3.12 .venv
uv pip install --python .venv\Scripts\python.exe --index-url https://pypi.org/simple tree-sitter==0.26.0 tree-sitter-hcl==1.2.0 pyyaml==6.0.3
uv pip install --python .venv\Scripts\python.exe --index-url https://test.pypi.org/simple --no-deps iaclineage==0.1.0b2
.venv\Scripts\iaclineage.exe --version
```

## Verify

Use `.venv/bin/iaclineage` below on Linux/macOS, or
`.venv\Scripts\iaclineage.exe` on Windows. Replace the example paths.

```sh
.venv/bin/iaclineage scan /path/to/terraform-repository
.venv/bin/iaclineage report /path/to/terraform-repository --format interactive-html --output /path/to/reports/lineage.html
```

For remote modules, first run `terraform get` yourself in every Terraform root
using the default `.terraform/modules` cache. Local-only scans do not require
Terraform. Missing cache entries produce `IAC103` instead of guessed relationships.

Check the version is `0.1.0b2`, open the report locally, find a known declaration,
and inspect its references and diagnostics. Keep reports outside the scanned
repository and all followed module directories. Source values remain excluded.
Only use `--include-source-values` deliberately: its output may contain secrets.

After PyPI publication, normal users can run
`pip install iaclineage`. To select this exact beta, use
`pip install iaclineage==0.1.0b2`.
See [platform support](platform-support.md) and the
[security policy](https://github.com/mayurmhjn/iaclineage/security/policy).
