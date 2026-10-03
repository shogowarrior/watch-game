"""app.runtime / app.imu_feed / app.telemetry on fake hardware.

The main test runs TWO ``Runtime`` instances in one process for a simulated
minute on one fake clock: SimRadio pair, fake BMA423 FIFO, fake touch, fake
PEK/battery, a counting display and the real ``hal.haptics.Motor`` on the
fake PWM. Both players press the side key to confirm the runes; later watch B
opens the menu with a long touch and taps BUZZ twice (FULL -> EVENTS -> OFF).

On MicroPython the real ``ui.renderer.Renderer`` draws every frame; CPython
has no ``framebuf``, so a stub renderer with the same ``frame`` contract is
used there.
"""

from array import array

from tests import fakes

MAC_A = b"\x24\x0a\xc4\x10\x00\x0a"
MAC_B = b"\x24\x0a\xc4\x10\x00\x0b"
RUN_MS = 60000


# ---- fake hardware ------------------------------------------------------------------
class Clock:
    def __init__(self, t=0):
        self.now = t

    def __call__(self):
        return self.now

    def sleep(self, ms):
        self.now += ms


class FakeIMU:
    """BMA423-like FIFO in milli-g at 100 Hz (or the ``set_odr`` rate):
    face-up gravity plus scripted spikes."""

    def __init__(self, clock, spikes=()):
        self.clock = clock
        self.fifo_mg = array("h", [0] * (170 * 3))
        self.spikes = list(spikes)     # (t_ms, samples wide, extra mg on z)
        self.odr = 100
        self.odrs = []                 # set_odr calls
        self.t0 = clock.now            # sample k (from 1) is taken k / odr s after t0
        self.k = 0                     # samples read

    def set_odr(self, hz):
        self.odr = hz
        self.odrs.append(hz)
        self.t0 = self.clock.now       # FIFO emptied
        self.k = 0

    def fifo_read_mg(self):
        hz = self.odr
        k1 = (self.clock.now - self.t0) * hz // 1000
        n = k1 - self.k
        if n > 170:                    # stream mode: the newest 170 stay
            self.k = k1 - 170
            n = 170
        a = self.fifo_mg
        for i in range(n):
            t_us = self.t0 * 1000 + (self.k + i + 1) * 1000000 // hz
            z = 1000 + (3 if i & 1 else -3)
            for t0, w, mg in self.spikes:
                if t0 * 1000 <= t_us < t0 * 1000 + w * 1000000 // hz:
                    z += mg
            a[3 * i] = 5
            a[3 * i + 1] = -4
            a[3 * i + 2] = z if z < 4000 else 3999
        self.k = k1
        return n


class ChipIMU(FakeIMU):
    """FakeIMU plus a BMA423 feature engine that comes up on the 3rd poll
    (``ok`` None: no blob, False: init fails); ``events`` latch until read."""

    def __init__(self, clock, ok):
        FakeIMU.__init__(self, clock)
        self.ok = ok
        self.feat_state = 0
        self.polls = 0
        self.calls = []
        self.events = 0

    def start_features(self):
        self.calls.append("start")
        if self.ok is None:
            return False               # bma423conf.bin missing
        self.feat_state = 1
        return True

    def poll_features(self):
        self.polls += 1
        if self.polls >= 3 and self.feat_state == 1:
            self.feat_state = 2 if self.ok else -1
            if self.ok:
                self.calls.append("on")
        return self.feat_state

    def features_ok(self):
        return self.feat_state == 2

    def steps(self):
        return 0

    def activity(self):
        return 0

    def poll_events(self):
        ev = self.events
        self.events = 0
        return ev


class FakeTouch:
    def __init__(self, clock, presses=()):
        self.clock = clock
        self.presses = list(presses)   # (t_down, t_up, x, y)
        self.contacts = 1              # fingers while touching (2: multi-touch)
        self.r = [False, 0, 0, 0]

    def read(self):
        t = self.clock.now
        r = self.r
        r[0] = False
        r[3] = 0
        for t0, t1, x, y in self.presses:
            if t0 <= t < t1:
                r[0] = True
                r[1] = x
                r[2] = y
                r[3] = self.contacts
        return r


class FakePMU:
    def __init__(self, clock, events=(), pct=90):
        self.clock = clock
        self.events = list(events)     # (t_ms, EV_* mask)
        self.pct = pct
        self.off = False
        self.fail_off = 0              # shutdown() raises OSError this many times
        self.cleared = 0

    def set_long_press_ms(self, ms):
        pass

    def clear_irqs(self):
        self.cleared += 1
        self.events = [e for e in self.events if e[0] > self.clock.now]

    def poll(self):
        ev = 0
        now = self.clock.now
        while self.events and self.events[0][0] <= now:
            ev |= self.events.pop(0)[1]
        return ev

    def battery_percent(self):
        return self.pct

    def battery_voltage(self):
        return 3900

    def is_charging(self):
        return False

    def shutdown(self):
        if self.fail_off:
            self.fail_off -= 1
            raise OSError(5)
        self.off = True


class FakeDisplay:
    def __init__(self):
        self.pushes = 0
        self.level = None
        self.asleep = False

    def push_strip(self, y0, h, buf):
        self.pushes += 1

    def brightness(self, level=None):
        if level is not None:
            self.level = level
        return self.level

    def sleep(self):
        self.asleep = True

    def wake(self, level=None, wait=None):
        self.asleep = False
        self.brightness(level)


class StubRenderer:
    """``ui.renderer.Renderer.frame`` contract without framebuf (CPython)."""

    def __init__(self):
        self.buf = bytearray(240 * 24 * 2)
        self._hb_t = None

    def frame(self, p, display=None, now=0):
        hb = None
        if p.heartbeat:
            per = p.pulse_period_ms * (p.heartbeat_every or 1)
            if self._hb_t is None or now - self._hb_t >= per:
                self._hb_t = now
                hb = p.heartbeat
        if display is not None:
            for s in range(10):
                display.push_strip(s * 24, 24, self.buf)
        return [hb] if hb else ()           # heartbeats only: params.haptic is the runtime's


def _renderer():
    try:
        from ui.renderer import Renderer      # needs framebuf (MicroPython)
        return Renderer()
    except ImportError:
        return StubRenderer()


class Board:
    """Duck-typed board: plain attributes (no lazy bring-up)."""

    def __init__(self, **parts):
        for k in parts:
            setattr(self, k, parts[k])


def _watch(clock, radio, imu_spikes=(), touches=(), buttons=()):
    from hal.haptics import Motor
    from app.runtime import Runtime
    b = Board(pmu=FakePMU(clock, buttons), display=FakeDisplay(),
              imu=FakeIMU(clock, imu_spikes), touch=FakeTouch(clock, touches),
              haptics=Motor(), radio=radio)
    gcn = [0]

    def collect():
        gcn[0] += 1

    rt = Runtime(b, clock=clock, sleep_ms=clock.sleep, renderer=_renderer(),
                 gc_collect=collect)
    rt.gc_count = gcn
    return rt


