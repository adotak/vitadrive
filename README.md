# VitaDrive

**Vehicle vitals monitoring, threshold warnings, predictive maintenance and a full service record for modern cars.**

VitaDrive collects telemetry from a car (OEM telematics / CAN gateway, OBD-II dongle, or the built-in simulator),
checks every reading against safety thresholds, raises warnings and critical alerts, predicts when each
maintenance item will be due, estimates remaining driving range, and keeps a permanent history of readings,
alerts and services. It runs as a headless service with a REST API, so it can be built into an in-vehicle
infotainment unit, a fleet backend, or a phone companion app.

![Dashboard](docs/dashboard.png)

## Features

| Area | What it does |
|---|---|
| **Monitoring** | 20+ vitals: engine oil level/life/pressure, coolant temp/level, brake fluid, front/rear pad thickness, 4x tire pressure (TPMS), tread depth, 12V battery, traction-battery charge/health/temp, fuel, transmission temp, washer fluid, OBD-II trouble codes (DTCs). |
| **Threshold alerts** | Warning and critical limits per metric (low and/or high), filtered by powertrain (ICE / hybrid / EV). Safety-critical DTCs (misfire, ABS, airbag, HV system) are escalated. Alerts are de-duplicated, resolve automatically when a reading returns to normal, and can be dismissed. |
| **Predictive maintenance** | 15 standard service items with distance *and* time intervals (whichever comes first). Distance limits are converted into calendar dates using the car's observed daily mileage. Wear items (brake pads, tires, oil life) use a regression of the sensor value vs. odometer to predict the exact date the limit will be hit. |
| **Range estimate** | Remaining km from fuel/charge level using *learned* consumption from recent driving (refuel/recharge events ignored), falling back to rated consumption for new cars. |
| **Records** | SQLite log of every reading, alert and service (date, odometer, cost, shop, notes). Logging a service resets its schedule and resolves related alerts. |
| **Integrations** | REST API (OpenAPI docs at `/docs`), OBD-II adapter (ELM327 via `python-obd`), realistic simulator, web dashboard, CLI. |

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

Push a reading from the car's gateway whenever new data is available (any field can be omitted):

```bash
curl -X PUT localhost:8000/api/vehicles/1HGCM82633A004352 -H 'content-type: application/json' \
  -d '{"vin":"1HGCM82633A004352","make":"Honda","model":"Civic","year":2024,"powertrain":"ice","tank_capacity_l":47}'

curl -X POST localhost:8000/api/vehicles/1HGCM82633A004352/readings -H 'content-type: application/json' \
  -d '{"vin":"1HGCM82633A004352","odometer_km":42010,"engine_oil_life_pct":12,"tire_pressure_fl_kpa":195,"dtc_codes":["P0420"]}'
# -> returns any newly raised alerts
```

| Endpoint | Purpose |
|---|---|
| `PUT /api/vehicles/{vin}` | Register / update a vehicle |
| `POST /api/vehicles/{vin}/readings` | Ingest telemetry, returns new alerts |
| `GET /api/vehicles/{vin}/report` | Overall status, latest vitals, active alerts, maintenance forecast, range |
| `GET /api/vehicles/{vin}/maintenance` | Due date / km remaining for every service item |
| `GET /api/vehicles/{vin}/range` | Remaining range estimate |
| `GET/POST /api/vehicles/{vin}/services` | Service history / log a service |
| `GET /api/vehicles/{vin}/alerts?active_only=true` | Alert log |
| `POST /api/alerts/{id}/ack` | Dismiss an alert |

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
  storage.py          SQLite history
  service.py          orchestration (ingest, report)
  api.py              FastAPI REST API + dashboard
  cli.py              command line
  adapters/           simulator, OBD-II
```

## Development

```bash
ruff check . && pytest
```

## Limitations

Standard OBD-II only exposes some metrics (coolant temp, fuel level, voltage, DTCs, odometer on newer cars).
Fluid levels, pad wear, TPMS and HV-battery data are manufacturer-specific and should be sent through the REST API
from the vehicle's own telematics or CAN gateway. Default thresholds and intervals are generic; always follow the
owner's manual for a specific vehicle.

## License

MIT
