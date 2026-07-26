import streamlit as st
import yfinance as yf
import pandas as pd
import numpy as np
import requests
import plotly.graph_objects as go
from plotly.subplots import make_subplots

# ============================================
# PAGE CONFIG
# ============================================
st.set_page_config(page_title="Company Valuation Explorer", page_icon="📊", layout="wide")

st.title("📊 Company Valuation Explorer")
st.caption("Type any company name below — no need to know the stock ticker.")

# ============================================
# TICKER SEARCH (name → ticker, real-time)
# ============================================
def search_ticker(query):
    """Searches Yahoo Finance for tickers matching a company name."""
    if not query or len(query) < 2:
        return []
    url = "https://query2.finance.yahoo.com/v1/finance/search"
    headers = {"User-Agent": "Mozilla/5.0"}
    params = {"q": query, "quotesCount": 8, "newsCount": 0}
    try:
        r = requests.get(url, headers=headers, params=params, timeout=5)
        data = r.json()
        results = []
        for q in data.get("quotes", []):
            symbol = q.get("symbol")
            name = q.get("shortname") or q.get("longname")
            exch = q.get("exchange", "")
            if symbol and name:
                results.append({"symbol": symbol, "name": name, "exchange": exch})
        return results
    except Exception:
        return []


# ============================================
# SIDEBAR: SEARCH + ASSUMPTIONS
# ============================================
with st.sidebar:
    st.header("1. Find a Company")
    query = st.text_input("Company name", placeholder="e.g. Tesla, Aramco, Apple")

    selected_ticker = None
    selected_name = None

    if query:
        matches = search_ticker(query)
        if matches:
            labels = [f"{m['name']}  ·  {m['symbol']}  ({m['exchange']})" for m in matches]
            choice = st.radio("Select the correct match:", labels, index=0)
            idx = labels.index(choice)
            selected_ticker = matches[idx]["symbol"]
            selected_name = matches[idx]["name"]
        else:
            st.warning("No matches found. Try a different spelling.")

    st.divider()
    st.header("2. Assumptions")
    st.caption("Defaults are reasonable — only change these if you understand what they mean.")

    projection_years = st.slider("Projection period (years)", 3, 10, 5)
    growth_cap = st.slider("Max annual growth rate assumed", 0.0, 0.20, 0.06, 0.01,
                             help="Caps how fast we assume cash flow grows, even if history was faster.")
    terminal_growth = st.slider("Long-term (terminal) growth rate", 0.0, 0.05, 0.025, 0.005,
                                  help="Assumed growth forever, after the projection period ends.")
    risk_free_rate = st.slider("Risk-free rate", 0.0, 0.10, 0.045, 0.005,
                                 help="Roughly the current 10-year government bond yield.")
    market_risk_premium = st.slider("Market risk premium", 0.0, 0.10, 0.05, 0.005)

    run_button = st.button("🔍 Run Valuation", type="primary", use_container_width=True)

# ============================================
# MAIN: RUN THE DCF
# ============================================
if not selected_ticker:
    st.info("👈 Search for a company in the sidebar to get started.")
    st.markdown("""
    ### How this works
    1. **Type a company name** (like "Tesla") in the sidebar — no need to know the ticker.
    2. **Pick the right match** from the suggestions.
    3. Click **Run Valuation**.
    4. You'll get an estimate of what the company is actually *worth*, compared to what it's
       currently trading for — based on a real financial model called a **Discounted Cash Flow (DCF)**.
    """)

