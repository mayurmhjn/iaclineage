# Contributing to IaCLineage

Thank you for contributing.

## Development

```sh
uv sync --locked --group dev
uv run --no-sync pytest
node --test tests/test_lineage_ui.cjs
```

For browser checks, install the pinned Node dependencies and run:

```sh
npm ci --ignore-scripts
npm run test:browser
```

## Contribution rules

- Keep the analyzer local-only: never add Terraform execution, network access,
  telemetry, provider access, plan/state ingestion, or cloud discovery.
- Treat scanned repositories as read-only and preserve structural-only defaults.
- Add focused regression tests for behavior changes.
- Do not commit generated reports, test artifacts, caches, virtual environments,
  or real Terraform source.

By submitting a contribution, you agree that it may be licensed under the
[Apache License 2.0](LICENSE).
