"""app/imu_feed.py: ImuFeed on a scripted BMA423-like FIFO, past what
tests/test_app_runtime.py reaches: noisy rest on every axis sign, knocks 1 to
14 samples wide and slow swings at 800 and 100 Hz, polls of 1 to 170 samples
(and a FIFO that overflowed) at times that run ahead of and behind the
sample clock, motor pulses beyond the 4 the feed remembers, blanking edges,
the clock across the 2^30 tick wrap, rate switches with gravity on negative
axes, bus errors on reads and on both halves of set_odr, odd rates and output
rates, z_sign from the chip or given, and the thresholds tuned on a running
feed. One line per poll: samples, the tap, and the feed's counters."""

from array import array

from app.imu_feed import ImuFeed, FAST_HZ
from finder.compat import ticks_add, ticks_diff

WRAP = 1 << 30


class Fifo:
    """The scripted chip: ``queue(n)`` samples of ``wave`` for the next read
    (the newest 170 stay); ``fail`` makes the next read ("read") or set_odr
    ("write": nothing set, "flush": rate set, FIFO flush failed) raise."""

    def __init__(self, wave, odr=100, z_sign=None):
        self.fifo_mg = array("h", [0] * (3 * 170))
        self.odr = odr
        if z_sign is not None:
            self.z_sign = z_sign
        self.wave = wave
        self.k = 0            # samples made
        self.n = 0            # samples waiting
        self.fail = None

    def set_odr(self, hz):
        if self.fail == "write":
            self.fail = None
            raise OSError(5)
        self.odr = hz
        self.n = 0
        if self.fail == "flush":
            self.fail = None
            raise OSError(5)

    def queue(self, n):
        self.n += n

    def fifo_read_mg(self):
        if self.fail == "read":
            self.fail = None
            raise OSError(5)
        n = self.n
        if n > 170:
            self.k += n - 170
            n = 170
        a = self.fifo_mg
        for i in range(n):
            x, y, z = self.wave(self.k)
            a[3 * i] = x
            a[3 * i + 1] = y
            a[3 * i + 2] = z
            self.k += 1
        self.n = 0
        return n


class Lcg:
    def __init__(self, seed):
        self.x = seed

    def next(self, m):
        self.x = (self.x * 1103515245 + 12345) & 0x7FFFFFFF
        return (self.x >> 8) % m


def wave_of(g, events, seed):
    """Gravity ``g`` (mg) with +-6 mg noise, plus ``events``: (first sample,
    width, (dx, dy, dz) mg)."""
    rng = Lcg(seed)

    def wave(k):
        x, y, z = g
        x += rng.next(13) - 6
        y += rng.next(13) - 6
        z += rng.next(13) - 6
        for k0, w, d in events:
            if k0 <= k < k0 + w:
                x += d[0]
                y += d[1]
                z += d[2]
        return (max(-4000, min(3999, x)), max(-4000, min(3999, y)), max(-4000, min(3999, z)))
    return wave


