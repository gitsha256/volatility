"""Verified TLS and TCP_NODELAY transport for the pinned Upstox SDK."""
import socket
import queue
import time
from datetime import datetime
from google.protobuf import json_format
from volatility_scanner.storage.journal import IST
import ssl
import threading

import websocket
from upstox_client import MarketDataStreamerV3
from upstox_client.feeder.market_data_feeder_v3 import MarketDataFeederV3


class VerifiedFeeder(MarketDataFeederV3):
    def connect(self):
        if self.ws and self.ws.sock:
            return
        self.ws = websocket.WebSocketApp(
            'wss://api.upstox.com/v3/feed/market-data-feed',
            header={'Authorization': self.api_client.configuration.auth_settings()['OAUTH2']['value']},
            on_open=self.on_open, on_message=self.on_message,
            on_error=self.on_error, on_close=self.on_close,
        )
        threading.Thread(
            target=self.ws.run_forever, name='upstox-websocket', daemon=True,
            kwargs={'sslopt': {'cert_reqs': ssl.CERT_REQUIRED, 'check_hostname': True},
                    'sockopt': ((socket.IPPROTO_TCP, socket.TCP_NODELAY, 1),
                                (socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1))},
        ).start()


class VerifiedStreamer(MarketDataStreamerV3):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.raw_callback = None
        self.accepting_frames = True
        self.decode_queue = queue.Queue()
        self.decoder_failure = None
        self.frames_received = 0
        self.bytes_received = 0
        self.decode_ms = 0.0
        self.decoder = threading.Thread(target=self._decode_loop, daemon=True, name='upstox-decoder')
        self.decoder.start()

    def handle_message(self, ws, message):
        if not self.accepting_frames:
            return
        received = datetime.now(IST).isoformat(timespec='microseconds')
        stamp = time.perf_counter_ns()
        if self.raw_callback:
            self.raw_callback(message, received)
        self.frames_received += 1
        self.bytes_received += len(message)
        self.decode_queue.put_nowait((message, received, stamp))

    def _decode_loop(self):
        while True:
            item = self.decode_queue.get()
            try:
                if item is None:
                    return
                message, received, stamp = item
                data = json_format.MessageToDict(self.decode_protobuf(message),
                                                always_print_fields_with_no_presence=True)
                data['_received_at_ist'] = received
                data['_receive_perf_ns'] = stamp
                self.decode_ms = (time.perf_counter_ns() - stamp) / 1e6
                self.emit(self.Event['MESSAGE'], data)
            except Exception as error:
                self.decoder_failure = error
            finally:
                self.decode_queue.task_done()

    def stop_decoder(self):
        if not self.decoder.is_alive():
            return
        self.decode_queue.join()
        self.decode_queue.put(None)
        self.decoder.join()

    def connect(self):
        self.feeder = VerifiedFeeder(
            api_client=self.api_client, instrumentKeys=self.instrumentKeys, mode=self.mode,
            on_open=self.handle_open, on_message=self.handle_message,
            on_error=self.handle_error, on_close=self.handle_close,
        )
        self.feeder.connect()
