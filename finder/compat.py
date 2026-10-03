"""Tiny portability layer so the logic runs on MicroPython and CPython.

Rules for code under finder/ and sim/:
  * import time helpers from here, never from ``time`` directly
  * no dataclasses, typing, numpy, f-string ``=`` specifiers, or ``deque`` ops
    beyond append/popleft
  * avoid allocating in per-sample hot paths where practical
"""

try:
    from micropython import const  # noqa: F401
except ImportError:  # CPython
    def const(x):
        return x

from array import array

try:
    from time import ticks_ms, ticks_us, ticks_diff, ticks_add, sleep_ms  # MicroPython
except ImportError:  # CPython
    import time as _time

    _TICKS_PERIOD = 1 << 30
    _TICKS_HALF = _TICKS_PERIOD // 2

    def ticks_ms():
        return int(_time.monotonic() * 1000) & (_TICKS_PERIOD - 1)

    def ticks_us():
        return int(_time.perf_counter() * 1000000) & (_TICKS_PERIOD - 1)

    def ticks_add(t, delta):
        return (t + delta) & (_TICKS_PERIOD - 1)

    def ticks_diff(a, b):
        """Signed difference a - b, wrap-aware (same contract as MicroPython)."""
        d = (a - b) & (_TICKS_PERIOD - 1)
        return d - _TICKS_PERIOD if d >= _TICKS_HALF else d

    def sleep_ms(ms):
        _time.sleep(ms / 1000.0)


class TickRing:
    """The last ``k`` event stamps (ticks ms), allocation-free after __init__.
    ``full_within`` is True when ``k`` events fell within ``window_ms`` of
    ``now`` (3 packets in 2 s, 3 touches in 1 s)."""

    def __init__(self, k):
        self.t = array("i", [0] * k)
        self.k = k
        self.clear()

    def clear(self):
        self.i = 0          # next slot = the oldest stamp once full
        self.n = 0          # stamps held, at most k

    def note(self, t):
        i = self.i
        self.t[i] = t
        i += 1
        self.i = 0 if i == self.k else i
        if self.n < self.k:
            self.n += 1

    def full_within(self, now, window_ms):
        return self.n == self.k and ticks_diff(now, self.t[self.i]) <= window_ms

    def expire(self, now, window_ms):
        """Forget all stamps once the newest is older than ``window_ms``, so no
        stamp ever ages into a wrapped ticks_diff."""
        if self.n and ticks_diff(now, self.t[(self.i or self.k) - 1]) > window_ms:
            self.clear()


def clamp(x, lo, hi):
    return lo if x < lo else hi if x > hi else x
