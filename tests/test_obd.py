from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest

from vitadrive.adapters.obd import OBDAdapter


def test_obd_adapter_reads_supported_metrics_without_hardware(monkeypatch) -> None:
    values = {
        "odometer": 42_100.5,
        "coolant": 91.5,
        "fuel": 62.0,
        "voltage": 14.1,
        "hybrid": None,
        "dtcs": [("P0420", "Catalyst efficiency below threshold")],
    }
    commands = SimpleNamespace(
        ODOMETER="odometer",
        COOLANT_TEMP="coolant",
        FUEL_LEVEL="fuel",
        CONTROL_MODULE_VOLTAGE="voltage",
        HYBRID_BATTERY_REMAINING="hybrid",
        GET_DTC="dtcs",
    )

    class FakeConnection:
        def is_connected(self) -> bool:
            return True

        def supports(self, command: str) -> bool:
            return command in values

        def query(self, command: str) -> SimpleNamespace:
            value = values[command]
            response_value = SimpleNamespace(magnitude=value) if isinstance(value, (int, float)) else value
            return SimpleNamespace(is_null=lambda: value is None, value=response_value)

    fake_obd = SimpleNamespace(commands=commands, OBD=lambda _port, **_kwargs: FakeConnection())
    monkeypatch.setitem(sys.modules, "obd", fake_obd)

    reading = OBDAdapter("VDDEMOICE0000001").read()

    assert reading.odometer_km == 42_100.5
    assert reading.coolant_temp_c == 91.5
    assert reading.fuel_level_pct == 62.0
    assert reading.battery_12v_voltage == 14.1
    assert reading.hv_battery_soc_pct is None
    assert reading.dtc_codes == ["P0420"]


def test_obd_adapter_requires_optional_extra(monkeypatch) -> None:
    monkeypatch.setitem(sys.modules, "obd", None)

    with pytest.raises(RuntimeError, match="Install the OBD extra"):
        OBDAdapter("VDDEMOICE0000001")


def test_obd_adapter_rejects_disconnected_dongle(monkeypatch) -> None:
    fake_obd = SimpleNamespace(
        commands=SimpleNamespace(),
        OBD=lambda _port, **_kwargs: SimpleNamespace(is_connected=lambda: False),
    )
    monkeypatch.setitem(sys.modules, "obd", fake_obd)

    adapter = OBDAdapter("VDDEMOICE0000001")
    assert not adapter.is_connected()
    with pytest.raises(RuntimeError, match="disconnected"):
        adapter.read()


def test_obd_adapter_reconnects_with_connection_options(monkeypatch) -> None:
    connections = [
        SimpleNamespace(is_connected=lambda: False),
        SimpleNamespace(is_connected=lambda: True),
    ]
    calls = []

    def connect(port, **options):
        calls.append((port, options))
        return connections.pop(0)

    fake_obd = SimpleNamespace(commands=SimpleNamespace(), OBD=connect)
    monkeypatch.setitem(sys.modules, "obd", fake_obd)

    adapter = OBDAdapter("VDDEMOICE0000001", port="/dev/rfcomm0", baudrate=38400, timeout=3, fast=True)

    assert not adapter.is_connected()
    assert adapter.reconnect()
    assert calls == [
        ("/dev/rfcomm0", {"baudrate": 38400, "timeout": 3, "fast": True}),
        ("/dev/rfcomm0", {"baudrate": 38400, "timeout": 3, "fast": True}),
    ]


def test_obd_adapter_reconnects_when_closing_old_connection_fails(monkeypatch) -> None:
    class BrokenConnection:
        def is_connected(self):
            return False

        def close(self):
            raise OSError("adapter disconnected")

    connections = [BrokenConnection(), SimpleNamespace(is_connected=lambda: True)]
    fake_obd = SimpleNamespace(commands=SimpleNamespace(), OBD=lambda _port, **_kwargs: connections.pop(0))
    monkeypatch.setitem(sys.modules, "obd", fake_obd)

    adapter = OBDAdapter("VDDEMOICE0000001")

    assert adapter.reconnect()
