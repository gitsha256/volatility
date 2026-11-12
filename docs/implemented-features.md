# Implemented features and API inventory

Repository inventory: 11 November 2026. Describes checked-in code, including current working-tree changes; does not mean every API has passed a live test or runs automatically.

## 1. Applications and entry points

| Application | Entry point | Implemented purpose |
|---|---|---|
| Dedicated option-chain algorithm feed | `stream_option_chain.py` | Direct Protobuf input, incremental one-second calculations, minute publications; optional live dashboard |
| Detailed Upstox recorder | `record_upstox.py` | Selected option-chain expiries, NIFTY, optional futures/VIX/depth, raw frames, decoded ticks, snapshots and analytics |
| Algorithm dashboard | `algorithm_dashboard.py` | Recorded-data viewer or isolated synthetic demo; live mode attaches through recorder |
| Generic Upstox API CLI | `upstox_api.py` | List/describe/call catalogue operations; manage static IPs |
| Upstox authentication | `authenticate_upstox.py` | Configure/generate credentials through supported login helpers |
| Historical capture | `capture_upstox_history.py` | Download broker candle history |
| Replay | `replay_upstox.py` | Replay recorded Upstox data |
| Original scanner | `main.py` | NIFTY futures/options pricing, volatility and strategy scoring |
| Original Streamlit dashboard | `streamlit_app.py` | Existing scanner UI, separate from algorithm dashboard |
| FYERS recorder/login | `record_market.py`, `authenticate.py` | FYERS market capture and authentication |

Primary implementation lives in `volatility_scanner/apps`; root scripts are launchers. See [README](../README.md), [algorithm design](pcr-time-algorithm.md) and [dashboard reference](algorithm-dashboard.md).

## 2. Upstox REST client

Implementation: [`api/client.py`](../volatility_scanner/brokers/upstox/api/client.py), [`api/catalog.py`](../volatility_scanner/brokers/upstox/api/catalog.py).

- Synchronous `APIClient` and asynchronous `AsyncAPIClient`.
- Generic `call(operation, path=..., query=..., headers=..., body=...)`; grouped operation methods and bounded concurrent `call_many`.
- Bundled OpenAPI catalogue, request-schema validation and operation inspection.
- Separate `market`, `algo` and `sandbox` credential profiles; sandbox default for generic client/CLI.
- Persistent HTTP sessions, shared sliding-window request budgets and credential-aware routing.
- Explicit opt-in for live mutations; structured errors and unknown-outcome reporting. Order submissions are not automatically retried.
- Trading, GTT, portfolio, quotes, option chains, history, analytics, account, instruments, charges, reports, news, fundamentals, IPO, mutual funds, payments and authentication exposed as callable operations.
- Callable coverage is broader than recorder integration: payments, IPO, fundamentals and other catalogue groups are not automatically executed by capture.

```python
from volatility_scanner.brokers.upstox.api.client import APIClient

with APIClient('market') as client:
    quote = client.call('getLtp', query={'instrument_key': 'NSE_INDEX|Nifty 50'})
```

Full operation list appears below, generated from the repository catalogue. [Existing API reference](upstox-api.md) contains documentation links and request usage.

## 3. Authentication and account configuration

Implementation: [`upstox/auth.py`](../volatility_scanner/brokers/upstox/auth.py), [`credentials.py`](../volatility_scanner/brokers/upstox/api/credentials.py), [`ip_whitelist.py`](../volatility_scanner/brokers/upstox/account/ip_whitelist.py).

- OAuth authorization/token helpers; TOTP/PIN token generation and near-expiry renewal.
- Serialized renewal and atomic `.env` token replacement; shared-key market/algo token aliases handled together.
- Recorder detects token changes and reconnects its market stream.
- Static-IP read/update through `getUserIps` and `updateUserIp`.
- IP validation, preservation of omitted secondary IP, unchanged-update skip and token renewal after successful IP update.
- Secrets stay outside dashboard/browser payloads; CLI redacts sensitive response fields.

## 4. WebSockets and live feeds

