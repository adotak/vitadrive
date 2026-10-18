# Changelog

All notable changes to VitaDrive are documented here. This project follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Safe Alembic schema adoption and versioned migrations ([#21](https://github.com/adotak/vitadrive/pull/21)).
- Database-backed request limits, query caps, and a daily Ask quota ([#22](https://github.com/adotak/vitadrive/pull/22)).
- Daily summaries for readings older than 30 days, preserving long-term trends ([#23](https://github.com/adotak/vitadrive/pull/23)).
- Structured JSON logs, request IDs, database health, and consistent API errors ([#24](https://github.com/adotak/vitadrive/pull/24)).
- Browser end-to-end coverage and its CI workflow ([#28](https://github.com/adotak/vitadrive/pull/28)).
- A multi-stage Docker image, PostgreSQL Compose setup, and smoke coverage ([#30](https://github.com/adotak/vitadrive/pull/30)).
- Persisted trip detection, summaries, insights, and dashboard history ([#31](https://github.com/adotak/vitadrive/pull/31)).
- OBD-II bridge transport with retries, offline buffering, and simulation ([#32](https://github.com/adotak/vitadrive/pull/32)).

### Changed

- Added package typing, mypy checks, a coverage gate, and pre-commit checks ([#26](https://github.com/adotak/vitadrive/pull/26)).
- Split dashboard JavaScript into browser-native ES modules ([#27](https://github.com/adotak/vitadrive/pull/27)).
- Added Lighthouse CI gates and improved dashboard contrast and image loading ([#29](https://github.com/adotak/vitadrive/pull/29)).

### Security

- Added security headers, a stricter content policy, CORS controls, Clerk issuer checks, and audit coverage ([#25](https://github.com/adotak/vitadrive/pull/25)).
- Removed inline script execution from the dashboard policy ([#27](https://github.com/adotak/vitadrive/pull/27)).

### Fixed

- Improved mobile text legibility and the dashboard's third-party image behavior as part of the Lighthouse work ([#29](https://github.com/adotak/vitadrive/pull/29)).

## [0.1.0]

Pre-roadmap baseline: vehicle monitoring, threshold alerts, maintenance
forecasts, range estimates, the REST API, dashboard, and SQLite storage.
