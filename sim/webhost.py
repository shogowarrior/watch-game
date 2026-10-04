"""Browser simulator host: two watches on one simulated world, stepped by the page.

The web page loads MicroPython WebAssembly, copies finder/, ui/ and sim/ into
its filesystem and drives one ``TwoWatchSim`` from requestAnimationFrame:

    from sim.webhost import TwoWatchSim
    s = TwoWatchSim(seed=1, profile="typical")
    s.step(16)                      # every animation frame (ms of sim time)
    addr = s.frame_addr(0)          # 240x240 byte-swapped RGB565, read from wasm memory
    js = s.telemetry_json()         # both watches + walker poses, one call per frame

Same wiring as tests/test_episode.py: sim.World + sim.radio (per-packet
RSSI) + sim.imu (MotionInfo), the games joined by ``sim.link.GameLink`` (each
watch beacons at its own ``Game.beacon_hz``), one ``finder.game.Game`` and one
``ui.renderer.Renderer`` + ``FrameCapture`` per watch. Physics runs in <= 50 ms
substeps, the logic at 10 Hz on sim-clock multiples of 100 ms, and each watch
renders at its ``fps_cap`` (at most once per ``step`` call, at the end; while its
screen is off the renderer runs without drawing, as app/runtime does, ui-spec §8).
Haptics go through one ``HapticPlayer`` per watch, so the read-out is what the
motor would play (§7). The frame buffers are allocated once and never replaced
(``reset`` keeps them), so a cached ``frame_addr`` stays valid.

Coordinates: metres, world x = east, world y = north (the page draws north
up, so screen y = -world y). Page headings are degrees clockwise from north
(0 = up, 90 = east); sim/world headings are radians counter-clockwise from +x:

    world_rad = radians(90 - page_deg)      page_deg = (90 - degrees(world_rad)) mod 360

Start: A at (0, 0) facing east (+x), B at (35, 10) facing A.

Demo helpers (page settable attributes):
  * ``auto_pair`` (default True): the pairing handshake is played as if the
    two watches were held together -- while both games are in the handshake
    (looking..calibrate), or one is in its split while the other finishes
    seen..calibrate (a watch that ends the round during the partner's split
    pairs again only after the partner has left too), the radio sees a
    proxy world with the watches 1 m apart, face to face, with the
    devices' offsets and low noise (at 1 m face to face the profile's path
    loss is 0 dB; no shadowing, 1 dB fading, no outliers: still hands), so
    both calibrations finish together -- and each watch presses its button
    0.8 s after ``seen`` (auto-confirm; it waits while that watch's MENU is
    open). When the proxy ends, both watches forget their last RSSI (so no
    beacon reports a 1 m ``rssi_last``) and a watch already in its split
    restarts its estimate, so no 1 m packet leaks into the first zone. The
    30 s split countdown is cut to 5 s (``Pairing.split_s``), for every split
    including new rounds. With ``auto_pair`` off the watches must really be
    brought within ~5 m and confirmed with ``tap``/``button``.
  * ``auto_turn`` (default True): an idle walker (no walk_to in progress)
    turns clockwise at the scan's sweep rate during its SCANNING sweep (held
    while the sweep is paused) and follows the DIRECTION turn pacer (turns to
    the scan heading + pacer angle), so the player "follows" the prompt.

The truth overlay (ui-spec §3) counts, per watch, the logic ticks an arrow is
shown and how many of them have the true bearing inside the cone
(``telemetry()["cone_hit_pct"]``, target about 80 %).

The watch is held flat and face-up (tilt 5 deg) unless ``set_posture`` says
otherwise; battery reads 90 %.

Real watches (docs/design/debug-mode.md): ``real_mode(True)`` stops the
simulated world (no physics, logic or haptics) and blanks both screens;
``show_params(i, json_text)`` then hands watch i the RenderParams of the latest
``rp`` record from that real watch, and ``step`` only advances a render clock,
so the renderer keeps animating the last params between records.
``real_mode(False)`` blanks the screens again and resumes the world where it
stopped. Each switch resets the renderers (their state belongs to the other
clock).
"""

import json
import math

