from __future__ import annotations

import streamlit as st

from volatility_scanner.config import ScannerConfig
from volatility_scanner.data import (
    FyersDataError,
    FyersDataProvider,
    MarketSnapshot,
    NSEDataProvider,
    OptionQuote,
    SampleMarketDataProvider,
)
from volatility_scanner.strategy import NiftyScanner, OpportunityScore


def get_market_snapshot(data_source: str) -> MarketSnapshot:
    if data_source == "FYERS":
        return FyersDataProvider().fetch_snapshot(symbol="NIFTY")
    provider = NSEDataProvider() if data_source == "NSE API" else SampleMarketDataProvider()
    return provider.fetch_snapshot(symbol="NIFTY")


def find_option_quote(snapshot: MarketSnapshot, strike: float) -> OptionQuote | None:
    return min(snapshot.option_chain, key=lambda quote: abs(quote.strike - strike), default=None)


def usable_quote(bid: float | None, ask: float | None) -> bool:
    return bid is not None and ask is not None and bid >= 0 and ask > 0 and ask >= bid


def iv_as_decimal(value: float | None) -> float | None:
    if value is None or value <= 0:
        return None
    return value / 100 if value > 1 else value


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
        data_source = st.selectbox("Market data", ["FYERS", "Sample market", "NSE API"])
        try:
            snapshot = get_market_snapshot(data_source)
        except FyersDataError as error:
            st.error(str(error))
            st.stop()

        with st.form("scanner_form"):
            futures_price = st.number_input(
                "NIFTY futures", min_value=1.0, value=float(snapshot.futures_price), step=25.0
            )
            strike = st.number_input(
                "Strike", min_value=1.0, value=float(round(futures_price / 25.0) * 25.0), step=25.0
            )
            strategies = st.multiselect(
                "Strategies",
                ["long_straddle", "long_call", "long_put"],
                default=["long_straddle", "long_call", "long_put"],
            )
            selected_quote = find_option_quote(snapshot, strike)
            available_ivs: list[float] = []
            if selected_quote is not None:
                if "long_call" in strategies or "long_straddle" in strategies:
                    ce_iv = iv_as_decimal(selected_quote.ce_iv)
                    if ce_iv is not None:
                        available_ivs.append(ce_iv)
                if "long_put" in strategies or "long_straddle" in strategies:
                    pe_iv = iv_as_decimal(selected_quote.pe_iv)
                    if pe_iv is not None:
                        available_ivs.append(pe_iv)
            chain_iv = sum(available_ivs) / len(available_ivs) if available_ivs else None
            iv = st.number_input(
                "Market IV (decimal)",
                min_value=0.001,
                value=chain_iv or 0.14,
                step=0.01,
                format="%.4f",
                help="FYERS IV is prefilled when available; otherwise enter a decimal such as 0.14 for 14%.",
            )
            forecast_rv = st.number_input(
                "Forecast RV", min_value=0.001, value=0.18, step=0.01, format="%.4f"
            )
            left, right = st.columns(2)
            minutes_left = left.number_input("Minutes left", min_value=0.0, value=180.0, step=15.0)
            expiry_days = right.number_input("Expiry days", min_value=0.01, value=7.0, step=1.0)
            submitted = st.form_submit_button("Run scan", type="primary", use_container_width=True)

    if snapshot.warning:
        st.warning(snapshot.warning)

    expiry_label = snapshot.expiry_data[0]["date"] if snapshot.expiry_data else "not provided"
    st.caption(
        f"Data: **{snapshot.source.upper()}** · NIFTY futures: **{futures_price:,.2f}** "
        f"· Chain expiry: **{expiry_label}**"
    )

    if submitted:
        selected_quote = find_option_quote(snapshot, strike)
        try:
            scanner = NiftyScanner(ScannerConfig())
            results: list[OpportunityScore] = []
            if data_source == "FYERS" and selected_quote is None:
                st.error("No FYERS option-chain strike is close to the selected strike.")
                st.stop()
            for strategy in strategies:
                option_type = "PE" if strategy == "long_put" else "CE"
                strategy_iv = iv
                bid, ask = 100.0, 120.0
                if selected_quote is not None:
                    live_iv = iv_as_decimal(
                        selected_quote.pe_iv if option_type == "PE" else selected_quote.ce_iv
                    )
                    if strategy == "long_straddle":
                        ce_iv = iv_as_decimal(selected_quote.ce_iv)
                        pe_iv = iv_as_decimal(selected_quote.pe_iv)
                        if ce_iv is not None and pe_iv is not None:
                            live_iv = (ce_iv + pe_iv) / 2.0
                    if live_iv is not None:
                        strategy_iv = live_iv

                if data_source == "FYERS":
                    if strategy == "long_straddle":
                        if not (
                            usable_quote(selected_quote.ce_bid, selected_quote.ce_ask)
                            and usable_quote(selected_quote.pe_bid, selected_quote.pe_ask)
                        ):
                            st.error("FYERS did not return valid CE and PE bid/ask quotes for this straddle.")
                            st.stop()
                        bid = float(selected_quote.ce_bid) + float(selected_quote.pe_bid)
                        ask = float(selected_quote.ce_ask) + float(selected_quote.pe_ask)
                    else:
                        selected_bid = selected_quote.ce_bid if option_type == "CE" else selected_quote.pe_bid
                        selected_ask = selected_quote.ce_ask if option_type == "CE" else selected_quote.pe_ask
                        if not usable_quote(selected_bid, selected_ask):
                            st.error(f"FYERS did not return a valid {option_type} bid/ask quote at this strike.")
                            st.stop()
                        bid, ask = float(selected_bid), float(selected_ask)

                results.append(
                    scanner.evaluate_single_strategy(
                        strategy=strategy,
                        futures_price=futures_price,
                        strike=strike,
                        iv=strategy_iv,
                        forecast_rv=forecast_rv,
                        expiry_days=expiry_days,
                        remaining_minutes=minutes_left,
                        option_type=option_type,
                        bid=bid,
                        ask=ask,
                    )
                )
        except ValueError as error:
            st.error(f"Cannot scan these inputs: {error}")
            st.stop()
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
