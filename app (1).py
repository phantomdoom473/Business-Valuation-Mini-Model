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
st.set_page_config(page_title="Company Valuation Explorer", layout="wide")

st.title("Company Valuation Explorer")
st.caption("Type a company name — you don't need to know the ticker.")

# ============================================
# TICKER SEARCH (name → ticker, real-time)
# ============================================
@st.cache_data(ttl=3600, show_spinner=False)
def search_ticker(query):
    """Searches Yahoo Finance for tickers matching a company name.

    Cached: Streamlit reruns the whole script on every keystroke, so without
    this the search box fires a Yahoo request per character typed. That is
    what gets the app's IP rate-limited, which in turn is what makes the
    valuation fetch below come back empty.
    """
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
# KEY DATA (price / shares / market cap / currency)
# ============================================
def _pick(source, *keys):
    """First non-empty value among `keys`, from a dict or a fast_info object."""
    for k in keys:
        try:
            v = source.get(k) if hasattr(source, "get") else source[k]
        except Exception:
            v = None
        if v:
            return v
    return None


@st.cache_data(ttl=1800, show_spinner=False)
def load_key_data(symbol):
    """Price, share count, market cap, beta and currency for `symbol`.

    yfinance's `.info` depends on Yahoo's cookie/crumb auth, which fails
    intermittently on shared hosts and fails most often on non-US listings
    such as 2222.SR. So every field has at least one fallback, and anything
    still missing is derived from the fields that did arrive.
    """
    t = yf.Ticker(symbol)
    price = shares = market_cap = currency = beta = None

    # 1. fast_info needs no crumb, so it survives when .info does not.
    try:
        fi = t.fast_info
        price = _pick(fi, "lastPrice", "last_price", "regularMarketPrice")
        shares = _pick(fi, "shares", "sharesOutstanding", "shares_outstanding")
        market_cap = _pick(fi, "marketCap", "market_cap")
        currency = _pick(fi, "currency")
    except Exception:
        pass

    # 2. .info — richer, but the unreliable one.
    try:
        info = t.info or {}
    except Exception:
        info = {}
    price = price or _pick(info, "currentPrice", "regularMarketPrice", "previousClose")
    shares = shares or _pick(info, "sharesOutstanding", "impliedSharesOutstanding")
    market_cap = market_cap or _pick(info, "marketCap")
    currency = currency or _pick(info, "currency", "financialCurrency")
    beta = _pick(info, "beta")

    # 3. The last close is the most dependable price there is.
    if not price:
        try:
            hist = t.history(period="1mo")
            if not hist.empty:
                price = float(hist["Close"].dropna().iloc[-1])
        except Exception:
            pass

    # 4. The full share-count series, when the single field is absent.
    if not shares:
        try:
            sf = t.get_shares_full(start="2023-01-01")
            if sf is not None and len(sf):
                shares = float(sf.dropna().iloc[-1])
        except Exception:
            pass

    # 5. Any two of price / shares / market cap give the third.
    if not shares and market_cap and price:
        shares = market_cap / price
    if not market_cap and shares and price:
        market_cap = shares * price

    return {"price": price, "shares": shares, "market_cap": market_cap,
            "currency": currency or "", "beta": beta or 1.0}


# ============================================
# SIDEBAR: SEARCH + ASSUMPTIONS
# ============================================
with st.sidebar:
    st.header("Find a company")
    query = st.text_input("Company name", placeholder="e.g. Aramco, Apple")

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
    st.header("Assumptions")
    st.caption("The defaults work fine. Only touch these if you know what they mean.")

    projection_years = st.slider("Projection period (years)", 3, 10, 5)
    growth_cap = st.slider("Max annual growth rate assumed", 0.0, 0.20, 0.06, 0.01,
                             help="Caps how fast we assume cash flow grows, even if history was faster.")
    terminal_growth = st.slider("Long-term (terminal) growth rate", 0.0, 0.05, 0.025, 0.005,
                                  help="Assumed growth forever, after the projection period ends.")
    risk_free_rate = st.slider("Risk-free rate", 0.0, 0.10, 0.045, 0.005,
                                 help="Roughly the current 10-year government bond yield.")
    market_risk_premium = st.slider("Market risk premium", 0.0, 0.10, 0.05, 0.005)

    run_button = st.button("Run Valuation", type="primary", use_container_width=True)

# ============================================
# MAIN: RUN THE DCF
# ============================================
if not selected_ticker:
    st.info("Search for a company in the sidebar to get started.")
    st.markdown("""
    Type a company name in the sidebar, pick the right match from the list, and hit Run Valuation.
    You'll get an estimate of what the company is actually worth based on its projected cash
    flows, next to what it's currently trading for. The model behind it is called a discounted
    cash flow, or DCF — the same basic approach analysts use to value real companies.
    """)