from finder import tuning as T
from finder import pairing as P
from finder import scan as S
from finder.compat import ticks_diff
from finder.haptic_patterns import TOTAL_MS, HapticPlayer
from finder.game import Game, M_PAIRING, M_HUNT, M_SCANNING
from finder.gestures import TAP as G_TAP, LONG_PRESS as G_LONG_PRESS, SWIPE_U as G_SWIPE_U, \
    SWIPE_D as G_SWIPE_D, SWIPE_L as G_SWIPE_L, SWIPE_R as G_SWIPE_R
from finder.arrow import PH_TURN, wrap180, wrap360
from finder.render_params import from_dict, validate
from sim import Sim
from sim.link import GameLink
from sim.world import World, Walker, WalkTo, PI, wrap
from sim.radio import profile as _profile, PROFILE_NAMES, INDOOR_PROFILES
from sim.rng import Rng

try:
    from ui.renderer import Renderer, FrameCapture
except ImportError:          # CPython: framebuf is MicroPython-only, logic still runs
    Renderer = None
    FrameCapture = None
try:
    import uctypes
except ImportError:
    uctypes = None

MACS = (b"\x24\x0a\xc4\x10\x00\x0a", b"\x24\x0a\xc4\x10\x00\x0b")
START_A = (0.0, 0.0)
START_B = (35.0, 10.0)
FRAME_BYTES = 240 * 240 * 2
PHYS_MS = 50               # max physics substep
TICK_MS = T.LOGIC_MS       # logic rate (10 Hz)
MAX_STEP_MS = 2000         # one step() never advances more than this
_RAD = PI / 180.0
_DEG = 180.0 / PI
WALK_MPS = 1.3
TURN_RAD_S = S.DEG_PER_S * _RAD     # auto-turn = the rate the sweep wedge assumes
AUTO_SPLIT_S = 5
AUTO_CONFIRM_MS = 800
TILT_FLAT = 5.0
TILT_TILTED = 50.0         # > scan.TILT_FAULT_DEG and > motion's 30 deg face-up limit: not face-up,
                           # the sweep pauses (page label 'tilted 50°')
BATTERY = 90
HAPTIC_KEEP = 16           # per-watch haptic names kept between reads
_PAIR_PROXY = (P.LOOKING, P.SEEN, P.CONFIRMED, P.CALIBRATE)
_PAIR_HELD = (P.SEEN, P.CONFIRMED, P.CALIBRATE)
# two still watches held 1 m apart, face to face: at 1 m path loss and body loss are 0 dB
# for every profile, so one low-noise channel serves them all
_CALM = dict(_profile("clean"), sigma_ff=1.0, sigma_t=0.3, out_p=0.0, loss=0.02, extra_loss=0.0)


def heading_to_world(deg):
    """Page heading (deg clockwise from north) -> world heading (rad CCW from +x)."""
    return wrap((90.0 - deg) * _RAD)


def heading_to_page(rad):
    """World heading (rad CCW from +x) -> page heading (deg clockwise from north), [0, 360)."""
    return wrap360(90.0 - rad * _DEG)


def _r(v, n=1):
    return None if v is None else round(v, n)


