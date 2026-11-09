"""Opt-in account event streaming. No orders or account mutations are performed."""
import json
import queue
import socket
import ssl
import threading
from urllib.parse import urlsplit

import websocket

from ..api.client import RequestError

UPDATE_TYPES = {'order', 'gtt_order', 'position', 'holding'}


def validate_socket_url(url):
    parsed = urlsplit(url)
    if (parsed.scheme != 'wss' or not parsed.hostname or
            not (parsed.hostname == 'upstox.com' or parsed.hostname.endswith('.upstox.com')) or
            parsed.username or parsed.password or parsed.fragment or parsed.port not in (None, 443)):
        raise RequestError('Upstox returned an invalid secure socket URL')
    return url


class PortfolioStream:
    def __init__(self, client, *, update_types=('order', 'gtt_order', 'position', 'holding'),
                 on_message=None, on_error=None, on_open=None, on_close=None, reconnect=True):
        if client.profile == 'sandbox':
            raise RequestError('Upstox does not document a sandbox portfolio WebSocket')
        if not update_types or set(update_types) - UPDATE_TYPES:
            raise RequestError('Unsupported portfolio update type')
        self.client, self.update_types = client, tuple(update_types)
        self.on_message, self.on_error = on_message, on_error
        self.on_open, self.on_close = on_open, on_close
        self.reconnect = reconnect
        self.connected = False
        self._stop = threading.Event()
        self._queue = queue.Queue()
        self._ws = self._thread = self._decoder = None

    def _error(self, code):
        if self.on_error:
            try:
                self.on_error({'type': 'portfolio_stream_error', 'code': code})
            except Exception:
                pass

    def _decode(self):
        while True:
            raw = self._queue.get()
            try:
                if raw is None:
                    return
                payload = json.loads(raw)
                if not isinstance(payload, dict):
                    raise ValueError('Invalid event')
                if self.on_message:
                    self.on_message(payload)
            except Exception:
                self._error('decode_or_callback_failed')
            finally:
                self._queue.task_done()

    def _run(self):
        attempt = 0
        while not self._stop.is_set():
            try:
                result = self.client.streams.getPortfolioStreamFeedAuthorize(
                    query={'update_types': ','.join(self.update_types)})
                data = result['data']
                url = validate_socket_url(data.get('authorized_redirect_uri') or data.get('authorizedRedirectUri') or '')
                if self._stop.is_set():
                    break
                def opened(_):
                    nonlocal attempt
                    attempt = 0
                    self.connected = True
                    if self.on_open:
                        try:
                            self.on_open()
                        except Exception:
                            self._error('open_callback_failed')
                self._ws = websocket.WebSocketApp(url, on_open=opened,
                    on_message=lambda _, raw: self._queue.put_nowait(raw),
                    on_error=lambda *_: self._error('connection_failed'))
                self._ws.run_forever(
                    sslopt={'cert_reqs': ssl.CERT_REQUIRED, 'check_hostname': True},
                    sockopt=((socket.IPPROTO_TCP, socket.TCP_NODELAY, 1), (socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)))
            except Exception:
                self._error('authorization_or_connection_failed')
            finally:
                self.connected = False
                if self.on_close:
                    try:
                        self.on_close()
                    except Exception:
                        self._error('close_callback_failed')
            if not self.reconnect:
                break
            attempt += 1
            self._stop.wait(min(30, 2 ** min(attempt, 5)))

    def connect(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._decoder = threading.Thread(target=self._decode, name='upstox-portfolio-decoder', daemon=True)
        self._thread = threading.Thread(target=self._run, name='upstox-portfolio-socket', daemon=True)
        self._decoder.start()
        self._thread.start()

    def close(self):
        self._stop.set()
        if self._ws:
            self._ws.close()
        if self._thread and self._thread is not threading.current_thread():
            self._thread.join()
        if self._decoder and self._decoder.is_alive():
            self._queue.put(None)
            if self._decoder is not threading.current_thread():
                self._decoder.join()

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, *_):
        self.close()
