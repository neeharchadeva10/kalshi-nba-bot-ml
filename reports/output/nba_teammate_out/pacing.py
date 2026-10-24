"""Adaptive request pacing (AIMD) for the shared props download.

Start 0.5 req/s.  +0.1 req/s after every 60 s with no 429 (additive increase); x0.5 on any 429 (multiplicative decrease);
floor 0.2, ceiling 1.5 req/s.  A Retry-After header is honoured (no request is released before it expires).
The clock and sleep functions are injected so the behaviour is unit-testable with a fake clock.
"""
from __future__ import annotations

import threading
import time


class AIMD:
    def __init__(self, start=0.5, step=0.1, period_s=60.0, floor=0.2, ceil=1.5, clock=time.monotonic, sleep=time.sleep):
        self.rate = float(start)
        self.step, self.period_s, self.floor, self.ceil = step, period_s, floor, ceil
        self.clock, self.sleep = clock, sleep
        now = clock()
        self.last_event = now          # last 429 or last increase
        self.next_slot = now           # earliest time the next request may be released
        self.blocked_until = now       # Retry-After
        self.n_429 = 0
        self.n_ok = 0
        self.min_rate = self.max_rate = self.rate
        self._lock = threading.Lock()

    def _update(self, now):
        while now - self.last_event >= self.period_s:        # one +step per full 60 s without a 429
            self.last_event += self.period_s
            self.rate = min(self.ceil, round(self.rate + self.step, 6))
            self.max_rate = max(self.max_rate, self.rate)

    def acquire(self):
        """Block until this request may be sent; slots are spaced 1/rate apart.  Thread-safe."""
        with self._lock:
            now = self.clock()
            self._update(now)
            t = max(now, self.next_slot, self.blocked_until)
            self.next_slot = t + 1.0 / self.rate
        d = t - now
        if d > 0:
            self.sleep(d)

    def on_ok(self):
        with self._lock:
            self.n_ok += 1

    def on_429(self, retry_after=None):
        with self._lock:
            now = self.clock()
            self.n_429 += 1
            self.rate = max(self.floor, self.rate * 0.5)
            self.min_rate = min(self.min_rate, self.rate)
            self.last_event = now                              # the 60 s no-429 clock restarts
            if retry_after is not None:
                self.blocked_until = max(self.blocked_until, now + float(retry_after))
            self.next_slot = max(self.next_slot, now + 1.0 / self.rate)
