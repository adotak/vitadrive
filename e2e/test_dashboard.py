from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import pytest
from playwright.sync_api import Page, expect

DEMO_ICE_VIN = "VDDEMOICE0000001"
DEMO_EV_VIN = "VDDEMOEV00000002"

NAVIGATION = [
    ("overview", "Overview", "Your car, at a glance."),
    ("maintenance", "Maintenance", "Maintenance"),
    ("health", "Health", "Health"),
    ("costs", "Costs", "Costs"),
    ("history", "History", "History"),
    ("trips", "Trips", "Trip history"),
    ("help", "Nearby help", "Nearby help"),
]


@pytest.mark.parametrize(("view", "section", "heading"), NAVIGATION, ids=[item[0] for item in NAVIGATION])
def test_navigation_pages_load(server_url: str, page: Page, view: str, section: str, heading: str) -> None:
    page.goto(f"{server_url}/#{view}")

    expect(page.locator("#currentSection")).to_have_text(section)
    expect(page.locator(f"#{view} h1")).to_have_text(heading)
    expect(page.locator(f"#{view} h1")).to_be_visible()


def test_help_search_renders_map(server_url: str, page: Page) -> None:
    page.goto(f"{server_url}/#help")
    page.get_by_label("or city / postcode").fill("London")
    page.get_by_role("button", name="Search").click()

    expect(page.locator("#nearBody")).to_be_visible()
    expect(page.locator("#nearList .place")).to_contain_text("E2E Garage")
    expect(page.locator("#nearMap.leaflet-container")).to_be_visible()


def test_switching_vehicles_updates_hero(server_url: str, page: Page) -> None:
    page.goto(f"{server_url}/#overview")

    page.get_by_label("Vehicle").select_option(DEMO_EV_VIN)

    expect(page.locator("#carName")).to_have_text("2024 Tesla Model 3")


def test_fuel_entry_persists_and_can_be_deleted(server_url: str, page: Page) -> None:
    page.goto(f"{server_url}/#costs")
    page.get_by_label("Vehicle").select_option(DEMO_ICE_VIN)
    page.locator("#logFuel summary").click()

    odometer = page.locator("#fuelForm [name='odometer_km']")
    expect(odometer).not_to_have_value("")
    station = f"E2E station {uuid4().hex[:8]}"
    filled_on = datetime.now(timezone.utc).date().isoformat()
    odometer_km = str(float(odometer.input_value()) + 1)
    page.locator("#fuelForm [name='filled_on']").fill(filled_on)
    odometer.fill(odometer_km)
    page.locator("#fuelForm [name='amount']").fill("32.5")
    page.locator("#fuelForm [name='cost']").fill("54.99")
    page.locator("#fuelForm [name='station']").fill(station)
    page.locator("#fuelForm button").click()

    row = page.locator("#fuelList .svc").filter(has_text=station)
    page.locator("#fuelListTitle").click()
    expect(row).to_be_visible()

    page.reload()
    page.get_by_label("Vehicle").select_option(DEMO_ICE_VIN)
    expect(page.locator("#carName")).to_have_text("2023 Toyota Corolla")
    page.locator("#fuelListTitle").click()
    expect(row).to_be_visible()

    page.once("dialog", lambda dialog: dialog.accept())
    row.locator('[data-action="delete-fuel"]').click()

    expect(row).to_have_count(0)


def test_calendar_download_contains_events(server_url: str, page: Page) -> None:
    page.goto(f"{server_url}/#maintenance")
    button = page.locator("#calendarBtn")
    expect(button).to_be_enabled(timeout=30_000)

    with page.expect_download() as download_info:
        button.click()

    download = download_info.value
    assert download.suggested_filename.endswith(".ics")
    download_path = download.path()
    assert download_path is not None
    calendar = Path(download_path).read_text(encoding="utf-8")
    assert "BEGIN:VCALENDAR" in calendar
    assert "BEGIN:VEVENT" in calendar


def test_alert_group_can_be_dismissed(server_url: str, page: Page) -> None:
    page.goto(f"{server_url}/#overview")
    group = page.locator("#alerts .issue").first
    expect(group).to_be_visible()
    group.locator("summary").click()
    dismiss = group.locator('[data-action="dismiss-alerts"]')
    expect(dismiss).to_be_visible()

    dismiss.click()

    expect(group).to_have_count(0)


def test_maintenance_row_prefills_service(server_url: str, page: Page) -> None:
    page.goto(f"{server_url}/#maintenance")
    prefill = page.locator('#maint [data-action="prefill-service"]').first
    expect(prefill).to_be_visible()
    item = prefill.get_attribute("data-item")
    assert item is not None

    prefill.click()

    expect(page.locator("#svcItem")).to_have_value(item)


def test_trip_summary_insights_and_recent_trips_render(server_url: str, page: Page) -> None:
    page.goto(f"{server_url}/#trips")

    expect(page.locator("#tripKpis .glance-item")).to_have_count(3)
    expect(page.locator("#tripKpis .metric-label")).to_have_text([
        "Distance per day",
        "Trips in the last 30 days",
        "Efficiency",
    ])
    efficiency = page.locator(
        "#tripKpis .trip-efficiency-good, #tripKpis .trip-efficiency-warning",
    )
    expect(efficiency).to_be_visible()
    expect(efficiency).not_to_contain_text("+")
    expect(page.locator("#tripInsights .trip-insight").first).to_be_visible()
    expect(page.locator("#tripList .trip-row").first).to_be_visible()
    expect(page.locator("#tripTrend .trip-week")).to_have_count(8)
