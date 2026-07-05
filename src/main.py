from __future__ import annotations

import argparse
import csv
from datetime import date, datetime
import html
import os
from pathlib import Path
import re

from models import EpsRecord, ScannerRow, Ticker, percent_change
from providers import fetch_yahoo_estimates
from universe import fetch_top_us_market_cap_tickers, write_tickers_csv
from utils.logger_util import LoggerUtil
from utils.telegram_util import TelegramUtil


PROVIDER_HISTORY_LABELS = {
    7: "7daysAgo",
    30: "30daysAgo",
    60: "60daysAgo",
    90: "90daysAgo",
}

DEFAULT_OUTPUT_DIR = "output"
DEFAULT_IMAGE_FONT_FAMILY = (
    '"Malgun Gothic", "Noto Sans CJK KR", "Noto Sans KR", "NanumGothic", '
    '"Apple SD Gothic Neo", "UnDotum", Arial, sans-serif'
)

FIELD_LABELS = {
    "avg": "평균",
}


def read_tickers(symbols: str, symbols_file: str | None) -> list[Ticker]:
    tickers: list[Ticker] = []

    if symbols:
        for symbol in symbols.split(","):
            symbol = symbol.strip()
            if symbol:
                tickers.append(Ticker(symbol=symbol))

    if symbols_file:
        with open(symbols_file, newline="", encoding="utf-8-sig") as handle:
            reader = csv.DictReader(handle)
            if "symbol" not in reader.fieldnames:
                raise SystemExit("symbols file must contain a 'symbol' column")
            for row in reader:
                symbol = (row.get("symbol") or "").strip()
                if symbol:
                    market_cap = row.get("market_cap") or row.get("marketCap")
                    tickers.append(
                        Ticker(
                            symbol=symbol,
                            name=(row.get("name") or "").strip(),
                            market_cap=_as_float(market_cap),
                        )
                    )

    seen: set[str] = set()
    unique: list[Ticker] = []
    for ticker in tickers:
        symbol = ticker.symbol.upper()
        if symbol in seen:
            continue
        seen.add(symbol)
        unique.append(Ticker(symbol=symbol, name=ticker.name, market_cap=ticker.market_cap))
    return unique


def rank_records(records: list[EpsRecord], limit: int) -> list[EpsRecord]:
    usable = [
        record
        for record in records
        if record.change_pct is not None and record.current_eps is not None and record.previous_eps is not None
    ]
    usable.sort(
        key=lambda item: item.change_pct if item.change_pct is not None else -999999.0,
        reverse=True,
    )
    return usable[:limit]


def write_csv(records: list[EpsRecord], output: str | None) -> None:
    if output is None:
        return

    fieldnames = [
        "rank",
        "provider",
        "symbol",
        "name",
        "market_cap",
        "period",
        "as_of",
        "current_eps",
        "previous_eps",
        "previous_label",
        "change_pct",
        "analysts",
    ]
    handle = open(output, "w", newline="", encoding="utf-8")
    try:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for index, record in enumerate(records, start=1):
            writer.writerow(
                {
                    "rank": index,
                    "provider": record.provider,
                    "symbol": record.symbol,
                    "name": record.name,
                    "market_cap": f"{record.market_cap:.0f}" if record.market_cap is not None else "",
                    "period": record.period,
                    "as_of": record.as_of.isoformat(),
                    "current_eps": f"{record.current_eps:.4f}",
                    "previous_eps": f"{record.previous_eps:.4f}",
                    "previous_label": record.previous_label,
                    "change_pct": f"{record.change_pct:.2f}",
                    "analysts": record.analysts or "",
                }
            )
    finally:
        handle.close()


def build_scanner_rows(
    records: list[EpsRecord],
    *,
    field: str,
    lookback_days: list[int],
) -> list[ScannerRow]:
    rows: list[ScannerRow] = []
    for record in records:
        if record.current_eps is None or record.error:
            continue

        row = ScannerRow(record=record)
        for days in lookback_days:
            baseline = _provider_baseline(record, field=field, days=days)
            if baseline is None:
                continue

            previous_eps, previous_label = baseline
            change_pct = percent_change(record.current_eps, previous_eps)
            if change_pct is None:
                continue
            row.previous_eps_by_day[days] = previous_eps
            row.previous_label_by_day[days] = previous_label
            row.change_pct_by_day[days] = change_pct

        rows.append(row)
    return rows


