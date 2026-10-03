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

import sys
from array import array

from tests import fakes

MPY = sys.implementation.name == "micropython"
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
    """100 Hz FIFO in milli-g: face-up gravity plus scripted spikes."""

    def __init__(self, clock, spikes=()):
        self.clock = clock
        self.last = clock.now
        self.fifo_mg = array("h", [0] * (170 * 3))
        self.spikes = list(spikes)     # (t_ms, samples wide, extra mg on z)

    def fifo_read_mg(self):
        now = self.clock.now
        n = (now - self.last) // 10
        if n > 170:
            self.last += (n - 170) * 10
            n = 170
        a = self.fifo_mg
        for i in range(n):
            t = self.last + (i + 1) * 10
            z = 1000 + (3 if i & 1 else -3)
            for t0, w, mg in self.spikes:
                if t0 <= t < t0 + w * 10:
                    z += mg
            a[3 * i] = 5
            a[3 * i + 1] = -4
            a[3 * i + 2] = z if z < 4000 else 3999
        self.last += n * 10
        return n


class FakeTouch:
    def __init__(self, clock, presses=()):
        self.clock = clock
        self.presses = list(presses)   # (t_down, t_up, x, y)
        self.r = [False, 0, 0]
        self.reads = 0

    def read(self):
        self.reads += 1
        t = self.clock.now
        r = self.r
        r[0] = False
        for t0, t1, x, y in self.presses:
            if t0 <= t < t1:
                r[0] = True
                r[1] = x
                r[2] = y
        return r


class FakePMU:
    def __init__(self, clock, events=(), pct=90):
        self.clock = clock
        self.events = list(events)     # (t_ms, EV_* mask)
        self.pct = pct
        self.off = False
        self.polls = 0

    def poll(self):
        self.polls += 1
        ev = 0
        now = self.clock.now
        while self.events and self.events[0][0] <= now:
            ev |= self.events.pop(0)[1]
        return ev

    def battery_percent(self):
        return self.pct

    def battery_voltage(self):
        return 3900

    def shutdown(self):
        self.off = True


class FakeDisplay:
    def __init__(self):
        self.pushes = 0
        self.level = None
        self.asleep = False
        self.sleeps = 0

    def push_strip(self, y0, h, buf):
        self.pushes += 1

    def brightness(self, level=None):
        if level is not None:
            self.level = level
        return self.level

    def sleep(self):
        self.asleep = True
        self.sleeps += 1

    def wake(self, level=None):
        self.asleep = False
        self.brightness(level)


class StubRenderer:
    """``ui.renderer.Renderer.frame`` contract without framebuf (CPython)."""

    def __init__(self):
        self.runes = (0, 3, 6)
        self.sun = False
        self.menu_rows = []
        self.buf = bytearray(240 * 24 * 2)
        self._hap_t = None
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
        if p.haptic and p.t_ms != self._hap_t:
            self._hap_t = p.t_ms
            return [p.haptic, hb] if hb else [p.haptic]
        return [hb] if hb else ()


def _renderer():
    try:
        import framebuf  # noqa: F401
        from ui.renderer import Renderer
        return Renderer()
    except ImportError:
        return StubRenderer()


class Board:
    """Duck-typed board: plain attributes (no lazy bring-up)."""

    def __init__(self, **parts):
        for k in parts:
            setattr(self, k, parts[k])


def _watch(clock, mac, radio, imu_spikes=(), touches=(), buttons=()):
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


def _nonzero(hist):
    for d in hist:
        if d:
            return True
    return False


