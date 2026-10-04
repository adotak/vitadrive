"""Realistic telemetry simulator for demos, testing and development without a car."""
from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone
from typing import Iterator, Optional

from ..models import Powertrain, SensorReading, Vehicle


class VehicleSimulator:
    """Simulates daily driving with gradual wear, fuel/charge use and occasional faults."""

    def __init__(self, vehicle: Vehicle, start_odometer_km: float = 25_000, seed: Optional[int] = None,
                 daily_km: float = 45, fault_rate: float = 0.02):
        self.vehicle = vehicle
        self.rng = random.Random(seed)
        self.daily_km = daily_km
        self.fault_rate = fault_rate
        self.odometer = start_odometer_km
        ev = vehicle.powertrain is Powertrain.EV
        self.state = {
            "engine_oil_level_pct": None if ev else 95.0,
            "engine_oil_life_pct": None if ev else 70.0,
            "coolant_level_pct": 95.0,
            "brake_fluid_level_pct": 95.0,
            "brake_pad_front_mm": 8.0,
            "brake_pad_rear_mm": 8.5,
            "washer_fluid_level_pct": 90.0,
            "tire_tread_mm": 7.0,
            "tires": [240.0, 240.0, 240.0, 240.0],
            "battery_12v_voltage": 12.7,
            "fuel_level_pct": None if ev else 80.0,
            "hv_battery_soc_pct": 85.0 if vehicle.powertrain is not Powertrain.ICE else None,
            "hv_battery_soh_pct": 96.0 if vehicle.powertrain is not Powertrain.ICE else None,
        }

    def _drive(self, km: float) -> None:
        s, r = self.state, self.rng
        self.odometer += km
        if s["engine_oil_level_pct"] is not None:
            s["engine_oil_level_pct"] = max(0, s["engine_oil_level_pct"] - km * 0.004)
            s["engine_oil_life_pct"] = max(0, s["engine_oil_life_pct"] - km * 0.01)
        s["coolant_level_pct"] = max(0, s["coolant_level_pct"] - km * 0.0005)
        s["brake_fluid_level_pct"] = max(0, s["brake_fluid_level_pct"] - km * 0.0003)
        s["brake_pad_front_mm"] = max(0, s["brake_pad_front_mm"] - km * 0.00012)
        s["brake_pad_rear_mm"] = max(0, s["brake_pad_rear_mm"] - km * 0.00008)
        s["washer_fluid_level_pct"] = max(0, s["washer_fluid_level_pct"] - km * r.uniform(0, 0.02))
        s["tire_tread_mm"] = max(0, s["tire_tread_mm"] - km * 0.00009)
        s["tires"] = [max(0, p - km * r.uniform(0, 0.006)) for p in s["tires"]]
        s["battery_12v_voltage"] = max(11.0, s["battery_12v_voltage"] - km * 0.000005)
        if s["fuel_level_pct"] is not None:
            cap = self.vehicle.tank_capacity_l or 50
            rate = self.vehicle.rated_consumption or 7.5
            s["fuel_level_pct"] -= km * rate / 100 / cap * 100 * r.uniform(0.9, 1.15)
            if s["fuel_level_pct"] < 12 and r.random() < 0.8:
                s["fuel_level_pct"] = r.uniform(85, 100)
            s["fuel_level_pct"] = max(0, s["fuel_level_pct"])
        if s["hv_battery_soc_pct"] is not None and self.vehicle.powertrain is Powertrain.EV:
            cap = self.vehicle.battery_capacity_kwh or 60
            rate = self.vehicle.rated_consumption or 16
            s["hv_battery_soc_pct"] -= km * rate / 100 / cap * 100 * r.uniform(0.9, 1.2)
            if s["hv_battery_soc_pct"] < 25 and r.random() < 0.85:
                s["hv_battery_soc_pct"] = r.uniform(80, 90)
            s["hv_battery_soc_pct"] = max(0, s["hv_battery_soc_pct"])
            s["hv_battery_soh_pct"] = max(0, s["hv_battery_soh_pct"] - km * 0.00004)

    def reading(self, timestamp: datetime) -> SensorReading:
        s, r = self.state, self.rng
        ice = self.vehicle.powertrain is not Powertrain.EV
        dtcs = []
        if r.random() < self.fault_rate:
            dtcs.append(r.choice(["P0420", "P0171", "P0300", "P0128", "C0035", "U0100"] if ice
                                 else ["P0A80", "U0100", "C0035", "B1000"]))
        fl, fr, rl, rr = (round(p + r.uniform(-2, 2), 1) for p in s["tires"])
        rnd = lambda v, d=1: None if v is None else round(v, d)  # noqa: E731
        return SensorReading(
            vin=self.vehicle.vin,
            timestamp=timestamp,
            odometer_km=round(self.odometer, 1),
            engine_oil_level_pct=rnd(s["engine_oil_level_pct"]),
            engine_oil_life_pct=rnd(s["engine_oil_life_pct"]),
            engine_oil_pressure_kpa=round(r.uniform(250, 400), 0) if ice else None,
            coolant_temp_c=round(r.gauss(92, 4), 1) if ice else None,
            coolant_level_pct=rnd(s["coolant_level_pct"]),
            brake_fluid_level_pct=rnd(s["brake_fluid_level_pct"]),
            brake_pad_front_mm=rnd(s["brake_pad_front_mm"], 2),
            brake_pad_rear_mm=rnd(s["brake_pad_rear_mm"], 2),
            transmission_fluid_temp_c=round(r.gauss(80, 6), 1) if ice else None,
            washer_fluid_level_pct=rnd(s["washer_fluid_level_pct"]),
            tire_pressure_fl_kpa=fl, tire_pressure_fr_kpa=fr, tire_pressure_rl_kpa=rl, tire_pressure_rr_kpa=rr,
            tire_tread_mm=rnd(s["tire_tread_mm"], 2),
            battery_12v_voltage=round(s["battery_12v_voltage"] + r.uniform(-0.05, 0.05), 2),
            hv_battery_soc_pct=rnd(s["hv_battery_soc_pct"]),
            hv_battery_soh_pct=rnd(s["hv_battery_soh_pct"]),
            hv_battery_temp_c=round(r.gauss(28, 4), 1) if s["hv_battery_soc_pct"] is not None else None,
            fuel_level_pct=rnd(s["fuel_level_pct"]),
            dtc_codes=dtcs,
        )

    def history(self, days: int, end: Optional[datetime] = None, per_day: int = 2) -> Iterator[SensorReading]:
        """Yield ``per_day`` readings per day for the last ``days`` days."""
        end = end or datetime.now(timezone.utc)
        start = end - timedelta(days=days)
        for i in range(days * per_day):
            self._drive(self.daily_km / per_day * self.rng.uniform(0.3, 1.7))
            yield self.reading(start + timedelta(days=(i + 1) / per_day))

    def inflate_tires(self, kpa: float = 240.0) -> None:
        self.state["tires"] = [kpa] * 4
