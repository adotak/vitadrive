"""Threshold rules for every monitored metric.

Each rule defines warning and critical limits. ``low`` limits trigger when the value
drops at or below the limit, ``high`` limits when it rises at or above it.
Limits are conservative defaults drawn from common OEM guidance; override per
vehicle by passing a custom rule set to :class:`vitadrive.monitor.HealthMonitor`.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .models import Powertrain


@dataclass(frozen=True)
class ThresholdRule:
    metric: str
    label: str
    unit: str
    warn_low: Optional[float] = None
    crit_low: Optional[float] = None
    warn_high: Optional[float] = None
    crit_high: Optional[float] = None
    applies_to: tuple[Powertrain, ...] = (Powertrain.ICE, Powertrain.HYBRID, Powertrain.EV)
    advice: str = ""


_ALL = (Powertrain.ICE, Powertrain.HYBRID, Powertrain.EV)
_COMBUSTION = (Powertrain.ICE, Powertrain.HYBRID)
_ELECTRIC = (Powertrain.HYBRID, Powertrain.EV)

_TIRE = dict(unit="kPa", warn_low=210, crit_low=180, warn_high=280, crit_high=310,
             advice="Adjust tire pressure to the value on the door-jamb placard; inspect for punctures.")

DEFAULT_RULES: tuple[ThresholdRule, ...] = (
    ThresholdRule("engine_oil_level_pct", "Engine oil level", "%", warn_low=40, crit_low=20,
                  applies_to=_COMBUSTION, advice="Top up engine oil; check for leaks."),
    ThresholdRule("engine_oil_life_pct", "Engine oil life", "%", warn_low=15, crit_low=5,
                  applies_to=_COMBUSTION, advice="Schedule an oil and filter change."),
    ThresholdRule("engine_oil_pressure_kpa", "Engine oil pressure", "kPa", warn_low=100, crit_low=70,
                  applies_to=_COMBUSTION, advice="Stop the engine safely; low oil pressure can destroy the engine."),
    ThresholdRule("coolant_temp_c", "Coolant temperature", "°C", warn_high=105, crit_high=115,
                  applies_to=_COMBUSTION, advice="Pull over and let the engine cool; check coolant and fan."),
    ThresholdRule("coolant_level_pct", "Coolant level", "%", warn_low=40, crit_low=20,
                  advice="Top up coolant when cold; inspect hoses and radiator."),
    ThresholdRule("brake_fluid_level_pct", "Brake fluid level", "%", warn_low=50, crit_low=30,
                  advice="Brake fluid low - inspect pads and lines immediately."),
    ThresholdRule("brake_pad_front_mm", "Front brake pads", "mm", warn_low=4, crit_low=2.5,
                  advice="Replace front brake pads."),
    ThresholdRule("brake_pad_rear_mm", "Rear brake pads", "mm", warn_low=4, crit_low=2.5,
                  advice="Replace rear brake pads."),
    ThresholdRule("transmission_fluid_temp_c", "Transmission fluid temp", "°C", warn_high=110, crit_high=125,
                  applies_to=_COMBUSTION, advice="Reduce load; have the transmission cooler checked."),
    ThresholdRule("washer_fluid_level_pct", "Washer fluid", "%", warn_low=20, crit_low=5,
                  advice="Refill windshield washer fluid."),
    ThresholdRule("tire_pressure_fl_kpa", "Tire pressure (front-left)", **_TIRE),
    ThresholdRule("tire_pressure_fr_kpa", "Tire pressure (front-right)", **_TIRE),
    ThresholdRule("tire_pressure_rl_kpa", "Tire pressure (rear-left)", **_TIRE),
    ThresholdRule("tire_pressure_rr_kpa", "Tire pressure (rear-right)", **_TIRE),
    ThresholdRule("tire_tread_mm", "Tire tread depth", "mm", warn_low=3, crit_low=1.6,
                  advice="Replace tires; 1.6 mm is the legal minimum in most regions."),
    ThresholdRule("battery_12v_voltage", "12V battery", "V", warn_low=12.2, crit_low=11.8,
                  warn_high=14.8, crit_high=15.2, advice="Test the 12V battery and charging system."),
    ThresholdRule("hv_battery_soc_pct", "Traction battery charge", "%", warn_low=20, crit_low=10,
                  applies_to=_ELECTRIC, advice="Charge soon."),
    ThresholdRule("hv_battery_soh_pct", "Traction battery health", "%", warn_low=80, crit_low=70,
                  applies_to=_ELECTRIC, advice="Battery degradation detected; book a diagnostic / warranty check."),
    ThresholdRule("hv_battery_temp_c", "Traction battery temp", "°C", warn_low=-10, crit_low=-20,
                  warn_high=45, crit_high=55, applies_to=_ELECTRIC,
                  advice="Battery outside optimal temperature; limit fast charging and heavy load."),
    ThresholdRule("fuel_level_pct", "Fuel level", "%", warn_low=15, crit_low=7,
                  applies_to=_COMBUSTION, advice="Refuel soon."),
)


def rules_for(powertrain: Powertrain, rules: tuple[ThresholdRule, ...] = DEFAULT_RULES) -> list[ThresholdRule]:
    return [r for r in rules if powertrain in r.applies_to]
