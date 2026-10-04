"""Demo data: simulated vehicles with driving history and a few past services."""
from __future__ import annotations

from .adapters.simulator import VehicleSimulator
from .models import Powertrain, ServiceRecord, Vehicle
from .service import VitaDrive
from .storage import LOCAL_OWNER

DEMO_VEHICLES = [
    Vehicle(vin="VDDEMOICE0000001", make="Toyota", model="Corolla", year=2023, powertrain=Powertrain.ICE,
            tank_capacity_l=50, rated_consumption=6.5),
    Vehicle(vin="VDDEMOEV00000002", make="Tesla", model="Model 3", year=2024, powertrain=Powertrain.EV,
            battery_capacity_kwh=60, rated_consumption=15),
]


def seed_vehicle(vd: VitaDrive, vehicle: Vehicle, owner_id: str = LOCAL_OWNER, days: int = 180,
                 per_day: int = 2, seed: int = 7, start_odometer_km: float = 30_000) -> int:
    """Register ``vehicle`` and fill it with simulated history. Returns the number of readings."""
    sim = VehicleSimulator(vehicle, start_odometer_km=start_odometer_km, seed=seed)
    count = 0
    with vd.store.transaction():
        vd.store.upsert_vehicle(vehicle, owner_id=owner_id)
        for reading in sim.history(days=days, per_day=per_day):
            vd.ingest(reading)
            count += 1
            if count == max(days * per_day // 9, 1):
                for item in ("tire_rotation", "cabin_filter", "wiper_blades"):
                    vd.store.add_service(ServiceRecord(
                        vin=vehicle.vin, item=item, performed_on=reading.timestamp.date(),
                        odometer_km=reading.odometer_km, cost=45.0, shop="VitaDrive Demo Garage"))
    return count
