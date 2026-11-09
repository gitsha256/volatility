"""Bounded read-only live reconnect/token-renewal verification; saves no credentials."""
import argparse
import json
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import upstox_client

from volatility_scanner.brokers.upstox.auth import ensure_upstox_token
from volatility_scanner.brokers.upstox.api.market_rest import MarketREST
from volatility_scanner.brokers.upstox.streams.market import VerifiedStreamer


def verify(output):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    config = upstox_client.Configuration()
    config.access_token = ensure_upstox_token()
    streamer = VerifiedStreamer(SimpleNamespace(configuration=config), ['NSE_INDEX|Nifty 50'], 'full')
    opened = threading.Event()
    snapshots = threading.Event()
    report = {'scope': 'Read-only, one index; closed-market snapshots are not live tick coverage', 'stages': []}
    streamer.on('open', lambda *_: opened.set())
    streamer.on('message', lambda data: snapshots.set() if data.get('feeds') else None)
    # Record event names only: SDK error text can contain signed connection URLs.
    streamer.on('error', lambda *_: report['stages'].append({'event': 'socket_error'}))

    def checkpoint(name, started):
        connected = opened.wait(30)
        received = snapshots.wait(15) if connected else False
        report['stages'].append(dict(stage=name, connected=connected, snapshot=received,
                                     seconds=round(time.monotonic() - started, 3)))
        if not connected or not received:
            raise RuntimeError('Live reliability checkpoint failed: ' + name)

    try:
        streamer.auto_reconnect(True, 2, 10)
        started = time.monotonic()
        streamer.connect()
        checkpoint('initial_connection', started)
        for attempt in range(3):
            time.sleep(10)
            opened.clear()
            snapshots.clear()
            started = time.monotonic()
            # Abrupt transport failure, rather than intentional SDK disconnect.
            streamer.feeder.ws.sock.shutdown()
            checkpoint('forced_transport_failure_' + str(attempt + 1), started)
        streamer.auto_reconnect(False)
        streamer.disconnect()
        opened.clear()
        snapshots.clear()
        started = time.monotonic()
        config.access_token = ensure_upstox_token(force=True)
        with_rest = MarketREST(config.access_token)
        try:
            with_rest.get('v2/user/profile')
            report['renewed_token_rest_authenticated'] = True
        finally:
            with_rest.close()
        streamer.auto_reconnect(True, 2, 10)
        streamer.connect()
        checkpoint('renewed_token_connection', started)
        time.sleep(30)
        report['passed'] = streamer.decoder_failure is None
    except Exception as error:
        report['passed'] = False
        report['failure_type'] = type(error).__name__
    finally:
        streamer.auto_reconnect(False)
        try:
            streamer.disconnect()
        finally:
            streamer.accepting_frames = False
            streamer.stop_decoder()
        report['frames_received'] = streamer.frames_received
        report['decoder_queue_empty'] = streamer.decode_queue.empty()
        (output / 'live-reliability.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps(report, indent=2))
    return report['passed']


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True)
    raise SystemExit(0 if verify(parser.parse_args().output) else 1)
