"""REST API + web dashboard. Run locally with ``vitadrive serve``; on Vercel via the root ``app.py``."""
from __future__ import annotations

import logging
import os
import secrets
from datetime import date
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlalchemy import text
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.cors import CORSMiddleware

from . import __version__, assistant, costs, nearby, observability, push, retention, webinfo
from .auth import AuthError, ClerkAuth
from .calendar import maintenance_calendar
from .demo import DEMO_VEHICLES, seed_vehicle
from .models import (
    Alert,
    CostSummary,
    FuelLog,
    MaintenanceForecast,
    RangeEstimate,
    SensorReading,
    ServiceRecord,
    Vehicle,
)
from .ratelimit import Limit, RateLimited, RateLimiter, default_limits
from .security import SecurityHeadersMiddleware, build_csp
from .service import HealthReport, UnknownVehicle, VitaDrive
from .storage import Store, current_db_revision

_STATIC = Path(__file__).parent / "static"
_log = logging.getLogger("vitadrive")
_ERROR_CODES = {
    400: "bad_request",
    401: "unauthorized",
    403: "forbidden",
    404: "not_found",
    409: "conflict",
    422: "validation_error",
    429: "rate_limited",
    502: "upstream_error",
    503: "unavailable",
    500: "internal_error",
}


def _request_id(request: Request) -> str | None:
    return getattr(request.state, "request_id", None) or observability.request_id_var.get()


