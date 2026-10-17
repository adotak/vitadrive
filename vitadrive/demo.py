"""Demo data: simulated vehicles with driving history and a few past services."""
from __future__ import annotations

import random
from datetime import datetime, time, timedelta, timezone

from .adapters.simulator import VehicleSimulator
from .models import FuelLog, Powertrain, SensorReading, ServiceRecord, Trip, Vehicle
from .service import VitaDrive
from .storage import LOCAL_OWNER, Store

DEMO_VEHICLES = [
    Vehicle(vin="VDDEMOICE0000001", make="Toyota", model="Corolla", year=2023, powertrain=Powertrain.ICE,
            tank_capacity_l=50, rated_consumption=6.5),
    Vehicle(vin="VDDEMOEV00000002", make="Tesla", model="Model 3", year=2024, powertrain=Powertrain.EV,
            battery_capacity_kwh=60, rated_consumption=15),
]

_STATIONS = ["Shell", "BP", "Esso", "Texaco"]
_CHARGERS = ["Home charger", "Home charger", "Tesla Supercharger", "Ionity"]


def _demo_trip_starts(start: datetime, end: datetime, rng: random.Random) -> list[datetime]:
    slots: list[datetime] = []
    day = start.date()
    while day <= end.date():
        times = (time(8), time(12, 30), time(17, 30)) if day.weekday() < 5 else (
            time(10, 30), time(11, 45), time(13),
        )
        for slot in times:
            candidate = datetime.combine(day, slot, tzinfo=start.tzinfo or timezone.utc)
            if start < candidate < end - timedelta(minutes=5):
                slots.append(candidate)
        day += timedelta(days=1)
    if not slots:
        return [start + (end - start) / 2]
    count = min(rng.choices((1, 2, 3), weights=(3, 5, 2), k=1)[0], len(slots))
    return sorted(rng.sample(slots, count))


def _demo_distances(total_km: float, count: int, rng: random.Random) -> list[float]:
    distances: list[float] = []
    remaining = total_km
    for index in range(count - 1):
        reserve = 0.1 * (count - index - 1)
        short_distance = min(rng.uniform(2, 4), remaining - reserve)
        distances.append(max(0.05, short_distance))
        remaining -= distances[-1]
    distances.append(remaining)
    return distances


def _seed_demo_trips(
    store: Store,
    vehicle: Vehicle,
    readings: list[SensorReading],
    rng: random.Random,
) -> None:
    ev = vehicle.powertrain is Powertrain.EV
    level_attr = "hv_battery_soc_pct" if ev else "fuel_level_pct"
    for previous, reading in zip(readings, readings[1:], strict=False):
        total_distance = reading.odometer_km - previous.odometer_km
        if total_distance < 0.15:
            continue
        starts = _demo_trip_starts(previous.timestamp, reading.timestamp, rng)
        distances = _demo_distances(total_distance, len(starts), rng)
        previous_level = getattr(previous, level_attr)
        current_level = getattr(reading, level_attr)
        weights = [distance * (1.12 if distance < 5 else 1) for distance in distances]
        total_weight = sum(weights)
        level_changes = [
            (current_level - previous_level) * weight / total_weight
            if previous_level is not None and current_level is not None else None
            for weight in weights
        ]
        odometer = previous.odometer_km
        level = previous_level
        for index, (trip_start, distance) in enumerate(zip(starts, distances, strict=False)):
            motorway = distance >= 12 and rng.random() < 0.08
            average_speed = rng.uniform(75, 100) if motorway else rng.uniform(25, 65)
            max_speed = rng.uniform(125, 135) if motorway else min(
                115, average_speed * rng.uniform(1.05, 1.35),
            )
            next_start = starts[index + 1] if index + 1 < len(starts) else reading.timestamp
            available = max(300, (next_start - trip_start).total_seconds() - 60)
            duration = min(available, max(300, distance / average_speed * 3600))
            end_level = (
                level + level_changes[index]
                if level is not None and level_changes[index] is not None else None
            )
            end_odometer = (
                reading.odometer_km if index == len(starts) - 1 else odometer + distance
            )
            store.add_trip(Trip(
                vin=vehicle.vin,
                started_at=trip_start,
                ended_at=trip_start + timedelta(seconds=duration),
                start_odometer_km=odometer,
                end_odometer_km=end_odometer,
                start_level_pct=level,
                end_level_pct=current_level if index == len(starts) - 1 else end_level,
                max_speed_kmh=max_speed,
                samples=2,
            ))
            odometer = end_odometer
            level = end_level


def seed_vehicle(vd: VitaDrive, vehicle: Vehicle, owner_id: str = LOCAL_OWNER, days: int = 180,
                 per_day: int = 2, seed: int = 7, start_odometer_km: float = 30_000) -> int:
    """Register ``vehicle`` and fill it with simulated history. Returns the number of readings."""
    sim = VehicleSimulator(vehicle, start_odometer_km=start_odometer_km, seed=seed)
    count, prev, rng = 0, None, random.Random(seed)
    ev = vehicle.powertrain is Powertrain.EV
    level_attr, capacity = (("hv_battery_soc_pct", vehicle.battery_capacity_kwh) if ev
                            else ("fuel_level_pct", vehicle.tank_capacity_l))
    stations = _CHARGERS if ev else _STATIONS
    trip_days = min(max(days, 0), 30)
    recent_readings: list[SensorReading] = []
    trip_reading_limit = trip_days * max(per_day, 0) + 1
    with vd.store.transaction():
        vd.store.upsert_vehicle(vehicle, owner_id=owner_id)
        for reading in sim.history(days=days, per_day=per_day):
            vd.ingest(reading)
            if trip_reading_limit and len(recent_readings) < trip_reading_limit:
                recent_readings.append(reading)
            elif trip_reading_limit:
                recent_readings.pop(0)
                recent_readings.append(reading)
            count += 1
            level = getattr(reading, level_attr)
            if capacity and prev is not None and level is not None and level - prev > 20:
                amount = round((level - prev) / 100 * capacity, 1)
                price = rng.uniform(0.18, 0.42) if ev else rng.uniform(1.58, 1.79)
                vd.store.add_fuel_log(FuelLog(
                    vin=vehicle.vin, filled_on=reading.timestamp.date(), odometer_km=reading.odometer_km,
                    amount=amount, cost=round(amount * price, 2), station=rng.choice(stations)))
            prev = level if level is not None else prev
            if count == max(days * per_day // 9, 1):
                for item in ("tire_rotation", "cabin_filter", "wiper_blades"):
                    vd.store.add_service(ServiceRecord(
                        vin=vehicle.vin, item=item, performed_on=reading.timestamp.date(),
                        odometer_km=reading.odometer_km, cost=45.0, shop="VitaDrive Demo Garage"))
        _seed_demo_trips(vd.store, vehicle, recent_readings, rng)
    return count
