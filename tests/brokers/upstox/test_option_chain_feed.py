import asyncio
from datetime import datetime, timezone
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from google.protobuf.json_format import ParseDict
from upstox_client.feeder.proto.MarketDataFeedV3_pb2 import FeedResponse

from volatility_scanner.brokers.upstox.streams.market import VerifiedStreamer
from volatility_scanner.brokers.upstox.streams.option_chain import OptionChainFeed, INDEX
from volatility_scanner.brokers.upstox.market.pcr_time import StreamingAnalytics


def frame(key, **data):
    return ParseDict({'feeds': {key: data}}, FeedResponse())


class TestOptionChainFeed(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        rows = [dict(symbol=key, strike_price=25000, option_type=kind, expiry_date='2026-12-03')
                for key, kind in (('NSE_FO|1', 'CE'), ('NSE_FO|2', 'PE'))]
        self.state = SimpleNamespace(loop=asyncio.get_running_loop(),
            contracts={r['symbol']: r for r in rows}, latest={}, received={}, index={},
            index_received=None, connected=False, symbols={r['symbol']: ('strike', r['option_type']) for r in rows})
        self.feed = OptionChainFeed(self.state)
        self.now = time.time()
        self.feed.connection(True, self.now - 1)

    def send(self, payload, now=None):
        timestamp = self.now if now is None else now
        self.feed.submit(payload, datetime.fromtimestamp(timestamp, timezone.utc).isoformat(), time.perf_counter_ns())

    async def test_protobuf_to_minute_calculation_without_json_conversion(self):
        streamer = VerifiedStreamer(protobuf_callback=self.feed.submit, max_decode_queue=2)
        try:
            with patch('volatility_scanner.brokers.upstox.streams.market.json_format.MessageToDict',
                       side_effect=AssertionError('Full dictionary conversion used')):
                for key, oi in (('NSE_FO|1', 100), ('NSE_FO|2', 200)):
                    raw = frame(key, firstLevelWithGreeks={'oi': oi, 'iv': 20}).SerializeToString()
                    streamer.handle_message(None, raw)
                streamer.handle_message(None, frame(INDEX, fullFeed={'indexFF': {'ltpc': {'ltp': 25000}}}).SerializeToString())
                await asyncio.to_thread(streamer.stop_decoder)
                await asyncio.sleep(0)
            self.assertIsNone(streamer.decoder_failure)
            events = list(StreamingAnalytics(exposure=True).calculate(self.state, time.time()))
            self.assertEqual(next(e[2] for e in events if e[0].endswith('_PCR_TIME'))['pcr'], 2)
            self.assertTrue(next(e[2] for e in events if e[0].endswith('_EXPOSURE'))['complete'])
        finally:
            await asyncio.to_thread(streamer.stop_decoder)

    async def test_hot_key_coalescing_and_unknown_instrument_memory_bound(self):
        message = frame('NSE_FO|1', fullFeed={'marketFF': {'oi': 100, 'iv': 20}})
        for number in range(10000):
            message.feeds['NSE_FO|1'].fullFeed.marketFF.oi = number
            self.send(message)
        self.send(frame('NSE_FO|not-subscribed', firstLevelWithGreeks={'oi': 999, 'iv': 20}))
        self.assertEqual(self.feed.health()['pending_instruments'], 1)
        self.assertEqual(self.feed.coalesced, 9999)
        await asyncio.sleep(0)
        self.assertEqual(self.state.latest['NSE_FO|1']['oi'], 9999)
        self.assertEqual(self.feed.handoffs, 1)

    async def test_ltpc_updates_do_not_refresh_oi_age_and_zero_oi_survives(self):
        self.send(frame('NSE_FO|1', fullFeed={'marketFF': {'oi': 0, 'iv': 20}}))
        await asyncio.sleep(0)
        self.send(frame('NSE_FO|1', ltpc={'ltp': 123}), self.now + 60)
        await asyncio.sleep(0)
        self.assertEqual(self.state.latest['NSE_FO|1']['oi'], 0)
        self.assertAlmostEqual(self.state.received['NSE_FO|1'], self.now, places=5)
        self.assertEqual(self.state.latest['NSE_FO|1']['ltp'], 123)

    async def test_reconnect_invalidates_old_cache_and_old_queued_frames(self):
        message = frame('NSE_FO|1', firstLevelWithGreeks={'oi': 100, 'iv': 20})
        self.send(message)
        await asyncio.sleep(0)
        self.feed.connection(False, self.now + 1)
        self.feed.connection(True, self.now + 2)
        self.send(message, self.now)
        await asyncio.sleep(0)
        self.assertFalse(self.state.received)
        self.send(message, self.now + 3)
        await asyncio.sleep(0)
        self.assertAlmostEqual(self.state.received['NSE_FO|1'], self.now + 3, places=5)

    async def test_rollover_drops_pending_removed_contract_and_close_drains(self):
        self.send(frame('NSE_FO|1', firstLevelWithGreeks={'oi': 100, 'iv': 20}))
        self.state.contracts.pop('NSE_FO|1')
        self.feed.configure(self.state.contracts)
        self.feed.close()
        await asyncio.sleep(0)
        self.assertNotIn('NSE_FO|1', self.state.latest)
        self.send(frame('NSE_FO|2', firstLevelWithGreeks={'oi': 100, 'iv': 20}))
        self.assertFalse(self.feed.pending)

    async def test_invalid_oi_is_not_replaced_with_fresh_valid_previous_value(self):
        self.send(frame('NSE_FO|1', firstLevelWithGreeks={'oi': 100, 'iv': 20}))
        await asyncio.sleep(0)
        self.send(frame('NSE_FO|1', firstLevelWithGreeks={'oi': -1, 'iv': 20}))
        await asyncio.sleep(0)
        self.assertIsNone(self.state.latest['NSE_FO|1']['oi'])

    async def test_invalid_iv_does_not_discard_valid_oi(self):
        message = frame('NSE_FO|1', firstLevelWithGreeks={'oi': 100, 'iv': 20})
        message.feeds['NSE_FO|1'].firstLevelWithGreeks.iv = float('nan')
        self.send(message)
        await asyncio.sleep(0)
        self.assertEqual(self.state.latest['NSE_FO|1']['oi'], 100)
        self.assertIsNone(self.state.latest['NSE_FO|1']['iv'])


class TestDecoderBounds(unittest.TestCase):
    def test_bounded_queue_backpressures_instead_of_dropping(self):
        release, entered, done = threading.Event(), threading.Event(), threading.Event()
        calls = []
        def consume(*_):
            entered.set()
            release.wait(5)
            calls.append(1)
        streamer = VerifiedStreamer(protobuf_callback=consume, max_decode_queue=1)
        raw = frame(INDEX, ltpc={'ltp': 25000}).SerializeToString()
        producer = threading.Thread(target=lambda: (streamer.handle_message(None, raw), done.set()))
        try:
            streamer.handle_message(None, raw)
            self.assertTrue(entered.wait(2))
            streamer.handle_message(None, raw)
            producer.start()
            self.assertFalse(done.wait(.05))
            release.set()
            producer.join(2)
            streamer.stop_decoder()
            self.assertTrue(done.is_set())
            self.assertEqual(len(calls), 3)
        finally:
            release.set()
            streamer.stop_decoder()

    def test_oversized_frame_surfaces_failure(self):
        streamer = VerifiedStreamer(max_decode_queue=1, max_frame_bytes=1)
        try:
            streamer.handle_message(None, b'oversized')
            self.assertIsInstance(streamer.decoder_failure, RuntimeError)
            self.assertTrue(streamer.decode_queue.empty())
        finally:
            streamer.stop_decoder()