# ---- the two-watch minute -----------------------------------------------------------
def test_two_watches_one_minute():
    fakes.install()
    from hal.radio import SimRadio
    from finder.game import BUZZ_OFF, BUZZ_FULL, M_PAIRING
    from finder import pairing as P
    from hal.axp202 import EV_SHORT
    from finder import tuning as T
    clock = Clock(0)
    ra = SimRadio(MAC_A, seed=11).begin()
    rb = SimRadio(MAC_B, seed=22).begin()
    ra.connect(rb, rssi=-50)
    a = _watch(clock, ra, imu_spikes=((1000, 2, 3000), (1500, 6, 3000), (20000, 2, 3000)),
               buttons=((2000, EV_SHORT),))
    y_buzz = T.MENU_ROWS_Y[2] + T.MENU_ROW_H // 2       # row 2: BUZZ
    y_resume = T.MENU_ROWS_Y[0] + T.MENU_ROW_H // 2     # row 0: RESUME
    b = _watch(clock, rb, buttons=((2500, EV_SHORT),),
               touches=((45000, 46000, 120, 120),          # long press: MENU
                        (47000, 47080, 120, y_buzz),       # BUZZ: EVENTS
                        (48000, 48080, 120, y_buzz),       # BUZZ: OFF
                        (49000, 49080, 120, y_resume)))    # RESUME
    rts = (a, b)
    for rt in rts:
        rt.begin(0)
    off_at = None
    off_idx = 0
    confirmed_seen = False
    while clock.now < RUN_MS:
        w = 1000
        for rt in rts:
            d = rt.step(clock.now)
            if d < w:
                w = d
        if b.game.pair.sub == P.CALIBRATE and a.game.pair.sub == P.CALIBRATE:
            confirmed_seen = True
        if off_at is None and b.game.buzz == BUZZ_OFF:
            off_at = clock.now
            off_idx = len(b.motor._pwm.history)
            a_idx = len(a.motor._pwm.history)
        clock.sleep(w if w > 0 else 1)

    # frames at the capped rate (20 fps, screens stay on: face-up)
    for rt in rts:
        assert not rt.errors, rt.errors
        assert rt.game.screen_on and not rt.display.asleep
        n = rt.frames
        assert 0.95 * 20 * RUN_MS / 1000 <= n <= 20 * RUN_MS / 1000 + 2, n
        assert rt.display.pushes == 10 * n
        assert rt.ticks >= 0.98 * RUN_MS / 100, rt.ticks
        assert rt.display.level is not None and rt.display.level > 0
        assert rt.gc_count[0] >= 30, rt.gc_count
        assert not any(rt.io_errors), rt.io_errors
        assert rt.fps > 18.0, rt.fps

    # beacons both ways, partner locked in the LinkMonitor
    assert ra.n_tx > 600 and rb.n_tx > 600, (ra.n_tx, rb.n_tx)
    assert a.link.n_rx > 300 and b.link.n_rx > 300, (a.link.n_rx, b.link.n_rx)
    assert a.link.partner == MAC_B and b.link.partner == MAC_A
    assert a.tx.seq == ra.n_tx

    # pairing: both confirmed -> calibrate -> split -> hunt
    assert confirmed_seen
    for rt in rts:
        assert rt.game.pair.peer_mac is not None
        assert rt.game.mode != M_PAIRING, (rt.game.mode, rt.game.pair.sub)
    assert a.game.pair.runes == b.game.pair.runes

    # bump spikes, sampled fast only in PAIRING seen / confirmed: 2.5 ms
    # accepted, 7.5 ms (a shake) rejected; the split's 100 Hz sees none
    assert a.board.imu.odrs[:2] == [T.BUMP_ODR_HZ, 100], a.board.imu.odrs
    assert a.feed.fast == a.game.bump_armed()
    assert a.feed.n_taps == 1, (a.feed.n_taps, a.feed.n_rejected)
    assert abs(a.feed.last_tap - 1000) <= 2, a.feed.last_tap
    assert a.feed.n_rejected == 1
    assert a.feed.n_samples >= 5900

    # haptics: the motor was driven; buzz OFF (via the menu) silenced watch B
    assert any(a.motor._pwm.history)
    assert any(b.motor._pwm.history[:off_idx])
    assert off_at is not None and 48000 <= off_at < 49000, off_at
    assert not any(b.motor._pwm.history[off_idx:]), b.motor._pwm.history[off_idx:]
    assert b.motor.duty_u16 == 0
    assert a.game.buzz == BUZZ_FULL
    assert any(a.motor._pwm.history[a_idx:])     # A (FULL) kept buzzing
    assert not b.game.menu_open
    for rt in rts:
        assert not rt.pmu.off and not rt.powered_off

    # timing stats are printable from the REPL
    n = a.frames
    s = a.stats()
    assert s["frames"] == n and s["render"][2] == n
    assert s["collect"][0] == a.gc_count[0] and s["gc"][2] == a.gc_count[0]
    assert a.frames == 0


# ---- smaller pieces -----------------------------------------------------------------
def test_missing_parts_and_screen_off():
    fakes.install()
    from app.runtime import Runtime
    clock = Clock(0)
    d = FakeDisplay()
    imu = FakeIMU(clock)
    b = Board(display=d, imu=imu)
    rt = Runtime(b, parts=("display", "imu", "touch", "pmu", "radio", "haptics"),
                 clock=clock, sleep_ms=clock.sleep, renderer=_renderer(),
                 gc_collect=lambda: None)
    rt.run(max_ms=1000)
    assert "touch" in rt.errors and "radio" in rt.errors and "pmu" in rt.errors
    assert rt.frames >= 18 and d.pushes == 10 * rt.frames
    # face down (z = -1 g) for > WRIST_DOWN_MS: display sleeps, frames stop
    imu.spikes.append((clock.now, 100000, -2000))
    n0 = d.pushes
    rt.run(max_ms=4000)
    assert d.asleep and not rt.screen_is_on and rt.bl_level == 0.0
    assert d.pushes < n0 + 10 * 20 * 3
    n1 = d.pushes
    rt.run(max_ms=1000)
    assert d.pushes == n1
    # a new runtime on the same board (notebook re-run): it wakes the asleep panel
    del imu.spikes[:]
    rt = Runtime(b, parts=("display", "imu"), clock=clock, sleep_ms=clock.sleep,
                 renderer=_renderer(), gc_collect=lambda: None)
    rt.run(max_ms=500)
    assert not d.asleep and rt.frames > 0 and d.level > 0, (d.asleep, rt.frames, d.level)


def test_no_renderer_uses_metronome():
    fakes.install()
    from hal.haptics import Motor
    from app.runtime import Runtime
    from finder.render_params import make_params
    clock = Clock(0)
    m = Motor()
    rt = Runtime(Board(haptics=m), clock=clock, sleep_ms=clock.sleep,
                 renderer=None, gc_collect=lambda: None)
    rt.begin(0)
    rt.renderer = None
    rt.params = make_params(t_ms=0, heartbeat="TICK", pulse_period_ms=500, ring_live=True)
    rt._fresh = True
    for _ in range(300):
        rt._stage_render(clock.now)
        rt._stage_haptic(clock.now)
        clock.sleep(10)
    assert rt.player.period_ms >= 500
    on = 0
    for d in m._pwm.history:
        if d:
            on += 1
    assert on >= 4, m._pwm.history
    # no packets (ghost ring, ui-spec §4 rule 5): the metronome stops
    rt.params = make_params(t_ms=clock.now, heartbeat="TICK", pulse_period_ms=500)
    for _ in range(10):                    # let a running pulse end
        rt._stage_render(clock.now)
        rt._stage_haptic(clock.now)
        clock.sleep(10)
    n = len(m._pwm.history)
    for _ in range(300):
        rt._stage_render(clock.now)
        rt._stage_haptic(clock.now)
        clock.sleep(10)
    assert rt.player.period_ms == 0 and not any(m._pwm.history[n:]), m._pwm.history[n:]


