# Streaming PCR/time and exposure algorithm

Design source: [PCR-time.txt](PCR-time.txt). This implementation emits analytics,
not orders. The text's claims of predicting dealer activity or liquidity crises
are hypotheses; they are not established by these calculations or tests.

## Running

For a dedicated algorithm feed with minimal payload and no per-tick disk writes:

```powershell
python stream_option_chain.py --expiry-scope front_two
```

This launches the existing lifecycle with `option_greeks` for the options and
`full` for NIFTY spot. It streams all selected strikes, not just ATM, and computes
the configured analytics every second and publishes ready results every 60 seconds. Output defaults to
`D:/market-data/volatility/algorithm-stream`; `--output`, `--stop`, expiry scope,
IV units and OI multiplier flags work as on the recorder.

The decoder reads Protobuf fields directly without whole-message JSON/dict
conversion, Greek/depth flattening or any REST polling for OI/IV. A 64-frame
decoder queue applies backpressure when full. Frames over 8 MiB produce a surfaced
failure; these limits bound queued payload memory to at most 512 MiB plus frames
being handled and normal runtime/cache overhead. Normal payloads are much smaller.
There is one shared socket and decoder, not one connection/thread per strike.

The single-producer handoff uses a dictionary keyed by subscribed instrument,
so pending state is bounded by the subscription size. Repeated pending updates
coalesce to the latest state, scheduling one event-loop callback per batch.
Only spot, OI, IV and option LTP are copied into the algorithm cache; unchanged
OI is not made fresh by LTPC-only messages. Ages use original socket receipt
timestamps, not delayed dispatch time. Reconnect clears freshness and pending
state; frames received before the new connection cutoff are ignored. Contract
refresh prunes removed instruments. Cache failures surface to the supervisor.

The dedicated feed persists minute results and contract metadata, **not every
intermediate tick**. Health exposes coalesced update count, pending instrument
count, handoffs and latest receive-to-cache delay. For full raw/tick retention,
use the original recorder command below; it keeps the normal detailed decoder.
Both modes reuse token renewal, resubscription, discovery, disk reserve checks,
graceful drain, batched SQLite writes and Excel export. REST is still needed for
initial contract discovery and periodic expiry/subscription refresh, not for
streamed algorithm inputs. No mode can replay broker ticks lost during outages.

Synthetic decoder/cache benchmark:

```powershell
python -m volatility_scanner.apps.benchmark_option_chain --frames 100000
```

This checks final state and frame counts without network/disk or algorithm
computation; its throughput is not broker latency or an end-to-end capture rate.

Bounded read-only broker smoke verification (disconnects after the initial
snapshots, stores a count/quality report under `D:/market-data/volatility/verification`):

```powershell
python -m volatility_scanner.apps.verify_option_chain_feed
```

Full recording plus minute algorithms:

```powershell
python record_upstox.py --expiry-scope front_two --feed-mode full --analytics pcr-time,exposure --exposure-iv-unit percent --exposure-oi-multiplier 1
```

The existing V3 WebSocket updates the per-instrument dictionary on every decoded
tick. A one-second consumer processes that dictionary incrementally on the
capture event loop without network calls or awaits. Quotes arriving during the
calculation remain queued and are processed afterward. This gives each
calculation a consistent cache view without copying the full history or locking
the socket thread. Exact cross-instrument exchange-time synchronization is not
available from these independently arriving quotes.

Producers mark OI/IV changes in a bounded dirty-key set. The consumer subtracts
old contributions and adds new ones to cached strike/expiry OI totals; unchanged
values reuse state. A bounded lazy deadline heap expires stale observations even
without new ticks. Receipt-only refreshes extend deadlines without recomputing
OI totals. Reconnect generations reset dependent state. The current completed
batch is exposed in process as `recorder.algorithm_consumer.latest_output` and
its epoch as `latest_epoch`; minute publication queues that ready batch through
the existing writer. There is no minute-boundary historical batch calculation.
Only minute publications reach SQLite/Excel, so output storage does not increase
60-fold. Health includes calculation duration, maximum duration, and missed
one-second sample slots. Delays do not fabricate samples or backfill missed
minutes, and gaps reset derivative/alert warmup.

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
6. Keep five minutes of one-second history in bounded deques (normally 301
   samples per series), with monotonic min/max queues for amortized O(1) range
   updates. Derivatives read only the last four observations. A gap over 2.5s or an invalid
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
discovery contract dictionary changes. OI totals update in O(changed instruments),
with O(log contracts) deadline maintenance and amortized heap compaction. PCR
time scores still update for every strike because time changes each second;
strike sorting costs O(strikes*log strikes). Temporal history is bounded at
O(strikes*window_seconds), not the full session. Greek base evaluation
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

## Reproducible one-second speed measurement

```powershell
python -m volatility_scanner.apps.benchmark_incremental --seconds 600
python -m volatility_scanner.apps.benchmark_incremental --seconds 600 --pcr-only
```

The benchmark uses 560 options across two expiries, six OI changes per second,
receipt refreshes for all instruments, and a full five-minute sliding window.
It reports mean/P50/P95/maximum one-second calculation time, input update cost,
ready-batch queue handoff cost and bounded state sizes. Reported publication is
an in-memory queue handoff, not SQLite/Excel commit or end-to-end latency.
Nonlinear Greeks and the gamma-flip landscape revalue each second: unchanged OI
does not imply unchanged exposure when spot or time changes. No stale Greek
result is substituted solely to improve benchmark numbers.
