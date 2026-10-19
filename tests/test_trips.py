from datetime import date, datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from vitadrive.api import create_app
from vitadrive.demo import DEMO_VEHICLES, seed_vehicle
from vitadrive.models import Powertrain, SensorReading, Trip, Vehicle
from vitadrive.service import VitaDrive
from vitadrive.storage import Store
from vitadrive.trips import detect_trips, trip_payload, trip_summary, update_trips

VIN = "VDTRIPTEST0000001"
TODAY = date(2025, 4, 16)
UTC = timezone.utc


def _vehicle(powertrain: Powertrain = Powertrain.ICE) -> Vehicle:
    return Vehicle(
        vin=VIN,
        make="Toyota",
        model="Corolla",
        year=2023,
        powertrain=powertrain,
        tank_capacity_l=50 if powertrain is not Powertrain.EV else None,
        battery_capacity_kwh=60 if powertrain is Powertrain.EV else None,
        rated_consumption=6.5 if powertrain is not Powertrain.EV else 15,
    )


def _time(hour: int, minute: int = 0, second: int = 0, day: date = TODAY) -> datetime:
    return datetime(day.year, day.month, day.day, hour, minute, second, tzinfo=UTC)


def _reading(
    timestamp: datetime,
    odometer: float,
    *,
    fuel: float | None = None,
    soc: float | None = None,
) -> SensorReading:
    return SensorReading(
        vin=VIN,
        timestamp=timestamp,
        odometer_km=odometer,
        fuel_level_pct=fuel,
        hv_battery_soc_pct=soc,
    )


def _trip(
    distance: float,
    *,
    started_at: datetime | None = None,
    speed: float = 80,
    start_level: float | None = None,
    end_level: float | None = None,
    duration_s: int = 3600,
) -> Trip:
    start = started_at or _time(8)
    return Trip(
        vin=VIN,
        started_at=start,
        ended_at=start + timedelta(seconds=duration_s),
        start_odometer_km=100,
        end_odometer_km=100 + distance,
        start_level_pct=start_level,
        end_level_pct=end_level,
        max_speed_kmh=speed,
        samples=3,
    )


def test_detect_trips_splits_on_gap_and_stationary_pair():
    readings = [
        _reading(_time(8), 100),
        _reading(_time(8, 1), 100.1),
        _reading(_time(8, 2), 100.1),
        _reading(_time(8, 3), 100.2),
        _reading(_time(8, 20), 100.3),
    ]

    trips = detect_trips(readings)

    assert len(trips) == 2
    assert [(trip.start_odometer_km, trip.end_odometer_km) for trip in trips] == [
        (100, 100.1),
        (100.1, 100.2),
    ]
    assert [trip.samples for trip in trips] == [2, 2]


def test_detect_trips_sorts_out_of_order_readings_and_splits_after_gap():
    readings = [
        _reading(_time(8, 12), 100.2),
        _reading(_time(8), 100),
        _reading(_time(8, 2), 100.1),
        _reading(_time(8, 30), 100.4),
        _reading(_time(8, 32), 100.5),
    ]

    trips = detect_trips(readings)

    assert len(trips) == 2
    assert [trip.distance_km for trip in trips] == pytest.approx([0.2, 0.1])


def test_trip_max_speed_ignores_segments_under_five_seconds():
    trips = detect_trips([
        _reading(_time(8), 100),
        _reading(_time(8, 0, 3), 100.1),
        _reading(_time(8, 0, 9), 100.2),
    ])

    assert len(trips) == 1
    assert trips[0].max_speed_kmh == pytest.approx(60)
    assert trips[0].duration_s == pytest.approx(9)


def test_incremental_detection_ignores_late_readings_and_matches_pure_detection(tmp_path):
    readings = [
        _reading(_time(8), 100, fuel=70),
        _reading(_time(8, 1), 100.1, fuel=69.9),
        _reading(_time(8, 2), 100.2, fuel=69.8),
        _reading(_time(8, 20), 100.3, fuel=69.7),
        _reading(_time(8, 21), 100.4, fuel=69.6),
    ]
    store = Store(tmp_path / "incremental.db")
    try:
        store.upsert_vehicle(_vehicle())
        previous = None
        for reading in readings:
            update_trips(store, previous, reading)
            store.add_reading(reading)
            previous = reading

        incremental = sorted(store.trips(VIN), key=lambda trip: trip.started_at)
        pure = detect_trips(readings)
        assert [
            (trip.started_at, trip.ended_at, trip.start_odometer_km, trip.end_odometer_km, trip.samples)
            for trip in incremental
        ] == [
            (trip.started_at, trip.ended_at, trip.start_odometer_km, trip.end_odometer_km, trip.samples)
            for trip in pure
        ]

        latest_reading = readings[-1]
        late = _reading(_time(8, 19), 100.35, fuel=69.65)
        update_trips(store, latest_reading, late)
        assert store.trips(VIN) == list(reversed(incremental))
    finally:
        store.close()


