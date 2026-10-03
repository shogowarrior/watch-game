"""Guided 360-degree body-turn scan (ui-spec 6 SCANNING, tokens thresholds.scan).

Phases: ``ready`` (flat check + 3 s countdown that holds until flat),
``sweep`` (12 s of *active* time; the wedge turns at 30 deg/s and holds while
a fault pauses it) and ``result`` (theta + s0, or a no-fix reason).

Direction comes from RAW per-packet RSSI -- own RSSI and the partner's
reported ``rssi_last`` (pass that, not ``rssi_filt``: a filtered value lags
and skews theta) -- each tagged with the wedge angle phi at arrival and fitted
with the first circular harmonic ``y = a0_src + a1*cos(phi - theta)``. Each
source gets its own a0 (the two radios differ by a few dB); with one source
this is exactly the spec's ``a0 + a1*cos(phi - theta)``.

Drive it with ``on_motion`` / ``on_packet`` / ``on_peer`` / ``on_input`` as
data arrives and ``update(t_ms)`` once per logic frame (10 Hz), then read the
render outputs (``sub``, ``countdown``, ``top_text``, ``word``, ``toast``,
``sweep()``, ``mirror``, ``pop_haptic()``, ``result``). Times are ticks ms.
"""

import math
from array import array
from finder.compat import const, ticks_diff, ticks_add
from finder.estimators.base import ACT_WALK, ACT_RUN

# ---- tokens.thresholds.scan (v0.2.0) ---------------------------------------
DURATION_MS = const(12000)
READY_MS = const(3000)
DEG_PER_S = 30.0
MIN_P2T_DB = 4.0          # no fix if 2*a1 below this
MAX_S0_DEG = 45.0
FLOOR_DEG = 20.0          # tokens.thresholds.arrow.scan_floor_deg
TILT_FAULT_DEG = 35.0
TILT_FAULT_MS = const(500)
ABORT_STEPS = const(8)
ABORT_PAUSE_MS = const(6000)
BINS = const(12)
MIN_PACKETS = const(4)
# ---- ui-spec 6 SCANNING ----------------------------------------------------
FLAT_DEG = 20.0           # face-up within 20 deg = flat
FLAT_OFF_DEG = 30.0       # hysteresis (as finder.motion face_up)
FLAT_HOLD_MS = const(500)
WALK_STEPS = const(3)     # >= 3 steps in 2 s = walking
WALK_WINDOW_MS = const(2000)
PEER_WALK_PENALTY_MS = const(2000)
PEER_WALK_FAIL_MS = const(4000)
PEER_WALK_DEG = 15.0
PEER_STALE_MS = const(1500)
TICK_EVERY_DEG = 45.0
BLINK_MS = const(800)     # best bin blinks twice (200 on/off)
MORPH_MS = const(400)
BLANK_AFTER_MS = const(150)   # tokens.haptics.blanking_ms_after_pulse
MAX_DT_MS = const(1000)   # a stalled loop never jumps the wedge further
MIN_FIT_N = const(12)
CAP = const(640)          # 12 s x 20 Hz x 2 sources + margin

SRC_OWN = const(0)
SRC_PEER = const(1)

READY = "ready"
SWEEP = "sweep"
RESULT = "result"

FAULT_TILT = "tilt"
FAULT_WALK = "walk"

R_NO_FIX = "no_fix"
R_FRIEND_MOVED = "friend_moved"
R_ABORT = "abort"
R_CANCEL = "cancel"
TOASTS = {R_NO_FIX: "NO FIX, TRY AGAIN", R_FRIEND_MOVED: "FRIEND MOVED",
          R_ABORT: "SCAN STOPPED"}

HINT_CHEST = "HOLD AT CHEST"
HINT_FLAT = "HOLD FLAT"
HINT_STILL = "STAND STILL"
WORD_TURN = "TURN RIGHT"

# tokens.haptics.patterns total length (ms), for self-blanking
HAPTIC_MS = {"TICK": 60, "DOUBLE": 200, "CLOSER": 440, "NOPE": 750}
_HPRIO = {"TICK": 0, "DOUBLE": 1, "CLOSER": 2, "NOPE": 3}

# finder.gestures codes (copied: that module is edited elsewhere)
G_TAP = const(1)
G_DOUBLE_TAP = const(2)

