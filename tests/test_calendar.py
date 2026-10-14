from datetime import date, datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from vitadrive import api as api_module
from vitadrive.api import create_app
from vitadrive.auth import AuthError
from vitadrive.calendar import maintenance_calendar
from vitadrive.models import MaintenanceForecast, Vehicle

NOW = datetime(2026, 10, 14, 12, tzinfo=timezone.utc)
CAR = Vehicle(vin="VDCALENDAR0000001", make="Toyota", model="Corolla", year=2023)
BASE = f"/api/vehicles/{CAR.vin}"
ALICE, BOB = {"Authorization": "alice"}, {"Authorization": "bob"}


def forecast(**changes):
    return MaintenanceForecast(**{
        "item": "oil_change", "label": "Engine oil & filter", "last_service_date": None,
        "last_service_km": None, "due_date": date(2026, 11, 14), "due_km": 20000,
        "days_remaining": 31, "km_remaining": 2000, "status": "ok", "reason": "every 10,000 km",
        **changes,
    })


def unfold(calendar):
    return calendar.replace("\r\n ", "")


def properties(calendar, name):
    return [line.split(":", 1)[1] for line in unfold(calendar).split("\r\n") if line.startswith(name + ":")]


def test_all_day_dates_reminder_and_forecast_context():
    calendar = maintenance_calendar(CAR, [forecast()], now=NOW)
    assert calendar.startswith("BEGIN:VCALENDAR\r\nVERSION:2.0\r\n")
    assert calendar.endswith("END:VCALENDAR\r\n")
    assert "\n" not in calendar.replace("\r\n", "")
    assert "DTSTAMP:20261014T120000Z\r\n" in calendar
    assert "DTSTART;VALUE=DATE:20261114\r\nDTEND;VALUE=DATE:20261115" in calendar
    assert "ACTION:DISPLAY\r\nTRIGGER:-P1D" in calendar
    assert "TRANSP:TRANSPARENT" in calendar
    description = properties(calendar, "DESCRIPTION")[0]
    assert "every 10\\,000 km" in description
    assert "Due odometer: 20\\,000 km" in description
    assert "snapshot" in description and "not a live subscription" in description
    assert CAR.vin not in calendar


def test_overdue_events_appear_today_without_past_alarms_and_keep_original_date():
    items = [forecast(), forecast(item="tires", due_date=date(2026, 9, 1), due_km=None),
             forecast(item="coolant", due_date=NOW.date())]
    calendar = maintenance_calendar(CAR, items, now=NOW)
    assert calendar.count("DTSTART;VALUE=DATE:20261014") == 2
    assert calendar.count("BEGIN:VALARM") == 1
    assert properties(calendar, "SUMMARY")[0].startswith("OVERDUE: ")
    assert "Predicted due date: 2026-09-01" in unfold(calendar)
    assert "DTSTART;VALUE=DATE:20260901" not in calendar


def test_calendar_handles_year_boundaries_and_stable_private_identifiers():
    item = forecast(due_date=date(2026, 12, 31))
    first = maintenance_calendar(CAR, [item], now=NOW)
    second = maintenance_calendar(CAR, [forecast(due_date=date(2027, 2, 1))], now=NOW + timedelta(days=1))
    assert properties(first, "UID") == properties(second, "UID")
    assert "DTEND;VALUE=DATE:20270101" in first
    other_car = CAR.model_copy(update={"vin": "OTHER"})
    assert properties(first, "UID") != properties(maintenance_calendar(other_car, [item], now=NOW), "UID")
    assert properties(first, "UID") != properties(
        maintenance_calendar(CAR, [forecast(item="tires")], now=NOW), "UID")


def test_mileage_overdue_is_labeled_even_when_projected_date_is_today():
    item = forecast(due_date=NOW.date(), km_remaining=-100, status="critical")
    calendar = maintenance_calendar(CAR, [item], now=NOW)
    assert properties(calendar, "SUMMARY")[0].startswith("OVERDUE: ")
    assert "arrange service as soon as possible" in unfold(calendar)
    assert "DTSTART;VALUE=DATE:20261014" in calendar
    assert "BEGIN:VALARM" not in calendar


@pytest.mark.parametrize("offset", [-7, 14])
def test_calendar_keeps_local_day_but_uses_utc_stamp(offset):
    instant = NOW.replace(hour=2 if offset < 0 else 12)
    local_now = instant.astimezone(timezone(timedelta(hours=offset)))
    item = forecast(due_date=date(2026, 9, 1))
    calendar = maintenance_calendar(CAR, [item], now=local_now)
    assert f"DTSTART;VALUE=DATE:{local_now:%Y%m%d}" in calendar
    assert f"DTSTAMP:{instant:%Y%m%dT%H%M%SZ}" in calendar


