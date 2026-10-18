"""Resilient telemetry bridge for local OBD-II and simulated data sources."""
from __future__ import annotations

import json
import logging
import random
import sqlite3
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Protocol, TextIO
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from . import __version__
from .adapters.simulator import VehicleSimulator
from .models import SensorReading, Vehicle

_LOGGER = logging.getLogger(__name__)
_BATCH_SIZE = 50
_BACKOFF_BASE_S = 1.0
_BACKOFF_CAP_S = 60.0


class ReadingSource(Protocol):
    def read(self) -> SensorReading: ...


class ReadingSink(Protocol):
    def send(self, reading: SensorReading) -> SendResult: ...


class SendStatus(str, Enum):
    SENT = "sent"
    RETRY = "retry"
    DROP = "drop"
    FATAL = "fatal"


@dataclass(frozen=True)
class SendResult:
    status: SendStatus
    alerts: list[dict[str, Any]] = field(default_factory=list)
    retry_after: float | None = None
    error: str | None = None


@dataclass(frozen=True)
class _BufferEntry:
    id: int
    reading: SensorReading


class FatalBridgeError(RuntimeError):
    """Raised when the server rejects the configured VIN or API key."""


class OfflineBuffer:
    """Persistent FIFO for readings that could not be delivered."""

    def __init__(self, path: str | Path | None = None, max_rows: int = 10_000) -> None:
        if max_rows < 1:
            raise ValueError("max_rows must be positive")
        self.path = Path(path or Path.home() / ".vitadrive" / "bridge-buffer.db").expanduser()
        if str(self.path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self.max_rows = max_rows
        self._connection = sqlite3.connect(str(self.path), timeout=30)
        self._connection.execute(
            "CREATE TABLE IF NOT EXISTS bridge_buffer "
            "(id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT NOT NULL, reading_json TEXT NOT NULL)",
        )
        self._connection.commit()

    def push(self, reading: SensorReading) -> None:
        self._connection.execute(
            "INSERT INTO bridge_buffer (created_at, reading_json) VALUES (?, ?)",
            (datetime.now(timezone.utc).isoformat(), reading.model_dump_json()),
        )
        count = int(self._connection.execute("SELECT COUNT(*) FROM bridge_buffer").fetchone()[0])
        overflow = count - self.max_rows
        if overflow > 0:
            self._connection.execute(
                "DELETE FROM bridge_buffer WHERE id IN "
                "(SELECT id FROM bridge_buffer ORDER BY id ASC LIMIT ?)",
                (overflow,),
            )
            _LOGGER.warning("Offline buffer is full; dropped %d oldest reading(s)", overflow)
        self._connection.commit()

    def peek(self, n: int) -> list[_BufferEntry]:
        if n <= 0:
            return []
        rows = self._connection.execute(
            "SELECT id, reading_json FROM bridge_buffer ORDER BY id ASC LIMIT ?",
            (n,),
        ).fetchall()
        return [_BufferEntry(int(row[0]), SensorReading.model_validate_json(row[1])) for row in rows]

    def ack(self, ids: list[int]) -> None:
        if not ids:
            return
        placeholders = ",".join("?" for _ in ids)
        self._connection.execute(f"DELETE FROM bridge_buffer WHERE id IN ({placeholders})", ids)
        self._connection.commit()

    def __len__(self) -> int:
        row = self._connection.execute("SELECT COUNT(*) FROM bridge_buffer").fetchone()
        return int(row[0])

    def close(self) -> None:
        self._connection.close()


class ApiSink:
    """Send one reading to the deployed ingestion endpoint using the stdlib HTTP client."""

    def __init__(
        self,
        base_url: str,
        vin: str,
        api_key: str,
        timeout: float = 10,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.url = f"{base_url.rstrip('/')}/api/vehicles/{quote(vin, safe='')}/readings"
        self.api_key = api_key
        self.timeout = timeout
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def send(self, reading: SensorReading) -> SendResult:
        request = Request(
            self.url,
            data=reading.model_dump_json().encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "User-Agent": f"vitadrive-bridge/{__version__}",
                "X-API-Key": self.api_key,
            },
            method="POST",
        )
        try:
            with urlopen(request, timeout=self.timeout) as response:
                return self._result(response.status, response.headers, response.read())
        except HTTPError as exc:
            return self._result(exc.code, exc.headers, exc.read())
        except (TimeoutError, URLError, OSError) as exc:
            return SendResult(SendStatus.RETRY, error=str(exc))

    def _result(self, status: int, headers: Any, body: bytes) -> SendResult:
        message = self._error_message(body)
        if 200 <= status < 300:
            try:
                alerts = json.loads(body) if body else []
            except json.JSONDecodeError:
                alerts = []
            return SendResult(SendStatus.SENT, alerts=alerts if isinstance(alerts, list) else [])
        if status in {401, 403, 404}:
            return SendResult(SendStatus.FATAL, error=message or f"HTTP {status}")
        if status in {400, 422}:
            return SendResult(SendStatus.DROP, error=message or f"HTTP {status}")
        if status == 429 or 500 <= status < 600:
            retry_after = self._retry_after(headers.get("Retry-After")) if status == 429 else None
            return SendResult(SendStatus.RETRY, retry_after=retry_after, error=message or f"HTTP {status}")
        return SendResult(SendStatus.FATAL, error=message or f"Unexpected HTTP {status}")

    @staticmethod
    def _error_message(body: bytes) -> str | None:
        if not body:
            return None
        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            return body.decode("utf-8", errors="replace").strip() or None
        if isinstance(payload, dict):
            message = payload.get("message") or payload.get("detail")
            if isinstance(message, dict):
                message = message.get("message") or message.get("msg")
            if isinstance(message, str):
                return message
        return None

    def _retry_after(self, value: str | None) -> float | None:
        if value is None:
            return None
        try:
            return max(0.0, float(value))
        except ValueError:
            try:
                retry_at = parsedate_to_datetime(value)
            except (TypeError, ValueError, OverflowError):
                return None
            if retry_at.tzinfo is None:
                retry_at = retry_at.replace(tzinfo=timezone.utc)
            now = self.clock()
            if now.tzinfo is None:
                now = now.replace(tzinfo=timezone.utc)
            return max(0.0, (retry_at.astimezone(timezone.utc) - now.astimezone(timezone.utc)).total_seconds())


class DryRunSink:
    """Write readings as JSON lines without making network requests."""

    def __init__(self, output: TextIO | None = None) -> None:
        self.output = output

    def send(self, reading: SensorReading) -> SendResult:
        print(reading.model_dump_json(), file=self.output or sys.stdout, flush=True)
        return SendResult(SendStatus.SENT)


class SimulatedSource:
    """Generate live-style readings with moving bursts and parked gaps."""

    def __init__(
        self,
        vehicle: Vehicle,
        interval_s: float,
        seed: int = 7,
        clock: Callable[[], datetime] | None = None,
        start_odometer_km: float = 25_000,
    ) -> None:
        if interval_s <= 0:
            raise ValueError("interval_s must be positive")
        self.vehicle = vehicle
        self.interval_s = interval_s
        self.rng = random.Random(seed)
        self.simulator = VehicleSimulator(
            vehicle,
            start_odometer_km=start_odometer_km,
            seed=seed,
            daily_km=45,
            fault_rate=0.01,
        )
        now = (clock or (lambda: datetime.now(timezone.utc)))()
        self.timestamp = now.replace(tzinfo=timezone.utc) if now.tzinfo is None else now.astimezone(timezone.utc)
        self.driving = True
        self.phase_remaining_s = float(self.rng.randint(10, 40) * 60)

    def read(self) -> SensorReading:
        if self.driving:
            speed_kmh = self.rng.uniform(40, 110)
            self.simulator.advance(speed_kmh * self.interval_s / 3600)
        reading = self.simulator.reading(self.timestamp)
        self.timestamp += timedelta(seconds=self.interval_s)
        self.phase_remaining_s -= self.interval_s
        if self.phase_remaining_s <= 0:
            self.driving = not self.driving
            if self.driving:
                self.phase_remaining_s = float(self.rng.randint(10, 40) * 60)
            else:
                self.phase_remaining_s = float(self.rng.randint(15, 60) * 60)
        return reading


class Bridge:
    """Read, durably buffer, and forward telemetry in FIFO order."""

    def __init__(
        self,
        source: ReadingSource,
        sink: ReadingSink,
        buffer: OfflineBuffer,
        interval_s: float,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
        rng: random.Random | None = None,
    ) -> None:
        if interval_s <= 0:
            raise ValueError("interval_s must be positive")
        self.source = source
        self.sink = sink
        self.buffer = buffer
        self.interval_s = interval_s
        self.sleep = sleep
        self.clock = clock
        self.rng = rng or random.Random()
        self._source_attempt = 0
        self._sink_attempt = 0
        self._pending_delay = 0.0

    def tick(self) -> dict[str, int]:
        self._pending_delay = 0.0
        read_ok = False
        reconnect_attempted = False
        try:
            connected = getattr(self.source, "is_connected", None)
            if callable(connected) and not connected():
                reconnect_attempted = True
                self._reconnect_source()
                if not connected():
                    raise ConnectionError("OBD-II adapter is still disconnected")
            reading = self.source.read()
            self.buffer.push(reading)
            read_ok = True
            self._source_attempt = 0
        except Exception as exc:
            self._source_failed(exc, reconnect=not reconnect_attempted)

        sent, dropped, alerts = self._flush_batch()
        self._print_alerts(alerts)
        result = {"read": int(read_ok), "sent": sent, "buffered": len(self.buffer), "dropped": dropped,
                  "alerts": len(alerts)}
        _LOGGER.info(
            "bridge tick read=%d sent=%d buffered=%d dropped=%d alerts=%d",
            result["read"], sent, result["buffered"], dropped, len(alerts),
        )
        return result

    def _reconnect_source(self) -> None:
        reconnect = getattr(self.source, "reconnect", None)
        if callable(reconnect):
            reconnect()

    def _source_failed(self, error: Exception, reconnect: bool) -> None:
        _LOGGER.warning("Reading failed: %s", error)
        if reconnect:
            try:
                self._reconnect_source()
            except Exception as reconnect_error:
                _LOGGER.warning("OBD-II reconnect failed: %s", reconnect_error)
        self._pending_delay = self._backoff(self._source_attempt)
        self._source_attempt += 1

    def _backoff(self, attempt: int, retry_after: float | None = None) -> float:
        ceiling = min(_BACKOFF_CAP_S, _BACKOFF_BASE_S * (2 ** min(attempt, 10)))
        jitter = self.rng.uniform(0.0, ceiling)
        return max(jitter, retry_after or 0.0)

    def _flush_batch(self) -> tuple[int, int, list[dict[str, Any]]]:
        sent = 0
        dropped = 0
        alerts: list[dict[str, Any]] = []
        for entry in self.buffer.peek(_BATCH_SIZE):
            result = self.sink.send(entry.reading)
            if result.status is SendStatus.SENT:
                self.buffer.ack([entry.id])
                sent += 1
                alerts.extend(result.alerts)
                self._sink_attempt = 0
            elif result.status is SendStatus.DROP:
                self.buffer.ack([entry.id])
                dropped += 1
                _LOGGER.error("Dropping invalid reading from API response: %s", result.error or "request rejected")
            elif result.status is SendStatus.FATAL:
                raise FatalBridgeError(
                    f"Server rejected the configured VIN or API key: {result.error or 'request rejected'}",
                )
            else:
                self._pending_delay = self._backoff(self._sink_attempt, result.retry_after)
                self._sink_attempt += 1
                break
        return sent, dropped, alerts

    @staticmethod
    def _print_alerts(alerts: list[dict[str, Any]]) -> None:
        for alert in alerts:
            severity = alert.get("severity", "info")
            if isinstance(severity, dict):
                severity = severity.get("value", "info")
            message = alert.get("message", "")
            print(f"[{str(severity).upper()}] {message}")

    def run(self, max_ticks: int | None = None) -> None:
        if max_ticks is not None and max_ticks < 0:
            raise ValueError("max_ticks must be non-negative")
        ticks = 0
        try:
            while max_ticks is None or ticks < max_ticks:
                self.tick()
                ticks += 1
                if max_ticks is not None and ticks >= max_ticks:
                    break
                delay = self._pending_delay or self.interval_s
                self._pending_delay = 0.0
                self.sleep(delay)
        except KeyboardInterrupt:
            self._flush_shutdown()

    def _flush_shutdown(self) -> None:
        deadline = self.clock() + 5.0
        attempts = 0
        while len(self.buffer) and attempts < 5 and self.clock() < deadline:
            before = len(self.buffer)
            self._pending_delay = 0.0
            try:
                _sent, _dropped, alerts = self._flush_batch()
            except FatalBridgeError as exc:
                _LOGGER.error("%s", exc)
                return
            self._print_alerts(alerts)
            if len(self.buffer) < before:
                attempts = 0
                continue
            delay = self._pending_delay
            if delay <= 0 or self.clock() + delay > deadline:
                return
            self.sleep(delay)
            attempts += 1
