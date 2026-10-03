"""Top-level game state machine (ui-spec §6 screens, §7 haptics, §8 interaction).

    PAIRING (looking/seen/confirmed/calibrate/split) -> SEARCHING | FAR..HOT
    FAR..HOT (+ DIRECTION arrow overlay, + trend chevrons) <-> SCANNING
    HOT --bump--> FOUND --tap/press--> PAIRING split (new round)
    FAR..HOT/SCANNING --5 s silence--> LINK_LOST --3 packets/2 s--> zone
    MENU is an overlay over any screen; LOW-BATTERY is a modifier.

Pure logic: no hardware. The main loop (or the simulator) feeds it

    g = Game(my_mac)
    g.on_packet(t_rx, mac, rssi, beacon)   # every valid beacon (proto.Beacon fields)
    g.set_motion(t, activity, steps, step_rate_hz, tilt_deg, face_up)
    g.on_gesture(t, code, x, y)            # finder.gestures codes
    g.on_button(t, long=False)
    g.on_accel_tap(t)                      # BMA423 single-tap IRQ
    g.on_wake(t)                           # wrist raise
    g.set_battery(t, pct)
    p = g.tick(t)                          # 10 Hz -> RenderParams (ui-spec §3)
    g.fill_beacon(tx_beacon, now)          # at transmit time; rate = g.beacon_hz

and hands ``g.runes``, ``g.menu_rows`` and ``g.sun`` to the renderer. It never
declares FOUND from RSSI: only a matched bump (both accelerometer taps within
400 ms while both watches are in HOT) or the fallback (both short presses
within 3 s in HOT with band <= ~5).

Choices where the spec is silent (all starting values):
  * in HOT the button short press is the fallback bump press; a second short
    press within 1 s (partner not pressing) starts a scan instead, so the scan
    stays reachable with the button alone (§11). A screen tap starts a scan
  * the expected partner beacon rate mirrors the partner's ``beacon_hz``
    (scan > saver > HOT > normal); after a rate change the lower of the old and
    new rates is expected for one delivery window, so the 5 s meter never
    reports a false drop while it still holds packets sent at the old rate
  * the LINK_LOST GO BACK / KEEP ON hint uses the last non-zero trend if it
    is at most 30 s old at link loss (``lost_trend``), else 0; it is forgotten
    on relink, a new round and SEARCHING
  * event haptics raised under the MENU are held (highest priority wins) and
    played when it closes; heartbeats are simply skipped
  * one-shot deadlines are cleared once passed and "since" stamps have their
    age capped at 6 h, so no ticks_diff ever sees a wrapped (> 2^29 ms) age
  * END ROUND (menu, confirmed) forgets the partner and returns to PAIRING
    looking; FOUND -> new round keeps pairing and calibration
  * during the scan sweep both slots stay empty (§3); a paused sweep shows
    only through the wedge colour (``sweep[3]``)
  * the partner's "low battery" LINK_LOST banner reads ``FRIEND LOW BATTERY``
    (``LOST, FRIEND LOW BAT`` is 20 chars, over the 18-char label limit)
  * an expiring arrow is dropped from RenderParams at once; the renderer runs
    the 600 ms shrink itself when ``arrow_deg`` goes None
  * the zone heartbeat is muted while the arrow is in ``turn`` (or static
    ``face``): only the pacer ticks play there (§6 DIRECTION table)
  * starting a scan hides the arrow but keeps it (aging like a lost-link
    arrow: steps and still time grow sigma, the phase clock pauses); it comes
    back if the scan ends in ``ready`` and is dropped once the sweep starts
  * ``ready`` cancels silently after WRIST_DOWN_MS face-down or 15 s without
    completing, so an accidental tap never pins the screen on and 20 Hz
    beacons; the screen stays on in ``ready`` only while face-up

Not yet wired (need a RenderParams contract change in finder/render_params.py
and ui/renderer.py first): the HOLD FLAT / STAND STILL chip while the sweep is
paused (§3 suppresses both slots in ``sweep``), the LINK_LOST last-trend mark
(§3 forces ``trend`` 0 there; the value is kept in ``lost_trend``), the 3 %
BYE inward ring at -120 px/s (outside the §3 speed/wavelength ranges), and the
WALK TEST probe with its 6th menu row (R-15, P2).
"""

from finder.compat import ticks_diff, ticks_add
from finder import tuning as T
from finder import proto
from finder.render_params import RenderParams
from finder.estimators.base import ACT_STILL
from finder.proximity import Proximity, DeliveryMeter, DELIVERY_WINDOW_MS
from finder.scan import ScanSession, R_CANCEL, HINT_FLAT, HINT_CHEST
from finder.scan import READY as SCAN_READY, SWEEP as SCAN_SWEEP
from finder import arrow as A
from finder import pairing as P
from finder.session import (MotionSnap, PeerView, LiveMirror, screen_code, fmt_mss,
                            SC_PAIRING, SC_HOT, SC_FOUND, SC_SCANNING, SC_BYE,
                            ST_PRESS, ST_GOODBYE, ST_CONFIRMED)

# game modes (MENU is an overlay flag)
M_PAIRING = "PAIRING"
M_SEARCHING = "SEARCHING"
M_HUNT = "HUNT"            # FAR..HOT, screen = zone name
M_SCANNING = "SCANNING"
M_FOUND = "FOUND"
M_LINK_LOST = "LINK_LOST"

FAR = 0
NEAR = 1
WARM = 2
HOT = 3

# finder.gestures codes (copied: that module is edited elsewhere)
G_TAP = 1
G_DOUBLE_TAP = 2
G_LONG_PRESS = 3
G_SWIPE_U = 6              # finder.gestures SWIPE_U / SWIPE_D
G_SWIPE_D = 7

BUZZ_FULL = 0
BUZZ_EVENTS = 1
BUZZ_OFF = 2
_BUZZ_ROW = ("BUZZ: FULL", "BUZZ: EVENTS", "BUZZ: OFF")
MENU_ROWS = ("RESUME", "SUN: OFF", "BUZZ: FULL", "PLACE: OUT", "END ROUND")
ROW_RESUME, ROW_SUN, ROW_BUZZ, ROW_PLACE, ROW_END = 0, 1, 2, 3, 4
MENU_VISIBLE = 4           # rows on screen; the list scrolls (ui-spec MENU)
MENU_TOP_MAX = len(MENU_ROWS) - MENU_VISIBLE
MENU_CONFIRM = "SURE? PRESS"

BUMP_FRESH_MS = 2000       # own tap older than this never matches
PEER_FOUND_TAP_MS = 2000   # partner already FOUND: follow if we tapped this recently
UNRELIABLE_PIN_MS = 3000   # StatusStrip stays pinned this long after 'unreliable' clears
FOUND_FOLLOW_MS = 500      # FOUND at least this long before following a new round
BATT_REARM_PCT = 3         # a threshold re-arms once charged this far above it
HOT_SCAN_PRESS_MS = 1000   # HOT: 2nd short press within this starts a scan
LAST_TREND_KEEP_MS = 30000  # a trend older than this at link loss counts as none
BUMP_KEEP_MS = 70000       # own tap forgotten after this (> proto BUMP_MAX)
STALE_MS = 21600000        # "since" stamps never age past 6 h (ticks wrap at 2^29)
SCAN_READY_MAX_MS = 15000  # scan ``ready`` never flat within this: silent cancel

