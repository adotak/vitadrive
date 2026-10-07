# VitaDrive

**Vehicle vitals monitoring, threshold warnings, predictive maintenance and a full service record for modern cars.**

**Live:** https://vitadrive.vercel.app

VitaDrive collects telemetry from a car (OEM telematics / CAN gateway, OBD-II dongle, or the built-in simulator),
checks every reading against safety thresholds, raises warnings and critical alerts, predicts when each
maintenance item will be due, estimates remaining driving range, and keeps a permanent history of readings,
alerts and services. It runs as a headless service with a REST API, so it can be built into an in-vehicle
infotainment unit, a fleet backend, or a phone companion app.

![Sign-in screen](docs/signin.jpg)

![Dashboard](docs/dashboard.png)

## Features

| Area | What it does |
|---|---|
| **Monitoring** | 20+ vitals: engine oil level/life/pressure, coolant temp/level, brake fluid, front/rear pad thickness, 4x tire pressure (TPMS), tread depth, 12V battery, traction-battery charge/health/temp, fuel, transmission temp, washer fluid, OBD-II trouble codes (DTCs). |
| **Threshold alerts** | Warning and critical limits per metric (low and/or high), filtered by powertrain (ICE / hybrid / EV). Safety-critical DTCs (misfire, ABS, airbag, HV system) are escalated. Alerts are de-duplicated, resolve automatically when a reading returns to normal, and can be dismissed. |
| **Predictive maintenance** | 15 standard service items with distance *and* time intervals (whichever comes first). Distance limits are converted into calendar dates using the car's observed daily mileage. Wear items (brake pads, tires, oil life) use a regression of the sensor value vs. odometer to predict the exact date the limit will be hit. |
| **Range estimate** | Remaining km from fuel/charge level using *learned* consumption from recent driving (refuel/recharge events ignored), falling back to rated consumption for new cars. |
| **Records** | Permanent log (Postgres in production, SQLite locally) of every reading, alert and service (date, odometer, cost, shop, notes). Logging a service resets its schedule and resolves related alerts. |
| **Integrations** | REST API (OpenAPI docs at `/docs`), OBD-II adapter (ELM327 via `python-obd`), realistic simulator, web dashboard, CLI. |
| **Accounts & security** | Clerk login on the dashboard. Each user only sees their own vehicles, and every car sends data with its own API key. |
| **Installable app** | Web app manifest and icons, so the dashboard can be installed from the browser ("Add to Home Screen" / "Install app") and opens full-screen like a native app. |
| **Deployment** | Vercel (serverless) + Supabase Postgres. SQLite still works for local use. See [docs/DEPLOY.md](docs/DEPLOY.md). |

## Quick start

```bash
pip install -e ".[dev]"
vitadrive demo                 # seeds 180 days of simulated data for a petrol car and an EV
vitadrive report VDDEMOICE0000001
vitadrive serve                # dashboard at http://127.0.0.1:8000, API docs at /docs
```

Live data from a car with an OBD-II dongle:

```bash
pip install -e ".[obd]"
vitadrive obd <VIN> --odometer 42000   # odometer only needed if the car doesn't report PID 0xA6
```

## Integrating with a vehicle

Register the vehicle, issue it an API key, then have the car's gateway push a reading whenever new data is
available (any field can be omitted). The examples below use a local server without login; on a deployed
instance, register the car and get its key from the dashboard instead.

```bash
curl -X PUT localhost:8000/api/vehicles/1HGCM82633A004352 -H 'content-type: application/json' \
  -d '{"vin":"1HGCM82633A004352","make":"Honda","model":"Civic","year":2024,"powertrain":"ice","tank_capacity_l":47}'

KEY=$(curl -s -X POST localhost:8000/api/vehicles/1HGCM82633A004352/api-key | python3 -c 'import sys,json;print(json.load(sys.stdin)["api_key"])')

curl -X POST localhost:8000/api/vehicles/1HGCM82633A004352/readings -H "X-API-Key: $KEY" -H 'content-type: application/json' \
  -d '{"vin":"1HGCM82633A004352","odometer_km":42010,"engine_oil_life_pct":12,"tire_pressure_fl_kpa":195,"dtc_codes":["P0420"]}'
# -> returns any newly raised alerts
```

| Endpoint | Purpose |
|---|---|
| `PUT /api/vehicles/{vin}` | Register / update a vehicle |
| `POST /api/vehicles/{vin}/api-key` | Issue a new ingest API key (shown once) |
| `POST /api/vehicles/{vin}/readings` | Ingest telemetry (`X-API-Key` header), returns new alerts |
| `GET /api/vehicles/{vin}/report` | Overall status, latest vitals, active alerts, maintenance forecast, range |
| `GET /api/vehicles/{vin}/maintenance` | Due date / km remaining for every service item |
| `GET /api/vehicles/{vin}/range` | Remaining range estimate |
| `GET/POST /api/vehicles/{vin}/services` | Service history / log a service |
| `GET /api/vehicles/{vin}/alerts?active_only=true` | Alert log |
| `POST /api/alerts/{id}/ack` | Dismiss an alert |

## Going live

Deploy on **Vercel** with a **Supabase** Postgres database and **Clerk** login. The step-by-step guide is in
[docs/DEPLOY.md](docs/DEPLOY.md). Settings are read from environment variables (see `.env.example`):
`DATABASE_URL`, `CLERK_PUBLISHABLE_KEY`, `CLERK_AUTHORIZED_PARTIES`.

Troubleshooting:

- **Every page says `{"detail":"Not Found"}`**: check that the Vercel *Framework Preset* is **FastAPI** and that the
  entrypoint is the root `app.py`, then redeploy without the build cache.
- **"Token issued for an unauthorized origin" after signing in**: `CLERK_AUTHORIZED_PARTIES` doesn't list the
  address the login came from (the error names it). Set it to your site URL, e.g. `https://vitadrive.vercel.app`,
  and redeploy.
- **"Development mode" under the sign-in box**: Clerk test keys (`pk_test_...`) are in use. Production keys
  require your own domain.

## Customising

Thresholds live in `vitadrive/thresholds.py` and the service schedule in `vitadrive/maintenance.py`. Pass your
own rule set / schedule to `HealthMonitor(rules=...)` and `MaintenancePlanner(schedule=...)` to match a specific
manufacturer's specs.

```
vitadrive/
  models.py           data models (Vehicle, SensorReading, Alert, ServiceRecord, ...)
  thresholds.py       warning/critical limits per metric
  monitor.py          threshold + DTC evaluation
  maintenance.py      schedule and due-date prediction
  range_estimator.py  remaining range
  storage.py          SQLite / PostgreSQL history (SQLAlchemy)
  auth.py             Clerk session verification
  demo.py             simulated demo vehicles
  service.py          orchestration (ingest, report)
  api.py              FastAPI REST API + dashboard
  cli.py              command line
  adapters/           simulator, OBD-II
```

## Development

```bash
ruff check . && pytest
# include the Postgres tests:
docker run -d -e POSTGRES_PASSWORD=devpass -e POSTGRES_DB=vitadrive -p 5432:5432 postgres:16
VITADRIVE_TEST_POSTGRES=postgresql://postgres:devpass@localhost:5432/vitadrive pytest
```

## Limitations

Standard OBD-II only exposes some metrics (coolant temp, fuel level, voltage, DTCs, odometer on newer cars).
Fluid levels, pad wear, TPMS and HV-battery data are manufacturer-specific and should be sent through the REST API
from the vehicle's own telematics or CAN gateway. Default thresholds and intervals are generic; always follow the
owner's manual for a specific vehicle.

## License

MIT
