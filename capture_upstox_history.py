"""Optional historical OHLCV/OI capture; runs only when explicitly invoked."""
import argparse
import asyncio
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import quote

from volatility_scanner.auth import AuthError
from volatility_scanner.capture_lifecycle import capture_guard
from volatility_scanner.exporter import export_directory
from volatility_scanner.recorder import Recorder
from volatility_scanner.upstox_auth import ensure_upstox_token
from volatility_scanner.upstox_rest import MarketREST
from volatility_scanner.upstox_coverage import INDEX


def windows(start, end):
    while start <= end:
        finish = min(end, start + timedelta(days=27))
        yield start.isoformat(), finish.isoformat()
        start = finish + timedelta(days=1)


async def capture(args):
    rest = MarketREST(await asyncio.to_thread(ensure_upstox_token))
    recorder = Recorder(args.output)
    try:
        keys = list(filter(None, args.instruments.split(',')))
        if args.expired_expiry:
            contracts = await asyncio.to_thread(rest.get, 'v2/expired-instruments/option/contract',
                                               {'instrument_key': INDEX, 'expiry_date': args.expired_expiry})
            keys.extend(r['instrument_key'] for r in contracts['data'])
        if not keys:
            raise AuthError('Specify --instruments or --expired-expiry')
        for key in dict.fromkeys(keys):
            for start, end in windows(date.fromisoformat(args.start), date.fromisoformat(args.end)):
                encoded = quote(key, safe='')
                if args.expired_expiry:
                    path = f'v2/expired-instruments/historical-candle/{encoded}/1minute/{end}/{start}'
                else:
                    path = f'v3/historical-candle/{encoded}/minutes/1/{end}/{start}'
                result = await asyncio.to_thread(rest.get, path)
                bucket = 'HISTORY_' + ''.join(c if c.isalnum() else '_' for c in key)
                for candle in result.get('data', {}).get('candles', []):
                    recorder.enqueue(bucket, 'historical', dict(instrument_key=key,
                        candle_timestamp=candle[0], open=candle[1], high=candle[2], low=candle[3],
                        close=candle[4], volume=candle[5], oi=candle[6] if len(candle) > 6 else None))
                await recorder.drain()
    finally:
        try:
            await recorder.drain()
        finally:
            for task in recorder.tasks:
                task.cancel()
            await asyncio.gather(*recorder.tasks, return_exceptions=True)
            await asyncio.to_thread(recorder.stop_writer)
            recorder.db.close()
            await asyncio.to_thread(rest.close)
    return await asyncio.to_thread(export_directory, args.output)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--from', dest='start', required=True)
    parser.add_argument('--to', dest='end', required=True)
    parser.add_argument('--instruments', default='', help='Comma-separated Upstox instrument keys')
    parser.add_argument('--expired-expiry', help='YYYY-MM-DD; fetch that expired NIFTY option chain')
    parser.add_argument('--output', default='D:/market-data/volatility/history')
    args = parser.parse_args()
    if bool(args.instruments) == bool(args.expired_expiry):
        parser.error('Choose exactly one of --instruments or --expired-expiry')
    try:
        if date.fromisoformat(args.start) > date.fromisoformat(args.end):
            parser.error('--from must be on or before --to')
        if args.expired_expiry:
            date.fromisoformat(args.expired_expiry)
    except ValueError:
        parser.error('Dates must use YYYY-MM-DD')
    try:
        with capture_guard(Path(args.output)):
            print(asyncio.run(capture(args)))
    except AuthError as error:
        print(error)
        raise SystemExit(1)
