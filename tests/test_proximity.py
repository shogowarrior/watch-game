import math
from finder import proximity as P
from finder.compat import ticks_add, ticks_diff
from finder.proximity import (prox, intensity, zone_for, ZoneTracker, band, band_raw,
                              TrendGate, Proximity, DeliveryMeter, FAR, NEAR, WARM, HOT)
from finder.estimators import make
from finder.estimators.base import MotionInfo, ACT_STILL, ACT_WALK, ACT_RUN, ACT_UNKNOWN
from sim.rng import Rng
from tests import Skip
from tests.est_helpers import STILL


# ---- §5.1 ------------------------------------------------------------------

def test_prox_reference_values():
    for d, p in ((55, 0.03), (28, 0.22), (14, 0.43), (7, 0.63), (3, 0.88)):
        assert abs(prox(d) - p) < 0.006, (d, prox(d))
    assert prox(60.0) == 0.0 and prox(500.0) == 0.0
    assert prox(2.0) > 0.9999 and prox(1.0) == 1.0 and prox(0.0) == 1.0
    assert prox(P.PROX_MIN_M / 10.0) == 1.0 and prox(-5.0) == 1.0
    last = 2.0
    for d in (1, 2, 3, 5, 8, 13, 21, 34, 55, 89):
        v = prox(d)
        assert 0.0 <= v <= 1.0 and v <= last
        last = v


def test_intensity_smoothing_tau():
    assert abs(intensity(0.0, 1.0, 1500) - (1.0 - math.exp(-1.0))) < 1e-6
    assert intensity(0.3, 0.9, 0) == 0.3
    assert intensity(0.3, 0.9, -50) == 0.3
    a = intensity(intensity(0.2, 0.8, 750), 0.8, 750)
    assert abs(a - intensity(0.2, 0.8, 1500)) < 1e-6
    i = 0.0
    for _ in range(100):
        i = intensity(i, 0.5, 100)
    assert abs(i - 0.5) < 0.01
    assert abs(intensity(1.0, 0.0, 1500, tau_ms=3000) - math.exp(-0.5)) < 1e-6


def test_proximity_intensity_jumps_on_first_fix_then_smooths():
    px = Proximity()
    px.update(0, 14.0)
    assert abs(px.intensity - prox(14.0)) < 1e-9
    px.update(1500, 3.0)
    want = prox(14.0) + (prox(3.0) - prox(14.0)) * (1.0 - math.exp(-1.0))
    assert abs(px.intensity - want) < 1e-6


# ---- §5.2 ------------------------------------------------------------------

def test_zone_for_first_fix():
    assert zone_for(80) == FAR and zone_for(28.1) == FAR
    assert zone_for(28.0) == NEAR and zone_for(14.1) == NEAR
    assert zone_for(14.0) == WARM and zone_for(7.1) == WARM
    assert zone_for(7.0) == HOT and zone_for(0.5) == HOT


def test_zone_first_fix_jumps_without_dwell():
    zt = ZoneTracker()
    assert zt.update(0, None) is None
    assert zt.update(100, 5.0) == HOT and zt.changed == 0


def _hold(zt, t0, t1, d, step=100):
    t = t0
    while t <= t1:
        zt.update(t, d)
        t += step
    return zt.zone


def test_zone_enter_dwell_each_boundary():
    for k, (enter, dwell) in enumerate(zip((28.0, 14.0, 7.0), (3000, 2000, 1500))):
        zt = ZoneTracker()
        zt.update(0, 100.0 if k == 0 else (20.0 if k == 1 else 10.0))
        assert zt.zone == k
        zt.update(1000, enter)          # condition starts at t = 1000
        _hold(zt, 1000, 1000 + dwell - 100, enter)
        assert zt.zone == k, (k, zt.zone)
        zt.update(1000 + dwell, enter)
        assert zt.zone == k + 1 and zt.changed == 1


