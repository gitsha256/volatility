"""Replay recorded Upstox Protobuf frames offline into a new journal."""
import argparse
import asyncio
import json
import sqlite3
from contextlib import closing
from pathlib import Path

from google.protobuf import json_format
from upstox_client.feeder.proto.MarketDataFeedV3_pb2 import FeedResponse

from volatility_scanner.apps.record_upstox import UpstoxRecorder
from volatility_scanner.storage.excel import export_directory


async def replay(source, output):
    source, output = Path(source).resolve(), Path(output).resolve()
    if (output / 'capture.sqlite3').exists():
        raise ValueError('Replay output must be a new journal; existing data preserved')
    recorder = UpstoxRecorder(output)
    recorder.context = {'NSE_INDEX|India VIX': 'INDIA_VIX'}
    count = 0
    try:
        with closing(sqlite3.connect(source.as_uri() + '?mode=ro', uri=True)) as conn:
            for bucket, raw in conn.execute("SELECT bucket,payload FROM events WHERE source IN ('chain','contract') ORDER BY id"):
                row = json.loads(raw)
                if row.get('symbol') and row.get('option_type') in ('CE', 'PE'):
                    recorder.symbols[row['symbol']] = (bucket, row['option_type'])
                    recorder.channel(bucket)
                elif row.get('instrument_key'):
                    recorder.context[row['instrument_key']] = bucket
            for received, raw in conn.execute('SELECT received_at,payload FROM raw_frames ORDER BY id'):
                message = json_format.MessageToDict(FeedResponse.FromString(raw),
                                                   always_print_fields_with_no_presence=True)
                message['_received_at_ist'] = received
                recorder.dispatch(message)
                count += 1
                if count % 250 == 0:
                    await asyncio.sleep(0)
        await recorder.drain()
        return count
    finally:
        try:
            await recorder.drain()
        finally:
            for task in recorder.tasks:
                task.cancel()
            await asyncio.gather(*recorder.tasks, return_exceptions=True)
            await asyncio.to_thread(recorder.stop_writer)
            recorder.db.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', required=True, help='Source capture.sqlite3')
    parser.add_argument('--output', required=True, help='New output directory')
    args = parser.parse_args()
    print('Replayed frames:', asyncio.run(replay(args.source, args.output)))
    print('Excel export:', export_directory(args.output))
