"""Paced, persistent read-only Upstox market-data requests."""
import threading
import time
from collections import deque

from curl_cffi import requests
from .auth import AuthError


class MarketREST:
    def __init__(self, token):
        self.session = requests.Session(headers={'Authorization': 'Bearer ' + token, 'Accept': 'application/json'})
        self.lock = threading.Lock()
        self.calls = deque()
        self.last_call = 0
        self.status = {}

    def get(self, path, params=None):
        with self.lock:
            now = time.monotonic()
            while self.calls and now - self.calls[0] > 1800:
                self.calls.popleft()
            delay = max(0, self.last_call + 0.3 - now)
            if len(self.calls) >= 1800:
                delay = max(delay, self.calls[0] + 1800 - now)
            if delay:
                time.sleep(delay)
            self.last_call = time.monotonic()
            self.calls.append(self.last_call)
            try:
                response = self.session.get('https://api.upstox.com/' + path, params=params,
                                            timeout=20, allow_redirects=False)
                self.status[path] = {'http': response.status_code, 'checked_epoch': time.time()}
                if response.status_code != 200:
                    raise AuthError(f'Upstox {path} returned HTTP {response.status_code}')
                payload = response.json()
                if payload.get('status') != 'success':
                    raise AuthError(f'Upstox {path} did not return success')
                return payload
            except AuthError:
                raise
            except Exception:
                self.status[path] = {'error': 'request failed', 'checked_epoch': time.time()}
                raise AuthError(f'Upstox {path} request failed') from None

    def close(self):
        with self.lock:
            self.session.close()
