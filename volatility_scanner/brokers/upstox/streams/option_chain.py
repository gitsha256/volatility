"""Direct Protobuf -> bounded latest-state mailbox -> minute algorithm cache."""
from datetime import datetime
import math
import threading
import time

INDEX = 'NSE_INDEX|Nifty 50'


class OptionChainFeed:
    """Single decoder producer, event-loop-owned cache. Intermediate states coalesce.

    This adapter serves minute algorithms; it does not promise a tick history.
    Pending memory scales with subscribed instruments, never the number of ticks.
    """
    def __init__(self, recorder):
        self.recorder = recorder
        self.loop = recorder.loop
        self.lock = threading.Lock()
        self.allowed = frozenset(recorder.contracts) | {INDEX}
        self.pending = {}
        self.scheduled = False
        self.cutoff = 0.0
        self.accepting = True
        self.failure = None
        self.updates = self.coalesced = self.handoffs = 0
        self.receive_to_cache_ms = 0.0

    def configure(self, contracts):
        with self.lock:
            self.allowed = frozenset(contracts) | {INDEX}
            self.pending = {key: value for key, value in self.pending.items() if key in self.allowed}

    def connection(self, connected, timestamp):
        """Called on the cache event loop; discard observations from older sockets."""
        with self.lock:
            self.cutoff = timestamp
            self.pending.clear()
        self.recorder.connected = connected
        self.recorder.analytics_generation = getattr(self.recorder, 'analytics_generation', 0) + 1
        self.recorder.received.clear()
        if hasattr(self.recorder,'price_received'):
            self.recorder.price_received.clear()
        self.recorder.index_received = None

    def submit(self, frame, received_at, stamp):
        received = datetime.fromisoformat(received_at).timestamp()
        schedule = False
        with self.lock:
            if not self.accepting or received < self.cutoff:
                return
            for key, feed in frame.feeds.items():
                if key not in self.allowed:
                    continue
                kind = feed.WhichOneof('FeedUnion')
                data = None
                full_kind = None
                if kind == 'fullFeed':
                    full_kind = feed.fullFeed.WhichOneof('FullFeedUnion')
                    if full_kind:
                        data = getattr(feed.fullFeed, full_kind)
                elif kind == 'firstLevelWithGreeks':
                    data = feed.firstLevelWithGreeks
                elif kind == 'ltpc':
                    data = feed.ltpc
                if data is None:
                    continue
                self.updates += 1
                if key in self.pending:
                    self.coalesced += 1
                fields = self.pending.setdefault(key, {})
                fields['_stamp'] = stamp
                if kind == 'ltpc':
                    price = data.ltp
                    trade_time = data.ltt
                else:
                    price = data.ltpc.ltp if data.HasField('ltpc') else None
                    trade_time = data.ltpc.ltt if data.HasField('ltpc') else 0
                    if key != INDEX and (kind == 'firstLevelWithGreeks' or full_kind == 'marketFF'):
                        # Protobuf scalar defaults include legitimate zero OI/IV.
                        # LTPC-only messages must not refresh the OI receipt age.
                        fields.update(oi=data.oi if math.isfinite(data.oi) and data.oi >= 0 else None,
                                      iv=data.iv if math.isfinite(data.iv) and data.iv >= 0 else None,
                                      _oi_received=received)
                if price is not None and math.isfinite(price) and price > 0:
                    fields.update(ltp=price, _price_received=received,
                                  trade_epoch=trade_time/1000 if trade_time>0 else None)
            if self.pending and not self.scheduled:
                self.scheduled = True
                schedule = True
        if schedule:
            self.loop.call_soon_threadsafe(self.drain)

    def drain(self):
        """One callback per batch, not per instrument/tick; no I/O under the lock."""
        with self.lock:
            pending, self.pending = self.pending, {}
            self.scheduled = False
        try:
            for key, fields in pending.items():
                stamp = fields.pop('_stamp')
                oi_received = fields.pop('_oi_received', None)
                price_received = fields.pop('_price_received', None)
                if key == INDEX:
                    if price_received is not None:
                        self.recorder.index.update(ltp=fields['ltp'])
                        self.recorder.index_received = price_received
                elif key in self.recorder.contracts:
                    previous = self.recorder.latest.setdefault(key, {})
                    old_received = self.recorder.received.get(key)
                    dirty = getattr(self.recorder, 'analytics_dirty', None)
                    if dirty is not None and oi_received is not None and (
                            previous.get('oi') != fields.get('oi') or previous.get('iv') != fields.get('iv')
                            or old_received is None or oi_received - old_received > 15):
                        dirty.add(key)
                    previous.update(fields)
                    if oi_received is not None:
                        self.recorder.received[key] = oi_received
                    if price_received is not None and hasattr(self.recorder,'price_received'):
                        self.recorder.price_received[key] = price_received
                self.receive_to_cache_ms = (time.perf_counter_ns() - stamp) / 1e6
            self.handoffs += bool(pending)
        except Exception as error:
            self.failure = error

    def close(self):
        with self.lock:
            self.accepting = False
        self.drain()

    def health(self):
        with self.lock:
            return dict(pending_instruments=len(self.pending), updates=self.updates,
                        coalesced_updates=self.coalesced, handoffs=self.handoffs,
                        receive_to_cache_ms=self.receive_to_cache_ms)
