import asyncio
import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import datetime
from pathlib import Path

from google.protobuf import json_format
from upstox_client.feeder.proto.MarketDataFeedV3_pb2 import FeedResponse

from volatility_scanner.apps.replay_upstox import replay
from volatility_scanner.storage.journal import Recorder, IST
from volatility_scanner.storage.excel import export_directory
from volatility_scanner.brokers.upstox.streams.market import VerifiedStreamer
from volatility_scanner.storage.lifecycle import capture_guard
from volatility_scanner.brokers.fyers.auth import AuthError


class TestOptimizedCapture(unittest.IsolatedAsyncioTestCase):
    async def test_shared_writer_burst_and_raw_replay(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'capture'
            recorder = Recorder(output)
            streamer = VerifiedStreamer()
            streamer.raw_callback = recorder.record_frame
            decoded = []
            streamer.on('message', decoded.append)
            try:
                for strike in range(147):
                    recorder.register(dict(symbol=f'NSE_FO|{strike}', strike_price=24000 + strike * 50,
                                           option_type='CE'), 1800000000)
                for tick in range(10000):
                    recorder.on_message(dict(symbol=f'NSE_FO|{tick % 147}', ltp=tick))
                message = json_format.ParseDict({'feeds': {'NSE_FO|0': {'fullFeed': {'marketFF': {
                    'ltpc': {'ltp': 101}, 'oi': 500, 'iv': 0.15,
                    'optionGreeks': {'delta': 0.5},
                }}}}}, FeedResponse())
                streamer.handle_message(None, message.SerializeToString())
                await asyncio.to_thread(streamer.stop_decoder)
                self.assertEqual(decoded[0]['feeds']['NSE_FO|0']['fullFeed']['marketFF']['oi'], 500)
                await asyncio.sleep(0)
                await recorder.drain()
                self.assertEqual(recorder.db.execute('select count(*) from events').fetchone()[0], 10147)
                self.assertEqual(recorder.db.execute('select count(*) from raw_frames').fetchone()[0], 1)
                self.assertLess(recorder.health()['commits'], 147)
                prices = [json.loads(p)['ltp'] for (p,) in recorder.db.execute(
                    "select payload from events where bucket='1800000000_24000' and source='websocket' order by id")]
                self.assertEqual(prices, list(range(0, 10000, 147)))
            finally:
                await recorder.drain()
                for task in recorder.tasks:
                    task.cancel()
                await asyncio.gather(*recorder.tasks, return_exceptions=True)
                recorder.stop_writer()
                recorder.db.close()
            target = Path(directory) / 'replay'
            self.assertEqual(await replay(output / 'capture.sqlite3', target), 1)
            with closing(sqlite3.connect(target / 'capture.sqlite3')) as conn:
                data = json.loads(conn.execute('select payload from events').fetchone()[0])
                self.assertEqual(data['oi'], 500)
                self.assertEqual(data['optionGreeks.delta'], 0.5)
                self.assertIn('socket_received_at_ist', data)

    async def test_hourly_export_skips_unchanged_and_keeps_closed_hour(self):
        with tempfile.TemporaryDirectory() as directory:
            recorder = Recorder(directory)
            try:
                for hour in (9, 10):
                    now = datetime(2026, 11, 9, hour, tzinfo=IST)
                    recorder.channel('1800000000_25000').put_nowait((now, 'websocket', {'ltp': 100 + hour}))
                await recorder.drain()
                self.assertEqual(export_directory(directory)['files'], 2)
                closed = Path(directory) / '2026-11-09/09/1800000000_25000.xlsx'
                changed = Path(directory) / '2026-11-09/10/1800000000_25000.xlsx'
                stamp = closed.stat().st_mtime_ns
                self.assertEqual(export_directory(directory)['files'], 0)
                recorder.channel('1800000000_25000').put_nowait((
                    datetime(2026, 11, 9, 10, 1, tzinfo=IST), 'websocket', {'ltp': 111}))
                await recorder.drain()
                self.assertEqual(export_directory(directory)['files'], 1)
                self.assertEqual(closed.stat().st_mtime_ns, stamp)
                self.assertTrue(changed.exists())
            finally:
                for task in recorder.tasks:
                    task.cancel()
                await asyncio.gather(*recorder.tasks, return_exceptions=True)
                recorder.stop_writer()
                recorder.db.close()

    async def test_singleton_lock(self):
        with tempfile.TemporaryDirectory() as directory:
            with capture_guard(directory):
                with self.assertRaises(AuthError):
                    with capture_guard(directory):
                        pass
            with capture_guard(directory):
                pass

    async def test_export_runs_in_separate_process(self):
        import multiprocessing
        from concurrent.futures import ProcessPoolExecutor
        with tempfile.TemporaryDirectory() as directory:
            recorder = Recorder(directory)
            try:
                recorder.enqueue('test', 'one_second', {'ltp': 100})
                await recorder.drain()
                with ProcessPoolExecutor(max_workers=1, mp_context=multiprocessing.get_context('spawn')) as pool:
                    result = await asyncio.get_running_loop().run_in_executor(pool, export_directory, directory)
                self.assertEqual(result['files'], 1)
                self.assertTrue(result['complete'])
            finally:
                for task in recorder.tasks:
                    task.cancel()
                await asyncio.gather(*recorder.tasks, return_exceptions=True)
                recorder.stop_writer()
                recorder.db.close()

    async def test_write_failure_surfaces_and_raw_decode_failure_is_retained(self):
        from unittest.mock import MagicMock, patch
        with tempfile.TemporaryDirectory() as directory:
            recorder = Recorder(directory)
            streamer = VerifiedStreamer()
            streamer.raw_callback = recorder.record_frame
            try:
                streamer.handle_message(None, b'\xff\xff')
                await asyncio.to_thread(streamer.stop_decoder)
                await recorder.drain()
                self.assertIsNotNone(streamer.decoder_failure)
                self.assertEqual(recorder.db.execute('select payload from raw_frames').fetchone()[0], b'\xff\xff')
                broken = MagicMock()
                broken.executemany.side_effect = sqlite3.OperationalError('disk full')
                with patch.object(recorder, 'db', broken), self.assertLogs('volatility_scanner.storage.journal', level='ERROR'):
                    recorder.enqueue('test', 'websocket', {'ltp': 1})
                    with self.assertRaisesRegex(RuntimeError, 'Recording failed'):
                        await recorder.drain()
            finally:
                await asyncio.to_thread(streamer.stop_decoder)
                for task in recorder.tasks:
                    task.cancel()
                await asyncio.gather(*recorder.tasks, return_exceptions=True)
                recorder.stop_writer()
                recorder.db.close()

    async def test_graceful_stop_and_health_without_network(self):
        from types import SimpleNamespace
        from unittest.mock import Mock, patch, AsyncMock
        import queue
        from volatility_scanner.apps.record_upstox import run
        with tempfile.TemporaryDirectory() as directory:
            streamer = Mock(decoder_failure=None, frames_received=0, bytes_received=0, decode_ms=0)
            streamer.decode_queue = queue.Queue()
            rest = Mock(status={})
            async def stop():
                await asyncio.sleep(0.4)
                Path(directory, 'stop.request').touch()
            stopper = asyncio.create_task(stop())
            with patch('volatility_scanner.apps.record_upstox.ensure_upstox_token', return_value='unused'), patch(
                'volatility_scanner.apps.record_upstox.discover', return_value=([
                    dict(symbol='NSE_FO|123', strike_price=25000, option_type='CE', expiry_date='2026-11-09')], [], 25001)
            ), patch('volatility_scanner.apps.record_upstox.MarketREST', return_value=rest), patch(
                'volatility_scanner.apps.record_upstox.supplements', new_callable=AsyncMock
            ), patch('volatility_scanner.apps.record_upstox.VerifiedStreamer', return_value=streamer):
                await run(SimpleNamespace(output=directory, export_interval=60, min_free_gb=0))
            await stopper
            streamer.disconnect.assert_called_once()
            streamer.stop_decoder.assert_called_once()
            self.assertTrue(Path(directory, 'health.json').exists())
            self.assertFalse(Path(directory, 'stop.request').exists())
            with closing(sqlite3.connect(Path(directory, 'capture.sqlite3'))) as conn:
                self.assertGreater(conn.execute('select count(*) from events').fetchone()[0], 0)
            self.assertEqual(len(list(Path(directory).rglob('*.xlsx'))), 2)
