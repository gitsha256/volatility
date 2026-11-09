import asyncio
from datetime import datetime
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest

from aiohttp import WSServerHandshakeError
from aiohttp.test_utils import TestClient, TestServer
from google.protobuf.json_format import ParseDict
from upstox_client.feeder.proto.MarketDataFeedV3_pb2 import FeedResponse

from volatility_scanner.apps.record_upstox import UpstoxRecorder
from volatility_scanner.dashboard.candles import CandleBook
from volatility_scanner.dashboard.server import DashboardServer
from volatility_scanner.dashboard.store import DashboardStore
from volatility_scanner.brokers.upstox.market.pcr_time import StreamingAnalytics
from volatility_scanner.brokers.upstox.streams.option_chain import OptionChainFeed, INDEX
from volatility_scanner.storage.journal import IST


class TestCandles(unittest.TestCase):
    def test_ohlc_seed_merge_and_session_gaps(self):
        base=datetime(2026,12,3,10,0,tzinfo=IST).timestamp()
        book=CandleBook(capacity=2)
        for offset,price in enumerate((100,105,95,101)):
            book.tick(price,base+offset)
        bar=book.snapshot()[0]
        self.assertEqual([bar[k] for k in ('open','high','low','close')],[100,105,95,101])
        book.seed([dict(time=int(base),open=99,high=104,low=94,close=100)])
        bar=book.snapshot()[0]
        self.assertEqual([bar[k] for k in ('open','high','low','close')],[99,105,94,101])
        book.tick(101,base+60);book.tick(102,base+120)
        self.assertEqual(len(book.snapshot()),2)
        book.set_market_status({'NSE_EQ':'NORMAL_CLOSE'})
        book.tick(999,base+121)
        self.assertEqual(book.snapshot()[-1]['close'],102)
        self.assertEqual(len(book.drain()),2)
        self.assertFalse(book.drain())


class TestAlgorithmDashboard(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.recorder=UpstoxRecorder(self.temp.name)
        self.rows=[dict(symbol=f'NSE_FO|{kind}',strike_price=25000,option_type=kind,expiry_date='2026-12-03')
                   for kind in ('CE','PE')]
        self.recorder.configure(self.rows,[],{'full':{r['symbol'] for r in self.rows}})
        self.server=DashboardServer(self.temp.name,recorder=self.recorder)
        self.client=TestClient(TestServer(self.server.app))
        await self.client.start_server()

    async def asyncTearDown(self):
        await self.client.close()
        await self.server.close()
        await self.recorder.drain()
        for task in self.recorder.tasks:task.cancel()
        await asyncio.gather(*self.recorder.tasks,return_exceptions=True)
        await asyncio.to_thread(self.recorder.stop_writer)
        self.recorder.db.close()
        self.temp.cleanup()

    async def test_page_assets_and_local_origin_restrictions(self):
        response=await self.client.get('/')
        self.assertEqual(response.status,200)
        self.assertIn('NIFTY / FLOW',await response.text())
        self.assertIn("default-src 'self'",response.headers['Content-Security-Policy'])
        response=await self.client.get('/static/dashboard.js')
        self.assertEqual(response.status,200)
        response=await self.client.get('/static/studies.js')
        self.assertEqual(response.status,200)
        response=await self.client.get('/',headers={'Host':'attacker.example'})
        self.assertEqual(response.status,403)
        with self.assertRaises(WSServerHandshakeError):
            await self.client.ws_connect('/ws',headers={'Origin':'https://attacker.example'})

    async def test_websocket_history_selection_and_no_credentials(self):
        self.recorder.enqueue('2026-12-03_PCR_TIME','pcr_time_minute',dict(pcr=1.2,sample_epoch=1800000000,scope='selected_expiry_contracts'))
        await self.recorder.drain()
        ws=await self.client.ws_connect('/ws')
        await ws.send_json(dict(expiry='2026-12-03',strike=25000,points=120))
        value=await ws.receive_json(timeout=3)
        self.assertEqual(value['type'],'snapshot')
        self.assertEqual(value['history'][0]['data']['pcr'],1.2)
        serialized=json.dumps(value).lower()
        self.assertNotIn('access_token',serialized)
        self.assertNotIn('api_secret',serialized)
        await ws.send_json(dict(expiry='bad',points=100000))
        self.assertEqual((await ws.receive_json(timeout=3))['type'],'error')
        await ws.close()

    async def test_coalescing_preserves_every_nifty_high_low_and_storage(self):
        base=datetime(2026,12,3,10,tzinfo=IST).timestamp()
        feed=OptionChainFeed(self.recorder)
        feed.connection(True,base-1)
        for offset,price in enumerate((100,200,90,110)):
            frame=ParseDict({'feeds':{INDEX:{'ltpc':{'ltp':price}}}},FeedResponse())
            feed.submit(frame,datetime.fromtimestamp(base+offset,IST).isoformat(),0)
        await asyncio.sleep(0)
        self.assertEqual(self.recorder.index['ltp'],110)
        bars=self.recorder.dashboard_book.drain()
        self.assertEqual(bars[0]['high'],200)
        self.assertEqual(bars[0]['low'],90)
        self.recorder.record_candles(bars)
        await self.recorder.drain()
        restored=DashboardStore(self.temp.name).candles()
        self.assertEqual(restored[0]['close'],110)

    async def test_health_is_read_only_and_does_not_start_feed(self):
        response=await self.client.get('/health')
        data=await response.json()
        self.assertFalse(data['connected'])
        self.assertIsNone(data['latest_epoch'])
        self.assertEqual(data['mode'],'live')

    async def test_last_valid_ratio_snapshot_does_not_restore_live_validity(self):
        base=datetime(2026,12,2,10,tzinfo=IST).timestamp()
        self.recorder.connected=True
        self.recorder.index={'ltp':25000}
        self.recorder.index_received=base
        for row in self.rows:
            self.recorder.latest[row['symbol']]={'oi':100 if row['option_type']=='CE' else 200,'iv':20}
            self.recorder.received[row['symbol']]=base
        consumer=StreamingAnalytics()
        self.recorder.algorithm_consumer=consumer
        consumer.consume(self.recorder,base)
        self.recorder.index_received=base+16
        consumer.consume(self.recorder,base+16)
        ws=await self.client.ws_connect('/ws')
        await ws.send_json(dict(expiry='2026-12-03',points=120))
        value=await ws.receive_json(timeout=3)
        current=next(e['data'] for e in value['current'] if e['source']=='pcr_time_minute')
        snapshot=value['ratio_snapshots'][0]
        self.assertFalse(current['valid'])
        self.assertIsNone(current['pcr'])
        self.assertEqual(snapshot['data']['pcr'],2)
        self.assertTrue(snapshot['snapshot'])
        self.assertNotIn('positioning_derivative_alert',snapshot['data'])
        await ws.close()
