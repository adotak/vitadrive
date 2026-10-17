"""Trip detection and driving-efficiency summaries."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from .models import SensorReading, Trip
from .storage import Store

TRIP_GAP = timedelta(minutes=10)
MIN_MOVE_KM = 0.05


def _utc(timestamp: datetime) -> datetime:
    if timestamp.tzinfo is None:
        return timestamp.replace(tzinfo=timezone.utc)
    return timestamp.astimezone(timezone.utc)


def _reading_level(reading: SensorReading) -> float | None:
    return reading.fuel_level_pct if reading.fuel_level_pct is not None else reading.hv_battery_soc_pct


def _segment_speed_kmh(previous: SensorReading, reading: SensorReading) -> float:
    seconds = (_utc(reading.timestamp) - _utc(previous.timestamp)).total_seconds()
    distance = reading.odometer_km - previous.odometer_km
    return distance * 3600 / seconds if seconds >= 5 else 0.0


def _qualifies(previous: SensorReading, reading: SensorReading) -> bool:
    gap = _utc(reading.timestamp) - _utc(previous.timestamp)
    return (
        gap > timedelta(0)
        and gap <= TRIP_GAP
        and reading.odometer_km - previous.odometer_km >= MIN_MOVE_KM
    )


def _trip_from_pair(previous: SensorReading, reading: SensorReading) -> Trip:
    return Trip(
        vin=reading.vin,
        started_at=previous.timestamp,
        ended_at=reading.timestamp,
        start_odometer_km=previous.odometer_km,
        end_odometer_km=reading.odometer_km,
        start_level_pct=_reading_level(previous),
        end_level_pct=_reading_level(reading),
        max_speed_kmh=_segment_speed_kmh(previous, reading),
        samples=2,
    )


def detect_trips(readings: list[SensorReading]) -> list[Trip]:
    ordered = sorted(readings, key=lambda reading: _utc(reading.timestamp))
    detected: list[Trip] = []
    current: Trip | None = None

    for previous, reading in zip(ordered, ordered[1:], strict=False):
        if not _qualifies(previous, reading):
            if current is not None:
                detected.append(current)
                current = None
            continue

        speed = _segment_speed_kmh(previous, reading)
        if current is None:
            current = _trip_from_pair(previous, reading)
        else:
            current = current.model_copy(update={
                "ended_at": reading.timestamp,
                "end_odometer_km": reading.odometer_km,
                "end_level_pct": _reading_level(reading),
                "max_speed_kmh": max(current.max_speed_kmh, speed),
                "samples": current.samples + 1,
            })

    if current is not None:
        detected.append(current)
    return detected


def update_trips(store: Store, previous: SensorReading | None, reading: SensorReading) -> None:
    if previous is None or _utc(reading.timestamp) <= _utc(previous.timestamp):
        return
    if not _qualifies(previous, reading):
        return

    latest = store.latest_trip(reading.vin)
    if latest is not None and _utc(latest.ended_at) == _utc(previous.timestamp):
        store.update_trip(latest.model_copy(update={
            "ended_at": reading.timestamp,
            "end_odometer_km": reading.odometer_km,
            "end_level_pct": _reading_level(reading),
            "max_speed_kmh": max(latest.max_speed_kmh, _segment_speed_kmh(previous, reading)),
            "samples": latest.samples + 1,
        }))
    else:
        store.add_trip(_trip_from_pair(previous, reading))
