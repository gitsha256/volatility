import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import MagicMock, patch

from volatility_scanner.apps import verify_upstox_live_order as verifier


class LiveOrderVerifierTests(unittest.TestCase):
    def test_v3_and_legacy_order_ids_are_cancelled_and_reconciled(self):
        for data in ({'order_ids': ['test-order']}, {'order_id': 'test-order'}):
            with self.subTest(data=data), TemporaryDirectory() as directory:
                api = MagicMock()
                api.__enter__.return_value = api
                stream = MagicMock(connected=True)
                stream.__enter__.return_value = stream

                def portfolio_stream(**callbacks):
                    callbacks['on_open']()
                    return stream

                api.portfolio_stream.side_effect = portfolio_stream

                def call(operation, **kwargs):
                    if operation == 'getLtp':
                        return {'data': {'RELIANCE': {
                            'instrument_token': 'NSE_EQ|INE002A01018', 'last_price': 1339.9}}}
                    if operation == 'placeOrder':
                        return {'status': 'success', 'data': data}
                    if operation == 'cancelOrder':
                        self.assertEqual(kwargs['query']['order_id'], 'test-order')
                        return {'status': 'success'}
                    if operation == 'getOrderDetails':
                        return {'data': [{'status': 'cancelled', 'filled_quantity': 0}]}
                    self.fail(operation)

                api.call.side_effect = call
                output = Path(directory) / 'report.json'
                with patch.object(verifier, 'APIClient', return_value=api), \
                        patch.object(verifier.time, 'sleep'), patch('builtins.print'):
                    verifier.verify(output=output)
                report = json.loads(output.read_text())
                self.assertEqual(report['order_id'], 'test-order')
                self.assertFalse(report['reconciliation_required'])
                self.assertEqual(sum(c.args[0] == 'placeOrder' for c in api.call.call_args_list), 1)
                self.assertEqual(sum(c.args[0] == 'cancelOrder' for c in api.call.call_args_list), 1)
