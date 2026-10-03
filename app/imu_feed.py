"""BMA423 FIFO -> ``MotionTracker`` (``out_hz``, g) + a bump spike detector.

    feed = ImuFeed(imu, on_tap=game.on_accel_tap)
    feed.motor(t, level)        # every motor level change (haptic blanking)
    feed.set_fast(armed)        # FAST_HZ while a bump can count, else SLOW_HZ
    n = feed.poll(now)          # every loop: drains the FIFO (170 frames)
    game.set_tracker(now, feed.tracker)

The FIFO runs in milli-g (``hal.bma423.fifo_read_mg``) at ``SLOW_HZ`` (100),
and at ``FAST_HZ`` (``BUMP_ODR_HZ``, 800) while ``set_fast(True)``: the chip's
filter passes about 0.4x its rate, so at 100 Hz a 1-3 ms knock is smeared to
about 1 g, while at 800 Hz it keeps most of its 3-8 g peak (bring-up,
3 Oct 2026). The FIFO then holds 212 ms. Samples are time-stamped backwards
from ``now`` (continuing the previous batch's clock, to a 1/256 ms, while it
stays within ``RESYNC_MS``), block-averaged to ``out_hz`` (the runtime uses
25) and handed to the tracker in g.

Bump spike (ui-spec §6 HOT/FOUND, §7), fast rate only: the gravity-removed
magnitude ``|a - g_lp|`` above ``SPIKE_G`` for a run ``SPIKE_MIN_MS``..
``SPIKE_MAX_MS`` wide. Longer runs (shakes, falls, slaps) are rejected, as
are runs within ``REFRACTORY_MS`` of the last accepted one. Blanking (§7): a
run that overlaps a motor pulse, from its start until ``BLANKING_MS`` after it
ends, is ignored. The feed keeps its own short history of pulses (``motor``)
because samples reach it up to a batch late, after the motor has already
moved on; it is also the game's ``blank_fn``. The thresholds are attributes
(``thr2`` = threshold in mg squared, ``spike_min_ms``, ``spike_max_ms``,
``refractory_ms``, ``blank_ms``), so a notebook can tune them on a running
feed.

Per sample the work is integer maths (none for spikes at the slow rate) plus
one tracker update per ``rate // out_hz`` samples.
"""

from finder import tuning as T
from finder.compat import ticks_add, ticks_diff
from finder.motion import MotionTracker
from finder.haptic_patterns import BLANKING_MS

SLOW_HZ = 100             # tracker only (hal.bma423 default odr)
FAST_HZ = T.BUMP_ODR_HZ   # while a bump can count
SPIKE_G = T.BUMP_SPIKE_G                      # ui-spec §6 HOT (generated SPEC rows)
SPIKE_MIN_MS, SPIKE_MAX_MS = T.BUMP_SPIKE_MS
REFRACTORY_MS = T.BUMP_REFRACTORY_MS          # ringing after a knock is not a second bump
RESYNC_MS = 40            # batch clock this far from ``now``: re-anchor
G_SHIFT = 4               # gravity low-pass at 100 Hz: tau ~ 16 samples (160 ms)
_NP = 4                   # motor pulses remembered for blanking


