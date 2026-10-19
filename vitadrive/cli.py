"""Command line interface: ``vitadrive --help``."""
from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import datetime, timezone

from .bridge import (
    ApiSink,
    Bridge,
    DryRunSink,
    FatalBridgeError,
    OfflineBuffer,
    ReadingSink,
    ReadingSource,
    SendResult,
    SendStatus,
    SimulatedSource,
)
from .demo import DEMO_VEHICLES, seed_vehicle
from .models import Powertrain, SensorReading, Severity, Vehicle
from .service import VitaDrive
from .storage import (
    LOCAL_OWNER,
    Store,
    _create_engine,
    current_db_revision,
    stamp_db,
    upgrade_db,
)

_COLORS = {Severity.OK: "\033[32m", Severity.INFO: "\033[36m", Severity.WARNING: "\033[33m",
           Severity.CRITICAL: "\033[31m"}
_RESET = "\033[0m"


def _c(sev: Severity, text: str) -> str:
    return f"{_COLORS[sev]}{text}{_RESET}" if sys.stdout.isatty() else text


def cmd_demo(args: argparse.Namespace) -> None:
    vd = VitaDrive(Store(args.db))
    for i, vehicle in enumerate(DEMO_VEHICLES):
        count = seed_vehicle(vd, vehicle, owner_id=args.owner, days=args.days, seed=args.seed + i,
                             start_odometer_km=30_000 + i * 12_000)
        print(f"Seeded {count} readings for {vehicle.year} {vehicle.make} {vehicle.model} ({vehicle.vin})")
    print("Run `vitadrive serve` to open the dashboard.")


def cmd_api_key(args: argparse.Namespace) -> None:
    store = Store(args.db)
    if store.get_vehicle(args.vin) is None:
        sys.exit(f"Unknown vehicle {args.vin}")
    print(store.rotate_api_key(args.vin))


def cmd_report(args: argparse.Namespace) -> None:
    vd = VitaDrive(Store(args.db))
    r = vd.report(args.vin)
    v = r.vehicle
    print(f"{v.year} {v.make} {v.model}  VIN {v.vin}  status: {_c(r.status, r.status.value.upper())}")
    if r.latest_reading:
        lr = r.latest_reading
        print(f"Odometer: {lr.odometer_km:,.0f} km  (last reading {lr.timestamp:%Y-%m-%d %H:%M})")
    if r.range:
        rg = r.range
        print(f"Range: {_c(rg.status, f'{rg.range_km:.0f} km')} on {rg.level_pct:.0f}% {rg.energy_source}"
              f" ({rg.consumption_per_100km}/100km, {rg.consumption_basis})")
    print("\nActive alerts:")
    for a in r.active_alerts or []:
        print(f"  [{_c(a.severity, a.severity.value.upper())}] {a.timestamp:%Y-%m-%d} {a.message}")
    if not r.active_alerts:
        print("  none")
    print("\nMaintenance schedule:")
    for m in r.maintenance:
        km = f"{m.km_remaining:,.0f} km" if m.km_remaining is not None else "-"
        print(f"  {_c(m.status, m.status.value.upper()):<10} {m.label:<32} due {m.due_date}  "
              f"({m.days_remaining} days, {km})  {m.reason}")


def cmd_vapid_keys(args: argparse.Namespace) -> None:
    from .push import generate_keys

    public, private = generate_keys()
    print(f"VAPID_PUBLIC_KEY={public}\nVAPID_PRIVATE_KEY={private}")
    print("Add both to your server environment (e.g. Vercel). Keep the private key secret.", file=sys.stderr)


def cmd_serve(args: argparse.Namespace) -> None:
    import uvicorn

    os.environ["DATABASE_URL"] = args.db
    uvicorn.run("vitadrive.api:get_app", factory=True, host=args.host, port=args.port)


class _LocalSink:
    def __init__(self, vd: VitaDrive) -> None:
        self.vd = vd

    def send(self, reading: SensorReading) -> SendResult:
        alerts = [alert.model_dump(mode="json") for alert in self.vd.ingest(reading)]
        return SendResult(SendStatus.SENT, alerts=alerts)