def test_zone_exit_dwell_symmetric():
    for k, (exit_, dwell) in enumerate(zip((36.0, 18.0, 9.0), (3000, 2000, 1500))):
        zt = ZoneTracker()
        zt.update(0, (20.0, 10.0, 5.0)[k])
        assert zt.zone == k + 1
        _hold(zt, 1000, 1000 + dwell - 100, exit_)
        assert zt.zone == k + 1
        zt.update(1000 + dwell, exit_)
        assert zt.zone == k and zt.changed == -1


def test_zone_hysteresis_gap_holds():
    zt = ZoneTracker()
    zt.update(0, 40.0)
    assert _hold(zt, 100, 30000, 30.0) == FAR      # 28 < d < 36: stays FAR
    zt = ZoneTracker()
    zt.update(0, 20.0)
    assert _hold(zt, 100, 30000, 30.0) == NEAR     # and stays NEAR
    zt = ZoneTracker()
    zt.update(0, 5.0)
    assert _hold(zt, 100, 30000, 8.0) == HOT


def test_zone_broken_condition_restarts_dwell():
    zt = ZoneTracker()
    zt.update(0, 50.0)
    _hold(zt, 100, 2900, 20.0)
    zt.update(3000, 30.0)           # blip back into the gap resets the timer
    _hold(zt, 3100, 6000, 20.0)
    assert zt.zone == FAR
    zt.update(6100, 20.0)
    assert zt.zone == NEAR


def test_zone_one_step_per_decision_and_dwell_restarts():
    zt = ZoneTracker()
    zt.update(0, 60.0)
    changes = []
    t = 100
    while t <= 12000:
        zt.update(t, 2.0)
        if zt.changed:
            changes.append((t, zt.zone))
        t += 100
    assert changes == [(3100, NEAR), (5100, WARM), (6600, HOT)], changes


def test_zone_rearm_jumps_again():
    zt = ZoneTracker()
    zt.update(0, 60.0)
    zt.rearm()
    assert zt.zone == FAR
    zt.update(100, 5.0)
    assert zt.zone == HOT and zt.changed == 1


# ---- §5.4 ------------------------------------------------------------------

def test_band_raw_edges():
    exp = ((1.0, 0), (3.49, 0), (3.5, 1), (6.9, 1), (7.0, 2), (13.9, 2), (14.0, 3),
           (27.9, 3), (28.0, 4), (54.9, 4), (55.0, 5), (200.0, 5))
    for d, b in exp:
        assert band_raw(d) == b, (d, band_raw(d), b)
    assert P.BAND_LABELS[band(None, 20.0, None)] == "~20"


def test_band_hysteresis_15pct():
    # up from ~20 (3) only at >= 28 * 1.15 = 32.2
    assert band(3, 30.0, None) == 3
    assert band(3, 32.1, None) == 3
    assert band(3, 32.3, None) == 4
    # down from ~40 (4) only below 28 / 1.15 = 24.35
    assert band(4, 25.0, None) == 4
    assert band(4, 24.3, None) == 3
    # multi-step moves still happen in one call
    assert band(5, 2.0, None) == 0
    assert band(0, 100.0, None) == 5
    # no chatter across an edge with +-10 % noise
    b = band(None, 14.0, None)
    for i in range(200):
        d = 14.0 * (1.1 if i % 2 else 0.91)
        b = band(b, d, None)
    assert b == 3


def test_band_zone_clamp():
    assert band(3, 60.0, NEAR) == 3
    assert band(3, 2.0, NEAR) == 3
    assert band(None, 8.0, HOT) == 1
    assert band(None, 1.0, FAR) == 4
    assert band(None, 100.0, FAR) == 5
    assert band(2, 2.0, WARM) == 2


