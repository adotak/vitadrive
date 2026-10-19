# ADR 0007: Clerk authentication

## Status

Accepted — 2026-10-17.

## Context

Owner-facing vehicle data needs account-level access control without
requiring the application to store user passwords. Local development should
remain possible without configuring an identity provider.

## Decision

Use Clerk-issued session JWTs for dashboard users and verify them against
Clerk's public JWKS. When `CLERK_PUBLISHABLE_KEY` is absent, use the shared
local owner for trusted, auth-off development. Authenticate vehicle ingest
separately with per-vehicle API keys.

## Consequences

The application does not need a Clerk secret key to verify sessions, and
local setup stays small. Auth-off mode is not suitable for public deployment;
deployed environments must configure Clerk and authorized parties correctly.
