# ADR 0003: Database-backed rate limits

## Status

Accepted — 2026-10-17.

## Context

Vercel can handle requests in separate, short-lived instances. An in-memory
counter would be local to one process and would reset when that process is
replaced.

## Decision

Persist rate-limit counters in the database, keyed by the limited subject and
time window. Allow local development to disable the limiter with
`VITADRIVE_RATE_LIMITS=0`.

## Consequences

Limits are shared across application instances and survive process restarts.
Each limited request adds database work, and database availability is required
for enforcing the configured limits.
