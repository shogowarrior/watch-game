"""Cost of sim/webhost.py per page frame (run under the wasm MicroPython):

    node tools/mpy/run.mjs tools/bench_webhost.py [steps]

Times ``step(50)`` (both watches rendering at 20 fps), ``step(16)`` (a 60 Hz
page), ``telemetry_json()`` and the heap allocated per step, in the HUNT
screens after the auto pairing, while A walks, and during A's active scan
sweep; then in real mode (debug-mode.md), ``show_params()`` with an ``rp``
record's params and ``step(50)`` drawing them. Also runs on CPython (logic
only, no frames).
"""

import gc
import json
import sys


def _root():
    f = globals().get("__file__", "tools/bench_webhost.py")
    i = f.rfind("/")
    d = f[:i] if i >= 0 else "."
    j = d.rfind("/")
    return d[:j] if j >= 0 else "."


_R = _root()
if _R not in sys.path:
    sys.path.insert(0, _R)

from finder.compat import ticks_diff, ticks_us  # noqa: E402
from finder.estimators.base import ACT_STILL  # noqa: E402
from finder.render_params import to_dict  # noqa: E402
from sim.webhost import TwoWatchSim  # noqa: E402


def _alloc():
    f = getattr(gc, "mem_alloc", None)
    return f() if f is not None else 0


def bench(label, s, fn, n):
    gc.collect()
    a0 = _alloc()
    gc_was = gc.isenabled() if hasattr(gc, "isenabled") else True
    gc.disable()
    t0 = ticks_us()
    for _ in range(n):
        fn(s)
    us = ticks_diff(ticks_us(), t0)
    a1 = _alloc()
    if gc_was:
        gc.enable()
    print("%-22s %7.3f ms/call  %7.0f B/call" % (label, us / 1000.0 / n, (a1 - a0) / n))
    return us / 1000.0 / n


def _step_until(s, cond, max_ms=20000):
    end = s.t_ms + max_ms
    while not cond(s):
        if s.t_ms >= end:
            raise SystemExit("bench: condition not reached by %d ms" % s.t_ms)
        s.step(50)


def _sweeping(s):
    g = s.games[0]
    return g.mode == "SCANNING" and g.scan.sub == "sweep" and not g.scan.paused


def main():
    args = [a for a in sys.argv[1:] if a.isdigit()]
    n = int(args[0]) if args else 200
    s = TwoWatchSim(seed=1)
    while s.t_ms < 15000:
        s.step(50)
    print("impl", sys.implementation.name, "screens", s.telemetry(0)["screen"],
          s.telemetry(1)["screen"], "renderers", s.renderers is not None)
    ms50 = bench("step(50)", s, lambda s: s.step(50), n)
    bench("step(16)", s, lambda s: s.step(16), n * 3)
    bench("telemetry_json()", s, lambda s: s.telemetry_json(), n)
    s.walk_to(0, 30.0, 8.0)
    bench("step(50) walking", s, lambda s: s.step(50), n)
    a = s.world.a
    s.set_pose(0, a.x, a.y)                   # stop where A is (a drag ends the walk)
    _step_until(s, lambda s: s.games[0].me.activity == ACT_STILL)
    s.tap(0)                                  # scan, then wait for the sweep itself
    _step_until(s, _sweeping)
    bench("step(50) scanning", s, lambda s: s.step(50), min(n, 200))    # the sweep lasts 12 s
    js = [json.dumps(to_dict(p)) for p in s._params]
    s.real_mode(True)                         # real watches: the page hands in rp records
    bench("show_params()", s, lambda s: s.show_params(0, js[0]), n)
    s.show_params(1, js[1])
    bench("step(50) real", s, lambda s: s.step(50), n)
    print("frames", s.frames, "sim t %.1f s" % (s.t_ms / 1000.0))
    print("step(50) = %.1f %% of a 50 ms real-time budget" % (100.0 * ms50 / 50.0))


main()
