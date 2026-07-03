from __future__ import annotations

from datetime import date
import time
from typing import Iterable

from models import EpsRecord, Ticker, as_float
from retry import is_retryable_exception, raise_for_http_response, retry_transient


YAHOO_LOOKBACK_COLUMNS = {
    "7d": "7daysAgo",
    "30d": "30daysAgo",
    "60d": "60daysAgo",
    "90d": "90daysAgo",
    "7daysAgo": "7daysAgo",
    "30daysAgo": "30daysAgo",
    "60daysAgo": "60daysAgo",
    "90daysAgo": "90daysAgo",
}

YAHOO_PROVIDER_HISTORY_COLUMNS = {
    "7daysAgo": 7,
    "30daysAgo": 30,
    "60daysAgo": 60,
    "90daysAgo": 90,
}

YAHOO_ESTIMATE_FIELDS = {
    "avg": "avg",
    "high": "high",
    "low": "low",
}


def fetch_yahoo_eps_trend(
    tickers: Iterable[Ticker],
    *,
    period: str,
    lookback: str,
    as_of: date,
    delay_seconds: float = 5.0,
    max_retries: int = 4,
    retry_base_delay: float = 15.0,
    retry_max_delay: float = 180.0,
) -> list[EpsRecord]:
    """Fetch Yahoo Finance EPS trend data through yfinance.

    Yahoo exposes relative periods such as 0q, +1q, 0y, +1y and columns like
    current, 7daysAgo, 30daysAgo, 60daysAgo, 90daysAgo. yfinance versions vary:
    some expose a dataframe method, while others require parsing quoteSummary.
    """

    try:
        import yfinance as yf
    except ImportError as exc:
        raise SystemExit(
            "yfinance is not installed. Run: pip install -r requirements.txt"
        ) from exc

    lookback_column = YAHOO_LOOKBACK_COLUMNS.get(lookback)
    if lookback_column is None:
        valid = ", ".join(sorted(YAHOO_LOOKBACK_COLUMNS))
        raise SystemExit(f"Unsupported lookback '{lookback}'. Use one of: {valid}")

    records: list[EpsRecord] = []
    for item in tickers:
        symbol = item.symbol.strip().upper()
        if not symbol:
            continue

        try:
            record = retry_transient(
                lambda: _fetch_yahoo_eps_trend_once(
                    yf,
                    item,
                    period=period,
                    lookback_column=lookback_column,
                    as_of=as_of,
                ),
                label=f"{symbol} Yahoo EPS trend",
                retries=max_retries,
                base_delay_seconds=retry_base_delay,
                max_delay_seconds=retry_max_delay,
            )
            records.append(record)
        except Exception as exc:
            records.append(
                EpsRecord(
                    symbol=symbol,
                    name=item.name,
                    provider="yahoo",
                    period=period,
                    as_of=as_of,
                    current_eps=None,
                    error=str(exc),
                )
            )

        if delay_seconds > 0:
            time.sleep(delay_seconds)

    return records


def fetch_yahoo_estimates(
    tickers: Iterable[Ticker],
    *,
    period: str,
    field: str,
    lookback: str,
    as_of: date,
    delay_seconds: float = 5.0,
    max_retries: int = 4,
    retry_base_delay: float = 15.0,
    retry_max_delay: float = 180.0,
) -> list[EpsRecord]:
    """Fetch Yahoo Finance EPS estimate data through yfinance."""

    try:
        import yfinance as yf
    except ImportError as exc:
        raise SystemExit(
            "yfinance is not installed. Run: pip install -r requirements.txt"
        ) from exc

    estimate_key = YAHOO_ESTIMATE_FIELDS.get(field)
    if estimate_key is None:
        valid = ", ".join(sorted(YAHOO_ESTIMATE_FIELDS))
        raise SystemExit(f"Unsupported field '{field}'. Use one of: {valid}")

    lookback_column = YAHOO_LOOKBACK_COLUMNS.get(lookback)
    if lookback_column is None:
        valid = ", ".join(sorted(YAHOO_LOOKBACK_COLUMNS))
        raise SystemExit(f"Unsupported lookback '{lookback}'. Use one of: {valid}")

    records: list[EpsRecord] = []
    for item in tickers:
        symbol = item.symbol.strip().upper()
        if not symbol:
            continue

        try:
            record = retry_transient(
                lambda: _fetch_yahoo_estimate_once(
                    yf,
                    item,
                    period=period,
                    field=field,
                    estimate_key=estimate_key,
                    lookback_column=lookback_column,
                    as_of=as_of,
                ),
                label=f"{symbol} Yahoo EPS estimate",
                retries=max_retries,
                base_delay_seconds=retry_base_delay,
                max_delay_seconds=retry_max_delay,
            )
            records.append(record)
        except Exception as exc:
            records.append(
                EpsRecord(
                    symbol=symbol,
                    name=item.name,
                    provider="yahoo",
                    period=period,
                    as_of=as_of,
                    current_eps=None,
                    market_cap=item.market_cap,
                    error=str(exc),
                )
            )

        if delay_seconds > 0:
            time.sleep(delay_seconds)

    return records