def select_scanner_rows(rows: list[ScannerRow], limit: int) -> list[ScannerRow]:
    usable = [row for row in rows if row.change_pct_by_day]
    return usable[:limit]


def write_scanner_csv(rows: list[ScannerRow], output: str | None, *, field: str, lookback_days: list[int]) -> None:
    if output is None:
        return

    fieldnames = [
        "rank",
        "provider",
        "symbol",
        "name",
        "market_cap",
        "field",
        "period",
        "as_of",
        "current_eps",
    ]
    for days in lookback_days:
        fieldnames.extend(
            [
                f"previous_eps_{days}d",
                f"previous_label_{days}d",
                f"change_{days}d_pct",
            ]
        )
    handle = open(output, "w", newline="", encoding="utf-8")
    try:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for index, row in enumerate(rows, start=1):
            record = row.record
            payload = {
                "rank": index,
                "provider": record.provider,
                "symbol": record.symbol,
                "name": record.name,
                "market_cap": f"{record.market_cap:.0f}" if record.market_cap is not None else "",
                "field": field,
                "period": record.period,
                "as_of": record.as_of.isoformat(),
                "current_eps": _format_float(record.current_eps, digits=4),
            }
            for days in lookback_days:
                payload[f"previous_eps_{days}d"] = _format_float(row.previous_eps_by_day.get(days), digits=4)
                payload[f"previous_label_{days}d"] = row.previous_label_by_day.get(days, "")
                payload[f"change_{days}d_pct"] = _format_float(row.change_pct_by_day.get(days), digits=2)
            writer.writerow(payload)
    finally:
        handle.close()


def save_rank_image(
    records: list[EpsRecord],
    *,
    field: str,
    output_dir: str,
    image_output: str | None,
) -> str:
    rows = []
    for index, record in enumerate(records, start=1):
        rows.append(
            {
                "순위": index,
                "종목": _stock_label(record),
                "기준EPS": _format_float(record.previous_eps, digits=4),
                "기준": _html_text(record.previous_label),
                "변화": _format_percent(record.change_pct),
            }
        )
    as_of_label = _rows_as_of(records)
    title = f"{as_of_label} 미국 EPS {FIELD_LABELS.get(field, field)} 변동률 순위"
    subtitle = ""
    return _save_table_image(rows, title=title, subtitle=subtitle, output_dir=output_dir, image_output=image_output)


