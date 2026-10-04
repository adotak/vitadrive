from __future__ import annotations

from datetime import date, datetime, timezone
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Powertrain(str, Enum):
    ICE = "ice"
    HYBRID = "hybrid"
    EV = "ev"


class Severity(str, Enum):
    OK = "ok"
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class Vehicle(BaseModel):
    vin: str = Field(min_length=3, max_length=17)
    make: str
    model: str
    year: int
    powertrain: Powertrain = Powertrain.ICE
    tank_capacity_l: Optional[float] = None
    battery_capacity_kwh: Optional[float] = None
    rated_consumption: Optional[float] = Field(
        default=None,
        description="L/100km for ICE/hybrid, kWh/100km for EV. Used until enough trip data exists.",
    )


class SensorReading(BaseModel):
    """A snapshot of vehicle telemetry. Any field may be missing if the car doesn't report it."""

    vin: str
    timestamp: datetime = Field(default_factory=utcnow)
    odometer_km: float

    engine_oil_level_pct: Optional[float] = None
    engine_oil_life_pct: Optional[float] = None
    engine_oil_pressure_kpa: Optional[float] = None
    coolant_temp_c: Optional[float] = None
    coolant_level_pct: Optional[float] = None
    brake_fluid_level_pct: Optional[float] = None
    brake_pad_front_mm: Optional[float] = None
    brake_pad_rear_mm: Optional[float] = None
    transmission_fluid_temp_c: Optional[float] = None
    washer_fluid_level_pct: Optional[float] = None

    tire_pressure_fl_kpa: Optional[float] = None
    tire_pressure_fr_kpa: Optional[float] = None
    tire_pressure_rl_kpa: Optional[float] = None
    tire_pressure_rr_kpa: Optional[float] = None
    tire_tread_mm: Optional[float] = None

    battery_12v_voltage: Optional[float] = None
    hv_battery_soc_pct: Optional[float] = None
    hv_battery_soh_pct: Optional[float] = None
    hv_battery_temp_c: Optional[float] = None

    fuel_level_pct: Optional[float] = None
    dtc_codes: list[str] = Field(default_factory=list)


class Alert(BaseModel):
    id: Optional[int] = None
    vin: str
    timestamp: datetime = Field(default_factory=utcnow)
    metric: str
    label: str
    value: float | str
    severity: Severity
    message: str
    acknowledged: bool = False


class ServiceRecord(BaseModel):
    id: Optional[int] = None
    vin: str
    item: str
    performed_on: date
    odometer_km: float
    cost: Optional[float] = None
    shop: Optional[str] = None
    notes: Optional[str] = None


class MaintenanceForecast(BaseModel):
    item: str
    label: str
    last_service_date: Optional[date]
    last_service_km: Optional[float]
    due_km: Optional[float]
    due_date: date
    days_remaining: int
    km_remaining: Optional[float]
    status: Severity
    reason: str


class RangeEstimate(BaseModel):
    energy_source: str
    level_pct: float
    consumption_per_100km: float
    consumption_basis: str
    range_km: float
    status: Severity
