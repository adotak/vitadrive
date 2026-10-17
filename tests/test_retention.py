import os
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, insert, select

from vitadrive import retention
from vitadrive.api import create_app
from vitadrive.demo import seed_vehicle
from vitadrive.models import Powertrain, SensorReading, Vehicle
from vitadrive.retention import PruneResult, merge_summary, prune, summarize_day
from vitadrive.service import VitaDrive
from vitadrive.storage import Store, rate_limits_t, readings_daily_t, readings_t

PG_URL = os.environ.get("VITADRIVE_TEST_POSTGRES")
NOW = datetime(2025, 4, 15, 12, tzinfo=timezone.utc)
VIN = "VDRETENTION000001"


class HeaderAuth:
    enabled, publishable_key, frontend_api = True, None, None

    def user_id(self, authorization):
        return authorization or "anon"


def _vehicle(vin=VIN):
    return Vehicle(
        vin=vin,
        make="Toyota",
        model="Corolla",
        year=2023,
        powertrain=Powertrain.ICE,
        tank_capacity_l=50,
        rated_consumption=6.5,
    )


def _reading(timestamp, odometer, **values):
    return SensorReading(vin=VIN, timestamp=timestamp, odometer_km=odometer, **values)


@pytest.fixture
def store(tmp_path):
    instance = Store(tmp_path / "retention.db")
    try:
        yield instance
    finally:
        instance.close()


def test_summarize_day_uses_means_latest_odometer_and_dtc_union():
    readings = [
        _reading(
            datetime(2025, 4, 1, 8, tzinfo=timezone.utc),
            100,
            coolant_temp_c=80,
            engine_oil_life_pct=50,
            dtc_codes=["P0420"],
        ),
        _reading(
            datetime(2025, 4, 1, 18, tzinfo=timezone.utc),
            120,
            coolant_temp_c=100,
            engine_oil_life_pct=None,
            dtc_codes=["P0301", "P0420"],
        ),
    ]

    summary = summarize_day(readings)

    assert summary.timestamp == readings[-1].timestamp
    assert summary.odometer_km == 120
    assert summary.coolant_temp_c == 90
    assert summary.engine_oil_life_pct == 50
    assert summary.fuel_level_pct is None
    assert summary.dtc_codes == ["P0301", "P0420"]


def test_merge_summary_weights_existing_means_and_keeps_latest_reading():
    existing = summarize_day([
        _reading(datetime(2025, 4, 1, 8, tzinfo=timezone.utc), 100, coolant_temp_c=80),
        _reading(datetime(2025, 4, 1, 12, tzinfo=timezone.utc), 110, coolant_temp_c=100),
    ])
    late = _reading(datetime(2025, 4, 1, 10, tzinfo=timezone.utc), 105, coolant_temp_c=110)

    merged = merge_summary(existing, 2, [late])

    assert merged.coolant_temp_c == pytest.approx(290 / 3)
    assert merged.timestamp == existing.timestamp
    assert merged.odometer_km == existing.odometer_km