class Run:
    def __init__(self, fifo, t, **kw):
        self.fifo = fifo
        self.t = t
        self.taps = []
        self.f = ImuFeed(fifo, on_tap=self.taps.append, **kw)

    def line(self, what):
        f = self.f
        return "%s t=%d hz=%d tap=%s taps=%d rej=%d low=%d blank=%d peak=%d n=%d gz=%r" % (
            what, self.t, f.hz, f.tap, f.n_taps, f.n_rejected, f.n_low, f.n_blanked, f.peak_mg,
            f.n_samples, f.tracker.gz)

    def poll(self, ms, n=None):
        """``ms`` later, a poll that finds ``n`` samples (None: what the rate made)."""
        self.t = ticks_add(self.t, ms)
        self.fifo.queue(ms * self.f.hz // 1000 if n is None else n)
        try:
            k = self.f.poll(self.t)
        except OSError:
            return self.line("poll error")
        return self.line("poll %d" % k)

    def fast(self, on):
        try:
            self.f.set_fast(on)
        except OSError:
            return self.line("fast %d error" % on)
        return self.line("fast %d" % on)

    def motor(self, ms, level):
        self.t = ticks_add(self.t, ms)
        self.f.motor(self.t, level)
        return "motor %d %r" % (self.t, level)

    def blanked(self, back):
        """blanked() at ``back`` ms before now."""
        t = ticks_add(self.t, -back)
        return "blanked %d %d" % (t, self.f.blanked(t))


def knocks(start, gap, widths, d):
    return [(start + k * gap, w, d) for k, w in enumerate(widths)]


def lines():
    yield "# poll|fast|motor|blanked t=... hz tap taps rejected low blanked peak_mg n_samples tracker.gz"
    # 800 Hz across the tick wrap: knocks 1..14 samples wide on every axis, polls of every size
    ev = knocks(400, 160, range(1, 15), (2900, -1500, 1200)) + knocks(3000, 200, (2, 3, 5), (-3000, 0, -2500))
    r = Run(Fifo(wave_of((40, -30, 990), ev + [(4000, 120, (900, 900, 0))], 1)), WRAP - 1500, out_hz=25)
    yield r.fast(True)
    rng = Lcg(7)
    for k in range(260):
        yield r.poll(3 + rng.next(37))
    yield r.poll(400)                        # the FIFO overflowed: the newest 170
    yield r.poll(7, 0)
    yield r.poll(20, 3)                      # samples come late: the batch clock re-anchors
    yield r.poll(5, 30)                      # ... and early
    for k in range(40):
        yield r.poll(20)
    # motor pulses: more than the 4 remembered, a knock in and after each
    ev = []
    for k in range(7):
        ev.append((100 + 240 * k, 2, (0, 0, 2800)))
        ev.append((100 + 240 * k + 120, 2, (0, 0, 2800)))
    r = Run(Fifo(wave_of((0, 0, 1000), ev, 2), odr=FAST_HZ), WRAP - 200, out_hz=50)
    for k in range(7):
        yield r.motor(110 if k else 120, 1.0)
        yield r.blanked(0)
        yield r.motor(60, 0.0)
        yield r.blanked(0)
        yield r.blanked(61)
        for j in range(5):
            yield r.poll(14)
        yield r.blanked(r.f.blank_ms + 4 * 14 + 1)
        yield r.blanked(r.f.blank_ms + 4 * 14)
    yield r.motor(10, 0.5)
    yield r.motor(10, 0.25)                  # still on: one pulse
    yield r.motor(10, 0)
    for back in (0, 9, 10, 11, 25, 200, 400, 900, 2000):
        yield r.blanked(back)
    # tuned on a running feed: lower run level and peak, wider spikes, short refractory and blanking
    r.fifo.wave = wave_of((0, 0, 1000), knocks(r.fifo.k + 100, 120, (1, 4, 9, 14, 20, 2, 2), (0, 1300, 0)), 3)
    f = r.f
    f.thr2 = 300 * 300
    f.peak2 = 1200 * 1200
    f.spike_min_ms = 2
    f.spike_max_ms = 20
    f.refractory_ms = 50
    f.blank_ms = 80
    for k in range(60):
        yield r.poll(19)
    # face down (chip z_sign -1 and given), slow and fast, bus errors on every call
    r = Run(Fifo(wave_of((-20, 15, -1000), knocks(300, 200, (2, 3), (-2500, 300, -2000)), 4), z_sign=-1), 1000,
            out_hz=25)
    for k in range(30):
        yield r.poll(23)
    r.fifo.fail = "write"
    yield r.fast(True)
    r.fifo.fail = "flush"
    yield r.fast(True)
    yield r.fast(True)
    r.fifo.fail = "read"
    yield r.poll(25)
    for k in range(40):
        yield r.poll(25)
    r.fifo.fail = "flush"
    yield r.fast(False)
    for k in range(20):
        yield r.poll(31)
    r = Run(Fifo(wave_of((500, -700, -500), (), 5), z_sign=1), 50, out_hz=100, z_sign=-1)
    for k in range(20):
        yield r.poll(17)
    # odd rates: 400 Hz left by an earlier run; 50 Hz, under the output rate
    r = Run(Fifo(wave_of((-300, 900, 300), knocks(200, 150, (1, 2, 4, 6), (1500, -1800, 900)), 6), odr=400),
            0, out_hz=50)
    for k in range(40):
        yield r.poll(21)
    yield r.fast(False)
    yield r.fast(True)
    for k in range(40):
        yield r.poll(29)
    r = Run(Fifo(wave_of((10, 990, -60), (), 7), odr=50), 300, out_hz=100)
    for k in range(30):
        yield r.poll(45)
    yield r.fast(True)
    yield r.fast(False)
    for k in range(10):
        yield r.poll(45)
    # a poll before any sample; rate switches carry gravity on negative axes over (a knock
    # right after each switch counts only if it did)
    r = Run(Fifo(wave_of((-1000, -40, 8), (), 8)), 0, out_hz=25)
    yield r.poll(0, 0)
    for k in range(12):
        yield r.fast(k % 2 == 0)
        if k % 2 == 0:
            r.fifo.wave = wave_of((-1000, -40, 8), [(r.fifo.k + 20, 2, (-2200, 0, 1500))], 9 + k)
        for j in range(6):
            yield r.poll(41)
    yield "end %d" % ticks_diff(r.t, 0)
