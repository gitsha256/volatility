import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, AsyncMock, patch

from volatility_scanner.brokers.fyers.auth import AuthError
from volatility_scanner.brokers.upstox.market.coverage import supplements
from volatility_scanner.apps.record_upstox import run


class TestDailyRetry(unittest.IsolatedAsyncioTestCase):
    async def test_failed_daily_job_is_retried_next_round(self):
        recorder = Mock(contracts={})
        rest = Mock()
        rest.get.side_effect = [AuthError('temporary outage'), {'status': 'success', 'data': []}]
        with patch('volatility_scanner.brokers.upstox.market.coverage.asyncio.sleep',
                   new=AsyncMock(side_effect=[None, asyncio.CancelledError])), self.assertLogs(
                       'volatility_scanner.brokers.upstox.market.coverage', level='WARNING'):
            with self.assertRaises(asyncio.CancelledError):
                await supplements(rest, recorder, {'fii'})
        self.assertEqual(rest.get.call_count, 2)
        recorder.enqueue.assert_called_once()


class TestRunnerFailures(unittest.IsolatedAsyncioTestCase):
    async def exercise(self, directory, streamer, fault):
        import queue
        streamer.decode_queue = queue.Queue()
        rest = Mock(status={})
        async def stop():
            await asyncio.sleep(0.6)
            Path(directory, 'stop.request').touch()
        stopper = asyncio.create_task(stop())
        async def broken_sampler(_):
            raise RuntimeError('snapshot task failed')
        patches = [patch('volatility_scanner.apps.record_upstox.ensure_upstox_token', return_value='unused'),
                   patch('volatility_scanner.apps.record_upstox.upstox_client.ApiClient', side_effect=AssertionError('Unused REST client created')),
                   patch('volatility_scanner.apps.record_upstox.MarketREST', return_value=rest),
                   patch('volatility_scanner.apps.record_upstox.discover', return_value=([
                       dict(symbol='NSE_FO|123', strike_price=25000, option_type='CE', expiry_date='2026-11-09')], [], 25000)),
                   patch('volatility_scanner.apps.record_upstox.VerifiedStreamer', return_value=streamer)]
        if fault:
            patches.append(patch('volatility_scanner.apps.record_upstox.UpstoxRecorder.sample_options', new=broken_sampler))
        from contextlib import ExitStack
        try:
            with ExitStack() as stack:
                for context in patches: stack.enter_context(context)
                await run(SimpleNamespace(output=directory, export_interval=60, min_free_gb=0))
        finally:
            stopper.cancel()
            await asyncio.gather(stopper, return_exceptions=True)
        return rest

    async def test_snapshot_failure_is_reported(self):
        with tempfile.TemporaryDirectory() as directory:
            streamer = Mock(decoder_failure=None, frames_received=0, bytes_received=0, decode_ms=0)
            with self.assertRaisesRegex(RuntimeError, 'snapshot task failed'):
                await self.exercise(directory, streamer, True)

    async def test_disconnect_error_still_drains_and_closes_writer(self):
        with tempfile.TemporaryDirectory() as directory:
            streamer = Mock(decoder_failure=None, frames_received=0, bytes_received=0, decode_ms=0)
            streamer.disconnect.side_effect = RuntimeError('disconnect failed')
            with self.assertLogs('volatility_scanner.apps.record_upstox', level='WARNING'):
                rest = await self.exercise(directory, streamer, False)
            streamer.stop_decoder.assert_called_once()
            rest.close.assert_called_once()
            self.assertTrue(list(Path(directory).rglob('*.xlsx')))

class TestLiteralExcelExport(unittest.IsolatedAsyncioTestCase):
    async def test_formula_text_and_chunk_boundaries_remain_literal(self):
        from openpyxl import load_workbook
        from volatility_scanner.storage.journal import Recorder
        from volatility_scanner.storage.excel import export_directory
        with tempfile.TemporaryDirectory() as directory:
            recorder = Recorder(directory)
            try:
                literal = '=1+1'
                long_literal = 'a' * 32000 + '=2+2'
                recorder.enqueue('literal', 'rest', {'formula_text': literal, 'long_text': long_literal})
                await recorder.drain()
                export_directory(directory)
                book = load_workbook(next(Path(directory).rglob('*.xlsx')), read_only=True, data_only=False)
                try:
                    header = [cell.value for cell in next(book.active.rows)]
                    cells = list(book.active.rows)[1]
                    formula = cells[header.index('formula_text')]
                    tail = cells[header.index('long_text__part2')]
                    self.assertEqual(formula.value, literal)
                    self.assertEqual(formula.data_type, 's')
                    self.assertEqual(tail.value, '=2+2')
                    self.assertEqual(tail.data_type, 's')
                finally:
                    book.close()
            finally:
                for task in recorder.tasks: task.cancel()
                await asyncio.gather(*recorder.tasks, return_exceptions=True)
                recorder.stop_writer()
                recorder.db.close()