def test_imu_feed_spikes_blanking_and_rate():
    from app.imu_feed import ImuFeed, FAST_HZ
    clock = Clock(0)
    imu = FakeIMU(clock, spikes=((1000, 1, 3000), (1500, 4, 3000), (2000, 8, 3000),
                                 (2600, 2, 3000), (2700, 2, 3000), (4500, 2, 3000)))
    taps = []
    f = ImuFeed(imu, on_tap=taps.append)
    f.set_fast(True)                       # 800 Hz: 1.25 ms a sample
    f.motor(3000, 1.0)
    f.motor(3060, 0.0)
    imu.spikes.append((3150, 2, 3000))     # 90 ms after the pulse: blanked
    imu.spikes.append((3300, 2, 3000))     # 240 ms after: accepted
    while clock.now < 4000:
        clock.sleep(30)
        f.poll(clock.now)
    assert taps == [1000, 1500, 2600, 3300], taps
    assert f.n_blanked == 1
    assert f.n_rejected == 3               # 10 ms wide, refractory, blanked
    assert f.tracker.face_up
    assert abs(f.tracker.gz - 1.0) < 0.1
    assert f.dec == FAST_HZ // 50 and f.n_samples == clock.now * FAST_HZ // 1000
    assert f.blanked(3000) and f.blanked(3200) and not f.blanked(3210)
    assert not f.blanked(2999)
    f.set_fast(False)                      # 100 Hz: knocks are not looked for
    while clock.now < 5000:
        clock.sleep(30)
        f.poll(clock.now)
    assert len(taps) == 4 and f.n_rejected == 3
    assert imu.odrs == [FAST_HZ, 100] and f.dec == 2 and f.tracker.face_up


def test_telemetry_ring_and_state_record():
    fakes.install()
    from hal.radio import SimRadio
    from app.telemetry import Telemetry
    import json
    clock = Clock(0)
    r = SimRadio(MAC_A).begin()
    rt = _watch(clock, r)
    tl = Telemetry(cap=8, hz=5, dev="A", sid="t1")
    rt.tele = tl
    rt.run(max_ms=3000)
    assert tl.n >= 15
    ls = tl.lines()
    assert len(ls) == 8
    d = json.loads(ls[-1])
    for k in ("t", "ev", "rssi", "zone", "ui", "scr", "bl", "fps", "batt_pct", "seq",
              "steps_since_scan"):
        assert k in d, k
    assert d["dev"] == "A" and d["ev"] == "s" and d["scr"] is True
    assert d["batt_pct"] == 90 and d["batt_mv"] == 3900 and d["chg"] is False
    tl.event(5, "btn", ("kind", "short"))
    assert json.loads(tl.lines(1)[0]) == {"t": 5, "ev": "btn", "kind": "short"}
    assert len(tl.lines(3)) == 3


