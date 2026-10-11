import streamlit as st
from claude_query import strategy_ticker, analyze_strategy, answer_data_query
import pandas as pd
from decimal import Decimal

st.set_page_config(
    page_title="S&P 500 Stock Dashboard",
    page_icon="📈",
    layout="wide"
)

st.title("📈 S&P 500 Stock Dashboard")
st.markdown("Ask any question about S&P 500 stock performance in plain English, or ask for a strategy on a single stock.")

st.divider()

with st.sidebar:
    st.header("About")
    st.markdown("""
    This dashboard uses:
    - **PostgreSQL** to store S&P 500 stock data
    - **Claude AI** to convert your question to SQL
    - **Fixed technical rules** for consistent BUY / HOLD / SELL signals
    - **Yahoo Finance** for earnings & valuation
    - **Claude web search** for the latest news
    - **MCP** to connect Claude to the database
    - **Streamlit** for this interface
    """)
    st.markdown("**Stocks tracked:**")
    st.markdown("All S&P 500 constituents (~500), with sector & industry")

st.subheader("Try asking:")
examples = [
    "Which 10 stocks had the highest return over the last 3 months?",
    "What is the average closing price of AAPL over the past 3 months?",
    "Which sector had the best average performance this year?",
    "Compare the closing prices of MSFT and GOOGL over the last 30 days",
    "Which stocks had the biggest single-day price drop this year?",
    "Top 5 Information Technology stocks by average trading volume",
    "What's the strategy for NVDA based on the last 3 months?",
    "Is AAPL a buy after its latest earnings?",
]

cols = st.columns(2)
for i, example in enumerate(examples):
    if cols[i % 2].button(example, width="stretch"):
        st.session_state.question = example

question = st.text_input(
    "Your question:",
    value=st.session_state.get("question", ""),
    placeholder="e.g. Which S&P 500 stock had the highest closing price last month?"
)

@st.cache_data(ttl=3600, show_spinner=False)
def cached_strategy(ticker, _question):
    # Keyed on ticker only, so any phrasing within the hour gets the same answer
    return analyze_strategy(ticker, _question)

PCT_WORDS = ("pct", "percent", "return")
PRICE_WORDS = ("open", "high", "low", "close", "price", "sma", "ma_", "eps", "target")

def fmt(value, prefix="", suffix=""):
    return "n/a" if value is None else f"{prefix}{value:,.2f}{suffix}"

def format_table(df):
    """Prices as $80.30, percentages as 12.34%, volumes with commas."""
    df = df.copy()
    for col in df.columns:
        if df[col].map(lambda v: isinstance(v, Decimal)).any():
            df[col] = df[col].astype(float)
    formats = {}
    for col in df.select_dtypes("number").columns:
        name = str(col).lower()
        if any(w in name for w in PCT_WORDS):
            formats[col] = "{:,.2f}%"
        elif any(w in name for w in PRICE_WORDS):
            formats[col] = "${:,.2f}"
        elif pd.api.types.is_integer_dtype(df[col]):
            formats[col] = "{:,}"
        else:
            formats[col] = "{:,.2f}"
    return df.style.format(formats, na_rep="n/a")

INDICATOR_LABELS = {
    "as_of": "As of",
    "close": "Close",
    "sma_20": "20-day average",
    "sma_50": "50-day average",
    "sma_200": "200-day average",
    "rsi_14": "RSI (14)",
    "return_1m_pct": "1-month return",
    "return_3m_pct": "3-month return",
    "return_6m_pct": "6-month return",
    "high_52w": "52-week high",
    "low_52w": "52-week low",
    "position_in_52w_range_pct": "Position in 52-week range",
    "volatility_20d_annualised_pct": "Volatility (20-day, annualised)",
    "volume_20d_vs_1y_avg": "Volume (20-day vs 1-year avg)",
}

