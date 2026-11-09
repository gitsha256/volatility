"""Synthetic Protobuf-to-algorithm-cache benchmark. No network or disk writes."""
import argparse
import asyncio
import json
import time
from types import SimpleNamespace

from google.protobuf.json_format import ParseDict
from upstox_client.feeder.proto.MarketDataFeedV3_pb2 import FeedResponse

from volatility_scanner.brokers.upstox.streams.market import VerifiedStreamer
from volatility_scanner.brokers.upstox.streams.option_chain import OptionChainFeed, INDEX


async def benchmark(count):
    contracts = {f'NSE_FO|{i}': {} for i in range(560)}
    state = SimpleNamespace(loop=asyncio.get_running_loop(), contracts=contracts,
                            latest={}, received={}, index={}, index_received=None, connected=True)
    feed = OptionChainFeed(state)
    streamer = VerifiedStreamer(protobuf_callback=feed.submit, max_decode_queue=64,
                                max_frame_bytes=8 * 1024 * 1024)
    frames = [ParseDict({'feeds': {key: {'firstLevelWithGreeks': {
        'oi': number, 'iv': 20, 'ltpc': {'ltp': 100}}}}}, FeedResponse()).SerializeToString()
        for number, key in enumerate(contracts)]
    spot = ParseDict({'feeds': {INDEX: {'ltpc': {'ltp': 25000}}}}, FeedResponse()).SerializeToString()
    started = time.perf_counter()
    try:
        streamer.handle_message(None, spot)
        for number in range(count):
            streamer.handle_message(None, frames[number % len(frames)])
            if number % 256 == 0:
                await asyncio.sleep(0)
        await asyncio.to_thread(streamer.stop_decoder)
        await asyncio.sleep(0)
        feed.close()
        elapsed = time.perf_counter() - started
        assert streamer.decoder_failure is None and feed.failure is None
        assert feed.updates == count + 1 and len(state.latest) == min(count, 560)
        assert state.index['ltp'] == 25000
        for key, value in state.latest.items():
            assert value['oi'] == int(key.split('|')[1])
        return dict(frames=count + 1, seconds=round(elapsed, 3),
                    frames_per_second=round((count + 1) / elapsed), cached_options=len(state.latest),
                    decoder_queue_capacity=64, **feed.health(),
                    scope='Synthetic decoder/cache throughput, coalesced state; excludes broker, disk, minute computation')
    finally:
        await asyncio.to_thread(streamer.stop_decoder)
        feed.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--frames', type=int, default=100000)
    args = parser.parse_args()
    if args.frames <= 0:
        parser.error('Frame count must be positive')
    print(json.dumps(asyncio.run(benchmark(args.frames)), indent=2))
