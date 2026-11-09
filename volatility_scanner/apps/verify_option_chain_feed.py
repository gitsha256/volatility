"""Bounded read-only live verification of the direct Protobuf algorithm input."""
import asyncio
import json
from pathlib import Path
import time
from types import SimpleNamespace

import upstox_client

from volatility_scanner.brokers.upstox.auth import ensure_upstox_token
from volatility_scanner.security.tokens import AuthError
from volatility_scanner.brokers.upstox.api.market_rest import MarketREST
from volatility_scanner.brokers.upstox.market.coverage import discover, coverage_plan, sync_subscriptions, expiry_epoch
from volatility_scanner.brokers.upstox.market.pcr_time import StreamingAnalytics
from volatility_scanner.brokers.upstox.streams.market import VerifiedStreamer
from volatility_scanner.brokers.upstox.streams.option_chain import OptionChainFeed
from volatility_scanner.analytics.pcr_time import finite


async def verify():
    token = await asyncio.to_thread(ensure_upstox_token)
    rest = MarketREST(token)
    streamer = feed = None
    report = dict(scope='Read-only initial snapshot smoke test, not a market-hours or minute-scheduler soak')
    try:
        contracts, futures, spot = await asyncio.to_thread(discover, rest, 'front_two', False)
        plan = coverage_plan(contracts, futures, spot, depth_contracts=0,
                             include_context=False, feed_mode='option_greeks')
        loop = asyncio.get_running_loop()
        state = SimpleNamespace(loop=loop, contracts={r['symbol']: r for r in contracts},
            latest={}, received={}, price_received={}, index={}, index_received=None, connected=False,
            symbols={r['symbol']: (f"{expiry_epoch(r['expiry_date'])}_{r['strike_price']:g}",r['option_type']) for r in contracts})
        feed = OptionChainFeed(state)
        config = upstox_client.Configuration()
        config.access_token = token
        streamer = VerifiedStreamer(SimpleNamespace(configuration=config), protobuf_callback=feed.submit,
                                    max_decode_queue=64, max_frame_bytes=8*1024*1024)
        opened = asyncio.Event()
        def on_open(timestamp):
            feed.connection(True, timestamp)
            opened.set()
        def socket_open():
            timestamp = time.time()
            sync_subscriptions(streamer, plan)
            loop.call_soon_threadsafe(on_open, timestamp)
        streamer.on('open', socket_open)
        for event in ('close','error','reconnecting','autoReconnectStopped'):
            streamer.on(event, lambda *_: loop.call_soon_threadsafe(feed.connection, False, time.time()))
        streamer.auto_reconnect(False)
        await asyncio.to_thread(streamer.connect)
        await asyncio.wait_for(opened.wait(), 20)
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if len(state.received) == len(contracts) and state.index_received is not None:
                break
            await asyncio.sleep(.1)
        algorithm=StreamingAnalytics(exposure=True)
        algorithm.consume(state,time.time())
        await asyncio.sleep(1)
        events=algorithm.consume(state,time.time())
        report.update(connected=state.connected, options_expected=len(contracts), options_received=len(state.received),
                      spot_received=state.index_received is not None, frames=streamer.frames_received,
                      options_with_positive_iv=sum(finite(v.get('iv')) and v['iv'] > 0 for v in state.latest.values()),
                      decoder_ok=streamer.decoder_failure is None, cache_ok=feed.failure is None,
                      complete_contract_models=sum(source=='premium_minute' and value['complete'] for _,source,value in events),
                      observed_premium_comparisons=sum(source=='premium_minute' and value.get('observed_change') is not None for _,source,value in events),
                      state_delivery=feed.health(), expiry_results=[dict(expiry=value['expiry'],valid=value['valid'],
                        reason=value['reason'],pcr=value['pcr']) for _,source,value in events
                        if source=='pcr_time_minute' and value['scope']=='selected_expiry_contracts'],
                      exposure_results=[dict(expiry=value['expiry'],complete=value['complete'],reason=value['reason'])
                        for bucket,source,value in events if source=='exposure_minute' and bucket.endswith('_EXPOSURE')])
    except Exception as error:
        report['failure_type'] = type(error).__name__
        if isinstance(error, AuthError):
            report['failure'] = str(error)
    finally:
        if streamer:
            try:
                await asyncio.to_thread(streamer.disconnect)
            finally:
                streamer.accepting_frames = False
                await asyncio.to_thread(streamer.stop_decoder)
        if feed:
            feed.close()
        await asyncio.to_thread(rest.close)
    report['passed'] = (report.get('connected',False) and report.get('decoder_ok',False)
                        and report.get('cache_ok',False) and report.get('spot_received',False)
                        and report.get('options_received',0) == report.get('options_expected',-1))
    path = Path('D:/market-data/volatility/verification/algorithm-stream-live.json')
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps(report,indent=2))
    return report['passed']


if __name__ == '__main__':
    raise SystemExit(0 if asyncio.run(verify()) else 1)
