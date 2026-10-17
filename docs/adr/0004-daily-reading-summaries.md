# ADR 0004: Daily summaries for older readings

## Status

Accepted — 2026-10-17.

## Context

Keeping every sensor sample indefinitely makes the readings table grow with
vehicle reporting frequency, while maintenance and reports still need
long-term trends.

## Decision

Keep recent readings in full and compact complete UTC days older than 30 days
into `readings_daily` summaries. Preserve the latest timestamp and odometer,
average numeric sensor values, union diagnostic codes, and retain a sample
count.

## Consequences

Storage growth is reduced while long-term trend queries remain available.
Historical values past the retention window are daily aggregates, not original
samples; trip history is stored separately and is not compacted.
