"""Explicit one-share RELIANCE BUY LIMIT test at latest LTP; AMO disabled, cancel immediately."""
import argparse
from collections import Counter
import json
import math
from pathlib import Path
import threading
import time

from volatility_scanner.brokers.upstox.api.client import APIClient, APIError


def verify(amo=False):
    output = Path('D:/market-data/volatility/reliability-20261109/' +
                  ('live-one-share-amo.json' if amo else 'live-one-share-order.json'))
    report = dict(instrument='NSE_EQ|INE002A01018', quantity=1, side='BUY', is_amo=amo)
    opened = threading.Event()
    counts = Counter()
    matching = []
    tag = 'live-check-' + str(int(time.time()))
    def message(event):
        counts[event.get('update_type', 'unknown')] += 1
        if event.get('tag') == tag:
            matching.append({key: event.get(key) for key in ('update_type', 'order_id', 'status',
                                                            'filled_quantity', 'pending_quantity')})
    try:
        with APIClient('algo', allow_live_mutations=True) as api:
            quote = api.call('getLtp', query={'instrument_key': report['instrument']})
            rows = [row for row in quote['data'].values()
                    if row.get('instrument_token') == report['instrument']]
            if len(rows) != 1:
                raise RuntimeError('Expected one matching quote; order not submitted')
            price = rows[0]['last_price']
            if not isinstance(price, (int, float)) or not math.isfinite(price) or price <= 0:
                raise RuntimeError('Invalid quote; order not submitted')
            report['limit_price'] = price
            with api.portfolio_stream(on_open=opened.set, on_message=message) as stream:
                report['connected'] = opened.wait(20)
                if not report['connected']:
                    raise RuntimeError('Portfolio connection failed; order not submitted')
                try:
                    result = api.call('placeOrder', body=dict(quantity=1, product='D', validity='DAY',
                        price=price, tag=tag, instrument_token=report['instrument'], order_type='LIMIT',
                        transaction_type='BUY', disclosed_quantity=0, trigger_price=0, is_amo=amo))
                    report['placement_status'] = result.get('status')
                    identifier = result.get('data', {}).get('order_id')
                    report['order_id'] = identifier
                    if identifier:
                        try:
                            cancelled = api.call('cancelOrder', query={'order_id': identifier})
                            report['cancel_status'] = cancelled.get('status')
                        except APIError as error:
                            report['cancel_error'] = str(error)
                        history = api.call('getOrderDetails', query={'order_id': identifier})
                        report['history_statuses'] = [row.get('status') for row in history.get('data', [])]
                    else:
                        report['reconciliation_required'] = True
                except APIError as error:
                    report.update(http=error.status, codes=list(error.codes), outcome_unknown=error.outcome_unknown)
                time.sleep(20)
                report['connected_at_end'] = stream.connected
    except Exception as error:
        report['failure_type'] = type(error).__name__
    finally:
        report.update(event_counts=dict(counts), matching_order_events=matching)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, indent=2), encoding='utf-8')
        print(json.dumps(report, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute-live', action='store_true', required=True)
    parser.add_argument('--amo', action='store_true')
    args = parser.parse_args()
    verify(args.amo)
