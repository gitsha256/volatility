import asyncio
import json
import tempfile
import unittest

from volatility_scanner.apps.record_upstox import UpstoxRecorder, UPSTOX_INDEX


class TestUpstoxCapture(unittest.IsolatedAsyncioTestCase):
    async def test_stream_fields_and_second_snapshots(self):
        with tempfile.TemporaryDirectory() as directory:
            recorder = UpstoxRecorder(directory)
            recorder.register(dict(symbol='NSE_FO|123', strike_price=25000,
                                   option_type='CE'), 1800000000)
            recorder.connected = True
            recorder.dispatch({'currentTs': '123456', 'feeds': {
                'NSE_FO|123': {'fullFeed': {'marketFF': {
                    'ltpc': {'ltp': 100}, 'oi': 500, 'iv': 0.15,
                    'optionGreeks': {'delta': 0.5},
                    'marketLevel': {'bidAskQuote': [{'bidP': 99, 'askP': 101}]},
                }}},
                UPSTOX_INDEX: {'fullFeed': {'indexFF': {'ltpc': {'ltp': 25001}}}},
            }})
            sampler = asyncio.create_task(recorder.sample_options())
            await asyncio.sleep(0)
            sampler.cancel()
            await asyncio.gather(sampler, return_exceptions=True)
            await recorder.drain()
            rows = recorder.db.execute(
                "select source,payload from events where source!='chain' and bucket!='NIFTY50'"
            ).fetchall()
            self.assertEqual({source for source, _ in rows}, {'websocket', 'one_second'})
            for source, payload in rows:
                data = json.loads(payload)
                self.assertEqual(data['oi'], 500)
                self.assertEqual(data['iv'], 0.15)
                self.assertEqual(data['optionGreeks.delta'], 0.5)
                self.assertEqual(data['marketLevel.bidAskQuote'][0]['bidP'], 99)
                if source == 'one_second':
                    self.assertFalse(data['stale'])
            self.assertEqual(recorder.index['ltp'], 25001)
            self.assertEqual(recorder.db.execute(
                "select count(*) from events where bucket='NIFTY50' and source='websocket'"
            ).fetchone()[0], 1)
            for task in recorder.tasks:
                task.cancel()
            await asyncio.gather(*recorder.tasks, return_exceptions=True)
            recorder.stop_writer()
            recorder.db.close()

class TestWholeChain(unittest.TestCase):
    def test_transport_verifies_tls_and_disables_nagle(self):
        import socket
        import ssl
        from unittest.mock import Mock, patch
        from volatility_scanner.brokers.upstox.streams.market import VerifiedFeeder
        client = Mock()
        client.configuration.auth_settings.return_value = {'OAUTH2': {'value': 'Bearer private'}}
        with patch('volatility_scanner.brokers.upstox.streams.market.websocket.WebSocketApp'), patch(
            'volatility_scanner.brokers.upstox.streams.market.threading.Thread'
        ) as thread:
            VerifiedFeeder(api_client=client).connect()
        settings = thread.call_args.kwargs['kwargs']
        self.assertEqual(settings['sslopt']['cert_reqs'], ssl.CERT_REQUIRED)
        self.assertTrue(settings['sslopt']['check_hostname'])
        self.assertIn((socket.IPPROTO_TCP, socket.TCP_NODELAY, 1), settings['sockopt'])
