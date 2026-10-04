"""The watch main loop: hardware <-> ``finder.game.Game`` <-> ``ui.renderer``.

    from hal.board import Board
    from app.runtime import Runtime
    rt = Runtime(Board())
    rt.run()                    # Ctrl-C returns to the REPL
    rt.print_stats()            # fps and ms per stage since the last print

One ``step(now)`` does, in order:

  radio   drain ESP-NOW -> LinkMonitor (partner lock = ``game.pair.peer_mac``,
          seq dedup) -> valid beacons -> ``game.on_packet``
  imu     BMA423 FIFO -> MotionTracker (25 Hz, g) + bump spikes -> ``on_accel_tap``
          (the FIFO runs fast only while ``game.bump_armed()``, set each tick);
          once a second the feature engine (if ``bma423conf.bin`` loaded):
          chip steps/activity -> tracker, wrist-wear -> ``game.on_wake`` (only
          when the screen is off: polled up to 1 s late, face_up has usually
          woken it, and a second wake would drop taps for 300 ms). Before the
          touch stage, so a knock's spike is known when its touch's gesture
          arrives (screen-to-screen knocks touch the panel, ui-spec §8)
  touch   FT6336 -> GestureRecognizer (multi-touch ignored, §8). Also sampled
          between a frame's bands once TOUCH_GAP_MS (15) have passed, so a 60 ms tap
          measures right while a frame renders; what those samples find
          waits for this stage (the game changes only between frames):
          ``game.on_gesture``, and ``game.on_touch_down`` when a finger lands
          (rain filter), in time order: a press lands before its gesture; on
          one sample the gesture goes first (it ended the previous press)
  button  AXP202 PEK -> ``game.on_button`` (short / long; wakes when off)
  logic   every 100 ms: tracker + battery (every 10 s) -> ``game.tick``
          -> RenderParams; screen power; AXP202 shutdown only once
          ``game.power_off``. A reading at or under BATT_WARN_PCT that is
          lower than the game's value reaches the game only after
          ``BATT_LOW_READS`` such readings in a row (1 s apart), and a
          shutdown-level one never on USB; until then the game keeps its
          last value, so one sagging reading never alarms, turns the saver
          on or powers the watch off
  render  on the frame lock (app/pacer.py): the fastest of 20, 10, 8, 7, 6, 5
          fps at or under ``params.fps_cap`` (20, saver 10) that the loop's
          measured cost fits; each frame is drawn at its slot time, so motion
          steps evenly (ui-spec §4 rule 6). Bands -> ``display.push_strip``.
          With the screen off the renderer still runs state-only
          (``display=None``) so heartbeats keep their time grid (ui-spec §7).
          A frame takes ~80 ms on the watch, so the motor is serviced after
          every band the renderer blits or pushes and after its overlays
          (~5-15 ms apart) and the frame's haptic events start at the
          post-render time (pulses keep their 60 ms ERM floor). The backlight
          follows ``params.backlight`` after each frame, so a woken panel is
          lit only over a fresh frame (§8: no stale frame)
  radio   beacon (``game.fill_beacon``) at ``game.beacon_hz`` via ``maybe_send``
  haptic  ``params.haptic`` (``play_named``; telemetry logs only accepted ones)
          + renderer heartbeats (``heartbeat``) -> HapticPlayer (buzz mode) -> Motor.
          A renderer that announces its next heartbeat spawn gets the beat
          handed over ahead, at the spawn time (``_beats``), so it lands on its
          ring at any lock rate; the loop wakes for it (``player.beat_due``)
  gc      ``gc.collect()`` once per ``GC_PERIOD_MS`` (10 s) when the next frame
          is at least ``GC_BUDGET_MS`` away (forced after ``GC_FORCE_MS``). On
          the SPIRAM build a collect sweeps the whole 4 MB heap: about 70 ms
          however little is garbage (bring-up, 3 Oct 2026), so it runs rarely.
          The two-watch simulator allocates under 1.5 MB in 10 s per watch
          (fakes included) against 3.7 MB free, and MicroPython collects by
          itself if the heap ever runs out first

With ``fps_log_ms`` (main.py: 10000) the loop prints one line that often:

    fps 9.9 lock 10 miss 0 late 4/18 jit 2.1 max 112 cost 86 gc 1 log 0.6

frames shown per second, the frame lock, slots missed, how late frames started
after their slot (avg/max ms), the sd and max of the interval between shown
frames (ms: the frame-time jitter), the lock's frame cost (busy ms between
frames), collects in the window and how long the previous line took to print
(ms; under 128 bytes it fits the UART FIFO, so print never waits on the wire).
In debug mode the line goes to the telemetry sink's ``log`` instead: on USB a
print could land inside a record the link is still writing, so the link queues
it between records (the bridge shows it as a text line).

``step`` returns the ms until the next deadline; ``run`` sleeps exactly that
(never longer than ``MAX_SLEEP_MS``) with ``idle``, which ticks the motor
every ms while a pattern plays (edges on time without polling any bus).
Clock, sleep and gc are injectable, so tests drive several runtimes from one
fake clock. Every part is optional: ``Runtime(board, parts=("display",
"radio"))`` runs just those (a notebook subset); a part that fails to come up
is recorded in ``errors`` and skipped. OSErrors that reach the loop from a
part (I2C glitches) are counted in ``io_errors`` and never stop the loop; the
touch and radio drivers count their own bus errors (``touch_errors``,
``radio_stats`` in ``stats()``).

Telemetry (app/telemetry.py) runs after the haptic stage: a state record at
5 Hz, events as they happen. In debug mode its ``sink`` sends them to the laptop,
from that 5 Hz record only (never from the render stage), and the sink's
``pump`` runs once per pass and at each mid-frame service (the USB link writes what
the UART's FIFO has room for, so it never stalls the loop and keeps up while
a frame renders); ``begin`` gives it the radio's MAC, and ``stats()`` shows
the sink's counters (``debug_stats``).

With ``watchdog_ms`` (main.py: 8000) ``run`` feeds hal/watchdog.py once per
pass: the stoppable soft watchdog while on USB, switched once to the ESP32
hardware WDT when VBUS goes away (checked with the 10 s battery reading). A
game started on battery keeps the hardware WDT, so Ctrl-C then reboots the
watch within the timeout (tools/deploy.py hard-resets first for this reason).
"""

