import asyncio
import json
from pathlib import Path
import tempfile
import unittest

from volatility_scanner.apps.record_upstox import UpstoxRecorder
from volatility_scanner.brokers.upstox.market.pcr_time import StreamingAnalytics
from volatility_scanner.brokers.upstox.market.coverage import expiry_epoch
from volatility_scanner.storage.algorithm import read_algorithm_events
from volatility_scanner.storage.excel import export_directory


class TestStreamAnalytics(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.recorder = UpstoxRecorder(self.temp.name)
        self.rows = [dict(symbol=f'NSE_FO|{expiry}_{strike}_{kind}', strike_price=strike,
                         option_type=kind, expiry_date=expiry)
                     for expiry in ('2026-12-03', '2026-12-10')
                     for strike in (25000, 25100) for kind in ('CE', 'PE')]
        self.recorder.configure(self.rows, [], {'full': {r['symbol'] for r in self.rows}})
        self.timestamp = expiry_epoch('2026-12-03') - 86400
        self.scanner = StreamingAnalytics(pcr=True, exposure=True)

    async def asyncTearDown(self):
        await self.recorder.drain()
        for task in self.recorder.tasks:
            task.cancel()
        await asyncio.gather(*self.recorder.tasks, return_exceptions=True)
        await asyncio.to_thread(self.recorder.stop_writer)
        self.recorder.db.close()
        self.temp.cleanup()

    def ticks(self, minute):
        now = self.timestamp + minute * 60
        self.recorder.connected = True
        for row in self.rows:
            self.recorder.dispatch({'feeds': {row['symbol']: {'fullFeed': {'marketFF': {
                'ltpc': {'ltp': 100}, 'oi': 100 if row['option_type'] == 'CE' else 200, 'iv': 20}}}}})
            self.recorder.received[row['symbol']] = now
        self.recorder.dispatch({'feeds': {'NSE_INDEX|Nifty 50': {'fullFeed': {'indexFF': {'ltpc': {'ltp': 25050}}}}}})
        self.recorder.index_received = now
        return now

    async def test_stream_to_minute_storage_export_and_indexed_retrieval(self):
        for second in range(301):
            now = self.ticks(second / 60)
            events = self.scanner.consume(self.recorder, now)
            if second % 60 == 0:
                for bucket, source, payload in events:
                    json.dumps(payload, allow_nan=False)
                    self.recorder.enqueue(bucket, source, payload)
                await self.recorder.drain()
        path = Path(self.temp.name, 'capture.sqlite3')
        first = read_algorithm_events(path, bucket='2026-12-03_PCR_TIME', source='pcr_time_minute', limit=2)
        second = read_algorithm_events(path, bucket='2026-12-03_PCR_TIME', source='pcr_time_minute', after_id=first[-1]['id'])
        self.assertEqual(len(first) + len(second), 6)
        self.assertEqual(second[-1]['data']['pcr'], 2)
        self.assertTrue(second[-1]['data']['window_ready'])
        exposures = read_algorithm_events(path, bucket='2026-12-03_EXPOSURE', source='exposure_minute')
        self.assertTrue(exposures[-1]['data']['complete'])
        plan = self.recorder.db.execute("""EXPLAIN QUERY PLAN SELECT id FROM events
            WHERE source IN ('pcr_time_minute','exposure_minute')
            AND bucket=? AND source=? AND id>? ORDER BY id LIMIT ?""",
            ('2026-12-03_PCR_TIME', 'pcr_time_minute', 0, 10)).fetchall()
        self.assertIn('algorithm_lookup', str(plan))
        result = await asyncio.to_thread(export_directory, self.temp.name)
        self.assertTrue(result['complete'])
        self.assertTrue(list(Path(self.temp.name).rglob('*PCR_TIME.xlsx')))

    async def test_staleness_disconnection_missing_oi_and_zero_call_oi(self):
        now = self.ticks(0)
        list(self.scanner.calculate(self.recorder, now))
        stale = list(self.scanner.calculate(self.recorder, now + 60))
        self.assertTrue(all(not e[2]['valid'] for e in stale if e[1] == 'pcr_time_minute'))
        self.assertFalse(self.scanner.scanner.history)
        now = self.ticks(2)
        self.recorder.latest[self.rows[0]['symbol']].pop('oi')
        missing = list(self.scanner.calculate(self.recorder, now))
        expiry = next(e[2] for e in missing if e[0] == '2026-12-03_PCR_TIME')
        self.assertFalse(expiry['valid'])
        now = self.ticks(3)
        self.recorder.connected = False
        disconnected = list(self.scanner.calculate(self.recorder, now))
        self.assertFalse(any(e[2].get('valid') for e in disconnected))

    async def test_rollover_clears_only_changed_expiry_history(self):
        for second in range(301):
            list(self.scanner.calculate(self.recorder, self.ticks(second / 60)))
        self.rows = [row for row in self.rows if row['expiry_date'] == '2026-12-10']
        self.recorder.configure(self.rows, [], {'full': {r['symbol'] for r in self.rows}})
        results = list(self.scanner.calculate(self.recorder, self.ticks(301 / 60)))
        self.assertTrue(all(key[0] == '2026-12-10' for key in self.scanner.scanner.history))
        aggregate = next(e[2] for e in results if e[0] == '2026-12-10_PCR_TIME')
        self.assertTrue(aggregate['window_ready'])

    async def test_missing_iv_withholds_exposure_without_disabling_pcr(self):
        now = self.ticks(0)
        self.recorder.latest[self.rows[0]['symbol']]['iv'] = 0
        results = list(self.scanner.calculate(self.recorder, now))
        self.assertTrue(next(e[2] for e in results if e[0] == '2026-12-03_PCR_TIME')['valid'])
        self.assertFalse(next(e[2] for e in results if e[0] == '2026-12-03_EXPOSURE')['complete'])