class TwoWatchSim:
    """Two ``Game`` + ``Renderer`` pairs on one sim world, stepped on a sim clock."""

    def __init__(self, seed=1, profile="typical", imu="typical"):
        if profile not in PROFILE_NAMES:
            raise ValueError("profile: one of %s" % ",".join(PROFILE_NAMES))
        self.seed = seed
        self.profile = profile
        self.imu = imu
        self.auto_turn = True
        self.auto_pair = True
        if Renderer is not None:
            self.caps = (FrameCapture(), FrameCapture())
            self.renderers = (Renderer(), Renderer())
            self.bufs = (self.caps[0].buf, self.caps[1].buf)
        else:
            self.caps = self.renderers = None
            self.bufs = (bytearray(FRAME_BYTES), bytearray(FRAME_BYTES))
        self._addr = (uctypes.addressof(self.bufs[0]), uctypes.addressof(self.bufs[1])) \
            if uctypes is not None else (None, None)
        self._proxy = World(Walker(0.0, 0.0, 0.0, name="A"), Walker(1.0, 0.0, PI, name="B"))
        self.real = False                # real_mode: screens from show_params, world stopped
        self.real_ms = 0                 # render clock while real
        self._shown = [None, None]       # RenderParams from show_params, per watch
        self.reset(seed)

    # ---- lifecycle ---------------------------------------------------------------
    def reset(self, seed=None):
        """New world, radio and games at t = 0 (frame buffers are kept)."""
        if seed is not None:
            self.seed = seed
        a = Walker(START_A[0], START_A[1], 0.0, WALK_MPS, "A")
        b = Walker(START_B[0], START_B[1],
                   math.atan2(START_A[1] - START_B[1], START_A[0] - START_B[0]), WALK_MPS, "B")
        self.world = World(a, b)
        self.sim = Sim(self.world, self.profile, self.seed, self.imu)
        self.games = (Game(MACS[0]), Game(MACS[1]))
        self.link = GameLink(self.games, MACS)
        self.tilt = [TILT_FLAT, TILT_FLAT]      # accelerometer tilt from flat, per watch
        self.face_up = [True, True]
        for g in self.games:
            g.set_place(self.profile in INDOOR_PROFILES)
        self.t_ms = 0
        self._prof_n = 0
        self._was_proxy = False
        self._walls_json = None
        self._next_due = [0, 0]
        self._params = [None, None]
        self._hap = [[], []]
        self.players = (HapticPlayer(), HapticPlayer())   # §7 rules, as app/runtime
        self._pk = [0, 0]
        self.pkts_per_s = [0, 0]
        self._turn_arrow = [None, None]
        self._turn_ref = [0.0, 0.0]
        self._in_cone = [None, None]
        self._cone_ms = [0, 0]           # logic time an arrow was shown
        self._cone_in_ms = [0, 0]        # ... with the true bearing inside its cone
        self.frames = [0, 0]
        self._blank()
        self._tick()

    def _blank(self):
        """Fresh renderers and black frames (in place: the buffers and their addresses stay)."""
        if self.renderers is not None:
            for r in self.renderers:
                r.reset()
        for buf in self.bufs:
            for k in range(0, FRAME_BYTES, 4800):
                buf[k:k + 4800] = bytes(4800)

    def set_profile(self, name):
        """Switch the radio channel (clean/typical/harsh/indoor); indoor adds walls.

        PLACE ownership: picking a radio profile (here or at ``reset``) sets MENU PLACE on
        both watches to what players would choose there (IN for harsh/indoor, OUT
        otherwise). A PLACE picked later in a watch menu holds until the next profile
        change or reset: the last writer wins. ``telemetry()["place"]`` shows the current
        value per watch."""
        if name not in PROFILE_NAMES:
            raise ValueError("profile: one of %s" % ",".join(PROFILE_NAMES))
        self.profile = name
        self._prof_n += 1
        for g in self.games:
            g.set_place(name in INDOOR_PROFILES)   # overrides a menu PLACE (see docstring)
        self.world.obstacles = []
        self.sim.set_profile(name, Rng(self.seed).fork(100 + self._prof_n))
        self._walls_json = None

    # ---- clock -------------------------------------------------------------------
    def step(self, dt_ms):
        """Advance ``dt_ms`` of sim time (physics <= 50 ms, logic 10 Hz, render at fps_cap)."""
        dt = int(dt_ms)
        if dt <= 0:
            return
        if dt > MAX_STEP_MS:
            dt = MAX_STEP_MS
        if self.real:
            self.real_ms += dt
            self._render_due()
            return
        while dt > 0:
            h = TICK_MS - self.t_ms % TICK_MS
            if h > PHYS_MS:
                h = PHYS_MS
            if h > dt:
                h = dt
            self._physics(h)
            dt -= h
            if self.t_ms % TICK_MS == 0:
                self._tick()
        self._render_due()

    def _pair_proxy(self):
        """1 m stand-in channel: both watches in the handshake (looking..calibrate), or one
        already in its split while the other finishes this handshake (seen..calibrate). A
        watch that ends the round during the partner's split is LOOKING: the split keeps
        the real channel, and the partner leaves through FRIEND LEFT as on real watches."""
        g0, g1 = self.games
        if not (self.auto_pair and g0.mode == M_PAIRING and g1.mode == M_PAIRING):
            return False
        s0 = g0.pair.sub
        s1 = g1.pair.sub
        if s0 in _PAIR_PROXY and s1 in _PAIR_PROXY:
            return True
        return (s0 == P.SPLIT and s1 in _PAIR_HELD) or (s1 == P.SPLIT and s0 in _PAIR_HELD)

    def _physics(self, h):
        s = h / 1000.0
        self._drive(s)
        sim = self.sim
        link = self.link
        link.set_rates(sim.radio)
        t1 = self.t_ms + h
        proxy = self._pair_proxy()
        if proxy:
            self._proxy.t = t1 / 1000.0
            pks = sim.step_with(s, self._proxy, _CALM)
        else:
            pks = sim.step(s)
            if self._was_proxy:
                self._proxy_done()
        self._was_proxy = proxy
        self.t_ms = t1
        self.world.t = t1 / 1000.0          # no float drift on the sim clock
        for pl in self.players:
            pl.tick(t1)
        link.deliver(pks)
        self._pk[0] += len(pks[0])
        self._pk[1] += len(pks[1])

    def _proxy_done(self):
        """Proxy just ended: a split that heard 1 m packets starts its estimate afresh, and
        no beacon reports a 1 m ``rssi_last`` (both games forget their last RSSI)."""
        for g in self.games:
            g.rssi_last = None
            if g.mode == M_PAIRING and g.pair.sub == P.SPLIT:
                g.restart_estimate()

    def _drive(self, s):
        """auto_turn: sweep clockwise at the scan rate; follow the DIRECTION turn pacer."""
        if not self.auto_turn:
            return
        for i in (0, 1):
            g = self.games[i]
            w = self._walker(i)
            if w.plan:
                continue
            if g.mode == M_SCANNING:
                sc = g.scan
                if sc.sub == "sweep" and not sc.paused and not g.menu_open:
                    w.heading = wrap(w.heading - TURN_RAD_S * s)    # clockwise
                continue
            a = g.arrow
            if g.mode == M_HUNT and a is not None and a.phase == PH_TURN:
                if self._turn_arrow[i] is not a:
                    self._turn_arrow[i] = a
                    self._turn_ref[i] = w.heading
                w.face(self._turn_ref[i] - a.pacer * _RAD, s)   # pacer: deg clockwise

    def _tick(self):
        t = self.t_ms
        for i in (0, 1):
            g = self.games[i]
            info = self.sim.motion(i)
            g.set_motion(t, info.activity, info.steps, info.step_rate_hz, self.tilt[i], self.face_up[i])
            g.set_battery(t, BATTERY)
            g.pair.split_s = AUTO_SPLIT_S if self.auto_pair else T.PAIR_SPLIT_S
            if self.auto_pair:
                self._auto_pair(g, t)
            p = g.tick(t)
            self._score_cone(i, p)
            if p.haptic and self.players[i].play_named(p.haptic, t):
                self._note(i, p.haptic)            # events play at the tick (heartbeats: frames)
            self._params[i] = p
        if t % 1000 == 0:
            self.pkts_per_s[0] = self._pk[0]
            self.pkts_per_s[1] = self._pk[1]
            self._pk[0] = self._pk[1] = 0

    def _score_cone(self, i, p):
        """Truth overlay: is the true bearing inside the shown cone (time-weighted)."""
        if p.arrow_deg is None or p.cone_deg is None:
            self._in_cone[i] = None
            return
        hit = abs(wrap180(self.true_bearing_rel_deg(i) - p.arrow_deg)) <= p.cone_deg
        self._in_cone[i] = hit
        self._cone_ms[i] += TICK_MS
        if hit:
            self._cone_in_ms[i] += TICK_MS

    def _auto_pair(self, g, t):
        pr = g.pair
        # an open MENU takes the short press (it moves the cursor): wait for it to close
        if (g.mode == M_PAIRING and pr.sub == P.SEEN and not pr.confirmed and not g.menu_open
                and ticks_diff(t, pr.t_sub) >= AUTO_CONFIRM_MS):
            g.on_button(t)

    def _render_due(self):
        rs = self.renderers
        if rs is None:
            return
        real = self.real
        t = self.real_ms if real else self.t_ms
        for i in (0, 1):
            nd = self._next_due[i]
            if t < nd:
                continue
            if real:
                p = self._shown[i]
                if p is None:                   # nothing heard from that watch yet
                    continue
                d = self.caps[i]                # the page dims it by the record's backlight
            else:
                p = self._params[i]
                d = self.caps[i] if self.games[i].screen_on else None   # §8: dark, no drawing
            beats = rs[i].frame(p, d, t)
            if not real:                        # a real watch's motor plays its own heartbeats
                pl = self.players[i]
                for e in beats:                 # heartbeats on live ring spawns
                    if pl.heartbeat(e, t):
                        self._note(i, e)
            if d is not None:
                self.frames[i] += 1
            per = 1000 // (p.fps_cap or T.FPS_TARGET)
            nd += per
            if nd <= t:
                nd = t + per
            self._next_due[i] = nd

    def _note(self, i, name):
        h = self._hap[i]
        if len(h) < HAPTIC_KEEP:
            h.append(name)

    # ---- controls ----------------------------------------------------------------
    def _walker(self, i):
        return self.world.a if i == 0 else self.world.b

    def set_pose(self, i, x, y, heading_deg=None):
        """Place walker i (drag); heading in page degrees (clockwise from north)."""
        h = None if heading_deg is None else heading_to_world(float(heading_deg))
        self._walker(i).set_pose(float(x), float(y), h)

    def walk_to(self, i, x, y, speed=None):
        """Walker i walks to (x, y) at ``speed`` m/s (default 1.3; replaces any walk in progress)."""
        w = self._walker(i)
        w.plan = []
        w.walk_to((float(x), float(y)), WALK_MPS if speed is None else float(speed))

    def set_posture(self, i, posture):
        """What the accelerometer sees: 'flat' (face up, 5 deg), 'tilted' (50 deg: past the
        30 deg face-up limit of finder.motion and the scan's 35 deg tilt fault, so a sweep
        pauses and ``ready`` cancels after 2 s, but short of the 60 deg wrist-down tilt, so
        the screen stays on, as on the watch) or 'down' (wrist lowered: the screen turns
        off after 10 s; the simulated watches run on battery)."""
        if posture == "flat":
            self.tilt[i], self.face_up[i] = TILT_FLAT, True
        elif posture == "tilted":
            self.tilt[i], self.face_up[i] = TILT_TILTED, False
        elif posture == "down":
            self.tilt[i], self.face_up[i] = 100.0, False
        else:
            raise ValueError("posture: flat, tilted or down")

    def tap(self, i, x=120, y=120):
        """Screen tap at (x, y); a tap on a dark screen wakes it (wrist raise)."""
        g = self.games[i]
        if not g.screen_on:
            g.on_wake(self.t_ms)
            return
        self._gesture(i, G_TAP, int(x), int(y))

    def long_press(self, i):
        self._gesture(i, G_LONG_PRESS)

    def swipe(self, i, up=True):
        """Vertical swipe on the screen (MENU: up shows the rows below, down the rows above)."""
        self._gesture(i, G_SWIPE_U if up else G_SWIPE_D)

    def swipe_h(self, i, left=True):
        """Sideways swipe (PAIRING looking: left = next how-to card, right = previous)."""
        self._gesture(i, G_SWIPE_L if left else G_SWIPE_R)

    def _gesture(self, i, code, x=120, y=120):
        """A finger lands (``Game.on_touch_down``: rain/sleeve burst filter),
        then its gesture ends, as on the watch."""
        g = self.games[i]
        g.on_touch_down(self.t_ms)
        g.on_gesture(self.t_ms, code, x, y)

    def button(self, i, long=False):
        self.games[i].on_button(self.t_ms, bool(long))

    def bump(self):
        """Both accelerometers tap at the same instant (watches knocked together)."""
        t = self.t_ms
        self.games[0].on_accel_tap(t)
        self.games[1].on_accel_tap(t)

    # ---- real watches ------------------------------------------------------------
    def real_mode(self, on):
        """On: stop the simulated world and draw only what ``show_params`` hands in (both
        screens black until then). Off: forget those params and resume the world."""
        on = bool(on)
        if on == self.real:
            return
        self.real = on
        self.real_ms = 0
        self._shown = [None, None]
        self._next_due = [0, 0]
        self._blank()

    def show_params(self, i, json_text):
        """Draw ``json_text`` (``finder.render_params.to_dict`` as JSON: an ``rp`` record's
        ``p``) on watch i's screen from the next frame on, until the next call. Real mode
        only. Raises ValueError for text that is not valid RenderParams (ui-spec §3), so a
        bad record never reaches the renderer."""
        if not self.real:
            raise ValueError("show_params: real_mode(True) first")
        try:
            d = json.loads(json_text)
            if not isinstance(d, dict):
                raise ValueError("not a JSON object")
            p = from_dict(d, False)             # fields a newer watch adds are ignored
            bad = validate(p)
        except (ValueError, TypeError) as e:    # TypeError: a list where a number goes
            raise ValueError("params: %s" % e)
        if bad:
            raise ValueError("params: %s" % bad[0])
        self._shown[i] = p

    # ---- read-outs ---------------------------------------------------------------
    def frame_addr(self, i):
        """Address of watch i's 115,200-byte frame in wasm memory (None on CPython)."""
        return self._addr[i]

    def frame_bytes(self, i):
        """Watch i's frame buffer itself (live, not a copy): 240x240 swapped RGB565."""
        return self.bufs[i]

    def true_bearing_rel_deg(self, i):
        """Bearing to the partner relative to i's heading, clockwise, (-180, 180]."""
        return wrap180(-self.world.rel_bearing(i) * _DEG)

    def telemetry(self, i):
        """Small dict for the page; ``haptic`` lists the patterns the motor starts (after
        HapticPlayer's §7 rules) since the last read.
        ``in_cone`` and ``cone_hit_pct`` score the last logic tick's arrow against the truth."""
        g = self.games[i]
        p = self._params[i]
        hap = self._hap[i]
        if hap:
            self._hap[i] = []
        est = g.est
        cms = self._cone_ms[i]
        return {
            "screen": p.screen, "sub": p.sub, "zone": p.zone, "band": p.dist_band,
            "true_dist_m": _r(self.world.distance(), 2),
            "est_dist_m": _r(est.dist_m), "rssi_last": g.rssi_last,
            "rssi_f": _r(est.rssi_f), "trend": p.trend,
            "arrow_deg": _r(p.arrow_deg), "cone_deg": _r(p.cone_deg),
            "true_bearing_rel_deg": _r(self.true_bearing_rel_deg(i)), "in_cone": self._in_cone[i],
            "cone_ms": cms, "cone_hit_pct": round(100.0 * self._cone_in_ms[i] / cms) if cms else None,
            "pkts_per_s": self.pkts_per_s[i], "haptic": hap,
            "steps": self.sim.imus[i].steps, "backlight": p.backlight,
            "place": "IN" if g.indoor else "OUT",
        }

    def constants_json(self):
        """Tuning values the page explains (zone edges and dwell, band edges, link loss,
        menu close, pairing split, haptic pattern lengths), so its text follows
        finder/tuning.py."""
        return json.dumps({
            "enter_m": list(T.ZONE_ENTER_M), "exit_m": list(T.ZONE_EXIT_M),
            "band_edges_m": list(T.BAND_EDGES_M),
            "dwell_ms": list(T.ZONE_DWELL_MS), "lost_ms": T.LINK_LOST_AFTER_MS,
            "menu_close_ms": T.MENU_AUTOCLOSE_MS, "split_s": T.PAIR_SPLIT_S,
            "haptic_ms": TOTAL_MS,
        })

    def _walls(self):
        s = self._walls_json
        if s is None:
            s = json.dumps([[round(o.x0, 2), round(o.y0, 2), round(o.x1, 2), round(o.y1, 2),
                             o.db] for o in self.world.obstacles])
            self._walls_json = s
        return s

    def _walker_dict(self, i):
        w = self._walker(i)
        tgt = None
        if w.plan and isinstance(w.plan[0], WalkTo):
            tgt = list(w.plan[0].target)        # walk_to targets are (x, y) points
        return {"x": round(w.x, 3), "y": round(w.y, 3),
                "heading_deg": round(heading_to_page(w.heading), 2),
                "moving": bool(w.plan), "target": tgt}

    def world_state(self):
        """Walker poses (page headings), walls [x0, y0, x1, y1, dB], profile, t_ms."""
        return {"t_ms": self.t_ms, "walkers": [self._walker_dict(0), self._walker_dict(1)],
                "walls": json.loads(self._walls()), "profile": self.profile}

    def telemetry_json(self):
        """One JSON string: {t_ms, watches: [A, B], world: {walkers, walls, profile}}."""
        return '{"t_ms":%d,"watches":%s,"world":{"walkers":%s,"walls":%s,"profile":"%s"}}' % (
            self.t_ms, json.dumps([self.telemetry(0), self.telemetry(1)]),
            json.dumps([self._walker_dict(0), self._walker_dict(1)]), self._walls(),
            self.profile)
