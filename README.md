# NIFTY Volatility Scanner

This project implements a compact analytical engine for NIFTY futures + options screening based on the design in the technical brief.

## Features

- FYERS API v3 live futures quotes and NIFTY option-chain snapshots
- Black-76 pricing and IV inversion for futures options
- Greek calculations for CE/PE positions
- Realized-volatility forecasting using EWMA, Parkinson and realized variance
- Horizon alignment for remaining-session analysis
- Strategy scoring for long straddles, long calls and long puts
- SQLite persistence for scored opportunities
- Optional Streamlit dashboard scaffold

## Quick start

CLI:

```bash
python -m main --futures-price 25000 --strike 25000 --iv 0.14 --forecast-rv 0.18 --minutes-left 180 --strategy long_straddle
```

Dashboard:

```bash
pip install -r requirements.txt
streamlit run streamlit_app.py
```

This prints the core metrics, including volatility spread, variance ratio, implied and forecast move, and confidence.

## FYERS live data setup

The dashboard's **FYERS** data source uses the FYERS v3 market-data endpoints for the selected NIFTY futures contract and current-expiry option chain. It does not place orders. FYERS credentials are read from environment variables and must not be committed:

- `FYERS_CLIENT_ID` — your FYERS app ID
- `FYERS_ACCESS_TOKEN` — an access token generated through FYERS' normal authentication flow
- `FYERS_NIFTY_FUTURES_SYMBOL` — the active futures contract symbol from FYERS, such as `NSE:<contract>-FUT`

In PowerShell, set these for the current terminal before starting Streamlit:

```powershell
$env:FYERS_CLIENT_ID = "your-app-id"
$env:FYERS_ACCESS_TOKEN = "your-access-token"
$env:FYERS_NIFTY_FUTURES_SYMBOL = "NSE:<active-contract>-FUT"
python -m streamlit run streamlit_app.py
```

FYERS access tokens expire according to the broker's authentication policy; generate a valid token using the FYERS authentication flow before running the dashboard. The dashboard reports FYERS errors instead of silently substituting sample prices. The sample and NSE data sources remain available for testing.

The FYERS provider fetches a quote snapshot when the app reruns; it is not a websocket tick stream. The scanner's forecast RV remains a manually entered assumption until historical/intraday candle data is connected.

## Modules

- `volatility_scanner/config.py` — scanner defaults and configuration
- `volatility_scanner/pricing.py` — Black-76 pricing, IV inversion and Greeks
- `volatility_scanner/volatility.py` — RV and forecast engine
- `volatility_scanner/data.py` — sample, NSE and FYERS market-data providers
- `volatility_scanner/strategy.py` — opportunity scoring and scanner logic
- `volatility_scanner/storage.py` — SQLite persistence utilities
- `main.py` — CLI entry point

## Design alignment

The implementation follows the recommended architecture:

- Data acquisition is intentionally isolated behind a clean model boundary.
- Option analytics uses Black-76, not Black-Scholes.
- Forecasting combines EWMA + Parkinson + realized volatility.
- Intraday horizon alignment compares appropriate remaining-session variance.
- Mispricing detection is separated from strategy evaluation.
- Risk controls and confidence estimates are included in scoring.

## Notes

This is a research and execution-prep engine, not a ready-to-trade autotrader. The defaults are intentionally conservative and should be validated with historical backtests before live use.