elif run_button:
    with st.spinner(f"Fetching data for {selected_name} ({selected_ticker})..."):
        try:
            company = yf.Ticker(selected_ticker)
            key = load_key_data(selected_ticker)
            cashflow_stmt = company.cashflow
            balance_sheet = company.balance_sheet

            current_price = key["price"]
            shares_outstanding = key["shares"]
            market_cap = key["market_cap"]
            beta = key["beta"]
            currency = key["currency"]          # "SAR" for 2222.SR, "USD" for US listings

            missing = [label for label, value in (("price", current_price),
                                                   ("share count", shares_outstanding),
                                                   ("market cap", market_cap)) if not value]
            if missing:
                st.error(
                    f"Couldn't load {', '.join(missing)} for {selected_name}. "
                    "Yahoo Finance is either rate-limiting this app or doesn't carry that "
                    "field for this listing. Wait a minute and try again, or pick a more "
                    "widely-covered company."
                )
                st.stop()

            # --- Free Cash Flow ---
            operating_cf = cashflow_stmt.loc["Operating Cash Flow"] if "Operating Cash Flow" in cashflow_stmt.index else None
            capex = cashflow_stmt.loc["Capital Expenditure"] if "Capital Expenditure" in cashflow_stmt.index else None

            if operating_cf is None or capex is None:
                st.error("This company's cash flow statement is missing fields the model needs. "
                         "Try a different, larger company.")
                st.stop()

            free_cash_flow = (operating_cf + capex).dropna().sort_index()
            fcf_values = free_cash_flow.values

            if len(fcf_values) == 0:
                st.error("This company doesn't have enough complete cash flow data to run the model. "
                         "Try a different, larger company.")
                st.stop()

            most_recent_fcf = fcf_values[-1]

            if len(fcf_values) > 1:
                # Only measure growth off a positive base. A negative prior-year
                # FCF flips the sign and produces a meaningless growth rate.
                historical_growth_rates = [(fcf_values[i] / fcf_values[i - 1]) - 1
                                            for i in range(1, len(fcf_values)) if fcf_values[i - 1] > 0]
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

            wacc_calculated = (weight_equity * cost_of_equity) + (weight_debt * cost_of_debt * (1 - tax_rate))
            wacc_floor = terminal_growth + 0.01       # keeps (WACC - g) from collapsing
            WACC = max(wacc_calculated, wacc_floor)
            wacc_was_floored = WACC > wacc_calculated

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
            col1.metric("Intrinsic Value", f"{intrinsic_value_per_share:,.2f} {currency}".strip())
            col2.metric("Current Market Price", f"{current_price:,.2f} {currency}".strip())
            col3.metric("Implied Upside/Downside", f"{upside_downside:+.1%}")
            col4.metric("WACC (discount rate)", f"{WACC:.2%}")

            if wacc_was_floored:
                st.warning(
                    f"The calculated WACC was {wacc_calculated:.2%}, below the terminal growth "
                    f"rate of {terminal_growth:.2%}. The model floored it at {WACC:.2%} so the "
                    "terminal value stays finite. Treat this valuation as unreliable — lower "
                    "the terminal growth rate and run it again."
                )

            if upside_downside > 0.10:
                st.success(f"{selected_name} looks undervalued in this model. "
                           f"It's trading below what its projected cash flows suggest it's worth.")
            elif upside_downside < -0.10:
                st.warning(f"{selected_name} looks overvalued in this model. "
                           f"It's trading above what its projected cash flows suggest it's worth.")
            else:
                st.info(f"{selected_name} looks roughly fairly valued in this model, "
                        f"close to what its projected cash flows suggest.")

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

            # 4. Price history — optional, so a failure here must not lose the valuation
            try:
                hist = company.history(period="2y")
            except Exception:
                hist = None
            if hist is not None and not hist.empty:
                fig.add_trace(go.Scatter(x=hist.index, y=hist["Close"], mode="lines",
                                           line=dict(color="#3182bd"), name="Market Price"), row=2, col=2)
                fig.add_hline(y=intrinsic_value_per_share, line_dash="dash", line_color="#2ca25f",
                              row=2, col=2)

            fig.update_layout(height=700, showlegend=False,
                               title_text=f"{selected_name} — Valuation Dashboard")
            st.plotly_chart(fig, use_container_width=True)

            st.divider()
            with st.expander("See the assumptions behind this number"):
                st.write(f"- **Historical FCF growth observed:** {avg_historical_growth:.2%}")
                st.write(f"- **Growth rate used in model (capped):** {growth_rate:.2%}")
                st.write(f"- **Terminal growth rate:** {terminal_growth:.2%}")
                st.write(f"- **Cost of equity (CAPM):** {cost_of_equity:.2%}")
                st.write(f"- **WACC:** {WACC:.2%}")
                st.write(f"- **Spread (WACC − terminal growth):** {WACC - terminal_growth:.2%} "
                         "— the smaller this is, the more the valuation swings on the discount rate")
                st.write(f"- **Net debt:** {net_debt:,.0f} {currency}".strip())
                st.write(f"- **Beta:** {beta:.2f}")
                st.write(f"- **Reporting currency:** {currency or 'unknown'}")

        except Exception as e:
            st.error(f"Something went wrong pulling data for this company: {e}")
            st.caption("Try a larger, more widely-covered public company — very small or "
                       "thinly-traded stocks often have incomplete data on Yahoo Finance.")

st.divider()
