import anthropic
import psycopg2
import os
import json
import re
from datetime import date
from typing import Literal
from pydantic import BaseModel
from dotenv import load_dotenv
from indicators import load_prices, compute_indicators, add_moving_averages, score_signal
from market_data import get_fundamentals

load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))
ANTHROPIC_KEY = os.getenv("ANTHROPIC_API_KEY")
DB_URL = os.getenv("DATABASE_URL")

try:
    import streamlit as st
    ANTHROPIC_KEY = st.secrets.get("ANTHROPIC_API_KEY") or ANTHROPIC_KEY
    DB_URL = st.secrets.get("DATABASE_URL") or DB_URL
except Exception:
    pass

missing = [name for name, value in (("ANTHROPIC_API_KEY", ANTHROPIC_KEY), ("DATABASE_URL", DB_URL)) if not value]
if missing:
    raise EnvironmentError(f"{', '.join(missing)} not found in .env or Streamlit secrets.")

client = anthropic.Anthropic(api_key=ANTHROPIC_KEY)

MODEL = "claude-sonnet-4-6"
# anthropic 1.x removed the temperature argument; this model still accepts it in the
# request body, and temperature 0 keeps answers consistent between runs
MAX_INSIGHT_ROWS = 200
MAX_PAUSE_RESTARTS = 3

class Route(BaseModel):
    intent: Literal["data_query", "strategy"]
    tickers: list[str]

def is_safe_sql(sql: str) -> bool:
    return sql.strip().split()[0].upper() in ("SELECT", "WITH")

def extract_sql(text: str) -> str:
    # Claude sometimes adds an explanation and wraps the query in a ```sql block
    match = re.search(r"```(?:sql)?\s*(.*?)```", text, re.DOTALL | re.IGNORECASE)
    return (match.group(1) if match else text).strip()

def run_query(sql: str):
    if not is_safe_sql(sql):
        raise ValueError("Only SELECT queries are allowed.")
    with psycopg2.connect(DB_URL) as conn:
        with conn.cursor() as cur:
            cur.execute(sql)
            columns = [desc[0] for desc in cur.description]
            rows = cur.fetchall()
    return columns, rows

def classify_question(question: str) -> Route:
    response = client.messages.parse(
        model=MODEL,
        max_tokens=500,
        extra_body={"temperature": 0},
        system="""Classify a question about S&P 500 stocks.
        intent = "strategy" when the user wants a trading view, outlook, recommendation or
        strategy for one specific stock (e.g. "should I buy NVDA", "strategy for Apple",
        "is TSLA a good buy after earnings").
        intent = "data_query" for everything else: rankings, comparisons, averages, sector
        questions, historical prices, or anything about many stocks.
        tickers = the Yahoo Finance tickers mentioned, converting company names to tickers
        (Apple -> AAPL, Berkshire Hathaway -> BRK-B). Empty list if none.""",
        messages=[{"role": "user", "content": question}],
        output_format=Route,
    )
    return response.parsed_output

def known_tickers(tickers: list[str]) -> list[str]:
    if not tickers:
        return []
    with psycopg2.connect(DB_URL) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT ticker FROM companies WHERE ticker = ANY(%s)", ([t.upper() for t in tickers],))
            found = {row[0] for row in cur.fetchall()}
    return [t.upper() for t in tickers if t.upper() in found]

def strategy_ticker(question: str):
    """Returns the ticker to analyse if this is a strategy question, otherwise None."""
    route = classify_question(question)
    tickers = known_tickers(route.tickers)
    if route.intent == "strategy" and tickers:
        return tickers[0]
    return None

def standalone_question(question: str, history: list[dict]) -> str:
    """Rewrites a follow-up like "what about AMD?" into a self-contained question."""
    if not history:
        return question
    transcript = "\n".join(f"{turn['role']}: {turn['content']}" for turn in history[-6:])
    response = client.messages.create(
        model=MODEL,
        max_tokens=200,
        extra_body={"temperature": 0},
        system="""Rewrite the user's latest question about S&P 500 stocks so it can be understood
        without the conversation. Fill in tickers, companies, metrics and time periods it refers to
        (e.g. after a strategy answer on NVDA, "what about AMD?" -> "What's the strategy for AMD?").
        If it is already self-contained, return it unchanged.
        Return ONLY the rewritten question, nothing else.""",
        messages=[{"role": "user", "content": f"Conversation:\n{transcript}\n\nLatest question: {question}"}],
    )
    return response.content[0].text.strip() or question

def ask_claude(question: str):
    ticker = strategy_ticker(question)
    if ticker:
        return analyze_strategy(ticker, question)
    return answer_data_query(question)

