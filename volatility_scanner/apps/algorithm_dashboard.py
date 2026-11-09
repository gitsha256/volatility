"""Independent local algorithm dashboard. Read-only journal view or isolated demo."""
import argparse
import asyncio
from pathlib import Path

from volatility_scanner.dashboard.server import DashboardServer


async def run(args):
    demo=None
    if args.demo:
        from volatility_scanner.dashboard.demo import DemoFeed
        demo=DemoFeed()
    server=DashboardServer(args.data,demo=demo)
    await server.start(args.port)
    print(f'Algorithm dashboard: http://127.0.0.1:{args.port}'+(' [SIMULATED DATA]' if demo else ' [READ-ONLY JOURNAL]'),flush=True)
    try:
        await asyncio.Event().wait()
    finally:
        await server.close()


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data',type=Path,default=Path('D:/market-data/volatility/algorithm-stream'))
    parser.add_argument('--port',type=int,default=8765)
    parser.add_argument('--demo',action='store_true',help='Isolated simulated data; no broker credentials or journal writes')
    args=parser.parse_args()
    if not 1<=args.port<=65535:
        parser.error('Port must be 1..65535')
    try:
        asyncio.run(run(args))
    except KeyboardInterrupt:
        pass
