"""Live negative test: zero quantity, invalid instrument, no AMO. No valid trade submitted."""
from collections import Counter
import argparse
import json
from pathlib import Path
import threading
import time

from volatility_scanner.brokers.upstox.api.client import APIClient, APIError


def main(real_instrument=False):
    output = Path('D:/market-data/volatility/reliability-20261109/' +
                  ('live-real-instrument-validation.json' if real_instrument else 'live-order-rejection.json'))
    counts = Counter()
    opened = threading.Event()
    report = {'test': 'Live invalid-order rejection, not executable trade verification',
              'quantity': 0, 'instrument': 'INVALID_TEST_INSTRUMENT', 'is_amo': False}
    def message(event):
        counts[event.get('update_type', 'unknown')] += 1
    with APIClient('algo', allow_live_mutations=True) as api:
        if real_instrument:
            result = api.call('searchInstrument', query=dict(query='RELIANCE', exchanges='NSE',
                              segments='EQ', instrument_types='EQ', records=20, page_number=1))
            rows = [row for row in result.get('data', []) if row.get('trading_symbol') == 'RELIANCE'
                    and row.get('segment') == 'NSE_EQ']
            if len(rows) != 1:
                raise RuntimeError('Expected one exact instrument match; no order request sent')
            report['instrument'] = rows[0]['instrument_key']
            report['symbol'] = rows[0]['trading_symbol']
            report['test'] = 'Live real-instrument zero-quantity validation; no executable trade'
        with api.portfolio_stream(on_open=opened.set, on_message=message) as stream:
            report['connected'] = opened.wait(20)
            if not report['connected']:
                raise RuntimeError('Portfolio stream connection failed; no request sent')
            body = dict(quantity=0, product='D', validity='IOC', price=0,
                        tag='negative-api-test', instrument_token=report['instrument'],
                        order_type='LIMIT', transaction_type='BUY', disclosed_quantity=0,
                        trigger_price=0, is_amo=False)
            try:
                result = api.call('placeOrder', body=body)
                report['response_status'] = result.get('status')
                report['unexpected_acceptance'] = True
            except APIError as error:
                report.update(http=error.status, codes=list(error.codes),
                              outcome_unknown=error.outcome_unknown,
                              rejected=400 <= error.status < 500 and not error.outcome_unknown)
            time.sleep(30)
            report['connected_at_end'] = stream.connected
    report['events'] = dict(counts)
    report['real_position_and_holding_changes_verified'] = False
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--real-instrument', action='store_true')
    main(parser.parse_args().real_instrument)
