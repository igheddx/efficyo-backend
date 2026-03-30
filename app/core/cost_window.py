"""Central cost context: windows, Cost Explorer metric, date ranges (UTC), and rounding.

All headline AWS spend totals (summary, insights, EC2-Other breakdown, 30-day trends) use the
same rolling window and UnblendedCost so numbers stay aligned. Cost Explorer ``End`` dates are
exclusive (AWS API semantics).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Literal

# Primary headline total (summary, insights, savings % denominator, EC2-Other NAT context).
DEFAULT_ACCOUNT_COST_WINDOW: Literal["rolling_30d"] = "rolling_30d"
ACCOUNT_ROLLING_WINDOW_DAYS = 30
DEFAULT_COST_METRIC = "UnblendedCost"

_CURRENCY_QUANT = Decimal("0.01")
_PERCENT_QUANT = Decimal("0.01")


def utc_today() -> date:
    """Calendar 'today' in UTC for Cost Explorer time periods (avoids host-TZ skew)."""
    return datetime.now(timezone.utc).date()


def account_cost_window_label() -> str:
    return "Rolling last 30 days"


def ce_time_period(start_inclusive: date, end_exclusive: date) -> dict[str, str]:
    return {"Start": start_inclusive.isoformat(), "End": end_exclusive.isoformat()}


def ce_rolling_period_days(days: int) -> tuple[date, date]:
    """
    Rolling window as Cost Explorer TimePeriod: Start inclusive, End exclusive.

    Span is ``days`` full day buckets ending the day before ``utc_today()`` (End exclusive).
    """
    d = max(1, int(days))
    end_exclusive = utc_today()
    start_inclusive = end_exclusive - timedelta(days=d)
    return start_inclusive, end_exclusive


def account_default_ce_period() -> tuple[date, date]:
    """Same window as headline account cost summary (rolling 30d, UTC)."""
    return ce_rolling_period_days(ACCOUNT_ROLLING_WINDOW_DAYS)


def ce_metrics() -> list[str]:
    return [DEFAULT_COST_METRIC]


def round_currency_decimal(value: Decimal) -> Decimal:
    return value.quantize(_CURRENCY_QUANT, rounding=ROUND_HALF_UP)


def round_currency(value: Decimal | float | str | int) -> float:
    """Round monetary amounts to 2 decimal places (half up)."""
    d = value if isinstance(value, Decimal) else Decimal(str(value))
    return float(round_currency_decimal(d))


def round_percentage(value: Decimal | float | str | int) -> float:
    """Round percentage values to 2 decimal places (half up)."""
    d = value if isinstance(value, Decimal) else Decimal(str(value))
    return float(d.quantize(_PERCENT_QUANT, rounding=ROUND_HALF_UP))


def account_cost_window_fields() -> dict[str, str]:
    return {
        "cost_window": DEFAULT_ACCOUNT_COST_WINDOW,
        "cost_window_label": account_cost_window_label(),
        "cost_metric": DEFAULT_COST_METRIC,
    }


def rolling_nd_window_fields(days: int) -> dict[str, str]:
    d = max(1, int(days))
    return {
        "cost_window": "rolling_nd",
        "cost_window_label": f"Rolling last {d} days",
        "cost_metric": DEFAULT_COST_METRIC,
    }


def wow_ce_14d_window_fields() -> dict[str, str]:
    return {
        "cost_window": "wow_ce_14d",
        "cost_window_label": (
            "Week-over-week (14 days of Cost Explorer daily data, UnblendedCost)"
        ),
        "cost_metric": DEFAULT_COST_METRIC,
    }


def savings_outcomes_nd_window_fields(days: int) -> dict[str, str]:
    d = max(1, int(days))
    return {
        "cost_window": "savings_outcomes_nd",
        "cost_window_label": f"Last {d} days (verified savings outcomes; not Cost Explorer spend)",
        "cost_metric": "",
    }


@dataclass(frozen=True)
class AccountCostContext:
    """Resolved defaults for account-level Cost Explorer pulls."""

    cost_window: str
    cost_window_label: str
    cost_metric: str
    start_date_inclusive: date
    end_date_exclusive: date

    @property
    def cost_period_start_iso(self) -> str:
        return self.start_date_inclusive.isoformat()

    @property
    def cost_period_end_iso(self) -> str:
        return self.end_date_exclusive.isoformat()

    def ce_time_period(self) -> dict[str, str]:
        return ce_time_period(self.start_date_inclusive, self.end_date_exclusive)


def default_account_cost_context() -> AccountCostContext:
    start, end = account_default_ce_period()
    f = account_cost_window_fields()
    return AccountCostContext(
        cost_window=f["cost_window"],
        cost_window_label=f["cost_window_label"],
        cost_metric=f["cost_metric"],
        start_date_inclusive=start,
        end_date_exclusive=end,
    )