W_SEARCHING = "SEARCHING"
W_WALK_ABOUT = "WALK ABOUT"
W_HOLD_STILL = "HOLD STILL"
W_BUMP = "BUMP!"
W_FOUND = "FOUND"
W_AGAIN = "TAP=AGAIN"
W_SAVER = "SAVER ON"
W_BYE = "BYE"
T_TAP_TO_SCAN = "TAP TO SCAN"
T_LOOK_AROUND = "LOOK AROUND"
T_TAP_WATCHES = "TAP WATCHES"
T_FRIEND_SCANNING = "FRIEND SCANNING"
T_BACK = "BACK IN RANGE"
T_FRIEND_OFF = "FRIEND IS OFF"
T_FRIEND_LOW = "FRIEND LOW BATTERY"

_NONE12 = (None,) * T.SCAN_BINS
_FP = T.FIELD_PAIRING_LOOKING
_FS = T.FIELD_SEARCHING
_FL = T.FIELD_LINK_LOST


def _make_est():
    """Default estimator: ``finder.estimators.make()``, else kalman2."""
    try:
        from finder import estimators
        mk = getattr(estimators, "make", None)
        if mk is not None:
            return mk()
    except ImportError:
        pass
    from finder.estimators.kalman2 import Estimator
    return Estimator()


def _bins(src):
    """Scan bins as a tuple clamped to 0..1 (scan.py float rounding can dip below 0)."""
    return tuple(None if v is None else 0.0 if v < 0.0 else 1.0 if v > 1.0 else v
                 for v in src)


def _cap(t_ms, s):
    """Stamp ``s`` with its age capped at ``STALE_MS``."""
    if s is not None and ticks_diff(t_ms, s) > STALE_MS:
        return ticks_add(t_ms, -STALE_MS)
    return s


def _pattern_ms(name):
    s = 0
    for v in T.HAPTIC_PATTERNS[name]:
        s += v
    return s