def test_band_trend_monotonic():
    assert band(2, 20.0, None, trend=1) == 2       # warmer never shows farther
    assert band(3, 9.0, None, trend=-1) == 3       # colder never shows closer
    assert band(3, 9.0, None, trend=1) == 2
    assert band(2, 20.0, None, trend=-1) == 3
    # zone wins over the trend rule
    assert band(2, 20.0, NEAR, trend=1) == 3


# ---- §5.5 trend gate --------------------------------------------------------

def _walk_gate(g, t0, secs, rssi0, slope_db_s, trend, conf, act=ACT_WALK, zone=NEAR,
               sd=2.0, delivery=1.0, step=100, log=None):
    t = t0
    n = secs * 1000 // step
    for i in range(n):
        r = rssi0 + slope_db_s * (i * step / 1000.0)
        g.update(t, trend, conf, r, sd, act, zone, delivery)
        if log is not None:
            log.append((t, g.trend))
        t = ticks_add(t, step)
    return t, rssi0 + slope_db_s * (n * step / 1000.0)


def _first_nonzero(log):
    for t, v in log:
        if v:
            return t, v
    return None


def test_trend_needs_full_window_and_two_evals():
    g = TrendGate()
    log = []
    _walk_gate(g, 0, 14, -80.0, 0.8, 1, 0.9, log=log)
    t, v = _first_nonzero(log)
    # evals at 0,1000,...; first full 8 s window at t=8000, second eval at 9000
    assert v == 1 and t == 9000, (t, v)
    assert g.trend == 1


def test_trend_requires_conf_and_delta():
    g = TrendGate()
    _walk_gate(g, 0, 20, -80.0, 0.8, 1, 0.59)
    assert g.trend == 0                              # conf below 0.6
    g = TrendGate()
    _walk_gate(g, 0, 20, -80.0, 0.3, 1, 0.9)         # 2.4 dB / 8 s < 3 dB
    assert g.trend == 0
    g = TrendGate()
    _walk_gate(g, 0, 20, -80.0, -0.8, 1, 0.9)        # delta disagrees with estimator
    assert g.trend == 0
    g = TrendGate()
    _walk_gate(g, 0, 20, -60.0, -0.8, -1, 0.9)
    assert g.trend == -1


def test_trend_only_while_walking_or_running():
    for act, ok in ((ACT_STILL, False), (ACT_UNKNOWN, False), (ACT_WALK, True),
                    (ACT_RUN, True)):
        g = TrendGate()
        _walk_gate(g, 0, 12, -80.0, 0.8, 1, 0.9, act=act)
        assert (g.trend == 1) == ok, act
    g = TrendGate()
    t, r = _walk_gate(g, 0, 12, -80.0, 0.8, 1, 0.9)
    assert g.trend == 1
    g.update(t, 1, 0.9, r, 2.0, ACT_STILL, NEAR, 1.0)   # stops: hidden at once
    assert g.trend == 0 and not g.trend_strong


def test_trend_never_in_hot_or_before_fix():
    g = TrendGate()
    _walk_gate(g, 0, 20, -60.0, 0.8, 1, 0.95, zone=HOT)
    assert g.trend == 0
    g = TrendGate()
    _walk_gate(g, 0, 20, -60.0, 0.8, 1, 0.95, zone=None)
    assert g.trend == 0
    for z in (FAR, NEAR, WARM):
        g = TrendGate()
        _walk_gate(g, 0, 12, -80.0, 0.8, 1, 0.9, zone=z)
        assert g.trend == 1


def test_unreliable_forces_trend_off():
    g = TrendGate()
    t, r = _walk_gate(g, 0, 12, -80.0, 0.8, 1, 0.9)
    assert g.trend == 1 and not g.unreliable
    g.update(t, 1, 0.9, r, P.UNRELIABLE_SD_DB + 0.5, ACT_WALK, NEAR, 1.0)
    assert g.unreliable and g.trend == 0
    g = TrendGate()
    t, r = _walk_gate(g, 0, 12, -80.0, 0.8, 1, 0.9)
    g.update(t, 1, 0.9, r, 2.0, ACT_WALK, NEAR, 0.49)
    assert g.unreliable and g.trend == 0
    g = TrendGate()
    _walk_gate(g, 0, 20, -80.0, 0.8, 1, 0.9, delivery=0.4)
    assert g.unreliable and g.trend == 0
    g = TrendGate()
    _walk_gate(g, 0, 20, -80.0, 0.8, 1, 0.9, sd=P.UNRELIABLE_SD_DB, delivery=0.5)   # edges are OK
    assert not g.unreliable and g.trend == 1


