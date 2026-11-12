# NIFTY Volatility Scanner

This project implements a compact analytical engine for NIFTY futures + options screening based on the design in the technical brief.

See [implemented features and API inventory](docs/implemented-features.md) for capture, algorithms, dashboards, WebSockets and the complete callable Upstox API list.

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
- `volatility_scanner/analytics/pricing.py` — Black-76 pricing, IV inversion and Greeks
- `volatility_scanner/analytics/volatility.py` — RV and forecast engine
- `volatility_scanner/market/data.py` — sample, NSE and FYERS market-data providers
- `volatility_scanner/strategies/scanner.py` — opportunity scoring and scanner logic
- `volatility_scanner/storage/opportunities.py` — SQLite persistence utilities
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

## WebSocket market recorder

Run independently of the dashboard using the existing FYERS `.env` credentials:

```powershell
.venv/Scripts/python.exe record_market.py
```

Each expiry/strike has a dedicated in-memory asyncio channel and consumer (CE/PE
share that strike's channel). Socket callbacks enqueue copies without disk I/O.
Strike consumers forward updates to one shared writer, which batches across
all strikes for up to 50 ms into the SQLite WAL journal. No ticks are intentionally dropped or deduplicated. Queues
are unbounded: sustained overload grows memory, rather than discarding updates.
A crash can lose queued/uncommitted updates. Ctrl+C drains queues and exports.

Files appear under `recordings/YYYY-MM-DD/`:

- `<expiry_epoch>_<strike>.xlsx`: every received option update, both legs,
  receipt timestamp in IST, source, and all delivered fields.
- `NIFTY50.xlsx`: latest spot-index fields sampled once per second, including
  last receipt time and stale/disconnected flags. Empty price before first tick;
  unchanged prices are repeated. This is a latest-value sample, not OHLC.
- `recordings/capture.sqlite3`: persistent history retained across restarts.

Excel refreshes every 60 seconds and on shutdown. Close workbooks to allow Windows
to replace them. Failed replacements leave the journal and prior workbook intact.
Large files split into sheets at Excel's row limit. Export reads the journal on
another thread while capture continues. Restarting preserves and re-exports history.
Use a separate output directory per recorder process.

Recorder selects the current (first returned) expiry only: the 24 nearest
strikes strictly below NIFTY spot and 24 strictly above, with both CE and PE.
An exact spot-matching strike is excluded. Total: 96 options plus NIFTY 50,
within the Standard plan's 100-instrument limit.
Selection refreshes every 30 seconds; removed contracts are unsubscribed and new
ones subscribed. Previously captured history is retained. REST refresh also saves
OI/IV fields; intermediate changes in REST-only fields cannot be captured.
WebSocket captures every delivered update for the selected contracts. Disconnected
periods cannot be backfilled. Restart after token renewal.
The running session uses `--output recordings/current_expiry` to separate it from
previous full-chain snapshots.

```powershell
.venv/Scripts/python.exe record_market.py --output recordings --strikes 50 --chain-interval 30 --export-interval 60
```

## Upstox TOTP login

Enable TOTP on your Upstox account and create an individual API app. Fill in
`UPSTOX_API_KEY`, `UPSTOX_PIN` (six digits), and `UPSTOX_TOTP_SECRET` (Base32
setup secret) in `.env`, then run:

```powershell
.venv/Scripts/python.exe authenticate_upstox.py
```

Uses the documented `/v2/totp-login/authorization/token` endpoint. Successful login
atomically saves `UPSTOX_ACCESS_TOKEN`, preserving FYERS settings. Upstox revokes
the app's previous access token when issuing a new one. Rejected login is not
retried automatically. Tokens and credentials are never printed. The existing
market recorder remains on FYERS; Upstox feed integration is a separate step.

## Upstox live recording

Capture is currently stopped. Start explicitly:

```powershell
.venv/Scripts/python.exe record_upstox.py
```

Default output: `D:/market-data/volatility/upstox`. The old workspace recordings
were preserved at `D:/market-data/volatility/legacy-recordings`; the workspace
`recordings` path is a junction to that archive. Existing daily Excel files are
preserved but no longer refreshed by the hourly exporter.

Uses the saved Upstox token; configured TOTP login handles a missing/expired JWT.
For a rejected token, run `authenticate_upstox.py`, then restart. The V3 full-mode
WebSocket subscribes to the selected NIFTY expiry scope plus NIFTY 50. The default
is current and next expiry; optional Plus modes and plan limits are described below. REST is used for startup context
and contract discovery every five minutes. TLS verification, TCP_NODELAY and TCP
keepalive are enabled. Reconnect restores subscriptions without redundant requests.

Capture pipeline:

1. Socket callback timestamps and queues original binary Protobuf frames.
2. A decoder thread processes frames and routes owned dictionaries to asyncio.
3. Dedicated expiry/strike channels preserve CE/PE updates and second snapshots.
4. One database writer combines all channels and raw frames into bounded-size
   batches, waiting at most 50 ms to collect a batch. SQLite WAL and synchronous
   FULL remain enabled. Actual commit latency depends on backlog and disk speed.
5. A separate spawned process exports changed hourly Excel chunks every five
   minutes. Closed, unchanged hours are skipped. Export uses a fixed event-ID
   boundary; updates arriving during export are included in the next pass.

All delivered option and index updates are retained. One-second snapshots repeat
latest values with stale/connected flags and last receipt time. Streamed fields
include OI, IV, Greeks, five-level depth, volume, prices/times and feed timestamp.
`socket_received_at_ist` records callback arrival before decoding;
`receive_to_dispatch_ms` measures local processing delay, not exchange latency.
Nested objects are flattened; arrays remain JSON in Excel. REST context retains
its original schema/units; no IV unit normalization is applied. REST strike-level
PCR is `rest_strike_pcr`; full-expiry PCR is not calculated.

Storage:

- Journal: `capture.sqlite3`, with `events` and binary `raw_frames` tables.
- Excel: `YYYY-MM-DD/HH/<expiry_epoch>_<strike>.xlsx` and `NIFTY50.xlsx`.
- Export checkpoint: `excel-progress.json`; advance only after successful exports.
- Health: `health.json`, refreshed every five seconds: queue depths, pending-write
  age, last commit/decode times, committed event/frame counts, incoming bytes,
  free disk space, connection state and latest export duration/result.

`--min-free-gb` defaults to 1 GB. Low disk space stops capture and drains queues;
final export is skipped below reserve. Unbounded queues avoid intentional drops,
but sustained overload can exhaust memory. Disk errors stop capture. An OS crash,
power failure, forced kill or write failure can lose queued/uncommitted frames.
Raw replay can reconstruct only frames already committed to the journal. No
exchange-level completeness, constant OI refresh rate or outage backfill is claimed.
Only one recorder per output directory is allowed through an OS file lock.

Gracefully stop (drains socket decoder, strike queues and writer, then exports):

```powershell
.venv/Scripts/python.exe record_upstox.py --stop
```

For a custom output directory pass the same `--output` to start and stop commands.
Force-killing a process bypasses draining. Ctrl+C also initiates shutdown.

Offline replay into a new output directory (does not authenticate/connect):

```powershell
.venv/Scripts/python.exe replay_upstox.py --source D:/market-data/volatility/upstox/capture.sqlite3 --output D:/market-data/volatility/replay
```

Replay reconstructs raw tick rows using saved contract mappings and socket receipt
timestamps. It does not synthesize historical one-second snapshots or copy raw
frames into the replay journal. Original source is opened read-only.

Offline synthetic pipeline benchmark:

```powershell
.venv/Scripts/python.exe benchmark_capture.py --frames 10000
```

The benchmark uses generated five-level Protobuf frames across 147 strike channels,
checks raw and decoded row counts, and reports throughput/commits. It never opens
a market-data connection. Results are local synthetic throughput, not broker
latency or a guarantee under real peak traffic.

## Optional Plus capture profiles

Implementation is ready; these commands do not run unless you invoke them.
Default scope is the current and next NIFTY option expiry (`front_two`). All
strikes/CE/PE in those two expiries are included. Normal full feed, raw Protobuf
and one-second snapshots are enabled by default. Other feeds are opt-in.

Example with the optional NIFTY features enabled:

```powershell
.venv/Scripts/python.exe record_upstox.py --expiry-scope front_two --context --depth-contracts 50 --analytics local,chain,oi,pcr,max-pain,fii,dii,calendar
```

| Option | Behavior |
|---|---|
| `--expiry-scope front_two` | Current + next option expiry; default |
| `--expiry-scope current` | Current expiry only |
| `--expiry-scope all` | All returned expiries, subject to documented capacity |
| `--feed-mode full` | Prices, OI/IV/Greeks, five-level depth; default |
| `--feed-mode option_greeks` | Broker's first-level/Greeks feed, including delivered OI/IV |
| `--feed-mode ltpc` | Last-trade/close fields only; OI/IV are unavailable |
| `--depth-contracts 0..50` | Upgrade nearest-expiry contracts nearest spot to D30; default 0 |
| `--context` | Add streaming India VIX and exact-underlying active NIFTY futures |
| `--analytics local` | Calculate per-expiry PCR/max pain from streamed OI once per second |
| `--analytics chain,oi,pcr,max-pain` | Full REST chain (including POP/previous OI when supplied) and broker analytics for each selected expiry; refresh after each round +60s |
| `--analytics fii,dii,calendar` | Daily institutional activity and session calendar snapshots |
| `--no-snapshots` | Disable repeated one-second latest-value rows |
| `--no-raw-frames` | Disable original Protobuf archival/replay |
| `--no-ticks` | Disable decoded tick rows; selected snapshots/raw archival remain independent |

Depth selection is deterministic: prioritize nearest expiry, then distance from
spot. D30 is capped at 50 total instruments. The planner conservatively caps
full/D30/context depth coverage together at 1500 in mixed mode; remaining contracts
may use `option_greeks`, capped at 2000. No instruments are silently dropped: an
oversized universe fails with an explicit message. D30 requires full feed mode.
`coverage.json` lists every selected contract, its mode, futures, and mode counts.
This records requested coverage, not a guarantee of broker delivery.

Context and local analytics use WebSocket data. Supplemental REST analytics run
independently from socket capture; they are not per-second broker values. Local
PCR/max pain are withheld until all selected contracts have OI. Receipt timestamps
are supplied for freshness assessment; a repeated value is not necessarily a new
OI observation. No trading signals are implied by these metrics.

REST calls use a persistent session, a global per-process pacing lock (at most
about 3.3 requests/second), and a conservative 1800-requests/30-minute budget.
Other programs using the same account can still consume the account's allowance.
Rejected/unavailable optional endpoints are logged and visible in health status;
stream capture continues. The calendar/FII/DII requests are once per day per run.
The app stores full response payloads. Excel splits oversized text/JSON into
`__part1`, `__part2`, etc. columns to avoid silent cell truncation; join parts in
order to reconstruct the original value. The SQLite journal remains authoritative.
Zero-valued protobuf scalar fields are explicitly decoded rather than omitted.

Historical/expired candles are a separate explicit job; they never run with live
capture automatically. Example for selected keys and dates:

```powershell
.venv/Scripts/python.exe capture_upstox_history.py --from 2026-11-02 --to 2026-11-06 --instruments 'NSE_INDEX|Nifty 50,NSE_INDEX|India VIX'
```

For an expired NIFTY option expiry, use `--expired-expiry YYYY-MM-DD` instead of
`--instruments`. The command discovers contracts for that expiry, fetches 1-minute
OHLCV/OI in <=28-day windows, then exports hourly workbooks. Output defaults to
`D:/market-data/volatility/history`. It does not recover historical ticks, depth,
or Greeks. Repeating a historical job currently appends another capture of its
responses; use a distinct output directory when avoiding duplicate rows matters.

This is NIFTY market-data capture. Portfolio/order execution, stock fundamentals,
stock news and unrelated market universes are outside this capture profile.
Optional Plus endpoints have offline implementation tests; availability and D30
subscription acceptance still require a future user-requested live verification.


## Source layout and complete Upstox APIs

Code and tests are grouped by content and usage. Root Python files remain thin
command entry points; existing launch commands are unchanged.

```text
volatility_scanner/
  config.py                 shared configuration and repository .env path
  analytics/                pricing, Greeks, volatility models
  strategies/               scanner and opportunity scoring
  market/                   market-data providers and existing live cache
  security/                 shared token storage, expiry hints and RFC 6238 TOTP
  storage/                  journal, Excel export, opportunities, process lock
  brokers/
    fyers/                  FYERS authentication and login
    upstox/
      auth.py               Market/Algo TOTP token renewal
      api/                  credentials, catalogue, sync/async clients, rate budgets
      market/               NIFTY universe, subscription coverage and analytics
      streams/              market Protobuf and portfolio JSON WebSockets
      specs/                official OpenAPI snapshot and documentation coverage
  apps/                     dashboard, CLIs, capture, replay, benchmarks, sandbox tests
tests/
  analytics/                model and strategy tests
  brokers/fyers/            FYERS authentication tests
  brokers/upstox/           Upstox auth, API, stream and coverage tests
  market/                   provider/live-feed tests
  storage/                  durability, exports, replay and failure tests
  apps/                     dashboard tests
```

Full endpoint inventory: [Upstox API reference](docs/upstox-api.md).
The bundled catalogue has 105 entries covering the official OpenAPI contract and
100 documented API reference pages, including the V3 feed routes absent from
Swagger. V2 market feeds are discontinued and explicitly rejected locally.
`publishEvent` is present in the published contract without a public reference page.
Not every operation is available in sandbox: only the seven flagged order routes
are accepted there. No sandbox request falls back to a live host.

Inspect supported operations without credentials or network requests:

```powershell
.venv/Scripts/python.exe upstox_api.py --list
.venv/Scripts/python.exe upstox_api.py --list trading
.venv/Scripts/python.exe upstox_api.py --describe placeOrder
```

Synchronous client (one persistent HTTP/2-capable session):

```python
from volatility_scanner.brokers.upstox.api.client import APIClient

with APIClient("market") as api:
    contracts = api.market.getOptionContracts(
        query={"instrument_key": "NSE_INDEX|Nifty 50"}
    )
```

Every operation also supports `api.call("operationId", path={}, query={},
headers={}, body=...)`. The names/sections come from the bundled contract and
`--describe`. Optional bodies must be omitted rather than supplied as `None`.
Requests are validated against cached JSON Schema validators before sending.
The returned value is the complete decoded response dictionary; no response
fields are discarded by model conversion.

Async client (persistent libcurl async session, up to eight concurrent transfers):

```python
from volatility_scanner.brokers.upstox.api.client import AsyncAPIClient

async with AsyncAPIClient("market") as api:
    result = await api.market.getOptionContracts(
        query={"instrument_key": "NSE_INDEX|Nifty 50"}
    )
```

`call_many([(operation_id, request_sections), ...], concurrency=4)` is available
on both clients. It returns results or exception objects in input order, preserving
partial successes. Clients share per-process sliding-window budgets by environment. Standard limits
are scoped per endpoint; order writes share the documented combined allowance.
Payout, payment-read, IPO and TOTP limits have separate budgets. Account activity in
other applications can consume the same broker allowance. `MarketREST` remains a
separate conservative discovery/analytics transport for the live recorder.

Profiles read separate `.env` entries:

- `market`: `UPSTOX_ACCESS_TOKEN`, `UPSTOX_API_KEY`, optional `UPSTOX_API_SECRET`.
- `algo`: `UPSTOX_ALGO_ACCESS_TOKEN`, `UPSTOX_ALGO_API_KEY`, `UPSTOX_ALGO_API_SECRET`.
- `sandbox`: `UPSTOX_SANDBOX_ACCESS_TOKEN`, `UPSTOX_SANDBOX_API_KEY`, `UPSTOX_SANDBOX_API_SECRET`.

API secrets are used only for browser OAuth code exchange, not market requests.
The existing `UUPSTOX_APP_SECRET` spelling is accepted as a legacy Market-profile
alias; prefer `UPSTOX_API_SECRET` in new configuration. Sandbox tokens are generated
in Developer Apps and do not support TOTP login.

```powershell
.venv/Scripts/python.exe authenticate_upstox.py --profile market
.venv/Scripts/python.exe authenticate_upstox.py --profile algo
```

Algo login can use `UPSTOX_ALGO_PIN`/`UPSTOX_ALGO_TOTP_SECRET`, falling back to the
shared account `UPSTOX_PIN`/`UPSTOX_TOTP_SECRET`. If Market and Algo have the same
non-empty API key, token renewal updates both token entries atomically because
Upstox revokes the old token for that app. Distinct API keys are never synchronized;
Sandbox is never synchronized with live profiles. Existing client instances must
be recreated after an external token renewal.

Live mutations are disabled by default. Calling one requires an explicit
`allow_live_mutation=True` argument or constructing the client with
`allow_live_mutations=True`. This exposes the API without executing a trade merely
because keys were added. The CLI has a matching `--allow-live-mutations` flag.
Order writes route to the documented HFT host; sandbox writes always route to
`api-sandbox.upstox.com`. No automatic retry is made after an ambiguous order
submission. `APIError.outcome_unknown` marks uncertain mutation outcomes; reconcile
orders before deciding to retry. No live order, payout, IPO, position conversion,
static-IP update or kill-switch mutation was performed during implementation.

Market and account WebSockets:

```python
from volatility_scanner.brokers.upstox.api.client import APIClient

with APIClient("market") as api:
    with api.market_stream(
        instrument_keys=["NSE_INDEX|Nifty 50"], mode="full", on_message=handle_market
    ):
        wait_for_stop()

with APIClient("algo") as api:
    with api.portfolio_stream(
        update_types=("order", "gtt_order", "position", "holding"),
        on_message=handle_portfolio,
    ):
        wait_for_stop()
```

`handle_market`, `handle_portfolio` and `wait_for_stop` are application callbacks.
Market streams support `ltpc`, `option_greeks`, `full`, and `full_d30`, with
subscription capacity validation. Portfolio updates decode in a worker thread,
re-authorize on reconnect, and validate signed socket hosts. Both verify TLS and
use TCP_NODELAY. Sandbox has no documented market/portfolio stream and is rejected.
Broker subscription limits apply across the account; local validation cannot see
connections opened by other programs. V2 market streaming is discontinued.

Run explicit sandbox-only mutation verification:

```powershell
.venv/Scripts/python.exe verify_upstox_sandbox.py
```

This tests V2/V3 place, modify and cancel, plus multi-order placement and cleanup
cancellations. It cannot accept a live profile. Latest implementation verification
successfully exercised all seven sandbox routes, a live read-only Algo profile
request, and the Market/Portfolio WebSocket handshakes. No account-event mutation
was generated to force holdings/position notifications; event handling is tested
with fixtures. Sandbox success does not verify exchange execution or live static-IP
permissions. Other endpoints are schema/routing tested, not all live-executed.

Offline performance checks:

```powershell
.venv/Scripts/python.exe benchmark_upstox_api.py --iterations 10000
.venv/Scripts/python.exe benchmark_capture.py --frames 100000
.venv/Scripts/python.exe -m unittest discover -s tests
```

API preparation benchmarks exclude Python import time, networking, rate-limit
waits and broker latency. Cached catalogue/validators avoid parsing the contract
per request; async I/O and bounded pools avoid per-call client/thread-pool creation.
Secrets are hidden from credential representations, error messages and CLI output.


For browser OAuth, `authorization_url()` creates and stores a random state by
default. `exchange_redirect(callback_url)` checks the registered callback target,
state and single-use code before exchange. `exchange_code(code)` is also exposed
for applications that perform their own callback validation. Authentication never
requires printing credentials. The catalogue corrects two published-contract gaps:
Bearer auth on history/calendar APIs, and the required OAuth `response_type=code`.

When running an installed package outside the repository, set
`VOLATILITY_ENV_FILE` to the absolute path of your private `.env`. Repository
commands default to the repository `.env`. The built wheel includes the endpoint
schemas and documentation inventory; it contains no credentials or recordings.
# Reliability verification

Read-only live reconnect/token-renewal check (renews the configured market token,
updates same-app aliases, opens one NIFTY subscription, then disconnects):

```powershell
.venv/Scripts/python.exe -m volatility_scanner.apps.verify_upstox_reliability --output D:/market-data/volatility/reliability
.venv/Scripts/python.exe -m unittest tests.brokers.upstox.test_reliability -v
.venv/Scripts/python.exe benchmark_capture.py --frames 1000000
```

The recorder checks token expiry every 30 seconds and reconnects when its token
changes. Renewal failures retry after 30 seconds. Storage stress verifies exact
raw/decoded counts, drained queues, SQLite integrity, and database/WAL size.
Rollover tests simulate changing contract sets while retaining prior events.
These bounded checks do not establish overnight stability, real expiry-day
behavior, or completeness during a broker/network outage. Market streams cannot
replay ticks missed while disconnected. Excel export and retention over multiple
days still require a longer market-hours soak.
# Streaming PCR/time algorithm

Incremental one-second PCR/time and modeled Gamma/Vanna/Charm analytics are available from the
WebSocket cache with `--analytics pcr-time,exposure`. See the
[design, formulas, data quality rules, and retrieval API](docs/pcr-time-algorithm.md).

For the dedicated direct-Protobuf algorithm streamer:

```powershell
python stream_option_chain.py --expiry-scope front_two
```

Streams selected option OI/IV and NIFTY spot into bounded in-memory state;
maintains sliding results every second and persists a ready batch every 60 seconds.
Use `record_upstox.py` when every received raw/tick
update must also be recorded.

The document's remaining modeled analytics include per-contract Jacobian/Hessian
premium decomposition, Color/Ultima, Charm/IV scenarios, Gamma curves/transitions,
and localized OTM/derivative-spike diagnostics. Contract results are retrievable
with `source='premium_minute'`. See the design's coverage and limitations table;
these analytics do not submit orders.

Separate live dashboard (no reuse of the existing Streamlit UI):

```powershell
python stream_option_chain.py --dashboard-port 8765
```

Open **http://127.0.0.1:8765** for live metric charts paired with synchronized
NIFTY candlesticks, expiry/strike filters, model tensors and data-quality status.
See [dashboard usage, architecture and verification](docs/algorithm-dashboard.md).
