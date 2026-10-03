"""Range-estimator bake-off on the two-watch simulator.

    python3 tools/bakeoff.py [--quick] [--est ema,kalman1d] [--seeds 0-9]
        [--profiles typical,harsh] [--scenarios approach,orbit] [--imu typical]
        [--rate 10] [--duration S] [--wrap] [--quiet] [--out results.md]

--imu picks the motion-hint error model (ideal|typical|drifty, see sim/imu.py);
--wrap starts the clock just before the ticks_ms wrap; --duration caps runs.

Each (scenario, profile, seed) is simulated once and the same trace is replayed
into every estimator (watch A's view), set up as the game sets it up: the
tokens path-loss exponent for the profile's environment (indoor for
``INDOOR_PROFILES``), then the 1 m calibration (PAIRING ``calibrate``, ui-spec
§5.8). Every received packet is an update, and ticks (50 ms) without a packet
get ``update(t_ms, None, ...)`` so estimators can age. Each tick also feeds the
game's ``finder.proximity`` (zones with hysteresis and dwell, the gated trend).
Metrics are taken after a 3 s warm-up. us/update averages every call (packet
and no-packet ticks); us/pkt averages packet updates only. Under the WebAssembly
port ticks_us has 1 ms resolution, so only these means are useful.
"""

import math
import sys


def _root():
    f = globals().get("__file__", "tools/bakeoff.py")
    i = f.rfind("/")
    d = f[:i] if i >= 0 else "."
    j = d.rfind("/")
    return d[:j] if j >= 0 else "."


_R = _root()
if _R not in sys.path:
    sys.path.insert(0, _R)

from finder import tuning as T  # noqa: E402
from finder.compat import ticks_add, ticks_diff, ticks_us  # noqa: E402
from finder.estimators import NAMES, cls as load  # noqa: E402
from finder.proximity import DeliveryMeter, Proximity, ZoneTracker  # noqa: E402
from sim import Sim  # noqa: E402
from sim import scenarios as _sc  # noqa: E402
from sim.radio import INDOOR_PROFILES, PROFILE_NAMES  # noqa: E402
from tools.cli import parse_args as _parse_args  # noqa: E402

DT = 0.05
WARMUP_S = 3.0
T0_MS = 10000
T0_WRAP_MS = (1 << 30) - 20000
W_DIST, W_TREND, W_FALSE, W_FLIPS = 0.35, 0.35, 0.15, 0.15
QUICK_SEEDS = [0, 1, 2]
QUICK_PROFILES = ["typical"]
VERDICT_MAX = 0.05      # ui-spec §5.5: false verdicts on a tangential walk (orbit)


class Trace:
    """One simulated run from watch A's point of view.

    ``ticks``: list of (t_ms, t_s, packets, my_motion, true_dist, radial_speed)
    where ``packets`` lists the (t_ms, rssi, peer_rssi, peer_motion) of every
    packet A received during the tick (empty when none arrived).
    """

    def __init__(self, scenario, prof, seed, ticks, cal, duration, meta, rate_hz):
        self.scenario = scenario
        self.prof = prof
        self.seed = seed
        self.ticks = ticks
        self.cal = cal
        self.duration = duration
        self.meta = meta
        self.rate_hz = rate_hz


def record(scenario, prof="typical", seed=0, imu="typical", duration=None, wrap=False, rate_hz=10.0):
    """Simulate a scenario and return a Trace."""
    world, dur = _sc.make(scenario, seed)
    if duration is not None and duration < dur:
        dur = duration
    s = Sim(world, prof, seed, imu, rate_hz)
    t0 = T0_WRAP_MS if wrap else T0_MS
    ticks = []
    n = int(dur / DT + 0.5)
    for _ in range(n):
        pa, _pb = s.step(DT)
        t = world.t
        if pa:
            pa = [(ticks_add(t0, p.t_ms), p.rssi, p.peer_rssi, p.peer_motion) for p in pa]
        ticks.append((ticks_add(t0, int(t * 1000.0 + 0.5)), t, pa, s.imus[0].info,
                      world.distance(), world.radial_speed))
    return Trace(scenario, prof, seed, ticks, s.cal_p0(0), dur, dict(world.meta), rate_hz)