def test_trend_strong_rule():
    g = TrendGate()
    _walk_gate(g, 0, 12, -80.0, 0.8, 1, 0.9)         # 6.4 dB / 8 s, conf 0.9
    assert g.trend == 1 and g.trend_strong
    g = TrendGate()
    _walk_gate(g, 0, 12, -80.0, 0.8, 1, 0.8)         # conf < 0.85
    assert g.trend == 1 and not g.trend_strong
    g = TrendGate()
    _walk_gate(g, 0, 12, -80.0, 0.5, 1, 0.95)        # 4 dB < 6 dB
    assert g.trend == 1 and not g.trend_strong
    g = TrendGate()
    _walk_gate(g, 0, 12, -60.0, -0.9, -1, 0.95)
    assert g.trend == -1 and g.trend_strong


def test_trend_flip_limit_5s():
    g = TrendGate()
    log = []
    t, r = _walk_gate(g, 0, 12, -80.0, 1.0, 1, 0.9, log=log)
    assert g.trend == 1
    # abrupt reversal: estimator says colder and the RSSI falls fast
    _walk_gate(g, t, 20, r, -3.0, -1, 0.9, log=log)
    assert g.trend == -1
    changes = []
    prev = 0
    for tt, v in log:
        if v != prev:
            changes.append((tt, v))
            prev = v
    # + shown, hidden, then - no sooner than 5 s after the hide
    assert [v for _, v in changes] == [1, 0, -1], changes
    assert changes[2][0] - changes[1][0] >= 5000, changes


def test_trend_same_sign_reshow_not_blocked():
    g = TrendGate()
    t, r = _walk_gate(g, 0, 12, -80.0, 1.0, 1, 0.9)
    assert g.trend == 1
    g.update(t, 1, 0.9, r, 2.0, ACT_STILL, NEAR, 1.0)
    assert g.trend == 0
    log = []
    _walk_gate(g, t + 100, 3, r, 1.0, 1, 0.9, log=log)
    t1, v = _first_nonzero(log)
    assert v == 1 and t1 - t <= 2200


def test_trend_gap_refills_window():
    g = TrendGate()
    t, r = _walk_gate(g, 0, 12, -80.0, 1.0, 1, 0.9)
    assert g.trend == 1
    log = []
    _walk_gate(g, t + 10000, 12, r, 1.0, 1, 0.9, log=log)
    t1, _ = _first_nonzero(log)
    assert t1 - (t + 10000) >= 8000


# ---- combined invariants ----------------------------------------------------

def _run_random(seed, secs=600, step=100):
    rng = Rng(seed)
    px = Proximity()
    d = 70.0
    v = -1.3            # m/s, walking towards
    rssi = None
    t = 0
    walking = True
    hist = []
    while t < secs * 1000:
        if rng.random() < 0.001:
            v = -v
        if rng.random() < 0.002:
            walking = not walking
        if walking:
            d += v * step / 1000.0
        if d < 1.0:
            d, v = 1.0, abs(v)
        if d > 90.0:
            d, v = 90.0, -abs(v)
        noise = (rng.random() - 0.5) * 8.0
        meas = -45.0 - 22.0 * math.log10(d) + noise
        rssi = meas if rssi is None else rssi + 0.15 * (meas - rssi)
        est_d = math.pow(10.0, (-45.0 - rssi) / 22.0)
        tr = (-1 if v > 0 else 1) if walking else 0
        conf = 0.5 + 0.5 * rng.random()
        sd = 7.0 if rng.random() < 0.01 else 2.5
        px.update(t, est_d, rssi, sd, tr, conf, ACT_WALK if walking else ACT_STILL, 0.9)
        hist.append((t, px.zone, px.zone_changed, px.band_idx, px.trend, px.trend_strong,
                     px.unreliable))
        t += step
    return hist


