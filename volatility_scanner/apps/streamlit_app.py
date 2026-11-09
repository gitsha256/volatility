from __future__ import annotations

import time

import streamlit as st

from volatility_scanner.config import ScannerConfig
from volatility_scanner.market.data import FyersDataError, FyersDataProvider, MarketSnapshot, NSEDataProvider, OptionQuote, SampleMarketDataProvider
from volatility_scanner.strategies.scanner import NiftyScanner, OpportunityScore
from volatility_scanner.market.live import get_live_snapshot


def get_market_snapshot(data_source: str) -> MarketSnapshot:
    if data_source == "FYERS":
        return get_live_snapshot(FyersDataProvider())
    provider = NSEDataProvider() if data_source == "NSE API" else SampleMarketDataProvider()
    return provider.fetch_snapshot(symbol="NIFTY")


def find_option_quote(snapshot: MarketSnapshot, strike: float) -> OptionQuote | None:
    return next((quote for quote in snapshot.option_chain if quote.strike == strike), None)


def chain_expiry_days(snapshot: MarketSnapshot) -> float:
    try:
        expiry = float(snapshot.expiry_data[0]["expiry"])
        if not expiry > 0 or not expiry < float("inf"):
            raise ValueError
    except (IndexError, KeyError, TypeError, ValueError):
        raise FyersDataError("FYERS did not return a valid option-chain expiry.") from None
    return max(0.0, (expiry - time.time()) / 86400.0)


def usable_quote(bid: float | None, ask: float | None) -> bool:
    return bid is not None and ask is not None and bid >= 0 and ask > 0 and ask >= bid


def iv_as_decimal(value: float | None) -> float | None:
    if value is None or value <= 0:
        return None
    return value / 100 if value > 1 else value


