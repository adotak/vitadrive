"""Public web data about a car model: a Wikipedia photo and NHTSA safety recalls (no API keys needed)."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from datetime import datetime
from typing import Any, Optional, TypeVar, cast

from pydantic import BaseModel

USER_AGENT = "VitaDrive/0.1 (+https://github.com/adotak/vitadrive)"
WIKI_SUMMARY_URL = "https://en.wikipedia.org/api/rest_v1/page/summary/"
WIKI_API_URL = "https://en.wikipedia.org/w/api.php"
NHTSA_RECALLS_URL = "https://api.nhtsa.gov/recalls/recallsByVehicle"
_CACHE_TTL_S = 6 * 3600
_cache: dict[tuple[object, ...], tuple[float, object]] = {}
_T = TypeVar("_T")


class WebServiceError(Exception):
    """A public data source could not be reached."""


class CarPhoto(BaseModel):
    title: str
    description: Optional[str] = None
    image: str
    page_url: str


class Recall(BaseModel):
    campaign: str
    reported_on: Optional[str] = None
    component: str
    summary: str
    consequence: Optional[str] = None
    remedy: Optional[str] = None
    park_it: bool = False
    park_outside: bool = False
    over_the_air: bool = False


class RecallsOut(BaseModel):
    make: str
    model: str
    year: int
    source: str = "NHTSA (US)"
    lookup_url: str
    recalls: list[Recall]


def _get_json(url: str, timeout: float = 10) -> Any:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 (fixed https hosts)
        return json.load(resp)


def _cached(key: tuple[object, ...], fn: Callable[[], _T]) -> _T:
    hit = _cache.get(key)
    if hit and time.monotonic() - hit[0] < _CACHE_TTL_S:
        return cast(_T, hit[1])
    val = fn()
    _cache[key] = (time.monotonic(), val)
    return val


def _wiki_url(title: str) -> str:
    return f"https://en.wikipedia.org/wiki/{urllib.parse.quote(title.replace(' ', '_'))}"


def _wider(url: str) -> str:
    """Wikimedia thumbnails come at 330px; ask for a sharper 960px copy of the same file."""
    return url.replace("/330px-", "/960px-") if "/thumb/" in url else url


def _photo_from_summary(title: str) -> Optional[CarPhoto]:
    try:
        d = _get_json(WIKI_SUMMARY_URL + urllib.parse.quote(title.replace(" ", "_"), safe=""))
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        raise
    thumb = (d.get("thumbnail") or {}).get("source")
    if d.get("type") != "standard" or not thumb:
        return None
    return CarPhoto(title=d["title"], description=d.get("description"), image=_wider(thumb),
                    page_url=(d.get("content_urls") or {}).get("desktop", {}).get("page") or _wiki_url(d["title"]))


def _photo_from_search(make: str, model: str) -> Optional[CarPhoto]:
    params = {"action": "query", "format": "json", "generator": "search", "gsrsearch": f"{make} {model} car",
              "gsrlimit": 1, "prop": "pageimages|description|info", "piprop": "thumbnail", "pithumbsize": 960,
              "inprop": "url"}
    found = _get_json(f"{WIKI_API_URL}?{urllib.parse.urlencode(params)}").get("query") or {}
    pages = list(found.get("pages", {}).values())
    if not pages or not (pages[0].get("thumbnail") or {}).get("source"):
        return None
    p = pages[0]
    if make.lower() not in p["title"].lower():
        return None
    return CarPhoto(title=p["title"], description=p.get("description"), image=p["thumbnail"]["source"],
                    page_url=p.get("fullurl") or _wiki_url(p["title"]))


def car_photo(make: str, model: str) -> Optional[CarPhoto]:
    """Photo of the model from its Wikipedia article: exact ``Make Model`` title first, then a search."""
    def run() -> CarPhoto | None:
        try:
            return _photo_from_summary(f"{make} {model}") or _photo_from_search(make, model)
        except Exception:
            raise WebServiceError("Wikipedia is unavailable right now.") from None

    return _cached(("photo", make.strip().lower(), model.strip().lower()), run)


def _date(s: Optional[str]) -> Optional[str]:
    try:
        return datetime.strptime(s, "%d/%m/%Y").date().isoformat() if s else None
    except ValueError:
        return None


def recalls(make: str, model: str, year: int) -> RecallsOut:
    """Safety recalls NHTSA has published for this make, model and year, newest first."""
    def run() -> RecallsOut:
        q = urllib.parse.urlencode({"make": make, "model": model, "modelYear": year})
        try:
            d = _get_json(f"{NHTSA_RECALLS_URL}?{q}", timeout=12)
        except urllib.error.HTTPError as exc:
            if exc.code in (400, 404):
                d = {"results": []}
            else:
                raise WebServiceError("The recall database is unavailable right now.") from None
        except Exception:
            raise WebServiceError("The recall database is unavailable right now.") from None
        items = [Recall(campaign=r.get("NHTSACampaignNumber") or "", reported_on=_date(r.get("ReportReceivedDate")),
                        component=(r.get("Component") or "Other").replace(":", " · ").title(),
                        summary=r.get("Summary") or "", consequence=r.get("Consequence"), remedy=r.get("Remedy"),
                        park_it=bool(r.get("parkIt")), park_outside=bool(r.get("parkOutSide")),
                        over_the_air=bool(r.get("overTheAirUpdate")))
                 for r in d.get("results") or []]
        items.sort(key=lambda r: r.reported_on or "", reverse=True)
        return RecallsOut(make=make, model=model, year=year, recalls=items,
                          lookup_url="https://www.nhtsa.gov/recalls")

    return _cached(("recalls", make.strip().lower(), model.strip().lower(), year), run)
