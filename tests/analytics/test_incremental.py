import asyncio
import random
import unittest
from unittest.mock import patch
from types import SimpleNamespace

from volatility_scanner.analytics.pcr_time import PCRTimeScanner, YEAR_SECONDS
from volatility_scanner.brokers.upstox.market.pcr_time import StreamingAnalytics, stream_analytics
from volatility_scanner.brokers.upstox.market.coverage import expiry_epoch


class TestIncremental(unittest.TestCase):
    def state(self):
        rows=[dict(symbol=key,strike_price=25000,option_type=kind,expiry_date='2026-12-03')
              for key,kind in (('call','CE'),('put','PE'))]
        now=expiry_epoch('2026-12-03')-86400
        return SimpleNamespace(contracts={r['symbol']:r for r in rows},
            symbols={r['symbol']:('pair',r['option_type']) for r in rows},
            latest={'call':dict(oi=100,iv=20),'put':dict(oi=200,iv=20)},
            received={'call':now,'put':now},index={'ltp':25000},index_received=now,
            connected=True,analytics_dirty=set(),analytics_generation=0),now

    def aggregate(self, scanner, state, now):
        return next(e[2] for e in scanner.consume(state,now) if e[0].endswith('_PCR_TIME'))

    def test_changed_leg_delta_and_deadline_expiration_without_ticks(self):
        state,now=self.state();scanner=StreamingAnalytics()
        self.assertEqual(self.aggregate(scanner,state,now)['pcr'],2)
        state.latest['put']['oi']=400;state.analytics_dirty.add('put')
        with patch.object(scanner,'_refresh',wraps=scanner._refresh) as refresh:
            self.assertEqual(self.aggregate(scanner,state,now+1)['pcr'],4)
            self.assertEqual(refresh.call_count,1)
        state.index_received=now+16
        result=self.aggregate(scanner,state,now+16)
        self.assertFalse(result['valid'])
        self.assertEqual(result['call_oi'],0)
        self.assertEqual(result['put_oi'],0)
        snapshot=scanner.last_valid_ratios['2026-12-03_PCR_TIME']['data']
        self.assertEqual(snapshot['pcr'],4)
        self.assertEqual(snapshot['sample_epoch'],now+1)
        self.assertEqual(snapshot['input_receipt_epoch'],now)
        self.assertIsNone(result['pcr'])

    def test_unchanged_receipt_refresh_and_reconnection(self):
        state,now=self.state();scanner=StreamingAnalytics()
        self.aggregate(scanner,state,now)
        for second in range(1,40):
            state.received=dict.fromkeys(state.contracts,now+second)
            state.index_received=now+second
            self.assertEqual(self.aggregate(scanner,state,now+second)['pcr'],2)
        state.analytics_generation+=1
        result=self.aggregate(scanner,state,now+40)
        self.assertIsNone(result['pcr_d1_per_minute'])

    def test_incremental_matches_brute_force_totals(self):
        state,now=self.state();scanner=StreamingAnalytics();rng=random.Random(42)
        for second in range(400):
            key=rng.choice(list(state.contracts))
            state.latest[key]['oi']=rng.randrange(1,1000)
            state.received=dict.fromkeys(state.contracts,now+second)
            state.index_received=now+second
            state.analytics_dirty.add(key)
            result=self.aggregate(scanner,state,now+second)
            self.assertAlmostEqual(result['pcr'],state.latest['put']['oi']/state.latest['call']['oi'])
            self.assertEqual(result['call_oi'],state.latest['call']['oi'])
            self.assertLessEqual(len(scanner.expirations),8)

    def test_rolling_extrema_match_brute_force_and_evict_old_extremes(self):
        scanner=PCRTimeScanner();rng=random.Random(9);spots=[]
        for second in range(1000):
            spot=rng.randrange(24000,26000);spots.append(spot)
            result=scanner.update('pair',timestamp=second,expiry=YEAR_SECONDS,
                                  call_oi=100,put_oi=200,spot=spot)
            if second>=300:
                window=spots[second-300:second+1]
                self.assertAlmostEqual(result['spot_range_fraction'],(max(window)-min(window))/window[0])
        self.assertEqual(len(scanner.history['pair']),301)

    def test_skipped_seconds_and_clock_reversal_are_reported_and_reset(self):
        state,now=self.state();scanner=StreamingAnalytics()
        self.aggregate(scanner,state,now)
        self.aggregate(scanner,state,now+1)
        self.aggregate(scanner,state,now+5)
        self.assertEqual(scanner.health()['missed_samples'],3)
        reversed_result=self.aggregate(scanner,state,now+4)
        self.assertIsNone(reversed_result['pcr_d1_per_minute'])
        self.assertGreaterEqual(scanner.health()['calculation_ms'],0)


class TestSecondConsumer(unittest.IsolatedAsyncioTestCase):
    async def test_consume_each_second_publish_ready_batch_only_on_minute(self):
        state,base=TestIncremental().state()
        state.enqueue=lambda *event: published.append(event)
        clock=[base+59]
        published=[]
        async def advance(_):
            clock[0]+=1
            state.index_received=clock[0]
            state.received=dict.fromkeys(state.contracts,clock[0])
            if clock[0]>=base+62:
                raise asyncio.CancelledError
        with patch('volatility_scanner.brokers.upstox.market.pcr_time.time.time',side_effect=lambda:clock[0]), \
             patch('volatility_scanner.brokers.upstox.market.pcr_time.asyncio.sleep',side_effect=advance):
            with self.assertRaises(asyncio.CancelledError):
                await stream_analytics(state)
        self.assertEqual(state.algorithm_consumer.latest_epoch,base+61)
        self.assertEqual(len(published),2)
        self.assertTrue(all(payload['sample_epoch']==base+60 for _,_,payload in published))
