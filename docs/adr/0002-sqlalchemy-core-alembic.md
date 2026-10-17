# ADR 0002: SQLAlchemy Core and Alembic

## Status

Accepted — 2026-10-17.

## Context

The application needs SQLite for a simple local setup and PostgreSQL for
concurrent deployed use. Existing domain objects are Pydantic models, and
schema changes must be reproducible across both database engines.

## Decision

Use SQLAlchemy Core tables and explicit queries, with Alembic migrations.
Serialize domain payloads in text columns while keeping ownership and query
keys in indexed columns.

## Consequences

The same storage layer supports SQLite and PostgreSQL without an ORM identity
map. Queries and migrations remain explicit, but developers must update the
Core metadata and review generated migration scripts when changing the
schema. Safe stamping preserves compatible legacy databases during adoption.
