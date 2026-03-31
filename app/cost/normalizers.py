from __future__ import annotations

from decimal import Decimal
from typing import Any

from app.core.cost_window import round_currency


def summarize_daily_rows_to_points(daily_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    points: list[dict[str, Any]] = []
    for row in daily_rows:
        total = Decimal("0")
        for value in (row.get("by_service") or {}).values():
            total += Decimal(str(value))
        points.append({"date": row.get("date"), "total_cost": round_currency(total)})
    return points

