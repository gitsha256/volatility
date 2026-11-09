"""Dedicated strike channels, durable tick journal, and Excel exports."""
from __future__ import annotations

import asyncio
import json
import logging
import math
import queue
import shutil
import sqlite3
import threading
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path


IST = timezone(timedelta(hours=5, minutes=30))
INDEX = "NSE:NIFTY50-INDEX"
log = logging.getLogger(__name__)


class Recorder:
    def __init__(self, directory: str | Path):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.db_path = self.directory / "capture.sqlite3"
        self.db = sqlite3.connect(self.db_path, check_same_thread=False)
        self.write_lock = threading.Lock()
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.execute("""CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY, day TEXT, bucket TEXT,
            received_at TEXT, source TEXT, payload TEXT)""")
        self.db.execute("CREATE INDEX IF NOT EXISTS event_bucket ON events(day, bucket, id)")
        self.db.commit()
        self.loop = asyncio.get_running_loop()
        self.channels = {}
        self.tasks = []
        self.symbols = {}
        self.index = {}
        self.index_received = None
        self.connected = False
        self.accepting = True
        self.failure = None
        self.export_lock = threading.Lock()
        self.db.execute("CREATE TABLE IF NOT EXISTS raw_frames (id INTEGER PRIMARY KEY, received_at TEXT NOT NULL, payload BLOB NOT NULL)")
        self.db.commit()
        self.pending = queue.Queue()
        self.metrics = dict(committed_events=0, committed_frames=0, commits=0,
                            commit_ms=0.0, oldest_write_ms=0.0)
        self.writer = threading.Thread(target=self._write_loop, daemon=True, name="capture-writer")
        self.writer.start()

    def register(self, row, expiry):
        symbol = row.get("symbol")
        strike = row.get("strike_price")
        if not symbol or row.get("option_type") not in ("CE", "PE"):
            return
        strike = float(strike)
        if strike <= 0 or not math.isfinite(strike):
            raise ValueError("Invalid strike in option chain")
        bucket = f"{int(expiry)}_{strike:g}"
        self.symbols[symbol] = (bucket, row["option_type"])
        self.channel(bucket)
        self.enqueue(bucket, "chain", dict(row, expiry=expiry))

    def channel(self, bucket):
        if bucket not in self.channels:
            queue = asyncio.Queue()
            self.channels[bucket] = queue
            self.tasks.append(asyncio.create_task(self.consume(bucket, queue)))
        return self.channels[bucket]

    def enqueue(self, bucket, source, payload):
        now = datetime.now(IST)
        if source == 'websocket' and payload.get('socket_received_at_ist'):
            now = datetime.fromisoformat(payload['socket_received_at_ist'])
        self.channel(bucket).put_nowait((now, source, payload))

    def on_message(self, message):
        # SDK callback runs on its socket thread. Only transfer owned copies here.
        if self.accepting and isinstance(message, dict):
            self.loop.call_soon_threadsafe(self.dispatch, dict(message))

    def dispatch(self, message):
        symbol = message.get("symbol")
        if symbol == INDEX:
            self.index.update(message)
            if "ltp" in message:
                self.index_received = time.time()
        elif symbol in self.symbols:
            bucket, leg = self.symbols[symbol]
            self.enqueue(bucket, "websocket", dict(message, option_type=leg))
        elif message.get("s") == "error":
            self.connected = False
            log.error("FYERS rejected a stream request; check credentials/subscription")

    async def consume(self, bucket, channel):
        while True:
            event = await channel.get()
            self.pending.put_nowait(("event", bucket, event, time.monotonic()))
            channel.task_done()

    def record_frame(self, payload, received_at):
        self.pending.put_nowait(("raw", "", (received_at, payload), time.monotonic()))

    def _write_loop(self):
        while True:
            first = self.pending.get()
            if first is None:
                self.pending.task_done()
                return
            batch = [first]
            deadline = time.monotonic() + 0.05
            while len(batch) < 10000:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                try:
                    batch.append(self.pending.get(timeout=remaining))
                except queue.Empty:
                    break
            try:
                events, frames = [], []
                for kind, bucket, item, _ in batch:
                    if kind == "raw":
                        frames.append((item[0], item[1]))
                    else:
                        now, source, payload = item
                        events.append((now.date().isoformat(), bucket,
                                       now.isoformat(timespec="microseconds"), source,
                                       json.dumps(payload, allow_nan=False, separators=(",", ":"))))
                started = time.monotonic()
                with self.write_lock, self.db:
                    self.db.executemany(
                        "INSERT INTO events(day,bucket,received_at,source,payload) VALUES(?,?,?,?,?)", events)
                    self.db.executemany("INSERT INTO raw_frames(received_at,payload) VALUES(?,?)", frames)
                self.metrics.update(
                    committed_events=self.metrics['committed_events'] + len(events),
                    committed_frames=self.metrics['committed_frames'] + len(frames),
                    commits=self.metrics['commits'] + 1,
                    commit_ms=(time.monotonic() - started) * 1000,
                    oldest_write_ms=(time.monotonic() - batch[0][3]) * 1000,
                )
            except Exception as error:
                self.failure = error
                log.exception("Capture writer failed; stopping capture")
            finally:
                for _ in batch:
                    self.pending.task_done()

    def stop_writer(self):
        if not self.writer.is_alive():
            return
        self.pending.join()
        self.pending.put(None)
        self.writer.join()

    def health(self):
        with self.pending.mutex:
            oldest = next((item[3] for item in self.pending.queue if item is not None), None)
        return dict(self.metrics, writer_queue=self.pending.qsize(),
                    strike_queue=sum(q.qsize() for q in self.channels.values()),
                    oldest_queued_ms=(time.monotonic() - oldest) * 1000 if oldest else 0,
                    free_bytes=shutil.disk_usage(self.directory).free,
                    connected=self.connected)

    async def sample_index(self):
        deadline = time.monotonic()
        while True:
            self.enqueue("NIFTY50", "one_second", dict(
                self.index, connected=self.connected,
                last_tick_received_epoch=self.index_received,
                stale=(not self.connected or self.index_received is None
                       or time.time() - self.index_received > 15),
            ))
            deadline += 1
            await asyncio.sleep(max(0, deadline - time.monotonic()))

    async def drain(self):
        await asyncio.gather(*(q.join() for q in self.channels.values()))
        await asyncio.to_thread(self.pending.join)
        if self.failure:
            raise RuntimeError("Recording failed; inspect capture logs") from self.failure

    def export(self):
        from volatility_scanner.storage.excel import export_directory
        with self.export_lock:
            return export_directory(self.directory)
