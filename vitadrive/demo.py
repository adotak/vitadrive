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


def _demo_trip_starts(
    start: datetime,
    end: datetime,
    rng: random.Random,
    count: int,
) -> list[datetime]:
    slots: list[datetime] = []
    day = start.date()
    while day <= end.date():
        times = (
            (time(8), time(10, 30), time(11, 30), time(12, 30), time(17, 30), time(20), time(23))
            if day.weekday() < 5 else
            (time(10, 30), time(11), time(11, 45), time(13), time(15), time(18), time(21), time(23))
        )
        for slot in times:
            candidate = datetime.combine(day, slot, tzinfo=start.tzinfo or timezone.utc)
            if start < candidate < end - timedelta(minutes=5):
                slots.append(candidate)
        day += timedelta(days=1)

    if len(slots) < count:
        interval = end - start
        for offset in range(1, count + 1):
            candidate = (start + interval * (offset / (count + 1))).replace(microsecond=0)
            if start < candidate < end - timedelta(minutes=5) and candidate not in slots:
                slots.append(candidate)
            if len(slots) >= count:
                break

    slots = sorted(set(slots))
    if not slots:
        return [(start + (end - start) / 2).replace(microsecond=0)]

    preferred = [
        slot for slot in slots
        if (slot.weekday() < 5 and (slot.hour, slot.minute) in {(8, 0), (17, 30)})
        or (slot.weekday() >= 5 and (slot.hour, slot.minute) in {(10, 30), (11, 45)})
    ][:count]
    remaining = [slot for slot in slots if slot not in preferred]
    selected = preferred + rng.sample(remaining, min(count - len(preferred), len(remaining)))
    return sorted(slot.replace(microsecond=0) for slot in selected)


def _demo_distances(
    total_km: float,
    count: int,
    rng: random.Random,
    *,
    short_errands: bool,
) -> list[float]:
    remaining = round(total_km * 10)
    count = min(count, remaining)
    distances: list[float] = []
    weights = [rng.uniform(0.9, 1.1) for _ in range(count)]
    for index in range(count - 1):
        reserve = count - index - 1
        max_units = remaining - reserve
        if short_errands:
            units = min(round(rng.uniform(2, 4) * 10), max_units)
        else:
            units = round(remaining * weights[index] / sum(weights[index:]))
            units = max(1, min(units, max_units))
        distances.append(units / 10)
        remaining -= units
    distances.append(remaining / 10)
    return distances


def _seed_demo_trips(
    store: Store,
    vehicle: Vehicle,
    readings: list[SensorReading],
    rng: random.Random,
) -> None:
    ev = vehicle.powertrain is Powertrain.EV
    level_attr = "hv_battery_soc_pct" if ev else "fuel_level_pct"
    for interval_index, (previous, reading) in enumerate(zip(readings, readings[1:], strict=False)):
        total_distance = reading.odometer_km - previous.odometer_km
        if total_distance < 0.15:
            continue
        trip_count = min(3, round(total_distance * 10))
        starts = _demo_trip_starts(previous.timestamp, reading.timestamp, rng, trip_count)
        distances = _demo_distances(
            total_distance,
            len(starts),
            rng,
            short_errands=not ev,
        )
        previous_level = getattr(previous, level_attr)
        current_level = getattr(reading, level_attr)
        previous_level = round(previous_level, 1) if previous_level is not None else None
        current_level = round(current_level, 1) if current_level is not None else None
        weights = [distance * (1.12 if distance < 5 else 1) for distance in distances]
        total_weight = sum(weights)
        level_changes = [
            (current_level - previous_level) * weight / total_weight
            if previous_level is not None and current_level is not None else None
            for weight in weights
        ]
        odometer = round(previous.odometer_km, 1)
        level = previous_level
        for index, (trip_start, distance) in enumerate(zip(starts, distances, strict=False)):
            motorway_minimum = 5 if ev else 8
            motorway = (
                index == len(distances) - 1
                and distance >= motorway_minimum
                and (not ev or interval_index % 3 != 0 or distance >= 12)
            )
            average_speed = rng.uniform(75, 100) if motorway else rng.uniform(25, 65)
            max_speed = rng.uniform(125, 135) if motorway else min(
                115, average_speed * rng.uniform(1.05, 1.35),
            )
            next_start = starts[index + 1] if index + 1 < len(starts) else reading.timestamp
            available = max(300, (next_start - trip_start).total_seconds() - 60)
            duration = round(min(available, max(300, distance / average_speed * 3600)))
            end_level = (
                round(level + level_changes[index], 1)
                if level is not None and level_changes[index] is not None else None
            )
            end_odometer = (
                round(reading.odometer_km, 1)
                if index == len(starts) - 1
                else round(odometer + distance, 1)
            )
            store.add_trip(Trip(
                vin=vehicle.vin,
                started_at=trip_start.replace(microsecond=0),
                ended_at=(trip_start + timedelta(seconds=duration)).replace(microsecond=0),
                start_odometer_km=odometer,
                end_odometer_km=end_odometer,
                start_level_pct=level,
                end_level_pct=current_level if index == len(starts) - 1 else end_level,
                max_speed_kmh=round(max_speed, 1),
                samples=2,
            ))
            odometer = end_odometer
            level = end_level


def seed_vehicle(vd: VitaDrive, vehicle: Vehicle, owner_id: str = LOCAL_OWNER, days: int = 180,
                 per_day: int = 2, seed: int = 7, start_odometer_km: float = 30_000) -> int:
    """Register ``vehicle`` and fill it with simulated history. Returns the number of readings."""
    ev = vehicle.powertrain is Powertrain.EV
    simulation_vehicle = (
        vehicle.model_copy(update={"rated_consumption": (vehicle.rated_consumption or 0) * 1.25})
        if ev else vehicle
    )
    sim = VehicleSimulator(simulation_vehicle, start_odometer_km=start_odometer_km, seed=seed)
    count, prev, rng = 0, None, random.Random(seed)
    level_attr, capacity = (("hv_battery_soc_pct", vehicle.battery_capacity_kwh) if ev
                            else ("fuel_level_pct", vehicle.tank_capacity_l))
    stations = _CHARGERS if ev else _STATIONS
    trip_days = min(max(days, 0), 56)
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
