import asyncio
import json
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import Mock

from openpyxl import load_workbook

from record_upstox import UpstoxRecorder
from volatility_scanner.recorder import IST
from volatility_scanner.upstox_coverage import discover, coverage_plan, sync_subscriptions, local_summary, supplements
from volatility_scanner.exporter import export_directory


def contracts(count=60):
    today = datetime.now(IST).date()
    return [dict(symbol=f'NSE_FO|{i}', instrument_key=f'NSE_FO|{i}',
                 strike_price=24000 + (i // 2) * 50, option_type='CE' if i % 2 == 0 else 'PE',
                 expiry_date=today.isoformat()) for i in range(count)]


class TestCoverage(unittest.TestCase):
    def test_discovers_only_two_expiries_without_optional_context(self):
        rows = contracts(6)
        today = datetime.now(IST).date()
        for i, row in enumerate(rows):
            row['expiry'] = (today + timedelta(days=(i // 2) * 7)).isoformat()
            row['instrument_type'] = row['option_type']
        rest = Mock()
        rest.get.side_effect = [dict(data=rows), dict(data=[{'underlying_spot_price': 25000}])]
        selected, futures, spot = discover(rest, include_context=False)
        self.assertEqual(len(selected), 4)
        self.assertEqual(futures, [])
        self.assertEqual(spot, 25000)
        self.assertEqual(rest.get.call_count, 2)

    def test_depth_and_context_are_selectable_and_within_limits(self):
        rows = contracts(100)
        plan = coverage_plan(rows, [], 25000, depth_contracts=50)
        self.assertEqual(len(plan['full_d30']), 50)
        self.assertEqual(len(plan['full']), 52)
        self.assertFalse(plan['full_d30'] & plan['full'])
        self.assertIn('NSE_INDEX|India VIX', plan['full'])
        minimal = coverage_plan(rows, [], 25000, 0, False)
        self.assertFalse(minimal['full_d30'])
        self.assertEqual(len(minimal['full']), 101)
        self.assertNotIn('NSE_INDEX|India VIX', minimal['full'])

    def test_reconnect_does_not_duplicate_subscriptions(self):
        plan = coverage_plan(contracts(), [], 25000)
        streamer = Mock(subscriptions={k: set(v) for k, v in plan.items()})
        sync_subscriptions(streamer, plan)
        streamer.subscribe.assert_not_called()
        streamer.unsubscribe.assert_not_called()

    def test_local_pcr_max_pain_requires_complete_oi(self):
        rows = contracts(4)
        latest = {r['symbol']: {'oi': oi} for r, oi in zip(rows, [10, 20, 30, 40])}
        result = local_summary(rows, latest)
        self.assertEqual(result['pcr'], 1.5)
        self.assertEqual(result['max_pain'], 24050)
        latest.pop(rows[0]['symbol'])
        self.assertFalse(local_summary(rows, latest)['complete'])
        self.assertIsNone(local_summary(rows, latest)['max_pain'])


class TestOptionalCapture(unittest.IsolatedAsyncioTestCase):
    async def test_ltp_greeks_context_and_zero_fields(self):
        with tempfile.TemporaryDirectory() as directory:
            recorder = UpstoxRecorder(directory)
            rows = contracts(2)
            plan = coverage_plan(rows, [], 25000, depth_contracts=0)
            recorder.configure(rows, [], plan)
            recorder.dispatch({'feeds': {
                rows[0]['symbol']: {'ltpc': {'ltp': 99}},
                rows[1]['symbol']: {'firstLevelWithGreeks': {'oi': 0, 'iv': 0, 'optionGreeks': {'delta': 0}}},
                'NSE_INDEX|India VIX': {'fullFeed': {'indexFF': {'ltpc': {'ltp': 15}}}},
            }})
            await recorder.drain()
            self.assertEqual(recorder.latest[rows[0]['symbol']]['ltpc.ltp'], 99)
            self.assertEqual(recorder.latest[rows[1]['symbol']]['oi'], 0)
            self.assertEqual(recorder.latest['NSE_INDEX|India VIX']['ltpc.ltp'], 15)
            recorder.record_ticks = False
            before = recorder.db.execute('select count(*) from events').fetchone()[0]
            recorder.dispatch({'feeds': {rows[0]['symbol']: {'ltpc': {'ltp': 100}}}})
            await recorder.drain()
            self.assertEqual(recorder.db.execute('select count(*) from events').fetchone()[0], before)
            for task in recorder.tasks: task.cancel()
            await asyncio.gather(*recorder.tasks, return_exceptions=True)
            recorder.stop_writer()
            recorder.db.close()

    async def test_large_analytics_payload_survives_excel_export(self):
        with tempfile.TemporaryDirectory() as directory:
            recorder = UpstoxRecorder(directory)
            value = 'a' * 70000
            recorder.enqueue('PCR', 'rest', {'data': value})
            await recorder.drain()
            export_directory(directory)
            path = next(Path(directory).rglob('*.xlsx'))
            book = load_workbook(path, read_only=True)
            rows = list(book.active.values)
            self.assertEqual(''.join(rows[1][3:]), value)
            self.assertEqual(rows[0][3:], ('data__part1', 'data__part2', 'data__part3'))
            book.close()
            for task in recorder.tasks: task.cancel()
            await asyncio.gather(*recorder.tasks, return_exceptions=True)
            recorder.stop_writer()
            recorder.db.close()

    async def test_only_selected_rest_analytics_is_requested(self):
        recorder = Mock(contracts={'x': {'expiry_date': '2026-11-09'}})
        rest = Mock()
        rest.get.return_value = {'status': 'success', 'data': {'pcr': 1.2}}
        task = asyncio.create_task(supplements(rest, recorder, {'pcr'}))
        for _ in range(100):
            if recorder.enqueue.called: break
            await asyncio.sleep(0.01)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        self.assertEqual(rest.get.call_count, 1)
        self.assertEqual(rest.get.call_args.args[0], 'v2/market/pcr')

class TestHistoricalCapture(unittest.IsolatedAsyncioTestCase):
    async def test_optional_history_is_paced_and_saves_candle_fields(self):
        from types import SimpleNamespace
        from unittest.mock import patch
        from capture_upstox_history import capture, windows
        from datetime import date
        self.assertEqual(list(windows(date(2026, 1, 1), date(2026, 2, 1))),
                         [('2026-01-01', '2026-01-28'), ('2026-01-29', '2026-02-01')])
        with tempfile.TemporaryDirectory() as directory:
            rest = Mock()
            rest.get.return_value = {'status': 'success', 'data': {'candles': [
                ['2026-11-09T09:15:00+05:30', 100, 110, 90, 105, 500, 1000]]}}
            with patch('capture_upstox_history.ensure_upstox_token', return_value='unused'), patch(
                'capture_upstox_history.MarketREST', return_value=rest):
                result = await capture(SimpleNamespace(output=directory, start='2026-11-09',
                    end='2026-11-09', instruments='NSE_FO|123', expired_expiry=None))
            self.assertEqual(result['files'], 1)
            self.assertIn('NSE_FO%7C123/minutes/1', rest.get.call_args.args[0])
            rest.close.assert_called_once()

class TestPlusDecoding(unittest.TestCase):
    def test_thirty_depth_levels_and_zero_values_are_preserved(self):
        from google.protobuf import json_format
        from upstox_client.feeder.proto.MarketDataFeedV3_pb2 import FeedResponse
        from volatility_scanner.upstox_stream import VerifiedStreamer
        message = json_format.ParseDict({'feeds': {'NSE_FO|123': {'fullFeed': {'marketFF': {
            'oi': 0, 'iv': 0, 'optionGreeks': {'delta': 0},
            'marketLevel': {'bidAskQuote': [{'bidP': 100, 'askP': 101}] * 30},
        }}}}}, FeedResponse())
        streamer = VerifiedStreamer()
        messages = []
        streamer.on('message', messages.append)
        streamer.handle_message(None, message.SerializeToString())
        streamer.stop_decoder()
        data = messages[0]['feeds']['NSE_FO|123']['fullFeed']['marketFF']
        self.assertEqual(len(data['marketLevel']['bidAskQuote']), 30)
        self.assertEqual(data['oi'], 0)
        self.assertEqual(data['iv'], 0)
        self.assertEqual(data['optionGreeks']['delta'], 0)

    def test_rest_pacing_and_errors_do_not_expose_credentials(self):
        from unittest.mock import patch
        from volatility_scanner.upstox_rest import MarketREST
        from volatility_scanner.auth import AuthError
        session = Mock()
        session.get.return_value = Mock(status_code=403)
        with patch('volatility_scanner.upstox_rest.requests.Session', return_value=session):
            rest = MarketREST('private-token')
        rest.last_call = 100
        with patch('volatility_scanner.upstox_rest.time.monotonic', return_value=100.1), patch(
            'volatility_scanner.upstox_rest.time.sleep') as sleep:
            with self.assertRaises(AuthError) as error:
                rest.get('v2/market/oi')
        self.assertNotIn('private-token', str(error.exception))
        self.assertAlmostEqual(sleep.call_args.args[0], 0.2)
        self.assertEqual(rest.status['v2/market/oi']['http'], 403)
        rest.close()
