"""Command line interface: ``vitadrive --help``."""
from __future__ import annotations

import argparse
import os
import sys
import time

from .demo import DEMO_VEHICLES, seed_vehicle
from .models import Severity
from .service import VitaDrive
from .storage import LOCAL_OWNER, Store

_COLORS = {Severity.OK: "\033[32m", Severity.INFO: "\033[36m", Severity.WARNING: "\033[33m",
           Severity.CRITICAL: "\033[31m"}
_RESET = "\033[0m"


def _c(sev: Severity, text: str) -> str:
    return f"{_COLORS[sev]}{text}{_RESET}" if sys.stdout.isatty() else text


def cmd_demo(args) -> None:
    vd = VitaDrive(Store(args.db))
    for i, vehicle in enumerate(DEMO_VEHICLES):
        count = seed_vehicle(vd, vehicle, owner_id=args.owner, days=args.days, seed=args.seed + i,
                             start_odometer_km=30_000 + i * 12_000)
        print(f"Seeded {count} readings for {vehicle.year} {vehicle.make} {vehicle.model} ({vehicle.vin})")
    print("Run `vitadrive serve` to open the dashboard.")


def cmd_api_key(args) -> None:
    store = Store(args.db)
    if store.get_vehicle(args.vin) is None:
        sys.exit(f"Unknown vehicle {args.vin}")
    print(store.rotate_api_key(args.vin))


def cmd_report(args) -> None:
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


def cmd_serve(args) -> None:
    import uvicorn

    os.environ["DATABASE_URL"] = args.db
    uvicorn.run("vitadrive.api:get_app", factory=True, host=args.host, port=args.port)


def cmd_obd(args) -> None:
    from .adapters.obd import OBDAdapter

    vd = VitaDrive(Store(args.db))
    adapter = OBDAdapter(args.vin, port=args.port, fallback_odometer_km=args.odometer)
    while True:
        for alert in vd.ingest(adapter.read()):
            print(f"[{alert.severity.value.upper()}] {alert.message}")
        time.sleep(args.interval)


def main(argv=None) -> None:
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

    s = sub.add_parser("serve", help="Run the REST API and dashboard")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8000)
    s.set_defaults(fn=cmd_serve)

    o = sub.add_parser("obd", help="Stream live data from an OBD-II adapter")
    o.add_argument("vin")
    o.add_argument("--port", default=None, help="Serial port, auto-detected if omitted")
    o.add_argument("--odometer", type=float, default=None, help="Odometer if the car doesn't report it")
    o.add_argument("--interval", type=float, default=30)
    o.set_defaults(fn=cmd_obd)

    args = p.parse_args(argv)
    args.fn(args)


if __name__ == "__main__":
    main()