_D2R = math.pi / 180.0
_R2D = 180.0 / math.pi


def tilt_from_gravity(gx, gy, gz, z_sign=1):
    """Angle (deg) between the display normal and up; 0 = face-up flat."""
    n = math.sqrt(gx * gx + gy * gy + gz * gz)
    if n <= 0.0:
        return 180.0
    c = gz * (1.0 if z_sign >= 0 else -1.0) / n
    c = -1.0 if c < -1.0 else 1.0 if c > 1.0 else c
    return math.acos(c) * _R2D


def fit_harmonic(phi, val, src, n):
    """LSQ fit of ``val = a0[src] + A cos(phi) + B sin(phi)`` over n samples.

    Two-pass (demeaned per source) so float32 on the ESP32 stays accurate.
    Returns (a1_db, theta_deg 0..360, sigma_res_db, n) or None if degenerate.
    """
    if n < MIN_FIT_N:
        return None
    k0 = k1 = 0
    y0 = y1 = c0 = c1 = s0 = s1 = 0.0
    for i in range(n):
        p = phi[i] * _D2R
        c = math.cos(p)
        s = math.sin(p)
        if src[i]:
            k1 += 1
            y1 += val[i]
            c1 += c
            s1 += s
        else:
            k0 += 1
            y0 += val[i]
            c0 += c
            s0 += s
    if k0:
        y0 /= k0
        c0 /= k0
        s0 /= k0
    if k1:
        y1 /= k1
        c1 /= k1
        s1 /= k1
    scc = sss = scs = syc = sys_ = syy = 0.0
    for i in range(n):
        p = phi[i] * _D2R
        if src[i]:
            c = math.cos(p) - c1
            s = math.sin(p) - s1
            y = val[i] - y1
        else:
            c = math.cos(p) - c0
            s = math.sin(p) - s0
            y = val[i] - y0
        scc += c * c
        sss += s * s
        scs += c * s
        syc += y * c
        sys_ += y * s
        syy += y * y
    det = scc * sss - scs * scs
    tr = scc + sss
    if tr <= 0.0 or det <= 1e-3 * tr * tr:
        return None          # angular coverage too narrow to fit a harmonic
    a = (syc * sss - sys_ * scs) / det
    b = (sys_ * scc - syc * scs) / det
    sse = syy - a * syc - b * sys_
    dof = n - 2 - (1 if k0 else 0) - (1 if k1 else 0)
    if dof < 1:
        return None
    sres = math.sqrt(sse / dof) if sse > 0.0 else 0.0
    a1 = math.sqrt(a * a + b * b)
    th = math.atan2(b, a) * _R2D
    if th < 0.0:
        th += 360.0
    return a1, th, sres, n


def sigma_fit_deg(a1, sigma_res, n):
    """deg(sigma_res / (a1 * sqrt(N/2))): theta sd of a harmonic fit."""
    if a1 <= 0.0 or n <= 0:
        return 180.0
    return sigma_res / (a1 * math.sqrt(n * 0.5)) * _R2D


def evaluate(a1, sigma_res, n, peer_walk_ms=0):
    """Fit quality gate. Returns (ok, s0_deg, reason)."""
    if peer_walk_ms > PEER_WALK_FAIL_MS:
        return False, None, R_FRIEND_MOVED
    sf = sigma_fit_deg(a1, sigma_res, n)
    s0 = math.sqrt(sf * sf + FLOOR_DEG * FLOOR_DEG)
    if peer_walk_ms > PEER_WALK_PENALTY_MS:
        s0 += PEER_WALK_DEG
    if 2.0 * a1 < MIN_P2T_DB or s0 > MAX_S0_DEG:
        return False, s0, R_NO_FIX
    return True, s0, None