def _simulation_vehicle(vin: str, local_vehicle: Vehicle | None = None) -> Vehicle:
    if local_vehicle is not None:
        return local_vehicle
    for vehicle in DEMO_VEHICLES:
        if vehicle.vin == vin:
            return vehicle
    return Vehicle(
        vin=vin,
        make="Simulator",
        model="Vehicle",
        year=datetime.now(timezone.utc).year,
        powertrain=Powertrain.ICE,
        tank_capacity_l=50,
        rated_consumption=7.5,
    )


def cmd_bridge(args: argparse.Namespace) -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    store: Store | None = None
    buffer = OfflineBuffer(":memory:" if args.dry_run else args.buffer)
    vehicle: Vehicle | None = None
    sink: ReadingSink
    try:
        if args.local:
            store = Store(args.db)
            vd = VitaDrive(store)
            vehicle = store.get_vehicle(args.vin)
            if vehicle is None:
                raise SystemExit(f"Unknown vehicle {args.vin}")
            sink = _LocalSink(vd)
        elif args.dry_run:
            sink = DryRunSink()
        else:
            sink = ApiSink(args.url, args.vin, args.api_key)

        source: ReadingSource
        if args.simulate:
            source = SimulatedSource(
                _simulation_vehicle(args.vin, vehicle),
                interval_s=args.interval,
            )
        else:
            from .adapters.obd import OBDAdapter

            source = OBDAdapter(
                args.vin,
                port=args.port,
                fallback_odometer_km=args.odometer,
                baudrate=args.baudrate,
                timeout=args.timeout,
                fast=args.fast,
            )
        bridge = Bridge(source, sink, buffer, interval_s=args.interval)
        bridge.run(max_ticks=args.count)
    except FatalBridgeError as exc:
        raise SystemExit(str(exc)) from exc
    finally:
        buffer.close()
        if store is not None:
            store.close()


def cmd_obd(args: argparse.Namespace) -> None:
    cmd_bridge(argparse.Namespace(
        vin=args.vin,
        url=None,
        api_key=None,
        port=args.port,
        baudrate=args.baudrate,
        timeout=args.timeout,
        fast=args.fast,
        interval=args.interval,
        simulate=False,
        dry_run=False,
        buffer=None,
        count=None,
        local=True,
        odometer=args.odometer,
        db=args.db,
    ))


def cmd_db_upgrade(args: argparse.Namespace) -> None:
    engine = _create_engine(args.db)
    try:
        upgrade_db(engine, args.revision)
    finally:
        engine.dispose()


def cmd_db_stamp(args: argparse.Namespace) -> None:
    engine = _create_engine(args.db)
    try:
        stamp_db(engine, args.revision)
    finally:
        engine.dispose()


def cmd_db_current(args: argparse.Namespace) -> None:
    engine = _create_engine(args.db)
    try:
        print(current_db_revision(engine) or "None")
    finally:
        engine.dispose()