def _fetch_yahoo_eps_trend_once(
    yf: object,
    item: Ticker,
    *,
    period: str,
    lookback_column: str,
    as_of: date,
) -> EpsRecord:
    symbol = item.symbol.strip().upper()
    yahoo_ticker = yf.Ticker(symbol)
    row = _get_eps_trend_row(yahoo_ticker, symbol, period)
    if row is None:
        return EpsRecord(
            symbol=symbol,
            name=item.name,
            provider="yahoo",
            period=period,
            as_of=as_of,
            current_eps=None,
            error="empty EPS trend",
        )

    return EpsRecord(
        symbol=symbol,
        name=item.name,
        provider="yahoo",
        period=period,
        as_of=as_of,
        current_eps=as_float(row.get("current")),
        previous_eps=as_float(row.get(lookback_column)),
        previous_label=lookback_column,
        provider_history_by_day=_provider_history_from_trend_row(row),
        eps_trend_current=as_float(row.get("current")),
        eps_trend_7d_ago=as_float(row.get("7daysAgo")),
        eps_trend_30d_ago=as_float(row.get("30daysAgo")),
        eps_trend_60d_ago=as_float(row.get("60daysAgo")),
        eps_trend_90d_ago=as_float(row.get("90daysAgo")),
    )


def _fetch_yahoo_estimate_once(
    yf: object,
    item: Ticker,
    *,
    period: str,
    field: str,
    estimate_key: str,
    lookback_column: str,
    as_of: date,
) -> EpsRecord:
    symbol = item.symbol.strip().upper()
    yahoo_ticker = yf.Ticker(symbol)
    estimate_row = _get_earnings_estimate_row(yahoo_ticker, symbol, period)
    trend_row = _get_eps_trend_row(yahoo_ticker, symbol, period)

    current_eps = None
    previous_eps = None
    previous_label = ""
    analysts = None
    estimate_avg = None
    estimate_low = None
    estimate_high = None
    year_ago_eps = None
    growth = None
    eps_trend_current = None

    if estimate_row is not None:
        estimate_avg = as_float(estimate_row.get("avg"))
        estimate_low = as_float(estimate_row.get("low"))
        estimate_high = as_float(estimate_row.get("high"))
        year_ago_eps = as_float(estimate_row.get("yearAgoEps"))
        growth = as_float(estimate_row.get("growth"))
        current_eps = as_float(estimate_row.get(estimate_key))
        analysts = _as_int(estimate_row.get("numberOfAnalysts"))
    if trend_row is not None:
        eps_trend_current = as_float(trend_row.get("current"))
    if field == "avg" and trend_row is not None:
        if eps_trend_current is not None:
            current_eps = eps_trend_current
        previous_eps = as_float(trend_row.get(lookback_column))
        previous_label = lookback_column

    if current_eps is None:
        return EpsRecord(
            symbol=symbol,
            name=item.name,
            provider="yahoo",
            period=period,
            as_of=as_of,
            current_eps=None,
            market_cap=item.market_cap,
            error="empty EPS estimate",
        )

    return EpsRecord(
        symbol=symbol,
        name=item.name,
        provider="yahoo",
        period=period,
        as_of=as_of,
        current_eps=current_eps,
        market_cap=item.market_cap,
        previous_eps=previous_eps,
        previous_label=previous_label,
        analysts=analysts,
        provider_history_by_day=_provider_history_from_trend_row(trend_row),
        estimate_avg=estimate_avg,
        estimate_low=estimate_low,
        estimate_high=estimate_high,
        year_ago_eps=year_ago_eps,
        growth=growth,
        eps_trend_current=eps_trend_current,
        eps_trend_7d_ago=as_float(trend_row.get("7daysAgo")) if trend_row is not None else None,
        eps_trend_30d_ago=as_float(trend_row.get("30daysAgo")) if trend_row is not None else None,
        eps_trend_60d_ago=as_float(trend_row.get("60daysAgo")) if trend_row is not None else None,
        eps_trend_90d_ago=as_float(trend_row.get("90daysAgo")) if trend_row is not None else None,
    )


