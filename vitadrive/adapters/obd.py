"""Live OBD-II adapter (ELM327 USB/Bluetooth dongles) via the optional ``obd`` package.

Install with ``pip install vitadrive[obd]``. Standard OBD-II exposes a subset of
VitaDrive's metrics (coolant temp, fuel level, control-module voltage, DTCs and,
on 2019+ vehicles, odometer PID 0xA6). Fluid levels, brake pads, TPMS and HV
battery data are manufacturer-specific and are best fed through the REST API
from the vehicle's own telematics/CAN gateway.
"""
from __future__ import annotations

from typing import Optional

from ..models import SensorReading


class OBDAdapter:
    def __init__(self, vin: str, port: Optional[str] = None, fallback_odometer_km: Optional[float] = None):
        try:
            import obd  # type: ignore
        except ImportError as exc:  # pragma: no cover - optional dependency
            raise RuntimeError("Install the OBD extra: pip install 'vitadrive[obd]'") from exc
        self._obd = obd
        self.vin = vin
        self.fallback_odometer_km = fallback_odometer_km
        self.connection = obd.OBD(port)
        if not self.connection.is_connected():
            raise RuntimeError("Could not connect to an OBD-II adapter")

    def _query(self, name: str) -> Optional[float]:
        cmd = getattr(self._obd.commands, name, None)
        if cmd is None or not self.connection.supports(cmd):
            return None
        response = self.connection.query(cmd)
        if response.is_null():
            return None
        value = response.value
        return float(value.magnitude) if hasattr(value, "magnitude") else value

    def read(self) -> SensorReading:
        odometer = self._query("ODOMETER") or self.fallback_odometer_km
        if odometer is None:
            raise RuntimeError("Vehicle does not report odometer over OBD-II; pass fallback_odometer_km")
        dtc_response = self._query("GET_DTC") or []
        return SensorReading(
            vin=self.vin,
            odometer_km=odometer,
            coolant_temp_c=self._query("COOLANT_TEMP"),
            fuel_level_pct=self._query("FUEL_LEVEL"),
            battery_12v_voltage=self._query("CONTROL_MODULE_VOLTAGE"),
            hv_battery_soc_pct=self._query("HYBRID_BATTERY_REMAINING"),
            dtc_codes=[code for code, _desc in dtc_response],
        )