| Feed | Implemented support | Code |
|---|---|---|
| Upstox market V3 | `ltpc`, `option_greeks`, `full`, `full_d30`; subscribe/unsubscribe/mode changes through transport/SDK; reconnect/resubscription | [`streams/market.py`](../volatility_scanner/brokers/upstox/streams/market.py), [`streams/factories.py`](../volatility_scanner/brokers/upstox/streams/factories.py) |
| Dedicated algorithm handoff | Direct Protobuf extraction of spot, OI, IV, option LTP/trade timestamp; bounded pending state keyed by instrument | [`streams/option_chain.py`](../volatility_scanner/brokers/upstox/streams/option_chain.py) |
| Upstox portfolio | Order, GTT-order, position and holding update subscriptions; background socket/decoder and reconnect | [`streams/portfolio.py`](../volatility_scanner/brokers/upstox/streams/portfolio.py) |
| Dashboard | Local browser WebSocket pushes selected calculation scope and NIFTY candles | [`dashboard/server.py`](../volatility_scanner/dashboard/server.py) |
| FYERS market | Futures and option quote streaming, cache and feed status | [`market/live.py`](../volatility_scanner/market/live.py) |

- TLS verification, socket tuning, decoder queue and surfaced decoder failures.
- Shared socket/decoder with per-instrument cache; no dedicated thread or socket per strike.
- Algorithm mode coalesces pending updates to latest instrument state. It does **not** persist every intermediate tick.
- Detailed recorder mode supports raw-frame and decoded-tick retention. Neither mode recovers broker ticks missed during an outage.
- Depth and subscription capacities remain subject to broker entitlements; V2 market-feed catalogue operations are disabled locally.

## 5. Capture scope and storage

Implementation: [`record_upstox.py`](../volatility_scanner/apps/record_upstox.py), [`market/coverage.py`](../volatility_scanner/brokers/upstox/market/coverage.py), [`storage`](../volatility_scanner/storage).

- Current expiry, front two expiries or all available expiries; selected contracts discovered/refreshed through REST.
- All strikes in selected expiry scope, subject to validated feed capacity.
- NIFTY spot; optional India VIX and active NIFTY futures context.
- Optional 30-level depth subset, up to 50 contracts as configured.
- Configurable raw frames, ticks, second snapshots and analytics; dedicated algorithm mode retains metadata/minute outputs instead of tick history.
- SQLite journal, WAL, durable writer, queued/batched commits and indexed algorithm retrieval.
- Stored NIFTY candles built from actual ticks; historical candle seeding for dashboard.
- Separate Excel workbooks by strike/bucket and recording partition; background process export, atomic replacement, export progress and locked-file retry handling.
- Disk reserve checks, recorder lock, graceful stop request, drain/export on shutdown and health/error reporting.

## 6. Incremental PCR/time algorithm

Implementation: [`analytics/pcr_time.py`](../volatility_scanner/analytics/pcr_time.py), [`market/pcr_time.py`](../volatility_scanner/brokers/upstox/market/pcr_time.py).

- One-second consumer; ready results published every 60 seconds.
- Dirty-key updates, cached OI totals, stale-input deadline heap and bounded five-minute histories.
- Per-strike and per-expiry call OI, put OI and PCR; expiry PCR uses ratio of sums.
- ACT/365 time to expiry at 15:30 IST; explicit final-minute floor.
- `PCR/t`, `PCR/t²`, `PCR/t³`.
- First, second and third temporal PCR derivatives using actual sample spacing; cubic-score derivative and spatial strike slope.
- Five-minute PCR/score growth, clock-only growth and spot-range measurements.
- Raw/positioning stress alerts, derivative stress alerts and localized OTM-put diagnostics.
- Per-leg OI growth, source-document time bands and max-pain calculations.
- Missing/stale/disconnected inputs invalidate outputs; gaps and membership changes reset affected warmup.
- Last-valid ratio snapshots kept separately for display, timestamped and labeled; cached values do not become fresh signals.

## 7. Exposure, Greeks and premium analysis

Implementation: [`analytics/exposure.py`](../volatility_scanner/analytics/exposure.py), [`analytics/premium.py`](../volatility_scanner/analytics/premium.py).

