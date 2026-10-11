import psycopg2
from psycopg2.extras import execute_values
import requests
import yfinance as yf
import pandas as pd
from io import StringIO
from collections import defaultdict
from dotenv import load_dotenv
import os

load_dotenv()

DB_URL = os.getenv("DATABASE_URL")

SP500_URL = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
BATCH_SIZE = 100

def get_connection():
    return psycopg2.connect(DB_URL)

def get_sp500_constituents():
    # Wikipedia rejects the default urllib user agent used by pd.read_html
    response = requests.get(SP500_URL, headers={"User-Agent": "Mozilla/5.0"}, timeout=30)
    response.raise_for_status()
    df = pd.read_html(StringIO(response.text))[0]
    df = df[["Symbol", "Security", "GICS Sector", "GICS Sub-Industry"]]
    df.columns = ["ticker", "name", "sector", "industry"]
    # Yahoo uses dashes for share classes, e.g. BRK.B -> BRK-B
    df["ticker"] = df["ticker"].str.replace(".", "-", regex=False)
    return df

def create_database():
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS companies (
                    ticker VARCHAR(20) PRIMARY KEY,
                    name TEXT,
                    sector TEXT,
                    industry TEXT
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS us_stocks (
                    id SERIAL PRIMARY KEY,
                    ticker VARCHAR(20),
                    date DATE,
                    open NUMERIC,
                    high NUMERIC,
                    low NUMERIC,
                    close NUMERIC,
                    volume BIGINT,
                    UNIQUE (ticker, date)
                )
            """)
            cur.execute("CREATE INDEX IF NOT EXISTS us_stocks_date_idx ON us_stocks (date)")
    print("Tables created successfully")

def sync_companies():
    companies = get_sp500_constituents()
    with get_connection() as conn:
        with conn.cursor() as cur:
            execute_values(cur, """
                INSERT INTO companies (ticker, name, sector, industry)
                VALUES %s
                ON CONFLICT (ticker) DO UPDATE SET
                    name = EXCLUDED.name,
                    sector = EXCLUDED.sector,
                    industry = EXCLUDED.industry
            """, list(companies.itertuples(index=False, name=None)))
    print(f"{len(companies)} S&P 500 companies synced")
    return companies["ticker"].tolist()

def extract_rows(df, ticker):
    if isinstance(df.columns, pd.MultiIndex):
        if ticker not in df.columns.get_level_values(0):
            return []
        df = df[ticker]
    df = df.dropna(subset=["Open", "High", "Low", "Close"])
    return [
        (
            ticker,
            date.date(),
            round(float(row["Open"]), 2),
            round(float(row["High"]), 2),
            round(float(row["Low"]), 2),
            round(float(row["Close"]), 2),
            int(row["Volume"]) if pd.notna(row["Volume"]) else 0
        )
        for date, row in df.iterrows()
    ]

def load_batch(conn, cur, tickers, start):
    try:
        print(f"Downloading {len(tickers)} tickers from {start}...")
        df = yf.download(
            tickers, start=start, auto_adjust=True,
            group_by="ticker", threads=True, progress=False
        )
        if df.empty:
            print("  No new data")
            return

        rows = []
        for ticker in tickers:
            rows.extend(extract_rows(df, ticker))

        execute_values(cur, """
            INSERT INTO us_stocks (ticker, date, open, high, low, close, volume)
            VALUES %s
            ON CONFLICT (ticker, date) DO NOTHING
        """, rows)
        conn.commit()
        print(f"  {len(rows)} rows loaded")
    except Exception as e:
        conn.rollback()
        print(f"  Failed to load batch starting {tickers[0]}: {e}")

def update_us_data(tickers):
    with get_connection() as conn:
        with conn.cursor() as cur:
            # Use the US market date; local time (e.g. Australia) can be a day ahead
            today = pd.Timestamp.now(tz="America/New_York").normalize().tz_localize(None)
            default_start = (today - pd.DateOffset(years=1)).strftime("%Y-%m-%d")

            cur.execute("SELECT ticker, MAX(date) FROM us_stocks GROUP BY ticker")
            last_dates = dict(cur.fetchall())

            by_start = defaultdict(list)
            for ticker in tickers:
                last_date = last_dates.get(ticker)
                if last_date is None:
                    by_start[default_start].append(ticker)
                    continue
                next_day = pd.Timestamp(last_date) + pd.Timedelta(days=1)
                if next_day > today:
                    continue
                by_start[next_day.strftime("%Y-%m-%d")].append(ticker)

            if not by_start:
                print("All tickers are already up to date")

            for start, group in sorted(by_start.items()):
                for i in range(0, len(group), BATCH_SIZE):
                    load_batch(conn, cur, group[i:i + BATCH_SIZE], start)
    print("US data update complete")

if __name__ == "__main__":
    create_database()
    tickers = sync_companies()
    update_us_data(tickers)
