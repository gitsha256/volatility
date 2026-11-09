"""Upstox V3 full-feed recorder with per-strike channels and second snapshots."""
import argparse
import asyncio
import json
import logging
import math
import multiprocessing
import os
import time
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import upstox_client

from volatility_scanner.security.tokens import AuthError
from volatility_scanner.storage.journal import Recorder, INDEX, IST
from volatility_scanner.brokers.upstox.auth import ensure_upstox_token
from volatility_scanner.brokers.upstox.streams.market import VerifiedStreamer
from volatility_scanner.brokers.upstox.streams.option_chain import OptionChainFeed
from volatility_scanner.storage.excel import export_directory
from volatility_scanner.storage.lifecycle import capture_guard
from volatility_scanner.brokers.upstox.api.market_rest import MarketREST
from volatility_scanner.brokers.upstox.market.coverage import discover, coverage_plan, sync_subscriptions, supplements, expiry_epoch, local_analytics
from volatility_scanner.brokers.upstox.market.pcr_time import stream_analytics

UPSTOX_INDEX = 'NSE_INDEX|Nifty 50'
log = logging.getLogger(__name__)


def flatten(value, prefix=''):
    result = {}
    for key, item in value.items():
        name = prefix + key
        if isinstance(item, dict):
            result.update(flatten(item, name + '.'))
        else:
            result[name] = item
    return result


class UpstoxRecorder(Recorder):
    def __init__(self, directory):
        super().__init__(directory)
        self.latest = {}
        self.received = {}
        self.context = {}
        self.contracts = {}
        self.modes = {}
        self.record_ticks = True
        self.algorithm_feed = None

    def enqueue(self, bucket, source, payload):
        if source != 'websocket' or self.record_ticks:
            super().enqueue(bucket, source, payload)

    def configure(self, contracts, futures, plan):
        selected = {row['symbol'] for row in contracts}
        for key in set(self.symbols) - selected:
            self.symbols.pop(key, None)
            self.latest.pop(key, None)
            self.received.pop(key, None)
        self.contracts = {row['symbol']: row for row in contracts}
        if self.algorithm_feed is not None:
            self.algorithm_feed.configure(self.contracts)
        for row in contracts:
            self.register(row, expiry_epoch(row['expiry_date']))
        self.context = ({'NSE_INDEX|India VIX': 'INDIA_VIX'}
                        if any('NSE_INDEX|India VIX' in keys for keys in plan.values()) else {})
        self.context.update({r['instrument_key']: 'FUTURE_' + r['instrument_key'].split('|')[-1] for r in futures})
        self.modes = {key: mode for mode, keys in plan.items() for key in keys}
        for row in futures:
            self.enqueue(self.context[row['instrument_key']], 'contract', row)

    def dispatch(self, message):
        if message.get('marketInfo'):
            self.enqueue('MARKET_STATUS', 'websocket', dict(message['marketInfo'],
                socket_received_at_ist=message.get('_received_at_ist'), feed_timestamp=message.get('currentTs')))
        for key, feed in message.get('feeds', {}).items():
            details = feed.get('fullFeed', {})
            data = (details.get('marketFF') or details.get('indexFF') or feed.get('firstLevelWithGreeks')
                    or ({'ltpc': feed['ltpc']} if feed.get('ltpc') else None))
            if not data:
                continue
            fields = flatten(data)
            fields.update(instrument_key=key, feed_timestamp=message.get('currentTs'))
            fields['subscription_mode'] = self.modes.get(key, 'full')
            fields['socket_received_at_ist'] = message.get('_received_at_ist')
            if message.get('_receive_perf_ns'):
                fields['receive_to_dispatch_ms'] = (time.perf_counter_ns() - message['_receive_perf_ns']) / 1e6
            if key == UPSTOX_INDEX:
                fields['symbol'] = INDEX
                if 'ltpc.ltp' in fields:
                    fields['ltp'] = fields['ltpc.ltp']
                super().dispatch(fields)
                self.enqueue('NIFTY50', 'websocket', fields)
            elif key in self.symbols:
                fields['symbol'] = key
                self.latest[key] = fields
                self.received[key] = time.time()
                super().dispatch(fields)
            elif key in self.context:
                fields['symbol'] = key
                self.latest[key] = fields
                self.received[key] = time.time()
                self.enqueue(self.context[key], 'websocket', fields)

    async def sample_options(self):
        deadline = time.monotonic()
        while True:
            for key, (bucket, leg) in list(self.symbols.items()):
                received = self.received.get(key)
                self.enqueue(bucket, 'one_second', dict(
                    self.latest.get(key, {}), symbol=key, option_type=leg,
                    connected=self.connected, last_tick_received_epoch=received,
                    stale=not self.connected or received is None or time.time() - received > 15,
                ))
            for key, bucket in self.context.items():
                received = self.received.get(key)
                self.enqueue(bucket, 'one_second', dict(self.latest.get(key, {}),
                    symbol=key, connected=self.connected, last_tick_received_epoch=received,
                    stale=not self.connected or received is None or time.time() - received > 15))
            deadline += 1
            await asyncio.sleep(max(0, deadline - time.monotonic()))