def test_invariants_random_walks():
    dwell = P.ZONE_DWELL_MS
    for seed in (1, 7, 42):
        hist = _run_random(seed)
        prev_b = None
        prev_tr = 0
        last_change_t = None
        last_sign = 0
        last_zone_t = None
        n_tr = n_zone = 0
        for t, z, zc, b, tr, strong, unrel in hist:
            lo, hi = P.ZONE_BANDS[z]
            assert lo <= b <= hi, (t, z, b)                  # band never contradicts zone
            if z == HOT or unrel:
                assert tr == 0, (t, z, tr, unrel)
            if strong:
                assert tr != 0
            if prev_b is not None:
                if tr > 0:
                    assert b <= prev_b, (t, b, prev_b)       # never contradicts trend
                if tr < 0:
                    assert b >= prev_b, (t, b, prev_b)
            if tr != prev_tr:
                if tr and tr == -last_sign:
                    assert t - last_change_t >= 5000, (t, last_change_t)
                last_change_t = t
                if tr:
                    last_sign = tr
                    n_tr += 1
            if zc:
                n_zone += 1
                assert abs(zc) == 1
                if last_zone_t is not None:
                    k = z if zc < 0 else z - 1
                    assert t - last_zone_t >= dwell[k], (t, last_zone_t, z)
                last_zone_t = t
            prev_b, prev_tr = b, tr
        assert n_tr > 0 and n_zone >= 3, (seed, n_tr, n_zone)


def test_proximity_forces_trend_off_when_zone_clamps_band():
    px = Proximity()
    t = 0
    # WARM, walking closer with a shown warmer trend
    rssi = -45.0 - 22.0 * math.log10(12.0)
    for i in range(120):
        px.update(t, 12.0, rssi + 0.1 * i, 2.0, 1, 0.9, ACT_WALK, 1.0)
        t += 100
    assert px.zone == WARM and px.trend == 1 and px.band == "~10"
    # the distance estimate jumps past the WARM exit edge while the trend holds
    for _ in range(30):
        px.update(t, 25.0, rssi + 12.0, 2.0, 1, 0.9, ACT_WALK, 1.0)
        t += 100
        if px.zone == NEAR:
            break
    assert px.zone == NEAR and px.band == "~20"
    assert px.trend == 0


def test_proximity_before_fix_and_rearm():
    px = Proximity()
    px.update(0, None, None, None, 0, 0.0, ACT_WALK)
    assert px.zone is None and px.band is None and px.trend == 0
    px.update(100, 30.0)
    assert px.zone == FAR and px.band == "~40"
    px.rearm()
    assert px.zone == FAR                             # last known kept for LINK_LOST
    px.update(300, 3.2)
    assert px.zone == HOT and px.band == "<3" and px.zone_changed == 1   # no hysteresis from ~40
    assert abs(px.intensity - prox(3.2)) < 1e-9       # jumps, no smoothing
    px.reset()
    assert px.zone is None and px.band is None


def test_update_est_reads_estimator_fields():
    class E:
        rssi_f = -70.0
        rssi_var = 4.0
        noise_db = 9.0      # learnt packet-to-packet noise -> unreliable
        dist_m = 12.0
        trend = 1
        trend_conf = 0.9
    px = Proximity()
    px.update_est(0, E(), ACT_WALK, 1.0)
    assert px.zone == WARM and px.unreliable
    E.noise_db = 2.0
    px.update_est(100, E(), ACT_WALK, 1.0)
    assert not px.unreliable
    E.noise_db = None   # no packet yet: §5.5 is on noise_db only, not rssi_var
    E.rssi_var = 100.0
    px.update_est(200, E(), ACT_WALK, 1.0)
    assert not px.unreliable
    px = Proximity()
    px.update_est(0, make(), ACT_STILL, 1.0)        # fresh estimator, nothing measured
    assert not px.unreliable


