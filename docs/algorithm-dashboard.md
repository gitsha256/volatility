# NIFTY / FLOW — independent algorithm dashboard

This is a new dashboard under `volatility_scanner/dashboard`, separate from the
repository's existing Streamlit dashboard. It has no order controls and binds
only to `127.0.0.1`. Broker credentials stay in Python and never enter browser
responses, JavaScript, URLs or chart assets.

## Run live

```powershell
.venv/Scripts/python.exe stream_option_chain.py --dashboard-port 8765
```

Open **http://127.0.0.1:8765**. The process streams the selected current/next
expiry contracts and NIFTY, updates the incremental algorithm every second,
pushes a selected-scope snapshot over one WebSocket, and records the ready
algorithm batch every 60 seconds. Use `--output` to select another journal.
Stop with `python stream_option_chain.py --stop` for the same output directory.

For a read-only view of another recording (no broker connection):

```powershell
python algorithm_dashboard.py --data D:/market-data/volatility/algorithm-stream --port 8765
```

For an explicitly labeled synthetic preview, isolated from credentials and
journals:

```powershell
python algorithm_dashboard.py --demo --port 8766
```

The demo generates input through the real calculation engine; its market values
are simulated. It never writes them into a live capture database.

## Display

- NIFTY OHLC, PCR, PCR/time², PCR/time³, Gamma, Vanna and nearest Gamma flip are
  prominent. Expiry, aggregate/strike scope, candle interval and history are
  selectable. The initial strike is chosen nearest to the displayed index level.
- Every available scalar/bool calculated field is discoverable in the category
  tabs and metric search, including actual PCR derivatives, rolling signals,
  time bands' numeric inputs, exposure/scenarios, per-leg Greeks, Jacobian/
  Hessian entries, premium decomposition and residuals. Categorical model/quality
  states appear as labels rather than fabricated numeric series.
- Each metric card has NIFTY candlesticks above its metric pane on one shared
  time axis. Metric and price scales remain separate. Time-range zoom/pan and
  vertical crosshairs synchronize across mounted charts. Candles can be
  aggregated client-side to 1, 5 or 15 minutes without more broker requests.
- Historical calculation points come from minute publications. The latest point
  updates every second inside the current bar. The selected history is a count
  of underlying one-minute bars, not uninterrupted wall-clock hours across
  closed sessions. Missing/invalid calculations produce gaps, not zeros or
  fabricated forward-filled values.
- The Gamma scenario sweep has a synchronized NIFTY candle context and an
  actual-spot marker. Its horizontal axis is hypothetical price, not time.
  It is not presented as a predicted future price path.
- Live transport, market closed, partial IV, stale/unavailable input, recorded
  history, simulated preview and paused display are distinguished. NIFTY status
  uses NSE_INDEX/NSE_EQ; an open commodity market does not make NIFTY live.

## Fast path and bounds

The frontend is native JavaScript/CSS with locally bundled Lightweight Charts
5.2.1 (Apache 2.0, included license and TradingView attribution). There is no
React bundle, Streamlit rerun or runtime chart CDN. Off-screen chart instances
are destroyed and recreated from bounded cached points as needed. Visible
charts update only their latest point/candle; changing scope loads history once.
Advanced duplicate tensor entries are available under All calculations.

The live server indexes the already-computed output batch once per calculation
epoch, groups identical browser subscriptions, and sends only selected expiry/
strike records plus recent NIFTY bars. It does not rescan the journal each
second in integrated live mode. Read-only mode uses bounded indexed tail
queries because another process owns the writer. History is limited to 1,440
one-minute points per source/leg; at most 16 WebSocket clients are accepted.
Slow sends have a timeout. Browser rendering is canvas-based and lazy.

## NIFTY candles

Every received index update enters a bounded, thread-safe OHLC accumulator
**before** the direct algorithm feed coalesces pending states. This preserves
received highs/lows even when only the final index value reaches the algorithm
cache. Up to 1,440 minute bars are retained in memory. The existing batched
SQLite writer upserts them into `nifty_candles`; this adds at most one changed
bar per second, not another per-tick history stream.

When the integrated dashboard starts, one read-only Upstox intraday-candle
request seeds the NIFTY history. Subsequent prices arrive over the existing
market WebSocket. A seed failure is reported, with captured candles retained.
The first unseeded capture minute is labeled partial. Market-closed snapshots
do not create artificial after-hours candles. No outage bars are invented.
Candles based on received ticks may omit broker ticks lost during an outage;
they are not claimed to be an exchange-complete reconstruction.

Reference: [Upstox Intraday Candle V3](https://upstox.com/developer/api-documentation/v3/get-intra-day-candle-data/).

## Verification

Backend tests cover original-tick high/low preservation through coalescing,
OHLC seed merging, session gating, bounded storage/restoration, static assets,
WebSocket history selection, credential exclusion and origin/host restrictions.
JavaScript syntax and DOM/chart-API logic checks cover metric discovery,
time-range synchronization, gaps, pause/resume and candle aggregation.

The development browser-control host was unavailable, so actual browser visual
rendering and responsive appearance were not visually verified. DOM checks do
not replace a real-browser visual review. A bounded live protocol check verified
Upstox connectivity, historical NIFTY candles and one-second dashboard packets.
