"""Trip detection and driving-efficiency summaries."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any

from .models import Powertrain, SensorReading, Trip, Vehicle
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


def _energy_distance(vehicle: Vehicle, trip: Trip) -> tuple[float, float] | None:
    start, end = trip.start_level_pct, trip.end_level_pct
    capacity = vehicle.battery_capacity_kwh if vehicle.powertrain is Powertrain.EV else vehicle.tank_capacity_l
    if (
        start is None
        or end is None
        or capacity is None
        or capacity <= 0
        or start <= end
        or trip.distance_km <= 0
    ):
        return None
    return (start - end) * capacity / 100, trip.distance_km


def _consumption(vehicle: Vehicle, trips: list[Trip]) -> float | None:
    totals = [result for trip in trips if (result := _energy_distance(vehicle, trip)) is not None]
    if not totals:
        return None
    energy = sum(value[0] for value in totals)
    distance = sum(value[1] for value in totals)
    return energy / distance * 100


def _weekly_summary(vehicle: Vehicle, trips: list[Trip], today: date) -> list[dict[str, Any]]:
    current_monday = today - timedelta(days=today.weekday())
    week_starts = [current_monday - timedelta(weeks=offset) for offset in range(7, -1, -1)]
    result: list[dict[str, Any]] = []
    for start in week_starts:
        end = start + timedelta(days=7)
        week_trips = [
            trip for trip in trips
            if start <= _utc(trip.started_at).date() < end
        ]
        if not week_trips:
            result.append({"week_start": start.isoformat(), "distance_km": None, "consumption": None})
            continue
        result.append({
            "week_start": start.isoformat(),
            "distance_km": round(sum(trip.distance_km for trip in week_trips), 1),
            "consumption": (
                None if (value := _consumption(vehicle, week_trips)) is None else round(value, 2)
            ),
        })
    return result


def _insights(
    vehicle: Vehicle,
    trips: list[Trip],
    vs_rated_pct: float | None,
) -> list[dict[str, str]]:
    insights: list[dict[str, str]] = []
    if trips and sum(trip.distance_km < 5 for trip in trips) / len(trips) > 0.4:
        insights.append({
            "kind": "tip",
            "text": "Short trips can use more fuel or energy per kilometre; combining errands may help.",
        })
    if vs_rated_pct is not None and vs_rated_pct > 10:
        insights.append({
            "kind": "tip",
            "text": f"Recent consumption is {vs_rated_pct:.0f}% above the rated figure; smoother driving may help.",
        })
    elif vs_rated_pct is not None and vs_rated_pct < -10:
        insights.append({
            "kind": "good",
            "text": "Your recent consumption is below the vehicle's rated figure.",
        })
    if trips and sum(trip.max_speed_kmh > 120 for trip in trips) / len(trips) > 0.2:
        insights.append({
            "kind": "tip",
            "text": "Driving at 100 instead of 120 km/h uses noticeably less fuel or energy.",
        })
    if not insights:
        energy = "energy" if vehicle.powertrain is Powertrain.EV else "fuel"
        insights.append({
            "kind": "good",
            "text": f"Your {energy} use looks steady over the last month.",
        })
    return insights[:4]


def trip_summary(
    vehicle: Vehicle,
    trips: list[Trip],
    days: int = 30,
    today: date | None = None,
) -> dict[str, Any]:
    today = today or date.today()
    first_day = today - timedelta(days=days - 1)
    window = [
        trip for trip in trips
        if first_day <= _utc(trip.started_at).date() <= today
    ]
    distance = sum(trip.distance_km for trip in window)
    average = _consumption(vehicle, window)
    rated = vehicle.rated_consumption
    vs_rated = (average / rated - 1) * 100 if average is not None and rated and rated > 0 else None
    return {
        "trips": len(window),
        "distance_km": round(distance, 1),
        "km_per_day": round(distance / days, 1),
        "avg_consumption": None if average is None else round(average, 2),
        "rated_consumption": rated,
        "vs_rated_pct": None if vs_rated is None else round(vs_rated, 1),
        "unit": "kWh/100 km" if vehicle.powertrain is Powertrain.EV else "L/100 km",
        "weekly": _weekly_summary(vehicle, trips, today),
        "insights": _insights(vehicle, window, vs_rated),
    }


def trip_payload(vehicle: Vehicle, trip: Trip) -> dict[str, Any]:
    energy_distance = _energy_distance(vehicle, trip)
    consumption = None
    if energy_distance is not None:
        consumption = round(energy_distance[0] / energy_distance[1] * 100, 2)
    return {
        **trip.model_dump(mode="json"),
        "distance_km": round(trip.distance_km, 1),
        "duration_s": round(trip.duration_s),
        "avg_speed_kmh": round(trip.avg_speed_kmh, 1),
        "consumption": consumption,
    }
