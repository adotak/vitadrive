"""Remaining driving range from current energy level and observed consumption."""
from __future__ import annotations

from typing import Optional

from .models import Powertrain, RangeEstimate, SensorReading, Severity, Vehicle

_DEFAULT_CONSUMPTION = {Powertrain.ICE: 8.0, Powertrain.HYBRID: 5.0, Powertrain.EV: 17.0}
_MIN_KM_FOR_LEARNING = 50


def _observed_consumption(readings: list[SensorReading], level_attr: str, capacity: float) -> Optional[float]:
    """Energy used per 100 km over the recent window, ignoring refuel/recharge events."""
    ordered = sorted((r for r in readings if getattr(r, level_attr) is not None), key=lambda r: r.timestamp)
    used = 0.0
    km = 0.0
    for prev, cur in zip(ordered, ordered[1:], strict=False):
        delta_level = getattr(prev, level_attr) - getattr(cur, level_attr)
        delta_km = cur.odometer_km - prev.odometer_km
        if delta_level <= 0 or delta_km <= 0:
            continue  # refuel, recharge, or stationary
        used += delta_level / 100 * capacity
        km += delta_km
    if km < _MIN_KM_FOR_LEARNING:
        return None
    return used / km * 100


def estimate_range(vehicle: Vehicle, readings: list[SensorReading]) -> Optional[RangeEstimate]:
    if not readings:
        return None
    latest = max(readings, key=lambda r: r.timestamp)
    if vehicle.powertrain is Powertrain.EV:
        level_attr, capacity, source, unit = "hv_battery_soc_pct", vehicle.battery_capacity_kwh, "battery", "kWh"
    else:
        level_attr, capacity, source, unit = "fuel_level_pct", vehicle.tank_capacity_l, "fuel", "L"
    level = getattr(latest, level_attr)
    if level is None or not capacity:
        return None

    observed = _observed_consumption(readings[-500:], level_attr, capacity)
    if observed:
        consumption, basis = observed, f"observed {unit}/100km from recent driving"
    else:
        consumption = vehicle.rated_consumption or _DEFAULT_CONSUMPTION[vehicle.powertrain]
        basis = f"rated {unit}/100km (not enough driving data yet)"

    range_km = level / 100 * capacity / consumption * 100
    status = Severity.CRITICAL if range_km < 30 else Severity.WARNING if range_km < 80 else Severity.OK
    return RangeEstimate(
        energy_source=source,
        level_pct=level,
        consumption_per_100km=round(consumption, 2),
        consumption_basis=basis,
        range_km=round(range_km, 1),
        status=status,
    )