def test_prune_compacts_old_history_and_preserves_forecast_and_range(tmp_path):
    store = Store(tmp_path / "forecast.db")
    vehicle = _vehicle()
    vd = VitaDrive(store)
    seed_vehicle(vd, vehicle, days=90, per_day=2, seed=17, start_odometer_km=30_000)
    now = datetime.now(timezone.utc)
    cutoff = datetime.combine((now - timedelta(days=30)).date(), datetime.min.time(), tzinfo=timezone.utc)
    today = now.date()
    forecasts_before = {item.item: item for item in vd.maintenance(vehicle.vin, today=today)}
    range_before = vd.range(vehicle.vin)

    try:
        result = prune(store, now=now)

        assert result.vehicles == 1
        assert result.days_summarized > 0
        assert result.readings_removed > 0
        with store.transaction() as connection:
            raw_old = connection.execute(
                select(func.count()).select_from(readings_t).where(
                    readings_t.c.vin == vehicle.vin,
                    readings_t.c.timestamp < cutoff.isoformat(),
                )
            ).scalar_one()
            summary_count = connection.execute(
                select(func.count()).select_from(readings_daily_t).where(readings_daily_t.c.vin == vehicle.vin)
            ).scalar_one()
            summary_rows = connection.execute(
                select(readings_daily_t.c.day, readings_daily_t.c.count).where(
                    readings_daily_t.c.vin == vehicle.vin
                )
            ).all()
            raw_count = connection.execute(
                select(func.count()).select_from(readings_t).where(readings_t.c.vin == vehicle.vin)
            ).scalar_one()
        assert raw_old == 0
        assert summary_count == result.days_summarized
        assert sum(row._mapping["count"] for row in summary_rows) == result.readings_removed
        assert raw_count > 0

        readings = store.readings(vehicle.vin)
        assert readings == sorted(readings, key=lambda reading: reading.timestamp)
        assert len(readings) == summary_count + raw_count
        forecasts_after = {item.item: item for item in vd.maintenance(vehicle.vin, today=today)}
        assert forecasts_after.keys() == forecasts_before.keys()
        for item, before in forecasts_before.items():
            after = forecasts_after[item]
            due_delta = abs((after.due_date - before.due_date).days)
            km_delta = abs(after.km_remaining - before.km_remaining) if before.km_remaining is not None else 0
            km_tolerance = max(abs(before.km_remaining or 0) * 0.02, 50)
            assert due_delta <= 3, f"{item}: due-date delta {due_delta} days"
            assert km_delta <= km_tolerance, f"{item}: km_remaining delta {km_delta:.1f} km"
        range_after = vd.range(vehicle.vin)
        assert range_after.range_km == pytest.approx(range_before.range_km, rel=0.01)
        assert range_after.status == range_before.status

        repeated = prune(store, now=now)
        assert repeated.vehicles == 0
        assert repeated.days_summarized == 0
        assert repeated.readings_removed == 0
    finally:
        store.close()


def test_prune_merges_late_readings_into_existing_day(store):
    vehicle = _vehicle()
    store.upsert_vehicle(vehicle)
    old_time = NOW - timedelta(days=40)
    store.add_reading(_reading(old_time, 100, coolant_temp_c=80, dtc_codes=["P0420"]))
    store.add_reading(_reading(old_time + timedelta(hours=10), 110, coolant_temp_c=100))

    first = prune(store, older_than_days=30, now=NOW)
    assert first.readings_removed == 2

    store.add_reading(_reading(old_time + timedelta(hours=5), 105, coolant_temp_c=110, dtc_codes=["P0301"]))
    second = prune(store, older_than_days=30, now=NOW)

    assert second.days_summarized == 1
    assert second.readings_removed == 1
    with store.transaction() as connection:
        row = connection.execute(
            select(readings_daily_t.c.count, readings_daily_t.c.data).where(
                readings_daily_t.c.vin == vehicle.vin,
                readings_daily_t.c.day == old_time.date().isoformat(),
            )
        ).one()
    summary = SensorReading.model_validate_json(row._mapping["data"])
    assert row._mapping["count"] == 3
    assert summary.coolant_temp_c == pytest.approx(290 / 3)
    assert summary.timestamp == old_time + timedelta(hours=10)
    assert summary.odometer_km == 110
    assert summary.dtc_codes == ["P0301", "P0420"]


def test_prune_dry_run_reports_changes_without_mutating_store(store):
    vehicle = _vehicle()
    store.upsert_vehicle(vehicle)
    old_reading = _reading(NOW - timedelta(days=40), 100)
    store.add_reading(old_reading)
    old_window = int(NOW.timestamp()) - 5 * 86400
    current_window = int(NOW.timestamp()) - 3600
    with store.transaction() as connection:
        connection.execute(insert(rate_limits_t).values(key="demo:old", window_start=old_window, count=1))
        connection.execute(insert(rate_limits_t).values(key="demo:recent", window_start=current_window, count=1))

    result = prune(store, older_than_days=30, now=NOW, dry_run=True)

    assert result.readings_removed == 1
    assert result.rate_limit_rows_removed == 1
    with store.transaction() as connection:
        raw_count = connection.execute(
            select(func.count()).select_from(readings_t).where(readings_t.c.vin == vehicle.vin)
        ).scalar_one()
        summary_count = connection.execute(
            select(func.count()).select_from(readings_daily_t).where(readings_daily_t.c.vin == vehicle.vin)
        ).scalar_one()
        rate_count = connection.execute(select(func.count()).select_from(rate_limits_t)).scalar_one()
    assert raw_count == 1
    assert summary_count == 0
    assert rate_count == 2


