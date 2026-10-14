"""Portable iCalendar snapshots of a vehicle's current maintenance forecast."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from hashlib import sha256

from .models import MaintenanceForecast, Vehicle


def _text(value: str) -> str:
    value = value.replace("\r\n", "\n").replace("\r", "\n")
    value = "".join(c for c in value if c == "\n" or ord(c) >= 32)
    return value.replace("\\", "\\\\").replace("\n", "\\n").replace(";", "\\;").replace(",", "\\,")


def _fold(line: str) -> str:
    """RFC 5545: at most 75 octets per physical line, without splitting UTF-8 characters."""
    parts, current, size = [], "", 0
    for char in line:
        width = len(char.encode("utf-8"))
        if size + width > 75:
            parts.append(current)
            current, size = " ", 1
        current += char
        size += width
    parts.append(current)
    return "\r\n".join(parts)


def maintenance_calendar(
    vehicle: Vehicle,
    forecasts: list[MaintenanceForecast],
    *,
    now: datetime | None = None,
    today: date | None = None,
) -> str:
    now = now or datetime.now().astimezone()
    today = today or now.date()
    stamp = now.astimezone(timezone.utc)
    car = f"{vehicle.year} {vehicle.make} {vehicle.model} (…{vehicle.vin[-4:]})"
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//VitaDrive//Maintenance//EN", "CALSCALE:GREGORIAN"]
    for forecast in sorted(forecasts, key=lambda f: (f.due_date, f.item)):
        overdue = forecast.due_date < today or (forecast.km_remaining is not None and forecast.km_remaining < 0)
        day = today if overdue else forecast.due_date
        summary = f"{'OVERDUE: ' if overdue else ''}{forecast.label} — {car}"
        description = [
            car,
            f"Predicted due date: {forecast.due_date.isoformat()}",
            f"Basis: {forecast.reason}",
        ]
        if forecast.due_km is not None:
            description.append(f"Due odometer: {forecast.due_km:,.0f} km (or the date above, whichever comes first).")
        if overdue:
            description.append("Overdue maintenance is shown today; arrange service as soon as possible.")
        description.append("Forecast snapshot, not a live subscription. Check VitaDrive for changes. "
                           "Follow your owner's manual; do not wait for a reminder if a warning appears.")
        uid = sha256(f"{vehicle.vin}\0{forecast.item}".encode()).hexdigest()
        lines.extend([
            "BEGIN:VEVENT",
            f"UID:{uid}@vitadrive",
            f"DTSTAMP:{stamp:%Y%m%dT%H%M%SZ}",
            f"DTSTART;VALUE=DATE:{day:%Y%m%d}",
            f"DTEND;VALUE=DATE:{day + timedelta(days=1):%Y%m%d}",
            f"SUMMARY:{_text(summary)}",
            f"DESCRIPTION:{_text(chr(10).join(description))}",
            "TRANSP:TRANSPARENT",
        ])
        if day > today:
            lines.extend(["BEGIN:VALARM", "ACTION:DISPLAY", "TRIGGER:-P1D",
                          f"DESCRIPTION:{_text(forecast.label)}", "END:VALARM"])
        lines.append("END:VEVENT")
    lines.append("END:VCALENDAR")
    return "\r\n".join(_fold(line) for line in lines) + "\r\n"
