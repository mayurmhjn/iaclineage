# Platform support

IaCLineage targets Python 3.12–3.14 on Windows, Linux, and macOS.
Each release candidate must pass the compatibility workflow on all three platforms.
The wheel is platform-independent; its parser dependencies must have compatible
wheels for the target Python version. Reports are self-contained local HTML files.

No Terraform executable, local server, Node.js, or browser automation is needed
to use the CLI or open a report.

## Install

Install the published version from [PyPI](https://pypi.org/project/iaclineage/):

```sh
pip install iaclineage
iaclineage --version
```

For pre-release validation, use the TestPyPI instructions in the
[tester guide](tester-guide.md).

## Browser support

Open interactive reports in current Chrome, Chromium, or Microsoft Edge. Browser
automation is a development-only check; it is not required by end users.

## Development verification

```sh
uv sync --locked --group dev
uv run --no-sync pytest
node --test tests/test_lineage_ui.cjs
uv run --no-sync python scripts/test_wheel.py
```

The GitHub compatibility workflow builds one candidate wheel and validates it on
Windows, Ubuntu, and macOS for Python 3.12–3.14. Artifacts are retained for 14
days. A successful workflow does not itself publish a package.
