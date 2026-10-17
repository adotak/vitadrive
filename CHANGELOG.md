# Changelog

All notable changes to VitaDrive are documented here. This project follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [1.0.0] - 2026-10-17

### Added

- Safe Alembic adoption of compatible legacy databases and versioned migrations ([#21](https://github.com/adotak/vitadrive/pull/21)).
- Database-backed request limits, query caps, and a daily Ask quota ([#22](https://github.com/adotak/vitadrive/pull/22)).
- Daily summaries for readings older than 30 days, preserving long-term trends ([#23](https://github.com/adotak/vitadrive/pull/23)).
- Structured JSON logs, request IDs, database health, and consistent API errors ([#24](https://github.com/adotak/vitadrive/pull/24)).
- Browser end-to-end coverage and its CI workflow ([#28](https://github.com/adotak/vitadrive/pull/28)).
- A multi-stage Docker image, PostgreSQL Compose setup, and smoke coverage ([#30](https://github.com/adotak/vitadrive/pull/30)).
- Persisted trip detection, summaries, insights, and dashboard history ([#31](https://github.com/adotak/vitadrive/pull/31)).
- OBD-II bridge transport with retries, offline buffering, and simulation ([#32](https://github.com/adotak/vitadrive/pull/32)).
- Architecture and contributor guides, ADRs, screenshots, repository templates, and documentation integrity tests ([#33](https://github.com/adotak/vitadrive/pull/33)).

### Changed

- Added package typing, mypy checks, a coverage gate, and pre-commit checks ([#26](https://github.com/adotak/vitadrive/pull/26)).
- Reworked dashboard JavaScript into native modules and delegated actions, removing inline script execution from the CSP ([#27](https://github.com/adotak/vitadrive/pull/27)).
- Prepared 1.0.0 release metadata, upgrade guidance, and version-consistency checks ([#34](https://github.com/adotak/vitadrive/pull/34)).

### Security

- Added security headers, a stricter content policy, CORS controls, Clerk issuer checks, and audit coverage ([#25](https://github.com/adotak/vitadrive/pull/25)).

### Fixed

- Added Lighthouse CI gates and improved dashboard contrast, mobile legibility, and third-party image behavior ([#29](https://github.com/adotak/vitadrive/pull/29)).

## [0.1.0]

Pre-roadmap baseline: vehicle monitoring, threshold alerts, maintenance
forecasts, range estimates, the REST API, dashboard, and SQLite storage.

[Unreleased]: https://github.com/adotak/vitadrive/compare/v1.0.0...HEAD
[1.0.0]: https://github.com/adotak/vitadrive/releases/tag/v1.0.0
