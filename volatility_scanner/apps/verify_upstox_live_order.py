"""Explicit one-share RELIANCE BUY LIMIT test at latest LTP; AMO disabled, cancel immediately."""
import argparse
from collections import Counter
import json
import math
from pathlib import Path
import threading
import time

from volatility_scanner.brokers.upstox.api.client import APIClient, APIError


def verify(amo=False,output=None):
    output = Path(output) if output else Path('D:/market-data/volatility/reliability-20261109/' +
                  ('live-one-share-amo.json' if amo else 'live-one-share-order.json'))
    report = dict(instrument='NSE_EQ|INE002A01018', quantity=1, side='BUY', is_amo=amo)
    opened = threading.Event()
    counts = Counter()
    matching = []
    target_events=[]
    tag = 'live-check-' + str(int(time.time()))
    def message(event):
        counts[event.get('update_type', 'unknown')] += 1
        if event.get('tag') == tag:
            matching.append({key: event.get(key) for key in ('update_type', 'order_id', 'status',
                                                            'filled_quantity', 'pending_quantity')})
        instrument=event.get('instrument_key') or event.get('instrument_token')
        if instrument==report['instrument']:
            target_events.append({key:event.get(key) for key in ('update_type','order_id','status',
                'filled_quantity','pending_quantity','quantity','net_quantity') if key in event})
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
                    data = result.get('data', {})
                    identifiers = data.get('order_ids') or [data.get('order_id')]
                    identifier = identifiers[0] if len(identifiers) == 1 else None
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
                if not report.get('order_id'):
                    identifiers = {event['order_id'] for event in matching if event.get('order_id')}
                    if len(identifiers) == 1:
                        report['order_id'] = identifiers.pop()
                if report.get('order_id'):
                    final=api.call('getOrderDetails',query={'order_id':report['order_id']})
                    history=final.get('data',[])
                    report['final_order_states']=[{key:row.get(key) for key in
                        ('status','filled_quantity','pending_quantity','average_price','status_message')} for row in history]
                    report['reconciliation_required'] = not history or history[-1].get('status') not in (
                        'complete', 'rejected', 'cancelled')
                report['connected_at_end'] = stream.connected
    except Exception as error:
        report['failure_type'] = type(error).__name__
    finally:
        report.update(event_counts=dict(counts), matching_order_events=matching)
        report['target_instrument_events']=target_events
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, indent=2), encoding='utf-8')
        print(json.dumps(report, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute-live', action='store_true', required=True)
    parser.add_argument('--amo', action='store_true')
    parser.add_argument('--output',type=Path)
    args = parser.parse_args()
    verify(args.amo,args.output)
