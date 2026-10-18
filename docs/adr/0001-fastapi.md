# ADR 0001: FastAPI application

## Status

Accepted — 2026-10-17.

## Context

VitaDrive needs a typed HTTP API for vehicle telemetry and owner-facing
features, plus a way to serve the dashboard and run locally or as a Vercel
serverless function.

## Decision

Use FastAPI for request routing, validation, dependency injection, OpenAPI
documentation, and static dashboard hosting. Keep business rules in service
modules rather than route handlers.

## Consequences

The API has generated request schemas and can run under Uvicorn locally or
through the root Vercel entry point. The application follows FastAPI and
Starlette's middleware and dependency semantics, so request behavior must be
tested at the HTTP boundary as well as in unit tests.