def _error_response(
    request: Request,
    status: int,
    detail,
    message: str,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    request_id = _request_id(request)
    response_headers = dict(headers or {})
    if request_id is not None:
        response_headers["X-Request-ID"] = request_id
    return JSONResponse(
        status_code=status,
        content={
            "detail": jsonable_encoder(detail),
            "error": {
                "status": status,
                "code": _ERROR_CODES.get(status, "error"),
                "message": message,
                "request_id": request_id,
            },
        },
        headers=response_headers,
    )

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
    cost: float | None = None
    shop: str | None = None
    notes: str | None = None


class FuelIn(BaseModel):
    filled_on: date
    odometer_km: float = Field(ge=0)
    amount: float = Field(gt=0, le=2000)
    cost: float = Field(ge=0, le=100_000)
    station: str | None = Field(default=None, max_length=120)


class ApiKeyOut(BaseModel):
    vin: str
    api_key: str


class ConfigOut(BaseModel):
    auth_enabled: bool
    clerk_publishable_key: str | None
    clerk_frontend_api: str | None
    assistant_enabled: bool = False
    push_public_key: str | None = None


class PushTestOut(BaseModel):
    sent: int


class AskIn(BaseModel):
    question: str = Field(min_length=1, max_length=500)


class AskOut(BaseModel):
    answer: str


def _default_db() -> str:
    url = os.environ.get("DATABASE_URL") or os.environ.get("VITADRIVE_DB")
    if url:
        return url
    if os.environ.get("VERCEL"):
        raise RuntimeError("DATABASE_URL must be set on Vercel (e.g. your Supabase Postgres connection string)")
    return "vitadrive.db"


def create_app(db: str | None = None, auth: ClerkAuth | None = None, llm=None,
               limits: dict[str, Limit] | None = None) -> FastAPI:
    observability.configure_logging()
    sentry_active = observability.init_sentry()
    app = FastAPI(title="VitaDrive", version=__version__,
                  description="Vehicle vitals monitoring, alerting and predictive maintenance.")
    auth = auth or ClerkAuth.from_env()
    cors_origins = [
        origin.strip()
        for origin in os.environ.get("CORS_ORIGINS", "").split(",")
        if origin.strip() and origin.strip() != "*"
    ]
    if cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=cors_origins,
            allow_credentials=False,
            allow_methods=["GET", "POST", "PUT", "DELETE"],
            allow_headers=["Authorization", "Content-Type", "X-API-Key", "X-Request-ID"],
            expose_headers=["X-Request-ID", "Retry-After"],
        )
    app.add_middleware(observability.RequestIDMiddleware, sentry_active=sentry_active)
    app.add_middleware(SecurityHeadersMiddleware, csp=build_csp(auth.frontend_api))
    vd = VitaDrive(Store(db or _default_db()))
    app_limits = default_limits()
    if limits is not None:
        app_limits.update(limits)
    rate_limiter = (None if os.environ.get("VITADRIVE_RATE_LIMITS", "1").strip().lower() in {"0", "false"}
                    else RateLimiter(vd.store))
    app.state.vitadrive = vd
    app.state.rate_limiter = rate_limiter

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException):
        message = exc.detail if isinstance(exc.detail, str) else str(exc.detail)
        return _error_response(request, exc.status_code, exc.detail, message, exc.headers)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        errors = exc.errors()
        first = errors[0] if errors else {}
        location = ".".join(str(part) for part in first.get("loc", ()))
        message = f"{location}: {first.get('msg', 'Invalid value')}" if location else first.get("msg", "Invalid value")
        return _error_response(request, 422, errors, message)

    @app.exception_handler(RateLimited)
    async def rate_limit_error(request: Request, exc: RateLimited):
        return _error_response(
            request,
            429,
            str(exc),
            str(exc),
            {"Retry-After": str(exc.retry_after)},
        )

    @app.exception_handler(Exception)
    async def unhandled_error(request: Request, _exc: Exception):
        token = observability.request_id_var.set(_request_id(request))
        try:
            _log.exception("Unhandled request exception")
        finally:
            observability.request_id_var.reset(token)
        return _error_response(request, 500, "Internal server error", "Internal server error")

    def current_user(authorization: str | None = Header(default=None)) -> str:
        try:
            return auth.user_id(authorization)
        except AuthError as exc:
            raise HTTPException(401, str(exc)) from None

    def hit_limit(name: str, subject: str) -> None:
        if rate_limiter is not None:
            rate_limiter.hit(app_limits[name], subject)

    def limited_user(name: str):
        def dependency(user: str = Depends(current_user)) -> str:
            hit_limit(name, user)
            return user
        return dependency

    def owned(vin: str, user: str = Depends(current_user)) -> str:
        """Resolve ``vin`` only if it belongs to the signed-in user (404 otherwise, to avoid leaking VINs)."""
        if vd.store.vehicle_owner(vin) != user:
            raise HTTPException(404, "Vehicle not registered")
        return vin

    def ingest_vin(vin: str, x_api_key: str | None = Header(default=None),
                   authorization: str | None = Header(default=None)) -> str:
        """Cars/gateways authenticate with the vehicle's API key; signed-in owners may also post."""
        if x_api_key:
            if vd.store.vin_for_api_key(x_api_key) != vin:
                raise HTTPException(401, "Invalid API key for this vehicle")
            return vin
        return owned(vin, current_user(authorization))

    def guard(fn, *args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except UnknownVehicle:
            raise HTTPException(404, "Vehicle not registered") from None

    app.mount("/static", StaticFiles(directory=_STATIC), name="static")

    @app.get("/favicon.ico", include_in_schema=False)
    def favicon():
        return FileResponse(_STATIC / "icon-192.png")

    @app.get("/manifest.webmanifest", include_in_schema=False)
    def manifest():
        return FileResponse(_STATIC / "manifest.webmanifest", media_type="application/manifest+json")

    @app.get("/sw.js", include_in_schema=False)
    def service_worker():
        return FileResponse(_STATIC / "sw.js", media_type="text/javascript", headers={"Cache-Control": "no-cache"})

    @app.get("/", include_in_schema=False)
    def dashboard():
        return FileResponse(_STATIC / "index.html")

    @app.get("/api/config", response_model=ConfigOut)
    def config():
        return ConfigOut(auth_enabled=auth.enabled, clerk_publishable_key=auth.publishable_key,
                         clerk_frontend_api=auth.frontend_api,
                         assistant_enabled=llm is not None or assistant.enabled(), push_public_key=push.public_key())

    @app.get("/api/health", include_in_schema=False)
    def health():
        try:
            with vd.store.transaction() as connection:
                connection.execute(text("SELECT 1")).scalar_one()
            revision = current_db_revision(vd.store.engine)
        except Exception:
            _log.exception("Database health check failed")
            return JSONResponse(
                status_code=503,
                content={"ok": False, "version": __version__, "database": "error"},
                headers={"Cache-Control": "no-store"},
            )
        return JSONResponse(
            content={"ok": True, "version": __version__, "database": "ok", "db_revision": revision},
            headers={"Cache-Control": "no-store"},
        )

    @app.get("/api/vehicles", response_model=list[Vehicle])
    def list_vehicles(user: str = Depends(current_user)):
        return vd.store.list_vehicles(owner_id=user)

    @app.put("/api/vehicles/{vin}", response_model=Vehicle)
    def register_vehicle(vin: str, vehicle: Vehicle, user: str = Depends(current_user)):
        if vehicle.vin != vin:
            raise HTTPException(400, "VIN in path and body must match")
        owner = vd.store.vehicle_owner(vin)
        if owner is not None and owner != user:
            raise HTTPException(409, "This VIN is registered to another account")
        return vd.store.upsert_vehicle(vehicle, owner_id=user)

    @app.delete("/api/vehicles/{vin}", status_code=204)
    def delete_vehicle(vin: str = Depends(owned)):
        vd.store.delete_vehicle(vin)

    @app.post("/api/vehicles/{vin}/api-key", response_model=ApiKeyOut)
    def issue_api_key(vin: str = Depends(owned), user: str = Depends(limited_user("api_key"))):
        """Create (or replace) the key the car uses to send readings. It is only shown once."""
        return ApiKeyOut(vin=vin, api_key=vd.store.rotate_api_key(vin))

    @app.post("/api/demo", response_model=list[Vehicle], status_code=201)
    def add_demo_vehicles(user: str = Depends(limited_user("demo"))):
        """Add two simulated vehicles with 90 days of history to the signed-in account."""
        created = []
        for i, template in enumerate(DEMO_VEHICLES):
            vehicle = template.model_copy(update={"vin": "VD" + template.vin[2:6] + secrets.token_hex(5).upper()})
            seed_vehicle(vd, vehicle, owner_id=user, days=90, per_day=1, seed=secrets.randbelow(10_000),
                         start_odometer_km=30_000 + i * 12_000)
            created.append(vehicle)
        return created

    @app.post("/api/vehicles/{vin}/readings", response_model=list[Alert], status_code=201)
    def ingest(reading: SensorReading, vin: str = Depends(ingest_vin)):
        if reading.vin != vin:
            raise HTTPException(400, "VIN in path and body must match")
        hit_limit("readings", vin)
        alerts = guard(vd.ingest, reading)
        push.notify_critical(vd.store, vin, alerts)
        return alerts

    @app.get("/api/vehicles/{vin}/readings", response_model=list[SensorReading])
    def readings(vin: str = Depends(owned), limit: int = Query(200, ge=1, le=1000)):
        return vd.store.readings(vin, limit=limit)

    @app.get("/api/vehicles/{vin}/alerts", response_model=list[Alert])
    def alerts(vin: str = Depends(owned), active_only: bool = False, limit: int = Query(200, ge=1, le=1000)):
        return vd.store.alerts(vin, include_acknowledged=not active_only, limit=limit)

    @app.post("/api/alerts/{alert_id}/ack", status_code=204)
    def ack(alert_id: int, user: str = Depends(current_user)):
        vin = vd.store.alert_vin(alert_id)
        if vin is None or vd.store.vehicle_owner(vin) != user:
            raise HTTPException(404, "Alert not found")
        vd.store.acknowledge_alert(alert_id)

    @app.get("/api/vehicles/{vin}/services", response_model=list[ServiceRecord])
    def services(vin: str = Depends(owned)):
        return vd.store.services(vin)

    @app.post("/api/vehicles/{vin}/services", response_model=ServiceRecord, status_code=201)
    def add_service(body: ServiceIn, vin: str = Depends(owned)):
        with vd.store.transaction():
            record = vd.store.add_service(ServiceRecord(vin=vin, **body.model_dump()))
            related = _SERVICE_RESOLVES.get(body.item)
            if related:
                for alert in vd.store.alerts(vin, include_acknowledged=False, limit=1000):
                    if alert.metric.startswith(related):
                        vd.store.acknowledge_alert(alert.id)
        return record

    @app.get("/api/vehicles/{vin}/fuel", response_model=list[FuelLog])
    def fuel_logs(vin: str = Depends(owned)):
        return vd.store.fuel_logs(vin)

    @app.post("/api/vehicles/{vin}/fuel", response_model=FuelLog, status_code=201)
    def add_fuel_log(body: FuelIn, vin: str = Depends(owned)):
        return vd.store.add_fuel_log(FuelLog(vin=vin, **body.model_dump()))

    @app.delete("/api/vehicles/{vin}/fuel/{log_id}", status_code=204)
    def delete_fuel_log(log_id: int, vin: str = Depends(owned)):
        if not vd.store.delete_fuel_log(vin, log_id):
            raise HTTPException(404, "Fill-up not found")

    @app.get("/api/vehicles/{vin}/costs", response_model=CostSummary)
    def cost_summary(vin: str = Depends(owned)):
        return costs.summarize(vd.store.get_vehicle(vin), vd.store.fuel_logs(vin))

    @app.get("/api/vehicles/{vin}/maintenance", response_model=list[MaintenanceForecast])
    def maintenance(vin: str = Depends(owned)):
        return guard(vd.maintenance, vin)

    @app.get("/api/vehicles/{vin}/maintenance/calendar.ics", response_class=Response)
    def calendar(vin: str = Depends(owned)):
        today = date.today()
        forecasts = guard(vd.maintenance, vin, today=today)
        if not forecasts:
            raise HTTPException(409, "Add a sensor reading before exporting maintenance dates")
        content = maintenance_calendar(vd.store.get_vehicle(vin), forecasts, today=today)
        return Response(content, media_type="text/calendar",
                        headers={"Content-Disposition": 'attachment; filename="vitadrive-maintenance.ics"',
                                 "Cache-Control": "private, no-store"})

    @app.get("/api/vehicles/{vin}/range", response_model=RangeEstimate | None)
    def range_(vin: str = Depends(owned)):
        return guard(vd.range, vin)

    @app.get("/api/vehicles/{vin}/report", response_model=HealthReport)
    def report(vin: str = Depends(owned)):
        return guard(vd.report, vin)

    @app.post("/api/vehicles/{vin}/ask", response_model=AskOut)
    def ask(body: AskIn, vin: str = Depends(owned), user: str = Depends(current_user)):
        if llm is None and not assistant.enabled():
            raise HTTPException(503, "Ask VitaDrive is off: set OPENAI_API_KEY in the server environment")
        hit_limit("ask", user)
        hit_limit("ask_daily", user)
        rep = guard(vd.report, vin)
        try:
            return AskOut(answer=assistant.ask(rep, body.question, llm=llm))
        except Exception as exc:  # provider errors (bad key, quota, timeout) shouldn't surface as a bare 500
            raise HTTPException(502, f"AI provider error: {type(exc).__name__}") from None

    @app.get("/api/vehicles/{vin}/nearby", response_model=nearby.NearbyOut)
    def nearby_places(vin: str = Depends(owned), kind: str | None = None,
                      lat: float | None = Query(None, ge=-90, le=90), lon: float | None = Query(None, ge=-180, le=180),
                      q: str | None = Query(None, min_length=2, max_length=200),
                      user: str = Depends(current_user)):
        """Shops that can fix what this car needs. Without a location, only the suggested needs are returned."""
        if kind is not None and kind not in nearby.KINDS:
            raise HTTPException(422, f"kind must be one of: {', '.join(nearby.KINDS)}")
        needs = nearby.needs(guard(vd.report, vin))
        kind = kind or (needs[0].kind if needs else "mechanic")
        out = nearby.NearbyOut(kind=kind, label=nearby.KINDS[kind][0], needs=needs, places=[])
        if (lat is None or lon is None) and not q:
            return out
        hit_limit("nearby", user)
        try:
            if lat is None or lon is None:
                lat, lon = nearby.geocode(q)
            out.radius_km, out.places = nearby.search(lat, lon, kind)
        except nearby.PlaceNotFound as exc:
            raise HTTPException(404, str(exc)) from None
        except nearby.MapServiceError as exc:
            raise HTTPException(502, str(exc)) from None
        out.lat, out.lon = lat, lon
        return out

    @app.post("/api/push/subscriptions", status_code=204)
    def push_subscribe(sub: push.PushSubscription, user: str = Depends(current_user)):
        """Remember this browser so critical alerts on the user's cars reach it as notifications."""
        if not push.enabled():
            raise HTTPException(503, "Alerts are off: set VAPID_PUBLIC_KEY and VAPID_PRIVATE_KEY on the server")
        vd.store.save_push_subscription(user, sub.model_dump())

    @app.delete("/api/push/subscriptions", status_code=204)
    def push_unsubscribe(endpoint: str = Query(max_length=2000), user: str = Depends(current_user)):
        vd.store.delete_push_subscription(endpoint, owner_id=user)

    @app.post("/api/push/test", response_model=PushTestOut)
    def push_test(user: str = Depends(current_user)):
        return PushTestOut(sent=push.send_to_user(vd.store, user, {
            "title": "VitaDrive alerts are on", "body": "You'll get a notification here when a reading turns critical.",
            "url": "/", "tag": "vitadrive-test"}, ttl=600))

    @app.get("/api/vehicles/{vin}/photo", response_model=webinfo.CarPhoto | None)
    def car_photo(vin: str = Depends(owned)):
        """A photo of this car's model from Wikipedia, or null when none is found or Wikipedia is down."""
        v = vd.store.get_vehicle(vin)
        try:
            return webinfo.car_photo(v.make, v.model)
        except webinfo.WebServiceError:
            return None

    @app.get("/api/vehicles/{vin}/recalls", response_model=webinfo.RecallsOut)
    def car_recalls(vin: str = Depends(owned)):
        """Safety recalls published by NHTSA for this car's make, model and year."""
        v = vd.store.get_vehicle(vin)
        try:
            return webinfo.recalls(v.make, v.model, v.year)
        except webinfo.WebServiceError as exc:
            raise HTTPException(502, str(exc)) from None

    @app.get("/api/cron/prune", response_model=retention.PruneResult, include_in_schema=False)
    def cron_prune(authorization: str | None = Header(default=None)):
        secret = os.environ.get("CRON_SECRET")
        if not secret:
            raise HTTPException(503, "Cron is off")
        if authorization is None or not secrets.compare_digest(authorization, f"Bearer {secret}"):
            raise HTTPException(401, "Invalid cron authorization")
        return retention.prune(vd.store)

    return app


_app: FastAPI | None = None


def get_app() -> FastAPI:
    global _app
    if _app is None:
        _app = create_app()
    return _app
