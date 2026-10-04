"""Command line interface: ``vitadrive --help``."""
from __future__ import annotations

import argparse
import sys
import time

from .adapters.simulator import VehicleSimulator
from .models import Powertrain, ServiceRecord, Severity, Vehicle
from .service import VitaDrive
from .storage import Store

_COLORS = {Severity.OK: "\033[32m", Severity.INFO: "\033[36m", Severity.WARNING: "\033[33m",
           Severity.CRITICAL: "\033[31m"}
_RESET = "\033[0m"


def _c(sev: Severity, text: str) -> str:
    return f"{_COLORS[sev]}{text}{_RESET}" if sys.stdout.isatty() else text


DEMO_VEHICLES = [
    Vehicle(vin="VDDEMOICE0000001", make="Toyota", model="Corolla", year=2023, powertrain=Powertrain.ICE,
            tank_capacity_l=50, rated_consumption=6.5),
    Vehicle(vin="VDDEMOEV00000002", make="Tesla", model="Model 3", year=2024, powertrain=Powertrain.EV,
            battery_capacity_kwh=60, rated_consumption=15),
]


def cmd_demo(args) -> None:
    vd = VitaDrive(Store(args.db))
    for i, vehicle in enumerate(DEMO_VEHICLES):
        vd.store.upsert_vehicle(vehicle)
        sim = VehicleSimulator(vehicle, start_odometer_km=30_000 + i * 12_000, seed=args.seed + i)
        count = 0
        for reading in sim.history(days=args.days):
            vd.ingest(reading)
            count += 1
            if count == 20:
                for item in ("tire_rotation", "cabin_filter", "wiper_blades"):
                    vd.store.add_service(ServiceRecord(
                        vin=vehicle.vin, item=item, performed_on=reading.timestamp.date(),
                        odometer_km=reading.odometer_km, cost=45.0, shop="VitaDrive Demo Garage"))
        print(f"Seeded {count} readings for {vehicle.year} {vehicle.make} {vehicle.model} ({vehicle.vin})")
    print(f"Database: {args.db}. Run `vitadrive serve --db {args.db}` to open the dashboard.")


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
    import os

    import uvicorn

    os.environ["VITADRIVE_DB"] = args.db
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
    p.add_argument("--db", default="vitadrive.db", help="SQLite database path")
    sub = p.add_subparsers(dest="cmd", required=True)

    d = sub.add_parser("demo", help="Seed the database with simulated vehicles")
    d.add_argument("--days", type=int, default=180)
    d.add_argument("--seed", type=int, default=7)
    d.set_defaults(fn=cmd_demo)

    r = sub.add_parser("report", help="Print a vehicle health report")
    r.add_argument("vin")
    r.set_defaults(fn=cmd_report)

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
