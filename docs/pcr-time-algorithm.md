# Streaming PCR/time and exposure algorithm

Design source: [PCR-time.txt](PCR-time.txt). This implementation emits analytics,
not orders. The text's claims of predicting dealer activity or liquidity crises
are hypotheses; they are not established by these calculations or tests.

## Running

```powershell
python record_upstox.py --expiry-scope front_two --feed-mode full --analytics pcr-time,exposure --exposure-iv-unit percent --exposure-oi-multiplier 1
```

The existing V3 WebSocket updates the per-instrument dictionary on every decoded
tick. At each minute boundary, a snapshot of that dictionary is processed on the
capture event loop without network calls or awaits. Quotes arriving during the
calculation remain queued and are processed afterward. This gives each
calculation a consistent cache view without copying the full history or locking
the socket thread. Exact cross-instrument exchange-time synchronization is not
available from these independently arriving quotes.

Opt in with `pcr-time`, `exposure`, or both. `ltpc` mode is rejected because it
does not supply OI/IV. The existing `local` analytics option remains independent.
No process starts automatically merely by adding this implementation.

## PCR/time

For each expiry and each matched CE/PE strike pair:

1. Require valid nonnegative OI and a positive, fresh NIFTY quote. Reject booleans,
   nonfinite inputs, missing legs, disconnected feeds and receipt ages over 15s.
   These are receipt ages, not proof of the broker's OI observation time.
2. `PCR = sum(put OI) / sum(call OI)`. A zero denominator produces no score;
   zero put OI produces zero. Aggregate PCR is the ratio of sums, not average
   strike PCR. Aggregates cover the selected contracts, not unobserved strikes.
3. Time is ACT/365 calendar seconds to 15:30 IST on the contract expiry date:
   `t = max(seconds_remaining, 60) / (365*86400)`.
4. Emit `pcr_over_t`, `pcr_over_t2`, `pcr_over_t3`. These are weighted scores,
   **not** velocity, acceleration or jerk. Expired contracts produce null scores;
   the final-minute time floor is explicit and disables stress alerts.
5. Separately estimate actual first/second/third PCR time derivatives using
   backward Lagrange finite differences over 2/3/4 observations and actual
   timestamp spacing. Units are PCR/minute, PCR/minute² and PCR/minute³.
   Also record the observed cubic-score change per minute.
6. Keep five minutes of history in bounded deques. A gap over 90s or an invalid
   sample resets that key's history. Changed contract membership resets that
   expiry's history; unchanged expiries retain it. Restart requires fresh warmup.
7. After five minutes, report score growth, PCR growth, clock-only cubic growth,
   and the entire window's spot range divided by initial spot.
8. `raw_stress_alert`: cubic score rises **more than 500%** (over six times its
   baseline) while spot range is at most 0.1%. `positioning_stress_alert` also
   requires PCR itself to rise more than 500%, removing alerts caused solely by
   time compression. A zero baseline cannot define percentage growth.

Uneven-strike neighbour secants give the spatial slope of the cubic score.
Missing/undefined neighbours are not bridged. These derivatives are descriptive
and can be noisy; neither their direction nor a stress alert is a trade signal.
Configuration thresholds live in `PCRTimeConfig`; defaults are experimental.
The document's inconsistent year/hour regime thresholds are intentionally not
used to classify market conditions.

## Greek exposure scenario

For each complete selected expiry, require finite positive IV for every leg.
Convert explicitly from configured IV units (`percent` default or `decimal`),
never by guessing from magnitude. Use the Black-Scholes spot model with zero
interest/dividend rates for the stream adapter. The callable model supports
explicit rates. This is separate from the existing Black-76 pricing engine.

Compute Gamma, Vanna, calendar Charm, Speed and Volga. Gamma/Vanna/Charm are
OI-weighted with the document's **assumed** call-positive / put-negative signs.
Long calls and long puts both have positive Gamma; subtracting puts is an
inventory scenario, not information obtainable from OI. IV surface dynamics,
actual dealer positions and hedge transactions are not observed.
Per-strike outputs also include signed Delta, Speed and Volga. Valid successive
expiry profiles include observed dollar-Gamma change per minute; gaps or changed
contract membership reset that comparison.

- Dollar Gamma per 1% spot move: `signed_OI * gamma * S² * 0.01`.
- Dollar Vanna per one IV percentage point: `signed_OI * vanna * S * 0.01`.
- Charm delta per calendar day: `signed_OI * calendar_charm_per_year / 365`.
- OI multiplier defaults to **1: use broker OI as supplied**. Set a contract lot
  multiplier only if the input OI is known to count contracts. Do not multiply
  quantities by lot size twice.
- Call/put walls are strikes with maximum unsigned dollar Gamma on each side.
- Gamma flips: freeze IV/OI/time, reprice aggregate signed Gamma over spot ±10%
  with a 21-point grid; refine sign-changing intervals with 24 bisections. Report
  all detected candidates and the nearest. No root in that range returns null;
  an identically zero sampled profile is marked indeterminate. This bounded
  search can miss tangencies or multiple crossings within a grid interval.

Partial/missing IV or stale quotes withhold the expiry exposure profile, while
valid PCR calculations may continue. No stale prior profile is republished.

## Storage and retrieval

No new database, service or dependency. The existing per-strike queues feed one
batched WAL SQLite writer with FULL synchronization. Minute strike results share
their existing expiry/strike bucket; aggregate buckets are `YYYY-MM-DD_PCR_TIME`
and `YYYY-MM-DD_EXPOSURE`. Sources are `pcr_time_minute` and `exposure_minute`.
Existing hourly Excel export picks up the new fields automatically.

Contract/strike lookup is dictionary-based. Groups rebuild only when the
discovery contract dictionary changes. PCR work is O(contracts + strikes*log
strikes); temporal history is O(strikes*window_minutes). Greek base evaluation
is O(contracts), with a bounded spot sweep/bisection cost. The partial SQLite
index `(bucket,source,id)` indexes only algorithm rows. Existing databases incur
a one-time scan to create this index at startup.

```python
from volatility_scanner.storage.algorithm import read_algorithm_events

page = read_algorithm_events(
    'D:/market-data/volatility/upstox/capture.sqlite3',
    bucket='2026-12-03_PCR_TIME', source='pcr_time_minute',
    after_id=0, limit=1000,
)
# Next page: after_id=page[-1]['id']; bounded read-only access, no OFFSET scans.
```

Tests cover mathematics, finite differences, five-minute windows, clock-only
amplification, zero/missing OI, time floors, expiry/disconnection, membership
rollover, exposure scaling, gamma roots, stream-cache integration, SQLite index
selection, cursor pagination and Excel output. Synthetic tests establish
implementation behavior, not economic predictive power or profitability.
