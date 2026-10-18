# Architecture decision records

These records capture the decisions that shape VitaDrive's runtime and deployment.

| ADR | Decision | Status |
|---|---|---|
| [0001](0001-fastapi.md) | FastAPI for the application API | Accepted |
| [0002](0002-sqlalchemy-core-alembic.md) | SQLAlchemy Core and Alembic | Accepted |
| [0003](0003-database-rate-limits.md) | Database-backed rate limits | Accepted |
| [0004](0004-daily-reading-summaries.md) | Compact readings older than 30 days | Accepted |
| [0005](0005-native-es-modules.md) | Browser-native ES modules without a build step | Accepted |
| [0006](0006-lightweight-runtime.md) | JSON anomaly inference and a stdlib bridge transport | Accepted |
| [0007](0007-clerk-authentication.md) | Clerk JWTs with an auth-off local mode | Accepted |
| [0008](0008-strict-csp.md) | Strict script Content Security Policy | Accepted |
