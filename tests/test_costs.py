from datetime import date

import pytest
from fastapi.testclient import TestClient

from vitadrive.api import create_app
from vitadrive.costs import summarize
from vitadrive.models import FuelLog, Powertrain, Vehicle

CAR = {"vin": "VDCOST0000000001", "make": "Toyota", "model": "Corolla", "year": 2023, "powertrain": "ice",
       "tank_capacity_l": 50}
BASE = f"/api/vehicles/{CAR['vin']}"
ALICE, BOB = {"Authorization": "alice"}, {"Authorization": "bob"}


class HeaderAuth:
    enabled, publishable_key, frontend_api = True, None, None

    def user_id(self, authorization):
        return authorization or "anon"


@pytest.fixture
def client(tmp_path):
    c = TestClient(create_app(str(tmp_path / "costs.db"), auth=HeaderAuth()))
    c.put(BASE, json=CAR, headers=ALICE).raise_for_status()
    return c


def log(day, km, litres, cost, month=9):
    return FuelLog(vin=CAR["vin"], filled_on=date(2026, month, day), odometer_km=km, amount=litres, cost=cost)


def test_fill_to_fill_cost_and_consumption():
    car = Vehicle(**CAR)
    logs = [log(20, 2000, 36, 64.8, 10), log(1, 1000, 40, 68), log(15, 1500, 35, 59.5)]
    s = summarize(car, logs, today=date(2026, 10, 25))
    assert s.unit == "L" and s.fills == 3 and s.total_cost == 192.3
    assert s.consumption_per_100km == 7.1  # (35 + 36) L over 1000 km; the first fill only sets the baseline
    assert s.cost_per_km == pytest.approx(0.1243)
    assert s.avg_price_per_unit == pytest.approx(192.3 / 111, abs=1e-3)
    assert [m.month for m in s.months] == ["2026-05", "2026-06", "2026-07", "2026-08", "2026-09", "2026-10"]
    assert s.this_month_cost == 64.8 and s.months[-2].cost == 127.5


def test_one_fill_has_no_per_km_figures_and_ev_uses_kwh():
    ev = Vehicle(vin="VDEV", make="Tesla", model="Model 3", year=2024, powertrain=Powertrain.EV)
    s = summarize(ev, [log(1, 1000, 40, 10)], today=date(2026, 1, 3))
    assert s.unit == "kWh" and s.cost_per_km is None and s.consumption_per_100km is None
    assert s.months[0].month == "2025-08" and s.this_month_cost == 0
    assert summarize(ev, [], today=date(2026, 1, 3)).fills == 0


def test_fuel_log_api_is_owner_scoped(client):
    body = {"filled_on": "2026-10-01", "odometer_km": 1000, "amount": 40, "cost": 68, "station": "Shell"}
    created = client.post(f"{BASE}/fuel", json=body, headers=ALICE).json()
    second = {**body, "filled_on": "2026-10-10", "odometer_km": 1600, "amount": 42, "cost": 71.4}
    client.post(f"{BASE}/fuel", json=second, headers=ALICE).raise_for_status()
    assert [x["odometer_km"] for x in client.get(f"{BASE}/fuel", headers=ALICE).json()] == [1600, 1000]
    s = client.get(f"{BASE}/costs", headers=ALICE).json()
    assert s["fills"] == 2 and s["cost_per_km"] == pytest.approx(0.119) and s["consumption_per_100km"] == 7.0

    assert client.get(f"{BASE}/fuel", headers=BOB).status_code == 404
    assert client.get(f"{BASE}/costs", headers=BOB).status_code == 404
    assert client.delete(f"{BASE}/fuel/{created['id']}", headers=BOB).status_code == 404
    assert client.delete(f"{BASE}/fuel/{created['id']}", headers=ALICE).status_code == 204
    assert client.delete(f"{BASE}/fuel/{created['id']}", headers=ALICE).status_code == 404
    assert len(client.get(f"{BASE}/fuel", headers=ALICE).json()) == 1


@pytest.mark.parametrize("bad", [{"amount": 0}, {"cost": -1}, {"odometer_km": -5}, {"station": "x" * 121}])
def test_fuel_log_validation(client, bad):
    body = {"filled_on": "2026-10-01", "odometer_km": 1000, "amount": 40, "cost": 68, **bad}
    assert client.post(f"{BASE}/fuel", json=body, headers=ALICE).status_code == 422


def test_deleting_vehicle_removes_its_fuel_logs(client):
    body = {"filled_on": "2026-10-01", "odometer_km": 1, "amount": 4, "cost": 6}
    client.post(f"{BASE}/fuel", json=body, headers=ALICE)
    client.delete(BASE, headers=ALICE).raise_for_status()
    client.put(BASE, json=CAR, headers=ALICE).raise_for_status()
    assert client.get(f"{BASE}/fuel", headers=ALICE).json() == []


def test_demo_cars_come_with_fill_ups_and_charges(client):
    cars = client.post("/api/demo", headers=ALICE).json()
    for car in cars:
        s = client.get(f"/api/vehicles/{car['vin']}/costs", headers=ALICE).json()
        assert s["unit"] == ("kWh" if car["powertrain"] == "ev" else "L")
        assert s["fills"] >= 3 and s["cost_per_km"] and 2 < s["consumption_per_100km"] < 30
