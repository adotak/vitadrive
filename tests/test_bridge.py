from __future__ import annotations

import json
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Iterator

import pytest
from fastapi.testclient import TestClient

from vitadrive import cli
from vitadrive.api import create_app
from vitadrive.bridge import (
    ApiSink,
    Bridge,
    DryRunSink,
    FatalBridgeError,
    OfflineBuffer,
    SendResult,
    SendStatus,
    SimulatedSource,
)
from vitadrive.models import Powertrain, SensorReading, Vehicle

VIN = "VDBRIDGETEST0001"


def _reading(index: int = 0) -> SensorReading:
    return SensorReading(
        vin=VIN,
        timestamp=datetime(2025, 1, 1, tzinfo=timezone.utc) + timedelta(seconds=index * 5),
        odometer_km=100 + index * 0.1,
        fuel_level_pct=80 - index * 0.01,
    )


class _Source:
    def __init__(self) -> None:
        self.index = 0

    def read(self) -> SensorReading:
        reading = _reading(self.index)
        self.index += 1
        return reading


class _Sink:
    def __init__(self, results: list[SendResult] | None = None) -> None:
        self.results = results or []
        self.sent: list[SensorReading] = []

    def send(self, reading: SensorReading) -> SendResult:
        self.sent.append(reading)
        return self.results.pop(0) if self.results else SendResult(SendStatus.SENT)


class _MaxRng:
    @staticmethod
    def uniform(_start: float, end: float) -> float:
        return end


class _ZeroRng:
    @staticmethod
    def uniform(_start: float, _end: float) -> float:
        return 0


@contextmanager
def _http_server(
    responses: list[tuple[int, dict[str, str], bytes]],
) -> Iterator[tuple[str, list[dict[str, Any]]]]:
    requests: list[dict[str, Any]] = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            body = self.rfile.read(int(self.headers["Content-Length"]))
            requests.append({"path": self.path, "headers": self.headers, "body": body})
            status, headers, response_body = responses.pop(0) if len(responses) > 1 else responses[0]
            self.send_response(status)
            for name, value in headers.items():
                self.send_header(name, value)
            self.send_header("Content-Length", str(len(response_body)))
            self.end_headers()
            self.wfile.write(response_body)

        def log_message(self, _format: str, *_args: Any) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", requests
    finally:
        server.shutdown()
        thread.join()
        server.server_close()


def test_offline_buffer_is_fifo_acknowledges_and_drops_oldest(tmp_path, caplog) -> None:
    buffer = OfflineBuffer(tmp_path / "buffer.db", max_rows=2)
    try:
        buffer.push(_reading(1))
        buffer.push(_reading(2))
        with caplog.at_level("WARNING"):
            buffer.push(_reading(3))
        assert len(buffer) == 2
        assert [entry.reading.odometer_km for entry in buffer.peek(10)] == [100.2, 100.3]
        assert "dropped 1 oldest" in caplog.text
        buffer.ack([buffer.peek(1)[0].id])
        assert [entry.reading.odometer_km for entry in buffer.peek(10)] == [100.3]
        buffer.ack([])
    finally:
        buffer.close()


def test_offline_buffer_persists_across_reopen(tmp_path) -> None:
    path = tmp_path / "persist.db"
    first = OfflineBuffer(path)
    first.push(_reading(4))
    first.close()
    second = OfflineBuffer(path)
    try:
        assert len(second) == 1
        assert second.peek(1)[0].reading == _reading(4)
        assert second.peek(0) == []
    finally:
        second.close()


@pytest.mark.parametrize(
    ("status", "headers", "body", "expected", "retry_after"),
    [
        (201, {}, b'[{"severity":"warning","message":"Check the coolant"}]', SendStatus.SENT, None),
        (429, {"Retry-After": "3"}, b'{"detail":"slow down"}', SendStatus.RETRY, 3),
        (500, {}, b'{"detail":"server error"}', SendStatus.RETRY, None),
        (422, {}, b'{"detail":"invalid reading"}', SendStatus.DROP, None),
        (401, {}, b'{"detail":"Invalid API key"}', SendStatus.FATAL, None),
        (403, {}, b'{"detail":"forbidden"}', SendStatus.FATAL, None),
        (404, {}, b'{"detail":"unknown vehicle"}', SendStatus.FATAL, None),
    ],
)
def test_api_sink_statuses_headers_and_json(
    status: int,
    headers: dict[str, str],
    body: bytes,
    expected: SendStatus,
    retry_after: float | None,
) -> None:
    with _http_server([(status, headers, body)]) as (url, requests):
        reading = _reading(2)
        result = ApiSink(url, VIN, "secret-key").send(reading)
    assert result.status is expected
    assert result.retry_after == retry_after
    assert requests[0]["path"] == f"/api/vehicles/{VIN}/readings"
    assert requests[0]["headers"]["X-API-Key"] == "secret-key"
    assert requests[0]["headers"]["User-Agent"].startswith("vitadrive-bridge/")
    assert json.loads(requests[0]["body"]) == reading.model_dump(mode="json")