def answer_data_query(question: str):
    sql_response = client.messages.create(
        model=MODEL,
        max_tokens=1000,
        extra_body={"temperature": 0},
        system="""You are a PostgreSQL expert. Convert the user's question into a SQL query.
        The database covers all S&P 500 companies and has two tables:

        us_stocks (daily prices, about the last year):
        - ticker (VARCHAR): Yahoo Finance ticker e.g. AAPL, MSFT, BRK-B (share classes use a dash)
        - date (DATE): trading date
        - open (NUMERIC): opening price in USD
        - high (NUMERIC): highest price in USD
        - low (NUMERIC): lowest price in USD
        - close (NUMERIC): closing price in USD
        - volume (BIGINT): number of shares traded

        companies (one row per S&P 500 constituent):
        - ticker (VARCHAR): primary key, joins to us_stocks.ticker
        - name (TEXT): company name e.g. Apple Inc.
        - sector (TEXT): GICS sector e.g. Information Technology, Health Care
        - industry (TEXT): GICS sub-industry

        Join on ticker when the question mentions company names, sectors or industries.
        Always add LIMIT 100 unless the user asks for a specific number of rows.
        Round prices and other computed values to 2 decimals with ROUND(value, 2).
        Express returns and percentage changes as percentages (multiply by 100, e.g. 12.34
        for 12.34%), rounded to 2 decimals, in a column whose name ends in _pct.

        Return ONLY the SQL query, nothing else. No explanation, no markdown, just raw SQL.""",
        messages=[{"role": "user", "content": question}]
    )

    sql = extract_sql(sql_response.content[0].text)

    try:
        columns, rows = run_query(sql)

        result_data = {"columns": columns, "rows": [list(r) for r in rows[:MAX_INSIGHT_ROWS]]}
        if len(rows) > MAX_INSIGHT_ROWS:
            result_data["note"] = f"Showing first {MAX_INSIGHT_ROWS} of {len(rows)} rows"

        insight_response = client.messages.create(
            model=MODEL,
            max_tokens=1000,
            extra_body={"temperature": 0},
            system="""You are a financial analyst specialising in US equities and the S&P 500.
            The user asked a question and you ran a SQL query to get data.
            Now provide a clear, concise insight based on the results.
            Be specific, mention actual numbers, and keep it to 3-5 sentences.""",
            messages=[
                {"role": "user", "content": f"""
                Original question: {question}
                SQL query used: {sql}
                Results: {json.dumps(result_data, default=str)}

                Please provide a clear insight based on these results.
                """}
            ]
        )

        insight = insight_response.content[0].text.strip()

        return {
            "mode": "data",
            "sql": sql,
            "columns": columns,
            "rows": rows,
            "insight": insight
        }

    except (psycopg2.Error, ValueError) as e:
        return {
            "mode": "data",
            "sql": sql,
            "columns": [],
            "rows": [],
            "insight": f"Error running query: {str(e)}"
        }

STRATEGY_SYSTEM = """You are an equity analyst writing a short strategy note on one S&P 500 stock.

You are given a rule-based technical signal (BUY, HOLD or SELL) computed from fixed rules,
the indicators behind it, and earnings/valuation data. Do not change the signal. If news,
earnings or valuation point the other way, say so clearly under Risks and in the Bottom line.

Use the web_search tool to find this company's news from the last 30 days and coverage of
its most recent earnings report. Only use news about this company or events that directly
affect it. Prefer reputable financial sources.

Write in markdown using exactly these headings, in this order:
### Signal
### Technical picture
### Earnings & valuation
### Recent news
### Risks
### Bottom line

Mention actual numbers. Keep each section to 2-4 sentences or bullets.
End with one line in italics: "Not financial advice."
"""

def collect_sources(content) -> list[dict]:
    sources = []
    for block in content:
        # Errors arrive as an object instead of a list of results
        if block.type == "web_search_tool_result" and isinstance(block.content, list):
            for result in block.content:
                if result.url not in {s["url"] for s in sources}:
                    sources.append({"title": result.title, "url": result.url, "age": getattr(result, "page_age", None)})
    return sources

def analyze_strategy(ticker: str, question: str):
    prices = load_prices(ticker, DB_URL)
    if prices.empty:
        return answer_data_query(question)

    indicators = compute_indicators(prices)
    signal = score_signal(indicators)
    fundamentals = get_fundamentals(ticker)

    with psycopg2.connect(DB_URL) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT name, sector, industry FROM companies WHERE ticker = %s", (ticker,))
            name, sector, industry = cur.fetchone()

    messages = [{"role": "user", "content": f"""
        Today's date: {date.today().isoformat()}
        User question: {question}
        Stock: {ticker} ({name}), sector {sector}, industry {industry}

        Rule-based signal: {json.dumps(signal)}
        Technical indicators (as of {indicators["as_of"]}): {json.dumps(indicators)}
        Earnings and valuation (from Yahoo Finance): {json.dumps(fundamentals, default=str)}
        """}]

    content = []
    for _ in range(MAX_PAUSE_RESTARTS + 1):
        response = client.messages.create(
            model=MODEL,
            max_tokens=4000,
            extra_body={"temperature": 0},
            system=STRATEGY_SYSTEM,
            tools=[{"type": "web_search_20260209", "name": "web_search", "max_uses": 3}],
            messages=messages,
        )
        content.extend(response.content)
        if response.stop_reason != "pause_turn":
            break
        # Long server-side tool turns pause; send the partial turn back to continue it
        messages = messages + [{"role": "assistant", "content": response.content}]

    insight = "".join(block.text for block in content if block.type == "text")
    # Drop any "let me search..." narration written before the first heading
    if "### " in insight:
        insight = insight[insight.index("### "):]
    insight = insight.strip()

    return {
        "mode": "strategy",
        "ticker": ticker,
        "name": name,
        "signal": signal,
        "indicators": indicators,
        "fundamentals": fundamentals,
        "insight": insight,
        "sources": collect_sources(content),
        "prices": add_moving_averages(prices)[["close", "sma_50", "sma_200"]].round(2),
    }

if __name__ == "__main__":
    result = ask_claude("Which S&P 500 stock had the highest closing price last month?")
    print("SQL:", result["sql"])
    print("Insight:", result["insight"])
