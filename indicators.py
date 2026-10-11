import psycopg2
import pandas as pd

def load_prices(ticker: str, db_url: str) -> pd.DataFrame:
    with psycopg2.connect(db_url) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT date, open, high, low, close, volume
                FROM us_stocks
                WHERE ticker = %s AND date >= CURRENT_DATE - INTERVAL '1 year'
                ORDER BY date
            """, (ticker,))
            rows = cur.fetchall()
    df = pd.DataFrame(rows, columns=["date", "open", "high", "low", "close", "volume"])
    df["date"] = pd.to_datetime(df["date"])
    for col in ("open", "high", "low", "close"):
        df[col] = df[col].astype(float)
    return df.set_index("date")

def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    # Wilder's smoothing
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / period, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / period, adjust=False).mean()
    return 100 - 100 / (1 + gain / loss)

def pct_return(close: pd.Series, days: int):
    if len(close) <= days:
        return None
    return round((close.iloc[-1] / close.iloc[-1 - days] - 1) * 100, 2)

def add_moving_averages(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    for window in (20, 50, 200):
        df[f"sma_{window}"] = df["close"].rolling(window).mean()
    return df

def compute_indicators(df: pd.DataFrame) -> dict:
    df = add_moving_averages(df)
    close = df["close"]
    last = df.iloc[-1]

    def value(x):
        return None if pd.isna(x) else round(float(x), 2)

    high_52w, low_52w = df["high"].max(), df["low"].min()
    daily_returns = close.pct_change()

    return {
        "as_of": df.index[-1].date().isoformat(),
        "close": value(last["close"]),
        "sma_20": value(last["sma_20"]),
        "sma_50": value(last["sma_50"]),
        "sma_200": value(last["sma_200"]),
        "rsi_14": value(rsi(close).iloc[-1]),
        "return_1m_pct": pct_return(close, 21),
        "return_3m_pct": pct_return(close, 63),
        "return_6m_pct": pct_return(close, 126),
        "high_52w": value(high_52w),
        "low_52w": value(low_52w),
        "position_in_52w_range_pct": value((last["close"] - low_52w) / (high_52w - low_52w) * 100),
        "volatility_20d_annualised_pct": value(daily_returns.tail(20).std() * (252 ** 0.5) * 100),
        "volume_20d_vs_1y_avg": value(df["volume"].tail(20).mean() / df["volume"].mean()),
    }

def score_signal(ind: dict) -> dict:
    """Fixed scorecard so the same data always gives the same verdict."""
    rules = []

    def add(points, reason):
        rules.append({"points": points, "reason": reason})

    if ind["sma_50"] is not None:
        if ind["close"] > ind["sma_50"]:
            add(1, f"Price {ind['close']} is above the 50-day average {ind['sma_50']}")
        else:
            add(-1, f"Price {ind['close']} is below the 50-day average {ind['sma_50']}")

    if ind["sma_50"] is not None and ind["sma_200"] is not None:
        if ind["sma_50"] > ind["sma_200"]:
            add(1, f"50-day average is above the 200-day average {ind['sma_200']} (long-term uptrend)")
        else:
            add(-1, f"50-day average is below the 200-day average {ind['sma_200']} (long-term downtrend)")

    if ind["rsi_14"] is not None:
        if ind["rsi_14"] < 30:
            add(1, f"RSI {ind['rsi_14']} is below 30 (oversold)")
        elif ind["rsi_14"] > 70:
            add(-1, f"RSI {ind['rsi_14']} is above 70 (overbought)")
        else:
            add(0, f"RSI {ind['rsi_14']} is neutral (30-70)")

    if ind["return_3m_pct"] is not None:
        if ind["return_3m_pct"] > 0:
            add(1, f"3-month return is positive ({ind['return_3m_pct']}%)")
        else:
            add(-1, f"3-month return is negative ({ind['return_3m_pct']}%)")

    score = sum(r["points"] for r in rules)
    if score >= 2:
        signal = "BUY"
    elif score <= -2:
        signal = "SELL"
    else:
        signal = "HOLD"
    return {"signal": signal, "score": score, "max_score": 4, "rules": rules}
