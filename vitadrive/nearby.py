"""Find nearby mechanics, tire shops, parts stores and chargers from OpenStreetMap (no API key needed)."""

from __future__ import annotations

import json
import math
import time
import urllib.parse
import urllib.request
from typing import Optional

from pydantic import BaseModel

from .models import Severity
from .service import HealthReport

USER_AGENT = "VitaDrive/0.1 (+https://github.com/adotak/vitadrive)"
OVERPASS_URLS = (
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
)
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
RADII_M = (5_000, 15_000, 40_000)
MAX_PLACES = 8
_CACHE_TTL_S = 600
_cache: dict[tuple, tuple[float, object]] = {}

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


def _get_json(url: str, data: Optional[bytes] = None, timeout: float = 25):
    req = urllib.request.Request(url, data=data, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 (fixed https hosts)
        return json.load(resp)


def _cached(key: tuple, fn):
    hit = _cache.get(key)
    if hit and time.monotonic() - hit[0] < _CACHE_TTL_S:
        return hit[1]
    val = fn()
    _cache[key] = (time.monotonic(), val)
    return val


def overpass(query: str) -> list[dict]:
    body = urllib.parse.urlencode({"data": query}).encode()
    last: Exception | None = None
    for url in OVERPASS_URLS:
        try:
            return _get_json(url, body).get("elements", [])
        except Exception as exc:  # try the mirror
            last = exc
    raise MapServiceError(f"Map service unavailable ({type(last).__name__})")


def geocode(q: str) -> tuple[float, float]:
    def run():
        url = NOMINATIM_URL + "?" + urllib.parse.urlencode({"q": q, "format": "json", "limit": 1})
        try:
            hits = _get_json(url, timeout=10)
        except Exception as exc:
            raise MapServiceError(f"Location search unavailable ({type(exc).__name__})") from None
        if not hits:
            raise PlaceNotFound(f"Couldn't find '{q}'")
        return float(hits[0]["lat"]), float(hits[0]["lon"])

    return _cached(("geo", q.strip().lower()), run)


def _address(tags: dict) -> Optional[str]:
    street = " ".join(filter(None, (tags.get("addr:housenumber"), tags.get("addr:street"))))
    parts = [p for p in (street, tags.get("addr:city") or tags.get("addr:suburb"), tags.get("addr:postcode")) if p]
    return ", ".join(parts) or None


def _place(el: dict, kind: str, lat: float, lon: float) -> Optional[Place]:
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


def search(lat: float, lon: float, kind: str) -> tuple[float, list[Place]]:
    """Closest places of ``kind``, widening the radius until a few are found. Returns (radius_km, places)."""
    filters = KINDS[kind][1]

    def run():
        places: list[Place] = []
        radius = RADII_M[0]
        for radius in RADII_M:
            sel = "".join(f"nwr{f}(around:{radius},{lat},{lon});" for f in filters)
            els = overpass(f"[out:json][timeout:20];({sel});out center 80;")
            places = [p for p in (_place(e, kind, lat, lon) for e in els) if p]
            if len(places) >= 3:
                break
        places.sort(key=lambda p: (p.name == KINDS[kind][0], p.distance_km))
        return radius / 1000, sorted(places[:MAX_PLACES], key=lambda p: p.distance_km)

    return _cached((kind, round(lat, 3), round(lon, 3)), run)
