# ADR 0005: Native dashboard ES modules

## Status

Accepted — 2026-10-17.

## Context

The dashboard is a small static SPA. Introducing a bundler would add a
separate build pipeline and generated assets to deploy and maintain.

## Decision

Use browser-native JavaScript ES modules in `vitadrive/static/js/`, served
directly by FastAPI. Share state through module exports and use delegated
actions instead of inline event-handler attributes.

## Consequences

There is no frontend build step and browser tooling can work against the
source modules directly. The supported browser must implement ES modules, and
module boundaries and imports are maintained by hand.
