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

try:
    from time import ticks_ms, ticks_diff, ticks_add  # MicroPython
except ImportError:  # CPython
    import time as _time

    _TICKS_PERIOD = 1 << 30
    _TICKS_HALF = _TICKS_PERIOD // 2

    def ticks_ms():
        return int(_time.monotonic() * 1000) & (_TICKS_PERIOD - 1)

    def ticks_add(t, delta):
        return (t + delta) & (_TICKS_PERIOD - 1)

    def ticks_diff(a, b):
        """Signed difference a - b, wrap-aware (same contract as MicroPython)."""
        d = (a - b) & (_TICKS_PERIOD - 1)
        return d - _TICKS_PERIOD if d >= _TICKS_HALF else d


def clamp(x, lo, hi):
    return lo if x < lo else hi if x > hi else x


def lerp(a, b, t):
    return a + (b - a) * t


def argv(g):
    """Command-line args for a script: ``argv(globals())``.

    The WebAssembly MicroPython runner cannot set ``sys.argv``; it passes the
    list as ``__argv__`` in the script globals instead.
    """
    a = g.get("__argv__")
    if a is not None:
        return list(a)
    import sys
    return list(getattr(sys, "argv", []))
