"""Find nearby mechanics, tire shops, parts stores and chargers from OpenStreetMap (no API key needed)."""

from __future__ import annotations

import json
import math
import re
import time
import urllib.parse
import urllib.request
from collections.abc import Callable
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from typing import Any, Optional, TypeVar, cast

from pydantic import BaseModel

from .models import Severity
from .service import HealthReport

USER_AGENT = "VitaDrive/0.1 (+https://github.com/adotak/vitadrive)"
OVERPASS_URLS = (
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
)
OVERPASS_TIMEOUT_S = 12
OVERPASS_BUDGET_S = 14
OVERPASS_ROUNDS = 2
OVERPASS_RETRY_DELAY_S = 1.0
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
RADII_M = (10_000, 40_000)
FALLBACK_RADII_M = (5_000, 15_000, 40_000)
NOMINATIM_DELAY_S = 1.1  # Nominatim allows one request per second
MAX_PLACES = 8
_CACHE_TTL_S = 600
_cache: dict[tuple[object, ...], tuple[float, object]] = {}
_T = TypeVar("_T")

# kind -> (label, Overpass tag filters)
KINDS: dict[str, tuple[str, tuple[str, ...]]] = {
    "mechanic": ("Mechanic", ('["shop"="car_repair"]',)),
    "tyres": ("Tire shop", ('["shop"="tyres"]',)),
    "parts": ("Parts store", ('["shop"="car_parts"]',)),
    "dealer": ("Car dealer", ('["shop"="car"]',)),
    "charging": ("EV charger", ('["amenity"="charging_station"]',)),
}

# metric / maintenance-item prefix -> kind that can fix it
_NEED_BY_PREFIX = (
    ("tire", "tyres"),
    ("tires", "tyres"),
    ("brake", "mechanic"),
    ("engine_oil", "mechanic"),
    ("oil_change", "mechanic"),
    ("coolant", "mechanic"),
    ("transmission", "mechanic"),
    ("dtc", "mechanic"),
    ("spark", "mechanic"),
    ("battery_12v", "parts"),
    ("wiper", "parts"),
    ("washer", "parts"),
    ("cabin_filter", "parts"),
    ("air_filter", "parts"),
    ("hv_battery_soc", "charging"),
    ("hv_battery", "dealer"),
    ("fuel", None),
)


_RANK = [Severity.OK, Severity.INFO, Severity.WARNING, Severity.CRITICAL]


class Need(BaseModel):
    kind: str
    label: str
    reason: str


class Place(BaseModel):
    name: str
    kind: str
    lat: float
    lon: float
    distance_km: float
    address: Optional[str] = None
    phone: Optional[str] = None
    website: Optional[str] = None
    opening_hours: Optional[str] = None


class NearbyOut(BaseModel):
    lat: Optional[float] = None
    lon: Optional[float] = None
    kind: str
    label: str
    radius_km: Optional[float] = None
    needs: list[Need]
    places: list[Place]


class MapServiceError(Exception):
    """The map service could not be reached."""


class PlaceNotFound(MapServiceError):
    """A typed location (city, postcode) could not be found."""


def _kind_for(key: str) -> Optional[str]:
    for prefix, kind in _NEED_BY_PREFIX:
        if key.startswith(prefix):
            return kind
    return "mechanic"


def needs(report: HealthReport) -> list[Need]:
    """Shop kinds this car needs right now, worst problem first (alerts, then overdue/soon maintenance)."""
    out: dict[str, Need] = {}
    for a in sorted(report.active_alerts, key=lambda a: _RANK.index(a.severity), reverse=True):
        kind = _kind_for(a.metric)
        if kind and kind not in out:
            out[kind] = Need(kind=kind, label=KINDS[kind][0], reason=a.label)
    for m in report.maintenance:
        if m.status in (Severity.WARNING, Severity.CRITICAL):
            kind = _kind_for(m.item)
            if kind and kind not in out:
                due = "overdue" if m.status == Severity.CRITICAL else "due soon"
                out[kind] = Need(kind=kind, label=KINDS[kind][0], reason=f"{m.label} {due}")
    return list(out.values())


def distance_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * 6371.0 * math.asin(math.sqrt(h))