def save_scanner_image(
    rows: list[ScannerRow],
    *,
    field: str,
    lookback_days: list[int],
    output_dir: str,
    image_output: str | None,
) -> str:
    active_lookback_days = [
        days for days in lookback_days if any(days in row.change_pct_by_day for row in rows)
    ]
    payload = []
    for index, row in enumerate(rows, start=1):
        record = row.record
        item = {
            "순위": index,
            "종목": _stock_label(record),
        }
        for days in active_lookback_days:
            item[f"{days}일"] = _format_percent(row.change_pct_by_day.get(days))
        payload.append(item)

    as_of_label = _scanner_rows_as_of(rows)
    title = f"{as_of_label} 미국 시총 상위 {len(rows)}개 EPS {FIELD_LABELS.get(field, field)} 변동률"
    subtitle = ""
    return _save_table_image(
        payload,
        title=title,
        subtitle=subtitle,
        output_dir=output_dir,
        image_output=image_output,
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Collect EPS trend changes for top US-listed stocks by market cap.",
    )
    parser.add_argument("--symbols", default="", help="Comma-separated tickers, e.g. AAPL,MSFT,NVDA")
    parser.add_argument("--symbols-file", help="CSV file with symbol and optional name columns")
    parser.add_argument(
        "--universe",
        choices=["manual", "top-market-cap"],
        default="top-market-cap",
        help="Ticker universe source. Use top-market-cap for US-listed large caps from Yahoo screener.",
    )
    parser.add_argument("--universe-limit", type=int, default=15, help="Universe size for top-market-cap")
    parser.add_argument(
        "--universe-output",
        default="",
        help="Optional path to save the fetched universe CSV.",
    )
    parser.add_argument(
        "--provider",
        choices=["yahoo"],
        default="yahoo",
        help="Data provider. yfinance/Yahoo is used for no-key collection.",
    )
    parser.add_argument(
        "--period",
        default="+1q",
        help="Yahoo EPS trend period: 0q, +1q, 0y, +1y. Default: +1q",
    )
    parser.add_argument(
        "--field",
        choices=["avg"],
        default="avg",
        help="EPS estimate field. Yahoo EPS trend lookback supports avg.",
    )
    parser.add_argument(
        "--lookback",
        default="30d",
        help="Yahoo built-in comparison window: 7d, 30d, 60d, 90d. Default: 30d",
    )
    parser.add_argument(
        "--output-mode",
        choices=["rank", "scanner"],
        default="scanner",
        help="rank keeps the legacy single-lookback CSV; scanner outputs multi-lookback signal rows.",
    )
    parser.add_argument(
        "--lookbacks",
        default="7,30,60,90",
        help="Comma-separated lookback days for --output-mode scanner. Yahoo avg supports 7,30,60,90.",
    )
    parser.add_argument("--as-of", default=date.today().isoformat(), help="Snapshot date, YYYY-MM-DD")
    parser.add_argument("--limit", type=int, default=15, help="Number of rows to output")
    parser.add_argument(
        "--delay",
        type=float,
        help="Delay between ticker requests. Defaults to 5s for Yahoo.",
    )
    parser.add_argument(
        "--retries",
        type=int,
        default=4,
        help="Retry count for transient Yahoo errors such as 429/5xx/timeouts.",
    )
    parser.add_argument(
        "--retry-base-delay",
        type=float,
        default=15.0,
        help="Initial Yahoo retry delay in seconds before exponential backoff.",
    )
    parser.add_argument(
        "--retry-max-delay",
        type=float,
        default=180.0,
        help="Maximum Yahoo retry delay in seconds.",
    )
    parser.add_argument("--output", help="Optional CSV output path.")
    parser.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR, help="Directory for final PNG output.")
    parser.add_argument("--image-output", help="Optional PNG output path. Defaults to output/<report-name>.png")
    parser.add_argument(
        "--no-telegram",
        action="store_true",
        help="Do not send the generated image to Telegram.",
    )
    return parser.parse_args()


def main() -> int:
    _load_env_file()
    started_at = datetime.now()
    logger = LoggerUtil().get_logger()
    args = parse_args()

    logger.info(
        "Run started: provider=%s field=%s mode=%s as_of=%s",
        args.provider,
        args.field,
        args.output_mode,
        args.as_of,
    )
    tickers = read_tickers(args.symbols, args.symbols_file)
    if args.universe == "top-market-cap":
        logger.info("Fetching top market-cap universe: limit=%s", args.universe_limit)
        fetched = fetch_top_us_market_cap_tickers(
            args.universe_limit,
            max_retries=args.retries,
            retry_base_delay=args.retry_base_delay,
            retry_max_delay=args.retry_max_delay,
        )
        tickers = _merge_tickers(tickers, fetched)
        if args.universe_output:
            write_tickers_csv(args.universe_output, fetched)
            logger.info("Universe CSV saved: %s", args.universe_output)

    if not tickers:
        raise SystemExit("Provide --symbols/--symbols-file or use --universe top-market-cap")

    as_of = date.fromisoformat(args.as_of)
    provider = args.provider
    delay_seconds = args.delay
    if delay_seconds is None:
        delay_seconds = 5.0

    records = fetch_yahoo_estimates(
        tickers,
        period=args.period,
        field=args.field,
        lookback=args.lookback,
        as_of=as_of,
        delay_seconds=delay_seconds,
        max_retries=args.retries,
        retry_base_delay=args.retry_base_delay,
        retry_max_delay=args.retry_max_delay,
    )

    logger.info("Fetched EPS records: %s", len(records))

    errors = [record for record in records if record.error]
    if errors:
        logger.warning("Skipped %s symbols with errors or missing EPS trend data.", len(errors))
        for record in errors[:10]:
            logger.warning("%s: %s", record.symbol, record.error)

    output = args.output
    if output:
        Path(output).parent.mkdir(parents=True, exist_ok=True)

    if args.output_mode == "scanner":
        lookback_days = _parse_lookback_days(args.lookbacks)
        scanner_rows = build_scanner_rows(
            records,
            field=args.field,
            lookback_days=lookback_days,
        )
        selected_rows = select_scanner_rows(scanner_rows, args.limit)
        if not selected_rows:
            logger.warning("No scanner rows had a usable EPS comparison baseline.")
        write_scanner_csv(selected_rows, output, field=args.field, lookback_days=lookback_days)
        image_path = save_scanner_image(
            selected_rows,
            field=args.field,
            lookback_days=lookback_days,
            output_dir=args.output_dir,
            image_output=args.image_output,
        )
        output_count = len(selected_rows)
    else:
        ranked = rank_records(records, args.limit)
        write_csv(ranked, output)
        image_path = save_rank_image(
            ranked,
            field=args.field,
            output_dir=args.output_dir,
            image_output=args.image_output,
        )
        output_count = len(ranked)

    finished_at = datetime.now()
    elapsed_seconds = (finished_at - started_at).total_seconds()
    logger.info("Image output saved: %s", image_path)
    if not args.no_telegram:
        _send_telegram_image(
            image_path,
            caption=f"{as_of.isoformat()} 미국 시총 상위 {output_count}개 EPS {FIELD_LABELS.get(args.field, args.field)} 변동률",
            logger=logger,
        )
    logger.info(
        "Run finished: %s output rows, %.1fs elapsed.",
        output_count,
        elapsed_seconds,
    )
    print(image_path)
    return 0