class ScanSession:
    """One scan, from the tap to the result. Allocation-light after __init__."""

    def __init__(self, t_ms, blank_fn=None, self_blank=True, cap=CAP):
        self.blank_fn = blank_fn      # optional: blank_fn(t_ms) -> True while a pulse blanks
        self.self_blank = self_blank  # also blank around the haptics this session emits
        self.cap = cap
        self.phi = array("f", [0.0] * cap)
        self.val = array("f", [0.0] * cap)
        self.src = bytearray(cap)
        self._scratch = array("f", [0.0] * cap)
        self._seg_lo = array("i", [0] * (BINS + 1))   # seg 12 = bin 0 after 345 deg
        self._seg_hi = array("i", [0] * (BINS + 1))
        self._med = array("f", [0.0] * (2 * BINS))    # per bin, per source
        self._bn = array("i", [0] * (2 * BINS))
        self._dirty = bytearray(BINS)
        self._raw = array("f", [0.0] * BINS)
        self._rawok = bytearray(BINS)
        self._sm = array("f", [0.0] * BINS)
        self._sum = array("f", [0.0, 0.0])
        self._cnt = array("i", [0, 0])
        self._step_t = array("i", [0] * 8)
        self.bins = [None] * BINS
        self.reset(t_ms)

    def reset(self, t_ms):
        self.phase = READY
        self.t_start = t_ms
        self._t_last = t_ms
        self.n = 0
        for i in range(BINS + 1):
            self._seg_lo[i] = -1
            self._seg_hi[i] = -1
        for i in range(2 * BINS):
            self._bn[i] = 0
        for i in range(BINS):
            self._dirty[i] = 0
            self._rawok[i] = 0
            self.bins[i] = None
        self._sum[0] = self._sum[1] = 0.0
        self._cnt[0] = self._cnt[1] = 0
        # motion
        self.tilt_deg = None
        self.activity = 0
        self._flat_since = None
        self._over_since = None
        self._tilt_fault = False
        self._steps_last = None
        self.steps = 0
        self._sti = 0
        self._stn = 0
        self._blank_flag = False
        self._blank_from = t_ms
        self._blank_until = None
        # ready
        self.flat = False
        self._cd_ms = 0
        self._digit = 0
        self.countdown = 3
        # sweep
        self.active_ms = 0
        self.pause_ms = 0
        self.wedge_deg = 0.0
        self.paused = False
        self.fault = None
        self._next_tick = TICK_EVERY_DEG
        self._peer_walk = False
        self._peer_t = None
        self.peer_walk_ms = 0
        self.mirror = None
        self._ema = None
        self._mt = t_ms
        self._mn = 0.0
        self._mx = 0.0
        self.t_sweep = None
        # result
        self.t_result = None
        self.reason = None
        self.theta_deg = None
        self.s0_deg = None
        self.sigma_fit_deg = None
        self.a1_db = None
        self.sigma_res_db = None
        self.best_bin = None
        self._haptic = None

    # ---- inputs -------------------------------------------------------------
    def set_blank(self, on):
        """External blanking flag (e.g. the motor is on or < 150 ms since)."""
        self._blank_flag = bool(on)

    def blanked(self, t_ms):
        if self._blank_flag:
            return True
        f = self.blank_fn
        if f is not None and f(t_ms):
            return True
        u = self._blank_until
        return (u is not None and ticks_diff(t_ms, self._blank_from) >= 0
                and ticks_diff(u, t_ms) > 0)

    def on_motion(self, t_ms, tilt_deg=None, steps=None, activity=None):
        """IMU update: tilt from flat (deg), cumulative step count, ACT_* code.

        Tilt and step (shake) samples inside a blanking window are ignored.
        """
        blank = self.blanked(t_ms)
        if tilt_deg is not None and not blank:
            self.tilt_deg = tilt_deg
            if self._flat_since is None:
                if tilt_deg <= FLAT_DEG:
                    self._flat_since = t_ms
            elif tilt_deg > FLAT_OFF_DEG:
                self._flat_since = None
            if tilt_deg > TILT_FAULT_DEG:
                if self._over_since is None:
                    self._over_since = t_ms
            else:
                self._over_since = None
                if self._tilt_fault and tilt_deg <= FLAT_DEG:
                    self._tilt_fault = False
        if activity is not None:
            self.activity = activity
        if steps is not None:
            last = self._steps_last
            self._steps_last = steps
            if last is not None and self.phase == SWEEP and not blank:
                d = steps - last
                if 0 < d < 1000:
                    self.steps += d
                    k = d if d < 8 else 8
                    while k:
                        self._step_t[self._sti] = t_ms
                        self._sti = (self._sti + 1) & 7
                        if self._stn < 8:
                            self._stn += 1
                        k -= 1

    def feed_tracker(self, t_ms, mt):
        """Convenience: read a finder.motion.MotionTracker."""
        self.on_motion(t_ms, tilt_from_gravity(mt.gx, mt.gy, mt.gz, mt.z_sign),
                       mt.steps, mt.activity)

    def on_packet(self, t_ms, rssi=None, peer_rssi=None):
        """One received beacon: own raw RSSI and/or partner's raw ``rssi_last``."""
        if self.phase != SWEEP or self.paused:
            return
        a = self.active_ms
        d = ticks_diff(t_ms, self._t_last)
        if d > 0:
            a += d if d < MAX_DT_MS else MAX_DT_MS
        if a >= DURATION_MS:
            return
        phi = a * DEG_PER_S * 0.001
        if rssi is not None:
            self._add(phi, rssi, SRC_OWN)
            self._mirror(t_ms, rssi)
        if peer_rssi is not None:
            self._add(phi, peer_rssi, SRC_PEER)

    def on_peer(self, t_ms, walking):
        """Partner motion from its beacon (flags bit4 walking or activity)."""
        self._peer_walk = bool(walking)
        self._peer_t = t_ms

    def on_input(self, t_ms, gesture):
        """Any tap (or double tap) cancels. Returns True if it cancelled."""
        if gesture == G_TAP or gesture == G_DOUBLE_TAP:
            return self.cancel(t_ms)
        return False

    def cancel(self, t_ms):
        """Short press / tap: stop ready/sweep with no arrow. A no-op once in
        ``result``: the fix (or failure toast) already stands."""
        if self.phase == RESULT:
            return False
        self.phase = RESULT
        self.t_result = t_ms
        self.reason = R_CANCEL
        self.theta_deg = None
        self.s0_deg = None
        self.paused = False
        return True

    # ---- per-frame ----------------------------------------------------------
    def update(self, t_ms):
        """Advance phases (10 Hz). Returns the haptic started this frame, if any."""
        dt = ticks_diff(t_ms, self._t_last)
        if dt < 0:
            dt = 0
        elif dt > MAX_DT_MS:
            dt = MAX_DT_MS
        ph = self.phase
        if ph == READY:
            self._t_last = t_ms
            self._update_ready(t_ms, dt)
        elif ph == SWEEP:
            self._update_sweep(t_ms, dt)
            self._t_last = t_ms
        else:
            self._t_last = t_ms
        return self._haptic

    def pop_haptic(self):
        h = self._haptic
        self._haptic = None
        return h

    def _emit(self, name, t_ms):
        h = self._haptic
        if h is None or _HPRIO[name] >= _HPRIO[h]:
            self._haptic = name
        if self.self_blank:
            end = ticks_add(t_ms, HAPTIC_MS[name] + BLANK_AFTER_MS)
            u = self._blank_until
            if u is None or ticks_diff(t_ms, u) >= 0:
                self._blank_from = t_ms
                self._blank_until = end
            elif ticks_diff(end, u) > 0:
                self._blank_until = end

    def _update_ready(self, t, dt):
        fs = self._flat_since
        flat = fs is not None and ticks_diff(t, fs) >= FLAT_HOLD_MS
        self.flat = flat
        if not flat:
            return
        held = ticks_diff(t, fs) - FLAT_HOLD_MS
        if self._digit == 0:
            dt = 0          # first flat frame shows 3 and ticks
        elif dt > held:
            dt = held
        self._cd_ms += dt
        if self._cd_ms >= READY_MS:
            self._start_sweep(t)
            return
        d = 3 - self._cd_ms // 1000
        self.countdown = d
        if d != self._digit:
            self._digit = d
            self._emit("TICK", t)

    def _start_sweep(self, t):
        self.phase = SWEEP
        self.t_sweep = t
        self.countdown = None
        self.active_ms = 0
        self.pause_ms = 0
        self.steps = 0
        self._stn = 0
        self.peer_walk_ms = 0
        self.wedge_deg = 0.0
        self._next_tick = TICK_EVERY_DEG

    def _steps_recent(self, t):
        k = 0
        for i in range(self._stn):
            if ticks_diff(t, self._step_t[i]) < WALK_WINDOW_MS:
                k += 1
        return k

    def _update_sweep(self, t, dt):
        # time since the last frame belongs to the previous paused state
        if self.paused:
            self.pause_ms += dt
        else:
            self.active_ms += dt
        pt = self._peer_t
        if self._peer_walk and pt is not None and ticks_diff(t, pt) < PEER_STALE_MS:
            self.peer_walk_ms += dt
        # faults
        os_ = self._over_since
        if not self._tilt_fault and os_ is not None and ticks_diff(t, os_) > TILT_FAULT_MS:
            self._tilt_fault = True
        act = self.activity
        walk = act == ACT_WALK or act == ACT_RUN or self._steps_recent(t) >= WALK_STEPS
        fault = FAULT_TILT if self._tilt_fault else (FAULT_WALK if walk else None)
        if fault is not None and not self.paused:
            self._emit("NOPE", t)       # once per pause episode
        self.paused = fault is not None
        self.fault = fault
        if self.steps > ABORT_STEPS or self.pause_ms > ABORT_PAUSE_MS:
            self._fail(t, R_ABORT)
            return
        a = self.active_ms
        if a > DURATION_MS:
            a = DURATION_MS
        w = a * DEG_PER_S * 0.001
        self.wedge_deg = w
        nt = self._next_tick
        while nt < 360.0 and w >= nt:
            self._emit("DOUBLE" if nt == 180.0 else "TICK", t)
            nt += TICK_EVERY_DEG
        self._next_tick = nt
        self._refresh_bins()
        if self.active_ms >= DURATION_MS:
            self._finish(t)

    def _fail(self, t, reason):
        self.phase = RESULT
        self.t_result = t
        self.reason = reason
        self.paused = False
        self.theta_deg = None
        self._emit("NOPE", t)

    def _finish(self, t):
        self.wedge_deg = 360.0
        fit = fit_harmonic(self.phi, self.val, self.src, self.n)
        if fit is None:
            self._fail(t, R_FRIEND_MOVED if self.peer_walk_ms > PEER_WALK_FAIL_MS else R_NO_FIX)
            return
        a1, th, sres, n = fit
        self.a1_db = a1
        self.sigma_res_db = sres
        self.sigma_fit_deg = sigma_fit_deg(a1, sres, n)
        ok, s0, reason = evaluate(a1, sres, n, self.peer_walk_ms)
        self.s0_deg = s0
        if not ok:
            self._fail(t, reason)
            return
        self.phase = RESULT
        self.t_result = t
        self.theta_deg = th
        self.best_bin = int((th + 15.0) / 30.0) % BINS
        self._emit("CLOSER", t)

    # ---- samples and bins ---------------------------------------------------
    def _add(self, phi, y, s):
        n = self.n
        if n >= self.cap:
            return
        self.phi[n] = phi
        self.val[n] = y
        self.src[n] = s
        self.n = n + 1
        seg = int((phi + 15.0) / 30.0)
        if seg > BINS:
            seg = BINS
        if self._seg_lo[seg] < 0:
            self._seg_lo[seg] = n
        self._seg_hi[seg] = n + 1
        self._dirty[seg if seg < BINS else 0] = 1
        self._sum[s] += y
        self._cnt[s] += 1

    def _mirror(self, t, rssi):
        """ui-spec 5.7 live mirror: 150 ms EMA of raw RSSI vs running min/max."""
        e = self._ema
        if e is None:
            e = float(rssi)
            self._mn = self._mx = e
        else:
            d = ticks_diff(t, self._mt)
            if d < 0:
                d = 0
            e += d / (150.0 + d) * (rssi - e)
        self._mt = t
        self._ema = e
        if e < self._mn:
            self._mn = e
        if e > self._mx:
            self._mx = e
        r = self._mx - self._mn
        v = (e - self._mn) / (r if r > 4.0 else 4.0)
        self.mirror = 0.0 if v < 0.0 else 1.0 if v > 1.0 else v

    def _median(self, b, s):
        buf = self._scratch
        src = self.src
        val = self.val
        k = 0
        seg = b
        while True:
            lo = self._seg_lo[seg]
            if lo >= 0:
                for i in range(lo, self._seg_hi[seg]):
                    if src[i] == s:
                        # insertion sort as we go
                        v = val[i]
                        j = k
                        while j > 0 and buf[j - 1] > v:
                            buf[j] = buf[j - 1]
                            j -= 1
                        buf[j] = v
                        k += 1
            if b != 0 or seg == BINS:
                break
            seg = BINS
        if k == 0:
            return 0, 0.0
        m = k >> 1
        return k, (buf[m] if k & 1 else 0.5 * (buf[m - 1] + buf[m]))

    def _refresh_bins(self):
        """Recompute dirty bin medians, then the +-30 deg smoothed 0..1 curve."""
        for b in range(BINS):
            if self._dirty[b]:
                self._dirty[b] = 0
                for s in (0, 1):
                    k, m = self._median(b, s)
                    self._bn[2 * b + s] = k
                    self._med[2 * b + s] = m
        c0 = self._cnt[0]
        c1 = self._cnt[1]
        m0 = self._sum[0] / c0 if c0 else 0.0
        m1 = self._sum[1] / c1 if c1 else 0.0
        for b in range(BINS):
            n0 = self._bn[2 * b]
            n1 = self._bn[2 * b + 1]
            tot = n0 + n1
            if tot < MIN_PACKETS:
                self._rawok[b] = 0
                continue
            self._rawok[b] = 1
            v = 0.0
            if n0:
                v += n0 * (self._med[2 * b] - m0)
            if n1:
                v += n1 * (self._med[2 * b + 1] - m1)
            self._raw[b] = v / tot
        lo = hi = None
        raw = self._raw
        ok = self._rawok
        for b in range(BINS):
            if not ok[b]:
                continue
            w = 2.0 * raw[b]
            ws = 2
            p = (b - 1) % BINS
            q = (b + 1) % BINS
            if ok[p]:
                w += raw[p]
                ws += 1
            if ok[q]:
                w += raw[q]
                ws += 1
            v = w / ws
            self._sm[b] = v
            if lo is None or v < lo:
                lo = v
            if hi is None or v > hi:
                hi = v
        if lo is None:
            for b in range(BINS):
                self.bins[b] = None
            return
        r = hi - lo
        if r < 4.0:
            r = 4.0
        for b in range(BINS):
            self.bins[b] = (self._sm[b] - lo) / r if ok[b] else None

    # ---- render outputs -----------------------------------------------------
    @property
    def sub(self):
        return self.phase

    @property
    def active(self):
        """True during ready/sweep: set the beacon scan flag and beacon at 20 Hz."""
        return self.phase != RESULT

    @property
    def glyph(self):
        return "countdown" if self.phase == READY else "turn" if self.phase == SWEEP else None

    @property
    def rim_warn(self):
        """Iris rim in status.warn (ready and not flat)."""
        return self.phase == READY and not self.flat

    @property
    def top_text(self):
        ph = self.phase
        if ph == READY:
            return HINT_CHEST if self.flat else HINT_FLAT
        if ph == SWEEP and self.paused:
            return HINT_FLAT if self.fault == FAULT_TILT else HINT_STILL
        return None

    @property
    def top_warn(self):
        return self.top_text in (HINT_FLAT, HINT_STILL)

    @property
    def word(self):
        return WORD_TURN if self.phase == READY else None

    @property
    def toast(self):
        r = self.reason
        return TOASTS.get(r) if r is not None else None

    @property
    def result(self):
        """(theta_deg, s0_deg) on a fix, else None. theta is clockwise from
        the heading at sweep start (screen-up)."""
        if self.phase == RESULT and self.reason is None and self.theta_deg is not None:
            return self.theta_deg, self.s0_deg
        return None

    @property
    def active_bin(self):
        return int((self.wedge_deg + 15.0) / 30.0) % BINS

    def sweep(self, t_ms=None):
        """(wedge_deg, bins, active_bin, paused) or None. ``bins`` is a shared
        list updated in place. In ``result`` (fix) the active bin is the best
        bin while its blink is on, else None."""
        ph = self.phase
        if ph == SWEEP:
            return self.wedge_deg, self.bins, self.active_bin, self.paused
        if ph == RESULT and self.result is not None:
            ab = self.best_bin
            if t_ms is not None:
                e = ticks_diff(t_ms, self.t_result)
                if e >= BLINK_MS or (e // 200) & 1:
                    ab = None
            return 360.0, self.bins, ab, False
        return None

    def done(self, t_ms):
        """True when the caller should leave SCANNING (reveal or zone screen)."""
        if self.phase != RESULT:
            return False
        if self.result is None:
            return True
        return ticks_diff(t_ms, self.t_result) >= BLINK_MS + MORPH_MS
