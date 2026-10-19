"""Live OBD-II adapter (ELM327 USB/Bluetooth dongles) via the optional ``obd`` package.

Install with ``pip install vitadrive[obd]``. Standard OBD-II exposes a subset of
VitaDrive's metrics (coolant temp, fuel level, control-module voltage, DTCs and,
on 2019+ vehicles, odometer PID 0xA6). Fluid levels, brake pads, TPMS and HV
battery data are manufacturer-specific and are best fed through the REST API
from the vehicle's own telematics/CAN gateway.
"""
from __future__ import annotations

from typing import Any, Optional, cast

from ..models import SensorReading


class OBDAdapter:
    def __init__(
        self,
        vin: str,
        port: Optional[str] = None,
        fallback_odometer_km: Optional[float] = None,
        baudrate: int | None = None,
        timeout: float = 5.0,
        fast: bool = False,
    ) -> None:
        try:
            import obd  # type: ignore[import-not-found]
        except ImportError as exc:
            raise RuntimeError("Install the OBD extra: pip install 'vitadrive[obd]'") from exc
        self._obd = obd
        self.vin = vin
        self.fallback_odometer_km = fallback_odometer_km
        self.port = port
        self.baudrate = baudrate
        self.timeout = timeout
        self.fast = fast
        self.connection: Any | None = None
        try:
            self.reconnect()
        except Exception:
            self.connection = None

    def is_connected(self) -> bool:
        return self.connection is not None and self.connection.is_connected()

    def reconnect(self) -> bool:
        if self.connection is not None:
            close = getattr(self.connection, "close", None)
            if callable(close):
                try:
                    close()
                except Exception:
                    pass
        self.connection = self._obd.OBD(
            self.port,
            baudrate=self.baudrate,
            timeout=self.timeout,
            fast=self.fast,
        )
        return self.is_connected()

    def _query(self, name: str) -> float | list[tuple[str, str]] | None:
        if self.connection is None:
            raise RuntimeError("OBD-II adapter is disconnected")
        cmd = getattr(self._obd.commands, name, None)
        if cmd is None or not self.connection.supports(cmd):
            return None
        response = self.connection.query(cmd)
        if response.is_null():
            return None
        value = response.value
        return float(value.magnitude) if hasattr(value, "magnitude") else cast(float | list[tuple[str, str]], value)

    def read(self) -> SensorReading:
        if not self.is_connected():
            raise RuntimeError("OBD-II adapter is disconnected")
        odometer = cast(float | None, self._query("ODOMETER")) or self.fallback_odometer_km
        if odometer is None:
            raise RuntimeError("Vehicle does not report odometer over OBD-II; pass fallback_odometer_km")
        dtc_response = cast(list[tuple[str, str]], self._query("GET_DTC") or [])
        return SensorReading(
            vin=self.vin,
            odometer_km=odometer,
            coolant_temp_c=cast(float | None, self._query("COOLANT_TEMP")),
            fuel_level_pct=cast(float | None, self._query("FUEL_LEVEL")),
            battery_12v_voltage=cast(float | None, self._query("CONTROL_MODULE_VOLTAGE")),
            hv_battery_soc_pct=cast(float | None, self._query("HYBRID_BATTERY_REMAINING")),
            dtc_codes=[code for code, _desc in dtc_response],
        )
