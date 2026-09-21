# Changelog

All notable changes to IaCLineage are documented here.

This project follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)
and uses Semantic Versioning concepts with Python PEP 440 pre-release versions.

## [0.1.0b3] - [Unreleased]

### Added

- Demo walkthrough video and GIF in `README.md` showcasing CLI scan execution, interactive lineage filtering, multi-hop remote module navigation, and source inspection.

### Fixed

- Fixed lineage connection arrowheads to align squarely with card input ports with zero vertical slope entry.
- Prevented overlapping numbered connection badges across shared column lanes with dynamic vertical de-collision spacing.

## [0.1.0b2] - 2026-09-20

### Fixed

- Respect dynamic-block and comprehension iterator scopes without losing references in collection expressions.
- Exclude `terraform.*` built-ins from declaration dependencies.
- Give aliased providers distinct addresses and resolve explicit resource, data, and import provider bindings.
- Retain all `find` matches, including duplicate addresses, and mark duplicate reference targets ambiguous.
- Accept canonical Terraform resource addresses in `find`, including module and project prefixes.
- Return exit code `1` for multiple `find` matches in both text and JSON formats (previously JSON returned `0`).

### Documentation

- Shorten the README, add a demo GIF placeholder, and move detailed report controls into a dedicated guide.

## [0.1.0b1] - 2026-09-12

### Added

- Initial public beta of the local-only Terraform source explorer.
- Offline interactive lineage reports with focused graph and evidence views.
- Windows, Linux, and macOS compatibility workflow for Python 3.12–3.14.

### Security

- Private Vulnerability Reporting policy for the latest release.
