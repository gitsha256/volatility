"""Observe real portfolio events without mutating the account; store counts only."""
import argparse
from collections import Counter
import json
from pathlib import Path
import threading
import time

from volatility_scanner.brokers.upstox.api.client import APIClient


def verify(output, seconds):
    opened = threading.Event()
    counts = Counter()
    errors = Counter()
    lock = threading.Lock()
    def message(event):
        with lock:
            counts[event.get('update_type', 'unknown')] += 1
    def error(event):
        with lock:
            errors[event.get('code', 'unknown')] += 1
    started = time.monotonic()
    connected = False
    with APIClient('algo') as api:
        with api.portfolio_stream(on_open=opened.set, on_message=message, on_error=error) as stream:
            connected = opened.wait(20)
            if connected:
                time.sleep(seconds)
            remained_connected = stream.connected
    report = dict(connected=connected, connected_at_end=remained_connected,
                  seconds=round(time.monotonic()-started, 3), events=dict(counts), errors=dict(errors),
                  mutations_performed=False,
                  event_verification={key: 'observed' if counts[key] else 'not_observed'
                                      for key in ('order', 'gtt_order', 'position', 'holding')})
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps(report, indent=2))
    return connected and remained_connected and not errors


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--seconds', type=int, default=60)
    parser.add_argument('--output', type=Path, default=Path('D:/market-data/volatility/verification/portfolio.json'))
    args = parser.parse_args()
    if not 1 <= args.seconds <= 3600:
        parser.error('seconds must be between 1 and 3600')
    raise SystemExit(0 if verify(args.output, args.seconds) else 1)