class Game:
    """One watch's game. Times are ticks ms; call ``tick`` at 10 Hz."""

    def __init__(self, my_mac=None, est=None, arrow_mode=A.MODE_GUIDED, blank_fn=None,
                 t_ms=0, battery=100):
        self.my_mac = bytes(my_mac) if my_mac is not None else None
        self.est = est if est is not None else _make_est()
        self.indoor = False
        self._set_n(T.PATH_LOSS_N)
        self.arrow_mode = arrow_mode
        self.blank_fn = blank_fn
        self.pair = P.Pairing(my_mac)
        self.px = Proximity()
        self.meter = DeliveryMeter()
        self.scan = ScanSession(t_ms, blank_fn=blank_fn)
        self.peer = PeerView()
        self.me = MotionSnap()
        self.mirror = LiveMirror()
        self.menu_rows = list(MENU_ROWS[:MENU_VISIBLE])   # visible window for the renderer (set in tick)
        self._rows = list(MENU_ROWS)
        self.menu_top = 0
        self.battery = battery
        self.sun = False
        self.buzz = BUZZ_FULL
        self.params = None
        # battery ladder: survives END ROUND
        self._bat_level = 100
        self._inter_until = None
        self._bye_t = None
        self.goodbye_left = 0
        self.power_off = False
        self.reset(t_ms)

    # ---- lifecycle ---------------------------------------------------------------

    def set_place(self, indoor):
        """Outdoor (default) or indoor/crowded: sets the path-loss exponent (tokens calibrate.n / n_indoor)."""
        self.indoor = bool(indoor)
        self._set_n(T.PATH_LOSS_N_INDOOR if self.indoor else T.PATH_LOSS_N)

    def _set_n(self, n):
        f = getattr(self.est, "set_exponent", None)   # duck-typed estimators may not have it
        if f is not None:
            f(n)

    def reset(self, t_ms):
        """Everything back to PAIRING ``looking`` (forget the partner)."""
        self.mode = M_PAIRING
        self.mode_t = t_ms
        self.pair.reset(t_ms)
        self.peer.reset()
        self.px.reset()
        self.meter.reset()
        self.est.reset()
        self.arrow = None
        self._stash = None        # arrow hidden under a scan (restored if it ends in ready)
        self._rdown = None        # scan ready: face-down since
        self._p1m = None
        self.rssi_last = None
        self.bump_t = None
        self.taps = 0
        self._tap_hot = False
        self._used_my = None
        self._used_peer = None
        self._press_t = None
        self._touch_t = None
        self._touches = [None, None, None]
        self._touch_i = 0
        self._touch_block = None
        self._hap = None
        self._burst = False
        self._blank_from = t_ms
        self._blank_until = None
        self._toast = None
        self._toast_sev = "info"
        self._toast_until = t_ms
        self._hint = None
        self._hint_until = t_ms
        self._last_trend = 0
        self._last_trend_t = None
        self.lost_trend = 0       # last trend at link loss (LINK_LOST hint / mark)
        self._held = None         # event haptic held while the MENU is open
        self._hz = self.meter.expected_hz
        self._hz_lo = self._hz
        self._hz_t = None
        self._unrel_t = None
        self._br_since = None
        self.bump_ready = False
        self._br_fired = False
        self._still_since = None
        self._still_done = False
        self._peer_sweep = False
        self._hold_n = 0
        self._hold_t = t_ms
        self.round_t0 = None
        self.found_t = None
        self._time_text = None
        self._lost_zone = None
        self._lost_band = None
        self._lost_i = 0.0
        self.menu_open = False
        self.menu_sel = 0
        self._menu_t = t_ms
        self._confirm_t = None
        self._set_rows()
        self.screen_on = True
        self._wake_t = t_ms
        self._input_t = t_ms
        self._down_since = None
        self._fu_prev = True
        self._peer_bat_warned = False
        self.scans = 0            # completed scans with a fix (arrows earned)
        self.state_byte = SC_PAIRING

    # ---- inputs ------------------------------------------------------------------
    def set_motion(self, t_ms, activity, steps=0, step_rate_hz=0.0, tilt_deg=None,
                   face_up=True):
        """Own motion (finder.motion.MotionTracker outputs or the simulator)."""
        self.me.set(t_ms, activity, steps, step_rate_hz, tilt_deg, face_up)

    def set_tracker(self, t_ms, mt):
        self.me.from_tracker(t_ms, mt)

    def set_battery(self, t_ms, pct):
        self.battery = None if pct is None else int(pct)

    def on_packet(self, t_ms, mac, rssi, b):
        """One valid beacon ``b`` (proto.Beacon fields) from ``mac`` at ``rssi`` dBm."""
        if self.power_off:
            return
        pr = self.pair
        if self.mode == M_PAIRING and pr.sub in (P.LOOKING, P.SEEN, P.CONFIRMED):
            if not pr.on_candidate(t_ms, mac, rssi, (b.state & 0x0F) == SC_PAIRING):
                return
            self._rx(t_ms, rssi, b)
            pr.on_rssi(t_ms, rssi)
            pr.set_peer_confirmed(t_ms, self.peer.confirmed)
            return
        if pr.peer_mac is None or mac != pr.peer_mac:
            return
        self._rx(t_ms, rssi, b)
        if self.mode == M_PAIRING and pr.sub == P.CALIBRATE:
            pr.on_rssi(t_ms, rssi)
            return
        peer_rssi = self.peer.rssi_last
        self.est.update(t_ms, rssi, peer_rssi, self.me.info, self.peer.motion)
        if self.mode == M_SCANNING:
            self.scan.on_packet(t_ms, rssi, peer_rssi)
        a = self.arrow
        if a is not None and a.phase == A.PH_TURN:
            self.mirror.add(t_ms, rssi)

    def _rx(self, t_ms, rssi, b):
        self.peer.on_beacon(t_ms, b)
        self.rssi_last = rssi
        self.meter.note(t_ms)

    def on_accel_tap(self, t_ms):
        """BMA423 single-tap IRQ; returns True if accepted as a bump tap."""
        tt = self._touch_t
        if tt is not None and 0 <= ticks_diff(t_ms, tt) < T.BUMP_TOUCH_GUARD_MS:
            return False
        if self.blanked(t_ms):
            return False
        self.bump_t = t_ms
        self.taps = (self.taps + 1) & 7
        self._tap_hot = self.mode == M_HUNT and self.px.zone == HOT
        return True

    def on_wake(self, t_ms):
        """Wrist raise: screen on, boost, ignore touches for 300 ms."""
        self._wake(t_ms)

    def on_gesture(self, t_ms, code, x=120, y=120):
        """Touch gesture (finder.gestures code) at the gesture start (x, y)."""
        if code == 0 or not self.screen_on or self.power_off:
            return
        if ticks_diff(t_ms, self._wake_t) < T.WAKE_TOUCH_IGNORE_MS:
            return
        if self._touch_block is not None and ticks_diff(t_ms, self._touch_block) < 0:
            return
        self._touch_t = t_ms
        if self._touch_burst(t_ms, 2 if code == G_DOUBLE_TAP else 1):
            return
        bt = self.bump_t
        if (self.mode == M_HUNT and self.px.zone == HOT and bt is not None
                and 0 <= ticks_diff(t_ms, bt) < T.BUMP_TAP_IGNORE_MS):
            return          # part of a bump
        self._input(t_ms)
        if code == G_LONG_PRESS:
            if self.menu_open:
                self._menu_select(t_ms, self.menu_sel)
            else:
                self._menu_open(t_ms)
            return
        if self.menu_open and (code == G_SWIPE_U or code == G_SWIPE_D):
            self._menu_scroll(t_ms, self.menu_top + (1 if code == G_SWIPE_U else -1))
            return
        if code != G_TAP and code != G_DOUBLE_TAP:
            return
        if self.menu_open:
            row = self._row_at(y)
            if row is not None:
                row += self.menu_top
                self.menu_sel = row
                self._menu_select(t_ms, row)
            return
        dx = x - T.CENTER[0]
        dy = y - T.CENTER[1]
        if dx * dx + dy * dy > T.TAP_R_MAX_PX * T.TAP_R_MAX_PX:
            return
        self._primary(t_ms, False)

    def on_button(self, t_ms, long=False):
        """Side button: short = primary action (wake only when off), long = MENU."""
        if self.power_off:
            return
        if not self.screen_on:
            self._wake(t_ms)
            return
        self._input(t_ms)
        if long:
            if self.menu_open:
                self._menu_select(t_ms, self.menu_sel)
            else:
                self._menu_open(t_ms)
            return
        if self.menu_open:
            if self._confirm_t is not None and self.menu_sel == ROW_END:
                self._menu_select(t_ms, ROW_END)
            else:
                self._confirm_t = None      # moving off END ROUND cancels SURE? PRESS
                self.menu_sel = (self.menu_sel + 1) % len(MENU_ROWS)
                self._menu_t = t_ms
                self._scroll_to_sel()
            return
        self._primary(t_ms, True)

    def _touch_burst(self, t_ms, n):
        """Rain/sleeve filter: >= 3 touches in 1 s blocks touches for 2 s."""
        for _ in range(n):
            self._touches[self._touch_i] = t_ms
            self._touch_i = (self._touch_i + 1) % T.TOUCH_BURST_COUNT
        old = self._touches[self._touch_i]
        if old is not None and ticks_diff(t_ms, old) <= T.TOUCH_BURST_WINDOW_MS:
            self._touch_block = ticks_add(t_ms, T.TOUCH_BURST_IGNORE_MS)
            for i in range(T.TOUCH_BURST_COUNT):
                self._touches[i] = None
            return True
        return False

    def _primary(self, t_ms, button):
        m = self.mode
        if m == M_PAIRING:
            self.pair.confirm(t_ms)
        elif m == M_HUNT:
            a = self.arrow
            if a is not None and a.tap(t_ms):
                return
            if button and self.px.zone == HOT:
                pt = self._press_t
                pv = self.peer
                if (pt is not None and 0 <= ticks_diff(t_ms, pt) <= HOT_SCAN_PRESS_MS
                        and not (pv.fresh(t_ms) and pv.pressed)):
                    self._press_t = None  # double press: scan, not a fallback bump
                    self._start_scan(t_ms)
                else:
                    self._press_t = t_ms  # fallback bump press
                return
            self._start_scan(t_ms)
        elif m == M_SCANNING:
            self.scan.cancel(t_ms)
        elif m == M_FOUND:
            if ticks_diff(t_ms, self.found_t) >= T.FOUND_CELEBRATE_MS:
                self._new_round(t_ms)

    def _input(self, t_ms):
        self._input_t = t_ms

    def _wake(self, t_ms):
        self.screen_on = True
        self._wake_t = t_ms
        self._input_t = t_ms
        self._down_since = None

    # ---- haptics / toasts --------------------------------------------------------
    def blanked(self, t_ms):
        """Accelerometer blanking: own events (+150 ms) or the external ``blank_fn``."""
        u = self._blank_until
        if (u is not None and ticks_diff(t_ms, self._blank_from) >= 0
                and ticks_diff(u, t_ms) > 0):
            return True
        f = self.blank_fn
        return f is not None and bool(f(t_ms))

    def _emit(self, t_ms, name):
        if name is None:
            return
        if self.menu_open:            # paused under the menu: keep the strongest
            h = self._held
            if h is None or T.HAPTIC_RANK[name] >= T.HAPTIC_RANK[h]:
                self._held = name
            return
        h = self._hap
        if h is None or T.HAPTIC_RANK[name] >= T.HAPTIC_RANK[h]:
            self._hap = name
        end = ticks_add(t_ms, _pattern_ms(name) + T.HAPTIC_BLANKING_MS)
        u = self._blank_until
        if u is None or ticks_diff(t_ms, u) >= 0:
            self._blank_from = t_ms
            self._blank_until = end
        elif ticks_diff(end, u) > 0:
            self._blank_until = end

    def _toast_set(self, t_ms, text, sev="info"):
        self._toast = text
        self._toast_sev = sev
        self._toast_until = ticks_add(t_ms, T.TOAST_MS)

    def _hint_set(self, t_ms, text):
        self._hint = text
        self._hint_until = ticks_add(t_ms, T.HINT_CHIP_MS)

    # ---- per tick ----------------------------------------------------------------
    def tick(self, t_ms):
        """Advance the logic (10 Hz); returns this frame's RenderParams."""
        self._hap = None
        self._burst = False
        if not self.power_off:
            self._expire(t_ms)
            self._power(t_ms)
            self._battery(t_ms)
            m = self.mode
            if m == M_PAIRING:
                self._tick_pairing(t_ms)
            elif m == M_SEARCHING:
                self._tick_searching(t_ms)
            elif m == M_HUNT:
                self._tick_hunt(t_ms)
            elif m == M_SCANNING:
                self._tick_scan(t_ms)
            elif m == M_FOUND:
                self._tick_found(t_ms)
            elif m == M_LINK_LOST:
                self._tick_lost(t_ms)
            self._tick_menu(t_ms)
            h = self._held
            if h is not None and not self.menu_open:
                self._held = None
                self._emit(t_ms, h)
            if self.mode != M_HUNT:
                self._peer_sweep = False
            self._set_expected(t_ms)
        self.state_byte = self._state_byte(t_ms)
        self._menu_window()
        p = self._params(t_ms)
        self.params = p
        return p

    def _expire(self, t_ms):
        """Clear passed deadlines and cap old stamps (ticks_diff wraps at 2^29 ms)."""
        if self._toast is not None and ticks_diff(self._toast_until, t_ms) <= 0:
            self._toast = None
        if self._hint is not None and ticks_diff(self._hint_until, t_ms) <= 0:
            self._hint = None
        s = self._inter_until
        if s is not None and ticks_diff(s, t_ms) <= 0:
            self._inter_until = None
        s = self._blank_until
        if s is not None and ticks_diff(s, t_ms) <= 0:
            self._blank_until = None
        s = self._touch_block
        if s is not None and ticks_diff(s, t_ms) <= 0:
            self._touch_block = None
        s = self._unrel_t
        if s is not None and ticks_diff(t_ms, s) >= UNRELIABLE_PIN_MS:
            self._unrel_t = None
        s = self._press_t
        if s is not None and ticks_diff(t_ms, s) > T.FALLBACK_PRESS_WINDOW_MS:
            self._press_t = None
        s = self.bump_t
        if s is not None and ticks_diff(t_ms, s) > BUMP_KEEP_MS:
            self.bump_t = None
        s = self._last_trend_t
        if s is not None and ticks_diff(t_ms, s) > LAST_TREND_KEEP_MS:
            self._last_trend = 0
            self._last_trend_t = None
        self._wake_t = _cap(t_ms, self._wake_t)
        self._input_t = _cap(t_ms, self._input_t)
        self.mode_t = _cap(t_ms, self.mode_t)
        self.found_t = _cap(t_ms, self.found_t)
        self.round_t0 = _cap(t_ms, self.round_t0)
        self._down_since = _cap(t_ms, self._down_since)
        self._still_since = _cap(t_ms, self._still_since)
        self._br_since = _cap(t_ms, self._br_since)
        self._menu_t = _cap(t_ms, self._menu_t)
        self._hold_t = _cap(t_ms, self._hold_t)
        self.peer.expire(t_ms, STALE_MS)

    def _peer_hz(self):
        """The partner's beacon rate as it decides it (mirrors ``beacon_hz``)."""
        pv = self.peer
        if pv.sweeping or (self.mode == M_SCANNING and self.scan.active):
            return T.BEACON_HZ_SCAN
        if pv.battery is not None and pv.battery <= T.BATT_CRITICAL_PCT:
            return T.BEACON_HZ_SAVER
        if pv.screen == SC_HOT:
            return T.BEACON_HZ_HOT
        return T.BEACON_HZ_NORMAL

    def _set_expected(self, t_ms):
        """Delivery meter rate: the lower of old/new for one window after a change."""
        hz = self._peer_hz()
        m = self.meter
        if hz != self._hz:
            e = m.expected_hz
            self._hz = hz
            self._hz_lo = hz if hz < e else e
            self._hz_t = t_ms
        if self._hz_t is not None:
            if ticks_diff(t_ms, self._hz_t) < DELIVERY_WINDOW_MS:
                m.expected_hz = self._hz_lo
                return
            self._hz_t = None
        m.expected_hz = hz

    def _update_px(self, t_ms):
        self.px.update_est(t_ms, self.est, self.me.activity, self.meter.ratio(t_ms))
        tr = self.px.trend
        if tr:
            self._last_trend = tr
            self._last_trend_t = t_ms
        if self.px.unreliable:
            self._unrel_t = t_ms

    def _forget_trend(self):
        self._last_trend = 0
        self._last_trend_t = None
        self.lost_trend = 0

    def _scan_hint(self, t_ms):
        """``TAP TO SCAN`` for 4 s on entering FAR or NEAR (no arrow shown)."""
        if self.arrow is None:
            self._hint_set(t_ms, T_TAP_TO_SCAN)

    # PAIRING
    def _tick_pairing(self, t_ms):
        pr = self.pair
        if pr.sub == P.LOOKING and self.peer.last_t is not None:
            self.peer.reset()
        if pr.sub in (P.SEEN, P.CONFIRMED) and self._bump_match(t_ms):
            self._consume_bump()
            pr.bump(t_ms)
        self._emit(t_ms, pr.update(t_ms))
        if pr.toast:
            self._toast_set(t_ms, pr.toast, "info")
        if pr.p1m is not None and pr.p1m != self._p1m:
            self._p1m = pr.p1m
            self.est.calibrate(pr.p1m)
            self.est.reset()
            self.px.reset()
        if pr.sub == P.SPLIT:
            self._update_px(t_ms)
        elif pr.sub == P.DONE:
            self.round_t0 = t_ms
            if self.peer.live3(t_ms) and self.px.zone is not None:
                self._enter_hunt(t_ms, False)
            else:
                self._enter_searching(t_ms)

    # SEARCHING
    def _enter_searching(self, t_ms):
        self.mode = M_SEARCHING
        self.mode_t = t_ms
        self.arrow = None
        self._forget_trend()
        self.est.reset()
        self.px.rearm()

    def _tick_searching(self, t_ms):
        if self.peer.live3(t_ms) and self.est.dist_m is not None:
            self._update_px(t_ms)
            if self.px.zone is not None:
                self._enter_hunt(t_ms, True)

    # FAR..HOT
    def _enter_hunt(self, t_ms, fanfare):
        self.mode = M_HUNT
        self.mode_t = t_ms
        z = self.px.zone
        self._input_t = t_ms
        if fanfare:
            self._burst = True
            self._emit(t_ms, "CLOSER")
        if z == FAR or z == NEAR:
            self._scan_hint(t_ms)
        elif z == HOT:
            self._hint_set(t_ms, T_LOOK_AROUND)
        self._br_since = None
        self.bump_ready = False
        self._br_fired = False

    def _tick_hunt(self, t_ms):
        age = self.peer.age(t_ms)
        if age is None or age > T.LINK_LOST_AFTER_MS:
            self._enter_lost(t_ms)
            return
        self._update_px(t_ms)
        px = self.px
        z = px.zone
        ch = px.zone_changed
        if ch:
            self._input_t = t_ms
            if ch > 0:
                self._burst = True
                self._emit(t_ms, "CLOSER")
                if z == HOT:
                    self._hint_set(t_ms, T_LOOK_AROUND)
                elif z == NEAR:
                    self._scan_hint(t_ms)
            else:
                self._emit(t_ms, "FARTHER")
                if self._hint == T_LOOK_AROUND:
                    self._hint = None
                if z == FAR or z == NEAR:
                    self._scan_hint(t_ms)
        self._update_arrow(t_ms, True)
        self._update_bump_ready(t_ms)
        self._update_still_hint(t_ms)
        self._update_peer_scan(t_ms)
        self._check_found(t_ms)

    def _update_arrow(self, t_ms, link_ok):
        a = self.arrow
        if a is None:
            return
        was = a.phase
        pv = self.peer
        a.update(t_ms, self.me.activity, self.me.steps, self.px.trend,
                 pv.fresh(t_ms) and pv.walking, link_ok, self.px.unreliable)
        if a.phase == A.PH_TURN and was != A.PH_TURN:
            self.mirror.reset(t_ms)
        self._emit(t_ms, a.haptic)
        if a.toast:
            self._toast_set(t_ms, a.toast, "info")
        if a.done:
            self.arrow = None

    def _update_bump_ready(self, t_ms):
        if self.px.zone == HOT and self.px.band_idx == T.BUMP_READY_BAND:
            if self._br_since is None:
                self._br_since = t_ms
            if ticks_diff(t_ms, self._br_since) >= T.BUMP_READY_HOLD_MS:
                self.bump_ready = True
                if not self._br_fired:
                    self._br_fired = True
                    self._emit(t_ms, "DOUBLE")
        else:
            self._br_since = None
            self.bump_ready = False
            self._br_fired = False

    def _update_still_hint(self, t_ms):
        z = self.px.zone
        if z != HOT and self.arrow is None and self.me.activity == ACT_STILL:
            if self._still_since is None:
                self._still_since = t_ms
            elif (not self._still_done
                  and ticks_diff(t_ms, self._still_since) >= T.HINT_STILL_MS):
                self._still_done = True
                self._hint_set(t_ms, T_TAP_TO_SCAN)
        else:
            self._still_since = None
            self._still_done = False
            if z == WARM and self._hint == T_TAP_TO_SCAN:
                self._hint = None        # WARM: TAP TO SCAN only while still

    def _update_peer_scan(self, t_ms):
        pv = self.peer
        sw = pv.sweeping and pv.fresh(t_ms)
        if sw and not self._peer_sweep:
            self._hold_n = 1
            self._hold_t = t_ms
            self._emit(t_ms, "HOLD")
        elif (sw and self.me.walking and self._hold_n < T.SCAN_HOLD_REPEAT_MAX
              and ticks_diff(t_ms, self._hold_t) >= T.SCAN_HOLD_REPEAT_MS):
            self._hold_n += 1
            self._hold_t = t_ms
            self._emit(t_ms, "HOLD")
        self._peer_sweep = sw

    # bump / FOUND
    def _bump_match(self, t_ms):
        m = self.bump_t
        q = self.peer.tap_t
        if m is None or q is None or m == self._used_my or q == self._used_peer:
            return False
        d = ticks_diff(t_ms, m)
        if d < 0 or d > BUMP_FRESH_MS:
            return False
        d = ticks_diff(m, q)
        return -T.BUMP_WINDOW_MS <= d <= T.BUMP_WINDOW_MS

    def _consume_bump(self):
        self._used_my = self.bump_t
        self._used_peer = self.peer.tap_t

    def _pressed(self, t_ms):
        pt = self._press_t
        return pt is not None and 0 <= ticks_diff(t_ms, pt) <= T.FALLBACK_PRESS_WINDOW_MS

    def _check_found(self, t_ms):
        px = self.px
        pv = self.peer
        if px.zone != HOT or not pv.fresh(t_ms):
            return
        ps = pv.screen
        if ps != SC_HOT and ps != SC_FOUND:
            return
        if self._tap_hot and self._bump_match(t_ms):
            self._consume_bump()
            self._enter_found(t_ms)
            return
        band_ok = px.band_idx is not None and px.band_idx <= T.FALLBACK_MAX_BAND
        if band_ok and self._pressed(t_ms) and pv.pressed:
            self._enter_found(t_ms)
            return
        if ps == SC_FOUND:
            bt = self.bump_t
            if (self._tap_hot and bt is not None and bt != self._used_my
                    and 0 <= ticks_diff(t_ms, bt) <= PEER_FOUND_TAP_MS) or self._pressed(t_ms):
                self._consume_bump()
                self._enter_found(t_ms)

    def _enter_found(self, t_ms):
        self.mode = M_FOUND
        self.mode_t = t_ms
        self.found_t = t_ms
        self.arrow = None
        self._press_t = None
        self.bump_ready = False
        self._burst = True
        self._emit(t_ms, "FOUND")
        s = 0 if self.round_t0 is None else ticks_diff(t_ms, self.round_t0) // 1000
        if s > 99 * 60 + 59:
            s = 99 * 60 + 59
        self._time_text = "TIME %d:%02d" % (s // 60, s % 60)

    def _tick_found(self, t_ms):
        pv = self.peer
        if (pv.fresh(t_ms) and pv.screen == SC_PAIRING
                and ticks_diff(t_ms, self.found_t) >= FOUND_FOLLOW_MS):
            self._new_round(t_ms)

    def _new_round(self, t_ms):
        self.mode = M_PAIRING
        self.mode_t = t_ms
        self.pair.start_split(t_ms)
        self.arrow = None
        self._forget_trend()
        self._hint = None
        self._press_t = None
        self._consume_bump()

    # SCANNING
    def _start_scan(self, t_ms):
        pv = self.peer
        if pv.fresh(t_ms) and pv.sweeping:
            self._toast_set(t_ms, T_FRIEND_SCANNING, "info")
            return
        a = self.arrow
        self._stash = a if a is not None and not a.done else None
        self.arrow = None
        self._rdown = None
        self._hint = None
        self.scan.reset(t_ms)
        self.mode = M_SCANNING
        self.mode_t = t_ms

    def _tick_scan(self, t_ms):
        sc = self.scan
        age = self.peer.age(t_ms)
        if age is None or age > T.LINK_LOST_AFTER_MS:
            sc.cancel(t_ms)
            self._unstash()
            self._enter_lost(t_ms)
            return
        me = self.me
        st = self._stash
        if st is not None:        # hidden: sigma keeps growing, the phase clock pauses
            st.update(t_ms, me.activity, me.steps, 0, False, False, self.px.unreliable)
            if st.done:
                self._stash = None
        tilt = me.tilt_deg
        if tilt is None:
            tilt = 0.0 if me.face_up else 90.0
        sc.on_motion(t_ms, tilt, me.steps, me.activity)
        if sc.phase == SCAN_READY:
            if me.face_up:
                self._rdown = None
            elif self._rdown is None:
                self._rdown = t_ms
            if ((self._rdown is not None and ticks_diff(t_ms, self._rdown) >= T.WRIST_DOWN_MS)
                    or ticks_diff(t_ms, self.mode_t) >= SCAN_READY_MAX_MS):
                sc.cancel(t_ms)   # accidental tap: silent, the arrow comes back
        pv = self.peer
        if pv.fresh(t_ms):
            sc.on_peer(t_ms, pv.walking)
            if (pv.sweeping and sc.active and self.my_mac is not None
                    and self.pair.peer_mac is not None and self.my_mac > self.pair.peer_mac):
                sc.cancel(t_ms)       # lower MAC keeps its scan
                self._toast_set(t_ms, T_FRIEND_SCANNING, "info")
        sc.update(t_ms)
        self._emit(t_ms, sc.pop_haptic())
        if sc.phase == SCAN_SWEEP:
            self._stash = None    # the sweep started: the old arrow is spent
        if sc.done(t_ms):
            r = sc.result
            if r is not None:
                self.arrow = A.make(r[0], r[1], t_ms, self.arrow_mode)
                if self.arrow is not None:
                    self.scans += 1
            elif sc.reason != R_CANCEL and sc.toast:
                self._toast_set(t_ms, sc.toast, "info")
            self.mode = M_HUNT
            self.mode_t = t_ms
            self._still_since = None
            if self._unstash():
                self._update_arrow(t_ms, True)

    def _unstash(self):
        """Put the arrow hidden by a scan back; True if there was one."""
        st = self._stash
        self._stash = None
        if st is None or st.done or st.phase == A.PH_EXPIRE:
            return False
        self.arrow = st
        return True

    # LINK_LOST
    def _enter_lost(self, t_ms):
        px = self.px
        self.mode = M_LINK_LOST
        self.mode_t = t_ms
        self._lost_zone = px.zone
        self._lost_band = px.band
        self._lost_i = px.intensity
        lt = self._last_trend
        self.lost_trend = lt if self._last_trend_t is not None else 0
        self._last_trend = 0
        self._last_trend_t = None
        self.bump_ready = False
        self._hint = None
        self._emit(t_ms, "LOST")
        self.est.reset()
        px.rearm()
        self._update_arrow(t_ms, False)

    def _tick_lost(self, t_ms):
        self._update_arrow(t_ms, False)
        if self.peer.live3(t_ms) and self.est.dist_m is not None:
            self._update_px(t_ms)
            if self.px.zone is not None:
                self._enter_hunt(t_ms, True)
                self._toast_set(t_ms, T_BACK, "info")
                self._update_arrow(t_ms, True)

    def _lost_banner(self, t_ms):
        pv = self.peer
        if pv.goodbye:
            return (T_FRIEND_OFF, "critical", True)
        if pv.battery is not None and pv.battery <= T.BATT_BANNER_PCT:
            return (T_FRIEND_LOW, "warn", True)
        el = pv.age(t_ms)
        el = ticks_diff(t_ms, self.mode_t) + T.LINK_LOST_AFTER_MS if el is None else el
        s = "LOST " + fmt_mss(el)
        if el >= T.LOST_HINT_AFTER_MS:
            s += " KEEP ON" if self.lost_trend > 0 else " GO BACK"
        return (s, "warn", True)

    # MENU
    def _set_rows(self):
        r = self._rows
        r[ROW_SUN] = "SUN: ON" if self.sun else "SUN: OFF"
        r[ROW_BUZZ] = _BUZZ_ROW[self.buzz]
        r[ROW_PLACE] = "PLACE: IN" if self.indoor else "PLACE: OUT"
        r[ROW_END] = MENU_CONFIRM if self._confirm_t is not None else MENU_ROWS[ROW_END]

    def _menu_window(self):
        """Copy the visible rows into ``menu_rows`` at tick time only, so the renderer's
        rows and ``sub`` (built in the same tick) always describe the same window."""
        r = self._rows
        top = self.menu_top
        v = self.menu_rows
        for k in range(MENU_VISIBLE):
            v[k] = r[top + k]

    def _scroll_to_sel(self):
        """Keep the selected row inside the visible window."""
        s = self.menu_sel
        if s < self.menu_top:
            self.menu_top = s
        elif s >= self.menu_top + MENU_VISIBLE:
            self.menu_top = s - MENU_VISIBLE + 1
        self._set_rows()

    def _menu_scroll(self, t_ms, top):
        """Swipe: show another part of the list; the selection follows into view.
        Any swipe cancels a pending END ROUND confirm (SURE? PRESS may scroll away)."""
        self._menu_t = t_ms
        self._confirm_t = None
        self.menu_top = 0 if top < 0 else MENU_TOP_MAX if top > MENU_TOP_MAX else top
        if self.menu_sel < self.menu_top:
            self.menu_sel = self.menu_top
        elif self.menu_sel >= self.menu_top + MENU_VISIBLE:
            self.menu_sel = self.menu_top + MENU_VISIBLE - 1
        self._set_rows()

    def _menu_sub(self):
        """RenderParams.sub for MENU: visible index + '^'/'v' when rows are hidden above/below."""
        s = str(self.menu_sel - self.menu_top)
        if self.menu_top > 0:
            s += "^"
        if self.menu_top < MENU_TOP_MAX:
            s += "v"
        return s

    def _menu_open(self, t_ms):
        if self.mode == M_SCANNING:
            self.scan.cancel(t_ms)     # the menu freezes the field: no scan under it
        self.menu_open = True
        self.menu_sel = 0
        self.menu_top = 0
        self._menu_t = t_ms
        self._confirm_t = None
        self._set_rows()

    def _menu_close(self):
        self.menu_open = False
        self._confirm_t = None
        self._set_rows()

    def _row_at(self, y):
        i = 0
        for y0 in T.MENU_ROWS_Y:
            if y0 <= y < y0 + T.MENU_ROW_H:
                return i
            i += 1
        return None

    def _menu_select(self, t_ms, row):
        self._menu_t = t_ms
        if row != ROW_END:
            self._confirm_t = None          # another row cancels a pending END ROUND confirm
        if row == ROW_RESUME:
            self._menu_close()
        elif row == ROW_SUN:
            self.sun = not self.sun
        elif row == ROW_BUZZ:
            self.buzz = (self.buzz + 1) % 3
        elif row == ROW_PLACE:
            self.set_place(not self.indoor)
        elif row == ROW_END:
            if self._confirm_t is not None:
                self._menu_close()
                self.reset(t_ms)
                return
            self._confirm_t = t_ms
        self._set_rows()

    def _tick_menu(self, t_ms):
        if not self.menu_open:
            return
        if ticks_diff(t_ms, self._menu_t) >= T.MENU_AUTOCLOSE_MS:
            self._menu_close()
        elif self._confirm_t is not None and ticks_diff(t_ms, self._confirm_t) >= T.MENU_CONFIRM_MS:
            self._confirm_t = None
            self._set_rows()

    # power / battery
    def _keep_on(self):
        if self.mode == M_SCANNING:
            return self.scan.phase != SCAN_READY or self.me.face_up
        a = self.arrow
        return a is not None and (a.phase == A.PH_TURN or a.phase == A.PH_FACE)

    def _power(self, t_ms):
        fu = self.me.face_up
        if fu and not self._fu_prev:
            self._wake(t_ms)
        self._fu_prev = fu
        if not self.screen_on:
            return
        if fu or self._keep_on():
            self._down_since = None
            return
        if self._down_since is None:
            self._down_since = t_ms
        lim = 3000 if self._critical() else T.WRIST_DOWN_MS
        if ticks_diff(t_ms, self._down_since) >= lim:
            self.screen_on = False

    def _critical(self):
        b = self.battery
        return b is not None and b <= T.BATT_BANNER_PCT

    @property
    def saver(self):
        b = self.battery
        return b is not None and b <= T.BATT_CRITICAL_PCT

    def _battery(self, t_ms):
        b = self.battery
        if b is not None:
            lv = self._bat_level
            if b <= T.BATT_SHUTDOWN_PCT and self._bye_t is None:
                if self.menu_open:
                    self._menu_close()    # shutting down: BYE is shown, NOPE plays
                self._bye_t = t_ms
                self.goodbye_left = T.GOODBYE_BEACONS
                self._emit(t_ms, "NOPE")
            elif b <= T.BATT_BANNER_PCT and lv > T.BATT_BANNER_PCT:
                self._bat_level = T.BATT_BANNER_PCT
                self._toast_set(t_ms, "BATTERY %d%%" % T.BATT_BANNER_PCT, "critical")
                self._emit(t_ms, "BATT")
            elif b <= T.BATT_CRITICAL_PCT and lv > T.BATT_CRITICAL_PCT:
                self._bat_level = T.BATT_CRITICAL_PCT
                self._inter_until = ticks_add(t_ms, T.BATT_INTERSTITIAL_MS)
                self._emit(t_ms, "BATT")
            elif b <= T.BATT_WARN_PCT and lv > T.BATT_WARN_PCT:
                self._bat_level = T.BATT_WARN_PCT
                self._toast_set(t_ms, "BATTERY %d%%" % T.BATT_WARN_PCT, "warn")
                self._emit(t_ms, "BATT")
            r = BATT_REARM_PCT
            rec = (100 if b > T.BATT_WARN_PCT + r else T.BATT_WARN_PCT
                   if b > T.BATT_CRITICAL_PCT + r else T.BATT_CRITICAL_PCT
                   if b > T.BATT_BANNER_PCT + r else self._bat_level)
            if rec > self._bat_level:
                self._bat_level = rec
        pb = self.peer.battery
        if pb is not None:
            if pb <= T.BATT_WARN_PCT and not self._peer_bat_warned:
                self._peer_bat_warned = True
                self._toast_set(t_ms, "FRIEND BATT %d%%" % T.BATT_WARN_PCT, "warn")
            elif pb > T.BATT_WARN_PCT + BATT_REARM_PCT:
                self._peer_bat_warned = False
        if (self._bye_t is not None and self.goodbye_left <= 0
                and ticks_diff(t_ms, self._bye_t) >= T.BYE_WORD_MS):
            self.power_off = True

    # ---- beacon ------------------------------------------------------------------
    def _screen(self):
        m = self.mode
        if m == M_HUNT:
            return T.ZONE_NAMES[self.px.zone]
        return m

    def _state_byte(self, t_ms):
        if self._bye_t is not None:
            return SC_BYE | ST_GOODBYE
        s = screen_code(self._screen())
        if self.mode == M_HUNT and self.px.zone == HOT and self._pressed(t_ms):
            s |= ST_PRESS
        if self.mode == M_PAIRING and self.pair.confirmed:
            s |= ST_CONFIRMED
        return s

    @property
    def beacon_hz(self):
        """Beacon rate (§5.8): 20 Hz in HOT and on both watches during a scan, 5 in saver."""
        if (self.mode == M_SCANNING and self.scan.active) or self._peer_sweep:
            return T.BEACON_HZ_SCAN
        if self.saver:
            return T.BEACON_HZ_SAVER
        if self.mode == M_HUNT and self.px.zone == HOT:
            return T.BEACON_HZ_HOT
        return T.BEACON_HZ_NORMAL

    @property
    def runes(self):
        return self.pair.runes

    @property
    def screen(self):
        return "MENU" if self.menu_open else self._screen()

    def fill_beacon(self, b, now):
        """Write this watch's fields into ``b`` (proto.Beacon) at transmit time."""
        b.state = self.state_byte
        b.set_flags(self.mode == M_SCANNING and self.scan.active, self.taps, self.me.walking)
        b.rssi_last = proto.clamp_i8(self.rssi_last)
        b.rssi_filt = proto.clamp_i8(self.est.rssi_f)
        b.steps = self.me.steps & 0xFFFF
        b.activity = self.me.activity
        b.battery = proto.BATT_UNKNOWN if self.battery is None else self.battery
        b.set_bump(now, self.bump_t)
        if self._bye_t is not None and self.goodbye_left > 0:
            self.goodbye_left -= 1
        return b

    # ---- RenderParams --------------------------------------------------------------
    def _backlight(self, t_ms):
        if not self.screen_on:
            return 0.0
        if self.sun:
            return T.BACKLIGHT_BOOST
        if self.saver:
            return T.SAVER_BACKLIGHT
        if ticks_diff(t_ms, self._wake_t) < T.WAKE_BOOST_MS:
            return T.BACKLIGHT_BOOST
        if (not self._keep_on() and self.me.face_up
                and ticks_diff(t_ms, self._input_t) >= T.IDLE_DIM_MS):
            return T.BACKLIGHT_LOW
        return T.BACKLIGHT_NORMAL

    def _status(self, t_ms, scr):
        own = 100 if self.battery is None else self.battery
        own = 0 if own < 0 else 100 if own > 100 else own
        pv = self.peer
        pb = pv.battery
        if pb is not None and pb > 100:
            pb = 100
        age = pv.age(t_ms)
        if age is None or age > T.LIVE_WINDOW_MS or self.pair.peer_mac is None:
            q = 0
        else:
            q = int(self.meter.ratio(t_ms) * T.LINK_Q_MAX + 0.5)
            q = 0 if q < 0 else T.LINK_Q_MAX if q > T.LINK_Q_MAX else q
        pinned = own <= T.BATT_WARN_PCT
        vis = False
        if scr == "SEARCHING" or scr in T.ZONE_NAMES:
            ut = self._unrel_t
            unrel = (scr in T.ZONE_NAMES and ut is not None
                     and ticks_diff(t_ms, ut) < UNRELIABLE_PIN_MS)
            vis = pinned or unrel or ticks_diff(t_ms, self._wake_t) < T.STATUS_AFTER_WAKE_MS
        elif scr == "LINK_LOST":
            vis = pinned
        return (own, pb, q, vis)

    def _params(self, t_ms):
        m = self.mode
        px = self.px
        pv = self.peer
        age = pv.age(t_ms)
        live = age is not None and age <= T.LIVE_WINDOW_MS
        scr = self._screen()
        sub = None
        zone = None
        ramp = "green"
        inten = px.intensity
        speed = 0.0
        period = 2400
        glow = T.GLOW_R_A + T.GLOW_R_B * inten
        glyph = "glow"
        adeg = cone = style = None
        trend = 0
        strong = False
        cd = None
        band = None
        stale = False
        word = None
        top = None
        banner = None
        sweep = None
        hb = None
        every = 1
        if m == M_PAIRING:
            pr = self.pair
            sub = pr.sub
            if sub == P.DONE:
                sub = P.SPLIT
            glyph = "runes"
            inten = _FP[1]
            glow = _FP[4]
            if sub == P.LOOKING:
                speed = _FP[2]
                period = _FP[3]
                top = "PAIR"
                word = "LOOKING"
            elif sub == P.SEEN:
                top = "SAME RUNES?"
                word = "TAP = YES"
            elif sub == P.CONFIRMED:
                top = "WAITING"
                word = "WAITING"
            elif sub == P.CALIBRATE:
                glyph = "countdown"
                cd = pr.countdown
                top = "HOLD STILL" if pr.unstable else "STAND 1 STEP APART"
                word = W_HOLD_STILL
            else:
                glyph = "countdown"
                cd = pr.countdown
                top = "NO PEEKING"
                word = "GO" if pr.go else "SPLIT UP"
                z = px.zone
                zone = z
                if z is not None:
                    speed = T.ZONE_SPEED_PX_S[z]
                    period = T.ZONE_PERIOD_MS[z]
                    inten = px.intensity
                    glow = T.GLOW_R_A + T.GLOW_R_B * inten
                else:
                    speed = _FP[2]
                    period = _FP[3]
        elif m == M_SEARCHING:
            ramp = _FS[0]
            inten = _FS[1]
            speed = _FS[2]
            period = _FS[3]
            glow = _FS[4]
            glyph = "seeker"
            live = False
            word = W_WALK_ABOUT if ticks_diff(t_ms, self.mode_t) >= T.SEARCHING_WALK_ABOUT_MS \
                else W_SEARCHING
        elif m == M_HUNT:
            z = px.zone
            zone = z
            speed = T.ZONE_SPEED_PX_S[z]
            period = T.ZONE_PERIOD_MS[z]
            band = px.band
            trend = px.trend
            strong = px.trend_strong and trend != 0
            a = self.arrow
            if a is None or not (a.phase == A.PH_TURN or a.phase == A.PH_FACE):
                hb = T.ZONE_HEARTBEAT[z]     # turn/face: only the pacer ticks
                every = T.ZONE_HB_EVERY[z]
            if a is not None and a.phase == A.PH_TURN and self.mirror.value is not None:
                inten = self.mirror.value
                glow = T.GLOW_R_A + T.GLOW_R_B * inten
            if a is not None and a.glyph == "arrow" and a.phase != A.PH_EXPIRE:
                glyph = "arrow"
                adeg = a.arrow_deg
                cone = a.cone_deg
                style = a.arrow_style
                sub = a.sub
                word = a.word
                top = a.top_text
                if a.sweep is not None:
                    sw = a.sweep
                    sweep = (sw[0], _NONE12 if sw[1] is None else sw[1], sw[2], sw[3])
            elif trend:
                glyph = "chevrons"
            if sub != "turn":
                if self._peer_sweep:
                    if top is None:
                        top = T_FRIEND_SCANNING
                    if word is None:
                        word = W_HOLD_STILL
                if self.bump_ready:
                    if word is None:
                        word = W_BUMP
                    if top is None:
                        top = T_TAP_WATCHES
                if top is None and self._hint is not None and ticks_diff(self._hint_until, t_ms) > 0:
                    top = self._hint
        elif m == M_SCANNING:
            sc = self.scan
            sub = sc.sub
            zone = px.zone
            z = zone if zone is not None else FAR
            period = T.ZONE_PERIOD_MS[z]
            mir = sc.mirror
            if sub == "ready":
                speed = T.ZONE_SPEED_PX_S[z]
                glow = T.FIELD_SCAN_READY_GLOW_R
                glyph = "countdown"
                cd = sc.countdown
                top = sc.top_text
                tl = self.me.tilt_deg
                if top == HINT_FLAT and tl is not None and tl <= T.SCAN_FLAT_DEG:
                    top = HINT_CHEST     # flat, only the 0.5 s hold is pending
                word = sc.word
            else:
                if mir is not None:
                    inten = mir
                glow = T.FIELD_SCAN_SWEEP_GLOW_R
                sw = sc.sweep(t_ms)
                if sw is not None:
                    sweep = (sw[0], _bins(sw[1]), sw[2], sw[3])
                    glyph = "turn"
        elif m == M_FOUND:
            sub = "celebrate" if ticks_diff(t_ms, self.found_t) < T.FOUND_CELEBRATE_MS \
                else "result"
            zone = HOT
            ramp = "gold"
            inten = 1.0
            period = 1200
            glow = T.GLOW_R_FOUND
            glyph = "check"
            top = self._time_text
            word = W_FOUND if sub == "celebrate" else W_AGAIN
        elif m == M_LINK_LOST:
            zone = self._lost_zone
            ramp = _FL[0]
            inten = self._lost_i
            speed = _FL[2]
            period = _FL[3]
            glow = _FL[4]
            glyph = "seeker"
            live = False
            band = self._lost_band
            stale = band is not None
            banner = self._lost_banner(t_ms)
        # modifiers: low-battery interstitial, goodbye word, toasts
        iu = self._inter_until
        if iu is not None and ticks_diff(iu, t_ms) > 0 and m != M_SCANNING:
            glyph = "battery"
            adeg = cone = style = None
            if sub in ("reveal", "turn", "walk"):
                sub = None
            sweep = None
            cd = None
            word = W_SAVER
        if self._bye_t is not None:
            word = W_BYE
            hb = None
        if banner is None and self._toast is not None and ticks_diff(self._toast_until, t_ms) > 0:
            banner = (self._toast, self._toast_sev, False)
        haptic = self._hap
        if self.buzz != BUZZ_FULL:
            hb = None
        if self.buzz == BUZZ_OFF:
            haptic = None
        if hb is None:
            every = 1
        status = self._status(t_ms, scr)
        if self.menu_open:
            scr = "MENU"
            sub = self._menu_sub()
            glyph = "glow"
            adeg = cone = style = None
            trend = 0
            strong = False
            cd = None
            band = None
            stale = False
            word = top = banner = sweep = None
            haptic = hb = None
            every = 1
        if inten < 0.0:
            inten = 0.0
        elif inten > 1.0:
            inten = 1.0
        return RenderParams(
            t_ms=t_ms, screen=scr, sub=sub, zone=zone,
            ramp=ramp, intensity=inten, speed_px_s=speed, pulse_period_ms=period,
            wavelength_px=abs(speed) * period / 1000.0, glow_r_px=glow,
            ring_live=live, burst=self._burst,
            glyph=glyph, arrow_deg=adeg, cone_deg=cone, arrow_style=style,
            trend=trend, trend_strong=strong, countdown=cd,
            dist_band=band, dist_stale=stale, word=word, top_text=top, banner=banner,
            status=status, sweep=sweep,
            haptic=haptic, heartbeat=hb, heartbeat_every=every,
            backlight=self._backlight(t_ms),
            fps_cap=T.SAVER_FPS if self.saver else T.FPS_TARGET)
