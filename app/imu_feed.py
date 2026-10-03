"""BMA423 FIFO -> ``MotionTracker`` (``out_hz``, g) + a bump spike detector.

    feed = ImuFeed(imu, on_tap=game.on_accel_tap)
    feed.motor(t, level)        # every motor level change (haptic blanking)
    n = feed.poll(now)          # every loop: drains the FIFO (170 frames = 1.7 s)
    game.set_tracker(now, feed.tracker)

The FIFO runs at 100 Hz in milli-g (``hal.bma423.fifo_read_mg``). Samples are
time-stamped backwards from ``now`` (continuing the previous batch's clock
while it stays within ``RESYNC_MS``), block-averaged to ``out_hz`` (50: pairs;
the runtime uses 25, a quarter of the tracker's float work of 100 Hz) and
handed to the tracker in g. The spike detector always sees every 100 Hz sample.

Bump spike (ui-spec §6 HOT/FOUND, §7): the gravity-removed magnitude
``|a - g_lp|`` above ``spike_g`` (2.5 g) for a run ``spike_min_ms``..
``spike_max_ms`` wide (10-20 ms: one or two 100 Hz samples). Longer runs
(shakes, falls, slaps) are rejected, as are runs within ``refractory_ms`` of
the last accepted one. Blanking (§7): a run that overlaps a motor pulse, from
its start until ``BLANKING_MS`` after it ends, is ignored. The feed keeps its
own short history of pulses because samples reach it up to a batch late,
after ``HapticPlayer.blanked`` has already moved on.

Per sample the work is integer maths plus one tracker update per
``odr_hz // out_hz`` samples.
"""

from finder.compat import ticks_add, ticks_diff
from finder.motion import MotionTracker
from finder.haptic_patterns import BLANKING_MS

SPIKE_G = 2.5
SPIKE_MIN_MS = 10
SPIKE_MAX_MS = 20
REFRACTORY_MS = 200       # ringing after a knock is not a second bump
RESYNC_MS = 40            # batch clock this far from ``now``: re-anchor
G_SHIFT = 4               # gravity low-pass: tau ~ 16 samples (160 ms at 100 Hz)
_NP = 4                   # motor pulses remembered for blanking


class ImuFeed:
    """Drains a BMA423-like FIFO (``fifo_read_mg`` + ``fifo_mg``)."""

    def __init__(self, imu, tracker=None, on_tap=None, odr_hz=100, out_hz=50,
                 spike_g=SPIKE_G, spike_min_ms=SPIKE_MIN_MS, spike_max_ms=SPIKE_MAX_MS,
                 refractory_ms=REFRACTORY_MS, blank_ms=BLANKING_MS, z_sign=1):
        self.imu = imu
        self.dt = 1000 // odr_hz
        d = odr_hz // out_hz
        self.dec = d if d > 1 else 1
        self.tracker = tracker if tracker is not None else MotionTracker(
            rate_hz=odr_hz // self.dec, z_sign=z_sign)
        self.on_tap = on_tap
        thr = int(spike_g * 1000)
        self.thr2 = thr * thr
        self.spike_min_ms = spike_min_ms
        self.spike_max_ms = spike_max_ms
        self.refractory_ms = refractory_ms
        self.blank_ms = blank_ms
        self._k = 1.0 / (1000.0 * self.dec)
        self._on = [0] * _NP      # pulse start times (ring)
        self._off = [0] * _NP     # pulse end times; valid while _used
        self._pi = 0
        self._pn = 0
        self._lvl_on = False
        self.reset()

    def reset(self):
        self.t = None             # time of the last sample
        self.n_samples = 0
        self.n_taps = 0
        self.n_rejected = 0       # runs too wide/narrow, blanked or refractory
        self.n_blanked = 0
        self.tap = None           # last accepted spike time (this poll)
        self.last_tap = None
        self.peak_mg = 0          # largest |a - g| seen (debug / tuning)
        self._gx = self._gy = self._gz = None
        self._run = 0
        self._run_t = 0
        self._run_pk = 0
        self._sx = self._sy = self._sz = 0
        self._sn = 0

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
    def poll(self, now):
        """Drain the FIFO; returns samples read. Sets ``tap`` (or None)."""
        self.tap = None
        imu = self.imu
        n = imu.fifo_read_mg()
        if not n:
            return 0
        a = imu.fifo_mg
        dt = self.dt
        start = ticks_add(now, -(n - 1) * dt)
        t = self.t
        if t is not None:
            t = ticks_add(t, dt)
            e = ticks_diff(t, start)
            if -RESYNC_MS <= e <= RESYNC_MS and ticks_diff(now, ticks_add(t, (n - 1) * dt)) >= 0:
                start = t
        t = start
        tr = self.tracker
        dec = self.dec
        k = self._k
        for i in range(n):
            j = 3 * i
            x = a[j]
            y = a[j + 1]
            z = a[j + 2]
            self._sample(t, x, y, z)
            self._sx += x
            self._sy += y
            self._sz += z
            self._sn += 1
            if self._sn >= dec:
                tr.add_sample(t, self._sx * k, self._sy * k, self._sz * k)
                self._sx = self._sy = self._sz = 0
                self._sn = 0
            self.t = t
            t = ticks_add(t, dt)
        self.n_samples += n
        return n

    def _sample(self, t, x, y, z):
        gx = self._gx
        if gx is None:
            self._gx = x
            self._gy = y
            self._gz = z
            return
        dx = x - gx
        dy = y - self._gy
        dz = z - self._gz
        m2 = dx * dx + dy * dy + dz * dz
        if m2 > self.thr2:
            if not self._run:
                self._run_t = t
                self._run_pk = 0
            self._run += 1
            if m2 > self._run_pk:
                self._run_pk = m2
            return                    # gravity frozen during a spike
        if self._run:
            self._end_run(t)
        s = G_SHIFT
        self._gx = gx + ((x - gx) >> s)
        self._gy += (y - self._gy) >> s
        self._gz += (z - self._gz) >> s

    def _end_run(self, t_end):
        w = self._run * self.dt
        t0 = self._run_t
        self._run = 0
        pk = self._run_pk
        if pk > self.peak_mg * self.peak_mg:
            self.peak_mg = int(pk ** 0.5)
        if w < self.spike_min_ms or w > self.spike_max_ms:
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
