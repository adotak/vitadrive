# ADR 0008: Strict script Content Security Policy

## Status

Accepted — 2026-10-17.

## Context

Inline scripts and event attributes require a broad script policy exception.
That exception weakens protection against injected script execution.

## Decision

Load dashboard code as external ES modules, delegate user actions through
data attributes, and omit `'unsafe-inline'` from `script-src`. Keep the
existing inline-style allowance separate from the script policy.

## Consequences

The dashboard works with a restrictive script policy and the browser has
fewer sources from which it may execute code. New interactions must use
external modules or approved script origins rather than inline handlers.
