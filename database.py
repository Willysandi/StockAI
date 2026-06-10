import psycopg2
import yfinance as yf
import pandas as pd
from dotenv import load_dotenv
import os

load_dotenv()

DB_URL = os.getenv("DATABASE_URL")

ASX_TICKERS = [
    "BHP.AX", "CBA.AX", "CSL.AX", "NAB.AX", "WBC.AX",
    "ANZ.AX", "WES.AX", "MQG.AX", "RIO.AX", "TLS.AX"
]

def get_connection():
    return psycopg2.connect(DB_URL)

def create_database():
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS asx_stocks (
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
    print("Table created successfully")

def load_asx_data():
    with get_connection() as conn:
        with conn.cursor() as cur:
            for ticker in ASX_TICKERS:
                try:
                    print(f"Downloading {ticker}...")
                    df = yf.download(ticker, period="1y", auto_adjust=True)

                    if isinstance(df.columns, pd.MultiIndex):
                        df.columns = [col[0] for col in df.columns]

                    for date, row in df.iterrows():
                        cur.execute("""
                            INSERT INTO asx_stocks (ticker, date, open, high, low, close, volume)
                            VALUES (%s, %s, %s, %s, %s, %s, %s)
                            ON CONFLICT DO NOTHING
                        """, (
                            ticker,
                            date.date(),
                            float(row["Open"]),
                            float(row["High"]),
                            float(row["Low"]),
                            float(row["Close"]),
                            int(row["Volume"])
                        ))
                    conn.commit()
                    print(f"  {ticker} loaded successfully")
                except Exception as e:
                    conn.rollback()
                    print(f"  Failed to load {ticker}: {e}")
    print("ASX data load complete")

def update_asx_data():
    with get_connection() as conn:
        with conn.cursor() as cur:
            today = pd.Timestamp.today().normalize()

            for ticker in ASX_TICKERS:
                try:
                    cur.execute(
                        "SELECT MAX(date) FROM asx_stocks WHERE ticker = %s", (ticker,)
                    )
                    last_date = cur.fetchone()[0]

                    if last_date is None:
                        start = "2024-01-01"
                    else:
                        next_day = pd.Timestamp(last_date) + pd.Timedelta(days=1)
                        if next_day > today:
                            print(f"  {ticker} is already up to date")
                            continue
                        start = next_day.strftime("%Y-%m-%d")

                    print(f"Updating {ticker} from {start}...")
                    df = yf.download(ticker, start=start, auto_adjust=True)

                    if df.empty:
                        print(f"  No new data for {ticker}")
                        continue

                    if isinstance(df.columns, pd.MultiIndex):
                        df.columns = [col[0] for col in df.columns]

                    for date, row in df.iterrows():
                        cur.execute("""
                            INSERT INTO asx_stocks (ticker, date, open, high, low, close, volume)
                            VALUES (%s, %s, %s, %s, %s, %s, %s)
                            ON CONFLICT (ticker, date) DO NOTHING
                        """, (
                            ticker,
                            date.date(),
                            float(row["Open"]),
                            float(row["High"]),
                            float(row["Low"]),
                            float(row["Close"]),
                            int(row["Volume"])
                        ))
                    conn.commit()
                    print(f"  {ticker} updated successfully")
                except Exception as e:
                    conn.rollback()
                    print(f"  Failed to update {ticker}: {e}")
    print("ASX data update complete")

if __name__ == "__main__":
    create_database()
    update_asx_data()
