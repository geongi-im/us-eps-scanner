from __future__ import annotations

import csv
from pathlib import Path

from models import Ticker, as_float
from retry import retry_transient


US_MAJOR_EXCHANGES = ["NMS", "NYQ", "NCM", "NGM", "ASE"]


def fetch_top_us_market_cap_tickers(
    limit: int = 100,
    *,
    max_retries: int = 4,
    retry_base_delay: float = 15.0,
    retry_max_delay: float = 180.0,
) -> list[Ticker]:
    """Fetch a US-listed equity universe sorted by intraday market cap.

    This uses yfinance's Yahoo screener wrapper. It is free/no-key, but can be
    rate-limited by Yahoo, so callers should cache the output.
    """

    try:
        import yfinance as yf
        from yfinance import EquityQuery
    except ImportError as exc:
        raise SystemExit(
            "yfinance is not installed. Run: pip install -r requirements.txt"
        ) from exc
    except Exception as exc:
        raise SystemExit(f"Installed yfinance does not expose screener APIs: {exc}") from exc

    if not hasattr(yf, "screen"):
        raise SystemExit("Installed yfinance does not support screen(). Run: pip install -U yfinance")

    query = EquityQuery(
        "and",
        [
            EquityQuery("eq", ["region", "us"]),
            EquityQuery("is-in", ["exchange", *US_MAJOR_EXCHANGES]),
            EquityQuery("gt", ["intradaymarketcap", 0]),
        ],
    )

    response = retry_transient(
        lambda: yf.screen(
            query,
            size=min(max(limit * 2, limit), 250),
            sortField="intradaymarketcap",
            sortAsc=False,
        ),
        label="Yahoo screener",
        retries=max_retries,
        base_delay_seconds=retry_base_delay,
        max_delay_seconds=retry_max_delay,
    )
    quotes = _extract_quotes(response)
    tickers: list[Ticker] = []
    seen: set[str] = set()
    seen_names: set[str] = set()
    for quote in quotes:
        symbol = str(quote.get("symbol") or "").strip().upper()
        if not symbol or symbol in seen:
            continue
        market_cap = _quote_number(quote, "intradaymarketcap", "marketCap")
        if market_cap is None:
            continue
        name = str(quote.get("shortName") or quote.get("longName") or "").strip()
        name_key = _normalize_company_name(name)
        if name_key and name_key in seen_names:
            continue
        tickers.append(Ticker(symbol=symbol, name=name, market_cap=market_cap))
        seen.add(symbol)
        if name_key:
            seen_names.add(name_key)
        if len(tickers) >= limit:
            break

    if not tickers:
        raise RuntimeError("Yahoo screener returned no market-cap-ranked tickers")
    return tickers


def write_tickers_csv(path: str | Path, tickers: list[Ticker]) -> None:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["symbol", "name", "market_cap"])
        writer.writeheader()
        for ticker in tickers:
            writer.writerow(
                {
                    "symbol": ticker.symbol,
                    "name": ticker.name,
                    "market_cap": f"{ticker.market_cap:.0f}" if ticker.market_cap is not None else "",
                }
            )


def _extract_quotes(response: object) -> list[dict[str, object]]:
    if isinstance(response, dict):
        quotes = response.get("quotes")
        if isinstance(quotes, list):
            return quotes

        finance = response.get("finance")
        if isinstance(finance, dict):
            result = finance.get("result")
            if isinstance(result, list) and result:
                quotes = result[0].get("quotes")
                if isinstance(quotes, list):
                    return quotes
    return []


def _quote_number(quote: dict[str, object], *keys: str) -> float | None:
    for key in keys:
        value = quote.get(key)
        if isinstance(value, dict):
            value = value.get("raw")
        number = as_float(value)
        if number is not None:
            return number
    return None


def _normalize_company_name(name: str) -> str:
    return "".join(ch.lower() for ch in name if ch.isalnum())