def test_store_trip_crud_supports_latest_since_and_limit(tmp_path):
    store = Store(tmp_path / "trip-storage.db")
    store.upsert_vehicle(_vehicle())
    older = store.add_trip(_trip(10, started_at=_time(8)))
    newer = store.add_trip(_trip(20, started_at=_time(9)))
    updated = newer.model_copy(update={"samples": 5, "max_speed_kmh": 105})
    store.update_trip(updated)
    try:
        assert older.id is not None
        assert store.latest_trip(VIN) == updated
        assert store.trips(VIN, limit=1) == [updated]
        assert store.trips(VIN, since=_time(8, 30)) == [updated]
    finally:
        store.close()


def test_trip_payload_rounds_real_values_without_rounding_storage(tmp_path):
    store = Store(tmp_path / "trip-precision.db")
    vehicle = _vehicle(Powertrain.EV)
    store.upsert_vehicle(vehicle)
    trip = Trip(
        vin=VIN,
        started_at=_time(8),
        ended_at=_time(8) + timedelta(seconds=1000),
        start_odometer_km=100.123,
        end_odometer_km=110.234,
        start_level_pct=82.34,
        end_level_pct=79.99,
        max_speed_kmh=87.654,
        samples=2,
    )
    try:
        stored = store.add_trip(trip)
        retrieved = store.trips(VIN)[0]
        payload = trip_payload(vehicle, retrieved)

        assert retrieved.start_odometer_km == pytest.approx(100.123)
        assert retrieved.end_odometer_km == pytest.approx(110.234)
        assert retrieved.start_level_pct == pytest.approx(82.34)
        assert retrieved.max_speed_kmh == pytest.approx(87.654)
        assert payload["start_odometer_km"] == 100.1
        assert payload["end_odometer_km"] == 110.2
        assert payload["start_level_pct"] == 82.3
        assert payload["end_level_pct"] == 80
        assert payload["max_speed_kmh"] == 87.7
        assert payload["distance_km"] == 10.1
        assert payload["avg_speed_kmh"] == 36.4
        assert payload["consumption"] == 13.95
        assert stored.start_odometer_km == pytest.approx(100.123)
    finally:
        store.close()


def test_ice_and_ev_consumption_use_powertrain_capacity():
    ice_trip = _trip(100, start_level=80, end_level=68)
    ev_trip = _trip(100, start_level=80, end_level=55)

    ice = trip_summary(_vehicle(), [ice_trip], today=TODAY)
    ev = trip_summary(_vehicle(Powertrain.EV), [ev_trip], today=TODAY)

    assert ice["trips"] == ev["trips"] == 1
    assert ice["distance_km"] == ev["distance_km"] == 100
    assert ice["km_per_day"] == ev["km_per_day"] == 3.3
    assert ice["avg_consumption"] == pytest.approx(6)
    assert ice["unit"] == "L/100 km"
    assert ev["avg_consumption"] == pytest.approx(15)
    assert ev["unit"] == "kWh/100 km"


def test_weekly_summary_has_eight_iso_weeks_and_null_empty_buckets():
    current = _trip(10, started_at=_time(8), start_level=70, end_level=69)
    previous = _trip(
        15,
        started_at=_time(9, day=TODAY - timedelta(days=7)),
        start_level=70,
        end_level=68,
    )

    summary = trip_summary(_vehicle(), [current, previous], today=TODAY)

    assert len(summary["weekly"]) == 8
    assert summary["weekly"][-1]["week_start"] == "2025-04-14"
    assert summary["weekly"][-1]["distance_km"] == 10
    assert summary["weekly"][-2]["distance_km"] == 15
    assert summary["weekly"][0] == {
        "week_start": "2025-02-24",
        "distance_km": None,
        "consumption": None,
    }


def test_insight_rules_cover_short_trips_consumption_speed_and_steady_driving():
    vehicle = _vehicle()
    short_trips = [_trip(4), _trip(4), _trip(10)]
    short_insights = trip_summary(vehicle, short_trips, today=TODAY)["insights"]
    assert any("Short trips" in item["text"] and item["kind"] == "tip" for item in short_insights)

    above = trip_summary(
        vehicle,
        [_trip(100, start_level=80, end_level=60)],
        today=TODAY,
    )["insights"]
    assert any("above the rated" in item["text"] and item["kind"] == "tip" for item in above)

    below = trip_summary(
        vehicle,
        [_trip(100, start_level=80, end_level=74)],
        today=TODAY,
    )["insights"]
    assert any("below the vehicle's rated" in item["text"] and item["kind"] == "good" for item in below)

    fast_trips = [_trip(10, speed=121), _trip(10), _trip(10), _trip(10)]
    fast_insights = trip_summary(vehicle, fast_trips, today=TODAY)["insights"]
    assert any("100 instead of 120" in item["text"] and item["kind"] == "tip" for item in fast_insights)

    at_threshold = [_trip(4), _trip(4), _trip(10), _trip(10), _trip(10, speed=121)]
    steady_insights = trip_summary(vehicle, at_threshold, today=TODAY)["insights"]
    assert len(steady_insights) == 1
    assert steady_insights[0]["kind"] == "good"
    assert "steady" in steady_insights[0]["text"]

    rated_consumption = trip_summary(
        vehicle,
        [_trip(100, start_level=80, end_level=67)],
        today=TODAY,
    )
    assert rated_consumption["vs_rated_pct"] == 0
    assert "steady" in rated_consumption["insights"][0]["text"]


