"""One shared FYERS socket; callbacks update memory, never Streamlit widgets."""
from __future__ import annotations

import atexit
import copy
import hashlib
import tempfile
import threading
import time
from pathlib import Path

from fyers_apiv3.FyersWebsocket.data_ws import FyersDataSocket

from volatility_scanner.market.data import FyersDataError, FyersDataProvider, MarketSnapshot, derive_missing_ivs


class LiveFeed:
    def __init__(self, provider: FyersDataProvider):
        self.provider = provider
        self._snapshot = provider.fetch_snapshot()
        self.started = time.time()
        self._lock = threading.Lock()
        self._last_tick = 0.0
        self._status = "connecting"
        self._closed = False
        self.auth_failed = False
        self._socket = None
        self._symbols = {}
        for quote in self._snapshot.option_chain:
            for leg in ("ce", "pe"):
                symbol = getattr(quote, leg + "_symbol")
                if symbol:
                    self._symbols[symbol] = (quote, leg)
        if not self._symbols:
            raise FyersDataError("FYERS did not return option symbols for streaming.")
        self._thread = threading.Thread(target=self._connect, daemon=True, name="fyers-feed")
        self._thread.start()

    def _connect(self):
        log_dir = Path(tempfile.gettempdir()) / "nifty-scanner-fyers"
        log_dir.mkdir(exist_ok=True)
        try:
            self._socket = FyersDataSocket(
                access_token=f"{self.provider.client_id}:{self.provider.access_token}",
                log_path=str(log_dir), write_to_file=False, litemode=False,
                reconnect=True, reconnect_retry=50,
                on_connect=self._on_connect, on_message=self._on_message,
                on_error=self._on_error, on_close=self._on_close,
            )
            if not self._closed:
                self._socket.connect()
        except Exception:
            self._on_error({})

    def _on_connect(self):
        if self._closed:
            return
        with self._lock:
            self._status = "connected; awaiting ticks"
            self._last_tick = 0.0
        self._socket.subscribe(
            symbols=[self.provider.futures_symbol, *self._symbols], data_type="SymbolUpdate"
        )

    def _on_error(self, message):
        with self._lock:
            self._status = "stream error; check authentication, plan or connection"
            if isinstance(message, dict) and message.get("code") in (-8, -15, -16, -17):
                self.auth_failed = True

    def _on_close(self, message):
        with self._lock:
            self._status = "disconnected; reconnecting"

    def _on_message(self, message):
        if self._closed or not isinstance(message, dict):
            return
        if message.get("s") == "error":
            self._on_error(message)
            return
        symbol = message.get("symbol")
        if symbol != self.provider.futures_symbol and symbol not in self._symbols:
            return
        changed = False
        with self._lock:
            if symbol == self.provider.futures_symbol:
                price = FyersDataProvider._number(message.get("ltp"))
                if price is not None and price > 0:
                    self._snapshot.futures_price = price
                    self._snapshot.futures_updated_at = time.time()
                    changed = True
            else:
                quote, leg = self._symbols[symbol]
                for field, key in (("bid", "bid_price"), ("ask", "ask_price")):
                    value = FyersDataProvider._number(message.get(key))
                    if value is not None and value >= 0:
                        setattr(quote, f"{leg}_{field}", value)
                        changed = True
                if changed:
                    setattr(quote, leg + "_updated_at", time.time())
            if changed:
                self._last_tick = time.time()
                self._status = "streaming"

    def snapshot(self) -> MarketSnapshot:
        with self._lock:
            snapshot = copy.deepcopy(self._snapshot)
            snapshot.last_update = self._last_tick
            snapshot.feed_status = self._status
        if snapshot.last_update and time.time() - snapshot.last_update > 15:
            snapshot.feed_status = "stale; no quote updates for 15s"
        # Derived values must be recalculated from the latest quotes, not reused.
        for quote in snapshot.option_chain:
            for leg in ("ce", "pe"):
                if getattr(quote, leg + "_iv_source") == "derived":
                    setattr(quote, leg + "_iv", None)
        try:
            expiry = float(snapshot.expiry_data[0]["expiry"])
            derive_missing_ivs(snapshot, (expiry - time.time()) / (365 * 86400))
        except (IndexError, KeyError, TypeError, ValueError):
            raise FyersDataError("Invalid live option-chain expiry.") from None
        return snapshot

    def close(self):
        if self._closed:
            return
        self._closed = True
        self._thread.join(timeout=5)
        if self._socket is not None:
            self._socket.close_connection()
        if self.provider._http is not None:
            self.provider._http.close()


_feed: LiveFeed | None = None
_identity = None
_feed_lock = threading.Lock()


def get_live_snapshot(provider: FyersDataProvider) -> MarketSnapshot:
    global _feed, _identity
    with _feed_lock:
        if (_feed is not None and _feed.auth_failed
                and provider.access_token == _feed.provider.access_token):
            provider.refresh_access_token(force=True)
        identity = hashlib.sha256(
            f"{provider.client_id}:{provider.access_token}:{provider.futures_symbol}".encode()
        ).digest()
        # Periodically refresh the strike universe and expiry, not prices on each rerun.
        if _feed is None or _identity != identity or time.time() - _feed.started > 300:
            if _feed is not None:
                _feed.close()
            _feed = None
            _feed = LiveFeed(provider)
            _identity = hashlib.sha256(
                f"{provider.client_id}:{provider.access_token}:{provider.futures_symbol}".encode()
            ).digest()
        return _feed.snapshot()


@atexit.register
def close_live_feed():
    if _feed is not None:
        _feed.close()
