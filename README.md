# NIFTY Volatility Scanner

This project implements a compact analytical engine for NIFTY futures + options screening based on the design in the technical brief.

## Features

- FYERS API v3 WebSocket futures and option bid/ask streaming
- Persistent REST connections with HTTP/3 negotiation and HTTP/2 fallback
- Black-76 pricing and IV inversion for futures options
- Greek calculations for CE/PE positions
- Realized-volatility forecasting using EWMA, Parkinson and realized variance
- Horizon alignment for remaining-session analysis
- Strategy scoring for long straddles, long calls and long puts
- SQLite persistence for scored opportunities
- Streamlit dashboard with automatic live updates and feed status

## Quick start

Use **Python 3.12** (supported range: 3.10–3.12). The official FYERS SDK pins
dependencies that are incompatible with Python 3.13/3.14. Create a virtual
environment and install dependencies before running either entry point:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

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

Recommended local setup: copy the template to `.env` beside this README:

```powershell
Copy-Item .env.example .env
```

Edit `.env` locally and fill in the three values above. The app reads only this
repository's `.env`, regardless of the working directory. Existing environment
variables take precedence; explicit provider arguments take precedence over both.
Values are read without variable interpolation and are not added to the process
environment. Restart the dashboard or rerun the scan after updating the file.

`.env` is ignored by Git, but remains a plaintext file: restrict access to your
Windows account and keep it out of shared folders. Never put real credentials in
`.env.example`. The scanner needs no App Secret; use it only during token generation.

Generate an access token locally:

```powershell
.\.venv\Scripts\python.exe authenticate.py
```

The helper reads optional `FYERS_APP_SECRET` and `FYERS_REDIRECT_URI` values from
`.env` (environment variables override them), or prompts for missing values.
Use the exact redirect URI registered in the FYERS API dashboard. The helper
opens the login link in your browser. Sign in to FYERS,
then paste the full redirected URL into the terminal's hidden prompt. The helper
verifies the login state and saves the access token to `.env` only after a
successful exchange. It never prints the token or writes the App Secret.
Market-data requests automatically use the saved token, not the App Secret.
Follow the [FYERS authentication flow](https://fyers.in/api-docs/v3/api/authentication/individual-app/authentication-flow).
Run this again when the token expires. Then start or restart Streamlit.

Alternatively, in PowerShell, set these for the current terminal before starting Streamlit:

```powershell
$env:FYERS_CLIENT_ID = "your-app-id"
$env:FYERS_ACCESS_TOKEN = "your-access-token"
$env:FYERS_NIFTY_FUTURES_SYMBOL = "NSE:<active-contract>-FUT"
python -m streamlit run streamlit_app.py
```

FYERS access tokens expire according to the broker's authentication policy; generate a valid token using the FYERS authentication flow before running the dashboard. The dashboard reports FYERS errors instead of silently substituting sample prices. The sample and NSE data sources remain available for testing.

### Live feed

The dashboard bootstraps the chain and futures quote through REST, then subscribes
to the futures and both option legs using FYERS' official `SymbolUpdate`
WebSocket. One shared connection updates an in-memory quote cache; browser
refreshes read that cache instead of polling REST. The dashboard redraws every
second and recalculates IV and scores. The chain universe and expiry are refreshed
through REST every five minutes. SDK reconnects resubscribe the current universe.

Connection status, receipt age and the negotiated REST protocol are displayed.
REST uses a persistent curl session configured to attempt HTTP/3 and fall back to
HTTP/2 or HTTP/1.1 as supported by FYERS and the network. HTTP/2 was observed in
local live tests; HTTP/3 is supported by the client but not confirmed end to end.
WebSockets use their own persistent connection, independent of REST HTTP versions.
No end-to-end latency guarantee is implied by a protocol selection.

Signals are disabled until the socket receives updates, while disconnected, or
when the selected futures/option quotes have not received updates for 15 seconds.
Last-known prices remain visible with feed status. No sample prices substitute for
a failed FYERS connection. Refresh the page after renewing credentials in `.env`;
the shared feed is replaced when credentials or futures symbol change.

`Use live chain IV` is enabled by default for FYERS. Disable it to enter a manual
IV assumption. Futures price always follows the feed. Forecast RV and remaining
session minutes remain manual assumptions until a historical forecast and session
calendar are connected. This dashboard is not an order execution system.

When FYERS omits IV, the provider estimates it with Black-76 from bid/ask
midpoints and the actual option expiry. The forward is inferred from a near-ATM
call/put pair of that expiry using put-call parity at zero interest, rather than
using a different-expiry futures contract. The chain displays IV percentages and
labels derived values. Expired, invalid or model-inconsistent quotes remain
unavailable; these estimates are not broker-supplied IV.

The dashboard scans on first load and when the data source changes. Select an
exact strike from the chain; unavailable strikes are rejected. Manual IV edits
apply to all selected strategies when live IV is disabled. FYERS
expiry days are derived from the chain's expiry timestamp and cannot be edited.
Qualification requires positive remaining time, positive time to expiry, and
positive estimated P&L after the model's costs. P&L and confidence remain research
heuristics, not calibrated trading forecasts.

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