# ---- the two-watch minute -----------------------------------------------------------
def test_two_watches_one_minute():
    fakes.install()
    from hal.radio import SimRadio
    from finder.game import BUZZ_OFF, BUZZ_FULL, M_PAIRING
    from finder import pairing as P
    from app.runtime import EV_SHORT
    clock = Clock(0)
    ra = SimRadio(MAC_A, seed=11).begin()
    rb = SimRadio(MAC_B, seed=22).begin()
    ra.connect(rb, rssi=-50)
    a = _watch(clock, MAC_A, ra, imu_spikes=((20000, 2, 3000), (22000, 6, 3000)),
               buttons=((2000, EV_SHORT),))
    y_buzz = 120 + 20                  # MENU_ROWS_Y[2] (row 2: BUZZ)
    b = _watch(clock, MAC_B, rb, buttons=((2500, EV_SHORT),),
               touches=((45000, 46000, 120, 120),          # long press: MENU
                        (47000, 47080, 120, y_buzz),       # BUZZ: EVENTS
                        (48000, 48080, 120, y_buzz),       # BUZZ: OFF
                        (49000, 49080, 120, 52)))          # RESUME
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
        n = rt.frames_total
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

    # bump spikes: 20 ms accepted, 60 ms (a shake) rejected
    assert a.feed.n_taps == 1, (a.feed.n_taps, a.feed.n_rejected)
    assert abs(a.feed.last_tap - 20000) <= 20, a.feed.last_tap
    assert a.feed.n_rejected >= 1
    assert a.feed.n_samples >= 5900

    # haptics: the motor was driven; buzz OFF (via the menu) silenced watch B
    assert _nonzero(a.motor._pwm.history)
    assert _nonzero(b.motor._pwm.history[:off_idx])
    assert off_at is not None and 48000 <= off_at < 49000, off_at
    assert not _nonzero(b.motor._pwm.history[off_idx:]), b.motor._pwm.history[off_idx:]
    assert b.motor.duty_u16 == 0
    assert a.game.buzz == BUZZ_FULL
    assert _nonzero(a.motor._pwm.history[a_idx:])     # A (FULL) kept buzzing
    assert not b.game.menu_open
    for rt in rts:
        assert not rt.pmu.off and not rt.powered_off

    # timing stats are printable from the REPL
    s = a.stats()
    assert s["frames"] == a.frames_total and s["render"][2] == a.frames_total
    assert s["collect"][0] == a.gc_count[0] and s["gc"][2] == a.gc_count[0]
    assert a.frames == 0


# ---- smaller pieces -----------------------------------------------------------------
def test_power_off_shuts_pmu_down():
    fakes.install()
    from hal.radio import SimRadio
    clock = Clock(1000)
    r = SimRadio(MAC_A).begin()
    rt = _watch(clock, MAC_A, r)
    rt.board.pmu.pct = 2                     # below BATT_SHUTDOWN_PCT: BYE, goodbye beacons
    rt.begin(clock.now)
    for _ in range(4000):
        if rt.powered_off:
            break
        clock.sleep(max(1, rt.step(clock.now)))
    assert rt.powered_off and rt.pmu.off
    assert rt.display.asleep
    assert rt.motor.duty_u16 == 0
    assert not rt.running


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
    assert rt.frames_total >= 18 and d.pushes == 10 * rt.frames_total
    # face down (z = -1 g) for > WRIST_DOWN_MS: display sleeps, frames stop
    imu.spikes.append((clock.now, 100000, -2000))
    n0 = d.pushes
    rt.run(max_ms=4000)
    assert d.asleep and not rt.screen_is_on and rt.bl_level == 0.0
    assert d.pushes < n0 + 10 * 20 * 3
    n1 = d.pushes
    rt.run(max_ms=1000)
    assert d.pushes == n1


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
    rt.params = make_params(t_ms=0, heartbeat="TICK", pulse_period_ms=500)
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


def test_imu_feed_spikes_blanking_and_rate():
    from app.imu_feed import ImuFeed
    clock = Clock(0)
    imu = FakeIMU(clock, spikes=((1000, 1, 3000), (1500, 2, 3000), (2000, 3, 3000),
                                 (2600, 2, 3000), (2700, 2, 3000)))
    taps = []
    f = ImuFeed(imu, on_tap=taps.append)
    f.motor(3000, 1.0)
    f.motor(3060, 0.0)
    imu.spikes.append((3150, 2, 3000))     # 90 ms after the pulse: blanked
    imu.spikes.append((3300, 2, 3000))     # 240 ms after: accepted
    while clock.now < 4000:
        clock.sleep(30)
        f.poll(clock.now)
    assert taps == [1000, 1500, 2600, 3300], taps
    assert f.n_blanked == 1
    assert f.n_rejected == 3               # 30 ms wide, refractory, blanked
    assert f.tracker.face_up
    assert abs(f.tracker.gz - 1.0) < 0.1
    assert f.n_samples == clock.now // 10
    assert f.blanked(3000) and f.blanked(3200) and not f.blanked(3210)
    assert not f.blanked(2999)


