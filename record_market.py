"""Run independently from Streamlit: python record_market.py."""
import argparse
import asyncio
import logging
import tempfile
import time
from pathlib import Path

from fyers_apiv3.FyersWebsocket.data_ws import FyersDataSocket

from volatility_scanner.data import FyersDataProvider, FyersDataError
from volatility_scanner.recorder import Recorder, INDEX

log = logging.getLogger(__name__)


def select_strikes(rows, spot, per_side=24):
    options = [r for r in rows if r.get("option_type") in ("CE", "PE")]
    strikes = sorted({float(r["strike_price"]) for r in options})
    below = [s for s in strikes if s < spot][-per_side:]
    above = [s for s in strikes if s > spot][:per_side]
    if len(below) != per_side or len(above) != per_side:
        raise FyersDataError(f"Chain must contain {per_side} strikes on each side of NIFTY")
    selected = set(below + above)
    result = [r for r in options if float(r["strike_price"]) in selected]
    if any({r["option_type"] for r in result if float(r["strike_price"]) == s}
           != {"CE", "PE"} for s in selected):
        raise FyersDataError("Selected strikes are missing CE/PE legs")
    log.info("NIFTY %.2f; selected strikes %s", spot, sorted(selected))
    return result


def fetch_chains(provider):
    def fetch(expiry=""):
        for attempt in range(6):
            time.sleep(1 if attempt == 0 else min(60, 2 ** (attempt + 1)))
            try:
                payload = provider._fetch_json("/options-chain-v3", {
                    "symbol": INDEX, "strikecount": provider.strike_count,
                    "timestamp": expiry, "greeks": "1",
                }).get("data", {})
                break
            except FyersDataError as error:
                if "HTTP 429" not in str(error) or attempt == 5:
                    raise
                log.warning("FYERS rate limit; backing off before retry")
        if not payload.get("optionsChain") or not payload.get("expiryData"):
            raise FyersDataError("Incomplete option chain; capture cannot start/refresh")
        return payload
    first = fetch()
    expiry = str(first["expiryData"][0]["expiry"])
    spot = next((provider._number(row.get("ltp")) for row in first["optionsChain"]
                 if row.get("symbol") == INDEX), None)
    if spot is None or spot <= 0:
        raise FyersDataError("Option chain is missing the NIFTY spot price")
    return [(expiry, select_strikes(first["optionsChain"], spot))]


async def run(args):
    provider = FyersDataProvider(strike_count=args.strikes)
    recorder = Recorder(args.output)
    socket = None
    sampler = None
    socket_task = None
    try:
        chains = await asyncio.to_thread(fetch_chains, provider)
        for expiry, rows in chains:
            for row in rows:
                recorder.register(row, expiry)
        if not recorder.symbols:
            raise FyersDataError("No option symbols returned")
        if len(recorder.symbols) + 1 > 5000:
            raise FyersDataError("Chain exceeds 5000-symbol socket limit")

        def subscribe():
            socket.subscribe(symbols=[INDEX, *recorder.symbols], data_type="SymbolUpdate")
            recorder.connected = True

        def disconnected(message):
            recorder.connected = False
            detail = str(message).replace(provider.access_token, "[redacted]")
            log.warning("Stream disconnected/error; index samples marked stale: %s", detail)

        log_dir = Path(tempfile.gettempdir()) / "nifty-recorder-fyers"
        log_dir.mkdir(exist_ok=True)
        socket = FyersDataSocket(
            access_token=f"{provider.client_id}:{provider.access_token}",
            log_path=str(log_dir), write_to_file=False, litemode=False,
            reconnect=True, reconnect_retry=50, on_connect=subscribe,
            on_message=recorder.on_message, on_error=disconnected, on_close=disconnected,
        )
        socket_task = asyncio.create_task(asyncio.to_thread(socket.connect))
        sampler = asyncio.create_task(recorder.sample_index())
        log.info("Recording %s option symbols across %s dedicated strike channels",
                 len(recorder.symbols), len(recorder.channels))
        next_refresh = time.monotonic() + args.chain_interval
        next_export = time.monotonic() + args.export_interval
        while True:
            await asyncio.sleep(0.2)
            if recorder.failure:
                raise RuntimeError("Capture journal failed") from recorder.failure
            if socket_task.done() and socket_task.exception():
                raise socket_task.exception()
            if time.monotonic() >= next_refresh:
                try:
                    chains = await asyncio.to_thread(fetch_chains, provider)
                    previous = set(recorder.symbols)
                    selected = {row["symbol"] for _, rows in chains for row in rows}
                    removed = previous - selected
                    if removed:
                        await asyncio.to_thread(socket.unsubscribe, symbols=sorted(removed),
                                                data_type="SymbolUpdate")
                        for symbol in removed:
                            recorder.symbols.pop(symbol, None)
                    for expiry, rows in chains:
                        for row in rows:
                            recorder.register(row, expiry)
                    added = set(recorder.symbols) - previous
                    if len(recorder.symbols) + 1 > 5000:
                        raise RuntimeError("Refreshed chain exceeds socket symbol limit")
                    if added:
                        await asyncio.to_thread(socket.subscribe, symbols=sorted(added),
                                                data_type="SymbolUpdate")
                except FyersDataError:
                    log.exception("Chain refresh failed; existing WebSocket capture continues")
                next_refresh = time.monotonic() + args.chain_interval
            if time.monotonic() >= next_export:
                await asyncio.to_thread(recorder.export)
                next_export = time.monotonic() + args.export_interval
    finally:
        recorder.accepting = False
        if socket:
            await asyncio.to_thread(socket.close_connection)
        await asyncio.sleep(0)  # Process already scheduled callback deliveries.
        if sampler:
            sampler.cancel()
            await asyncio.gather(sampler, return_exceptions=True)
        try:
            await recorder.drain()
            await asyncio.to_thread(recorder.export)
        finally:
            for task in recorder.tasks:
                task.cancel()
            await asyncio.gather(*recorder.tasks, return_exceptions=True)
            await asyncio.to_thread(recorder.stop_writer)
            recorder.db.close()
            if provider._http:
                provider._http.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default="recordings")
    parser.add_argument("--strikes", type=int, choices=range(1, 51), default=50,
                        metavar="1..50", help="FYERS strikecount per expiry")
    parser.add_argument("--chain-interval", type=float, default=30,
                        help="REST refresh seconds for OI/IV and strike discovery")
    parser.add_argument("--export-interval", type=float, default=60)
    args = parser.parse_args()
    if args.chain_interval < 10 or args.export_interval < 1:
        parser.error("chain interval must be >=10s; export interval must be >=1s")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        asyncio.run(run(args))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