@st.fragment(run_every=1.0)
def refresh_live() -> None:
    now = time.monotonic()
    previous = st.session_state.get("last_live_refresh", now)
    st.session_state["last_live_refresh"] = now
    if now - previous >= 0.8:
        st.rerun()


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
            background: linear-gradient(135deg, #fffdf6, #f6edda);
            border: 1px solid #d8ceb9;
            border-radius: 12px;
            padding: 12px 14px;
            min-height: 92px;
        }
        [data-testid="stMetricLabel"] { color: #53594f; }
        [data-testid="stMetricValue"] { color: #292d29; font-size: 1.45rem; }
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
        if data_source == "FYERS":
            refresh_live()
        try:
            snapshot = get_market_snapshot(data_source)
            live_expiry_days = chain_expiry_days(snapshot) if data_source == "FYERS" else None
        except FyersDataError as error:
            st.error(str(error))
            st.stop()

        use_live_iv = st.checkbox("Use live chain IV", value=True) if data_source == "FYERS" else False
        with st.form("scanner_form"):
            futures_price = st.number_input(
                "NIFTY futures", min_value=1.0, value=float(snapshot.futures_price), step=25.0,
                disabled=data_source == "FYERS",
            )
            strike = st.number_input(
                "Strike", min_value=1.0,
                value=float(min(snapshot.option_chain, key=lambda q: abs(q.strike - futures_price)).strike)
                if snapshot.option_chain else float(round(futures_price / 25.0) * 25.0), step=25.0,
                key=f"strike_{data_source}",
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
                disabled=use_live_iv,
                key=f"market_iv_{data_source}",
                help="Chain IV prefills this assumption. Your entered value applies to all selected strategies.",
            )
            forecast_rv = st.number_input(
                "Forecast RV", min_value=0.001, value=0.18, step=0.01, format="%.4f"
            )
            left, right = st.columns(2)
            minutes_left = left.number_input("Minutes left", min_value=0.0, value=180.0, step=15.0)
            expiry_days = right.number_input(
                "Expiry days", min_value=0.0,
                value=live_expiry_days if live_expiry_days is not None else 7.0,
                step=1.0, disabled=live_expiry_days is not None,
                help="FYERS expiry is calculated from the option-chain timestamp.",
            )
            submitted = st.form_submit_button("Run scan", type="primary", use_container_width=True)

    if snapshot.warning:
        st.warning(snapshot.warning)

    expiry_label = snapshot.expiry_data[0]["date"] if snapshot.expiry_data else "not provided"
    st.caption(
        f"Data: **{snapshot.source.upper()}** · NIFTY futures: **{futures_price:,.2f}** "
        f"· Chain expiry: **{expiry_label}**"
    )
    if data_source == "FYERS":
        age = f"{time.time() - snapshot.last_update:.1f}s ago" if snapshot.last_update else "no ticks received"
        st.caption(f"WebSocket: **{snapshot.feed_status}** · Last received: **{age}** · REST: **{snapshot.http_version}**")
        if snapshot.feed_status != "streaming":
            st.warning("Live quotes are not fresh. Displaying the last snapshot; signals are disabled.")

    if submitted or st.session_state.get("scan_source") != data_source or data_source == "FYERS":
        st.session_state.pop("scan_results", None)
        selected_quote = find_option_quote(snapshot, strike)
        try:
            scanner = NiftyScanner(ScannerConfig())
            results: list[OpportunityScore] = []
            if selected_quote is None:
                st.error("Selected strike is unavailable. Choose an exact strike from the option chain.")
                st.stop()
            for strategy in strategies:
                option_type = "PE" if strategy == "long_put" else "CE"
                strategy_iv = iv
                if use_live_iv and selected_quote is not None:
                    leg_ivs = (
                        [selected_quote.ce_iv, selected_quote.pe_iv] if strategy == "long_straddle"
                        else [selected_quote.pe_iv if option_type == "PE" else selected_quote.ce_iv]
                    )
                    if any(value is None or value <= 0 for value in leg_ivs):
                        st.error("Live IV is unavailable for this strategy. Disable 'Use live chain IV' to enter an assumption.")
                        st.stop()
                    strategy_iv = sum(leg_ivs) / len(leg_ivs)
                bid, ask = 100.0, 120.0
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
                        expiry_days=live_expiry_days if live_expiry_days is not None else expiry_days,
                        remaining_minutes=minutes_left,
                        option_type=option_type,
                        bid=bid,
                        ask=ask,
                    )
                )
                if data_source == "FYERS" and snapshot.feed_status != "streaming":
                    results[-1].qualifies = False
                if data_source == "FYERS":
                    quote_times = (
                        [selected_quote.ce_updated_at, selected_quote.pe_updated_at]
                        if strategy == "long_straddle" else
                        [selected_quote.pe_updated_at if option_type == "PE" else selected_quote.ce_updated_at]
                    )
                    if time.time() - min(snapshot.futures_updated_at, *quote_times) > 15:
                        results[-1].qualifies = False
        except ValueError as error:
            st.error(f"Cannot scan these inputs: {error}")
            st.stop()
        st.session_state["scan_results"] = results
        st.session_state["scan_source"] = data_source

    results: list[OpportunityScore] = st.session_state.get("scan_results", [])
    if results:
        best = max(results, key=lambda item: (item.qualifies, item.expected_pnl, item.confidence))
        st.subheader("Opportunity tiles")
        tiles = st.columns(5)
        tiles[0].metric("Best strategy", best.strategy.replace("_", " ").title())
        tiles[1].metric("Forecast RV − IV", f"{best.volatility_spread:+.2%}")
        tiles[2].metric("Variance ratio", f"{best.variance_ratio:.2f}×")
        tiles[3].metric("Forecast move", f"{best.forecast_move:,.1f} pts")
        tiles[4].metric("Status", "Potential" if best.qualifies else "No trade")

        st.subheader("Strategy comparison")
        comparison_rows = [
            {
                "Strategy": result.strategy.replace("_", " ").title(),
                "Status": "Potential" if result.qualifies else "No trade",
                "RV-IV": f"{result.volatility_spread:+.2%}",
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
        st.caption(
            "IV shown as percentages. Derived IV uses bid/ask midpoints, chain expiry, "
            "and a near-ATM put-call parity forward at zero interest. "
            "Unavailable means quotes could not support an estimate."
        )
        chain_rows = [
            {
                "Strike": quote.strike,
                "CE Bid": quote.ce_bid,
                "CE Ask": quote.ce_ask,
                "CE IV": f"{quote.ce_iv:.2%}" if quote.ce_iv else "Unavailable",
                "CE IV source": quote.ce_iv_source,
                "CE quote age": f"{time.time() - quote.ce_updated_at:.1f}s" if quote.ce_updated_at else "No stream tick",
                "PE Bid": quote.pe_bid,
                "PE Ask": quote.pe_ask,
                "PE IV": f"{quote.pe_iv:.2%}" if quote.pe_iv else "Unavailable",
                "PE IV source": quote.pe_iv_source,
                "PE quote age": f"{time.time() - quote.pe_updated_at:.1f}s" if quote.pe_updated_at else "No stream tick",
            }
            for quote in snapshot.option_chain
        ]
        st.dataframe(chain_rows, hide_index=True, use_container_width=True, height=260)


if __name__ == "__main__":
    main()