def test_telemetry_ring_and_state_record():
    fakes.install()
    from hal.radio import SimRadio
    from app.telemetry import Telemetry
    import json
    clock = Clock(0)
    r = SimRadio(MAC_A).begin()
    rt = _watch(clock, MAC_A, r)
    tl = Telemetry(cap=8, hz=5, dev="A", sid="t1")
    rt.tele = tl
    rt.run(max_ms=3000)
    assert tl.n >= 15
    ls = tl.lines()
    assert len(ls) == 8
    d = json.loads(ls[-1])
    for k in ("t", "ev", "rssi", "zone", "ui", "scr", "bl", "fps", "batt_pct", "seq"):
        assert k in d, k
    assert d["dev"] == "A" and d["ev"] == "s" and d["scr"] is True
    assert d["batt_pct"] == 90 and d["batt_mv"] == 3900
    tl.event(5, "btn", ("kind", "short"))
    assert json.loads(tl.lines(1)[0]) == {"t": 5, "ev": "btn", "kind": "short"}
    assert len(tl.lines(3)) == 3


def test_real_board_drivers_short_run():
    """hal drivers on the fake buses: API use matches the drivers."""
    m = fakes.install()
    from hal import axp202
    from hal import bma423 as B
    pmu = m.add_i2c_device(0, 0x35, {0x03: 0x41, 0x12: 0x02, 0xB9: 80})
    w1c = axp202.REG_INTSTS1

    def pmu_write(reg, data, _w=pmu.write):
        if w1c <= reg < w1c + 5:           # IRQ status: write 1 to clear
            pmu.regs[reg] &= ~data[0] & 0xFF
            return
        _w(reg, data)
    pmu.write = pmu_write
    imu = m.add_i2c_device(0, 0x19, {0x00: 0x13})
    m.add_i2c_device(1, 0x38, {0xA3: 0x64})
    clock = Clock(5000)
    frame = bytes((0, 0, 0, 0, 0x00, 0x20))       # z = +1 g at +-4 g

    def imu_read(reg, n, _r=imu.read):
        if reg == B.REG_FIFO_LENGTH_0:
            k = 6 * 3                             # 3 frames per poll
            return bytes((k & 0xFF, k >> 8))[:n]
        if reg == B.REG_FIFO_DATA:
            return (frame * (n // 6 + 1))[:n]
        return _r(reg, n)
    imu.read = imu_read
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
    assert not any(rt.io_errors), rt.io_errors
    assert rt.game.battery == 80
    assert rt.feed.n_samples > 0 and rt.feed.tracker.face_up
    assert rt.frames_total >= 5
    assert pmu.regs[w1c + 2] == 0          # PEK IRQ consumed
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
    from app.runtime import EV_SHORT
    from finder.haptic_patterns import MIN_PULSE_MS
    ra = SimRadio(MAC_A, seed=11).begin()
    rb = SimRadio(MAC_B, seed=22).begin()
    ra.connect(rb, rssi=-50)
    ca = Clock(0)
    cb = Clock(0)
    a = _watch(ca, MAC_A, ra, buttons=((2000, EV_SHORT),))
    b = _watch(cb, MAC_B, rb, buttons=((2500, EV_SHORT),))
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
        assert rt.frames_total >= 0.95 * 20 * end / 1000, rt.frames_total
        p = _pulses(rt.motor.log)
        assert len(p) >= 6, rt.motor.log
        ticks = [on for on, g0, g1 in p if (g0 is None or g0 >= 200) and (g1 is None or g1 >= 200)]
        assert len(ticks) >= 3, p
        for on in ticks:                   # single TICK: exact start, end within a strip
            assert MIN_PULSE_MS <= on <= MIN_PULSE_MS + strip, p
        for on, g0, g1 in p:               # a later pulse may start up to a strip late
            assert on >= MIN_PULSE_MS - strip, p
        assert rt.feed.dec == 4            # tracker fed at 25 Hz


class _AxpBus:
    """Real ``hal.axp202.AXP202`` on the fake I2C bus (W1C IRQ status)."""

    def __init__(self, m, pct, vbus):
        from hal import axp202
        dev = m.add_i2c_device(0, 0x35, {0x03: 0x41, 0x12: 0x02, 0xB9: pct,
                                         0x00: 0x20 if vbus else 0x00, 0x32: 0x46})
        w1c = axp202.REG_INTSTS1

        def write(reg, data, _w=dev.write):
            if w1c <= reg < w1c + 5:
                dev.regs[reg] &= ~data[0] & 0xFF
                return
            _w(reg, data)
        dev.write = write
        self.dev = dev
        self.pmu = axp202.AXP202(m.I2C(0, scl=m.Pin(22), sda=m.Pin(21)))


def _run(rt, clock, ms):
    t_end = clock.now + ms
    while clock.now < t_end and not rt.powered_off:
        clock.sleep(max(1, rt.step(clock.now)))


def test_low_battery_shutdown_needs_confirmation_and_no_usb():
    m = fakes.install()
    from hal.radio import SimRadio
    from app.runtime import BATT_LOW_READS, BATT_RECHECK_MS
    from finder import tuning as T
    bus = _AxpBus(m, pct=2, vbus=True)
    clock = Clock(1000)
    rt = _watch(clock, MAC_A, SimRadio(MAC_A).begin())
    rt.board.pmu = bus.pmu
    rt.begin(clock.now)
    _run(rt, clock, 25000)                 # on USB: 2 % never powers off
    assert not rt.powered_off and rt.game._bye_t is None
    assert rt.game.battery == T.BATT_SHUTDOWN_PCT + 1
    assert bus.dev.regs[0x32] & 0x80 == 0
    bus.dev.regs[0x00] = 0                 # unplugged
    t_unplug = clock.now
    _run(rt, clock, 30000)
    assert rt.powered_off and not rt.running
    assert bus.dev.regs[0x32] == 0x46 | 0x80   # REG 32H bit 7 via the real driver
    assert clock.now - t_unplug >= (BATT_LOW_READS - 1) * BATT_RECHECK_MS


def test_one_low_battery_reading_is_ignored():
    fakes.install()
    from hal.radio import SimRadio
    from finder import tuning as T
    clock = Clock(1000)
    rt = _watch(clock, MAC_A, SimRadio(MAC_A).begin())
    rt.board.pmu.pct = 0                   # a sagging voltage-fallback reading
    rt.begin(clock.now)
    rt.step(clock.now)
    assert rt.game.battery == T.BATT_SHUTDOWN_PCT + 1
    rt.board.pmu.pct = 60
    _run(rt, clock, 15000)
    assert not rt.powered_off and not rt.pmu.off
    assert rt.game._bye_t is None and rt.game.battery == 60


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
    rt.run(max_ms=1200)
    assert rt.feed.tracker.chip_live and not woke
    dev.regs[0x1E] = 30
    dev.regs[0x1C] = 0x08                  # wrist-wear latched
    rt.run(max_ms=1200)
    assert len(woke) == 1 and rt.feed.tracker.steps >= 20
    assert not any(rt.io_errors), rt.io_errors


def test_bma423_feature_engine_pending_then_missing():
    from app.runtime import Runtime, CHIP_OFF, CHIP_ON, CHIP_PENDING

    class ChipIMU(FakeIMU):
        def __init__(self, clock, ok):
            FakeIMU.__init__(self, clock)
            self.ok = ok
            self.feat_state = 0
            self.polls = 0
            self.calls = []

        def load_config(self, wait=True):
            self.calls.append(("load", wait))
            if self.ok is None:
                return False               # bma423conf.bin missing
            self.feat_state = 1
            return True

        def features_ready(self):
            self.polls += 1
            if self.polls >= 3:
                self.feat_state = 2 if self.ok else -1
            return self.feat_state == 2

        def features_ok(self):
            return self.feat_state == 2

        def enable_step_counter(self):
            self.calls.append("steps")

        def enable_feature(self, off):
            self.calls.append(("feat", off))

        def map_interrupts(self, int1=0, latched=False):
            self.calls.append(("map", int1, latched))

        def steps(self):
            return 0

        def activity(self):
            return 0

        def poll_events(self):
            return 0

    for ok, state in ((True, CHIP_ON), (False, CHIP_OFF), (None, CHIP_OFF)):
        clock = Clock(0)
        imu = ChipIMU(clock, ok)
        rt = Runtime(Board(imu=imu), clock=clock, sleep_ms=clock.sleep,
                     renderer=_renderer(), gc_collect=lambda: None)
        rt.begin(0)
        assert imu.calls[0] == ("load", False)             # never blocks the boot
        assert rt._chip == (CHIP_OFF if ok is None else CHIP_PENDING)
        rt.run(max_ms=3500)
        assert rt._chip == state, (ok, rt._chip)
        if ok:
            assert imu.calls[1:] == ["steps", ("feat", 0x40), ("map", 0x08, True)]
        else:
            assert imu.calls[1:] == []


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


def test_deploy_copies_app_and_boot():
    try:
        import tools.deploy as d           # CPython host tool only
    except ImportError:
        return
    assert "boot.py" in d.FILES and "main.py" in d.FILES
    assert "app" in d.DIRS
    _, files, notes = d.collect()
    remote = [r for _, r in files]
    for f in ("boot.py", "main.py", "app/__init__.py", "app/runtime.py", "app/imu_feed.py"):
        assert f in remote, f
    assert not any(n.startswith("missing") for n in notes), notes