class ImuFeed:
    """Drains a BMA423-like FIFO (``fifo_read_mg`` + ``fifo_mg``; ``set_odr``
    for the fast rate). ``z_sign`` None: the imu's own (``hal.board``)."""

    def __init__(self, imu, on_tap=None, out_hz=50, z_sign=None):
        self.imu = imu
        self.out_hz = out_hz
        if z_sign is None:
            z_sign = getattr(imu, "z_sign", 1)
        self.tracker = MotionTracker(rate_hz=out_hz, z_sign=z_sign)
        self.on_tap = on_tap
        thr = int(SPIKE_G * 1000)
        self.thr2 = thr * thr
        self.spike_min_ms = SPIKE_MIN_MS
        self.spike_max_ms = SPIKE_MAX_MS
        self.refractory_ms = REFRACTORY_MS
        self.blank_ms = BLANKING_MS
        self._on = [0] * _NP      # pulse start times (ring)
        self._off = [0] * _NP     # pulse end times (the newest: its start while on)
        self._pi = 0
        self._pn = 0
        self._lvl_on = False
        self.n_samples = 0
        self.n_taps = 0
        self.n_rejected = 0       # runs too wide/narrow, blanked or refractory
        self.n_blanked = 0
        self.last_tap = None
        self.peak_mg = 0          # largest |a - g| seen (debug / tuning)
        self.fast = False
        self._rate(SLOW_HZ)

    def _rate(self, hz):
        self.hz = hz
        d = hz // self.out_hz
        self.dec = d if d > 1 else 1
        self._k = 1.0 / (1000.0 * self.dec)
        self._dq = 256000 // hz   # sample period, ms in Q8
        s = G_SHIFT
        r = hz // SLOW_HZ
        while r > 1:              # same 160 ms gravity tau at any rate
            r >>= 1
            s += 1
        self.g_shift = s
        self.tap = None
        self._tb = None           # this batch: first sample at _tb + _f0/256 ms
        self._f0 = 0
        self._n = 0               # ... and its size
        self._gx = self._gy = self._gz = None
        self._run = 0
        self._run_t = 0
        self._run_pk = 0
        self._sx = self._sy = self._sz = 0
        self._sn = 0

    def set_fast(self, on):
        """``FAST_HZ`` (spikes detected) or ``SLOW_HZ``; samples still in the
        FIFO are dropped (``imu.set_odr`` flushes it). Raises OSError from
        the bus, leaving the rate as it was."""
        on = bool(on)
        if on == self.fast:
            return
        hz = FAST_HZ if on else SLOW_HZ
        self.imu.set_odr(hz)
        self.fast = on
        self._rate(hz)

    # ---- haptic blanking ----
    def motor(self, t, level):
        """Motor output changed at ``t`` (level 0 = off)."""
        on = level > 0
        if on == self._lvl_on:
            return
        self._lvl_on = on
        if on:
            i = self._pi
            self._on[i] = t
            self._off[i] = t
            self._pi = (i + 1) % _NP
            if self._pn < _NP:
                self._pn += 1
        elif self._pn:
            self._off[(self._pi - 1) % _NP] = t

    def blanked(self, t):
        """True if ``t`` falls in a pulse or ``blank_ms`` after one ends."""
        n = self._pn
        i = self._pi
        last = True
        while n:
            i = (i - 1) % _NP
            s = self._on[i]
            if ticks_diff(t, s) >= 0:
                if last and self._lvl_on:
                    return True
                if ticks_diff(t, ticks_add(self._off[i], self.blank_ms)) < 0:
                    return True
                return False          # older pulses end before this one starts
            last = False
            n -= 1
        return False

    # ---- FIFO ----
    def _at(self, i):
        """Time (ms) of sample ``i`` of this batch."""
        return ticks_add(self._tb, (self._f0 + i * self._dq) >> 8)

    def _stamp(self, now, n):
        """Batch clock: the last of ``n`` samples is taken at ``now``, unless
        the previous batch's clock continues within ``RESYNC_MS``."""
        dq = self._dq
        tb = ticks_add(now, -(((n - 1) * dq) >> 8))
        f0 = 0
        q = self._tb
        if q is not None:
            q0 = self._f0 + self._n * dq      # first sample after the last batch
            q = ticks_add(q, q0 >> 8)
            q0 &= 255
            e = ticks_diff(q, tb)
            if (-RESYNC_MS <= e <= RESYNC_MS
                    and ticks_diff(now, ticks_add(q, (q0 + (n - 1) * dq) >> 8)) >= 0):
                tb = q
                f0 = q0
        self._tb = tb
        self._f0 = f0
        self._n = n

    def poll(self, now):
        """Drain the FIFO; returns samples read. Sets ``tap`` (or None)."""
        self.tap = None
        imu = self.imu
        n = imu.fifo_read_mg()
        if not n:
            return 0
        a = imu.fifo_mg
        self._stamp(now, n)
        tr = self.tracker
        dec = self.dec
        k = self._k
        fast = self.fast
        sx = self._sx
        sy = self._sy
        sz = self._sz
        sn = self._sn
        gx = self._gx
        if gx is None:
            gx = a[0]
            gy = a[1]
            gz = a[2]
        else:
            gy = self._gy
            gz = self._gz
        thr2 = self.thr2
        s = self.g_shift
        for i in range(n):
            j = 3 * i
            x = a[j]
            y = a[j + 1]
            z = a[j + 2]
            if fast:
                dx = x - gx
                dy = y - gy
                dz = z - gz
                m2 = dx * dx + dy * dy + dz * dz
                if m2 > thr2:                 # gravity frozen during a spike
                    if not self._run:
                        self._run_t = self._at(i)
                        self._run_pk = 0
                    self._run += 1
                    if m2 > self._run_pk:
                        self._run_pk = m2
                else:
                    if self._run:
                        self._end_run(self._at(i))
                    gx += (x - gx) >> s
                    gy += (y - gy) >> s
                    gz += (z - gz) >> s
            sx += x
            sy += y
            sz += z
            sn += 1
            if sn >= dec:
                tr.add_sample(self._at(i), sx * k, sy * k, sz * k)
                sx = sy = sz = sn = 0
        self._sx = sx
        self._sy = sy
        self._sz = sz
        self._sn = sn
        self._gx = gx
        self._gy = gy
        self._gz = gz
        self.n_samples += n
        return n

    def _end_run(self, t_end):
        w = self._run * 1000000 // self.hz      # us
        t0 = self._run_t
        self._run = 0
        pk = self._run_pk
        if pk > self.peak_mg * self.peak_mg:
            self.peak_mg = int(pk ** 0.5)
        if w < self.spike_min_ms * 1000 or w > self.spike_max_ms * 1000:
            self.n_rejected += 1
            return
        if self.blanked(t0) or self.blanked(t_end):
            self.n_blanked += 1
            self.n_rejected += 1
            return
        lt = self.last_tap
        if lt is not None and 0 <= ticks_diff(t0, lt) < self.refractory_ms:
            self.n_rejected += 1
            return
        self.last_tap = t0
        self.tap = t0
        self.n_taps += 1
        cb = self.on_tap
        if cb is not None:
            cb(t0)
