"""Per-packet cost and allocation of range estimators on a recorded sim trace.

    python3 tools/bench_est.py [--est kalman2,particle] [--scenario zigzag_search]
        [--profile typical] [--seed 101] [--reps 3] [--n 12]
    node tools/mpy/run.mjs tools/bench_est.py

Packet updates are replayed back to back and timed as one block, so
the 1 ms ticks_us resolution of the WebAssembly port does not matter. On
MicroPython ``bytes/pkt`` is the heap allocated per packet with GC off (every
float result is a 16-byte heap block); on CPython it prints ``-``. Copy finder/,
sim/ and this file to a watch to time the real thing.
"""

import sys
import gc


def _root():
    f = globals().get("__file__", "tools/bench_est.py")
    i = f.rfind("/")
    d = f[:i] if i >= 0 else "."
    j = d.rfind("/")
    return d[:j] if j >= 0 else "."


_R = _root()
if _R not in sys.path:
    sys.path.insert(0, _R)

from finder.compat import argv, ticks_add  # noqa: E402
from finder import estimators  # noqa: E402
from sim import Sim  # noqa: E402
from sim import scenarios as _sc  # noqa: E402

try:
    from time import ticks_us as _tus, ticks_diff as _tdiff

    def _clock_us():
        return _tus()

    def _el_us(a, b):
        return _tdiff(b, a)
except ImportError:
    from time import perf_counter as _pc

    def _clock_us():
        return _pc() * 1e6

    def _el_us(a, b):
        return b - a


def packets(scenario="zigzag_search", prof="typical", seed=101, dt=0.05):
    """(t_ms, rssi, peer_rssi, my_motion, peer_motion) for every packet watch A gets."""
    world, dur = _sc.make(scenario, seed)
    s = Sim(world, prof, seed)
    out = []
    pm = None
    for _ in range(int(dur / dt + 0.5)):
        pa, _pb = s.step(dt)
        for p in pa:
            if p.peer_motion is not None:
                pm = p.peer_motion
            out.append((ticks_add(10000, p.t_ms), p.rssi, p.peer_rssi, s.imus[0].info, pm))
    return out, s.cal_p0(0)


def bench(name, calls, cal, reps=3, kw=None):
    """Returns (best us per packet, bytes allocated per packet or None)."""
    kw = kw or {}
    best = None
    for _ in range(reps):
        e = estimators.make(name, **kw)
        e.calibrate(cal)
        u = e.update
        gc.collect()
        t0 = _clock_us()
        for c in calls:
            u(c[0], c[1], c[2], c[3], c[4])
        el = _el_us(t0, _clock_us())
        if best is None or el < best:
            best = el
    alloc = None
    mem = getattr(gc, "mem_alloc", None)
    if mem is not None:
        e = estimators.make(name, **kw)
        e.calibrate(cal)
        u = e.update
        k = min(200, len(calls))
        gc.collect()
        gc.disable()
        try:
            a0 = mem()
            for c in calls[:k]:
                u(c[0], c[1], c[2], c[3], c[4])
            alloc = (mem() - a0) / k
        finally:
            gc.enable()
    return best / len(calls), alloc


def main(args):
    o = {"est": ",".join(estimators.NAMES), "scenario": "zigzag_search", "profile": "typical",
         "seed": "101", "reps": "3", "n": None}
    i = 0
    while i < len(args):
        a = args[i]
        if "=" in a:
            k, v = a[2:].split("=", 1)
            i += 1
        else:
            k, v = a[2:], args[i + 1]
            i += 2
        if k not in o:
            raise ValueError("unknown option " + a)
        o[k] = v
    calls, cal = packets(o["scenario"], o["profile"], int(o["seed"]))
    print("%d packets (%s/%s seed %s)" % (len(calls), o["scenario"], o["profile"], o["seed"]))
    print("| estimator | us/pkt | bytes/pkt |")
    print("|---|---|---|")
    for name in [x.strip() for x in o["est"].split(",") if x.strip()]:
        kw = {"n": int(o["n"])} if o["n"] and name == "particle" else {}
        us, b = bench(name, calls, cal, int(o["reps"]), kw)
        lab = name + (" n=" + o["n"] if kw else "")
        print("| %s | %.1f | %s |" % (lab, us, "-" if b is None else "%.0f" % b))


if __name__ == "__main__":
    main(argv(globals())[1:])