def _merge_tickers(primary: list[Ticker], fallback: list[Ticker]) -> list[Ticker]:
    if not primary:
        return fallback

    by_symbol = {ticker.symbol.upper(): ticker for ticker in fallback}
    merged: list[Ticker] = []
    for ticker in primary:
        fallback_ticker = by_symbol.get(ticker.symbol.upper())
        merged.append(
            Ticker(
                symbol=ticker.symbol,
                name=ticker.name or (fallback_ticker.name if fallback_ticker else ""),
                market_cap=ticker.market_cap
                if ticker.market_cap is not None
                else (fallback_ticker.market_cap if fallback_ticker else None),
            )
        )
    return merged


def _as_float(value: object) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(str(value).replace(",", ""))
    except ValueError:
        return None


def _parse_lookback_days(value: str) -> list[int]:
    days: list[int] = []
    seen: set[int] = set()
    for raw_part in value.split(","):
        part = raw_part.strip().lower().removesuffix("d")
        if not part:
            continue
        try:
            day = int(part)
        except ValueError as exc:
            raise SystemExit(f"Invalid lookback day '{raw_part}'. Use values like 1,7,30.") from exc
        if day <= 0:
            raise SystemExit("Lookback days must be positive integers.")
        if day not in seen:
            days.append(day)
            seen.add(day)
    if not days:
        raise SystemExit("At least one lookback day is required.")
    return days


def _provider_baseline(record: EpsRecord, *, field: str, days: int) -> tuple[float, str] | None:
    if field != "avg":
        return None
    previous_eps = record.provider_history_by_day.get(days)
    if previous_eps is None:
        return None
    return previous_eps, PROVIDER_HISTORY_LABELS.get(days, f"{days}daysAgo")


def _send_telegram_image(image_path: str, *, caption: str, logger: object) -> None:
    try:
        telegram = TelegramUtil()
        if not telegram.is_configured:
            logger.info("Telegram is not configured. Skipping image send.")
            return

        result = telegram.send_photo(image_path, caption=caption)
        message_id = (result.get("result") or {}).get("message_id")
        logger.info("Telegram image sent. message_id=%s", message_id)
    except Exception as exc:
        logger.warning("Telegram image send failed: %s", exc)


def _format_float(value: float | None, *, digits: int) -> str:
    return f"{value:.{digits}f}" if value is not None else ""


