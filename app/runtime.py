"""The watch main loop: hardware <-> ``finder.game.Game`` <-> ``ui.renderer``.

    from hal.board import Board
    from app.runtime import Runtime
    rt = Runtime(Board())
    rt.run()                    # Ctrl-C returns to the REPL
    rt.print_stats()            # fps and ms per stage since the last print

One ``step(now)`` does, in order:

  radio   drain ESP-NOW -> LinkMonitor (partner lock = ``game.pair.peer_mac``)
          -> valid beacons -> ``game.on_packet``
  imu     BMA423 FIFO -> MotionTracker (25 Hz, g) + bump spikes -> ``on_accel_tap``;
          once a second the feature engine (if ``bma423conf.bin`` loaded):
          chip steps/activity -> tracker, wrist-wear -> ``game.on_wake``
  touch   FT6336 -> GestureRecognizer -> ``game.on_gesture``
  button  AXP202 PEK -> ``game.on_button`` (short / long; wakes when off)
  logic   every 100 ms: tracker + battery (every 10 s) -> ``game.tick``
          -> RenderParams; screen power + backlight; AXP202 shutdown only
          once ``game.power_off``. A shutdown-level battery reading reaches
          the game only off USB and after ``BATT_LOW_READS`` in a row (1 s
          apart), so one sagging reading never powers the watch off
  render  at ``params.fps_cap`` (20, saver 15): strips -> ``display.push_strip``.
          With the screen off the renderer still runs state-only
          (``display=None``) so heartbeats keep their time grid (ui-spec §7).
          A frame takes ~40 ms on the watch, so the motor is serviced after
          every strip (~4 ms) and the frame's haptic events start at the
          post-render time (pulses keep their 60 ms ERM floor)
  radio   beacon (``game.fill_beacon``) at ``game.beacon_hz`` via ``maybe_send``
  haptic  renderer events + ``params.haptic`` -> HapticPlayer (buzz mode) -> Motor
  gc      ``gc.collect()`` once per ``gc_period_ms`` when the next frame is at
          least ``gc_budget_ms`` away (forced after 4 periods)

``step`` returns the ms until the next deadline; ``run`` sleeps exactly that
(never longer than ``MAX_SLEEP_MS``) with ``idle``, which ticks the motor
every ms while a pattern plays (edges on time without polling any bus). Clock, sleep and gc are injectable, so
tests and the simulator drive several runtimes from one fake clock. Every
part is optional: ``Runtime(board, parts=("display", "radio"))`` runs just
those (a notebook subset); a part that fails to come up is recorded in
``errors`` and skipped. OSErrors from a part (I2C glitches) are counted in
``io_errors`` and never stop the loop.
"""

import gc

from finder.compat import ticks_add, ticks_diff, ticks_ms
from finder import proto
from finder import tuning as T
from finder import gestures as G
from finder.game import Game
from finder.haptic_patterns import HapticPlayer, MODE_FULL, MODE_EVENTS, MODE_OFF
from finder.link import LinkMonitor, R_BAD, R_DUP

try:
    from time import ticks_us as _ticks_us
except ImportError:  # CPython
    import time as _time

    def _ticks_us():
        return int(_time.perf_counter() * 1000000) & 0x3FFFFFFF

try:
    from time import sleep_ms as _sleep_ms
except ImportError:  # CPython
    import time as _time2

    def _sleep_ms(ms):
        _time2.sleep(ms / 1000)

