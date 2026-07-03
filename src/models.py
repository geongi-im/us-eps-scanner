from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
import math


@dataclass(frozen=True)
class Ticker:
    symbol: str
    name: str = ""
    market_cap: float | None = None


@dataclass
class EpsRecord:
    symbol: str
    name: str
    provider: str
    period: str
    as_of: date
    current_eps: float | None
    market_cap: float | None = None
    previous_eps: float | None = None
    previous_label: str = ""
    analysts: int | None = None
    provider_history_by_day: dict[int, float] = field(default_factory=dict)
    estimate_avg: float | None = None
    estimate_low: float | None = None
    estimate_high: float | None = None
    year_ago_eps: float | None = None
    growth: float | None = None
    eps_trend_current: float | None = None
    eps_trend_7d_ago: float | None = None
    eps_trend_30d_ago: float | None = None
    eps_trend_60d_ago: float | None = None
    eps_trend_90d_ago: float | None = None
    error: str = ""

    @property
    def change_pct(self) -> float | None:
        return percent_change(self.current_eps, self.previous_eps)


@dataclass
class ScannerRow:
    record: EpsRecord
    previous_eps_by_day: dict[int, float] = field(default_factory=dict)
    previous_label_by_day: dict[int, str] = field(default_factory=dict)
    change_pct_by_day: dict[int, float] = field(default_factory=dict)


def as_float(value: object) -> float | None:
    if value is None:
        return None
    try:
        if isinstance(value, str):
            value = value.replace(",", "").strip()
            if value in {"", "-", "None", "null", "nan", "NaN"}:
                return None
        result = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(result):
        return None
    return result


def percent_change(current: float | None, previous: float | None) -> float | None:
    if current is None or previous is None:
        return None
    if previous == 0 or math.isnan(previous):
        return None
    return (current - previous) / abs(previous) * 100.0
