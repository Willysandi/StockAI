import time
import pandas as pd
import yfinance as yf

CACHE_TTL_SECONDS = 3600
_cache = {}

def _clean(value):
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    if hasattr(value, "item"):
        value = value.item()
    if isinstance(value, float):
        return round(value, 2)
    return value

def _next_earnings_date(stock):
    calendar = stock.calendar
    dates = calendar.get("Earnings Date") if isinstance(calendar, dict) else None
    if not dates:
        return None
    first = dates[0] if isinstance(dates, list) else dates
    return str(first)

def _recent_earnings(stock):
    df = stock.get_earnings_dates(limit=12)
    if df is None or df.empty:
        return []
    df = df.dropna(subset=["Reported EPS"]).head(4)
    return [
        {
            "date": idx.date().isoformat(),
            "eps_estimate": _clean(row.get("EPS Estimate")),
            "eps_actual": _clean(row.get("Reported EPS")),
            "surprise_pct": _clean(row.get("Surprise(%)")),
        }
        for idx, row in df.iterrows()
    ]

def _valuation(stock):
    info = stock.info or {}
    return {
        "market_cap": _clean(info.get("marketCap")),
        "trailing_pe": _clean(info.get("trailingPE")),
        "forward_pe": _clean(info.get("forwardPE")),
        "analyst_target_mean": _clean(info.get("targetMeanPrice")),
        "analyst_recommendation": _clean(info.get("recommendationKey")),
    }

def get_fundamentals(ticker: str) -> dict:
    cached = _cache.get(ticker)
    if cached and time.time() - cached[0] < CACHE_TTL_SECONDS:
        return cached[1]

    stock = yf.Ticker(ticker)
    result = {}
    # yfinance fields are often missing for some tickers, so each one fails on its own
    for key, fetch, default in (
        ("next_earnings_date", _next_earnings_date, None),
        ("recent_earnings", _recent_earnings, []),
        ("valuation", _valuation, {}),
    ):
        try:
            result[key] = fetch(stock)
        except Exception:
            result[key] = default

    _cache[ticker] = (time.time(), result)
    return result
