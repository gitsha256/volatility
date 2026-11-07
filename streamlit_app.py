from __future__ import annotations

import streamlit as st

from volatility_scanner.config import ScannerConfig
from volatility_scanner.data import NSEDataProvider, SampleMarketDataProvider
from volatility_scanner.strategy import NiftyScanner, OpportunityScore


@st.cache_data(ttl=30)
def get_market_snapshot(data_source: str):
    provider = NSEDataProvider() if data_source == "NSE API" else SampleMarketDataProvider()
    return provider.fetch_snapshot(symbol="NIFTY")


def main() -> None:
    st.set_page_config(
        page_title="NIFTY Volatility Scanner",
        page_icon="📊",
        layout="wide",
        initial_sidebar_state="expanded",
    )
    st.markdown(
        """
        <style>
        .block-container { padding-top: 1.15rem; padding-bottom: 1rem; }
        h1 { margin-bottom: .15rem; }
        [data-testid="stMetric"] {
            background: linear-gradient(135deg, #10243a, #173b53);
            border: 1px solid #28536a;
            border-radius: 12px;
            padding: 12px 14px;
            min-height: 92px;
        }
        [data-testid="stMetricLabel"] { color: #b7cfdd; }
        [data-testid="stMetricValue"] { font-size: 1.45rem; }
        [data-testid="stDataFrame"] { border-radius: 10px; }
        section[data-testid="stSidebar"] { min-width: 290px; }
        </style>
        """,
        unsafe_allow_html=True,
    )

    st.title("NIFTY Volatility Monitor")
    st.caption("Intraday options research · decision support only")

    with st.sidebar:
        st.subheader("Scanner inputs")
        data_source = st.selectbox("Market data", ["Sample market", "NSE API"])
        snapshot = get_market_snapshot(data_source)

        with st.form("scanner_form"):
            futures_price = st.number_input(
                "NIFTY futures", min_value=1.0, value=float(snapshot.futures_price), step=25.0
            )
            strike = st.number_input(
                "Strike", min_value=1.0, value=float(round(futures_price / 25.0) * 25.0), step=25.0
            )
            iv = st.number_input("Market IV", min_value=0.001, value=0.14, step=0.01, format="%.4f")
            forecast_rv = st.number_input(
                "Forecast RV", min_value=0.001, value=0.18, step=0.01, format="%.4f"
            )
            left, right = st.columns(2)
            minutes_left = left.number_input("Minutes left", min_value=0.0, value=180.0, step=15.0)
            expiry_days = right.number_input("Expiry days", min_value=0.01, value=7.0, step=1.0)
            option_type = st.selectbox("Option type", ["CE", "PE"])
            strategies = st.multiselect(
                "Strategies",
                ["long_straddle", "long_call", "long_put"],
                default=["long_straddle", "long_call", "long_put"],
            )
            submitted = st.form_submit_button("Run scan", type="primary", use_container_width=True)

    if snapshot.warning:
        st.warning(snapshot.warning)

    st.caption(f"Data: **{snapshot.source.upper()}** · NIFTY futures: **{futures_price:,.2f}**")

    if submitted:
        scanner = NiftyScanner(ScannerConfig())
        results = scanner.evaluate_all_strategies(
            strategies=strategies,
            futures_price=futures_price,
            strike=strike,
            iv=iv,
            forecast_rv=forecast_rv,
            expiry_days=expiry_days,
            remaining_minutes=minutes_left,
            option_type=option_type,
            bid=100.0,
            ask=120.0,
        )
        st.session_state["scan_results"] = results

    results: list[OpportunityScore] = st.session_state.get("scan_results", [])
    if results:
        best = max(results, key=lambda item: (item.qualifies, item.expected_pnl, item.confidence))
        st.subheader("Opportunity tiles")
        tiles = st.columns(5)
        tiles[0].metric("Best strategy", best.strategy.replace("_", " ").title())
        tiles[1].metric("IV − forecast RV", f"{best.volatility_spread:+.2%}")
        tiles[2].metric("Variance ratio", f"{best.variance_ratio:.2f}×")
        tiles[3].metric("Forecast move", f"{best.forecast_move:,.1f} pts")
        tiles[4].metric("Status", "Potential" if best.qualifies else "No trade")

        st.subheader("Strategy comparison")
        comparison_rows = [
            {
                "Strategy": result.strategy.replace("_", " ").title(),
                "Status": "Potential" if result.qualifies else "No trade",
                "IV-RV": f"{result.volatility_spread:+.2%}",
                "Variance": f"{result.variance_ratio:.2f}×",
                "Expected P&L": f"₹{result.expected_pnl:,.0f}",
                "Confidence": f"{result.confidence:.0%}",
            }
            for result in results
        ]
        st.dataframe(
            comparison_rows,
            hide_index=True,
            use_container_width=True,
            height=min(190, 42 + 35 * len(comparison_rows)),
        )
    else:
        st.info("Set your assumptions in the sidebar and select **Run scan** to see the opportunity tiles.")

    with st.expander("Option chain snapshot", expanded=False):
        chain_rows = [
            {
                "Strike": quote.strike,
                "CE Bid": quote.ce_bid,
                "CE Ask": quote.ce_ask,
                "CE IV": quote.ce_iv,
                "PE Bid": quote.pe_bid,
                "PE Ask": quote.pe_ask,
                "PE IV": quote.pe_iv,
            }
            for quote in snapshot.option_chain
        ]
        st.dataframe(chain_rows, hide_index=True, use_container_width=True, height=260)


if __name__ == "__main__":
    main()