def test_api_sink_parses_retry_date_and_network_failure(monkeypatch) -> None:
    retry_date = "Wed, 01 Jan 2025 00:00:03 GMT"
    with _http_server([(429, {"Retry-After": retry_date}, b"{}")]) as (url, _requests):
        result = ApiSink(
            url,
            VIN,
            "secret-key",
            clock=lambda: datetime(2025, 1, 1, tzinfo=timezone.utc),
        ).send(_reading())
    assert result.retry_after == 3

    def offline(*_args: Any, **_kwargs: Any) -> None:
        from urllib.error import URLError

        raise URLError("network down")

    monkeypatch.setattr("vitadrive.bridge.urlopen", offline)
    assert ApiSink("http://example.invalid", VIN, "secret-key").send(_reading()).status is SendStatus.RETRY


def test_api_sink_unexpected_status_is_fatal() -> None:
    with _http_server([(418, {}, b"Unexpected")]) as (url, _requests):
        result = ApiSink(url, VIN, "secret-key").send(_reading())
    assert result.status is SendStatus.FATAL
    assert result.error == "Unexpected"


def test_transient_failure_buffers_and_later_flush_preserves_order() -> None:
    buffer = OfflineBuffer(":memory:")
    sink = _Sink([SendResult(SendStatus.RETRY), SendResult(SendStatus.SENT), SendResult(SendStatus.SENT)])
    bridge = Bridge(_Source(), sink, buffer, interval_s=5, rng=_ZeroRng())  # type: ignore[arg-type]
    try:
        first = bridge.tick()
        second = bridge.tick()
        assert first["buffered"] == 1
        assert second["buffered"] == 0
        assert [reading.odometer_km for reading in sink.sent] == [100, 100, 100.1]
    finally:
        buffer.close()


def test_backoff_grows_with_full_jitter_and_caps() -> None:
    buffer = OfflineBuffer(":memory:")
    sink = _Sink([SendResult(SendStatus.RETRY) for _ in range(10)])
    delays: list[float] = []
    bridge = Bridge(_Source(), sink, buffer, 5, sleep=delays.append, rng=_MaxRng())  # type: ignore[arg-type]
    try:
        bridge.run(max_ticks=10)
        assert delays == [1, 2, 4, 8, 16, 32, 60, 60, 60]
    finally:
        buffer.close()


def test_retry_after_is_honoured_and_fatal_api_response_stops() -> None:
    with _http_server([
        (429, {"Retry-After": "3"}, b"{}"),
        (201, {}, b"[]"),
    ]) as (url, _requests):
        buffer = OfflineBuffer(":memory:")
        delays: list[float] = []
        bridge = Bridge(
            _Source(),
            ApiSink(url, VIN, "secret-key"),
            buffer,
            5,
            sleep=delays.append,
            rng=_ZeroRng(),  # type: ignore[arg-type]
        )
        try:
            bridge.run(max_ticks=2)
            assert delays == [3]
            assert len(buffer) == 0
        finally:
            buffer.close()

    buffer = OfflineBuffer(":memory:")
    fatal = _Sink([SendResult(SendStatus.FATAL, error="Invalid API key")])
    bridge = Bridge(_Source(), fatal, buffer, 5)  # type: ignore[arg-type]
    try:
        with pytest.raises(FatalBridgeError, match="Invalid API key"):
            bridge.run(max_ticks=5)
        assert len(fatal.sent) == 1
    finally:
        buffer.close()


