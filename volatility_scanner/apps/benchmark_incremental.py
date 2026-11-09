"""Synthetic one-second incremental consumer and ready-result publication benchmark."""
import argparse
import json
from pathlib import Path
from queue import SimpleQueue
import statistics
import time
from types import SimpleNamespace

from volatility_scanner.brokers.upstox.market.coverage import expiry_epoch
from volatility_scanner.brokers.upstox.market.pcr_time import StreamingAnalytics


def benchmark(seconds=600, exposure=True):
    rows = [dict(symbol=f'{expiry}-{strike}-{kind}', expiry_date=expiry, strike_price=strike, option_type=kind)
            for expiry in ('2026-12-03','2026-12-10') for strike in range(21500,28500,50) for kind in ('CE','PE')]
    state = SimpleNamespace(contracts={r['symbol']:r for r in rows},
        latest={r['symbol']:dict(oi=500+r['strike_price']%700+(200 if r['option_type']=='CE' else 0),
                                iv=18+r['strike_price']%300/100) for r in rows},
        symbols={r['symbol']:(f"{r['expiry_date']}_{r['strike_price']}",r['option_type']) for r in rows},
        connected=True, analytics_dirty=set(), analytics_generation=0, index={'ltp':25000}, received={})
    scanner = StreamingAnalytics(exposure=exposure)
    base = expiry_epoch('2026-12-03')-86400
    samples, publication, input_times = [], [], []
    output_queue = SimpleQueue()
    # All instruments refresh receipt ages; only six options change OI each second.
    keys = list(state.contracts)
    for second in range(seconds):
        started = time.perf_counter_ns()
        now = base+second
        state.index_received = now
        state.received = dict.fromkeys(keys, now)
        for offset in range(6):
            key = keys[(second*6+offset) % len(keys)]
            state.latest[key]['oi'] += 1
            state.analytics_dirty.add(key)
        input_times.append((time.perf_counter_ns()-started)/1e6)
        started = time.perf_counter_ns()
        scanner.consume(state, now)
        samples.append((time.perf_counter_ns()-started)/1e6)
        if second % 60 == 0:
            started = time.perf_counter_ns()
            for event in scanner.latest_output:
                output_queue.put(event)
            publication.append((time.perf_counter_ns()-started)/1e6)
            while not output_queue.empty():
                output_queue.get()
    def summarize(values):
        ordered = sorted(values)
        return dict(mean_ms=round(statistics.mean(values),4),p50_ms=round(statistics.median(values),4),
                    p95_ms=round(ordered[int(.95*(len(values)-1))],4),max_ms=round(max(values),4))
    return dict(instruments=len(rows),simulated_seconds=seconds,exposure=exposure,
                input_updates=summarize(input_times),one_second_calculation=summarize(samples),
                ready_batch_queue_handoff=summarize(publication),results_per_snapshot=len(scanner.latest_output),
                history_samples_per_series=max(map(len,scanner.scanner.history.values())),
                deadline_heap_entries=len(scanner.expirations),
                one_second_budget_used_percent=round(statistics.mean(samples)/10,3),
                scope='Synthetic in-memory consumer; excludes broker, network, SQLite, Excel and OS scheduling latency')


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--seconds',type=int,default=600)
    parser.add_argument('--pcr-only',action='store_true')
    parser.add_argument('--output',type=Path)
    args=parser.parse_args()
    if args.seconds < 301:
        parser.error('Use at least 301 seconds to include a full five-minute window')
    report=benchmark(args.seconds,not args.pcr_only)
    if args.output:
        args.output.parent.mkdir(parents=True,exist_ok=True)
        args.output.write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps(report,indent=2))
