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

Drive it with ``on_motion`` / ``on_packet`` as data arrives, ``on_peer``
before an ``update`` while the partner's beacon is fresh, ``cancel`` on a tap
or short press and ``update(t_ms)`` once per logic frame (10 Hz), then read
the render outputs (``sub``, ``countdown``, ``top_text``, ``word``, ``toast``,
``sweep()``, ``mirror``, ``pop_haptic()``, ``result``).
Times are ticks ms. Accelerometer blanking around haptic pulses comes from
``blank_fn`` (the game's ``blanked``, which covers the haptics this session
raises).
"""

import math
from array import array
from finder import tuning as T
from finder.compat import TickRing, const, ticks_diff
from finder.estimators.base import ACT_WALK, ACT_RUN
from finder.haptic_patterns import stronger
from finder.motion import FACE_OFF_DEG
from finder.session import LiveMirror

DURATION_MS = T.SCAN_DURATION_MS
READY_MS = T.SCAN_READY_MS
DEG_PER_S = T.SCAN_DEG_PER_S
MIN_P2T_DB = T.SCAN_MIN_P2T_DB      # no fix if 2*a1 below this
MAX_S0_DEG = T.SCAN_MAX_S0_DEG
FLOOR_DEG = T.SCAN_FLOOR_DEG
TILT_FAULT_DEG = T.SCAN_TILT_FAULT_DEG
TILT_FAULT_MS = T.SCAN_TILT_FAULT_MS
ABORT_STEPS = T.SCAN_ABORT_STEPS
ABORT_PAUSE_MS = T.SCAN_ABORT_PAUSE_MS
BINS = T.SCAN_BINS
MIN_PACKETS = T.SCAN_MIN_PACKETS_PER_BIN
FLAT_DEG = T.SCAN_FLAT_DEG          # face-up within 20 deg = flat ...
FLAT_HOLD_MS = T.SCAN_FLAT_HOLD_MS  # ... held 0.5 s
WALK_STEPS = T.SCAN_WALK_STEPS      # >= 3 steps in 2 s = walking
WALK_WINDOW_MS = T.SCAN_WALK_WINDOW_MS
PEER_WALK_PENALTY_MS = T.SCAN_PEER_WALK_WIDEN_MS
PEER_WALK_FAIL_MS = T.SCAN_PEER_WALK_FAIL_MS
PEER_WALK_DEG = T.SCAN_PEER_WALK_WIDEN_DEG
TICK_EVERY_DEG = T.SCAN_TICK_DEG
BLINK_MS = 4 * T.SCAN_BLINK_MS      # best bin blinks twice (SCAN_BLINK_MS on/off)
MORPH_MS = T.SCAN_MORPH_MS
# spec-silent internals
FLAT_OFF_DEG = FACE_OFF_DEG   # hysteresis: the one finder.motion face_up uses
MAX_DT_MS = const(1000)   # a stalled loop never jumps the wedge further
MIN_FIT_N = const(12)
CAP = DURATION_MS * T.BEACON_HZ_SCAN // 500 + 160   # 2 sources at the scan rate + margin (640)

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
WORD_TURN = "TURN RIGHT"

_D2R = math.pi / 180.0
_R2D = 180.0 / math.pi


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

    def __init__(self, t_ms, blank_fn=None):
        self.blank_fn = blank_fn      # optional: blank_fn(t_ms) -> True while a pulse blanks
        self.phi = array("f", [0.0] * CAP)
        self.val = array("f", [0.0] * CAP)
        self.src = bytearray(CAP)
        self._scratch = array("f", [0.0] * CAP)
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
        self._steps_t = TickRing(WALK_STEPS)   # the walk fault: >= 3 steps in 2 s
        self._lm = LiveMirror()
        self.bins = [None] * BINS
        self.reset(t_ms)

    def reset(self, t_ms):
        self.phase = READY
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
        self.activity = 0
        self._flat_since = None
        self._over_since = None
        self._tilt_fault = False
        self._steps_last = None
        self.steps = 0
        self._steps_t.clear()
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
        self.peer_walk_ms = 0
        self._lm.reset(t_ms)
        self.t_sweep = None
        # result
        self.t_result = None
        self.reason = None
        self.theta_deg = None
        self.s0_deg = None
        self.sigma_fit_deg = None
        self.a1_db = None
        self.best_bin = None
        self._haptic = None

    # ---- inputs -------------------------------------------------------------
    def blanked(self, t_ms):
        f = self.blank_fn
        return f is not None and bool(f(t_ms))

    def on_motion(self, t_ms, tilt_deg=None, steps=None, activity=None):
        """IMU update: tilt from flat (deg), cumulative step count, ACT_* code.

        Tilt and step (shake) samples inside a blanking window are ignored.
        """
        blank = self.blanked(t_ms)
        if tilt_deg is not None and not blank:
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
                    k = d if d < WALK_STEPS else WALK_STEPS
                    while k:
                        self._steps_t.note(t_ms)
                        k -= 1

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
            self._lm.add(t_ms, rssi)
        if peer_rssi is not None:
            self._add(phi, peer_rssi, SRC_PEER)

    def on_peer(self, t_ms, walking):
        """Partner walking (beacon flags bit4 or activity) for the next
        ``update`` only: Game calls it on each logic tick while the partner's
        beacon is fresh, so a frame without a call (stale beacon) counts as
        not walking and freshness is not checked again here."""
        self._peer_walk = bool(walking)

    def cancel(self, t_ms):
        """Short press / tap: stop ready/sweep with no arrow. A no-op once in
        ``result``: the fix (or failure toast) already stands."""
        if self.phase == RESULT:
            return False
        self.phase = RESULT
        self.t_result = t_ms
        self.reason = R_CANCEL
        self.paused = False
        return True

    # ---- per-frame ----------------------------------------------------------
    def update(self, t_ms):
        """Advance phases (10 Hz); ``pop_haptic()`` then gives the frame's haptic."""
        dt = ticks_diff(t_ms, self._t_last)
        if dt < 0:
            dt = 0
        elif dt > MAX_DT_MS:
            dt = MAX_DT_MS
        if self.phase == READY:
            self._update_ready(t_ms, dt)
        elif self.phase == SWEEP:
            self._update_sweep(t_ms, dt)
        self._t_last = t_ms
        self._peer_walk = False         # an on_peer report lasts one frame

    def pop_haptic(self):
        h = self._haptic
        self._haptic = None
        return h

    def _emit(self, name):
        """Keep the strongest haptic raised this frame."""
        self._haptic = stronger(self._haptic, name)

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
            self._emit("TICK")

    def _start_sweep(self, t):
        self.phase = SWEEP
        self.t_sweep = t
        self.countdown = None
        self.active_ms = 0
        self.pause_ms = 0
        self.steps = 0
        self._steps_t.clear()
        self.peer_walk_ms = 0
        self.wedge_deg = 0.0
        self._next_tick = TICK_EVERY_DEG

    def _update_sweep(self, t, dt):
        # time since the last frame belongs to the previous paused state
        if self.paused:
            self.pause_ms += dt
        else:
            self.active_ms += dt
        if self._peer_walk:
            self.peer_walk_ms += dt
        # faults
        os_ = self._over_since
        if not self._tilt_fault and os_ is not None and ticks_diff(t, os_) > TILT_FAULT_MS:
            self._tilt_fault = True
        act = self.activity
        walk = act == ACT_WALK or act == ACT_RUN or self._steps_t.full_within(t, WALK_WINDOW_MS)
        fault = FAULT_TILT if self._tilt_fault else (FAULT_WALK if walk else None)
        if self.active_ms >= DURATION_MS:
            fault = None                # the turn is complete: nothing left to pause
        if fault is not None and not self.paused:
            self._emit("NOPE")          # once per pause episode
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
            self._emit("DOUBLE" if nt == 180.0 else "TICK")
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
        self._emit("NOPE")

    def _finish(self, t):
        self.wedge_deg = 360.0
        fit = fit_harmonic(self.phi, self.val, self.src, self.n)
        if fit is None:
            self._fail(t, R_FRIEND_MOVED if self.peer_walk_ms > PEER_WALK_FAIL_MS else R_NO_FIX)
            return
        a1, th, sres, n = fit
        self.a1_db = a1
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
        self._emit("CLOSER")

    # ---- samples and bins ---------------------------------------------------
    def _add(self, phi, y, s):
        n = self.n
        if n >= CAP:
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
            self._sm[b] = w / ws
            v = self._sm[b]          # the stored (float32) value, so bins stay in 0..1
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
    def mirror(self):
        """ui-spec §5.7 live mirror of own raw RSSI (0..1), None before a packet."""
        return self._lm.value

    @property
    def top_text(self):
        if self.phase == READY:
            return HINT_CHEST if self.flat else HINT_FLAT
        return None  # ui-spec §6: no chip during the sweep, even paused

    @property
    def word(self):
        return WORD_TURN if self.phase == READY else None

    @property
    def toast(self):
        return TOASTS.get(self.reason)

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
        list updated in place. In ``result`` (fix) slot 0 is theta (the morph
        target; no wedge is drawn) and the active bin is the best bin while its
        blink is on, else None."""
        ph = self.phase
        if ph == SWEEP:
            return self.wedge_deg, self.bins, self.active_bin, self.paused
        if ph == RESULT and self.result is not None:
            ab = self.best_bin
            if t_ms is not None:
                e = ticks_diff(t_ms, self.t_result)
                if e >= BLINK_MS or (e // T.SCAN_BLINK_MS) & 1:
                    ab = None
            return self.theta_deg, self.bins, ab, False
        return None

    def done(self, t_ms):
        """True when the caller should leave SCANNING (reveal or zone screen)."""
        if self.phase != RESULT:
            return False
        if self.result is None:
            return True
        return ticks_diff(t_ms, self.t_result) >= BLINK_MS + MORPH_MS
