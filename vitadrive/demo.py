"""Demo data: simulated vehicles with driving history and a few past services."""
from __future__ import annotations

import random

from .adapters.simulator import VehicleSimulator
from .models import FuelLog, Powertrain, ServiceRecord, Vehicle
from .service import VitaDrive
from .storage import LOCAL_OWNER

DEMO_VEHICLES = [
    Vehicle(vin="VDDEMOICE0000001", make="Toyota", model="Corolla", year=2023, powertrain=Powertrain.ICE,
            tank_capacity_l=50, rated_consumption=6.5),
    Vehicle(vin="VDDEMOEV00000002", make="Tesla", model="Model 3", year=2024, powertrain=Powertrain.EV,
            battery_capacity_kwh=60, rated_consumption=15),
]

_STATIONS = ["Shell", "BP", "Esso", "Texaco"]
_CHARGERS = ["Home charger", "Home charger", "Tesla Supercharger", "Ionity"]


def seed_vehicle(vd: VitaDrive, vehicle: Vehicle, owner_id: str = LOCAL_OWNER, days: int = 180,
                 per_day: int = 2, seed: int = 7, start_odometer_km: float = 30_000) -> int:
    """Register ``vehicle`` and fill it with simulated history. Returns the number of readings."""
    sim = VehicleSimulator(vehicle, start_odometer_km=start_odometer_km, seed=seed)
    count, prev, rng = 0, None, random.Random(seed)
    ev = vehicle.powertrain is Powertrain.EV
    level_attr, capacity = (("hv_battery_soc_pct", vehicle.battery_capacity_kwh) if ev
                            else ("fuel_level_pct", vehicle.tank_capacity_l))
    stations = _CHARGERS if ev else _STATIONS
    with vd.store.transaction():
        vd.store.upsert_vehicle(vehicle, owner_id=owner_id)
        for reading in sim.history(days=days, per_day=per_day):
            vd.ingest(reading)
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
    return count
