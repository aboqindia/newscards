#!/usr/bin/env python3
"""Fetch weekly market % changes for the heatmap video.

Usage:
  python3 fetch_markets.py
  python3 fetch_markets.py --out markets.json
  python3 fetch_markets.py --mode current --out markets.json
  python3 fetch_markets.py --as-of 2026-11-27 --mode current --out markets.json

Modes:
  auto (default)
    Friday (IST): current week — previous Friday's close versus the
    latest available price right now (today's close if the market has
    already shut, live/latest intraday if it is still open).
    Sat/Sun and Mon–Thu: last completed Mon–Fri week, Friday close
    versus previous Friday close (or the last session of that week if
    Friday was a holiday).

  current
    Always the current week (Monday–Friday of the week containing the
    reference date). The end price is the latest available print, or
    the last print on or before --as-of when that date is in the past.
    Mid-week this is a week-to-date move.

  completed
    Always the last completed Monday–Friday week.

The date-range label is always that week's Monday–Friday
(e.g. '23–27 Nov 2026'), even if the fetch runs mid-week.

Tickers that fail or whose latest used price is more than 7 days old
are skipped — values are never invented.

Data source: Yahoo Finance public chart API (regularMarketPrice, daily
bars, or 5-minute bars). No extra Python packages are required.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from typing import Any

# Approximate relative market-cap weights (equity market cap, plus the
# spot value of gold, silver and bitcoin). US is deliberately the largest
# tile; units are arbitrary and only the ratios matter.
MARKETS: list[dict[str, Any]] = [
    # name, Yahoo ticker, tile code, ISO flag (or None), weight
    {"name": "US",          "ticker": "^GSPC",      "code": "US",     "flag": "us", "weight": 50.0},
    {"name": "CHINA",       "ticker": "000001.SS",  "code": "CN",     "flag": "cn", "weight": 10.0},
    {"name": "JAPAN",       "ticker": "^N225",      "code": "JP",     "flag": "jp", "weight": 6.5},
    {"name": "INDIA",       "ticker": "^NSEI",      "code": "IN",     "flag": "in", "weight": 5.0},
    {"name": "UK",          "ticker": "^FTSE",      "code": "GB",     "flag": "gb", "weight": 3.3},
    {"name": "FRANCE",      "ticker": "^FCHI",      "code": "FR",     "flag": "fr", "weight": 3.2},
    {"name": "CANADA",      "ticker": "^GSPTSE",    "code": "CA",     "flag": "ca", "weight": 3.1},
    {"name": "GERMANY",     "ticker": "^GDAXI",     "code": "DE",     "flag": "de", "weight": 2.5},
    {"name": "SAUDI",       "ticker": "^TASI.SR",   "code": "SA",     "flag": "sa", "weight": 2.4},
    {"name": "SWITZERLAND", "ticker": "^SSMI",      "code": "CH",     "flag": "ch", "weight": 2.3},
    {"name": "TAIWAN",      "ticker": "^TWII",      "code": "TW",     "flag": "tw", "weight": 2.0},
    {"name": "AUSTRALIA",   "ticker": "^AXJO",      "code": "AU",     "flag": "au", "weight": 1.8},
    {"name": "S. KOREA",    "ticker": "^KS11",      "code": "KR",     "flag": "kr", "weight": 1.8},
    {"name": "NETHERLANDS", "ticker": "^AEX",       "code": "NL",     "flag": "nl", "weight": 1.2},
    {"name": "HONG KONG",   "ticker": "^HSI",       "code": "HK",     "flag": "hk", "weight": 0.9},
    {"name": "SWEDEN",      "ticker": "^OMX",       "code": "SE",     "flag": "se", "weight": 1.0},
    {"name": "ITALY",       "ticker": "FTSEMIB.MI", "code": "IT",     "flag": "it", "weight": 0.9},
    {"name": "SPAIN",       "ticker": "^IBEX",      "code": "ES",     "flag": "es", "weight": 0.8},
    {"name": "BRAZIL",      "ticker": "^BVSP",      "code": "BR",     "flag": "br", "weight": 0.9},
    {"name": "DENMARK",     "ticker": "^OMXC25",    "code": "DK",     "flag": "dk", "weight": 0.7},
    {"name": "INDONESIA",   "ticker": "^JKSE",      "code": "ID",     "flag": "id", "weight": 0.7},
    {"name": "SINGAPORE",   "ticker": "^STI",       "code": "SG",     "flag": "sg", "weight": 0.6},
    {"name": "ISRAEL",      "ticker": "TA35.TA",    "code": "IL",     "flag": "il", "weight": 0.4},
    {"name": "BELGIUM",     "ticker": "^BFX",       "code": "BE",     "flag": "be", "weight": 0.4},
    {"name": "MEXICO",      "ticker": "^MXX",       "code": "MX",     "flag": "mx", "weight": 0.45},
    {"name": "NORWAY",      "ticker": "OSEBX.OL",   "code": "NO",     "flag": "no", "weight": 0.4},
    {"name": "MALAYSIA",    "ticker": "^KLSE",      "code": "MY",     "flag": "my", "weight": 0.4},
    {"name": "S. AFRICA",   "ticker": "^J203.JO",   "code": "ZA",     "flag": "za", "weight": 0.35},
    {"name": "TURKEY",      "ticker": "XU100.IS",   "code": "TR",     "flag": "tr", "weight": 0.35},
    {"name": "AUSTRIA",     "ticker": "^ATX",       "code": "AT",     "flag": "at", "weight": 0.25},
    {"name": "IRELAND",     "ticker": "^ISEQ",      "code": "IE",     "flag": "ie", "weight": 0.25},
    {"name": "FINLAND",     "ticker": "^OMXH25",    "code": "FI",     "flag": "fi", "weight": 0.25},
    {"name": "GOLD",        "ticker": "GC=F",       "code": "GOLD",   "flag": None, "weight": 8.0},
    {"name": "SILVER",      "ticker": "SI=F",       "code": "SILVER", "flag": None, "weight": 1.2},
    {"name": "BITCOIN",     "ticker": "BTC-USD",    "code": "BTC",    "flag": None, "weight": 1.5},
]

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)
CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
IST = timezone(timedelta(hours=5, minutes=30))
STALE_DAYS = 7
MONTHS = (
    "Jan", "Feb", "Mar", "Apr", "May", "Jun",
    "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
)


def today_ist() -> date:
    return datetime.now(IST).date()


def last_completed_friday(today: date) -> date:
    """Most recent Friday whose trading week is finished.

    Mon–Fri use the previous Friday (the week is still open on Friday).
    Sat–Sun use the Friday that just ended.
    """
    if today.weekday() >= 5:  # Saturday / Sunday
        return today - timedelta(days=today.weekday() - 4)
    return today - timedelta(days=today.weekday() + 3)


def iso_week_monday(day: date) -> date:
    return day - timedelta(days=day.weekday())


def resolve_week(
    today: date, mode: str
) -> tuple[str, date, date, date, date]:
    """Return (resolved_mode, prev_monday, prev_friday, this_monday, this_friday)."""
    resolved = mode
    if mode == "auto":
        # Friday IST posts the week still in progress; every other day
        # keeps the last fully completed Mon–Fri week.
        resolved = "current" if today.weekday() == 4 else "completed"

    if resolved == "current":
        this_monday = iso_week_monday(today)
        this_friday = this_monday + timedelta(days=4)
    elif resolved == "completed":
        this_friday = last_completed_friday(today)
        this_monday = this_friday - timedelta(days=4)
    else:
        raise ValueError(f"unknown mode {mode!r}")

    prev_friday = this_friday - timedelta(days=7)
    prev_monday = prev_friday - timedelta(days=4)
    return resolved, prev_monday, prev_friday, this_monday, this_friday


def format_date_range(monday: date, friday: date) -> str:
    if monday.year == friday.year and monday.month == friday.month:
        return f"{monday.day}–{friday.day} {MONTHS[friday.month - 1]} {friday.year}"
    if monday.year == friday.year:
        return (
            f"{monday.day} {MONTHS[monday.month - 1]}–"
            f"{friday.day} {MONTHS[friday.month - 1]} {friday.year}"
        )
    return (
        f"{monday.day} {MONTHS[monday.month - 1]} {monday.year}–"
        f"{friday.day} {MONTHS[friday.month - 1]} {friday.year}"
    )


def exchange_tz(meta: dict[str, Any]) -> timezone:
    offset = int(meta.get("gmtoffset") or 0)
    return timezone(timedelta(seconds=offset))


def fetch_chart(
    ticker: str, interval: str, range_: str, timeout: int = 20
) -> dict[str, Any]:
    """Return the first Yahoo chart result for ticker."""
    params = urllib.parse.urlencode({"interval": interval, "range": range_})
    enc = urllib.parse.quote(ticker, safe="")
    url = f"{CHART_URL.format(ticker=enc)}?{params}"
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"HTTP {exc.code}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"network error: {exc.reason}") from exc

    chart = payload.get("chart") or {}
    if chart.get("error"):
        err = chart["error"]
        raise RuntimeError(err.get("description") or err.get("code") or "chart error")
    results = chart.get("result") or []
    if not results:
        raise RuntimeError("no chart result")
    return results[0]


def parse_bars(result: dict[str, Any]) -> list[tuple[date, float]]:
    """Return [(session_date, close), ...] in exchange-local calendar dates."""
    meta = result.get("meta") or {}
    timestamps = result.get("timestamp") or []
    indicators = (result.get("indicators") or {}).get("quote") or [{}]
    closes = (indicators[0] or {}).get("close") or []
    if not timestamps or not closes:
        raise RuntimeError("empty quote series")

    tz = exchange_tz(meta)
    rows: list[tuple[date, float]] = []
    for ts, close in zip(timestamps, closes):
        if close is None or ts is None:
            continue
        try:
            value = float(close)
        except (TypeError, ValueError):
            continue
        if value <= 0:
            continue
        session = datetime.fromtimestamp(int(ts), tz=tz).date()
        rows.append((session, value))
    if not rows:
        raise RuntimeError("no valid closes")
    rows.sort(key=lambda row: row[0])
    return rows


def regular_market_print(result: dict[str, Any]) -> tuple[date, float] | None:
    """Latest official Yahoo print (live if the session is open)."""
    meta = result.get("meta") or {}
    price = meta.get("regularMarketPrice")
    ts = meta.get("regularMarketTime")
    if price is None or ts is None:
        return None
    try:
        value = float(price)
    except (TypeError, ValueError):
        return None
    if value <= 0:
        return None
    session = datetime.fromtimestamp(int(ts), tz=exchange_tz(meta)).date()
    return session, value


def last_bar_on_or_before(
    rows: list[tuple[date, float]], cutoff: date
) -> tuple[date, float] | None:
    chosen: tuple[date, float] | None = None
    for session, value in rows:
        if session <= cutoff:
            chosen = (session, value)
        else:
            break
    return chosen


def close_in_week(
    rows: list[tuple[date, float]], week_monday: date, week_friday: date
) -> tuple[date, float] | None:
    """Last session close that falls inside Monday–Friday inclusive."""
    chosen: tuple[date, float] | None = None
    for session, value in rows:
        if week_monday <= session <= week_friday:
            chosen = (session, value)
        elif session > week_friday:
            break
    return chosen


def fetch_intraday_print(ticker: str, cutoff: date) -> tuple[date, float] | None:
    """Last 5-minute bar on or before cutoff, if Yahoo has one."""
    try:
        result = fetch_chart(ticker, interval="5m", range_="5d")
        rows = parse_bars(result)
    except Exception:
        return None
    return last_bar_on_or_before(rows, cutoff)


def latest_current_print(
    ticker: str,
    daily_result: dict[str, Any],
    daily_rows: list[tuple[date, float]],
    cutoff: date,
    use_live: bool,
) -> tuple[date, float, str]:
    """Best current-week end price: live quote, else last 1d bar, else 5m bar."""
    candidates: list[tuple[date, float, str]] = []

    if use_live:
        live = regular_market_print(daily_result)
        if live is not None and live[0] <= cutoff:
            candidates.append((live[0], live[1], "live"))

    daily = last_bar_on_or_before(daily_rows, cutoff)
    if daily is not None:
        candidates.append((daily[0], daily[1], "1d"))

    # Try 5-minute bars only when we have no quote at all, or when the
    # market may already be open today but regularMarketPrice is missing
    # and the last daily bar is from an earlier session.
    have_live = any(src == "live" for _session, _value, src in candidates)
    have_same_day = any(session == cutoff for session, _value, _src in candidates)
    if not candidates or (use_live and not have_live and not have_same_day):
        time.sleep(0.05)
        intra = fetch_intraday_print(ticker, cutoff)
        if intra is not None:
            candidates.append((intra[0], intra[1], "5m"))

    if not candidates:
        raise RuntimeError(f"no price on or before {cutoff.isoformat()}")

    # Most recent session wins; on the same session prefer live, then 5m, then 1d.
    rank = {"live": 2, "5m": 1, "1d": 0}
    session, value, source = max(candidates, key=lambda row: (row[0], rank[row[2]]))
    return session, value, source


def pct_change(start_px: float, end_px: float) -> float:
    if start_px <= 0:
        raise RuntimeError("non-positive previous close")
    return (end_px - start_px) / start_px * 100.0


def weekly_completed_change(
    rows: list[tuple[date, float]],
    prev_monday: date,
    prev_friday: date,
    this_monday: date,
    this_friday: date,
    today: date,
) -> tuple[float, date, date, str]:
    end = close_in_week(rows, this_monday, this_friday)
    start = close_in_week(rows, prev_monday, prev_friday)
    if end is None:
        raise RuntimeError(f"no close in week ending {this_friday.isoformat()}")
    if start is None:
        raise RuntimeError(f"no close in week ending {prev_friday.isoformat()}")
    if (today - end[0]).days > STALE_DAYS:
        raise RuntimeError(f"stale close {end[0].isoformat()} (older than {STALE_DAYS} days)")
    return pct_change(start[1], end[1]), start[0], end[0], "1d"


def weekly_current_change(
    ticker: str,
    daily_result: dict[str, Any],
    rows: list[tuple[date, float]],
    prev_monday: date,
    prev_friday: date,
    this_friday: date,
    today: date,
    use_live: bool,
) -> tuple[float, date, date, str]:
    start = close_in_week(rows, prev_monday, prev_friday)
    if start is None:
        raise RuntimeError(f"no close in week ending {prev_friday.isoformat()}")

    cutoff = min(today, this_friday)
    end_date, end_px, source = latest_current_print(
        ticker, daily_result, rows, cutoff, use_live
    )
    if (today - end_date).days > STALE_DAYS:
        raise RuntimeError(
            f"stale price {end_date.isoformat()} (older than {STALE_DAYS} days)"
        )
    return pct_change(start[1], end_px), start[0], end_date, source


def fetch_all(
    today: date, mode: str, now_ist: date
) -> tuple[dict[str, Any], list[dict[str, str]]]:
    resolved, prev_monday, prev_friday, this_monday, this_friday = resolve_week(
        today, mode
    )
    # Live quotes are only safe when the reference date is today. A
    # historical --as-of must not pick up a newer regularMarketPrice.
    use_live = today >= now_ist

    items: list[dict[str, Any]] = []
    failed: list[dict[str, str]] = []

    for i, market in enumerate(MARKETS):
        if i:
            time.sleep(0.05)
        ticker = market["ticker"]
        try:
            daily_result = fetch_chart(ticker, interval="1d", range_="2mo")
            rows = parse_bars(daily_result)
            if resolved == "current":
                pct, start_date, end_date, source = weekly_current_change(
                    ticker,
                    daily_result,
                    rows,
                    prev_monday,
                    prev_friday,
                    this_friday,
                    today,
                    use_live,
                )
            else:
                pct, start_date, end_date, source = weekly_completed_change(
                    rows,
                    prev_monday,
                    prev_friday,
                    this_monday,
                    this_friday,
                    today,
                )
        except Exception as exc:
            failed.append(
                {
                    "name": market["name"],
                    "code": market["code"],
                    "ticker": ticker,
                    "reason": str(exc),
                }
            )
            print(f"FAIL  {market['name']:<12} {ticker:<12} {exc}", file=sys.stderr)
            continue
        item = {
            "name": market["name"],
            "code": market["code"],
            "pct": round(pct, 4),
            "weight": market["weight"],
        }
        if market["flag"]:
            item["flag"] = market["flag"]
        items.append(item)
        sign = "+" if pct >= 0 else ""
        print(
            f"OK    {market['name']:<12} {ticker:<12} "
            f"{start_date} → {end_date}  {sign}{pct:.2f}%  ({source})"
        )

    payload = {
        "title": "GLOBAL MARKETS THIS WEEK",
        "date_range": format_date_range(this_monday, this_friday),
        "week_start": this_monday.isoformat(),
        "week_end": this_friday.isoformat(),
        "mode": resolved,
        "as_of": today.isoformat(),
        "items": items,
    }
    return payload, failed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default="markets.json", help="Output JSON path")
    parser.add_argument(
        "--as-of",
        default=None,
        help=(
            "Reference date YYYY-MM-DD in IST (defaults to today IST). "
            "Used to pick the week and to cap prices for historical runs."
        ),
    )
    parser.add_argument(
        "--mode",
        choices=("auto", "current", "completed"),
        default="auto",
        help=(
            "auto: current week on Friday IST, last completed week otherwise. "
            "current: week in progress (latest prices). "
            "completed: last finished Mon–Fri week."
        ),
    )
    args = parser.parse_args()

    now_ist = today_ist()
    today = date.fromisoformat(args.as_of) if args.as_of else now_ist

    payload, failed = fetch_all(today, args.mode, now_ist)
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2)
        fh.write("\n")

    n_ok, n_fail, n_all = len(payload["items"]), len(failed), len(MARKETS)
    print()
    print(
        f"Week {payload['date_range']} [{payload['mode']}]: "
        f"{n_ok} ok, {n_fail} skipped, {n_all} attempted"
    )
    print(f"Wrote {args.out}")
    if n_ok < 8:
        print("ERROR: too few markets to build a heatmap", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
