import streamlit as st
import pandas as pd
from decimal import Decimal

st.set_page_config(
    page_title="StockAI",
    page_icon="📈",
    layout="centered"
)

try:
    from claude_query import standalone_question, strategy_ticker, analyze_strategy, answer_data_query
except EnvironmentError as e:
    # Streamlit Cloud hides exception messages, so show the missing setting explicitly
    st.error(f"StockAI isn't configured yet: {e} Add it under the app's Settings → Secrets.")
    st.stop()

DISCLAIMER = "StockAI provides information, not financial advice. Data may be delayed or incomplete; do your own research."

EXAMPLES = [
    "Which 10 stocks had the highest return over the last 3 months?",
    "What is the average closing price of AAPL over the past 3 months?",
    "Which sector had the best average performance this year?",
    "Compare the closing prices of MSFT and GOOGL over the last 30 days",
    "Which stocks had the biggest single-day price drop this year?",
    "Top 5 Information Technology stocks by average trading volume",
    "What's the strategy for NVDA based on the last 3 months?",
    "Is AAPL a buy after its latest earnings?",
]

if "messages" not in st.session_state:
    st.session_state.messages = []

with st.sidebar:
    st.title("📈 StockAI")
    if st.button("New chat", icon="➕", width="stretch"):
        st.session_state.messages = []
        st.rerun()
    st.caption(DISCLAIMER)

st.title("📈 StockAI")
st.caption("Ask anything about S&P 500 stocks: prices, returns, sectors, or whether a stock looks like a buy.")

@st.cache_data(ttl=3600, show_spinner=False)
def cached_strategy(ticker, _question):
    # Keyed on ticker only, so any phrasing within the hour gets the same answer
    return analyze_strategy(ticker, _question)

PCT_WORDS = ("pct", "percent", "return")
PRICE_WORDS = ("open", "high", "low", "close", "price", "sma", "ma_", "eps", "target")

def md(text):
    # Streamlit renders $...$ as LaTeX, which mangles prices like $80.30
    return text.replace("$", "\\$")

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

def render_strategy(result, key):
    ind, sig, fund = result["indicators"], result["signal"], result["fundamentals"]
    val = fund.get("valuation") or {}

    st.subheader(f"{result['name']} ({result['ticker']})")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Signal", sig["signal"], f"score {sig['score']:+d} / {sig['max_score']}", delta_color="off")
    c2.metric("Close", fmt(ind["close"], "$"), f"{ind['return_3m_pct']:.2f}% (3m)" if ind["return_3m_pct"] is not None else None)
    c3.metric("RSI (14)", fmt(ind["rsi_14"]))
    c4.metric("Next earnings", fund.get("next_earnings_date") or "n/a")

    with st.expander("Why this signal?"):
        for rule in sig["rules"]:
            st.markdown(md(f"- **{rule['points']:+d}** {rule['reason']}"))
        st.caption("Score ≥ 2 = BUY, ≤ −2 = SELL, otherwise HOLD. Same data always gives the same signal.")

    st.markdown("**Price vs moving averages**")
    st.line_chart(result["prices"])

    st.markdown(md(result["insight"]))

    with st.expander("Indicators & earnings"):
        rows = [(INDICATOR_LABELS.get(k, k), format_indicator(k, v)) for k, v in ind.items()]
        st.dataframe(pd.DataFrame(rows, columns=["Indicator", "Value"]), hide_index=True, width="stretch", key=f"ind_{key}")
        st.markdown("**Recent earnings (EPS)**")
        if fund.get("recent_earnings"):
            st.dataframe(format_table(pd.DataFrame(fund["recent_earnings"])), hide_index=True, width="stretch", key=f"eps_{key}")
        else:
            st.caption("No earnings data available.")
        st.markdown(md(
            f"P/E {fmt(val.get('trailing_pe'))} trailing, {fmt(val.get('forward_pe'))} forward · "
            f"Analyst target {fmt(val.get('analyst_target_mean'), '$')} · "
            f"Rating {val.get('analyst_recommendation') or 'n/a'}"
        ))

    if result["sources"]:
        with st.expander(f"Sources ({len(result['sources'])})"):
            for src in result["sources"]:
                age = f" · {src['age']}" if src.get("age") else ""
                st.markdown(f"- [{src['title']}]({src['url']}){age}")

def render_data(result, key):
    st.markdown(md(result["insight"]))

    if not result["rows"]:
        return

    df = pd.DataFrame(result["rows"], columns=result["columns"])
    if len(df) > 10:
        with st.expander(f"Data ({len(df)} rows)"):
            st.dataframe(format_table(df), hide_index=True, width="stretch", key=f"data_{key}")
    else:
        st.dataframe(format_table(df), hide_index=True, width="stretch", key=f"data_{key}")

    if "close" in df.columns and "date" in df.columns:
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

def render_message(message, key):
    if message["role"] == "user":
        st.markdown(md(message["content"]))
    elif message.get("error"):
        st.error(message["error"])
    elif message["result"]["mode"] == "strategy":
        render_strategy(message["result"], key)
    else:
        render_data(message["result"], key)

def summarize(result):
    """Short text of an answer, used to resolve follow-up questions."""
    if result["mode"] == "strategy":
        return f"Strategy for {result['ticker']} ({result['name']}): {result['signal']['signal']}"
    return result["insight"][:300]

def answer(question):
    history = [
        {"role": m["role"], "content": m["content"]}
        for m in st.session_state.messages[:-1] if not m.get("error")
    ]
    question = standalone_question(question, history)
    ticker = strategy_ticker(question)
    if ticker:
        return cached_strategy(ticker, question)
    return answer_data_query(question)

pending = st.session_state.pop("pending_question", None)

for i, message in enumerate(st.session_state.messages):
    with st.chat_message(message["role"]):
        render_message(message, i)

if not st.session_state.messages and not pending:
    st.markdown("**Try asking:**")
    cols = st.columns(2)
    for i, example in enumerate(EXAMPLES):
        if cols[i % 2].button(example, width="stretch", key=f"example_{i}"):
            st.session_state.pending_question = example
            st.rerun()
    st.caption(DISCLAIMER)

question = st.chat_input("Ask about any S&P 500 stock…") or pending

if question:
    st.session_state.messages.append({"role": "user", "content": question})
    with st.chat_message("user"):
        st.markdown(md(question))

    with st.chat_message("assistant"):
        with st.spinner("Analysing…"):
            try:
                result = answer(question)
                reply = {"role": "assistant", "content": summarize(result), "result": result}
            except Exception:
                reply = {
                    "role": "assistant",
                    "content": "Error",
                    "error": "Sorry, I couldn't answer that. Try rephrasing, or check the ticker is in the S&P 500.",
                }
    st.session_state.messages.append(reply)
    st.rerun()
