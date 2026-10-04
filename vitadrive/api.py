"""REST API + web dashboard. Run with ``vitadrive serve``."""
from __future__ import annotations

import os
from datetime import date
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from . import __version__
from .models import Alert, MaintenanceForecast, RangeEstimate, SensorReading, ServiceRecord, Vehicle
from .service import HealthReport, UnknownVehicle, VitaDrive
from .storage import Store

_STATIC = Path(__file__).parent / "static"

# Logging one of these services resolves open alerts whose metric starts with the prefix.
_SERVICE_RESOLVES = {
    "oil_change": "engine_oil",
    "brake_pads_front": "brake_pad_front",
    "brake_pads_rear": "brake_pad_rear",
    "tires": "tire_tread",
    "coolant": "coolant_level",
    "brake_fluid": "brake_fluid",
    "battery_12v": "battery_12v",
}


class ServiceIn(BaseModel):
    item: str
    performed_on: date
    odometer_km: float
    cost: Optional[float] = None
    shop: Optional[str] = None
    notes: Optional[str] = None


def create_app(db_path: Optional[str] = None) -> FastAPI:
    app = FastAPI(title="VitaDrive", version=__version__,
                  description="Vehicle vitals monitoring, alerting and predictive maintenance.")
    vd = VitaDrive(Store(db_path or os.environ.get("VITADRIVE_DB", "vitadrive.db")))
    app.state.vitadrive = vd

    def guard(fn, *args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except UnknownVehicle:
            raise HTTPException(404, "Vehicle not registered") from None

    @app.get("/", include_in_schema=False)
    def dashboard():
        return FileResponse(_STATIC / "index.html")

    @app.get("/api/vehicles", response_model=list[Vehicle])
    def list_vehicles():
        return vd.store.list_vehicles()

    @app.put("/api/vehicles/{vin}", response_model=Vehicle)
    def register_vehicle(vin: str, vehicle: Vehicle):
        if vehicle.vin != vin:
            raise HTTPException(400, "VIN in path and body must match")
        return vd.store.upsert_vehicle(vehicle)

    @app.post("/api/vehicles/{vin}/readings", response_model=list[Alert], status_code=201)
    def ingest(vin: str, reading: SensorReading):
        if reading.vin != vin:
            raise HTTPException(400, "VIN in path and body must match")
        return guard(vd.ingest, reading)

    @app.get("/api/vehicles/{vin}/readings", response_model=list[SensorReading])
    def readings(vin: str, limit: int = 200):
        guard(vd._vehicle, vin)
        return vd.store.readings(vin, limit=limit)

    @app.get("/api/vehicles/{vin}/alerts", response_model=list[Alert])
    def alerts(vin: str, active_only: bool = False, limit: int = 200):
        guard(vd._vehicle, vin)
        return vd.store.alerts(vin, include_acknowledged=not active_only, limit=limit)

    @app.post("/api/alerts/{alert_id}/ack", status_code=204)
    def ack(alert_id: int):
        if not vd.store.acknowledge_alert(alert_id):
            raise HTTPException(404, "Alert not found")

    @app.get("/api/vehicles/{vin}/services", response_model=list[ServiceRecord])
    def services(vin: str):
        guard(vd._vehicle, vin)
        return vd.store.services(vin)

    @app.post("/api/vehicles/{vin}/services", response_model=ServiceRecord, status_code=201)
    def add_service(vin: str, body: ServiceIn):
        guard(vd._vehicle, vin)
        record = vd.store.add_service(ServiceRecord(vin=vin, **body.model_dump()))
        related = _SERVICE_RESOLVES.get(body.item)
        if related:
            for alert in vd.store.alerts(vin, include_acknowledged=False, limit=1000):
                if alert.metric.startswith(related):
                    vd.store.acknowledge_alert(alert.id)
        return record

    @app.get("/api/vehicles/{vin}/maintenance", response_model=list[MaintenanceForecast])
    def maintenance(vin: str):
        return guard(vd.maintenance, vin)

    @app.get("/api/vehicles/{vin}/range", response_model=Optional[RangeEstimate])
    def range_(vin: str):
        return guard(vd.range, vin)

    @app.get("/api/vehicles/{vin}/report", response_model=HealthReport)
    def report(vin: str):
        return guard(vd.report, vin)

    return app


app = None


def get_app() -> FastAPI:
    global app
    if app is None:
        app = create_app()
    return app