PARTS = ("pmu", "display", "imu", "touch", "haptics", "radio")
GAME_ID = 1
TICK_MS = 100              # game logic rate (10 Hz)
INPUT_MS = 20              # touch/button/imu poll when nothing else is due
HAPTIC_MS = 10             # loop period while the motor is running (60 ms pulses)
HAPTIC_SLICE_MS = 1        # ``idle`` motor tick while a pattern plays
BATTERY_MS = 10000
BATT_LOW_READS = 3         # shutdown-level readings in a row before the game sees one
BATT_RECHECK_MS = 1000     # ... taken this far apart
IMU_OUT_HZ = 25            # MotionTracker rate (25-50 Hz; 25 halves its float work)
CHIP_MS = 1000             # BMA423 feature engine (steps/activity/wrist) poll
MAX_SLEEP_MS = 50
GC_PERIOD_MS = 1000
GC_BUDGET_MS = 10
STAGES = ("radio", "imu", "touch", "button", "logic", "render", "tx", "haptic", "gc", "tele")
S_RADIO = 0
S_IMU = 1
S_TOUCH = 2
S_BUTTON = 3
S_LOGIC = 4
S_RENDER = 5
S_TX = 6
S_HAPTIC = 7
S_GC = 8
S_TELE = 9
EV_SHORT = 0x01            # hal.axp202 EV_* (copied: no hal import in logic code)
EV_LONG = 0x02
EV_WRIST_WEAR = 0x0008     # hal.bma423
FEAT_WRIST_WEAR = 0x40     # hal.bma423 FEATURES_IN offset
FEAT_PENDING = 1           # hal.bma423 feat_state
CHIP_OFF = 0               # feature engine: absent / failed (software steps only)
CHIP_PENDING = 1           # blob uploaded, engine starting
CHIP_ON = 2
ACC_LIMIT_US = 0x1FFFFFFF  # stage sums halve past this (stay MicroPython small ints)
_BUZZ_MODE = (MODE_FULL, MODE_EVENTS, MODE_OFF)   # index = game.BUZZ_*
AXP_OFF_CTL = 0x32         # AXP202 REG 32H bit7: shut all outputs down
_NO_EVENTS = ()


def _shutdown_pmu(pmu):
    """AXP202 power-off (``pmu.shutdown()`` if the driver has one)."""
    f = getattr(pmu, "shutdown", None)
    if f is not None:
        f()
        return
    pmu.write(AXP_OFF_CTL, pmu.read(AXP_OFF_CTL) | 0x80)


class _HapticDisplay:
    """Display proxy for the renderer: services the motor after each strip,
    so pulse edges stay within one strip (~4 ms) of schedule mid-frame."""

    def __init__(self, rt, display):
        self.rt = rt
        self.d = display

    def push_strip(self, y0, h, buf):
        self.d.push_strip(y0, h, buf)
        rt = self.rt
        rt._stage_haptic(rt.clock())