def test_real_board_drivers_short_run():
    """hal drivers on the fake buses: API use matches the drivers."""
    m = fakes.install()
    from hal import axp202
    from hal import bma423 as B
    pmu = m.add_axp202({0xB9: 80})
    w1c = axp202.REG_INTSTS1
    imu = m.add_i2c_device(0, 0x19, {0x00: 0x13})
    tp = m.add_i2c_device(1, 0x38, {0xA3: 0x64})
    clock = Clock(5000)
    frame = bytes((0, 0, 0, 0, 0x00, 0xE0))       # face-up: z = -1 g (hal/pins.py BMA423_Z_SIGN)

    def imu_read(reg, n, _r=imu.read):
        if reg == B.REG_FIFO_LENGTH_0:
            k = 6 * 3                             # 3 frames per poll
            return bytes((k & 0xFF, k >> 8))[:n]
        if reg == B.REG_FIFO_DATA:
            return (frame * (n // 6 + 1))[:n]
        return _r(reg, n)
    imu.read = imu_read

    def tp_read(reg, n, _r=tp.read):
        if reg == 0x02 and clock.now & 64:        # TD_STATUS: NACK now and then
            raise OSError(19)
        return _r(reg, n)
    tp.read = tp_read
    import hal.board as hb
    from app.runtime import Runtime
    board = hb.Board()
    rt = Runtime(board, clock=clock, sleep_ms=clock.sleep, renderer=_renderer(),
                 gc_collect=lambda: None)
    rt.begin(clock.now)
    pmu.regs[w1c + 2] = axp202.IRQ3_PEK_SHORT
    for _ in range(60):
        clock.sleep(max(1, rt.step(clock.now)))
        del m.spi_log[:]
    assert not rt.errors, rt.errors
    assert not any(rt.io_errors), rt.io_errors     # the touch driver counts its own
    assert rt.stats(reset=False)["touch_errors"] == board.touch.errors > 0
    assert rt.game.battery == 80
    assert rt.feed.n_samples > 0 and rt.feed.tracker.face_up
    assert rt.frames >= 5
    assert pmu.regs[w1c + 2] == 0          # PEK IRQ consumed
    assert pmu.regs[axp202.REG_POK_SET] & 0x30 == 0x10   # PEK long press 1.5 s (tokens)
    assert board.radio.n_tx >= 3


# ---- review fixes -------------------------------------------------------------------
class SlowDisplay(FakeDisplay):
    """Each strip costs ``strip_ms`` of the watch's clock (a 10-strip frame
    ~40 ms, as on the watch; hal/README.md)."""

    def __init__(self, clock, strip_ms=4):
        FakeDisplay.__init__(self)
        self.clock = clock
        self.strip_ms = strip_ms

    def push_strip(self, y0, h, buf):
        self.pushes += 1
        self.clock.now += self.strip_ms


class RecMotor:
    """Logs motor on/off edges on the watch's clock."""

    def __init__(self, clock):
        self.clock = clock
        self.lvl = 0
        self.log = []

    def set(self, lvl):
        if (lvl > 0) != (self.lvl > 0):
            self.log.append((self.clock.now, lvl > 0))
        self.lvl = lvl


def _pulses(log):
    """-> [(on_ms, gap_before_ms, gap_after_ms)] (None at the ends)."""
    out = []
    for i in range(len(log) - 1):
        t0, on = log[i]
        if not on or log[i + 1][1]:
            continue
        before = t0 - log[i - 1][0] if i > 0 else None
        after = log[i + 2][0] - log[i + 1][0] if i + 2 < len(log) else None
        out.append((log[i + 1][0] - t0, before, after))
    return out


def test_haptic_pulses_keep_floor_with_slow_frames():
    """Frames take 40 ms: renderer events start after the frame, the motor is
    serviced between strips and while idle, so pulses keep MIN_PULSE_MS."""
    fakes.install()
    from hal.radio import SimRadio
    from hal.axp202 import EV_SHORT
    from finder.haptic_patterns import MIN_PULSE_MS
    ra = SimRadio(MAC_A, seed=11).begin()
    rb = SimRadio(MAC_B, seed=22).begin()
    ra.connect(rb, rssi=-50)
    ca = Clock(0)
    cb = Clock(0)
    a = _watch(ca, ra, buttons=((2000, EV_SHORT),))
    b = _watch(cb, rb, buttons=((2500, EV_SHORT),))
    rts = ((a, ca), (b, cb))
    for rt, c in rts:
        rt.board.display = SlowDisplay(c)
        rt.board.haptics = RecMotor(c)
        rt.begin(0)
    due = [0, 0]
    end = 20000
    while ca.now < end or cb.now < end:
        k = 0 if due[0] <= due[1] else 1
        rt, c = rts[k]
        if c.now < due[k]:
            c.now = due[k]
        w = rt.step(c.now)
        rt.idle(w if w > 0 else 1)         # own clock: the other watch is independent
        due[k] = c.now
    strip = 4
    for rt, c in rts:
        assert rt.frames >= 0.95 * 20 * end / 1000, rt.frames
        p = _pulses(rt.motor.log)
        assert len(p) >= 6, rt.motor.log
        ticks = [on for on, g0, g1 in p if (g0 is None or g0 >= 200) and (g1 is None or g1 >= 200)]
        assert len(ticks) >= 3, p
        for on in ticks:                   # single TICK: exact start, end within a strip
            assert MIN_PULSE_MS <= on <= MIN_PULSE_MS + strip, p
        for on, g0, g1 in p:               # a later pulse may start a strip late, never shorter
            assert on >= MIN_PULSE_MS, p
        assert rt.feed.dec == 4            # tracker fed at 25 Hz


class JitterDisplay(SlowDisplay):
    """Strips cost 3, 4 or 5 ms in turn (SPI and GC jitter)."""

    def push_strip(self, y0, h, buf):
        self.pushes += 1
        self.clock.now += 3 + self.pushes % 3


def test_scan_countdown_ticks_all_reach_the_motor():
    """The scan ``ready`` countdown TICKs (1 s apart, ui-spec §6) each become a
    full motor pulse, though frames take ~40 ms with jitter."""
    fakes.install()
    from hal.radio import SimRadio
    from hal.axp202 import EV_SHORT
    from finder.haptic_patterns import MIN_PULSE_MS
    from finder.game import M_SCANNING
    ra = SimRadio(MAC_A, seed=3).begin()
    rb = SimRadio(MAC_B, seed=4).begin()
    ra.connect(rb, rssi=-50)
    ca = Clock(0)
    cb = Clock(0)
    a = _watch(ca, ra, buttons=((1000, EV_SHORT),))      # both confirm: calibrate
    b = _watch(cb, rb, buttons=((1000, EV_SHORT),))
    rts = ((a, ca), (b, cb))
    for rt, c in rts:
        rt.board.display = JitterDisplay(c)
        rt.board.haptics = RecMotor(c)
        rt.begin(0)
    due = [0, 0]
    t_scan = None
    while ca.now < 7000:
        if t_scan is None and ca.now >= 2000:
            t_scan = ca.now
            a.game._start_scan(t_scan)
            del a.motor.log[:]
        k = 0 if due[0] <= due[1] else 1
        rt, c = rts[k]
        if c.now < due[k]:
            c.now = due[k]
        w = rt.step(c.now)
        rt.idle(w if w > 0 else 1)
        due[k] = c.now
    assert a.game.mode == M_SCANNING
    on = [t for t, v in a.motor.log if v]
    p = _pulses(a.motor.log)
    assert len(on) >= 3, a.motor.log
    assert 950 <= on[1] - on[0] <= 1050 and 950 <= on[2] - on[1] <= 1050, on
    assert on[0] - t_scan <= 500 + 100 + 60, (t_scan, on)   # flat held 0.5 s, logic + frame
    for d, g0, g1 in p[:3]:
        assert d >= MIN_PULSE_MS, p


class _AxpBus:
    """Real ``hal.axp202.AXP202`` on the fake I2C bus (W1C IRQ status)."""

    def __init__(self, m, pct, vbus):
        from hal import axp202
        self.dev = m.add_axp202({0xB9: pct, 0x00: 0x20 if vbus else 0x00, 0x32: 0x46})
        self.pmu = axp202.AXP202(m.I2C(0, scl=m.Pin(22), sda=m.Pin(21)))


def _run(rt, clock, ms):
    """Step ``ms``; return the haptic names the logic ticks raised."""
    haps = []
    n = rt.ticks
    t_end = clock.now + ms
    while clock.now < t_end and not rt.powered_off:
        clock.sleep(max(1, rt.step(clock.now)))
        if rt.ticks != n and rt.params.haptic:
            haps.append(rt.params.haptic)
        n = rt.ticks
    return haps


def test_low_battery_shutdown_needs_confirmation_and_no_usb():
    m = fakes.install()
    from hal.radio import SimRadio
    from app.runtime import BATT_LOW_READS, BATT_RECHECK_MS
    from finder import tuning as T
    bus = _AxpBus(m, pct=2, vbus=True)
    clock = Clock(1000)
    rt = _watch(clock, SimRadio(MAC_A).begin())
    rt.board.pmu = bus.pmu
    rt.begin(clock.now)
    _run(rt, clock, 25000)                 # on USB: 2 % never powers off
    assert not rt.powered_off and rt.game._bye_t is None
    assert rt.game.battery == T.BATT_SHUTDOWN_PCT + 1
    assert bus.dev.regs[0x32] & 0x80 == 0
    bus.dev.regs[0x00] = 0                 # unplugged
    while not rt._low_n:                   # first off-USB reading (unconfirmed)
        t_low = clock.now
        clock.sleep(max(1, rt.step(t_low)))
    assert rt.game.battery == T.BATT_SHUTDOWN_PCT + 1 and rt.game._bye_t is None
    _run(rt, clock, 30000)
    assert rt.powered_off and not rt.running
    assert bus.dev.regs[0x32] == 0x46 | 0x80   # REG 32H bit 7 via the real driver
    assert rt.game._bye_t - t_low == (BATT_LOW_READS - 1) * BATT_RECHECK_MS, rt.game._bye_t - t_low
    assert rt.display.asleep and rt.motor.duty_u16 == 0


def test_one_low_battery_reading_is_ignored():
    """An unconfirmed shutdown-level reading never reaches the game: it keeps
    its last value (100 at boot), so no BATTERY 5% toast, BATT buzz or saver."""
    fakes.install()
    from hal.radio import SimRadio
    clock = Clock(1000)
    rt = _watch(clock, SimRadio(MAC_A).begin())
    pmu = rt.board.pmu
    pmu.pct = 0                            # a sagging voltage-fallback reading at boot
    rt.begin(clock.now)
    rt.step(clock.now)
    g = rt.game
    assert g.battery == 100 and not g.saver and g._toast is None, (g.battery, g._toast)
    pmu.pct = 60
    _run(rt, clock, 11000)
    assert g.battery == 60
    pmu.pct = 0                            # one more, at a later 10 s reading
    rt._t_batt = clock.now
    while not rt._low_n:
        clock.sleep(max(1, rt.step(clock.now)))
    pmu.pct = 60
    haps = _run(rt, clock, 3000)
    assert g.battery == 60 and not g.saver and g._toast is None, (g.battery, g._toast)
    assert "BATT" not in haps, haps
    assert not rt.powered_off and not pmu.off and g._bye_t is None


def test_one_sagging_ladder_reading_is_ignored():
    """One falling reading on the LOW-BATTERY ladder (5 % from a cell at 8 %)
    neither alarms nor latches the 5 % step, so the real 5 % still buzzes."""
    fakes.install()
    from hal.radio import SimRadio
    clock = Clock(1000)
    rt = _watch(clock, SimRadio(MAC_A).begin())
    pmu = rt.board.pmu
    pmu.pct = 8
    rt.begin(clock.now)
    _run(rt, clock, 11000)
    g = rt.game
    assert g.battery == 8, g.battery
    pmu.pct = 5                            # one sag at a later 10 s reading
    rt._t_batt = clock.now
    haps = _run(rt, clock, 500)
    assert rt._low_n == 1 and g.battery == 8, (rt._low_n, g.battery)
    pmu.pct = 8
    haps += _run(rt, clock, 12000)
    assert g.battery == 8 and g._toast is None, (g.battery, g._toast)
    assert "BATT" not in haps, haps
    pmu.pct = 5                            # the real 5 %
    haps = _run(rt, clock, 15000)
    assert haps.count("BATT") == 1 and g.battery == 5, (haps, g.battery)


def test_power_off_survives_panel_and_bus_errors():
    """A panel error never skips the AXP202 power-off, and a glitched
    power-off write on the shared I2C0 is retried."""
    fakes.install()
    from hal.radio import SimRadio
    from app.runtime import S_LOGIC
    clock = Clock(1000)
    rt = _watch(clock, SimRadio(MAC_A).begin())
    pmu = rt.board.pmu
    pmu.fail_off = 2

    def panel_error():
        raise OSError(5)
    rt.board.display.sleep = panel_error
    rt.begin(clock.now)
    rt._power_off(clock.now)
    assert pmu.off and rt.powered_off and not rt.running
    assert rt.io_errors[S_LOGIC] == 3, rt.io_errors


def test_no_radio_still_powers_off_at_shutdown_level():
    """ui-spec LOW-BATTERY 3 %: BYE ends in the AXP202 power-off even when no
    goodbye beacon can be sent (R-12: never die silently)."""
    fakes.install()
    from hal.haptics import Motor
    from app.runtime import Runtime
    clock = Clock(1000)
    pmu = FakePMU(clock, pct=2)
    rt = Runtime(Board(pmu=pmu, display=FakeDisplay(), imu=FakeIMU(clock), haptics=Motor()),
                 clock=clock, sleep_ms=clock.sleep, renderer=_renderer(), gc_collect=lambda: None)
    rt.begin(clock.now)
    _run(rt, clock, 10000)
    assert "radio" in rt.errors
    assert rt.powered_off and pmu.off


def test_press_latched_before_begin_is_dropped():
    """A side-key press latched before the loop starts (e.g. during the app
    import) never reaches the game, so it never opens MENU."""
    fakes.install()
    from hal.radio import SimRadio
    from hal.axp202 import EV_LONG
    clock = Clock(1000)
    rt = _watch(clock, SimRadio(MAC_A).begin(), buttons=((500, EV_LONG),))
    rt.begin(clock.now)
    seen = []
    ob = rt.game.on_button
    rt.game.on_button = lambda t, long=False: (seen.append((t, long)), ob(t, long))
    _run(rt, clock, 1000)
    assert rt.pmu.cleared == 1 and not seen and not rt.game.menu_open, seen


def test_no_renderer_wake_restores_backlight():
    """Without a renderer no frame lights the woken panel: _screen does."""
    fakes.install()
    from app.runtime import Runtime
    clock = Clock(0)
    d = FakeDisplay()
    imu = FakeIMU(clock)
    rt = Runtime(Board(display=d, imu=imu), parts=("display", "imu"), clock=clock,
                 sleep_ms=clock.sleep, gc_collect=lambda: None)
    rt.begin(0)
    rt.renderer = None                     # MicroPython: begin() built a Renderer
    imu.spikes.append((1000, 100000, -2000))   # face down: the screen goes off
    rt.run(max_ms=5000)
    assert d.asleep and not rt.screen_is_on
    rt.game.on_wake(clock.now)
    rt.run(max_ms=500)
    assert not d.asleep and d.level is not None and d.level > 0, d.level


class WakeDisplay(FakeDisplay):
    """Logs pushes and backlight levels; ``wake`` blocks 120 ms (SLPOUT) on
    the watch's clock unless given the loop's ``wait``."""

    def __init__(self, clock):
        FakeDisplay.__init__(self)
        self.clock = clock
        self.log = []

    def push_strip(self, y0, h, buf):
        self.pushes += 1
        self.log.append("push")

    def brightness(self, level=None):
        if level is not None:
            self.log.append(level)
        return FakeDisplay.brightness(self, level)

    def wake(self, level=None, wait=None):
        self.log.append("wake")
        if wait is None:
            self.clock.now += 120
        else:
            wait(120)
        FakeDisplay.wake(self, level)


def test_wake_lights_a_fresh_frame_and_keeps_motor_timing():
    """ui-spec §8: the panel wakes dark and is lit only once a fresh frame is
    out; the motor is serviced through the 120 ms SLPOUT wait."""
    fakes.install()
    from app.runtime import Runtime
    from finder.haptic_patterns import LOST
    clock = Clock(0)
    d = WakeDisplay(clock)
    imu = FakeIMU(clock)
    m = RecMotor(clock)
    rt = Runtime(Board(display=d, imu=imu, haptics=m), parts=("display", "imu", "haptics"),
                 clock=clock, sleep_ms=clock.sleep, renderer=_renderer(), gc_collect=lambda: None)
    rt.run(max_ms=1000)
    imu.spikes.append((clock.now, 100000, -2000))    # face down: the screen goes off
    rt.run(max_ms=4000)
    assert d.asleep and not rt.screen_is_on
    del d.log[:]
    del m.log[:]
    drawn = []                                       # (frame's now, real time) when lit
    frame = rt.renderer.frame

    def rec(p, disp, now):
        if disp is not None:
            drawn.append((now, clock.now))
        return frame(p, disp, now)
    rt.renderer.frame = rec
    rt.game.on_wake(clock.now)
    rt.player.play_named("LOST", clock.now)          # 5 x 60 ms on, 60 ms off
    rt.run(max_ms=1000)
    assert drawn[0][0] == drawn[0][1], drawn[:2]     # no stale frame after the SLPOUT wait
    i = d.log.index("wake")
    lit = [k for k in range(i, len(d.log)) if d.log[k] not in ("wake", "push") and d.log[k] > 0]
    assert lit and d.log[i + 1:lit[0]].count("push") >= 10, d.log[:20]
    on_ms, off_ms = LOST[0][0], LOST[0][1]
    p = _pulses(m.log)
    assert len(p) == len(LOST), m.log
    for on, g0, g1 in p:                             # every edge within the 1 ms idle slice
        assert on_ms <= on <= on_ms + 1, m.log
        assert g1 is None or off_ms <= g1 <= off_ms + 1, m.log


class UsbPMU(FakePMU):
    """VBUS present except from ``unplug[0]`` to ``unplug[1]``."""

    unplug = (15000, 25000)

    def vbus_present(self):
        return not (self.unplug[0] <= self.clock.now < self.unplug[1])


def test_watchdog_turns_hardware_once_unplugged():
    """Started on USB: the stoppable soft watchdog. The first battery reading
    (every 10 s) without VBUS switches it, one way, to the hardware WDT."""
    m = fakes.install()
    from hal.radio import SimRadio
    from hal.watchdog import MODE_HW
    clock = Clock(1000)
    rt = _watch(clock, SimRadio(MAC_A).begin())
    rt.board.pmu = UsbPMU(clock)
    rt.watchdog_ms = 8000
    rt.run(max_ms=11000)                             # battery reads at 1 s, 11 s: on USB
    assert not m.wdts and rt.wd is None              # soft, switched off on exit
    rt.run(max_ms=20000)                             # unplugged 15-25 s: switch at the 21 s read
    assert len(m.wdts) == 1 and m.wdts[0].feeds > 50, m.wdts
    assert rt.wd.mode == MODE_HW                     # cannot be stopped: still armed


def test_touch_down_spike_is_not_a_bump():
    """ui-spec §6/§8: the accelerometer spike of the finger itself is no bump
    tap, whether it reaches the game just before or after the touch-down."""
    fakes.install()
    from hal.radio import SimRadio
    from app.telemetry import Telemetry
    import json
    clock = Clock(0)
    rt = _watch(clock, SimRadio(MAC_A).begin(),
                imu_spikes=((4990, 2, 3000), (5250, 2, 3000), (7000, 2, 3000)),
                touches=((5050, 5350, 120, 120), (8000, 8100, 120, 120)))
    rt.tele = Telemetry(cap=200, hz=0)
    _arm(rt)
    _run(rt, clock, 6000)
    ev = [json.loads(s) for s in rt.tele.lines()]
    taps = [(e["t"], e["ok"]) for e in ev if e["ev"] == "tap"]
    assert taps == [(4990, True), (5250, False)], taps   # 4990: before the finger was seen
    assert rt.game.bump_t is None                    # ... dropped at touch-down
    assert [e["g"] for e in ev if e["ev"] == "touch"] == ["TAP"]
    _run(rt, clock, 1500)
    assert rt.game.bump_t == 7000                    # a real knock still counts
    rt.board.touch.contacts = 2                      # two fingers: ignored (§8)
    n = rt.tele.n
    _run(rt, clock, 1000)
    assert not [s for s in rt.tele.lines(rt.tele.n - n) if '"touch"' in s]


def _arm(rt):
    """Begin with the game always taking bump spikes, so the IMU runs fast."""
    rt.begin(0)
    rt.game.bump_armed = lambda: True


def _touch_downs(rt):
    """Times of ``game.on_touch_down`` calls (the game still sees them)."""
    out = []
    od = rt.game.on_touch_down
    rt.game.on_touch_down = lambda t: (out.append(t), od(t))
    return out


def test_short_taps_count_while_frames_render():
    """ui-spec §8: a 60-400 ms touch is a TAP. A frame takes 40 ms, so touch
    is also sampled between strips: 70 ms taps at every phase all count."""
    fakes.install()
    from hal.radio import SimRadio
    from app.telemetry import Telemetry
    import json
    clock = Clock(0)
    taps = [(5000 + 507 * i, 5070 + 507 * i, 120, 120) for i in range(40)]
    rt = _watch(clock, SimRadio(MAC_A).begin(), touches=taps)
    rt.board.display = SlowDisplay(clock)
    rt.tele = Telemetry(cap=400, hz=0)
    rt.begin(0)
    downs = _touch_downs(rt)
    _run(rt, clock, 26000)
    ev = [json.loads(s) for s in rt.tele.lines()]
    assert [e["g"] for e in ev if e["ev"] == "touch"] == ["TAP"] * 40, ev
    assert len(downs) == 40 and all(0 <= d - t[0] <= 10 for d, t in zip(downs, taps)), downs


def test_finger_spike_after_a_mid_frame_touch_down():
    """A touch-down sampled between strips reaches the game before the next
    FIFO read, so the finger's spike just after it is no bump (§6)."""
    fakes.install()
    from hal.radio import SimRadio
    from app.telemetry import Telemetry
    import json
    clock = Clock(0)
    rt = _watch(clock, SimRadio(MAC_A).begin(), imu_spikes=((5030, 2, 3000),),
                touches=((5020, 5150, 120, 120),))
    rt.board.display = SlowDisplay(clock)
    rt.tele = Telemetry(cap=200, hz=0)
    _arm(rt)
    _run(rt, clock, 6000)
    ev = [json.loads(s) for s in rt.tele.lines()]
    assert [(e["t"], e["ok"]) for e in ev if e["ev"] == "tap"] == [(5030, False)], ev
    assert rt.game.bump_t is None


def _bump_in_hot(t0, finger):
    """Two Runtimes on connected SimRadios, paired and both in HOT; both
    accelerometers spike at ``t0`` and, with ``finger``, A's screen is tapped
    then (touch-down 70 ms after the spike). Runs to ``t0`` + 1.2 s."""
    fakes.install()
    from hal.radio import SimRadio
    from hal.axp202 import EV_SHORT
    from finder.game import BUZZ_OFF, M_HUNT
    from finder.proximity import HOT
    clock = Clock(0)
    ra = SimRadio(MAC_A, seed=11).begin()
    rb = SimRadio(MAC_B, seed=22).begin()
    ra.connect(rb, rssi=-50)
    a = _watch(clock, ra, imu_spikes=((t0, 2, 3000),), buttons=((1000, EV_SHORT),),
               touches=((t0 + 70, t0 + 160, 120, 120),) if finger else ())
    b = _watch(clock, rb, imu_spikes=((t0, 2, 3000),), buttons=((1100, EV_SHORT),))
    rts = (a, b)
    for rt in rts:
        rt.begin(0)
        rt.game.pair.split_s = 1           # short split countdown
        rt.game.buzz = BUZZ_OFF            # no motor pulse can blank a spike
    hot = False
    while clock.now < t0 + 1200:
        if not hot and clock.now >= t0 - 50:
            hot = True
            for rt in rts:
                assert rt.game.mode == M_HUNT and rt.game.px.zone == HOT, rt.game.mode
        w = 1000
        for rt in rts:
            d = rt.step(clock.now)
            if d < w:
                w = d
        clock.sleep(w if w > 0 else 1)
    assert a.feed.n_taps == 1 and b.feed.n_taps == 1, (a.feed.n_taps, b.feed.n_taps)
    return a.game, b.game


def test_withdrawn_finger_spike_never_reaches_the_partner():
    """ui-spec §6, rule 10: a spike withdrawn by a touch-down that follows it
    was never a bump, on either watch. It is neither matched nor sent in
    beacons until it is 100 ms old, so the partner's own knock 0 ms apart
    finds nothing to match; without the finger, both watches enter FOUND."""
    from finder.game import M_FOUND
    ga, gb = _bump_in_hot(7960, finger=True)
    assert ga.mode != M_FOUND and gb.mode != M_FOUND, (ga.mode, gb.mode)
    assert ga.bump_t is None and gb.peer.tap_t is None, (ga.bump_t, gb.peer.tap_t)
    ga, gb = _bump_in_hot(7960, finger=False)
    assert ga.mode == M_FOUND and gb.mode == M_FOUND, (ga.mode, gb.mode)


def test_touch_down_seen_when_a_press_follows_a_gap():
    """A sample gap over the 60 ms debounce (panel wake, GC) can end one press
    and start the next on the same sample: that finger still lands (§6)."""
    fakes.install()
    from hal.radio import SimRadio
    clock = Clock(0)
    rt = _watch(clock, SimRadio(MAC_A).begin(),
                touches=((5000, 5500, 120, 120), (5555, 5705, 120, 120)))
    rt.begin(0)
    downs = _touch_downs(rt)
    _run(rt, clock, 5500)
    rt.step(clock.now)                     # lifted
    clock.now += 60                        # no sample for 60 ms; touching again
    _run(rt, clock, 500)
    assert downs == [5000, 5560], downs


def test_tap_never_beats_its_own_touch_down():
    """§8 burst filter: a press that lands and ends between two stage samples
    (mid-frame samples, then a GC pause) reaches the game landing first, so
    as the 3rd landing in 1 s it blocks its own TAP."""
    fakes.install()
    from hal.radio import SimRadio
    clock = Clock(0)
    rt = _watch(clock, SimRadio(MAC_A).begin(),
                touches=((5000, 5100, 120, 120), (5300, 5400, 120, 120), (5605, 5680, 120, 120)))
    _run(rt, clock, 5600)
    g = rt.game
    rt.step(clock.now)
    t = clock.now
    for dt in (10, 90):                    # mid-frame samples: landed, lifted
        clock.now = t + dt
        rt._sample_touch(clock.now)
    clock.now = t + 160                    # next stage: the TAP is due as well
    i0 = g._input_t
    rt._stage_touch(clock.now)
    assert g._touch_block is not None and g._input_t == i0, (g._touch_block, g._input_t, i0)


def test_touch_that_lands_in_the_wake_window_is_ignored():
    """ui-spec §8: touches are ignored for 300 ms after a wake, judged by when
    the finger landed: a tap landing 150 ms after the wake is dropped, though
    it is reported (lift + 60 ms debounce) after the window."""
    fakes.install()
    from hal.radio import SimRadio
    clock = Clock(0)
    rt = _watch(clock, SimRadio(MAC_A).begin(), imu_spikes=((1000, 400, -2000),))
    _run(rt, clock, 4500)                  # face down 1-5 s: the screen goes off
    g = rt.game
    assert not g.screen_on
    while not g.screen_on:                 # face up: wake
        clock.sleep(max(1, rt.step(clock.now)))
    t = g._wake_t
    rt.board.touch.presses.append((t + 150, t + 250, 120, 120))
    _run(rt, clock, 1000)
    assert g._input_t == t, (g._input_t, t)


def test_wrist_wear_on_a_lit_screen_keeps_taps():
    """The chip's wrist-wear event is polled up to 1 s late: on a screen that
    is already on (face up) it is no new wake, so a tap just after it counts."""
    fakes.install()
    from app.runtime import Runtime, CHIP_ON
    from hal.bma423 import EV_WRIST_WEAR
    clock = Clock(0)
    imu = ChipIMU(clock, True)
    touch = FakeTouch(clock)
    rt = Runtime(Board(imu=imu, display=FakeDisplay(), touch=touch),
                 parts=("imu", "display", "touch"), clock=clock, sleep_ms=clock.sleep,
                 renderer=_renderer(), gc_collect=lambda: None)
    rt.begin(0)
    _run(rt, clock, 4000)
    g = rt.game
    assert rt._chip == CHIP_ON and g.screen_on
    t_poll = rt._t_chip
    imu.events = EV_WRIST_WEAR
    touch.presses.append((t_poll + 60, t_poll + 160, 120, 120))   # TAP ~220 ms after the poll
    _run(rt, clock, 1000)
    assert imu.events == 0 and g.screen_on
    assert g._input_t > t_poll + 160, (g._input_t, t_poll)


def test_io_errors_never_stop_the_loop():
    """OSErrors that reach the loop from a part are counted in io_errors; the
    loop goes on (the real touch driver and motor never raise: belt and braces)."""
    fakes.install()
    from hal.radio import SimRadio
    from hal.axp202 import EV_SHORT
    from app.runtime import S_TOUCH, S_BUTTON, S_LOGIC, S_HAPTIC
    clock = Clock(0)
    rt = _watch(clock, SimRadio(MAC_A).begin(), buttons=((2000, EV_SHORT),))
    b = rt.board
    n = [0, 0]

    def touch_read(_r=b.touch.read):
        n[0] += 1
        if n[0] % 3 == 0:
            raise OSError(116)
        return _r()

    def pmu_poll(_p=b.pmu.poll):
        n[1] += 1
        if n[1] % 2 == 0:
            raise OSError(5)
        return _p()

    def battery_percent():
        raise OSError(5)

    class BadMotor(RecMotor):
        def set(self, lvl):
            if lvl > 0:
                raise OSError(5)
            RecMotor.set(self, lvl)

    b.touch.read = touch_read
    b.pmu.poll = pmu_poll
    b.pmu.battery_percent = battery_percent
    b.haptics = BadMotor(clock)
    rt.begin(0)
    rt.player.play_named("LOST", 1000)
    _run(rt, clock, 5000)
    e = rt.io_errors
    assert e[S_TOUCH] and e[S_BUTTON] and e[S_LOGIC] and e[S_HAPTIC], e
    assert rt.running and rt.ticks >= 45 and rt.frames >= 90, (rt.ticks, rt.frames)


def _tmpdir():
    try:
        import tempfile
        return tempfile.mkdtemp()          # CPython
    except ImportError:
        return "."                         # MicroPython: in-memory filesystem


def test_telemetry_flushes_to_file():
    """Lines go to ``path`` every ``flush_ms``; records the ring overwrote
    before a flush count as ``dropped``; an unwritable path falls back to RAM."""
    import os
    from app.telemetry import Telemetry
    d = _tmpdir()
    p = d + "/_tele_test.jsonl"
    try:
        tl = Telemetry(cap=4, path=p, flush_ms=1000)
        for i in range(3):
            tl.event(i, "e")
        assert tl.flush(0) == 3
        for i in range(10):
            tl.event(10 + i, "e")
        assert tl.dropped == 6
        assert tl.flush(500) == 0          # not due yet
        assert tl.flush(1000) == 4
        with open(p) as f:
            assert f.read().count("\n") == 7
    finally:
        os.remove(p)
        if d != ".":
            os.rmdir(d)
    tl = Telemetry(cap=4, path="nodir/x.jsonl")
    tl.event(0, "e")
    assert tl.flush(0, force=True) == 0 and tl.path is None
    assert isinstance(tl.err, OSError)


def test_crash_is_logged_and_flushed():
    """An exception in the loop is re-raised after a ``crash`` event, and the
    records since the last flush reach the file (R-08: the moments before it)."""
    import os
    import json
    from app.runtime import Runtime
    from app.telemetry import Telemetry
    d = _tmpdir()
    p = d + "/_crash_test.jsonl"
    clock = Clock(0)
    rt = Runtime(Board(), clock=clock, sleep_ms=clock.sleep, renderer=StubRenderer(),
                 gc_collect=lambda: None, telemetry=Telemetry(path=p))
    rt.begin(0)
    tick = rt.game.tick

    def bad_tick(t):
        if t >= 2500:
            raise ValueError("boom")
        return tick(t)
    rt.game.tick = bad_tick
    raised = False
    try:
        try:
            rt.run(max_ms=5000)
        except ValueError:
            raised = True
        with open(p) as f:
            ls = [s for s in f]
    finally:
        os.remove(p)
        if d != ".":
            os.rmdir(d)
    e = json.loads(ls[-1])
    assert raised and e["ev"] == "crash" and e["t"] >= 2500 and "boom" in e["e"], ls[-1]
    assert len(ls) == rt.tele.n            # nothing left only in RAM


def test_telemetry_logs_only_haptics_that_play():
    """ui-spec §7 guard: an event within 1 s of one of the same or higher rank
    is dropped, so the log has no buzz the wrist never felt (a drawn frame's
    event and one no frame drew)."""
    fakes.install()
    from app.runtime import Runtime
    from app.telemetry import Telemetry
    from finder.render_params import make_params
    import json
    clock = Clock(0)
    rt = Runtime(Board(), clock=clock, sleep_ms=clock.sleep, renderer=StubRenderer(),
                 gc_collect=lambda: None)
    rt.begin(0)
    rt.tele = Telemetry(cap=20, hz=0)
    for t, name in ((1000, "CLOSER"), (1300, "FARTHER")):
        rt.params = make_params(t_ms=t, haptic=name)
        rt._fresh = True
        rt._stage_render(t)
    for t in (3000, 3300):                 # NOPE then NOPE, never drawn
        rt.params = make_params(t_ms=t, haptic="NOPE")
        rt._fresh = True
        rt._stage_logic(t)
    ev = [json.loads(s) for s in rt.tele.lines()]
    assert [e["pattern"] for e in ev if e["ev"] == "haptic"] == ["CLOSER", "NOPE"], ev


def test_telemetry_session_files():
    """main.py's field-test telemetry: /log is created, each boot appends to
    the next ``<n>_<dev>.jsonl``, also after an older log was deleted."""
    import os
    from app.telemetry import session
    d = _tmpdir()
    log = d + "/_log_test"
    try:
        tl = session("A", log)
        assert (tl.sid, tl.dev, tl.path) == ("0", "A", log + "/0_A.jsonl")
        tl.event(0, "e")
        assert tl.flush(0) == 1 and tl.err is None
        tl = session("A", log)
        assert tl.sid == "1" and tl.path == log + "/1_A.jsonl"
        tl.event(0, "e")
        assert tl.flush(0) == 1
        os.remove(log + "/0_A.jsonl")      # pulled and deleted to free flash
        tl = session("A", log)
        assert tl.sid == "2" and tl.path == log + "/2_A.jsonl"
    finally:
        for n in os.listdir(log):
            os.remove(log + "/" + n)
        os.rmdir(log)
        if d != ".":
            os.rmdir(d)


def test_bma423_feature_engine_started_and_polled():
    """Real ``hal.bma423`` driver: engine up -> step counter + wrist wear
    enabled and latched on INT1; chip steps reach the tracker, wrist -> wake."""
    from tests.test_hal_bma423 import _imu
    from app.runtime import Runtime, CHIP_ON
    m, dev, bmod, imu = _imu()
    dev.regs[0x2A] = 1                     # engine running (init_ok)
    clock = Clock(0)
    rt = Runtime(Board(imu=imu, display=FakeDisplay()), parts=("imu", "display"),
                 clock=clock, sleep_ms=clock.sleep, renderer=_renderer(),
                 gc_collect=lambda: None)
    rt.begin(0)
    assert rt._chip == CHIP_ON and not rt.errors, rt.errors
    assert dev.feat[0x3B] & 0x30 == 0x30   # STEP_COUNTER_EN | STEP_ACTIVITY_EN
    assert dev.feat[0x40] & 0x01           # FEAT_WRIST_WEAR enabled
    assert dev.regs[0x56] & 0x08 and dev.regs[0x55] == 1   # INT1 map, latched
    woke = []
    g = rt.game
    wake = g.on_wake

    def on_wake(t):
        woke.append(t)
        wake(t)
    g.on_wake = on_wake
    dev.regs[0x1E] = 10
    dev.regs[0x27] = 1                     # walking
    rt.run(max_ms=2500)                    # no FIFO samples: face down, dark after 2 s
    assert rt.feed.tracker.chip_live and not woke and not g.screen_on
    dev.regs[0x1E] = 30
    dev.regs[0x1C] = 0x08                  # wrist-wear latched
    rt.run(max_ms=1000)
    assert len(woke) == 1 and g.screen_on and rt.feed.tracker.steps >= 20
    dev.regs[0x1C] = 0x08                  # again on the lit screen: read, no second wake
    rt.run(max_ms=1000)
    assert len(woke) == 1 and dev.regs[0x1C] == 0
    assert not any(rt.io_errors), rt.io_errors


def test_bma423_feature_bus_error_is_retried():
    """A bus error while the features are switched on is retried on the next
    1 s poll: the chip ends ON with the step counter and wrist wear really
    enabled, and the stale error is cleared."""
    from tests.test_hal_bma423 import _imu
    from app.runtime import Runtime, CHIP_ON, CHIP_PENDING
    m, dev, bmod, imu = _imu()
    dev.regs[0x2A] = 1                     # engine running (init_ok)
    real = dev.write
    fail = [True]

    def write(reg, data):
        if reg == 0x5E and fail[0]:        # the first feature-config write NACKs
            fail[0] = False
            raise OSError(5)
        return real(reg, data)
    dev.write = write
    clock = Clock(0)
    rt = Runtime(Board(imu=imu, display=FakeDisplay()), parts=("imu", "display"),
                 clock=clock, sleep_ms=clock.sleep, renderer=_renderer(),
                 gc_collect=lambda: None)
    rt.begin(0)
    assert rt._chip == CHIP_PENDING and "imu_features" in rt.errors
    rt.run(max_ms=3000)
    assert rt._chip == CHIP_ON and "imu_features" not in rt.errors, rt.errors
    assert dev.feat[0x3B] & 0x30 == 0x30   # STEP_COUNTER_EN | STEP_ACTIVITY_EN
    assert dev.feat[0x40] & 0x01           # FEAT_WRIST_WEAR enabled


def test_bma423_feature_upload_bus_error_is_retried():
    """A NACK during the boot-time blob upload is retried: the chip ends ON."""
    from tests.test_hal_bma423 import _imu, _tmp, _write, _blob, _rm, _w
    from app.runtime import Runtime, CHIP_ON
    m, dev, bmod, imu = _imu()
    p = _tmp("t_rt_bma423_upload.bin")
    _write(p, _blob())
    dev.init_status = 1
    start = imu.start_features
    imu.start_features = lambda: start(path=p, expect_sha256=None)
    real = dev.write
    fail = [True]

    def write(reg, data):
        if reg == 0x5E and fail[0]:        # the first blob chunk NACKs
            fail[0] = False
            raise OSError(5)
        return real(reg, data)
    dev.write = write
    clock = Clock(0)
    rt = Runtime(Board(imu=imu, display=FakeDisplay()), parts=("imu", "display"),
                 clock=clock, sleep_ms=clock.sleep, renderer=_renderer(),
                 gc_collect=lambda: None)
    try:
        rt.begin(0)
        rt.run(max_ms=3000)
    finally:
        _rm(p)
    assert rt._chip == CHIP_ON and "imu_features" not in rt.errors, rt.errors
    assert _w(dev, 0x59)[-1] == 1 and _w(dev, 0x59).count(1) == 1   # INIT_CTRL=1 once


def test_bma423_feature_engine_pending_then_missing():
    from app.runtime import Runtime, CHIP_OFF, CHIP_ON, CHIP_PENDING

    for ok, state in ((True, CHIP_ON), (False, CHIP_OFF), (None, CHIP_OFF)):
        clock = Clock(0)
        imu = ChipIMU(clock, ok)
        rt = Runtime(Board(imu=imu), clock=clock, sleep_ms=clock.sleep,
                     renderer=_renderer(), gc_collect=lambda: None)
        rt.begin(0)
        assert imu.calls[0] == "start"                     # never blocks the boot
        assert rt._chip == (CHIP_OFF if ok is None else CHIP_PENDING)
        rt.run(max_ms=3500)
        assert rt._chip == state, (ok, rt._chip)
        assert imu.calls[1:] == (["on"] if ok else [])


def test_stage_sums_stay_small_ints():
    from app.runtime import Runtime, S_RENDER, ACC_LIMIT_US
    us = [0]
    rt = Runtime(Board(), clock_us=lambda: us[0])
    for _ in range(30000):                 # 40 ms frames: 1.2e9 us > 2**30
        a = us[0]
        us[0] = (a + 40000) & 0x3FFFFFFF   # ticks_us wraps at 2**30
        rt._acc(S_RENDER, a)
    assert rt.st_us[S_RENDER] <= ACC_LIMIT_US < 2 ** 30
    s = rt.stats(reset=False)
    assert s["render"][0] == 40.0 and s["render"][1] == 40.0
