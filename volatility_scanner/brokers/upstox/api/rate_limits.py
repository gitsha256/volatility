"""Shared sliding-window limits with synchronous and asyncio acquisition."""
import asyncio
from collections import deque
import threading
import time


class RateBudget:
    def __init__(self, windows):
        self.windows = [(seconds, limit, deque()) for seconds, limit in windows]
        self.lock = threading.Lock()

    def reserve(self):
        with self.lock:
            now = time.monotonic()
            wait = 0
            for seconds, limit, stamps in self.windows:
                while stamps and now - stamps[0] >= seconds:
                    stamps.popleft()
                if len(stamps) >= limit:
                    wait = max(wait, stamps[0] + seconds - now)
            if wait <= 0:
                for _, _, stamps in self.windows:
                    stamps.append(now)
            return wait

    def acquire(self):
        while (delay := self.reserve()) > 0:
            time.sleep(delay)

    async def async_acquire(self):
        while (delay := self.reserve()) > 0:
            await asyncio.sleep(delay)


_lock = threading.Lock()
_budgets = {}


def budget(environment, category, operation=None):
    windows = {'orders': ((1, 10), (60, 500), (1800, 2000)),
               'standard': ((1, 50), (60, 500), (1800, 2000)),
               'totp': ((1, 1), (60, 10), (1800, 60)),
               'payout': ((60, 5), (1800, 30)),
               'payments_read': ((1, 10), (60, 500), (1800, 2000)),
               'ipo': ((1, 1), (60, 10), (1800, 300))}[category]
    # Standard limits are per API/user; order writes have a combined budget.
    scope = operation if category == 'standard' else None
    key = (environment, category, scope)
    with _lock:
        if key not in _budgets:
            _budgets[key] = RateBudget(windows)
        return _budgets[key]