def _load_env_file() -> None:
    env_path = Path(__file__).resolve().parent.parent / ".env"
    if not env_path.exists():
        return

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def _save_table_image(
    rows: list[dict[str, object]],
    *,
    title: str,
    subtitle: str,
    output_dir: str,
    image_output: str | None,
) -> str:
    if not rows:
        rows = [{"결과": "출력할 데이터가 없습니다."}]

    try:
        import imgkit
        import pandas as pd
    except ImportError as exc:
        raise SystemExit("imgkit and pandas are required. Run: pip install -r requirements.txt") from exc

    output_path = Path(image_output) if image_output else _default_image_path(output_dir, title)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    image_width = _image_width(rows)
    font_family = os.getenv("IMAGE_FONT_FAMILY") or DEFAULT_IMAGE_FONT_FAMILY

    df_html = pd.DataFrame(rows).to_html(index=False, classes="styled-table", escape=False)
    df_html = re.sub(r"(<tr[^>]*>)\s*<td([^>]*)>", r'\1<td class="first-col"\2>', df_html)

    subtitle_html = f'<div class="subtitle">{html.escape(subtitle)}</div>' if subtitle else ""
    html_text = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="UTF-8">
        <style>
            body {{
                font-family: {font_family};
                margin: 16px;
                color: #222;
                background: #ffffff;
                max-width: {image_width}px;
            }}
            .caption {{
                font-size: 24px;
                font-weight: 700;
                margin-bottom: 4px;
                text-align: center;
            }}
            .subtitle {{
                font-size: 13px;
                color: #555;
                margin-bottom: 12px;
                text-align: center;
            }}
            table {{
                border-collapse: collapse;
                width: 100%;
                table-layout: auto;
                box-shadow: 0 1px 3px rgba(0,0,0,0.12);
            }}
            th, td {{
                border: 1px solid #dddddd;
                padding: 7px 8px;
                text-align: center;
            }}
            th {{
                background: #2f343b;
                color: white;
                font-size: 12px;
                font-weight: 700;
                white-space: nowrap;
            }}
            td {{
                font-size: 12px;
                font-weight: 500;
                white-space: normal;
                line-height: 1.35;
            }}
            td.first-col {{
                font-weight: 700;
                white-space: nowrap;
            }}
            .subtext {{
                display: block;
                font-size: 10px;
                color: #666666;
                font-weight: 500;
                margin-top: 2px;
            }}
            tr:nth-child(even) td {{
                background: #f7f8fa;
            }}
            .positive {{
                color: #c62828;
                font-weight: 700;
            }}
            .negative {{
                color: #1565c0;
                font-weight: 700;
            }}
            .source {{
                text-align: right;
                font-size: 11px;
                color: #666666;
                margin-top: 10px;
            }}
        </style>
    </head>
    <body>
        <div class="caption">{html.escape(title)}</div>
        {subtitle_html}
        {df_html}
        <div class="source">출처: Yahoo Finance via yfinance, MQ</div>
    </body>
    </html>
    """

    options = {
        "format": "png",
        "encoding": "UTF-8",
        "quality": 100,
        "width": image_width,
        "enable-local-file-access": None,
        "minimum-font-size": 10,
    }
    wkhtmltoimage_path = os.getenv("WKHTMLTOIMAGE_PATH")
    if wkhtmltoimage_path:
        config = imgkit.config(wkhtmltoimage=wkhtmltoimage_path)
        imgkit.from_string(html_text, str(output_path), options=options, config=config)
    else:
        imgkit.from_string(html_text, str(output_path), options=options)
    return str(output_path)


def _default_image_path(output_dir: str, title: str) -> Path:
    safe_title = re.sub(r"[^A-Za-z0-9]+", "_", title).strip("_").lower()
    date_label = datetime.now().strftime("%Y%m%d")
    return Path(output_dir) / f"{safe_title}_{date_label}.png"


def _image_width(rows: list[dict[str, object]]) -> int:
    column_count = len(rows[0]) if rows else 1
    return min(1180, max(620, 120 * column_count + 120))


def _format_percent(value: float | None) -> str:
    if value is None:
        return ""
    return _format_number(value, digits=2, signed=True, suffix="%")


def _stock_label(record: EpsRecord) -> str:
    symbol = html.escape(record.symbol)
    name = html.escape(record.name)
    if name:
        return f"{symbol}<span class='subtext'>{name}</span>"
    return symbol


def _rows_as_of(records: list[EpsRecord]) -> str:
    if records:
        return records[0].as_of.isoformat()
    return date.today().isoformat()


def _scanner_rows_as_of(rows: list[ScannerRow]) -> str:
    if rows:
        return rows[0].record.as_of.isoformat()
    return date.today().isoformat()


def _format_number(value: float | None, *, digits: int, signed: bool = False, suffix: str = "") -> str:
    if value is None:
        return ""
    sign = "+" if signed and value > 0 else ""
    formatted = f"{sign}{value:.{digits}f}{suffix}"
    if value > 0:
        return f"<span class='positive'>{formatted}</span>"
    if value < 0:
        return f"<span class='negative'>{formatted}</span>"
    return formatted


def _html_text(value: object) -> str:
    return html.escape(str(value or ""))


if __name__ == "__main__":
    raise SystemExit(main())