def test_utf8_folding_and_text_escaping_prevent_content_injection():
    model = "É🚗" * 50 + "\\,;\r\nBEGIN:VEVENT\rATTENDEE:someone@example.com\x00"
    calendar = maintenance_calendar(CAR.model_copy(update={"model": model}), [forecast()], now=NOW)
    assert all(len(line.encode("utf-8")) <= 75 for line in calendar.split("\r\n"))
    assert "\r\n " in calendar
    assert properties(calendar, "SUMMARY")[0].endswith(
        "É🚗" * 50 + "\\\\\\,\\;\\nBEGIN:VEVENT\\nATTENDEE:someone@example.com (…0001)")
    assert unfold(calendar).split("\r\n").count("BEGIN:VEVENT") == 1
    assert "\x00" not in calendar


class HeaderAuth:
    enabled, publishable_key, frontend_api = True, None, None

    def user_id(self, authorization):
        if not authorization:
            raise AuthError("Sign in required")
        return authorization


@pytest.fixture
def client(tmp_path):
    app = create_app(str(tmp_path / "calendar.db"), auth=HeaderAuth())
    with TestClient(app) as client:
        client.put(BASE, json=CAR.model_dump(mode="json"), headers=ALICE).raise_for_status()
        yield client


def add_reading(client, day=None):
    client.post(f"{BASE}/readings", headers=ALICE, json={
        "vin": CAR.vin, "timestamp": f"{day or date.today()}T12:00:00Z", "odometer_km": 10000,
    }).raise_for_status()


def test_download_headers_owner_isolation_and_missing_readings(client):
    path = f"{BASE}/maintenance/calendar.ics"
    assert client.get(path).status_code == 401
    assert client.get(path, headers=BOB).status_code == 404
    assert client.get("/api/vehicles/UNKNOWN/maintenance/calendar.ics", headers=ALICE).status_code == 404
    assert client.get(path, headers=ALICE).status_code == 409
    add_reading(client)
    response = client.get(path, headers=ALICE)
    assert response.status_code == 200
    assert response.headers["content-type"] == "text/calendar; charset=utf-8"
    assert response.headers["content-disposition"] == 'attachment; filename="vitadrive-maintenance.ics"'
    assert response.headers["cache-control"] == "private, no-store"
    forecasts = client.get(f"{BASE}/maintenance", headers=ALICE).json()
    assert response.text.count("BEGIN:VEVENT") == len(forecasts)
    for item in forecasts:
        assert f"Predicted due date: {item['due_date']}" in unfold(response.text)
    assert client.get(path, headers=BOB).status_code == 404
    key = client.post(f"{BASE}/api-key", headers=ALICE).json()["api_key"]
    assert client.get(path, headers={"X-API-Key": key}).status_code == 401


def test_export_reflects_logged_service_and_ev_schedule(client):
    add_reading(client, date.today() - timedelta(days=400))
    path = f"{BASE}/maintenance/calendar.ics"
    before = client.get(path, headers=ALICE).text
    assert "OVERDUE: Engine oil & filter" in unfold(before)
    client.post(f"{BASE}/services", headers=ALICE, json={
        "item": "oil_change", "performed_on": date.today().isoformat(), "odometer_km": 10000,
    }).raise_for_status()
    after = client.get(path, headers=ALICE).text
    assert "OVERDUE: Engine oil & filter" not in unfold(after)
    assert set(properties(before, "UID")) == set(properties(after, "UID"))
    ev = {**CAR.model_dump(mode="json"), "powertrain": "ev"}
    client.put(BASE, json=ev, headers=ALICE).raise_for_status()
    calendar = unfold(client.get(path, headers=ALICE).text)
    assert "Engine oil & filter" not in calendar and "Spark plugs" not in calendar
    assert "Traction battery health check" in calendar


def test_api_uses_one_day_for_forecasting_and_export(client, monkeypatch):
    class FixedDate(date):
        @classmethod
        def today(cls):
            return date(2026, 10, 13)

    monkeypatch.setattr(api_module, "date", FixedDate)
    add_reading(client, date(2025, 9, 1))
    response = client.get(f"{BASE}/maintenance/calendar.ics", headers=ALICE)
    assert response.status_code == 200
    events = unfold(response.text).split("BEGIN:VEVENT")[1:]
    overdue = [event for event in events if "SUMMARY:OVERDUE:" in event]
    assert overdue
    assert all("DTSTART;VALUE=DATE:20261013" in event for event in overdue)
