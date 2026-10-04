from datetime import date, datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from vitadrive.adapters.simulator import VehicleSimulator
from vitadrive.api import create_app
from vitadrive.maintenance import MaintenancePlanner, wear_rate_per_km
from vitadrive.models import Powertrain, SensorReading, ServiceRecord, Severity, Vehicle
from vitadrive.monitor import HealthMonitor
from vitadrive.range_estimator import estimate_range
from vitadrive.service import VitaDrive
from vitadrive.storage import Store

CAR = Vehicle(vin="TESTVIN0000000001", make="Honda", model="Civic", year=2024,
              powertrain=Powertrain.ICE, tank_capacity_l=50, rated_consumption=6.0)
EV = Vehicle(vin="TESTEV00000000002", make="Kia", model="EV6", year=2025,
             powertrain=Powertrain.EV, battery_capacity_kwh=77, rated_consumption=18)
T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)


def reading(**kw):
    kw.setdefault("vin", CAR.vin)
    kw.setdefault("odometer_km", 10_000)
    kw.setdefault("timestamp", T0)
    return SensorReading(**kw)


@pytest.fixture
def vd(tmp_path):
    v = VitaDrive(Store(tmp_path / "t.db"))
    v.store.upsert_vehicle(CAR)
    v.store.upsert_vehicle(EV)
    return v


def test_threshold_severity_levels():
    m = HealthMonitor()
    alerts = {a.metric: a.severity for a in m.evaluate(
        reading(engine_oil_level_pct=35, coolant_temp_c=120, tire_pressure_fl_kpa=230, brake_pad_front_mm=2.0),
        Powertrain.ICE)}
    assert alerts == {"engine_oil_level_pct": Severity.WARNING, "coolant_temp_c": Severity.CRITICAL,
                      "brake_pad_front_mm": Severity.CRITICAL}


def test_powertrain_specific_rules():
    m = HealthMonitor()
    ev_alerts = m.evaluate(reading(vin=EV.vin, fuel_level_pct=1, hv_battery_soc_pct=8), Powertrain.EV)
    assert [a.metric for a in ev_alerts] == ["hv_battery_soc_pct"]


def test_dtc_classification():
    alerts = HealthMonitor().evaluate(reading(dtc_codes=["p0301", "P0420"]), Powertrain.ICE)
    assert {(a.value, a.severity) for a in alerts} == {("P0301", Severity.CRITICAL), ("P0420", Severity.WARNING)}


def test_ingest_dedupes_and_ack_reopens(vd):
    assert len(vd.ingest(reading(fuel_level_pct=10))) == 1
    assert vd.ingest(reading(fuel_level_pct=9, odometer_km=10_010)) == []
    alert = vd.store.alerts(CAR.vin)[0]
    vd.store.acknowledge_alert(alert.id)
    assert len(vd.ingest(reading(fuel_level_pct=8, odometer_km=10_020))) == 1
    assert len(vd.store.readings(CAR.vin)) == 3


def test_alert_auto_resolves_when_condition_clears(vd):
    vd.ingest(reading(fuel_level_pct=10))
    vd.ingest(reading(fuel_level_pct=90, odometer_km=10_010, dtc_codes=["P0420"]))
    assert [a.metric for a in vd.store.alerts(CAR.vin, include_acknowledged=False)] == ["dtc"]


def test_maintenance_interval_and_distance_projection():
    readings = [reading(odometer_km=10_000, timestamp=T0),
                reading(odometer_km=11_000, timestamp=T0 + timedelta(days=20))]  # 50 km/day
    services = [ServiceRecord(vin=CAR.vin, item="oil_change", performed_on=date(2025, 12, 1), odometer_km=9_500)]
    today = date(2026, 1, 21)
    fc = {f.item: f for f in MaintenancePlanner().forecast(Powertrain.ICE, readings, services, today=today)}
    oil = fc["oil_change"]
    assert oil.due_km == 19_500
    assert oil.km_remaining == 8_500
    assert oil.due_date == today + timedelta(days=170)  # 8500 km / 50 km/day, before the 12-month limit
    assert "spark_plugs" in fc and "hv_battery_check" not in fc


def test_wear_rate_prediction():
    readings = [reading(odometer_km=10_000 + i * 1000, timestamp=T0 + timedelta(days=i * 20),
                        brake_pad_front_mm=8 - i * 0.5) for i in range(5)]  # 0.5 mm per 1000 km
    assert wear_rate_per_km(readings, "brake_pad_front_mm") == pytest.approx(-0.0005)
    today = (T0 + timedelta(days=80)).date()
    fc = {f.item: f for f in MaintenancePlanner().forecast(Powertrain.ICE, readings, [], today=today)}
    pads = fc["brake_pads_front"]
    assert pads.km_remaining == pytest.approx(6_000)  # 6 mm -> 3 mm at 0.5 mm/1000 km
    assert "wear rate" in pads.reason


def test_overdue_is_critical():
    readings = [reading(odometer_km=30_000, timestamp=T0)]
    services = [ServiceRecord(vin=CAR.vin, item="tire_rotation", performed_on=date(2025, 1, 1), odometer_km=15_000)]
    fc = {f.item: f for f in MaintenancePlanner().forecast(Powertrain.ICE, readings, services, today=date(2026, 1, 1))}
    assert fc["tire_rotation"].status is Severity.CRITICAL


def test_range_rated_then_observed():
    r = estimate_range(CAR, [reading(fuel_level_pct=50)])
    assert r.range_km == pytest.approx(25 / 6 * 100, rel=1e-3)
    # 10% of 50 L = 5 L over 100 km -> 5 L/100km observed
    history = [reading(fuel_level_pct=60 - i * 2, odometer_km=10_000 + i * 20, timestamp=T0 + timedelta(hours=i))
               for i in range(6)]
    r = estimate_range(CAR, history)
    assert r.consumption_per_100km == pytest.approx(5.0)
    assert "observed" in r.consumption_basis


def test_simulator_end_to_end(vd):
    sim = VehicleSimulator(EV, seed=1)
    for r in sim.history(days=60):
        vd.ingest(r)
    report = vd.report(EV.vin)
    assert report.latest_reading.odometer_km > 25_000
    assert report.range and report.range.energy_source == "battery"
    assert all(m.item != "oil_change" for m in report.maintenance)


def test_api_flow(tmp_path):
    client = TestClient(create_app(str(tmp_path / "api.db")))
    assert client.put(f"/api/vehicles/{CAR.vin}", json=CAR.model_dump(mode="json")).status_code == 200
    r = client.post(f"/api/vehicles/{CAR.vin}/readings",
                    json={"vin": CAR.vin, "odometer_km": 12_000, "engine_oil_life_pct": 3, "fuel_level_pct": 40})
    assert r.status_code == 201 and r.json()[0]["metric"] == "engine_oil_life_pct"
    assert len(client.get(f"/api/vehicles/{CAR.vin}/alerts?active_only=true").json()) == 1
    r = client.post(f"/api/vehicles/{CAR.vin}/services",
                    json={"item": "oil_change", "performed_on": "2026-01-02", "odometer_km": 12_000, "cost": 80})
    assert r.status_code == 201
    assert client.get(f"/api/vehicles/{CAR.vin}/alerts?active_only=true").json() == []
    report = client.get(f"/api/vehicles/{CAR.vin}/report").json()
    assert report["range"]["range_km"] > 0 and report["maintenance"]
    assert client.get("/api/vehicles/UNKNOWN/report").status_code == 404
    assert client.get("/").status_code == 200