- Black–Scholes spot-model pricing with explicit IV units, rates, dividend yield and OI multiplier.
- Delta, Vega, calendar Theta, Gamma, Vanna, calendar Charm, Speed, Volga, Color, Ultima, Vega decay and second calendar-time sensitivity.
- OI-weighted Gamma/Vanna/Charm exposure with explicitly assumed call-positive/put-negative signs.
- Gamma scenario sweeps, flip/root search, walls and model scenarios including IV shocks and time drift.
- Per-leg premium model, Jacobian/Hessian, incremental Taylor decomposition, observed premium changes and residuals.
- Freshness checks include option trade timestamp for observed premium comparisons.
- Aggregate exposure withheld for incomplete IV/OI books; available contract-level models can still be produced.
- Analytics are descriptive/model outputs. No automatic trading strategy executes orders from these signals.

## 8. Dashboards and indicators

Implementation: [`dashboard`](../volatility_scanner/dashboard); original UI: [`apps/streamlit_app.py`](../volatility_scanner/apps/streamlit_app.py).

- Independent algorithm dashboard with expiry/strike selection, aggregate scope, metric search, categories and live health/quality labels.
- Calculated metrics plotted as lines against synchronized NIFTY candlestick context; separate scales, zoom/pan and crosshairs.
- 1/5/15-minute candle aggregation; historical minute publications plus one-second forming updates.
- 25 DEMA green, 55 DEMA yellow, 105 DEMA red; Supertrend ATR(10), multiplier 3.
- Incremental study updates handle forming bars without double counting.
- Metric OHLC built from observed one-second values; Supertrend waits for sufficient usable OHLC history.
- Gaps, partial data, snapshots, paused display and synthetic demo explicitly distinguished.
- Loopback binding, Host/Origin restrictions, local chart assets and content-security policy.

### Local dashboard API

| Method/transport | Route | Purpose |
|---|---|---|
| HTTP GET | `/` | Dashboard HTML |
| HTTP GET | `/health` | Recorder/dashboard health |
| WebSocket | `/ws` | Selection requests and initial/live data packets |
| HTTP static | `/static/…` | JavaScript, CSS and bundled chart assets |

WebSocket selection uses `expiry`, `strike` and `points`. Responses include catalogue, selection, metadata, candles, calculated history/current rows and last-valid ratio snapshots. This server exposes no order-placement controls.

## 9. Original scanner and other data providers

Implementation: [`market/data.py`](../volatility_scanner/market/data.py), [`strategies/scanner.py`](../volatility_scanner/strategies/scanner.py), [`analytics/pricing.py`](../volatility_scanner/analytics/pricing.py), [`analytics/volatility.py`](../volatility_scanner/analytics/volatility.py).

- Black-76 futures-option pricing, implied-volatility inversion and option Greeks.
- EWMA, Parkinson and realized-variance volatility forecasts; remaining-session move/horizon alignment.
- Long-straddle, long-call and long-put opportunity scoring; SQLite opportunity persistence.
- Synthetic snapshots, NSE option-chain HTTP provider and FYERS provider.
- FYERS REST option chain and futures quotes; browser authentication, refresh-token path and opt-in TOTP helper.
- FYERS internal TOTP endpoints and refresh-token availability depend on provider behavior; implementation does not guarantee those services remain available.

| Provider API | Usage in repository |
|---|---|
| FYERS `https://api-t1.fyers.in/data/options-chain-v3` | Option-chain snapshot |
| FYERS `https://api-t1.fyers.in/data/quotes` | Futures quote |
| FYERS v3 authentication | Browser/token exchange and renewal helpers |
| FYERS internal login endpoints | Opt-in TOTP login helper |
| NSE `/api/option-chain-indices?symbol=NIFTY` | Alternative snapshot provider |

## 10. Verification and performance tools

Implemented under [`apps`](../volatility_scanner/apps), tests under [`tests`](../tests).

- Unit tests cover models, incremental calculations, data freshness, streams, auth, IP management, storage/export, failures and dashboard payloads.
- Node tests cover DEMA, Supertrend and live metric OHLC behavior.
- Synthetic capture, API, Protobuf handoff and incremental-algorithm benchmarks.
- Read-only live option-chain feed verifier.
- Live reliability verifier covering forced reconnect and token renewal; automated reliability/storage tests cover rollover simulation and storage checks.
- Sandbox API/order verification and portfolio-connection verification tools.
- Explicit live rejection and one-share live order test tools; real mutations require deliberate invocation.
- Latest one-share test (11 November 2026): portfolio stream delivered received/rejected events; order rejected for insufficient margin, zero filled. Filled-position/holding-change events remain unverified by that test.
- Automated tests and simulated failure checks do not establish uninterrupted long-duration live reliability. Broker delivery, network latency and account entitlements remain external dependencies.