def evaluate(cls, tr):
    """Replay a Trace into a fresh ``cls()``; returns a metrics dict."""
    est = cls()
    est.set_exponent(T.PATH_LOSS_N_INDOOR if tr.prof in INDOOR_PROFILES else T.PATH_LOSS_N)
    est.calibrate(tr.cal)
    upd = est.update
    px = Proximity()
    meter = DeliveryMeter(tr.rate_hz)
    peer_m = None
    calls = 0
    us = 0.0
    n_pk = 0
    us_pk = 0.0
    se = 0.0
    n_d = 0
    n_eval = 0
    n_mov = 0
    ok = 0
    cov = 0
    ok_g = 0
    cov_g = 0
    n_still = 0
    false = 0
    false_g = 0
    tz = ZoneTracker()        # the true distance through the game's zone rules
    ef = 0
    tf = 0
    turn_t = tr.meta.get("turn_t")
    lag = None
    streak = None
    t_last = 0.0
    for t_ms, t, pks, mm, d, vr in tr.ticks:
        if pks:
            for p_t, rssi, prssi, pm in pks:
                if pm is not None:
                    peer_m = pm
                c0 = ticks_us()
                upd(p_t, rssi, prssi, mm, peer_m)
                e_us = ticks_diff(ticks_us(), c0)
                us += e_us
                us_pk += e_us
                calls += 1
                n_pk += 1
                meter.note(p_t)
        else:
            c0 = ticks_us()
            upd(t_ms, None, None, mm, peer_m)
            us += ticks_diff(ticks_us(), c0)
            calls += 1
        px.update_est(t_ms, est, mm.activity, meter.ratio(t_ms))
        tz.update(t_ms, d)
        if t < WARMUP_S:
            continue
        t_last = t
        n_eval += 1
        tr_ = est.trend
        ed = est.dist_m
        if ed is not None and ed == ed:
            if ed < 0.1:
                ed = 0.1
            elif ed > 1000.0:
                ed = 1000.0
            e = math.log(ed / d)
            se += e * e
            n_d += 1
        if px.zone_changed:
            ef += 1
        if tz.changed:
            tf += 1
        if vr > 0.3 or vr < -0.3:
            n_mov += 1
            want = 1 if vr < 0.0 else -1
            if tr_ != 0:
                cov += 1
                if tr_ == want:
                    ok += 1
            if px.trend != 0:
                cov_g += 1
                if px.trend == want:
                    ok_g += 1
        elif -0.1 < vr < 0.1:
            n_still += 1
            if tr_ != 0:
                false += 1
            if px.trend != 0:
                false_g += 1
        if turn_t is not None and lag is None and t >= turn_t:
            if tr_ == 1:
                if streak is None:
                    streak = t
                elif t - streak >= 1.0 - 1e-6:
                    lag = streak - turn_t
            else:
                streak = None
    span_min = (t_last - WARMUP_S) / 60.0 if t_last > WARMUP_S else 0.0
    m = {
        "est": cls.name, "scenario": tr.scenario, "profile": tr.prof, "seed": tr.seed,
        "dist_log_rmse": math.sqrt(se / n_d) if n_d else None,
        "dist_cov": n_d / n_eval if n_eval else 0.0,
        "trend_acc": ok / n_mov if n_mov else None,
        "trend_cov": cov / n_mov if n_mov else None,
        "false_trend": false / n_still if n_still else None,
        "false_verdict": false_g / n_still if n_still else None,
        "gated_acc": ok_g / n_mov if n_mov else None,
        "gated_cov": cov_g / n_mov if n_mov else None,
        "reversal_lag_s": None, "reversal_miss": None,
        "zone_flips": (max(0, ef - tf) / span_min) if span_min > 0 else None,
        "us_per_update": us / calls if calls else 0.0,
        "us_per_packet": us_pk / n_pk if n_pk else None,
    }
    if turn_t is not None:
        if lag is None:
            m["reversal_lag_s"] = tr.duration - turn_t
            m["reversal_miss"] = 1
        else:
            m["reversal_lag_s"] = lag
            m["reversal_miss"] = 0
    return m


