# ADR 0006: Lightweight model inference and bridge transport

## Status

Accepted — 2026-10-17.

## Context

The deployed service should not need a machine-learning framework just to
score readings. A vehicle-side bridge must also keep working through network
outages without depending on a separate HTTP client package.

## Decision

Export the trained anomaly model to JSON and run inference in plain Python.
Use the Python standard library for bridge HTTP delivery and a persistent
SQLite offline buffer; install the optional `obd` extra only on hardware that
needs an ELM327 adapter.

## Consequences

The application and transport avoid heavyweight runtime dependencies, and
the bridge can retry buffered readings after connectivity returns. Model
training remains a separate PyTorch workflow, and the bridge process must
manage its local buffer and hardware connection.