## 11. Complete Upstox operation catalogue

Generated from `catalogue().describe()` in this working tree, without broker requests. `Sandbox` means the repository routes that operation to sandbox, not that every case has passed a sandbox/live test. `Disabled` marks discontinued V2 feeds. Other entries are exposed through the generic client; provider/account acceptance is separate.

Catalogue totals: **105 operations** across **17 groups**.

### account (7)

| Callable operation | Method | Route | Sandbox | Status |
|---|---|---|---|---|
| `getKillSwitch` | GET | `/v2/user/kill-switch` | no | Callable |
| `getProfile` | GET | `/v2/user/profile` | no | Callable |
| `getUserFundMargin` | GET | `/v2/user/get-funds-and-margin` | no | Callable |
| `getUserFundMarginV3` | GET | `/v3/user/get-funds-and-margin` | no | Callable |
| `getUserIps` | GET | `/v2/user/ip` | no | Callable |
| `updateKillSwitch` | POST | `/v2/user/kill-switch` | no | Callable |
| `updateUserIp` | PUT | `/v2/user/ip` | no | Callable |

### analytics (13)

| Callable operation | Method | Route | Sandbox | Status |
|---|---|---|---|---|
| `getChangeOiData` | GET | `/v2/market/change-oi` | no | Callable |
| `getDiiData` | GET | `/v2/market/dii` | no | Callable |
| `getExchangeTimings` | GET | `/v2/market/timings/{date}` | no | Callable |
| `getFiiData` | GET | `/v2/market/fii` | no | Callable |
| `getHoliday` | GET | `/v2/market/holidays/{date}` | no | Callable |
| `getHolidays` | GET | `/v2/market/holidays` | no | Callable |
| `getMarketStatus` | GET | `/v2/market/status/{exchange}` | no | Callable |
| `getMaxPainData` | GET | `/v2/market/max-pain` | no | Callable |
| `getOiData` | GET | `/v2/market/oi` | no | Callable |
| `getPcrData` | GET | `/v2/market/pcr` | no | Callable |
| `getSmartlistFutures` | GET | `/v2/market/smartlist/futures` | no | Callable |
| `getSmartlistMtf` | GET | `/v2/market/smartlist/mtf` | no | Callable |
| `getSmartlistOptions` | GET | `/v2/market/smartlist/options` | no | Callable |

### authentication (5)

| Callable operation | Method | Route | Sandbox | Status |
|---|---|---|---|---|
| `authorize` | GET | `/v2/login/authorization/dialog` | no | Callable |
| `generateTokenViaTotpAndPin` | POST | `/v2/totp-login/authorization/token` | no | Callable |
| `initTokenRequestForIndieUser` | POST | `/v3/login/auth/token/request/{client_id}` | no | Callable |
| `logout` | DELETE | `/v2/logout` | no | Callable |
| `token` | POST | `/v2/login/authorization/token` | no | Callable |

### charges (2)

| Callable operation | Method | Route | Sandbox | Status |
|---|---|---|---|---|
| `getBrokerage` | GET | `/v2/charges/brokerage` | no | Callable |
| `postMargin` | POST | `/v2/charges/margin` | no | Callable |

### events (1)

| Callable operation | Method | Route | Sandbox | Status |
|---|---|---|---|---|
| `publishEvent` | POST | `/v2/events` | no | Contract only; no public guide |

### fundamentals (8)

| Callable operation | Method | Route | Sandbox | Status |
|---|---|---|---|---|
| `getBalanceSheet` | GET | `/v2/fundamentals/{isin}/balance-sheet` | no | Callable |
| `getCashFlow` | GET | `/v2/fundamentals/{isin}/cash-flow` | no | Callable |
| `getCompanyProfile` | GET | `/v2/fundamentals/{isin}/profile` | no | Callable |
| `getCompetitors` | GET | `/v2/fundamentals/{instrument_key}/competitors` | no | Callable |
| `getCorporateActions` | GET | `/v2/fundamentals/{isin}/corporate-actions` | no | Callable |
| `getIncomeStatement` | GET | `/v2/fundamentals/{isin}/income-statement` | no | Callable |
| `getKeyRatios` | GET | `/v2/fundamentals/{isin}/key-ratios` | no | Callable |
| `getShareHoldings` | GET | `/v2/fundamentals/{isin}/share-holdings` | no | Callable |