import gc

from finder.compat import sleep_ms as _sleep_ms, ticks_add, ticks_diff, ticks_ms, ticks_us
from finder import proto
from finder import tuning as T
from finder import gestures as G
from finder.game import Game
from finder.haptic_patterns import HapticPlayer
from finder.link import LinkMonitor, R_BAD, R_DUP
from app.pacer import FramePacer
from hal.axp202 import EV_SHORT, EV_LONG
from hal.bma423 import EV_WRIST_WEAR, FEAT_OK, FEAT_PENDING
from hal.watchdog import MODE_SOFT, Watchdog

PARTS = ("pmu", "display", "imu", "touch", "haptics", "radio")
GAME_ID = 1
TICK_MS = T.LOGIC_MS       # game logic rate (10 Hz)
INPUT_MS = 20              # touch/button/imu poll when nothing else is due
TOUCH_GAP_MS = 15          # touch samples between bands at least this far apart
HAPTIC_SLICE_MS = 1        # ``idle`` motor tick while a pattern plays
BATTERY_MS = 10000
BATT_LOW_READS = 3         # falling readings <= 20 % in a row before the game sees one
BATT_RECHECK_MS = 1000     # ... taken this far apart
IMU_OUT_HZ = 25            # MotionTracker rate (25-50 Hz; 25 halves its float work)
CHIP_MS = 1000             # BMA423 feature engine (steps/activity/wrist) poll
CHIP_TRIES = 5             # engine starts in all: at boot, then at the poll after a bus error
MAX_SLEEP_MS = 50
GC_PERIOD_MS = 10000       # one ~70 ms collect per period (4 MB SPIRAM heap)
GC_FORCE_MS = 20000        # ... even with no slack before the next frame
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
CHIP_OFF = 0               # feature engine: absent / failed (software steps only)
CHIP_PENDING = 1           # blob uploaded, engine starting
CHIP_ON = 2
CHIP_RETRY = 3             # a bus error stopped the start: the poll starts it again
ACC_LIMIT_US = 0x1FFFFFFF  # stage sums halve past this (stay MicroPython small ints)
PMU_OFF_TRIES = 3          # shared I2C0: retry a glitched AXP202 power-off write
_NO_EVENTS = ()