def test_prune_removes_expired_rate_limits_and_is_idempotent(store):
    old_window = int(NOW.timestamp()) - 5 * 86400
    recent_window = int(NOW.timestamp()) - 3600
    with store.transaction() as connection:
        connection.execute(insert(rate_limits_t).values(key="ask:old", window_start=old_window, count=1))
        connection.execute(insert(rate_limits_t).values(key="ask:recent", window_start=recent_window, count=1))

    first = prune(store, now=NOW)
    second = prune(store, now=NOW)

    assert first.rate_limit_rows_removed == 1
    assert second.rate_limit_rows_removed == 0
    with store.transaction() as connection:
        keys = connection.execute(select(rate_limits_t.c.key)).scalars().all()
    assert keys == ["ask:recent"]


def test_delete_vehicle_removes_daily_summaries(store):
    vehicle = _vehicle()
    store.upsert_vehicle(vehicle)
    store.add_reading(_reading(NOW - timedelta(days=40), 100))
    prune(store, older_than_days=30, now=NOW)

    store.delete_vehicle(vehicle.vin)

    with store.transaction() as connection:
        count = connection.execute(
            select(func.count()).select_from(readings_daily_t).where(readings_daily_t.c.vin == vehicle.vin)
        ).scalar_one()
    assert count == 0


def test_readings_applies_since_and_limit_after_merging_summaries(store):
    vehicle = _vehicle()
    store.upsert_vehicle(vehicle)
    old_day = NOW - timedelta(days=40)
    store.add_reading(_reading(old_day, 100, coolant_temp_c=80))
    store.add_reading(_reading(old_day + timedelta(hours=10), 110, coolant_temp_c=100))
    prune(store, older_than_days=30, now=NOW)
    recent = _reading(NOW - timedelta(hours=1), 200, coolant_temp_c=90)
    store.add_reading(recent)

    assert store.readings(vehicle.vin, limit=1) == [recent]
    assert store.readings(vehicle.vin, since=old_day + timedelta(hours=11)) == [recent]


def test_prune_cli_prints_result_counts(tmp_path, capsys):
    from vitadrive.cli import main

    path = tmp_path / "cli.db"
    main(["--db", str(path), "prune", "--days", "30", "--dry-run"])

    output = capsys.readouterr().out
    assert "Vehicles: 0" in output
    assert "days summarized: 0" in output
    assert "readings removed: 0" in output
    assert "rate-limit rows removed: 0" in output


def test_cron_prune_secret_gate_and_result(tmp_path, monkeypatch):
    client = TestClient(create_app(str(tmp_path / "cron.db"), auth=HeaderAuth()))
    monkeypatch.delenv("CRON_SECRET", raising=False)
    assert client.get("/api/cron/prune").status_code == 503

    monkeypatch.setenv("CRON_SECRET", "cron-secret")
    assert client.get("/api/cron/prune").status_code == 401
    assert client.get("/api/cron/prune", headers={"Authorization": "Bearer wrong"}).status_code == 401
    expected = PruneResult(vehicles=1, days_summarized=2, readings_removed=4, rate_limit_rows_removed=3)
    calls = []
    monkeypatch.setattr(retention, "prune", lambda store: calls.append(store) or expected)

    response = client.get("/api/cron/prune", headers={"Authorization": "Bearer cron-secret"})

    assert response.status_code == 200
    assert response.json() == expected.model_dump()
    assert len(calls) == 1


@pytest.mark.skipif(not PG_URL, reason="set VITADRIVE_TEST_POSTGRES to run against Postgres")
def test_postgres_prune_and_readings():
    store = Store(PG_URL)
    vehicle = _vehicle(f"VDRET{uuid4().hex[:12].upper()}")
    vd = VitaDrive(store)
    try:
        seed_vehicle(vd, vehicle, days=10, per_day=2, seed=13, start_odometer_km=20_000)
        now = datetime.now(timezone.utc)
        result = prune(store, older_than_days=5, now=now)
        readings = store.readings(vehicle.vin)
        with store.transaction() as connection:
            summary_count = connection.execute(
                select(func.count()).select_from(readings_daily_t).where(readings_daily_t.c.vin == vehicle.vin)
            ).scalar_one()
            raw_count = connection.execute(
                select(func.count()).select_from(readings_t).where(readings_t.c.vin == vehicle.vin)
            ).scalar_one()

        assert result.vehicles == 1
        assert result.readings_removed > 0
        assert len(readings) == summary_count + raw_count
        assert readings == sorted(readings, key=lambda reading: reading.timestamp)
    finally:
        store.delete_vehicle(vehicle.vin)
        store.close()