### history (10)

| Callable operation | Method | Route | Sandbox | Status |
|---|---|---|---|---|
| `getExpiredFutureContracts` | GET | `/v2/expired-instruments/future/contract` | no | Callable |
| `getExpiredHistoricalCandleData` | GET | `/v2/expired-instruments/historical-candle/{expired_instrument_key}/{interval}/{to_date}/{from_date}` | no | Callable |
| `getExpiredOptionContracts` | GET | `/v2/expired-instruments/option/contract` | no | Callable |
| `getExpiriesResponse` | GET | `/v2/expired-instruments/expiries` | no | Callable |
| `getHistoricalCandleData` | GET | `/v3/historical-candle/{instrumentKey}/{unit}/{interval}/{to_date}` | no | Callable |
| `getHistoricalCandleData_1` | GET | `/v3/historical-candle/{instrumentKey}/{unit}/{interval}/{to_date}/{from_date}` | no | Callable |
| `getHistoricalCandleData_2` | GET | `/v2/historical-candle/{instrumentKey}/{interval}/{to_date}` | no | Callable |
| `getHistoricalCandleData_3` | GET | `/v2/historical-candle/{instrumentKey}/{interval}/{to_date}/{from_date}` | no | Callable |
| `getIntraDayCandleData` | GET | `/v3/historical-candle/intraday/{instrumentKey}/{unit}/{interval}` | no | Callable |
| `getIntraDayCandleData_1` | GET | `/v2/historical-candle/intraday/{instrumentKey}/{interval}` | no | Callable |

### instruments (1)

| Callable operation | Method | Route | Sandbox | Status |
|---|---|---|---|---|
| `searchInstrument` | GET | `/v2/instruments/search` | no | Callable |

### ipo (6)

| Callable operation | Method | Route | Sandbox | Status |
|---|---|---|---|---|
| `applyForIpo` | POST | `/v2/ipos/orders` | no | Callable |
| `cancelIpoOrder` | DELETE | `/v2/ipos/orders/{order_id}` | no | Callable |
| `getIpoDetails` | GET | `/v2/ipos/{id}` | no | Callable |
| `getIpoListing` | GET | `/v2/ipos` | no | Callable |
| `getIpoOrderById` | GET | `/v2/ipos/orders/{order_id}` | no | Callable |
| `getIpoOrders` | GET | `/v2/ipos/orders` | no | Callable |

### market (9)

| Callable operation | Method | Route | Sandbox | Status |
|---|---|---|---|---|
| `getFullMarketQuote` | GET | `/v2/market-quote/quotes` | no | Callable |
| `getFullMarketQuoteV3` | GET | `/v3/market-quote/quotes` | no | Callable |
| `getLtp` | GET | `/v3/market-quote/ltp` | no | Callable |
| `getMarketQuoteOHLC` | GET | `/v2/market-quote/ohlc` | no | Callable |
| `getMarketQuoteOHLCV3` | GET | `/v3/market-quote/ohlc` | no | Callable |
| `getMarketQuoteOptionGreek` | GET | `/v3/market-quote/option-greek` | no | Callable |
| `getOptionContracts` | GET | `/v2/option/contract` | no | Callable |
| `getPutCallOptionChain` | GET | `/v2/option/chain` | no | Callable |
| `ltp` | GET | `/v2/market-quote/ltp` | no | Callable |

### mutual_funds (4)

| Callable operation | Method | Route | Sandbox | Status |
|---|---|---|---|---|
| `getMutualFundHoldings` | GET | `/v2/mf/holdings` | no | Callable |
| `getMutualFundOrder` | GET | `/v2/mf/orders/{order_id}` | no | Callable |
| `getMutualFundOrders` | GET | `/v2/mf/orders` | no | Callable |
| `getMutualFundSips` | GET | `/v2/mf/sips` | no | Callable |

### news (1)

| Callable operation | Method | Route | Sandbox | Status |
|---|---|---|---|---|
| `getNews` | GET | `/v2/news` | no | Callable |