elif run_button:
    with st.spinner(f"Fetching data for {selected_name} ({selected_ticker})..."):
        try:
            company = yf.Ticker(selected_ticker)
            info = company.info
            cashflow_stmt = company.cashflow
            balance_sheet = company.balance_sheet

            current_price = info.get("currentPrice") or info.get("regularMarketPrice")
            shares_outstanding = info.get("sharesOutstanding")
            market_cap = info.get("marketCap")
            beta = info.get("beta") or 1.0

            if current_price is None or shares_outstanding is None or market_cap is None:
                st.error("This company is missing key data (price, shares outstanding, or market cap). "
                         "Try a larger, more widely-covered company.")
                st.stop()

            # --- Free Cash Flow ---
            operating_cf = cashflow_stmt.loc["Operating Cash Flow"] if "Operating Cash Flow" in cashflow_stmt.index else None
            capex = cashflow_stmt.loc["Capital Expenditure"] if "Capital Expenditure" in cashflow_stmt.index else None

            if operating_cf is None or capex is None:
                st.error("This company's cash flow statement doesn't have the fields this model needs. "
                         "Try a different, larger company.")
                st.stop()

            free_cash_flow = (operating_cf + capex).sort_index()
            fcf_values = free_cash_flow.values
            most_recent_fcf = fcf_values[-1]

            if len(fcf_values) > 1:
                historical_growth_rates = [(fcf_values[i] / fcf_values[i - 1]) - 1
                                            for i in range(1, len(fcf_values)) if fcf_values[i - 1] != 0]
                avg_historical_growth = np.mean(historical_growth_rates) if historical_growth_rates else 0.03
            else:
                avg_historical_growth = 0.03

            growth_rate = min(avg_historical_growth, growth_cap)
            growth_rate = max(growth_rate, 0.01)

            # --- WACC ---
            cost_of_equity = risk_free_rate + beta * market_risk_premium
            cost_of_debt = 0.045
            tax_rate = 0.20

            total_debt = balance_sheet.loc["Total Debt"].iloc[0] if "Total Debt" in balance_sheet.index else 0
            cash_and_equiv = balance_sheet.loc["Cash And Cash Equivalents"].iloc[0] if "Cash And Cash Equivalents" in balance_sheet.index else 0

            total_value = market_cap + total_debt
            weight_equity = market_cap / total_value
            weight_debt = total_debt / total_value if total_value > 0 else 0

            WACC = (weight_equity * cost_of_equity) + (weight_debt * cost_of_debt * (1 - tax_rate))
            WACC = max(WACC, terminal_growth + 0.01)  # safety floor so the math doesn't break

            # --- Project FCF ---
            projected_fcf = []
            current_fcf = most_recent_fcf
            for _ in range(projection_years):
                current_fcf = current_fcf * (1 + growth_rate)
                projected_fcf.append(current_fcf)

            # --- Terminal Value & Present Value ---
            terminal_value = (projected_fcf[-1] * (1 + terminal_growth)) / (WACC - terminal_growth)
            discounted_fcf = [fcf / ((1 + WACC) ** yr) for yr, fcf in enumerate(projected_fcf, start=1)]
            discounted_terminal_value = terminal_value / ((1 + WACC) ** projection_years)

            enterprise_value = sum(discounted_fcf) + discounted_terminal_value
            net_debt = total_debt - cash_and_equiv
            equity_value = enterprise_value - net_debt
            intrinsic_value_per_share = equity_value / shares_outstanding

            upside_downside = (intrinsic_value_per_share / current_price) - 1

            # ============================================
            # RESULTS
            # ============================================
            st.subheader(f"Results: {selected_name} ({selected_ticker})")

            col1, col2, col3, col4 = st.columns(4)
            col1.metric("Intrinsic Value", f"${intrinsic_value_per_share:,.2f}")
            col2.metric("Current Market Price", f"${current_price:,.2f}")
            col3.metric("Implied Upside/Downside", f"{upside_downside:+.1%}")
            col4.metric("WACC (discount rate)", f"{WACC:.2%}")

            if upside_downside > 0.10:
                st.success(f"📈 The model suggests **{selected_name} may be undervalued** — "
                           f"trading below what its projected cash flows imply it's worth.")
            elif upside_downside < -0.10:
                st.warning(f"📉 The model suggests **{selected_name} may be overvalued** — "
                           f"trading above what its projected cash flows imply it's worth.")
            else:
                st.info(f"⚖️ The model suggests **{selected_name} is roughly fairly valued** — "
                        f"close to what its projected cash flows imply.")

            st.divider()

            # --- Charts ---
            fig = make_subplots(
                rows=2, cols=2,
                subplot_titles=("Projected Free Cash Flow", "Intrinsic Value vs Market Price",
                                 "Sensitivity: WACC vs Terminal Growth", "Price History vs Valuation")
            )

            # 1. Projected FCF
            fig.add_trace(go.Bar(x=list(range(1, projection_years + 1)), y=projected_fcf,
                                   marker_color="#2c7fb8", name="Projected FCF"), row=1, col=1)

            # 2. Value comparison
            fig.add_trace(go.Bar(x=["Intrinsic Value", "Market Price"],
                                   y=[intrinsic_value_per_share, current_price],
                                   marker_color=["#2ca25f", "#de2d26"], name="Value"), row=1, col=2)

            # 3. Sensitivity heatmap
            wacc_range = np.linspace(WACC - 0.02, WACC + 0.02, 5)
            growth_range = np.linspace(max(terminal_growth - 0.01, 0), terminal_growth + 0.01, 5)
            sens_matrix = []
            for w in wacc_range:
                row_vals = []
                for g in growth_range:
                    if w <= g:
                        row_vals.append(None)
                        continue
                    tv = (projected_fcf[-1] * (1 + g)) / (w - g)
                    disc_fcf = [f / ((1 + w) ** yr) for yr, f in enumerate(projected_fcf, start=1)]
                    disc_tv = tv / ((1 + w) ** projection_years)
                    ev = sum(disc_fcf) + disc_tv
                    eq_val = ev - net_debt
                    row_vals.append(round(eq_val / shares_outstanding, 2))
                sens_matrix.append(row_vals)

            fig.add_trace(go.Heatmap(
                z=sens_matrix,
                x=[f"{g:.2%}" for g in growth_range],
                y=[f"{w:.2%}" for w in wacc_range],
                colorscale="RdYlGn", showscale=False
            ), row=2, col=1)

            # 4. Price history
            hist = company.history(period="2y")
            fig.add_trace(go.Scatter(x=hist.index, y=hist["Close"], mode="lines",
                                       line=dict(color="#3182bd"), name="Market Price"), row=2, col=2)
            fig.add_hline(y=intrinsic_value_per_share, line_dash="dash", line_color="#2ca25f",
                          row=2, col=2)

            fig.update_layout(height=700, showlegend=False,
                               title_text=f"{selected_name} — Valuation Dashboard")
            st.plotly_chart(fig, use_container_width=True)

            st.divider()
            with st.expander("📋 See the underlying assumptions used"):
                st.write(f"- **Historical FCF growth observed:** {avg_historical_growth:.2%}")
                st.write(f"- **Growth rate used in model (capped):** {growth_rate:.2%}")
                st.write(f"- **Terminal growth rate:** {terminal_growth:.2%}")
                st.write(f"- **Cost of equity (CAPM):** {cost_of_equity:.2%}")
                st.write(f"- **WACC:** {WACC:.2%}")
                st.write(f"- **Net debt:** ${net_debt:,.0f}")
                st.write(f"- **Beta:** {beta:.2f}")

        except Exception as e:
            st.error(f"Something went wrong pulling data for this company: {e}")
            st.caption("Try a larger, more widely-covered public company — very small or "
                       "thinly-traded stocks often have incomplete data on Yahoo Finance.")

st.divider()
st.caption("Built as a learning project — not financial advice. All figures are estimates based on "
           "simplified assumptions.")