def _get_json(url: str, data: Optional[bytes] = None, timeout: float = 25) -> Any:
    req = urllib.request.Request(url, data=data, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 (fixed https hosts)
        return json.load(resp)


def _cached(key: tuple[object, ...], fn: Callable[[], _T]) -> _T:
    hit = _cache.get(key)
    if hit and time.monotonic() - hit[0] < _CACHE_TTL_S:
        return cast(_T, hit[1])
    val = fn()
    _cache[key] = (time.monotonic(), val)
    return val


def _overpass_round(body: bytes, timeout: float) -> Optional[list[dict[str, Any]]]:
    pool = ThreadPoolExecutor(len(OVERPASS_URLS))
    pending = {pool.submit(_get_json, url, body, timeout) for url in OVERPASS_URLS}
    try:
        while pending:
            done, pending = wait(pending, timeout=timeout + 1, return_when=FIRST_COMPLETED)
            if not done:
                return None
            for fut in done:
                if fut.exception() is None and isinstance(fut.result(), dict) and "elements" in fut.result():
                    return cast(list[dict[str, Any]], fut.result()["elements"])
        return None
    finally:
        pool.shutdown(wait=False, cancel_futures=True)


def overpass(query: str) -> list[dict[str, Any]]:
    """Ask every Overpass mirror at once and use the first good answer (public mirrors are often busy or slow).

    Busy mirrors usually fail fast, so if a round fails quickly it is retried once.
    """
    body = urllib.parse.urlencode({"data": query}).encode()
    deadline = time.monotonic() + OVERPASS_BUDGET_S
    for attempt in range(OVERPASS_ROUNDS):
        left = deadline - time.monotonic()
        if left < 3:
            break
        if attempt:
            time.sleep(OVERPASS_RETRY_DELAY_S)
        els = _overpass_round(body, min(OVERPASS_TIMEOUT_S, left))
        if els is not None:
            return els
    raise MapServiceError("The free map service is busy right now. Try again in a minute.")


def geocode(q: str) -> tuple[float, float]:
    def run() -> tuple[float, float]:
        url = NOMINATIM_URL + "?" + urllib.parse.urlencode({"q": q, "format": "json", "limit": 1})
        try:
            hits = _get_json(url, timeout=10)
        except Exception:
            raise MapServiceError("Location search is busy right now. Try again in a minute.") from None
        if not hits:
            raise PlaceNotFound(f"Couldn't find '{q}'")
        return float(hits[0]["lat"]), float(hits[0]["lon"])

    return _cached(("geo", q.strip().lower()), run)


def _address(tags: dict[str, Any]) -> Optional[str]:
    street = " ".join(filter(None, (tags.get("addr:housenumber"), tags.get("addr:street"))))
    parts = [p for p in (street, tags.get("addr:city") or tags.get("addr:suburb"), tags.get("addr:postcode")) if p]
    return ", ".join(parts) or None


def _place(el: dict[str, Any], kind: str, lat: float, lon: float) -> Optional[Place]:
    plat = el.get("lat", el.get("center", {}).get("lat"))
    plon = el.get("lon", el.get("center", {}).get("lon"))
    if plat is None or plon is None:
        return None
    tags = el.get("tags", {})
    return Place(
        name=tags.get("name") or tags.get("brand") or tags.get("operator") or KINDS[kind][0],
        kind=kind, lat=plat, lon=plon, distance_km=round(distance_km(lat, lon, plat, plon), 2),
        address=_address(tags), phone=tags.get("phone") or tags.get("contact:phone"),
        website=tags.get("website") or tags.get("contact:website"), opening_hours=tags.get("opening_hours"),
    )


def _nominatim_places(lat: float, lon: float, kind: str, radius_m: int) -> list[Place]:
    """Backup lookup: Nominatim's ``[key=value]`` search inside a box around the user."""
    dlat = radius_m / 111_000
    dlon = dlat / max(math.cos(math.radians(lat)), 0.01)
    places: list[Place] = []
    for f in KINDS[kind][1]:
        k, v = cast(re.Match[str], re.fullmatch(r'\["(.+)"="(.+)"\]', f)).groups()
        params = {"q": f"[{k}={v}]", "format": "jsonv2", "limit": 40, "bounded": 1, "extratags": 1,
                  "addressdetails": 1, "viewbox": f"{lon - dlon},{lat + dlat},{lon + dlon},{lat - dlat}"}
        try:
            hits = _get_json(NOMINATIM_URL + "?" + urllib.parse.urlencode(params), timeout=10)
        except Exception:
            raise MapServiceError("The free map service is busy right now. Try again in a minute.") from None
        for h in hits:
            tags = {**(h.get("extratags") or {}), "name": h.get("name")}
            addr = h.get("address") or {}
            tags.update({"addr:housenumber": addr.get("house_number"), "addr:street": addr.get("road"),
                         "addr:city": addr.get("city") or addr.get("town") or addr.get("village") or addr.get("suburb"),
                         "addr:postcode": addr.get("postcode")})
            place = _place({"lat": float(h["lat"]), "lon": float(h["lon"]), "tags": tags}, kind, lat, lon)
            if place and place.distance_km <= radius_m / 1000:
                places.append(place)
    return places


def _best(places: list[Place], kind: str) -> list[Place]:
    unique = {(round(p.lat, 4), round(p.lon, 4)): p for p in places}.values()
    named = sorted(unique, key=lambda p: (p.name == KINDS[kind][0], p.distance_km))
    return sorted(named[:MAX_PLACES], key=lambda p: p.distance_km)


def search(lat: float, lon: float, kind: str) -> tuple[float, list[Place]]:
    """Closest places of ``kind``, widening the radius until a few are found. Returns (radius_km, places).

    Uses Overpass, and Nominatim when every Overpass mirror is busy.
    """
    filters = KINDS[kind][1]

    def run() -> tuple[float, list[Place]]:
        places: list[Place] = []
        radius = searched = RADII_M[0]
        try:
            for radius in RADII_M:
                sel = "".join(f"nwr{f}(around:{radius},{lat},{lon});" for f in filters)
                els = overpass(f"[out:json][timeout:10];({sel});out center 80;")
                places, searched = [p for p in (_place(e, kind, lat, lon) for e in els) if p], radius
                if len(places) >= 3:
                    break
        except MapServiceError:
            if places:
                return searched / 1000, _best(places, kind)
            for i, radius in enumerate(FALLBACK_RADII_M):
                if i:
                    time.sleep(NOMINATIM_DELAY_S)
                places = _nominatim_places(lat, lon, kind, radius)
                if len(places) >= 3:
                    break
        return radius / 1000, _best(places, kind)

    return _cached((kind, round(lat, 3), round(lon, 3)), run)