class Runtime:
    """One watch: owns the Game, renderer, haptic player and loop timing."""

    def __init__(self, board, parts=PARTS, clock=None, sleep_ms=None, clock_us=None,
                 renderer=None, game_id=GAME_ID, telemetry=None, gc_collect=None,
                 gc_period_ms=GC_PERIOD_MS, gc_budget_ms=GC_BUDGET_MS,
                 battery_ms=BATTERY_MS, z_sign=1, mac=None, touch_every=1, watchdog_ms=None):
        self.board = board
        self.watchdog_ms = watchdog_ms
        self.wd = None
        self.parts = parts
        self.clock = clock or ticks_ms
        self.sleep_ms = sleep_ms or _sleep_ms
        self.us = clock_us or _ticks_us
        self.renderer = renderer
        self.game_id = game_id
        self.tele = telemetry
        self.gc_collect = gc_collect or gc.collect
        self.gc_period_ms = gc_period_ms
        self.gc_budget_ms = gc_budget_ms
        self.battery_ms = battery_ms
        self.z_sign = z_sign
        self.touch_every = touch_every
        self._mac = mac
        self.errors = {}
        self.io_errors = [0] * len(STAGES)
        self.started = False
        self.running = False
        # parts (None when absent)
        self.pmu = self.display = self.imu = self.touch = self.motor = self.radio = None
        self.game = None
        self.feed = None
        self.link = None
        self.player = HapticPlayer()
        self.gestures = G.GestureRecognizer(long_ms=T.LONG_PRESS_MS, slop_px=T.TAP_MOVE_PX)
        self.tx = proto.Beacon(game_id)
        self.rxb = proto.Beacon(game_id)
        self.txbuf = bytearray(proto.SIZE)
        self.params = None
        self.batt_mv = None
        self.screen_is_on = True
        self.bl_level = None
        self.fps = 0.0
        self.powered_off = False
        self._init_stats()

    # ---- bring-up ----------------------------------------------------------------
    def _part(self, name):
        if name not in self.parts:
            return None
        try:
            return getattr(self.board, name)
        except Exception as e:  # noqa: BLE001 - optional hardware
            self.errors[name] = e
            return None

    def begin(self, now=None):
        """Bring up the parts and the game (idempotent)."""
        if self.started:
            return self
        self.pmu = self._part("pmu")
        self.display = self._part("display")
        self.imu = self._part("imu")
        self.touch = self._part("touch")
        self.motor = self._part("haptics")
        self.radio = self._part("radio")
        self._hdisp = None if self.display is None else _HapticDisplay(self, self.display)
        if self.renderer is None:
            try:
                from ui.renderer import Renderer
                self.renderer = Renderer()
            except (ImportError, MemoryError) as e:
                self.errors["renderer"] = e
        mac = self._mac
        if mac is None and self.radio is not None:
            mac = getattr(self.radio, "mac", None)
        now = self.clock() if now is None else now
        if self.imu is not None and hasattr(self.imu, "fifo_read_mg"):
            from app.imu_feed import ImuFeed
            self.feed = ImuFeed(self.imu, z_sign=self.z_sign, out_hz=IMU_OUT_HZ)
            blank = self.feed.blanked
        else:
            blank = self.player.blanked
        self.game = Game(mac, blank_fn=blank, t_ms=now)
        if self.feed is not None:
            self.feed.on_tap = self._on_tap
        self.link = LinkMonitor(self.game_id)
        self._vbus = getattr(self.pmu, "vbus_present", None)
        self._low_n = 0
        self._chip = CHIP_OFF
        if self.feed is not None:
            self._chip_start()
        self._rx_cb = self._on_rx
        self._buzz = -1
        self._fresh = False
        self._hap_t = None
        self._metro = None
        self._metro_ms = 0
        self._fps_t = now
        self._fps_n = 0
        self._t_tick = now
        self._t_frame = now
        self._t_input = now
        self._t_batt = now
        self._t_chip = now
        self._t_gc = now
        self._win_t = now
        self._motor_lvl = 0.0
        self._touch_n = 0
        self.screen_is_on = True
        self.started = True
        self.running = True
        return self

    # ---- main loop -----------------------------------------------------------------
    def run(self, max_ms=None):
        """Loop until ``stop()``/power-off (or ``max_ms``); Ctrl-C stops it too."""
        self.begin()
        t0 = self.clock()
        if self.watchdog_ms and self.wd is None:
            self.wd = self._make_watchdog()
        wd = self.wd
        try:
            while self.running:
                if wd is not None:
                    wd.feed()
                w = self.step()
                if max_ms is not None and ticks_diff(self.clock(), t0) >= max_ms:
                    break
                if w > 0:
                    self.idle(w)
        finally:
            self.quiet()
            if wd is not None and wd.stop():
                self.wd = None
        return self

    def _make_watchdog(self):
        """Hardware WDT on battery, stoppable timer watchdog on USB power (hal/watchdog.py)."""
        from hal.watchdog import Watchdog
        usb = False
        if self._vbus is not None:
            try:
                usb = bool(self._vbus())
            except OSError:
                usb = False
        try:
            return Watchdog(self.watchdog_ms, usb=usb, clock=self.clock)
        except (ImportError, AttributeError, ValueError, OSError) as e:
            self.errors["watchdog"] = e
            return None

    def stop(self):
        self.running = False

    def hap_active(self):
        """True while the motor is on or a pattern (event/heartbeat) plays."""
        pl = self.player
        return self._motor_lvl > 0 or pl.busy or pl._hb.pat is not None

    def idle(self, ms):
        """Sleep ``ms``; while a pattern plays, tick the motor every
        ``HAPTIC_SLICE_MS`` so its edges land on time (ERM 60 ms floor)."""
        if not self.hap_active():
            self.sleep_ms(ms)
            return
        clk = self.clock
        end = ticks_add(clk(), ms)
        while True:
            self._stage_haptic(clk())
            r = ticks_diff(end, clk())
            if r <= 0:
                return
            self.sleep_ms(HAPTIC_SLICE_MS if r > HAPTIC_SLICE_MS else r)

    def quiet(self):
        """Motor off (after Ctrl-C or an exception)."""
        m = self.motor
        if m is not None:
            try:
                m.set(0)
            except OSError:
                pass

    def step(self, now=None):
        """One loop pass at ``now``; returns ms until the next deadline."""
        if not self.started:
            self.begin(now)
        if now is None:
            now = self.clock()
        us = self.us
        self.loops += 1
        if self.powered_off:
            return MAX_SLEEP_MS
        a = us()
        if self.radio is not None:
            self._stage_radio(now)
            a = self._acc(S_RADIO, a)
        if self.feed is not None:
            self._stage_imu(now)
            a = self._acc(S_IMU, a)
        if self.touch is not None:
            self._stage_touch(now)
            a = self._acc(S_TOUCH, a)
        if self.pmu is not None:
            self._stage_button(now)
            a = self._acc(S_BUTTON, a)
        if ticks_diff(now, self._t_tick) >= 0:
            self._stage_logic(now)
            a = self._acc(S_LOGIC, a)
            if self.powered_off:
                return MAX_SLEEP_MS
        if ticks_diff(now, self._t_frame) >= 0:
            self._stage_render(now)
            a = self._acc(S_RENDER, a)
            now = self.clock()        # a frame takes ~40 ms on the watch
        if self.radio is not None:
            self._stage_tx(now)
            a = self._acc(S_TX, a)
        self._stage_haptic(now)
        a = self._acc(S_HAPTIC, a)
        tl = self.tele
        if tl is not None:
            if tl.due(now):
                tl.record(now, self)
            tl.flush(now)
            a = self._acc(S_TELE, a)
        self._t_input = ticks_add(now, INPUT_MS)
        if ticks_diff(now, self._t_gc) >= self.gc_period_ms:
            # slack to the next frame/tick (inputs and beacons can slip a few ms)
            nx = self._t_frame if ticks_diff(self._t_frame, self._t_tick) < 0 else self._t_tick
            if (ticks_diff(nx, self.clock()) >= self.gc_budget_ms
                    or ticks_diff(now, self._t_gc) >= 4 * self.gc_period_ms):
                self._gc(now)
        return self._wait(self.clock())

    def _wait(self, now):
        nx = self._t_input
        t = self._t_tick
        if ticks_diff(t, nx) < 0:
            nx = t
        t = self._t_frame
        if ticks_diff(t, nx) < 0:
            nx = t
        r = self.radio
        if r is not None:
            t = r.sched.next_t
            if t is not None and ticks_diff(t, nx) < 0:
                nx = t
        w = ticks_diff(nx, now)
        if w > HAPTIC_MS and self.hap_active():
            w = HAPTIC_MS
        if w > MAX_SLEEP_MS:
            w = MAX_SLEEP_MS
        return w if w > 0 else 0

    # ---- stages -------------------------------------------------------------------
    def _stage_radio(self, now):
        g = self.game
        link = self.link
        pm = g.pair.peer_mac
        if pm != link.partner:
            if pm is None:
                link.unlock()
            else:
                link.lock(pm, now=now)
        try:
            self.radio.poll(now, callback=self._rx_cb)
        except OSError:
            self.io_errors[S_RADIO] += 1
        link.tick(now)

    def _on_rx(self, mac, buf, n, rssi, t):
        r = self.link.on_packet(mac, buf, n, rssi, t)
        if r == R_BAD or r == R_DUP:
            return
        b = self.rxb
        b.unpack_from(buf)
        self.game.on_packet(t, mac, rssi, b)
        tl = self.tele
        if tl is not None and tl.beacons:
            tl.event(t, "bcn_rx", ("peer_seq", b.seq), ("rssi", rssi))

    def _stage_imu(self, now):
        try:
            self.feed.poll(now)
        except OSError:
            self.io_errors[S_IMU] += 1
        if self._chip and ticks_diff(now, self._t_chip) >= 0:
            self._t_chip = ticks_add(now, CHIP_MS)
            imu = self.imu
            try:
                if self._chip == CHIP_PENDING:
                    self._chip_poll()
                elif imu.features_ok():
                    self.feed.tracker.set_chip(now, imu.steps(), imu.activity())
                    if imu.poll_events() & EV_WRIST_WEAR:
                        self.game.on_wake(now)
            except OSError:
                self.io_errors[S_IMU] += 1

    def _chip_start(self):
        """Upload the BMA423 feature blob (non-blocking start; False/missing
        blob -> software steps only). Finished by ``_chip_poll``."""
        imu = self.imu
        lc = getattr(imu, "load_config", None)
        if lc is None:
            return
        try:
            if lc(wait=False):
                self._chip = CHIP_PENDING
                self._chip_poll()
        except OSError as e:
            self.errors["imu_features"] = e

    def _chip_poll(self):
        """Engine up: chip step counter + activity, latched wrist-wear event."""
        imu = self.imu
        if not imu.features_ready():
            if imu.feat_state != FEAT_PENDING:
                self._chip = CHIP_OFF         # init failed (feat_error says why)
            return
        imu.enable_step_counter()
        imu.enable_feature(FEAT_WRIST_WEAR)
        imu.map_interrupts(int1=EV_WRIST_WEAR, latched=True)
        self._chip = CHIP_ON

    def _on_tap(self, t):
        ok = self.game.on_accel_tap(t)
        tl = self.tele
        if tl is not None:
            tl.event(t, "tap", ("ok", ok))

    def _stage_touch(self, now):
        self._touch_n += 1
        if self._touch_n < self.touch_every:
            return
        self._touch_n = 0
        try:
            r = self.touch.read()
        except OSError:
            self.io_errors[S_TOUCH] += 1
            return
        gr = self.gestures
        code = gr.update(now, r[0], r[1], r[2])
        while code:
            self.game.on_gesture(now, code, gr.ev_x, gr.ev_y)
            tl = self.tele
            if tl is not None:
                tl.event(now, "touch", ("g", G.NAMES[code]), ("x", gr.ev_x), ("y", gr.ev_y))
            code = gr.next()

    def _stage_button(self, now):
        try:
            ev = self.pmu.poll()
        except OSError:
            self.io_errors[S_BUTTON] += 1
            return
        if not ev:
            return
        tl = self.tele
        if ev & EV_LONG:
            self.game.on_button(now, True)
            if tl is not None:
                tl.event(now, "btn", ("kind", "long"))
        if ev & EV_SHORT:
            self.game.on_button(now, False)
            if tl is not None:
                tl.event(now, "btn", ("kind", "short"))

    def _stage_logic(self, now):
        g = self.game
        nx = ticks_add(self._t_tick, TICK_MS)
        self._t_tick = nx if ticks_diff(nx, now) > 0 else ticks_add(now, TICK_MS)
        if self.pmu is not None and ticks_diff(now, self._t_batt) >= 0:
            self._t_batt = ticks_add(now, self.battery_ms)
            self._battery(now)
        if self.feed is not None:
            g.set_tracker(now, self.feed.tracker)
        q = self.params
        if self._fresh and q.haptic and self._hap_t != q.t_ms:
            self._hap_t = q.t_ms          # no frame drew it: play it anyway
            self.player.play_named(q.haptic, now)
        p = g.tick(now)
        self.params = p
        self._fresh = True
        self.ticks += 1
        b = g.buzz
        if b != self._buzz:
            self._buzz = b
            self.player.set_mode(_BUZZ_MODE[b])
        self._screen(now, p)
        if g.power_off:
            self._power_off(now)

    def _battery(self, now):
        pmu = self.pmu
        try:
            pct = pmu.battery_percent()
            if self.tele is not None:
                self.batt_mv = pmu.battery_voltage()
            low = pct is not None and pct <= T.BATT_SHUTDOWN_PCT
            vb = self._vbus
            if low and vb is not None and vb():
                low = False                   # on USB: never an automatic power-off
                self._low_n = 0
                pct = T.BATT_SHUTDOWN_PCT + 1
        except OSError:
            self.io_errors[S_LOGIC] += 1
            return
        if low:
            self._low_n += 1
            if self._low_n < BATT_LOW_READS:  # hold it back; re-read soon
                self._t_batt = ticks_add(now, BATT_RECHECK_MS)
                pct = T.BATT_SHUTDOWN_PCT + 1
        else:
            self._low_n = 0
        self.game.set_battery(now, pct)

    def _screen(self, now, p):
        d = self.display
        on = self.game.screen_on
        if on != self.screen_is_on:
            self.screen_is_on = on
            if d is not None:
                try:
                    if on:
                        d.wake(p.backlight)
                    else:
                        d.sleep()
                except OSError:
                    self.io_errors[S_LOGIC] += 1
            self.bl_level = p.backlight if on else 0.0
            if on:
                self._t_frame = now      # first frame at once (§8: no intro)
        if on and p.backlight != self.bl_level:
            self.bl_level = p.backlight
            if d is not None:
                d.brightness(p.backlight)

    def _power_off(self, now):
        """Game said so: motor off, panel asleep, then the AXP202 cuts power."""
        self.powered_off = True
        self.running = False
        tl = self.tele
        if tl is not None:
            tl.event(now, "pwr", ("off", True))
            tl.flush(now, force=True)
        self.quiet()
        d = self.display
        try:
            if d is not None:
                d.sleep()
            if self.pmu is not None:
                _shutdown_pmu(self.pmu)
        except OSError:
            self.io_errors[S_LOGIC] += 1

    def _stage_render(self, now):
        p = self.params
        if p is None:
            return
        cap = p.fps_cap or T.FPS_TARGET
        per = 1000 // cap
        nx = ticks_add(self._t_frame, per)
        self._t_frame = nx if ticks_diff(nx, now) > 0 else ticks_add(now, per)
        r = self.renderer
        has_event = self._fresh and bool(p.haptic) and self._hap_t != p.t_ms
        self._fresh = False
        if r is None:
            ev = self._no_renderer(now, p, has_event)
        else:
            g = self.game
            rn = g.runes
            if rn is not None:
                r.runes = rn
            r.sun = g.sun
            r.menu_rows = g.menu_rows
            d = self._hdisp if self.screen_is_on else None
            ev = r.frame(p, d, now)
            now = self.clock()        # events start when the frame is out
            if d is not None:
                self.frames += 1
                self.frames_total += 1
                self._fps_n += 1
        el = ticks_diff(now, self._fps_t)
        if el >= 1000:
            self.fps = self._fps_n * 1000.0 / el
            self._fps_n = 0
            self._fps_t = now
        if has_event:
            self._hap_t = p.t_ms
            tl = self.tele
            if tl is not None:
                tl.event(now, "haptic", ("pattern", p.haptic))
        pl = self.player
        for i in range(len(ev)):
            if i == 0 and has_event and ev[0] == p.haptic:
                pl.play_named(ev[0], now)
            else:
                pl.heartbeat(ev[i], now)
        if has_event and (not ev or ev[0] != p.haptic):
            pl.play_named(p.haptic, now)   # renderer did not echo it: play it anyway

    def _no_renderer(self, now, p, has_event):
        """No renderer (no framebuf): heartbeats from the player's metronome."""
        hb = p.heartbeat
        per = p.pulse_period_ms * (p.heartbeat_every or 1) if hb else 0
        if hb != self._metro or per != self._metro_ms:
            self._metro = hb
            self._metro_ms = per
            self.player.set_metronome(per, now, hb)
        return _NO_EVENTS

    def _stage_tx(self, now):
        r = self.radio
        s = r.sched
        per = 1000 // self.game.beacon_hz
        if per != s.period:
            s.period = per
            s.jitter = per // 10 if per >= 20 else 1
        if not s.due(now):
            return
        b = self.tx
        b.next_seq()
        self.game.fill_beacon(b, now)
        b.pack_into(self.txbuf)
        if not r.maybe_send(now, self.txbuf):
            self.io_errors[S_TX] += 1

    def _stage_haptic(self, now):
        lvl = self.player.tick(now)
        if lvl != self._motor_lvl:
            self._motor_lvl = lvl
            if self.feed is not None:
                self.feed.motor(now, lvl)
            m = self.motor
            if m is not None:
                try:
                    m.set(lvl)
                except OSError:
                    self.io_errors[S_HAPTIC] += 1

    def _gc(self, now):
        a = self.us()
        self.gc_collect()
        d = ticks_diff(self.us(), a)
        self._t_gc = now
        self.gc_n += 1
        self.gc_last_us = d
        if d > self.gc_max_us:
            self.gc_max_us = d
        self._acc(S_GC, a)

    # ---- stats ----------------------------------------------------------------------
    def _init_stats(self):
        n = len(STAGES)
        self.st_us = [0] * n
        self.st_max = [0] * n
        self.st_n = [0] * n
        self.loops = 0
        self.frames = 0
        self.frames_total = 0
        self.ticks = 0
        self.gc_n = 0
        self.gc_last_us = 0
        self.gc_max_us = 0

    def _acc(self, i, a):
        b = self.us()
        d = ticks_diff(b, a)
        s = self.st_us
        s[i] += d
        self.st_n[i] += 1
        if d > self.st_max[i]:
            self.st_max[i] = d
        if s[i] > ACC_LIMIT_US:           # long window: decay, keep the average
            s[i] >>= 1
            self.st_n[i] >>= 1
        return b

    def stats(self, reset=True):
        """{fps, loops, frames, ticks, collect: (n, last_ms, max_ms),
        <stage>: (avg_ms, max_ms, calls)} since the last reset (allocates:
        REPL / notebook use). A stage whose time sum passes ``ACC_LIMIT_US``
        (~9 min of rendering) has sum and calls halved, so windows of any
        length stay allocation-free; its ``calls`` then undercount."""
        now = self.clock()
        el = ticks_diff(now, self._win_t) if self.started else 0
        fps = self.frames * 1000.0 / el if el > 0 else 0.0
        out = {"fps": round(fps, 1), "window_ms": el, "loops": self.loops,
               "frames": self.frames, "ticks": self.ticks,
               "collect": (self.gc_n, self.gc_last_us / 1000.0, self.gc_max_us / 1000.0)}
        for i in range(len(STAGES)):
            n = self.st_n[i]
            out[STAGES[i]] = (round(self.st_us[i] / 1000.0 / n, 2) if n else 0.0,
                              round(self.st_max[i] / 1000.0, 2), n)
        if self.radio is not None:
            out["radio_stats"] = self.radio.stats()
        if any(self.io_errors):
            out["io_errors"] = dict((STAGES[i], self.io_errors[i])
                                    for i in range(len(STAGES)) if self.io_errors[i])
        try:
            out["mem_free"] = gc.mem_free()
        except AttributeError:
            pass
        if reset:
            ft = self.frames_total
            self._init_stats()
            self.frames_total = ft
            self._win_t = now
        return out

    def print_stats(self, reset=True):
        """Print ``stats()`` as a small table (REPL)."""
        s = self.stats(reset)
        print("fps %.1f  loops %d  frames %d  ticks %d  window %d ms" % (
            s["fps"], s["loops"], s["frames"], s["ticks"], s["window_ms"]))
        print("stage       avg ms   max ms   calls")
        for k in STAGES:
            v = s[k]
            print("%-10s %7.2f  %7.2f  %6d" % (k, v[0], v[1], v[2]))
        g = s["collect"]
        print("gc: %d collects, last %.1f ms, max %.1f ms" % g)
        for k in ("mem_free", "io_errors", "radio_stats"):
            if k in s:
                print(k, s[k])
        if self.errors:
            print("parts missing:", self.errors)