async def run(args):
    token = await asyncio.to_thread(ensure_upstox_token)
    recorder = UpstoxRecorder(args.output)
    algorithm_only = getattr(args, 'algorithm_stream', False)
    recorder.record_ticks = not algorithm_only and not getattr(args, 'no_ticks', False)
    rest = MarketREST(token)
    streamer = None
    samplers = []
    export_task = None
    pool = ProcessPoolExecutor(max_workers=1, mp_context=multiprocessing.get_context('spawn'))
    stop_file = Path(args.output) / 'stop.request'
    health_at = time.monotonic()
    export_result = None
    try:
        contracts, futures, spot = await asyncio.to_thread(discover, rest, getattr(args, 'expiry_scope', 'front_two'),
                                                         getattr(args, 'context', False))
        if not getattr(args, 'context', False):
            futures = []
        def make_plan():
            return coverage_plan(contracts, futures, spot, getattr(args, 'depth_contracts', 0),
                                 getattr(args, 'context', False), getattr(args, 'feed_mode', 'full'))
        plan = make_plan()
        recorder.configure(contracts, futures, plan)
        def save_coverage():
            payload = dict(updated_at=datetime.now(IST).isoformat(),
                           counts={mode: len(keys) for mode, keys in plan.items()},
                           contracts=contracts, futures=futures,
                           modes={mode: sorted(keys) for mode, keys in plan.items()})
            temporary = Path(args.output) / 'coverage.tmp.json'
            temporary.write_text(json.dumps(payload))
            os.replace(temporary, Path(args.output) / 'coverage.json')
        save_coverage()
        config = upstox_client.Configuration()
        config.access_token = token
        # The pinned feed implementation only reads configuration; avoid its unused REST thread pool.
        if algorithm_only:
            recorder.algorithm_feed = OptionChainFeed(recorder)
            streamer = VerifiedStreamer(SimpleNamespace(configuration=config),
                protobuf_callback=recorder.algorithm_feed.submit, max_decode_queue=64,
                max_frame_bytes=8 * 1024 * 1024)
        else:
            streamer = VerifiedStreamer(SimpleNamespace(configuration=config))
        if not algorithm_only and not getattr(args, 'no_raw_frames', False):
            streamer.raw_callback = recorder.record_frame
        streamer.auto_reconnect(True, 5, 50)

        def opened():
            sync_subscriptions(streamer, plan)
            if recorder.algorithm_feed is not None:
                recorder.loop.call_soon_threadsafe(recorder.algorithm_feed.connection, True, time.time())
            else:
                recorder.loop.call_soon_threadsafe(setattr, recorder, 'connected', True)
            log.info('Upstox connected; mode counts %s', {mode: len(keys) for mode, keys in plan.items()})

        def disconnected(*_):
            if recorder.algorithm_feed is not None:
                recorder.loop.call_soon_threadsafe(recorder.algorithm_feed.connection, False, time.time())
            else:
                recorder.loop.call_soon_threadsafe(setattr, recorder, 'connected', False)
            log.warning('Upstox stream unavailable; samples marked stale')

        def message(data):
            # SDK creates a new decoded dictionary per callback and never reuses it.
            if recorder.accepting and isinstance(data, dict):
                recorder.loop.call_soon_threadsafe(recorder.dispatch, data)

        streamer.on('open', opened)
        streamer.on('message', message)
        for event in ('close', 'error', 'reconnecting', 'autoReconnectStopped'):
            streamer.on(event, disconnected)
        await asyncio.to_thread(streamer.connect)
        if not algorithm_only and not getattr(args, 'no_snapshots', False):
            samplers.extend([asyncio.create_task(recorder.sample_index()),
                             asyncio.create_task(recorder.sample_options())])
        if getattr(args, 'analytics', ''):
            enabled = set(args.analytics.split(','))
            if 'local' in enabled:
                samplers.append(asyncio.create_task(local_analytics(recorder)))
            if enabled & {'pcr-time', 'exposure'}:
                samplers.append(asyncio.create_task(stream_analytics(recorder,
                    pcr='pcr-time' in enabled, exposure='exposure' in enabled,
                    iv_unit=getattr(args, 'exposure_iv_unit', 'percent'),
                    oi_multiplier=getattr(args, 'exposure_oi_multiplier', 1.0))))
            if enabled - {'local', 'pcr-time', 'exposure'}:
                samplers.append(asyncio.create_task(supplements(rest, recorder, enabled)))
        refresh_at = time.monotonic() + 300
        token_check_at = time.monotonic() + 30
        export_at = time.monotonic() + args.export_interval
        while True:
            await asyncio.sleep(0.2)
            if stop_file.exists():
                stop_file.unlink()
                log.info('Graceful stop requested; draining capture')
                break
            if recorder.failure:
                raise RuntimeError('Capture journal failed') from recorder.failure
            for task in samplers:
                if task.cancelled():
                    raise RuntimeError('Capture worker was unexpectedly cancelled')
                if task.done():
                    task.result()
                    raise RuntimeError('Capture worker exited unexpectedly')
            if streamer.decoder_failure:
                raise RuntimeError('Protobuf decoding failed' +
                    ('; raw frames retained' if not algorithm_only and not getattr(args, 'no_raw_frames', False) else '')) from streamer.decoder_failure
            if recorder.algorithm_feed is not None and recorder.algorithm_feed.failure:
                raise RuntimeError('Algorithm stream cache failed') from recorder.algorithm_feed.failure
            if time.monotonic() >= token_check_at:
                try:
                    renewed = await asyncio.to_thread(ensure_upstox_token)
                    if renewed != token:
                        recorder.connected = False
                        await asyncio.to_thread(streamer.auto_reconnect, False)
                        await asyncio.to_thread(streamer.disconnect)
                        await asyncio.to_thread(rest.set_token, renewed)
                        config.access_token = renewed
                        token = renewed
                        await asyncio.to_thread(streamer.auto_reconnect, True, 5, 50)
                        await asyncio.to_thread(streamer.connect)
                        log.info('Access token renewed; reconnecting with existing subscriptions')
                except AuthError as error:
                    log.warning('Token renewal failed; retrying in 30 seconds: %s', error)
                token_check_at = time.monotonic() + 30
            if time.monotonic() >= health_at:
                if export_task is not None and export_task.done():
                    export_result = export_task.result()
                status = dict(recorder.health(), decoder_queue=streamer.decode_queue.qsize(),
                              frames_received=streamer.frames_received, bytes_received=streamer.bytes_received,
                              decode_ms=streamer.decode_ms, export=export_result,
                              updated_at=datetime.now(IST).isoformat())
                status['rest'] = dict(rest.status)
                status['coverage'] = {mode: len(keys) for mode, keys in plan.items()}
                if recorder.algorithm_feed is not None:
                    status['algorithm_stream'] = recorder.algorithm_feed.health()
                temporary = Path(args.output) / 'health.tmp.json'
                temporary.write_text(json.dumps(status))
                os.replace(temporary, Path(args.output) / 'health.json')
                if status['free_bytes'] < args.min_free_gb * 1024**3:
                    raise RuntimeError('Free disk space below reserve; stopping before disk fills')
                if status['oldest_queued_ms'] > 1000 or status['decoder_queue'] > 10000:
                    log.warning('Capture backlog: %s', status)
                health_at = time.monotonic() + 5
            if time.monotonic() >= refresh_at:
                try:
                    contracts, futures, spot = await asyncio.to_thread(discover, rest, getattr(args, 'expiry_scope', 'front_two'),
                                                                     getattr(args, 'context', False))
                    if not getattr(args, 'context', False):
                        futures = []
                    plan = make_plan()
                    recorder.configure(contracts, futures, plan)
                    await asyncio.to_thread(sync_subscriptions, streamer, plan)
                    save_coverage()
                except AuthError as error:
                    log.warning('%s; existing stream continues', error)
                refresh_at = time.monotonic() + 300
            if time.monotonic() >= export_at:
                if export_task is None or export_task.done():
                    if export_task is not None:
                        export_result = await export_task
                    export_task = recorder.loop.run_in_executor(pool, export_directory, args.output)
                    export_at = time.monotonic() + args.export_interval
    finally:
        if streamer:
            for operation, label in ((lambda: streamer.auto_reconnect(False), 'reconnect'),
                                     (streamer.disconnect, 'socket')):
                try:
                    await asyncio.to_thread(operation)
                except Exception:
                    log.warning('Could not close %s cleanly; continuing journal cleanup', label)
            streamer.accepting_frames = False
            await asyncio.to_thread(streamer.stop_decoder)
        if recorder.algorithm_feed is not None:
            recorder.algorithm_feed.close()
        await asyncio.sleep(0)
        recorder.accepting = False
        for task in samplers:
            task.cancel()
        await asyncio.gather(*samplers, return_exceptions=True)
        try:
            await recorder.drain()
            if export_task:
                await export_task
            if recorder.health()['free_bytes'] >= args.min_free_gb * 1024**3:
                await recorder.loop.run_in_executor(pool, export_directory, args.output)
            else:
                log.warning('Final Excel export skipped to preserve disk reserve; journal retained')
        finally:
            for task in recorder.tasks:
                task.cancel()
            await asyncio.gather(*recorder.tasks, return_exceptions=True)
            await asyncio.to_thread(recorder.stop_writer)
            recorder.db.close()
            await asyncio.to_thread(rest.close)
            pool.shutdown(wait=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', default='D:/market-data/volatility/upstox')
    parser.add_argument('--export-interval', type=float, default=300)
    parser.add_argument('--min-free-gb', type=float, default=1)
    parser.add_argument('--stop', action='store_true', help='Request a graceful stop of this output directory')
    parser.add_argument('--expiry-scope', choices=('front_two', 'current', 'all'), default='front_two')
    parser.add_argument('--feed-mode', choices=('full', 'option_greeks', 'ltpc'), default='full')
    parser.add_argument('--depth-contracts', type=int, choices=range(51), default=0, metavar='0..50')
    parser.add_argument('--context', action='store_true', help='Include India VIX and active NIFTY futures')
    parser.add_argument('--analytics', default='', help='Comma-separated: local,pcr-time,exposure,chain,oi,pcr,max-pain,fii,dii,calendar')
    parser.add_argument('--exposure-iv-unit', choices=('percent', 'decimal'), default='percent')
    parser.add_argument('--exposure-oi-multiplier', type=float, default=1.0,
                        help='Scale broker OI into underlying units; default uses OI as supplied')
    parser.add_argument('--no-snapshots', action='store_true')
    parser.add_argument('--no-raw-frames', action='store_true')
    parser.add_argument('--no-ticks', action='store_true', help='Keep selected snapshots/raw frames without decoded tick rows')
    parser.add_argument('--algorithm-stream', action='store_true',
                        help='Direct Protobuf cache: minute analytics only, no raw/tick/second recordings')
    args = parser.parse_args(argv)
    if args.algorithm_stream:
        args.analytics = args.analytics or 'pcr-time,exposure'
        if set(args.analytics.split(',')) - {'pcr-time', 'exposure'}:
            parser.error('Algorithm stream supports pcr-time and exposure only')
        if args.context or args.depth_contracts:
            parser.error('Algorithm stream requires only spot and option OI/IV; omit context/depth')
    if not math.isfinite(args.export_interval) or args.export_interval < 1:
        parser.error('Export interval must be >=1 second')
    if not math.isfinite(args.min_free_gb) or args.min_free_gb < 0:
        parser.error('Disk reserve cannot be negative')
    if args.feed_mode != 'full' and args.depth_contracts:
        parser.error('30-level depth requires --feed-mode full')
    if not math.isfinite(args.exposure_oi_multiplier) or args.exposure_oi_multiplier <= 0:
        parser.error('Exposure OI multiplier must be finite and positive')
    if set(filter(None, args.analytics.split(','))) - {'local', 'pcr-time', 'exposure', 'chain', 'oi', 'pcr', 'max-pain', 'fii', 'dii', 'calendar'}:
        parser.error('Unknown analytics source')
    if args.feed_mode == 'ltpc' and set(args.analytics.split(',')) & {'pcr-time', 'exposure'}:
        parser.error('PCR/time and exposure require full or option_greeks feed data')
    if args.stop:
        Path(args.output, 'stop.request').touch()
        raise SystemExit(0)
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    try:
        with capture_guard(args.output):
            asyncio.run(run(args))
    except AuthError as error:
        print(error)
        raise SystemExit(1)
    except KeyboardInterrupt:
        pass


if __name__ == '__main__':
    main()