METRICS = ["dist_log_rmse", "dist_cov", "trend_acc", "trend_cov", "false_trend", "false_verdict",
           "gated_acc", "gated_cov", "reversal_lag_s", "reversal_miss", "zone_flips", "us_per_update", "us_per_packet"]


def mean_metrics(rows):
    """Mean of each metric over rows, ignoring None (metric not applicable)."""
    out = {}
    for k in METRICS:
        s = 0.0
        n = 0
        for r in rows:
            v = r[k]
            if v is not None:
                s += v
                n += 1
        out[k] = s / n if n else None
    out["runs"] = len(rows)
    return out


def composite(m):
    """Weighted score in [0, 1]; weights renormalised over available terms."""
    terms = []
    if m["dist_log_rmse"] is not None:
        terms.append((W_DIST, 1.0 - min(1.0, m["dist_log_rmse"])))
    if m["trend_acc"] is not None:
        terms.append((W_TREND, m["trend_acc"]))
    if m["false_trend"] is not None:
        terms.append((W_FALSE, 1.0 - m["false_trend"]))
    if m["zone_flips"] is not None:
        terms.append((W_FLIPS, 1.0 - min(1.0, m["zone_flips"] / 10.0)))
    w = 0.0
    s = 0.0
    for wi, v in terms:
        w += wi
        s += wi * v
    return s / w if w else 0.0


def run(ests, scen_names, profiles, seeds, imu="typical", wrap=False, duration=None, progress=None,
        rate_hz=10.0):
    """Returns list of per-run metric dicts."""
    classes = [load(e) for e in ests]
    rows = []
    for sc in scen_names:
        for pr in profiles:
            for sd in seeds:
                tr = record(sc, pr, sd, imu, duration, wrap, rate_hz)
                for c in classes:
                    rows.append(evaluate(c, tr))
            if progress:
                progress(sc, pr)
    return rows


def _f(v, nd=3):
    if v is None:
        return "-"
    return ("%." + str(nd) + "f") % v


def _group(rows, key):
    g = {}
    order = []
    for r in rows:
        k = r[key]
        if k not in g:
            g[k] = []
            order.append(k)
        g[k].append(r)
    return order, g


def report(rows, header=""):
    """Markdown report: summary table + score by scenario and by profile."""
    L = []
    if header:
        L.append(header)
        L.append("")
    L.append("score = %.2f*(1-min(1,dist_log_rmse)) + %.2f*trend_acc + %.2f*(1-false_trend)"
             " + %.2f*(1-min(1,zone_flips/10)); weights renormalised when a term is n/a."
             % (W_DIST, W_TREND, W_FALSE, W_FLIPS))
    L.append("")
    L.append("false_verdict, gated_acc and gated_cov score the game's gated trend "
             "(finder.proximity) as false_trend, trend_acc and trend_cov score the estimator's raw "
             "one (not in the score); zone_flips counts the game's zone changes (hysteresis, dwell) "
             "beyond those of the true distance through the same zone rules.")
    L.append("")
    L.append("| estimator | score | dist_log_rmse | dist_cov | trend_acc | trend_cov | false_trend"
             " | false_verdict | gated_acc | gated_cov | reversal_lag_s | rev_miss | zone_flips/min"
             " | us/update | us/pkt | runs |")
    L.append("|---" * 16 + "|")
    eorder, eg = _group(rows, "est")
    summ = []
    for e in eorder:
        m = mean_metrics(eg[e])
        summ.append((composite(m), e, m))
    summ.sort(key=lambda x: -x[0])
    for s, e, m in summ:
        L.append("| %s | %s | %s | %s | %s | %s | %s | %s | %s | %s | %s | %s | %s | %s | %s | %d |" % (
            e, _f(s), _f(m["dist_log_rmse"]), _f(m["dist_cov"], 2), _f(m["trend_acc"]),
            _f(m["trend_cov"], 2), _f(m["false_trend"]), _f(m["false_verdict"]),
            _f(m["gated_acc"]), _f(m["gated_cov"], 2),
            _f(m["reversal_lag_s"], 2), _f(m["reversal_miss"], 2), _f(m["zone_flips"], 2),
            _f(m["us_per_update"], 1), _f(m["us_per_packet"], 1), m["runs"]))
    tgt = []
    for _, e, _m in summ:
        for pr in ("typical", "harsh"):
            fv = mean_metrics([r for r in eg[e] if r["scenario"] == "orbit" and r["profile"] == pr])
            if fv["false_verdict"] is not None:
                tgt.append("%s/%s %s %s" % (e, pr, _f(fv["false_verdict"]),
                                            "ok" if fv["false_verdict"] < VERDICT_MAX else "FAIL"))
    if tgt:
        L.append("")
        L.append("ui-spec §5.5 target, false_verdict < %.2f on orbit: %s" % (VERDICT_MAX, ", ".join(tgt)))
    for key, title in (("scenario", "Score by scenario"), ("profile", "Score by profile")):
        korder, _ = _group(rows, key)
        L.append("")
        L.append("%s (score / dist_log_rmse / trend_acc / false_trend)" % title)
        L.append("")
        L.append("| estimator | " + " | ".join(korder) + " |")
        L.append("|---" * (len(korder) + 1) + "|")
        for _, e, _m in summ:
            _, g = _group(eg[e], key)
            cells = []
            for k in korder:
                m = mean_metrics(g[k])
                cells.append("%s / %s / %s / %s" % (_f(composite(m), 2), _f(m["dist_log_rmse"], 2),
                                                   _f(m["trend_acc"], 2), _f(m["false_trend"], 2)))
            L.append("| " + e + " | " + " | ".join(cells) + " |")
    return "\n".join(L) + "\n"


