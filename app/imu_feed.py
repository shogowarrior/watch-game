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
stays within ``_resync`` ms: 40 at 100 Hz, 10 at 800 Hz), block-averaged to
``out_hz`` (the runtime uses 25) and handed to the tracker in g.

Bump spike (ui-spec §6 HOT/FOUND, §7), fast rate only: a run of the
gravity-removed magnitude ``|a - g_lp|`` above ``RUN_G``, ``SPIKE_MIN_MS``..
``SPIKE_MAX_MS`` wide, that peaks at ``SPIKE_G`` or more. The width is taken
at the lower level so that a slow excursion (turning the watch in the hand,
a swing) whose top just crosses ``SPIKE_G`` is still one long run, and is
rejected; soft bumps peak at 1-1.5 g in 1-8 samples (2026-10-04). Longer
runs (shakes, falls, slaps) are rejected, as are runs within
``REFRACTORY_MS`` of the last accepted one. The gravity
low-pass (160 ms at any rate, kept across rate switches) holds still during a
run, and follows again once the run is longer than ``spike_max_ms``, so a
wrong gravity estimate cannot hold a run open for good. Blanking (§7): a run
that overlaps a motor pulse, from its start until ``BLANKING_MS`` after it
ends, is ignored. The feed keeps its own short history of pulses (``motor``)
because samples reach it up to a batch late, after the motor has already
moved on; it is also the game's ``blank_fn``. The thresholds are attributes
(``thr2`` = run level in mg squared, ``peak2`` = spike peak in mg squared,
``spike_min_ms``, ``spike_max_ms``,
``refractory_ms``, ``blank_ms``), so a notebook can tune them on a running
feed.

The per-sample work is one integer kernel (``feed_kernel``): gravity,
spike runs and block sums, compiled with @micropython.viper on the watch
(``KERNEL``: "viper", after a self-check against the plain Python version)
and run as plain Python elsewhere (CPython, the wasm port). It reports block
sums and run starts/ends as records; only those reach Python objects. (The
kernel relies on 32-bit viper words: on a 64-bit port ``ptr32`` loads are
not sign-extended, the self-check fails and the plain version runs.)
"""

from array import array

from finder import tuning as T
from finder.compat import ticks_add, ticks_diff
from finder.motion import MotionTracker
from finder.haptic_patterns import BLANKING_MS

SLOW_HZ = 100             # tracker only (hal.bma423 default odr)
FAST_HZ = T.BUMP_ODR_HZ   # while a bump can count
SPIKE_G = T.BUMP_SPIKE_G                      # ui-spec §6 HOT (tokens thresholds.bump_spike)
RUN_G = T.BUMP_RUN_G
SPIKE_MIN_MS, SPIKE_MAX_MS = T.BUMP_SPIKE_MS
REFRACTORY_MS = T.BUMP_REFRACTORY_MS          # ringing after a knock is not a second bump
RESYNC_MIN_MS = 10        # batch clock this far from ``now`` (or 4 samples): re-anchor
G_SHIFT = 4               # gravity low-pass at 100 Hz: tau ~ 16 samples (160 ms)
_NP = 4                   # motor pulses remembered for blanking

# kernel state (array 'i')
S_GX = 0                  # gravity x, y, z in mg << shift
S_SHIFT = 3
S_THR2 = 4
S_FAST = 5
S_RUN = 6                 # samples in the open run (0: none)
S_PK = 7                  # its peak |a - g|^2
S_SX = 8                  # block sums x, y, z and count
S_SN = 11
S_DEC = 12
S_RMAX = 13               # run length past which gravity follows again
S_SEED = 14               # 1 once gravity is seeded
S_N = 15
# kernel records (array 'i', 5 ints each)
R_BLOCK = 1               # (1, i, sx, sy, sz): block of ``dec`` samples ends at i
R_START = 2               # (2, i, 0, 0, 0): run starts at sample i
R_END = 3                 # (3, i, run, peak, 0): run ended before sample i

_FSRC = """
def feed_kernel(mg, n: int, st, ev) -> int:
    ap = ptr16(mg)
    sp = ptr32(st)
    ep = ptr32(ev)
    gx = sp[0]
    gy = sp[1]
    gz = sp[2]
    s = sp[3]
    thr2 = sp[4]
    fast = sp[5]
    run = sp[6]
    pk = sp[7]
    sx = sp[8]
    sy = sp[9]
    sz = sp[10]
    sn = sp[11]
    dec = sp[12]
    rmax = sp[13]
    seed = sp[14]
    k = 0
    m = 0
    i = 0
    j = 0
    while i < n:
        x = int(ap[j])
        y = int(ap[j + 1])
        z = int(ap[j + 2])
        if x > 32767:
            x -= 65536
        if y > 32767:
            y -= 65536
        if z > 32767:
            z -= 65536
        if not seed:
            gx = x << s
            gy = y << s
            gz = z << s
            seed = 1
        follow = 1
        if fast:
            dx = x - (gx >> s)
            dy = y - (gy >> s)
            dz = z - (gz >> s)
            m2 = dx * dx + dy * dy + dz * dz
            if m2 > thr2:
                if not run:
                    ep[k] = 2
                    ep[k + 1] = i
                    ep[k + 2] = 0
                    ep[k + 3] = 0
                    ep[k + 4] = 0
                    k += 5
                    m += 1
                    pk = 0
                run += 1
                if m2 > pk:
                    pk = m2
                if run <= rmax:
                    follow = 0
            elif run:
                ep[k] = 3
                ep[k + 1] = i
                ep[k + 2] = run
                ep[k + 3] = pk
                ep[k + 4] = 0
                k += 5
                m += 1
                run = 0
        if follow:
            gx += x - (gx >> s)
            gy += y - (gy >> s)
            gz += z - (gz >> s)
        sx += x
        sy += y
        sz += z
        sn += 1
        if sn >= dec:
            ep[k] = 1
            ep[k + 1] = i
            ep[k + 2] = sx
            ep[k + 3] = sy
            ep[k + 4] = sz
            k += 5
            m += 1
            sx = 0
            sy = 0
            sz = 0
            sn = 0
        i += 1
        j += 3
    sp[0] = gx
    sp[1] = gy
    sp[2] = gz
    sp[6] = run
    sp[7] = pk
    sp[8] = sx
    sp[9] = sy
    sp[10] = sz
    sp[11] = sn
    sp[14] = seed
    return m
