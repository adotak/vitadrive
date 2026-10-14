"""Running costs from logged fill-ups and charges: cost per km, real consumption and monthly spend."""
from __future__ import annotations

from datetime import date
from typing import Optional

from .models import CostSummary, FuelLog, MonthCost, Powertrain, Vehicle

MIN_KM = 50  # below this, per-km figures are mostly noise
MONTHS_SHOWN = 6


def _last_months(today: date, n: int) -> list[str]:
    y, m = today.year, today.month
    out = []
    for _ in range(n):
        out.append(f"{y:04d}-{m:02d}")
        y, m = (y - 1, 12) if m == 1 else (y, m - 1)
    return out[::-1]


def summarize(vehicle: Vehicle, logs: list[FuelLog], today: Optional[date] = None) -> CostSummary:
    """Fill-to-fill method: what was bought after the first logged fill is what the car used over that distance."""
    today = today or date.today()
    unit = "kWh" if vehicle.powertrain is Powertrain.EV else "L"
    logs = sorted(logs, key=lambda x: (x.odometer_km, x.filled_on))
    total_cost = sum(x.cost for x in logs)
    total_amount = sum(x.amount for x in logs)

    cost_per_km = per_100 = None
    if len(logs) >= 2 and (km := logs[-1].odometer_km - logs[0].odometer_km) >= MIN_KM:
        cost_per_km = round(sum(x.cost for x in logs[1:]) / km, 4)
        per_100 = round(sum(x.amount for x in logs[1:]) / km * 100, 2)

    by_month: dict[str, float] = {}
    for x in logs:
        key = x.filled_on.strftime("%Y-%m")
        by_month[key] = by_month.get(key, 0.0) + x.cost
    months = [MonthCost(month=k, cost=round(by_month.get(k, 0.0), 2)) for k in _last_months(today, MONTHS_SHOWN)]

    return CostSummary(
        unit=unit, fills=len(logs), total_cost=round(total_cost, 2),
        avg_price_per_unit=round(total_cost / total_amount, 3) if total_amount else None,
        cost_per_km=cost_per_km, consumption_per_100km=per_100,
        this_month_cost=months[-1].cost, months=months,
    )
