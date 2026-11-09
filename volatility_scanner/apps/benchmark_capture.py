"""Offline synthetic Protobuf -> disk benchmark. Never opens a market connection."""
import argparse
import asyncio
import json
import tempfile
import time
from pathlib import Path

from google.protobuf import json_format
from upstox_client.feeder.proto.MarketDataFeedV3_pb2 import FeedResponse

from volatility_scanner.apps.record_upstox import UpstoxRecorder
from volatility_scanner.brokers.upstox.streams.market import VerifiedStreamer


async def benchmark(count):
    with tempfile.TemporaryDirectory(prefix='capture-benchmark-') as directory:
        recorder = UpstoxRecorder(directory)
        streamer = VerifiedStreamer()
        streamer.raw_callback = recorder.record_frame
        streamer.on('message', lambda data: recorder.loop.call_soon_threadsafe(recorder.dispatch, data))
        messages = []
        for strike in range(147):
            key = f'NSE_FO|{strike}'
            recorder.register(dict(symbol=key, strike_price=23000 + strike * 50, option_type='CE'), 1800000000)
            frame = json_format.ParseDict({'feeds': {key: {'fullFeed': {'marketFF': {
                'ltpc': {'ltp': 100, 'ltt': '123456'}, 'oi': 1000, 'iv': 0.15,
                'optionGreeks': {'delta': 0.5, 'gamma': 0.001, 'theta': -1, 'vega': 10},
                'marketLevel': {'bidAskQuote': [{'bidP': 99, 'askP': 101, 'bidQ': '100', 'askQ': '100'}] * 5},
            }}}}}, FeedResponse())
            messages.append(frame.SerializeToString())
        await recorder.drain()
        initial_commits = recorder.health()['commits']
        started = time.perf_counter()
        try:
            for number in range(count):
                streamer.handle_message(None, messages[number % 147])
                if number % 250 == 0:
                    await asyncio.sleep(0)
            await asyncio.to_thread(streamer.stop_decoder)
            await asyncio.sleep(0)
            await recorder.drain()
            elapsed = time.perf_counter() - started
            ticks = recorder.db.execute("select count(*) from events where source='websocket'").fetchone()[0]
            frames = recorder.db.execute('select count(*) from raw_frames').fetchone()[0]
            assert ticks == frames == count, (ticks, frames, count)
            integrity = recorder.db.execute('pragma integrity_check').fetchone()[0]
            assert integrity == 'ok', integrity
            storage_bytes = sum(path.stat().st_size for path in Path(directory).glob('capture.sqlite3*'))
            return dict(frames=count, decoded_updates=ticks, seconds=round(elapsed, 3),
                        updates_per_second=round(count / elapsed),
                        commits=recorder.health()['commits'] - initial_commits,
                        queues_empty=not recorder.health()['writer_queue'],
                        storage_bytes=storage_bytes, sqlite_integrity=integrity,
                        note='Synthetic offline throughput; not broker latency or a sustained load guarantee')
        finally:
            await asyncio.to_thread(streamer.stop_decoder)
            await asyncio.sleep(0)
            await recorder.drain()
            for task in recorder.tasks:
                task.cancel()
            await asyncio.gather(*recorder.tasks, return_exceptions=True)
            await asyncio.to_thread(recorder.stop_writer)
            recorder.db.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--frames', type=int, default=10000)
    args = parser.parse_args()
    if args.frames < 1:
        parser.error('Frames must be positive')
    print(json.dumps(asyncio.run(benchmark(args.frames)), indent=2))
