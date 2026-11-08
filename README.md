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
- `FYERS_APP_SECRET` — app secret for initial login and token renewal
- `FYERS_REFRESH_TOKEN` — saved automatically by the login helper
- `FYERS_PIN` — your FYERS account PIN, required for automatic token renewal
- `FYERS_AUTH_MODE` — `browser`, `totp`, or legacy `refresh`
- `FYERS_USERNAME` — FYERS account login ID for TOTP login, separate from App ID
- `FYERS_TOTP_SECRET` — Base32 setup secret for External 2FA TOTP, not a changing six-digit code
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
verifies the login state and saves both access and refresh tokens to `.env` only
after a successful exchange. It never prints tokens or writes the App Secret.
Market-data requests automatically use the saved token, not the App Secret.
Follow the [FYERS authentication flow](https://fyers.in/api-docs/v3/api/authentication/individual-app/authentication-flow).

### Automatic token renewal

For fresh daily TOTP login, see the opt-in setup below. It does not use the
refresh-token API. The following refresh-token limitation applies to `refresh` mode.

**Live verification (8 November 2026):** FYERS rejects the refresh endpoint with
HTTP 400 / code -16: `Refresh token API is currently disabled to comply with SEBI
regulations.` Automatic renewal cannot operate while FYERS disables this API.
The app reports this explicitly and preserves saved tokens. Use `authenticate.py`
to sign in through your browser when needed. The support article's 15-day
refresh-token validity does not guarantee the renewal endpoint is enabled.

Set `FYERS_PIN` directly in your private `.env`, then run `authenticate.py` once
to capture a refresh token (older versions saved only the access token). No daily
browser login is needed only if FYERS re-enables the endpoint and accepts that refresh token. The running app
checks the token's expiry on each refresh, renews within 60 seconds of expiry, and
replaces the WebSocket connection with the renewed credentials. REST authentication
errors (HTTP 401 or FYERS codes -8/-15/-16/-17) trigger one renewal and one retry.
Concurrent renewal requests share a lock; failures wait 60 seconds before retrying.

Tokens are replaced atomically in `.env`, preserving other settings. If access or
refresh tokens were supplied as environment variables, successful renewal also
updates those values in the running process. An external service manager's saved
environment is not changed. Explicit `FyersDataProvider(access_token=...)` values
remain caller-managed and do not trigger automatic renewal.

FYERS states refresh tokens are valid for **15 days**. They cannot provide
indefinite unattended login; sign in again when a refresh token expires, is
revoked, or FYERS requires authentication. See
[FYERS refresh-token validity](https://support.fyers.in/portal/en/kb/articles/what-is-the-function-of-the-refresh-token-in-fyers).
Renewal failures keep existing tokens and show a configuration/sign-in error.
The existing stale-data controls prevent signals while disconnected.

To test renewal immediately after one-time setup:

```powershell
.\.venv\Scripts\python.exe authenticate.py --refresh
```

### Opt-in TOTP login

TOTP login uses FYERS' internal login endpoints rather than the disabled refresh
endpoint. These endpoints are not a supported public authentication API and may
change or require browser interaction. Browser sign-in remains available with
`authenticate.py`; the app never attempts to bypass CAPTCHA or approval screens.

1. Enable **External 2FA TOTP** under FYERS **Profile → Others**, and link your
   authenticator. Keep the Base32 setup secret private. See
   [FYERS TOTP setup](https://support.fyers.in/portal/en/kb/articles/how-to-enable-or-disable-time-based-one-time-password-totp-in-fyers).
2. Set these values directly in your private `.env`:

   ```dotenv
   FYERS_AUTH_MODE=totp
   FYERS_USERNAME=your-account-login-id
   FYERS_TOTP_SECRET=your-base32-setup-secret
   FYERS_PIN=your-four-digit-pin
   ```

   Existing `FYERS_CLIENT_ID`, `FYERS_APP_SECRET` and the registered
   `FYERS_REDIRECT_URI` are also required. The setup secret is not the current
   six-digit authenticator code. Keep your system clock synchronized.
3. Test a fresh login without a browser:

   ```powershell
   .\.venv\Scripts\python.exe authenticate.py --totp
   ```

The app reuses valid access tokens, performs TOTP login when missing or near
expiry, retries rejected REST authentication once, saves tokens atomically, and
replaces the live socket after rotation. No Windows scheduled task is required
while the app is running; startup also checks the token. TOTP is generated
locally with standard HMAC-SHA1 and a 30-second interval. Login validates random
state and the registered redirect before exchanging the code.

Transient connection failures back off for 60 seconds. Credential rejections
stop further automatic attempts until authentication configuration changes or
the app restarts, reducing repeated PIN attempts. Failures preserve existing
tokens and never log PINs, setup secrets, OTPs or response bodies. To disable
unattended login, set `FYERS_AUTH_MODE=browser`.

Alternatively, in PowerShell, set these for the current terminal before starting Streamlit:

```powershell
$env:FYERS_CLIENT_ID = "your-app-id"
$env:FYERS_ACCESS_TOKEN = "your-access-token"
$env:FYERS_NIFTY_FUTURES_SYMBOL = "NSE:<active-contract>-FUT"
python -m streamlit run streamlit_app.py
```

FYERS access tokens expire according to the broker's authentication policy. The
app renews them automatically when refresh credentials are configured; otherwise
it requests sign-in. The dashboard reports FYERS errors instead of silently
substituting sample prices. The sample and NSE sources remain available for testing.

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