def test_unreliable_from_default_estimator_noise():
    """R-14: deep packet-to-packet fades read unreliable, a smooth channel does not."""
    for amp, want in ((10.0, True), (1.0, False)):
        est = make()
        px = Proximity()
        for i in range(300):
            t = 100 * i
            est.update(t, -70.0 + (amp if i & 1 else -amp), None, STILL, STILL)
            px.update_est(t, est, ACT_STILL, 1.0)
        assert px.unreliable == want, (amp, est.noise_db)


def test_update_est_with_real_estimator():
    est = make()
    px = Proximity()
    t = 0
    for i in range(200):
        d = 40.0 - 0.13 * i
        est.update(t, -45.0 - 22.0 * math.log10(d), None, MotionInfo(ACT_WALK, 1.8, i // 5), STILL)
        px.update_est(t, est, ACT_WALK, 1.0)
        t += 100
    assert px.zone in (NEAR, WARM) and px.band in ("~10", "~20")
    assert 0.0 < px.intensity < 1.0


# ---- delivery ---------------------------------------------------------------

def test_delivery_meter():
    m = DeliveryMeter(expected_hz=10)
    assert m.ratio(0) == 1.0
    for i in range(50):
        m.note(i * 100)
    assert abs(m.ratio(4999) - 1.0) < 0.03
    for i in range(25):                 # half rate for the next 5 s
        m.note(5000 + i * 200)
    r = m.ratio(9999)
    assert 0.45 < r < 0.55, r
    assert m.ratio(30000) == 0.0        # silence
    m.reset()
    assert m.ratio(40000) == 1.0
    m = DeliveryMeter(expected_hz=10)
    m.expire(40000)                     # no history: nothing to roll
    assert m._t is None
    m.note(41000)
    m.expire(41000 + 3000)              # a silence rolls the window: its stamp keeps up
    assert m._t == 44000 and m.ratio(44000) < 0.1


def test_ticks_wrap():
    """Hard rule 1: zone dwell, trend eval clock and delivery buckets across the 2^30 ms wrap."""
    t0 = ticks_add(0, -1500)
    zt = ZoneTracker()
    zt.update(t0, 50.0)
    for i in range(1, 32):
        zt.update(ticks_add(t0, 100 * i), 20.0)
    assert zt.zone == NEAR
    g = TrendGate()
    log = []
    t0 = ticks_add(0, -5000)
    _walk_gate(g, t0, 14, -80.0, 0.8, 1, 0.9, log=log)
    t, v = _first_nonzero(log)
    # first verdict at 9000 ms, as in test_trend_needs_full_window_and_two_evals
    assert v == 1 and ticks_diff(t, t0) == 9000, (t, v)
    m = DeliveryMeter(expected_hz=10)
    t0 = ticks_add(0, -3000)
    for i in range(60):
        m.note(ticks_add(t0, 100 * i))
    assert m.ratio(ticks_add(t0, 12000)) == 0.0


# ---- tokens cross-checks ---------------------------------------------------

def test_delivery_window_matches_tokens_json():
    try:
        import json
        f = open("docs/design/tokens.json")
    except (ImportError, OSError):
        raise Skip("no docs/design/tokens.json (wasm copies code only)")
    try:
        tok = json.load(f)
    finally:
        f.close()
    # delivery window: "packet delivery ratio over last 5 s"
    metric = tok["glyphs"]["link_bars"]["metric"]
    assert ("last %d s" % (P.DELIVERY_WINDOW_MS // 1000)) in metric, metric