"""


def _ident(x):
    return x


# Self-check batch (x, y, z mg): rest, a 3-sample knock, rest, a long slap
# that outlasts rmax (gravity follows), negative axes, then rest.
_CHECK = ([(12, -8, 1000)] * 5 + [(2900, -2500, 3999)] * 3 + [(10, -6, 998)] * 6
          + [(-3500, 1200, -4000)] * 12 + [(-20, 4, 1003)] * 9)


def kernel_agrees(ka, kb):
    """True if kernels ``ka`` and ``kb`` give the same records and state on
    the ``_CHECK`` batch, fast and slow, in one call and in two halves."""
    n = len(_CHECK)
    mg = array("h", [0] * (3 * n))
    for i in range(n):
        mg[3 * i], mg[3 * i + 1], mg[3 * i + 2] = _CHECK[i]
    half = array("h", mg[3 * 17:])
    for fast in (1, 0):
        out = []
        for fn in (ka, kb):
            st = array("i", [0] * S_N)
            st[S_SHIFT] = 7
            st[S_THR2] = 2000 * 2000
            st[S_FAST] = fast
            st[S_DEC] = 4
            st[S_RMAX] = 6
            ev = array("i", [0] * (5 * (2 * n + 2)))
            k = fn(mg, n, st, ev)
            r = bytes(ev[:5 * k]) + bytes(st)
            k = fn(half, n - 17, st, ev)
            out.append(r + bytes(ev[:5 * k]) + bytes(st))
        if out[0] != out[1]:
            return False
    return True


def _compile_kernel():
    ns = {"ptr16": _ident, "ptr32": _ident}
    exec(_FSRC, ns)
    py = ns["feed_kernel"]
    try:
        vs = {}
        exec("@micropython.viper" + _FSRC, vs)
    except Exception:  # noqa: BLE001 - no viper (CPython, wasm): plain Python
        return py, "python"
    if not kernel_agrees(py, vs["feed_kernel"]):
        return py, "python (viper self-check failed)"
    return vs["feed_kernel"], "viper"


feed_kernel, KERNEL = _compile_kernel()


class ImuFeed:
    """Drains a BMA423-like FIFO (``fifo_read_mg`` + ``fifo_mg``; ``set_odr``
    and ``odr`` for the fast rate). ``z_sign`` None: the imu's own
    (``hal.board``). The feed starts at the imu's ``odr`` (default
    ``SLOW_HZ``); the first ``set_fast`` puts the chip where it belongs."""

    def __init__(self, imu, on_tap=None, out_hz=50, z_sign=None):
        self.imu = imu
        self.out_hz = out_hz
        if z_sign is None:
            z_sign = getattr(imu, "z_sign", 1)
        self.tracker = MotionTracker(rate_hz=out_hz, z_sign=z_sign)
        self.on_tap = on_tap
        thr = int(RUN_G * 1000)
        self.thr2 = thr * thr
        pk = int(SPIKE_G * 1000)
        self.peak2 = pk * pk
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
        self.n_low = 0            # runs that never reached the spike peak
        self.n_blanked = 0
        self.last_tap = None
        self.peak_mg = 0          # largest |a - g| seen (debug / tuning)
        self._st = array("i", [0] * S_N)
        cap = len(imu.fifo_mg) // 3
        self._ev = array("i", [0] * (5 * (2 * cap + 2)))   # <= 2 records a sample
        self._run_t = 0
        self.hz = 0
        hz = getattr(imu, "odr", SLOW_HZ)
        self.fast = hz != SLOW_HZ     # a chip left fast: set_fast(False) slows it
        self._rate(hz)

    def _rate(self, hz):
        self.hz = hz
        d = hz // self.out_hz
        self.dec = d if d > 1 else 1
        self._k = 1.0 / (1000.0 * self.dec)
        self._dq = 256000 // hz   # sample period, ms in Q8
        r = 4000 // hz
        self._resync = r if r > RESYNC_MIN_MS else RESYNC_MIN_MS
        s = G_SHIFT
        r = hz // SLOW_HZ
        while r > 1:              # same 160 ms gravity tau at any rate
            r >>= 1
            s += 1
        st = self._st
        s0 = st[S_SHIFT]
        for a in range(S_GX, S_GX + 3):   # gravity carries over at the new scale
            st[a] = (st[a] >> s0) << s
        st[S_SHIFT] = s
        st[S_FAST] = 1 if self.fast else 0
        st[S_RUN] = 0                     # a run open at the switch is dropped
        st[S_SX] = st[S_SX + 1] = st[S_SX + 2] = st[S_SN] = 0
        st[S_DEC] = self.dec
        self.g_shift = s
        self.tap = None
        self._tb = None           # this batch: first sample at _tb + _f0/256 ms
        self._f0 = 0
        self._n = 0               # ... and its size

    def set_fast(self, on):
        """``FAST_HZ`` (spikes detected) or ``SLOW_HZ``; samples still in the
        FIFO are dropped (``imu.set_odr`` flushes it). Raises OSError from
        the bus; the feed then follows whatever rate the chip reports
        (``imu.odr``), and the next call retries."""
        on = bool(on)
        if on == self.fast:
            return
        hz = FAST_HZ if on else SLOW_HZ
        imu = self.imu
        try:
            imu.set_odr(hz)
        finally:
            odr = getattr(imu, "odr", hz)
            if odr != self.hz:
                self.fast = odr != SLOW_HZ
                self._rate(odr)

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
        """Time (ms) of sample ``i`` of this batch (negative: an earlier one)."""
        return ticks_add(self._tb, (self._f0 + i * self._dq) >> 8)

    def _stamp(self, now, n):
        """Batch clock: the last of ``n`` samples is taken at ``now``, unless
        the previous batch's clock continues within ``_resync`` ms."""
        dq = self._dq
        tb = ticks_add(now, -(((n - 1) * dq) >> 8))
        f0 = 0
        q = self._tb
        if q is not None:
            q0 = self._f0 + self._n * dq      # first sample after the last batch
            q = ticks_add(q, q0 >> 8)
            q0 &= 255
            e = ticks_diff(q, tb)
            r = self._resync
            if (-r <= e <= r
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
        self._stamp(now, n)
        st = self._st
        st[S_THR2] = self.thr2
        st[S_RMAX] = self.spike_max_ms * self.hz // 1000 + 1
        ev = self._ev
        m = feed_kernel(imu.fifo_mg, n, st, ev)
        if m:
            tr = self.tracker
            k = self._k
            for r in range(0, 5 * m, 5):
                c = ev[r]
                if c == R_BLOCK:
                    tr.add_sample(self._at(ev[r + 1]), ev[r + 2] * k, ev[r + 3] * k,
                                  ev[r + 4] * k)
                elif c == R_START:
                    self._run_t = self._at(ev[r + 1])
                else:
                    self._end_run(self._at(ev[r + 1]), ev[r + 2], ev[r + 3])
        self.n_samples += n
        return n

    def _end_run(self, t_end, run, pk):
        w = run * (1000000 // self.hz)          # us
        t0 = self._run_t
        if pk > self.peak_mg * self.peak_mg:
            self.peak_mg = int(pk ** 0.5)
        if pk < self.peak2:
            self.n_low += 1                     # a wobble, never a spike
            return
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
