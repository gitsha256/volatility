import asyncio
import tempfile
import unittest
from unittest.mock import Mock

from volatility_scanner.apps.record_upstox import UpstoxRecorder
from volatility_scanner.brokers.upstox.api.market_rest import MarketREST
from volatility_scanner.brokers.upstox.market.coverage import sync_subscriptions


class ReliabilityTests(unittest.IsolatedAsyncioTestCase):
    async def test_rollover_retains_history_and_removes_expired_routing(self):
        with tempfile.TemporaryDirectory() as directory:
            recorder = UpstoxRecorder(directory)
            stream = Mock(subscriptions={'full': set()})
            stream.subscribe.side_effect = lambda keys, mode: stream.subscriptions[mode].update(keys)
            stream.unsubscribe.side_effect = lambda keys: stream.subscriptions['full'].difference_update(keys)
            try:
                for number in range(20):
                    key = f'NSE_FO|{number}'
                    plan = {'full': {key}}
                    recorder.configure([dict(symbol=key, strike_price=25000, option_type='CE',
                                             expiry_date=f'2026-12-{number + 1:02d}')], [], plan)
                    sync_subscriptions(stream, plan)
                    recorder.dispatch({'feeds': {key: {'fullFeed': {'marketFF': {'ltpc': {'ltp': number}}}}}})
                    await recorder.drain()
                    self.assertEqual(set(recorder.symbols), {key})
                    self.assertEqual(set(recorder.latest), {key})
                    self.assertEqual(stream.subscriptions['full'], {key})
                count = recorder.db.execute("select count(*) from events where source='websocket'").fetchone()[0]
                self.assertEqual(count, 20)
                self.assertEqual(recorder.db.execute('pragma integrity_check').fetchone()[0], 'ok')
            finally:
                await recorder.drain()
                for task in recorder.tasks:
                    task.cancel()
                await asyncio.gather(*recorder.tasks, return_exceptions=True)
                await asyncio.to_thread(recorder.stop_writer)
                recorder.db.close()

    async def test_renewal_reuses_rest_transport_and_rate_budget(self):
        rest = MarketREST('initial')
        try:
            session = rest.session
            rest.calls.append(123)
            rest.set_token('renewed')
            self.assertIs(rest.session, session)
            self.assertEqual(rest.session.headers['Authorization'], 'Bearer renewed')
            self.assertEqual(list(rest.calls), [123])
        finally:
            rest.close()