def cmd_prune(args: argparse.Namespace) -> None:
    from .retention import prune

    store = Store(args.db)
    try:
        result = prune(store, older_than_days=args.days, dry_run=args.dry_run)
        print(
            f"Vehicles: {result.vehicles}; days summarized: {result.days_summarized}; "
            f"readings removed: {result.readings_removed}; "
            f"rate-limit rows removed: {result.rate_limit_rows_removed}"
        )
    finally:
        store.close()


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="vitadrive", description="Vehicle vitals monitoring & predictive maintenance")
    p.add_argument("--db", default=os.environ.get("DATABASE_URL", "vitadrive.db"),
                   help="SQLite file path or Postgres URL (default: $DATABASE_URL or vitadrive.db)")
    sub = p.add_subparsers(dest="cmd", required=True)

    d = sub.add_parser("demo", help="Seed the database with simulated vehicles")
    d.add_argument("--days", type=int, default=180)
    d.add_argument("--seed", type=int, default=7)
    d.add_argument("--owner", default=LOCAL_OWNER, help="Owner user ID (Clerk user ID when auth is enabled)")
    d.set_defaults(fn=cmd_demo)

    r = sub.add_parser("report", help="Print a vehicle health report")
    r.add_argument("vin")
    r.set_defaults(fn=cmd_report)

    k = sub.add_parser("api-key", help="Issue a new ingest API key for a vehicle")
    k.add_argument("vin")
    k.set_defaults(fn=cmd_api_key)

    v = sub.add_parser("vapid-keys", help="Generate the key pair that turns on phone/desktop alerts")
    v.set_defaults(fn=cmd_vapid_keys)

    s = sub.add_parser("serve", help="Run the REST API and dashboard")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8000)
    s.set_defaults(fn=cmd_serve)

    o = sub.add_parser("obd", help="Stream live data from an OBD-II adapter")
    o.add_argument("vin")
    o.add_argument("--port", default=None, help="Serial port, auto-detected if omitted")
    o.add_argument("--odometer", type=float, default=None, help="Odometer if the car doesn't report it")
    o.add_argument("--baudrate", type=int, default=None)
    o.add_argument("--timeout", type=float, default=5)
    o.add_argument("--fast", action="store_true")
    o.add_argument("--interval", type=float, default=30)
    o.set_defaults(fn=cmd_obd)

    b = sub.add_parser("bridge", help="Stream readings to a VitaDrive server")
    b.add_argument("vin")
    b.add_argument("--url")
    b.add_argument("--api-key", default=os.environ.get("VITADRIVE_API_KEY"))
    b.add_argument("--port", default=None, help="Serial port, auto-detected if omitted")
    b.add_argument("--odometer", type=float, default=None, help="Fallback odometer when OBD-II does not report it")
    b.add_argument("--baudrate", type=int, default=None)
    b.add_argument("--timeout", type=float, default=5)
    b.add_argument("--fast", action="store_true")
    b.add_argument("--interval", type=float, default=5)
    b.add_argument("--simulate", action="store_true", help="Generate realistic live-style readings without hardware")
    b.add_argument("--dry-run", action="store_true", help="Print reading JSON lines without sending them")
    b.add_argument("--buffer", default=None, help="Offline buffer SQLite path")
    b.add_argument("--count", type=int, default=None, help="Stop after this many readings")
    b.add_argument("--local", action="store_true", help="Ingest readings into the local --db")
    b.set_defaults(fn=cmd_bridge)

    db = sub.add_parser("db", help="Manage database migrations")
    db_sub = db.add_subparsers(dest="db_cmd", required=True)
    db_upgrade = db_sub.add_parser("upgrade", help="Apply database migrations")
    db_upgrade.add_argument("revision", nargs="?", default="head")
    db_upgrade.set_defaults(fn=cmd_db_upgrade)
    db_stamp = db_sub.add_parser("stamp", help="Mark the database at a migration revision")
    db_stamp.add_argument("revision")
    db_stamp.set_defaults(fn=cmd_db_stamp)
    db_current = db_sub.add_parser("current", help="Print the current database revision")
    db_current.set_defaults(fn=cmd_db_current)

    prune_parser = sub.add_parser("prune", help="Compact old readings and remove expired rate limits")
    prune_parser.add_argument("--days", type=int, default=30)
    prune_parser.add_argument("--dry-run", action="store_true")
    prune_parser.set_defaults(fn=cmd_prune)

    args = p.parse_args(argv)
    if args.cmd == "bridge":
        if args.local and args.dry_run:
            p.error("--local and --dry-run cannot be used together")
        if not args.local and not args.dry_run:
            if not args.url:
                p.error("--url is required unless --dry-run or --local is specified")
            if not args.api_key:
                p.error("provide --api-key or set VITADRIVE_API_KEY")
        if args.count is not None and args.count < 0:
            p.error("--count must be non-negative")
        if args.interval <= 0:
            p.error("--interval must be positive")
        if args.timeout <= 0:
            p.error("--timeout must be positive")
        if args.baudrate is not None and args.baudrate <= 0:
            p.error("--baudrate must be positive")
    args.fn(args)


if __name__ == "__main__":
    main()