def format_indicator(key, value):
    if value is None:
        return "n/a"
    if key == "as_of":
        return value
    if key.endswith("_pct"):
        return f"{value:,.2f}%"
    if key == "volume_20d_vs_1y_avg":
        return f"{value:.2f}×"
    if key == "rsi_14":
        return f"{value:.2f}"
    return f"${value:,.2f}"

def show_strategy(result):
    ind, sig, fund = result["indicators"], result["signal"], result["fundamentals"]
    val = fund.get("valuation") or {}

    st.subheader(f"{result['name']} ({result['ticker']})")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Signal", sig["signal"], f"score {sig['score']:+d} / {sig['max_score']}", delta_color="off")
    c2.metric("Close", fmt(ind["close"], "$"), f"{ind['return_3m_pct']:.2f}% (3m)" if ind["return_3m_pct"] is not None else None)
    c3.metric("RSI (14)", fmt(ind["rsi_14"]))
    c4.metric("Next earnings", fund.get("next_earnings_date") or "n/a")

    with st.expander("How the signal was scored", expanded=True):
        for rule in sig["rules"]:
            st.markdown(f"- **{rule['points']:+d}** {rule['reason']}")
        st.caption("Score ≥ 2 = BUY, ≤ −2 = SELL, otherwise HOLD. Same data always gives the same signal.")

    st.subheader("Price vs moving averages")
    st.line_chart(result["prices"])

    left, right = st.columns(2)
    with left:
        st.markdown("**Key indicators**")
        rows = [(INDICATOR_LABELS.get(k, k), format_indicator(k, v)) for k, v in ind.items()]
        st.dataframe(pd.DataFrame(rows, columns=["Indicator", "Value"]), hide_index=True, width="stretch")
    with right:
        st.markdown("**Recent earnings (EPS)**")
        if fund.get("recent_earnings"):
            st.dataframe(format_table(pd.DataFrame(fund["recent_earnings"])), hide_index=True, width="stretch")
        else:
            st.caption("No earnings data available.")
        st.markdown(
            f"P/E {fmt(val.get('trailing_pe'))} trailing, {fmt(val.get('forward_pe'))} forward · "
            f"Analyst target {fmt(val.get('analyst_target_mean'), '$')} · "
            f"Rating {val.get('analyst_recommendation') or 'n/a'}"
        )

    st.subheader("Claude's Strategy Note")
    st.markdown(result["insight"])

    if result["sources"]:
        with st.expander(f"Sources ({len(result['sources'])})"):
            for src in result["sources"]:
                age = f" · {src['age']}" if src.get("age") else ""
                st.markdown(f"- [{src['title']}]({src['url']}){age}")

if st.button("Ask Claude", type="primary") and question:
    with st.spinner("Claude is thinking..."):
        try:
            ticker = strategy_ticker(question)
            if ticker:
                result = cached_strategy(ticker, question)
            else:
                result = answer_data_query(question)
        except Exception as e:
            st.error(f"Something went wrong: {e}")
            st.stop()

    st.divider()

    if result["mode"] == "strategy":
        show_strategy(result)
        st.stop()

    st.subheader("Claude's Insight")
    st.info(result["insight"])

    with st.expander("View generated SQL"):
        st.code(result["sql"], language="sql")

    if result["rows"]:
        st.subheader("Data")
        df = pd.DataFrame(result["rows"], columns=result["columns"])
        st.dataframe(format_table(df), width="stretch")

        if "close" in df.columns and "date" in df.columns:
            st.subheader("Price Chart")
            df["date"] = pd.to_datetime(df["date"])
            df["close"] = df["close"].astype(float).round(2)
            df = df.sort_values("date")
            ticker_count = df["ticker"].nunique() if "ticker" in df.columns else 1
            if ticker_count > 10:
                st.caption(f"Chart skipped: {ticker_count} tickers is too many to plot. Ask about 10 or fewer.")
            else:
                if ticker_count > 1:
                    chart_data = df.pivot(index="date", columns="ticker", values="close")
                else:
                    chart_data = df.set_index("date")[["close"]]
                st.line_chart(chart_data)
    else:
        st.warning("No data returned for this query.")
