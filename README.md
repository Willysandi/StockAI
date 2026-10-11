# 📈 StockAI

Chat with AI about S&P 500 stocks. Ask in plain English, for example *"Which sector did best this year?"* or *"Should I buy NVDA?"*, and get an answer with tables and charts. Follow-up questions keep the context, so *"what about AMD?"* works after asking about NVDA.

Behind the scenes, Claude AI converts data questions into SQL and runs them against a PostgreSQL database of historical stock data. A strategy question about a single stock gets a consistent rule-based BUY / HOLD / SELL signal, combined with the latest earnings and news.

![Python](https://img.shields.io/badge/Python-3.10+-blue)
![Streamlit](https://img.shields.io/badge/Streamlit-app-FF4B4B)
![PostgreSQL](https://img.shields.io/badge/PostgreSQL-database-336791)
![Claude](https://img.shields.io/badge/Claude-AI-D97757)

## How it works

```
User question (plain English)
        │
        ▼
Claude AI ──► generates SQL ──► PostgreSQL (us_stocks + companies)
        │                              │
        ▼                              ▼
Claude AI ◄── query results ◄── safe SELECT-only execution
        │
        ▼
Chat reply: insight + data table + price chart
```

1. You type a question like *"Which stock had the biggest single-day price drop this year?"*
2. If it's a follow-up, Claude first rewrites it into a self-contained question using the recent chat (e.g. *"what about AMD?"* → *"What's the strategy for AMD?"*)
3. Claude converts it to a SQL query against the `us_stocks` and `companies` tables
4. The query runs against PostgreSQL (SELECT-only queries are enforced)
5. Claude analyses the results and writes a short financial insight
6. The chat shows the insight, a formatted data table and a price chart

A quick classification step first decides whether the question is a **data query** (the flow above) or a **strategy question** about one stock (e.g. *"Should I buy NVDA?"*), which uses strategy mode below.

## Strategy mode

To keep answers consistent, the numbers and the verdict are calculated in Python code, not by Claude:

1. `indicators.py` computes a fixed set of indicators from the last year of prices: SMA 20/50/200, RSI(14), 1/3/6-month returns, 52-week range position, volatility and relative volume.
2. A fixed scorecard turns them into a signal:

   | Rule | Points |
   |---|---|
   | Close above 50-day average | +1, otherwise −1 |
   | 50-day average above 200-day average | +1, otherwise −1 |
   | RSI below 30 / above 70 | +1 / −1 (0 in between) |
   | 3-month return positive | +1, otherwise −1 |

   A score of 2 or more is **BUY**, −2 or less is **SELL**, anything else is **HOLD**. The same data always gives the same signal.
3. `market_data.py` pulls the next earnings date, the last 4 quarters of EPS (estimate, actual, surprise) and valuation (P/E, analyst target and rating) from Yahoo Finance.
4. Claude uses its **web search** tool (up to 3 searches) to find news from the last 30 days, then writes a note with fixed sections: Signal, Technical picture, Earnings & valuation, Recent news, Risks, Bottom line. Claude can't change the signal, but it flags news that points the other way. All calls use `temperature=0`.
5. The dashboard caches each stock's strategy for 1 hour, so asking again (in any wording) returns the identical answer.

Web search costs about $10 per 1,000 searches plus tokens, so at most about 3 cents per strategy question for searches. It must be enabled for your organisation in the [Anthropic Console](https://console.anthropic.com). Strategy notes are informational, not financial advice.

## Stocks tracked

All S&P 500 constituents (~500 companies). The constituent list — with company name, GICS sector and sub-industry — is pulled from [Wikipedia](https://en.wikipedia.org/wiki/List_of_S%26P_500_companies) each time `database.py` runs, and one year of daily OHLCV prices is downloaded from Yahoo Finance. Tickers use Yahoo format (e.g. `AAPL`, `BRK-B`).

## Project structure

| File | Purpose |
|---|---|
| `app.py` | Streamlit chat UI |
| `claude_query.py` | Claude integration: resolves follow-ups, routes the question, then question → SQL → results → insight, or the strategy note with web search |
| `indicators.py` | Fixed technical indicators and the BUY / HOLD / SELL scorecard |
| `market_data.py` | Earnings dates, EPS history and valuation from Yahoo Finance |
| `database.py` | Creates the `us_stocks` and `companies` tables, syncs S&P 500 constituents and loads/updates prices from Yahoo Finance |
| `mcp_server.py` | MCP server exposing the database as a `query_us_stocks` tool (e.g. for Claude Desktop) |
| `requirements.txt` | Python dependencies |

## Setup

### 1. Clone and install dependencies

```bash
git clone https://github.com/Willysandi/StockAI.git
cd StockAI
python -m venv venv
venv\Scripts\activate        # Windows
# source venv/bin/activate   # macOS/Linux
pip install -r requirements.txt
```

### 2. Configure environment variables

Create a `.env` file in the project root:

```env
ANTHROPIC_API_KEY=your-anthropic-api-key
DATABASE_URL=postgresql://user:password@host:5432/dbname
```

- Get an Anthropic API key at [console.anthropic.com](https://console.anthropic.com)
- `DATABASE_URL` can point to any PostgreSQL instance (local, Supabase, Neon, etc.)

If deploying to Streamlit Cloud, set the same two values in **Streamlit secrets** instead.

### 3. Create the table and load stock data

```bash
python database.py
```

This creates the tables (if they don't exist), syncs the S&P 500 constituent list and downloads daily price data in batches. The first run loads ~125k rows and takes a few minutes. Re-run it any time to fetch the latest prices — it only inserts dates that are missing.

### 4. Run the app

```bash
streamlit run app.py
```

Open http://localhost:8501 and start asking questions.

## Example questions

- Which 10 stocks had the highest return over the last 3 months?
- What is the average closing price of AAPL over the past 3 months?
- Which sector had the best average performance this year?
- Compare the closing prices of MSFT and GOOGL over the last 30 days
- Top 5 Information Technology stocks by average trading volume
- What's the strategy for NVDA based on the last 3 months?
- Is AAPL a buy after its latest earnings?

## MCP server (optional)

`mcp_server.py` exposes the database as an [MCP](https://modelcontextprotocol.io) tool so Claude Desktop (or any MCP client) can query it directly. Add this to your Claude Desktop config:

```json
{
  "mcpServers": {
    "us-stocks": {
      "command": "python",
      "args": ["C:/path/to/StockAI/mcp_server.py"]
    }
  }
}
```

## Database schema

```sql
CREATE TABLE companies (
    ticker   VARCHAR(20) PRIMARY KEY,
    name     TEXT,
    sector   TEXT,
    industry TEXT
);

CREATE TABLE us_stocks (
    id     SERIAL PRIMARY KEY,
    ticker VARCHAR(20),
    date   DATE,
    open   NUMERIC,
    high   NUMERIC,
    low    NUMERIC,
    close  NUMERIC,
    volume BIGINT,
    UNIQUE (ticker, date)
);
```

> Upgrading from the ASX version? The old `asx_stocks` table is left untouched. Drop it manually with `DROP TABLE asx_stocks;` once you no longer need it.

## Safety

Only read-only `SELECT` (or `WITH ... SELECT`) queries generated by Claude are executed — anything else is rejected before it reaches the database.

## License

MIT