def parse_seeds(s):
    out = []
    for part in s.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part[1:]:
            i = part.index("-", 1)
            a = int(part[:i])
            b = int(part[i + 1:])
            out.extend(range(a, b + 1))
        else:
            out.append(int(part))
    return out


def _csv(s):
    return [x.strip() for x in s.split(",") if x.strip()]


OPTIONS = {"quick": False, "est": None, "seeds": None, "profiles": None, "scenarios": None,
           "imu": "typical", "out": None, "wrap": False, "duration": None, "quiet": False,
           "rate": None}


def parse_args(args):
    """``--key value`` / ``--key=value`` into a copy of ``OPTIONS`` (tools/cli.py)."""
    return _parse_args(args, OPTIONS, ("quick", "wrap", "quiet"))


def main(args):
    o = parse_args(args)
    ests = _csv(o["est"]) if o["est"] else list(NAMES)
    if o["quick"]:
        seeds, profiles, scen = QUICK_SEEDS, QUICK_PROFILES, _sc.QUICK
    else:
        seeds, profiles, scen = list(range(10)), PROFILE_NAMES, _sc.NAMES
    if o["seeds"]:
        seeds = parse_seeds(o["seeds"])
        if not seeds:
            raise ValueError("--seeds is empty")
    if o["profiles"]:
        profiles = _csv(o["profiles"])
    if o["scenarios"]:
        scen = _csv(o["scenarios"])
    dur = float(o["duration"]) if o["duration"] else None
    if dur is not None and dur <= WARMUP_S:
        raise ValueError("--duration must be > %g s (the warm-up)" % WARMUP_S)

    def prog(sc, pr):
        if not o["quiet"]:
            sys.stderr.write("  %s/%s done\n" % (sc, pr))

    rate = float(o["rate"]) if o["rate"] else 10.0
    rows = run(ests, scen, profiles, seeds, o["imu"], o["wrap"], dur, prog, rate)
    hdr = "Bake-off: est=%s scenarios=%s profiles=%s seeds=%s imu=%s%s" % (
        ",".join(ests), ",".join(scen), ",".join(profiles),
        o["seeds"] or ("%d-%d" % (seeds[0], seeds[-1]) if len(seeds) > 1 else str(seeds[0])),
        o["imu"], " wrap" if o["wrap"] else "")
    md = report(rows, hdr)
    print(md)
    if o["out"]:
        f = open(o["out"], "w")
        f.write(md)
        f.close()
    return rows


if __name__ == "__main__":
    main(sys.argv[1:])