class _HapticDisplay:
    """Display proxy for the renderer: ``service`` runs after each band the
    renderer blits, after its overlays and after each band it pushes (~5-15
    ms apart on the watch), so pulse edges stay within one band of schedule mid-frame,
    an announced heartbeat is handed over as soon as the frame's spawns are
    known, touch is sampled there once TOUCH_GAP_MS have passed since the
    last sample, and the debug sink is pumped there."""

    def __init__(self, rt, display):
        self.rt = rt
        self.d = display

    def push_strip(self, y0, h, buf):
        self.d.push_strip(y0, h, buf)
        self.service()

    def service(self):
        rt = self.rt
        t = rt.clock()
        r = rt.renderer
        if getattr(r, "hb_next_t", -1) != -1:
            rt._sync_beat(r, t)
        rt._stage_haptic(t)
        if rt.touch is not None and ticks_diff(t, rt._touch_t) >= TOUCH_GAP_MS:
            rt._sample_touch(t)
        tl = rt.tele
        if tl is not None and tl.sink is not None:
            tl.sink.pump(rt.clock())    # USB: refill the UART's FIFO mid-frame


class Runtime:
    """One watch: owns the Game, renderer, haptic player and loop timing."""

    def __init__(self, board, parts=PARTS, clock=None, sleep_ms=None, clock_us=None,
                 renderer=None, telemetry=None, gc_collect=None, z_sign=None, watchdog_ms=None,
                 fps_log_ms=None):
        self.board = board
        self.watchdog_ms = watchdog_ms
        self.fps_log_ms = fps_log_ms
        self.log_line = print                 # the fps line's sink (tests swap it)
        self.wd = None
        self.parts = parts
        self.clock = clock or ticks_ms
        self.sleep_ms = sleep_ms or _sleep_ms
        self.us = clock_us or ticks_us
        self.renderer = renderer
        self.tele = telemetry
        self.gc_collect = gc_collect or gc.collect
        self.z_sign = z_sign                  # None: the board's (hal/pins.py BMA423_Z_SIGN)
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
        self.gestures = G.GestureRecognizer()     # ui-spec §8 thresholds (finder/tuning.py)
        self.tx = proto.Beacon(GAME_ID)
        self.rxb = proto.Beacon(GAME_ID)
        self.txbuf = bytearray(proto.SIZE)
        self.params = None
        self.batt_mv = None
        self.batt_chg = None
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
        if self.pmu is not None:
            try:
                self.pmu.set_long_press_ms(T.BUTTON_LONG_MS)   # §8 long press (AXP202 PEK)
                self.pmu.clear_irqs()         # presses latched before the loop never open MENU
            except OSError:
                self.io_errors[S_BUTTON] += 1
        self._hdisp = None if self.display is None else _HapticDisplay(self, self.display)
        if self.renderer is None:
            try:
                from ui.renderer import Renderer
                self.renderer = Renderer()
            except (ImportError, MemoryError) as e:
                self.errors["renderer"] = e
        mac = None if self.radio is None else self.radio.mac
        if mac is not None and self.tele is not None:
            self.tele.set_mac(mac)            # the datagram ``mac`` (debug mode)
        now = self.clock() if now is None else now
        if self.imu is not None and hasattr(self.imu, "fifo_read_mg"):
            from app.imu_feed import ImuFeed
            self.feed = ImuFeed(self.imu, z_sign=self.z_sign, out_hz=IMU_OUT_HZ)
        blank = None if self.feed is None else self.feed.blanked
        self.game = Game(mac, blank_fn=blank, t_ms=now)
        if self.feed is not None:
            self.feed.on_tap = self._on_tap
        self.link = LinkMonitor(GAME_ID)
        self._vbus = getattr(self.pmu, "vbus_present", None)
        self._low_n = 0
        self._chip = CHIP_OFF
        self._chip_tries = 0
        if self.feed is not None:
            self._chip_start()
        self._rx_cb = self._on_rx
        self._td_t = None                     # touch-down sampled, not yet dispatched
        self._touch_t = now                   # last touch sample
        self._g_code = 0                      # gesture sampled, not yet dispatched
        self._g_t = self._g_t0 = self._g_x = self._g_y = 0
        self._buzz = -1
        self._fresh = False
        self._metro = None
        self._metro_ms = 0
        self._hb_at = None          # spawn time of the beat handed to the player ahead
        self._hb_was = None         # ... and of the one handed before it
        self._fps_t = now
        self._fps_n = 0
        self._t_tick = now
        self.pacer = FramePacer(now)
        self._busy = 0                        # loop busy ms since the last drawn frame
        self._gc_ms = 0
        self._log_t = now
        self._log_gc = 0                      # collects since the last fps line
        self._log_us = 0                      # the last fps line's print time
        self._t_input = now
        self._t_batt = now
        # a start stopped by a bus error waits one poll period (_chip_start says why)
        self._t_chip = ticks_add(now, CHIP_MS) if self._chip == CHIP_RETRY else now
        self._t_gc = now
        self._win_t = now
        self._motor_lvl = 0.0
        # a panel an earlier runtime (or the notebook) put to sleep: _screen wakes it
        self.screen_is_on = not getattr(self.display, "asleep", False)
        self.started = True
        self.running = True
        return self

    # ---- main loop -----------------------------------------------------------------
    def run(self, max_ms=None):
        """Loop until ``stop()``/power-off (or ``max_ms``); Ctrl-C stops it too.
        An exception is logged (telemetry ``crash``) and re-raised; telemetry
        is flushed on every exit, and ``quiet()`` stops the motor and slows
        the IMU."""
        self.begin()
        t0 = self.clock()
        if self.watchdog_ms and self.wd is None:
            self.wd = self._make_watchdog()
        try:
            while self.running:
                wd = self.wd                # _battery may switch it to the hardware WDT
                if wd is not None:
                    wd.feed()
                w = self.step()
                if max_ms is not None and ticks_diff(self.clock(), t0) >= max_ms:
                    break
                if w > 0:
                    self.idle(w)
        except Exception as e:              # field test: keep the cause on flash (R-08)
            tl = self.tele
            if tl is not None:
                try:
                    tl.event(self.clock(), "crash", ("e", repr(e)))
                except Exception:
                    pass                    # e.g. MemoryError again: keep the original
            raise
        finally:
            wd = self.wd                    # first: a second Ctrl-C in the cleanup below
            if wd is not None and wd.stop():    # must not leave the soft watchdog armed
                self.wd = None
            self.quiet()
            tl = self.tele
            if tl is not None:
                try:
                    tl.flush(force=True)    # the last <= flush_ms of records (also on Ctrl-C)
                except Exception:
                    pass
        return self

    def _make_watchdog(self, usb=None):
        """Hardware WDT on battery, stoppable timer watchdog on USB power
        (hal/watchdog.py); ``usb`` None reads VBUS (no PMU: battery)."""
        if usb is None:
            usb = False
            if self._vbus is not None:
                try:
                    usb = bool(self._vbus())
                except OSError:
                    pass
        try:
            return Watchdog(self.watchdog_ms, usb=usb, clock=self.clock)
        except (ImportError, AttributeError, ValueError, OSError) as e:
            self.errors["watchdog"] = e
            return None

    def stop(self):
        self.running = False

    def hap_active(self):
        """True while the motor is on or a pattern (event/heartbeat) plays."""
        return self._motor_lvl > 0 or self.player.active

    def idle(self, ms):
        """Sleep ``ms``; while a pattern plays, tick the motor every
        ``HAPTIC_SLICE_MS`` so its edges land on time (ERM 60 ms floor). A
        heartbeat due inside the sleep starts on time."""
        clk = self.clock
        end = ticks_add(clk(), ms)
        if not self.hap_active():
            d = self.player.beat_due
            if d is None or ticks_diff(d, end) > 0:
                self.sleep_ms(ms)
                return
            r = ticks_diff(d, clk())
            if r > 0:
                self.sleep_ms(r)
        while True:
            self._stage_haptic(clk())
            r = ticks_diff(end, clk())
            if r <= 0:
                return
            self.sleep_ms(HAPTIC_SLICE_MS if r > HAPTIC_SLICE_MS else r)

    def quiet(self):
        """Motor off and the IMU back at its slow rate (after Ctrl-C or an
        exception), so a notebook's next feed finds the chip as it expects."""
        m = self.motor
        if m is not None:
            try:
                m.set(0)
            except OSError:
                pass
        f = self.feed
        if f is not None:
            try:
                f.set_fast(False)
            except OSError:
                pass

    def step(self, now=None):
        """One loop pass at ``now``; returns ms until the next deadline."""
        if not self.started:
            self.begin(now)
        if now is None:
            now = self.clock()
        t0 = now
        self._gc_ms = 0
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
            now = self.clock()        # a panel wake blocks ~120 ms (SLPOUT)
        if ticks_diff(now, self.pacer.t_next) >= 0:
            self._stage_render(now, t0)
            a = self._acc(S_RENDER, a)
            now = self.clock()        # a frame takes ~80 ms on the watch
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
            sk = tl.sink
            if sk is not None:
                sk.pump(self.clock())   # USB: the FIFO's room now (record and flush took time)
            a = self._acc(S_TELE, a)
        self._t_input = ticks_add(now, INPUT_MS)
        if ticks_diff(now, self._t_gc) >= GC_PERIOD_MS:
            # slack to the next frame/tick (inputs and beacons can slip a few ms)
            nx = self.pacer.t_next
            if ticks_diff(self._t_tick, nx) < 0:
                nx = self._t_tick
            if (ticks_diff(nx, self.clock()) >= GC_BUDGET_MS
                    or ticks_diff(now, self._t_gc) >= GC_FORCE_MS):
                self._gc(now)
        if self.fps_log_ms and ticks_diff(now, self._log_t) >= self.fps_log_ms:
            self._fps_log(now)
        t = self.clock()
        self._busy += ticks_diff(t, t0) - self._gc_ms
        return self._wait(t)

    def _wait(self, now):
        nx = self._t_input
        t = self._t_tick
        if ticks_diff(t, nx) < 0:
            nx = t
        t = self.pacer.t_next
        if ticks_diff(t, nx) < 0:
            nx = t
        t = self.player.beat_due
        if t is not None and ticks_diff(t, nx) < 0:
            nx = t
        r = self.radio
        if r is not None:
            t = r.next_due()
            if t is not None and ticks_diff(t, nx) < 0:
                nx = t
        w = ticks_diff(nx, now)
        if w > MAX_SLEEP_MS:
            w = MAX_SLEEP_MS
        return w if w > 0 else 0

    # ---- stages -------------------------------------------------------------------
    def _stage_radio(self, now):
        link = self.link
        pm = self.game.pair.peer_mac
        if pm != link.partner:
            if pm is None:
                link.unlock()
            else:
                link.lock(pm)
        try:
            self.radio.poll(now, callback=self._rx_cb)
        except OSError:
            self.io_errors[S_RADIO] += 1

    def _on_rx(self, mac, buf, n, rssi, t):
        r = self.link.on_packet(mac, buf, n, t)
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
                if self._chip == CHIP_RETRY:
                    self._chip_start()
                elif self._chip == CHIP_PENDING:
                    self._chip_poll()
                elif imu.features_ok():
                    self.feed.tracker.set_chip(now, imu.steps(), imu.activity())
                    if imu.poll_events() & EV_WRIST_WEAR and not self.game.screen_on:
                        self.game.on_wake(now)
            except OSError:
                self.io_errors[S_IMU] += 1

    def _chip_start(self):
        """Start the BMA423 feature engine without blocking (no blob: software
        steps only); ``_chip_poll`` finishes it (hal.bma423 start/poll_features).
        A start stopped by a bus error is started again by the 1 s poll, one
        period later, CHIP_TRIES starts in all: a lost ACK on INIT_CTRL=1 has
        brought the engine up by then (no second upload), else the 6 KB blob
        goes again."""
        start = getattr(self.imu, "start_features", None)   # absent on a bare FIFO imu
        if start is None:
            return
        self._chip_tries += 1
        try:
            ok = start()
        except OSError as e:
            self.errors["imu_features"] = e
            self._chip = CHIP_RETRY if self._chip_tries < CHIP_TRIES else CHIP_OFF
            return
        self.errors.pop("imu_features", None)       # an earlier start's bus error
        if not ok:
            self._chip = CHIP_OFF
            return
        self._chip = CHIP_PENDING
        try:
            self._chip_poll()
        except OSError as e:
            self.errors["imu_features"] = e     # retried by the 1 s poll

    def _chip_poll(self):
        st = self.imu.poll_features()
        if st == FEAT_OK:                     # chip steps/activity + wrist-wear event on
            self._chip = CHIP_ON
            self.errors.pop("imu_features", None)   # an earlier bus error was retried
        elif st != FEAT_PENDING:
            self._chip = CHIP_OFF             # init failed (imu.feat_error says why)

    def _on_tap(self, t):
        ok = self.game.on_accel_tap(t)
        tl = self.tele
        if tl is not None:
            tl.event(t, "tap", ("ok", ok))

    def _sample_touch(self, t):
        """FT6336 -> GestureRecognizer at ``t``; a landing finger and a gesture
        wait for ``_stage_touch`` (at most one of each per frame)."""
        self._touch_t = t
        try:
            r = self.touch.read()           # [touching, x, y, contacts]
        except OSError:
            self.io_errors[S_TOUCH] += 1
            return
        gr = self.gestures
        code = gr.update(t, r[0], r[1], r[2], r[3] > 1)
        if gr.began and self._td_t is None:
            self._td_t = t
        if code and not self._g_code:
            self._g_code = code
            self._g_t = t
            self._g_x = gr.ev_x
            self._g_y = gr.ev_y
            self._g_t0 = gr.ev_t

    def _stage_touch(self, now):
        self._sample_touch(now)
        code = self._g_code
        td = self._td_t                     # in time order; on one sample the gesture
        self._td_t = None                   # first (it ended the press before that finger)
        if td is not None and (not code or ticks_diff(self._g_t, td) > 0):
            self.game.on_touch_down(td)     # a finger landed (rain filter)
            td = None
        if code:
            self._g_code = 0
            t = self._g_t
            self.game.on_gesture(t, code, self._g_x, self._g_y, self._g_t0)
            tl = self.tele
            if tl is not None:
                tl.event(t, "touch", ("g", G.NAMES[code]), ("x", self._g_x), ("y", self._g_y))
        if td is not None:
            self.game.on_touch_down(td)     # landed as (or after) the gesture's press ended

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
            self._t_batt = ticks_add(now, BATTERY_MS)
            self._battery(now)
        if self.feed is not None:
            g.set_tracker(now, self.feed.tracker)
        q = self.params
        if self._fresh and q.haptic:      # no frame drew it: play it anyway
            if self.player.play_named(q.haptic, now):
                self._log_haptic(now, q.haptic)
        p = g.tick(now)
        self.params = p
        self._fresh = True
        if self.feed is not None:
            try:
                self.feed.set_fast(g.bump_armed())
            except OSError:
                self.io_errors[S_IMU] += 1
        self.ticks += 1
        b = g.buzz
        if b != self._buzz:
            self._buzz = b
            self.player.set_mode(b)           # game.BUZZ_* are HapticPlayer modes
        self._screen(now, p)
        if g.power_off:
            self._power_off(now)

    def _battery(self, now):
        pmu = self.pmu
        vb = self._vbus
        try:
            pct = pmu.battery_percent()
            if self.tele is not None:
                self.batt_mv = pmu.battery_voltage()
                self.batt_chg = pmu.is_charging()
            usb = vb is not None and vb()
        except OSError:
            self.io_errors[S_LOGIC] += 1
            return
        wd = self.wd
        if wd is not None and vb is not None and not usb and wd.mode == MODE_SOFT:
            wd.stop()                         # unplugged: hardware WDT from now on (one way)
            self.wd = self._make_watchdog(usb=False)
        if usb and pct is not None and pct <= T.BATT_SHUTDOWN_PCT:
            pct = T.BATT_SHUTDOWN_PCT + 1     # on USB: never an automatic power-off
        self.game.set_usb(now, usb)           # on USB: the screen stays on (§8)
        b = self.game.battery
        if pct is not None and pct <= T.BATT_WARN_PCT and (b is None or pct < b):
            self._low_n += 1                  # a drop on the LOW-BATTERY ladder: confirm it
            if self._low_n < BATT_LOW_READS:  # hold back (game keeps its last value); re-read soon
                self._t_batt = ticks_add(now, BATT_RECHECK_MS)
                return
        self._low_n = 0
        self.game.set_battery(now, pct)

    def _screen(self, now, p):
        d = self.display
        on = self.game.screen_on
        if on != self.screen_is_on:
            self.screen_is_on = on
            if d is not None:
                try:
                    if on:              # dark until a fresh frame is out; motor ticks meanwhile
                        d.wake(0, wait=self.idle)
                    else:
                        d.sleep()
                except OSError:
                    self.io_errors[S_LOGIC] += 1
            self.bl_level = 0.0
            self._busy = 0
            if on:
                self.pacer.restart(now)  # first frame at once (§8: no intro)
        if on and self.renderer is None:
            self._backlight(p.backlight)  # no frames to wait for

    def _backlight(self, level):
        if level != self.bl_level:
            self.bl_level = level
            d = self.display
            if d is not None:
                d.brightness(level)

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
        if d is not None:
            try:
                d.sleep()
            except OSError:
                self.io_errors[S_LOGIC] += 1
        pmu = self.pmu
        if pmu is not None:
            for _ in range(PMU_OFF_TRIES):
                try:
                    pmu.shutdown()
                    return
                except OSError:
                    self.io_errors[S_LOGIC] += 1

    def _stage_render(self, now, t0=None):
        """A frame on the lock's grid: drawn at its slot time (``pacer.begin``),
        so the animation moves on by whole lock periods; ``t0`` is when this
        loop pass started (its busy time up to here belongs to this frame)."""
        p = self.params
        if p is None:
            return
        r = self.renderer
        d = self._hdisp if (r is not None and self.screen_is_on) else None
        busy = None
        if d is not None:                     # state-only frames leave the lock alone
            pre = 0 if t0 is None else ticks_diff(now, t0)
            busy = self._busy + pre
            self._busy = -pre                 # the rest of this pass: the next frame's
        else:
            self._busy = 0                    # not a drawn frame's cost: never grows
        pc = self.pacer
        slot = pc.begin(now, p.fps_cap or T.FPS_TARGET, busy)
        event = p.haptic if self._fresh else None    # params.haptic plays once
        self._fresh = False
        if r is None:
            self._no_renderer(now, p)
            ev = _NO_EVENTS
        else:
            ev = r.frame(p, d, slot)
            now = self.clock()        # events start when the frame is out
            if d is not None:
                pc.shown(now)
                self._backlight(p.backlight)    # lit only over a fresh frame
                self.frames += 1
                self._fps_n += 1
        el = ticks_diff(now, self._fps_t)
        if el >= 1000:
            self.fps = self._fps_n * 1000.0 / el
            self._fps_n = 0
            self._fps_t = now
        pl = self.player
        if event is not None and pl.play_named(event, now):
            self._log_haptic(now, event)
        if r is not None:
            self._beats(r, ev, now)

    def _beats(self, r, ev, now):
        """Heartbeats on ring spawns (an event replaces one). A renderer that
        announces its next heartbeat spawn (``hb_next_t``, ``hb_next``) gets
        the beat handed to the player ahead (``_sync_beat``), so the buzz lands
        on its ring at any frame lock rate, and the frame that spawns that
        ring (``hb_t0``, the spawn's time) does not start it again. Otherwise
        a beat starts when its frame is out, up to a frame late."""
        pl = self.player
        if getattr(r, "hb_next_t", -1) == -1:
            if len(ev):
                pl.heartbeat(ev[0], now)
            return
        if len(ev):
            t0 = r.hb_t0
            if t0 != self._hb_at and t0 != self._hb_was:
                pl.heartbeat(ev[0], now)    # not handed ahead: starts now
        self._sync_beat(r, now)

    def _sync_beat(self, r, now):
        """Hand the announced spawn's beat to the player at the spawn time
        once it falls before the frame after next. Re-read after the
        renderer's state step and between bands (``_HapticDisplay.service``),
        before the motor is ticked: a spawn still ahead that moves or goes (new
        tempo, MENU) moves or drops its beat before it can start."""
        nt = r.hb_next_t
        at = self._hb_at
        pl = self.player
        pc = self.pacer
        if nt is not None and ticks_diff(nt, ticks_add(pc.t_next, pc.period)) <= 0:
            if nt != at and not pl.beat_playing:
                pl.heartbeat(r.hb_next, nt)
                self._hb_was = at
                self._hb_at = nt
        elif (at is not None and ticks_diff(at, now) > 0 and pl.beat_due == at
                and nt != at):
            pl.cancel_heartbeat()           # its spawn moved or is gone
            self._hb_at = None

    def _log_haptic(self, now, name):
        """Only events the player accepted (§7 guard drops some): the log
        must not claim a buzz the wrist never felt."""
        tl = self.tele
        if tl is not None:
            tl.event(now, "haptic", ("pattern", name))

    def _no_renderer(self, now, p):
        """No renderer (no framebuf): heartbeats from the player's metronome,
        silent while no packets arrive (``ring_live`` False: ghost ring, §4)."""
        hb = p.heartbeat if p.ring_live else None
        per = p.pulse_period_ms * (p.heartbeat_every or 1) if hb else 0
        if hb != self._metro or per != self._metro_ms:
            self._metro = hb
            self._metro_ms = per
            self.player.set_metronome(per, now, hb)

    def _stage_tx(self, now):
        r = self.radio
        r.set_rate(self.game.beacon_hz)
        if not r.due(now):
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
        t = self.clock()
        self.gc_collect()
        self._gc_ms += ticks_diff(self.clock(), t)    # not frame cost: the lock ignores it
        self._log_gc += 1
        d = ticks_diff(self.us(), a)
        self._t_gc = now
        self.gc_n += 1
        self.gc_last_us = d
        if d > self.gc_max_us:
            self.gc_max_us = d
        self._acc(S_GC, a)

    def _fps_log(self, now):
        """The serial fps line (module docstring); its own print is timed and
        shown on the next line. Allocates (formatting): once per fps_log_ms."""
        pc = self.pacer
        el = ticks_diff(now, self._log_t)
        n = pc.frames
        a = self.us()
        line = "fps %.1f lock %d miss %d late %d/%d jit %.1f max %d cost %d gc %d log %.1f" % (
            n * 1000.0 / el if el > 0 else 0.0, pc.fps, pc.missed,
            pc.late_sum // n if n else 0, pc.late_max, pc.jitter_ms(), pc.iv_max,
            pc.cost, self._log_gc, self._log_us / 1000.0)
        tl = self.tele
        if tl is not None and tl.sink is not None:
            tl.sink.log(line)       # debug mode: USB queues it between records
        else:
            self.log_line(line)
        self._log_us = ticks_diff(self.us(), a)
        self._log_t = now
        self._log_gc = 0
        pc.window()

    # ---- stats ----------------------------------------------------------------------
    def _init_stats(self):
        n = len(STAGES)
        self.st_us = [0] * n
        self.st_max = [0] * n
        self.st_n = [0] * n
        self.loops = 0
        self.frames = 0
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
        tl = self.tele
        if tl is not None and tl.sink is not None:
            out["debug_stats"] = tl.sink.stats()
        te = 0 if self.touch is None else getattr(self.touch, "errors", 0)
        if te:
            out["touch_errors"] = te
        if any(self.io_errors):
            out["io_errors"] = dict((STAGES[i], self.io_errors[i])
                                    for i in range(len(STAGES)) if self.io_errors[i])
        try:
            out["mem_free"] = gc.mem_free()
        except AttributeError:
            pass
        if reset:
            self._init_stats()
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
        for k in ("mem_free", "io_errors", "touch_errors", "radio_stats", "debug_stats"):
            if k in s:
                print(k, s[k])
        if self.errors:
            print("parts missing:", self.errors)
