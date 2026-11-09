import asyncio
import tempfile
import unittest
from pathlib import Path

from openpyxl import load_workbook

from volatility_scanner.storage.journal import Recorder, INDEX


class TestRecorder(unittest.IsolatedAsyncioTestCase):
    async def test_burst_isolation_export_and_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            recorder = Recorder(directory)
            for strike in (25000, 25050):
                for leg in ('CE', 'PE'):
                    recorder.register(dict(symbol=f'{strike}{leg}', strike_price=strike,
                                           option_type=leg, oi=123), '1800000000')
            for i in range(250):
                recorder.on_message(dict(symbol='25000CE', ltp=i, bid_size=i + 1))
            recorder.on_message(dict(symbol='25050PE', ltp=77))
            recorder.on_message(dict(symbol=INDEX, ltp=25123))
            await asyncio.sleep(0)
            sampler = asyncio.create_task(recorder.sample_index())
            await asyncio.sleep(0)
            sampler.cancel()
            await asyncio.gather(sampler, return_exceptions=True)
            await recorder.drain()
            self.assertEqual(recorder.db.execute('SELECT count(*) FROM events').fetchone()[0], 256)
            recorder.export()
            files = list(Path(directory).rglob('*.xlsx'))
            self.assertEqual(len(files), 3)
            strike_file = next(p for p in files if p.name == '1800000000_25000.xlsx')
            book = load_workbook(strike_file, read_only=True)
            rows = list(book.active.values)
            columns = rows[0]
            ltp = columns.index('ltp')
            self.assertEqual([r[ltp] for r in rows[3:]], list(range(250)))
            book.close()
            for task in recorder.tasks:
                task.cancel()
            await asyncio.gather(*recorder.tasks, return_exceptions=True)
            recorder.stop_writer()
            recorder.db.close()
            restarted = Recorder(directory)
            self.assertEqual(restarted.db.execute('SELECT count(*) FROM events').fetchone()[0], 256)
            restarted.export()
            restarted.stop_writer()
            restarted.db.close()

class TestStrikeSelection(unittest.TestCase):
    def test_four_each_side_excludes_exact_spot(self):
        from volatility_scanner.apps.record_market import select_strikes
        rows = [dict(strike_price=s, option_type=leg, symbol=f'{s}{leg}')
                for s in range(24500, 25550, 50) for leg in ('CE', 'PE')]
        selected = select_strikes(rows, 25000, per_side=4)
        self.assertEqual(len(selected), 16)
        self.assertEqual(sorted({r['strike_price'] for r in selected}),
                         [24800, 24850, 24900, 24950, 25050, 25100, 25150, 25200])
        selected = select_strikes(rows, 25023, per_side=4)
        self.assertEqual(sorted({r['strike_price'] for r in selected}),
                         [24850, 24900, 24950, 25000, 25050, 25100, 25150, 25200])

    def test_24_each_side_within_standard_plan(self):
        from volatility_scanner.apps.record_market import select_strikes
        rows = [dict(strike_price=s, option_type=leg, symbol=f'{s}{leg}')
                for s in range(22000, 28050, 50) for leg in ('CE', 'PE')]
        for spot in (25000, 25023):
            selected = select_strikes(rows, spot)
            strikes = {r['strike_price'] for r in selected}
            self.assertEqual(len(selected), 96)
            self.assertEqual(len([s for s in strikes if s < spot]), 24)
            self.assertEqual(len([s for s in strikes if s > spot]), 24)
