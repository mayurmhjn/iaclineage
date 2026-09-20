# Releasing IaCLineage

The next candidate is `0.1.0b2`. It is not published until the publishing
workflow completes. Its changes are recorded in [CHANGELOG.md](../CHANGELOG.md).

## Prepare and validate

Keep the version in `pyproject.toml`, `src/iaclineage/__init__.py`, `uv.lock`,
and the tester guide consistent. Record the actual release date in the changelog
when publishing.

```sh
uv sync --locked --group dev
uv run --no-sync pytest --cov=iaclineage --cov-branch --cov-fail-under=85
node --test tests/test_lineage_ui.cjs
uv run --no-sync python scripts/test_wheel.py --browser
uv build
uvx twine check --strict dist/*
```

Browser setup is in the [platform guide](platform-support.md). Review the
distribution inventory and changes before committing; local checks alone do
not establish compatibility on every platform.

## Publish

1. Commit and push the reviewed candidate. Require the **Platform compatibility**
   workflow to pass for that commit on Windows, Ubuntu, and macOS with Python
   3.12, 3.13, and 3.14.
2. Create and push the matching tag, `v0.1.0b2`.
3. Run **Publish Python package** manually against that tag. The workflow
   repeats compatibility checks, verifies version declarations, builds the
   distributions, checks package metadata, and tests the installed wheel.
4. Approve the `testpypi` environment. After upload, the workflow installs the
   candidate from TestPyPI and exercises the CLI and interactive report.
5. Review the results, then approve the `pypi` environment. It promotes the
   same distributions to PyPI.
6. Verify `pip install iaclineage==0.1.0b2` in a fresh environment, check
   `iaclineage --version`, and generate an interactive report. Publish release
   notes describing the changed JSON `find` exit code for multiple matches.

The GitHub environments must retain their required reviewers and tag policies.
Publication uses Trusted Publishing; no repository API token is required.

## Demo recording

Save a short recording to `docs/images/demo.gif`. The commented image near the
top of [README.md](../README.md) already points to its public raw-file URL;
uncomment it once the GIF exists. Use synthetic Terraform source and show
declaration search, field selection, and an upstream/downstream trace.