### payments (6)

| Callable operation | Method | Route | Sandbox | Status |
|---|---|---|---|---|
| `cancelPayout` | DELETE | `/v2/user/payments/payout/{transaction_id}` | no | Callable |
| `getPayinHistory` | GET | `/v2/user/payments/payin` | no | Callable |
| `getPayoutHistory` | GET | `/v2/user/payments/payout` | no | Callable |
| `getPayoutModes` | GET | `/v2/user/payments/payout/modes` | no | Callable |
| `initiatePayout` | POST | `/v2/user/payments/payout` | no | Callable |
| `modifyPayout` | PUT | `/v2/user/payments/payout/{transaction_id}` | no | Callable |

### portfolio (4)

| Callable operation | Method | Route | Sandbox | Status |
|---|---|---|---|---|
| `convertPositions` | PUT | `/v2/portfolio/convert-position` | no | Callable |
| `getHoldings` | GET | `/v2/portfolio/long-term-holdings` | no | Callable |
| `getMtfPositions` | GET | `/v3/portfolio/mtf-positions` | no | Callable |
| `getPositions` | GET | `/v2/portfolio/short-term-positions` | no | Callable |

### reports (4)

| Callable operation | Method | Route | Sandbox | Status |
|---|---|---|---|---|
| `getProfitAndLossCharges` | GET | `/v2/trade/profit-loss/charges` | no | Callable |
| `getTradeWiseProfitAndLossData` | GET | `/v2/trade/profit-loss/data` | no | Callable |
| `getTradeWiseProfitAndLossMetaData` | GET | `/v2/trade/profit-loss/metadata` | no | Callable |
| `getTradesByDateRange` | GET | `/v2/charges/historical-trades` | no | Callable |

### streams (6)

| Callable operation | Method | Route | Sandbox | Status |
|---|---|---|---|---|
| `getMarketDataFeed` | GET | `/v2/feed/market-data-feed` | no | Disabled |
| `getMarketDataFeedAuthorize` | GET | `/v2/feed/market-data-feed/authorize` | no | Disabled |
| `getMarketDataFeedAuthorizeV3` | GET | `/v3/feed/market-data-feed/authorize` | no | Callable |
| `getMarketDataFeedV3` | GET | `/v3/feed/market-data-feed` | no | Callable |
| `getPortfolioStreamFeed` | GET | `/v2/feed/portfolio-stream-feed` | no | Callable |
| `getPortfolioStreamFeedAuthorize` | GET | `/v2/feed/portfolio-stream-feed/authorize` | no | Callable |

### trading (18)

| Callable operation | Method | Route | Sandbox | Status |
|---|---|---|---|---|
| `cancelGTTOrder` | DELETE | `/v3/order/gtt/cancel` | no | Callable |
| `cancelMultiOrder` | DELETE | `/v2/order/multi/cancel` | no | Callable |
| `cancelOrder` | DELETE | `/v3/order/cancel` | yes | Callable |
| `cancelOrder_1` | DELETE | `/v2/order/cancel` | yes | Callable |
| `exitPositions` | POST | `/v2/order/positions/exit` | no | Callable |
| `getGttOrderDetails` | GET | `/v3/order/gtt` | no | Callable |
| `getOrderBook` | GET | `/v2/order/retrieve-all` | no | Callable |
| `getOrderDetails` | GET | `/v2/order/history` | no | Callable |
| `getOrderStatus` | GET | `/v2/order/details` | no | Callable |
| `getTradeHistory` | GET | `/v2/order/trades/get-trades-for-day` | no | Callable |
| `getTradesByOrder` | GET | `/v2/order/trades` | no | Callable |
| `modifyGTTOrder` | PUT | `/v3/order/gtt/modify` | no | Callable |
| `modifyOrder` | PUT | `/v3/order/modify` | yes | Callable |
| `modifyOrder_1` | PUT | `/v2/order/modify` | yes | Callable |
| `placeGTTOrder` | POST | `/v3/order/gtt/place` | no | Callable |
| `placeMultiOrder` | POST | `/v2/order/multi/place` | yes | Callable |
| `placeOrder` | POST | `/v3/order/place` | yes | Callable |
| `placeOrder_1` | POST | `/v2/order/place` | yes | Callable |