def _provider_history_from_trend_row(row: dict[str, object] | None) -> dict[int, float]:
    if row is None:
        return {}

    history: dict[int, float] = {}
    for column, days in YAHOO_PROVIDER_HISTORY_COLUMNS.items():
        value = as_float(row.get(column))
        if value is not None:
            history[days] = value
    return history


def _get_eps_trend_row(yahoo_ticker: object, symbol: str, period: str) -> dict[str, object] | None:
    dataframe_row = _get_eps_trend_from_dataframe(yahoo_ticker, period)
    if dataframe_row is not None:
        return dataframe_row
    return _get_eps_trend_from_quote_summary(yahoo_ticker, symbol, period)


def _get_earnings_estimate_row(yahoo_ticker: object, symbol: str, period: str) -> dict[str, object] | None:
    dataframe_row = _get_earnings_estimate_from_dataframe(yahoo_ticker, period)
    if dataframe_row is not None:
        return dataframe_row
    return _get_earnings_estimate_from_quote_summary(yahoo_ticker, symbol, period)


def _get_eps_trend_from_dataframe(yahoo_ticker: object, period: str) -> dict[str, object] | None:
    for method_name in ("get_eps_trend", "get_earnings_trend"):
        method = getattr(yahoo_ticker, method_name, None)
        if method is None:
            continue
        try:
            df = method()
        except Exception as exc:
            if is_retryable_exception(exc):
                raise
            continue
        if df is None or getattr(df, "empty", False):
            continue
        if "period" in df.columns:
            df = df.set_index("period")
        df.index = df.index.map(str)
        if period not in df.index:
            continue
        row = df.loc[period]
        if hasattr(row, "iloc") and getattr(row, "ndim", 1) > 1:
            row = row.iloc[0]
        return dict(row)
    return None


def _get_earnings_estimate_from_dataframe(
    yahoo_ticker: object,
    period: str,
) -> dict[str, object] | None:
    for method_name in ("get_earnings_estimate",):
        method = getattr(yahoo_ticker, method_name, None)
        if method is None:
            continue
        try:
            df = method()
        except Exception as exc:
            if is_retryable_exception(exc):
                raise
            continue
        if df is None or getattr(df, "empty", False):
            continue
        if "period" in df.columns:
            df = df.set_index("period")
        df.index = df.index.map(str)
        if period not in df.index:
            continue
        row = df.loc[period]
        if hasattr(row, "iloc") and getattr(row, "ndim", 1) > 1:
            row = row.iloc[0]
        return dict(row)
    return None


def _get_eps_trend_from_quote_summary(
    yahoo_ticker: object,
    symbol: str,
    period: str,
) -> dict[str, object] | None:
    item = _get_yahoo_trend_item(yahoo_ticker, symbol, period)
    if item is None:
        return None
    eps_trend = item.get("epsTrend") or {}
    return {key: _raw_value(value) for key, value in eps_trend.items()}


def _get_earnings_estimate_from_quote_summary(
    yahoo_ticker: object,
    symbol: str,
    period: str,
) -> dict[str, object] | None:
    item = _get_yahoo_trend_item(yahoo_ticker, symbol, period)
    if item is None:
        return None
    estimate = item.get("earningsEstimate") or {}
    return {key: _raw_value(value) for key, value in estimate.items()}


def _get_yahoo_trend_item(yahoo_ticker: object, symbol: str, period: str) -> dict[str, object] | None:
    cache = getattr(yahoo_ticker, "_eps_scanner_trend_cache", None)
    if cache is None:
        cache = {}
        setattr(yahoo_ticker, "_eps_scanner_trend_cache", cache)
    if period in cache:
        return cache[period]

    data = getattr(yahoo_ticker, "_data", None)
    if data is None or not hasattr(data, "get"):
        return None

    url = f"https://query2.finance.yahoo.com/v10/finance/quoteSummary/{symbol}"
    response = data.get(url, params={"modules": "earningsTrend"}, timeout=30)
    raise_for_http_response(response, provider="Yahoo", context="quoteSummary")

    payload = response.json()
    result = (payload.get("quoteSummary") or {}).get("result") or []
    if not result:
        cache[period] = None
        return None
    trend = ((result[0].get("earningsTrend") or {}).get("trend")) or []
    for item in trend:
        if str(item.get("period")) == period:
            cache[period] = item
            return item
    cache[period] = None
    return None


def _raw_value(value: object) -> object:
    if isinstance(value, dict) and "raw" in value:
        return value["raw"]
    return value


def _as_int(value: object) -> int | None:
    number = as_float(value)
    return int(number) if number is not None else None