def test_dry_run_prints_json_lines_without_http(monkeypatch, capsys) -> None:
    def no_http(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("dry-run must not make HTTP requests")

    monkeypatch.setattr("vitadrive.bridge.urlopen", no_http)
    buffer = OfflineBuffer(":memory:")
    bridge = Bridge(_Source(), DryRunSink(), buffer, 5)
    try:
        bridge.run(max_ticks=2)
        lines = capsys.readouterr().out.splitlines()
        assert len(lines) == 2
        assert [json.loads(line)["odometer_km"] for line in lines] == [100, 100.1]
    finally:
        buffer.close()


def test_keyboard_interrupt_flushes_buffer_within_shutdown_window() -> None:
    buffer = OfflineBuffer(":memory:")
    sink = _Sink([SendResult(SendStatus.RETRY), SendResult(SendStatus.SENT)])

    def interrupt(_delay: float) -> None:
        raise KeyboardInterrupt

    bridge = Bridge(_Source(), sink, buffer, 5, sleep=interrupt, rng=_ZeroRng())  # type: ignore[arg-type]
    try:
        bridge.run()
        assert len(buffer) == 0
        assert len(sink.sent) == 2
    finally:
        buffer.close()


def test_simulated_source_advances_virtual_time_and_distance() -> None:
    vehicle = Vehicle(
        vin=VIN,
        make="Toyota",
        model="Test",
        year=2024,
        powertrain=Powertrain.ICE,
        tank_capacity_l=50,
    )
    start = datetime(2025, 1, 1, tzinfo=timezone.utc)
    source = SimulatedSource(vehicle, 5, seed=4, clock=lambda: start)
    first, second = source.read(), source.read()
    assert first.timestamp == start
    assert second.timestamp - first.timestamp == timedelta(seconds=5)
    assert second.odometer_km > first.odometer_km
    with pytest.raises(ValueError, match="interval_s"):
        SimulatedSource(vehicle, 0)


class _HeaderAuth:
    enabled = False
    publishable_key = None
    frontend_api = None

    def user_id(self, authorization: str | None) -> str:
        return authorization or "local"


def test_simulated_bridge_ingests_readings_and_detects_trips(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("VITADRIVE_RATE_LIMITS", "0")
    vehicle = Vehicle(
        vin=VIN,
        make="Toyota",
        model="Test",
        year=2024,
        powertrain=Powertrain.ICE,
        tank_capacity_l=50,
        rated_consumption=7.5,
    )
    app = create_app(str(tmp_path / "bridge-api.db"), auth=_HeaderAuth())
    store = app.state.vitadrive.store
    store.upsert_vehicle(vehicle)
    api_key = store.rotate_api_key(VIN)
    client = TestClient(app)

    class ClientSink:
        def send(self, reading: SensorReading) -> SendResult:
            response = client.post(
                f"/api/vehicles/{VIN}/readings",
                json=reading.model_dump(mode="json"),
                headers={"X-API-Key": api_key},
            )
            if response.status_code != 201:
                return SendResult(SendStatus.RETRY, error=response.text)
            return SendResult(SendStatus.SENT, alerts=response.json())

    bridge_buffer = OfflineBuffer(":memory:")
    source = SimulatedSource(
        vehicle,
        5,
        seed=1,
        clock=lambda: datetime(2025, 1, 1, tzinfo=timezone.utc),
    )
    bridge = Bridge(source, ClientSink(), bridge_buffer, 5, sleep=lambda _delay: None)
    try:
        with client:
            bridge.run(max_ticks=720)
        assert len(store.readings(VIN)) == 720
        assert len(store.trips(VIN)) >= 1
    finally:
        bridge_buffer.close()
        store.close()


def test_cli_bridge_validation_env_key_and_obd_compatibility(monkeypatch) -> None:
    with pytest.raises(SystemExit):
        cli.main(["bridge", VIN])
    with pytest.raises(SystemExit):
        cli.main(["bridge", VIN, "--local", "--dry-run"])
    with pytest.raises(SystemExit):
        cli.main(["bridge", VIN, "--url", "http://example.test", "--count", "-1"])

    captured: list[Any] = []
    monkeypatch.setenv("VITADRIVE_API_KEY", "secret-key")
    monkeypatch.setattr(cli, "cmd_bridge", captured.append)
    cli.main(["bridge", VIN, "--url", "http://example.test", "--simulate", "--count", "0"])
    assert captured[-1].api_key == "secret-key"
    cli.main(["obd", VIN, "--odometer", "42000"])
    assert captured[-1].local is True
    assert captured[-1].odometer == 42000
