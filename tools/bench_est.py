"""Per-packet cost and allocation of range estimators on a recorded sim trace.

    python3 tools/bench_est.py [--est kalman2,particle] [--scenario zigzag_search]
        [--profile typical] [--seed 101] [--reps 3] [--n 12]
    node tools/mpy/run.mjs tools/bench_est.py

Packet updates are replayed back to back and timed as one block, so
the 1 ms ticks_us resolution of the WebAssembly port does not matter. On
MicroPython ``bytes/pkt`` is the heap allocated per packet with GC off (every
float result is a 16-byte heap block); on CPython it prints ``-``. Copy finder/,
sim/, tools/bakeoff.py, tools/cli.py and this file (keeping the tools/ folder)
to a watch to time the real thing.
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

from finder.compat import ticks_diff, ticks_us  # noqa: E402
from finder import estimators  # noqa: E402
from tools import bakeoff  # noqa: E402
from tools.cli import parse_args  # noqa: E402


def packets(scenario="zigzag_search", prof="typical", seed=101):
    """(t_ms, rssi, peer_rssi, my_motion, peer_motion) for every packet watch A gets."""
    tr = bakeoff.record(scenario, prof, seed)
    out, pm = [], None
    for _t_ms, _t, pks, mm, _d, _vr in tr.ticks:
        for p_t, rssi, prssi, ppm in pks:
            if ppm is not None:
                pm = ppm
            out.append((p_t, rssi, prssi, mm, pm))
    return out, tr.cal


def bench(name, calls, cal, reps=3, kw=None):
    """Returns (best us per packet, bytes allocated per packet or None)."""
    kw = kw or {}
    best = None
    for _ in range(reps):
        e = estimators.make(name, **kw)
        e.calibrate(cal)
        u = e.update
        gc.collect()
        t0 = ticks_us()
        for c in calls:
            u(c[0], c[1], c[2], c[3], c[4])
        el = ticks_diff(ticks_us(), t0)
        if best is None or el < best:
            best = el
    alloc = None
    mem = getattr(gc, "mem_alloc", None)
    if mem is not None:
        e = estimators.make(name, **kw)
        e.calibrate(cal)
        u = e.update
        k = min(200, len(calls))
        sub = calls[:k]         # sliced before a0, so the slice is not counted
        gc.collect()
        gc.disable()
        try:
            a0 = mem()
            for c in sub:
                u(c[0], c[1], c[2], c[3], c[4])
            alloc = (mem() - a0) / k
        finally:
            gc.enable()
    return best / len(calls), alloc


def main(args):
    o = parse_args(args, {"est": ",".join(estimators.NAMES), "scenario": "zigzag_search",
                          "profile": "typical", "seed": "101", "reps": "3", "n": None})
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
    main(sys.argv[1:])