class HeaderAuth:
    enabled = False
    publishable_key = None
    frontend_api = None

    def user_id(self, authorization):
        return authorization or "local"


def test_trips_api_shape_validation_and_owner_isolation(tmp_path):
    app = create_app(str(tmp_path / "trips-api.db"), auth=HeaderAuth())
    vehicle = _vehicle(Powertrain.EV)
    app.state.vitadrive.store.upsert_vehicle(vehicle, owner_id="alice")
    app.state.vitadrive.store.add_trip(_trip(
        100,
        started_at=datetime.now(UTC) - timedelta(hours=1),
        start_level=80,
        end_level=55,
    ))
    try:
        with TestClient(app) as client:
            response = client.get(f"/api/vehicles/{VIN}/trips", headers={"Authorization": "alice"})
            assert response.status_code == 200
            body = response.json()
            assert body["trips"] == 1
            assert body["avg_consumption"] == 15
            assert body["unit"] == "kWh/100 km"
            assert len(body["weekly"]) == 8
            assert body["recent"][0]["distance_km"] == 100
            assert body["recent"][0]["duration_s"] == 3600
            assert body["recent"][0]["avg_speed_kmh"] == 100
            assert body["recent"][0]["max_speed_kmh"] == 80
            assert body["recent"][0]["consumption"] == 15

            assert client.get(
                f"/api/vehicles/{VIN}/trips",
                headers={"Authorization": "bob"},
            ).status_code == 404
            for days in (0, 366):
                assert client.get(
                    f"/api/vehicles/{VIN}/trips?days={days}",
                    headers={"Authorization": "alice"},
                ).status_code == 422
    finally:
        app.state.vitadrive.store.close()


def test_demo_trips_sum_to_each_recent_odometer_delta(tmp_path):
    store = Store(tmp_path / "demo-trips.db")
    vehicle = _vehicle()
    try:
        seed_vehicle(VitaDrive(store), vehicle, days=70, per_day=1, seed=29)
        readings = store.readings(VIN)[-57:]
        trips = store.trips(VIN)

        assert len(readings) > 1
        assert trips
        for previous, reading in zip(readings, readings[1:], strict=False):
            interval = [
                trip for trip in trips
                if trip.start_odometer_km >= previous.odometer_km - 1e-7
                and trip.end_odometer_km <= reading.odometer_km + 1e-7
            ]
            assert sum(trip.distance_km for trip in interval) == pytest.approx(
                reading.odometer_km - previous.odometer_km,
            )
    finally:
        store.close()


def test_demo_profiles_are_rounded_and_show_distinct_insights(tmp_path):
    store = Store(tmp_path / "demo-profiles.db")
    try:
        for index, vehicle in enumerate(DEMO_VEHICLES):
            seed_vehicle(VitaDrive(store), vehicle, days=90, per_day=1, seed=29 + index)
            trips = store.trips(vehicle.vin)
            assert trips
            for trip in trips:
                assert trip.start_odometer_km == round(trip.start_odometer_km, 1)
                assert trip.end_odometer_km == round(trip.end_odometer_km, 1)
                assert trip.start_level_pct is None or trip.start_level_pct == round(
                    trip.start_level_pct, 1,
                )
                assert trip.end_level_pct is None or trip.end_level_pct == round(
                    trip.end_level_pct, 1,
                )
                assert trip.max_speed_kmh == round(trip.max_speed_kmh, 1)
                assert trip.started_at.microsecond == trip.ended_at.microsecond == 0

            summary = trip_summary(vehicle, trips, days=30, today=date.today())
            assert len(summary["weekly"]) == 8
            assert all(week["distance_km"] is not None for week in summary["weekly"])
            assert summary["trips"] < len(trips)
            insight_text = [insight["text"] for insight in summary["insights"]]
            assert any("100 instead of 120" in text for text in insight_text)
            if vehicle.powertrain is Powertrain.EV:
                assert any("above the rated" in text for text in insight_text)
            else:
                assert any("Short trips" in text for text in insight_text)
    finally:
        store.close()
