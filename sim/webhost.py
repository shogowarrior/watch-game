"""Browser simulator host: two watches on one simulated world, stepped by the page.

The web page loads MicroPython WebAssembly, copies finder/, ui/ and sim/ into
its filesystem and drives one ``TwoWatchSim`` from requestAnimationFrame:

    from sim.webhost import TwoWatchSim
    s = TwoWatchSim(seed=1, profile="typical")
    s.step(16)                      # every animation frame (ms of sim time)
    addr = s.frame_addr(0)          # 240x240 byte-swapped RGB565, read from wasm memory
    js = s.telemetry_json()         # both watches + walker poses, one call per frame

Wiring is that of tests/test_episode.py: sim.World + sim.radio (per-packet
RSSI, beacon rate from ``Game.beacon_hz``) + sim.imu (MotionInfo), beacons
built by ``Game.fill_beacon`` and packed/unpacked with finder.proto, one
``finder.game.Game`` and one ``ui.renderer.Renderer`` + ``FrameCapture`` per
watch. Physics runs in <= 50 ms substeps, the logic at 10 Hz on sim-clock
multiples of 100 ms, and each watch renders at its ``fps_cap`` (at most once
per ``step`` call, at the end). The frame buffers are allocated once and never
replaced (``reset`` keeps them), so a cached ``frame_addr`` stays valid.

Coordinates: metres, world x = east, world y = north (the page draws north
up, so screen y = -world y). Page headings are degrees clockwise from north
(0 = up, 90 = east); sim/world headings are radians counter-clockwise from +x:

    world_rad = radians(90 - page_deg)      page_deg = (90 - degrees(world_rad)) mod 360

Start: A at (0, 0) facing east (+x), B at (35, 10) facing A.

Demo helpers (page settable attributes):
  * ``auto_pair`` (default True): the pairing handshake is played as if the
    two watches were held together -- while both games are in PAIRING and
    either is still looking/seen/confirmed/calibrating, the radio sees a
    proxy world with the watches 1 m apart, face to face, with the
    profile's path loss and device offsets but low noise (no shadowing,
    1 dB fading, no outliers: still hands), so both calibrations finish
    together -- and each watch presses its button 0.8 s after ``seen``
    (auto-confirm). When the proxy ends, a watch already in its split
    restarts its estimate so no 1 m packet leaks into the first zone. The 30 s split countdown is cut to 5 s (shifting the
    split start back 25 s), for every split including new rounds. With
    ``auto_pair`` off the watches must really be brought within ~5 m and
    confirmed with ``tap``/``button``.
  * ``auto_turn`` (default True): an idle walker (no walk_to in progress)
    turns clockwise at 30 deg/s during its SCANNING sweep (held while the
    sweep is paused) and follows the DIRECTION turn pacer (turns to the scan
    heading + pacer angle), so the player "follows" the prompt.

The watch is held flat and face-up (tilt 5 deg) unless ``set_posture`` says otherwise; battery reads 90 %.
"""

import json
import math

from finder import proto
from finder import tuning as T
from finder import pairing as P
from finder.compat import ticks_add
from finder.game import Game, M_PAIRING, M_HUNT, M_SCANNING, G_TAP, G_LONG_PRESS, G_SWIPE_U, G_SWIPE_D
from finder.arrow import PH_TURN
from finder.render_params import replace as _replace
from sim import Sim
from sim.world import World, Walker, WalkTo, PI, wrap
from sim.radio import Radio, add_walls, profile as _profile, PROFILE_NAMES
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

INDOOR_PROFILES = ("harsh", "indoor")   # radio profiles where the watches use the indoor exponent

MACS = (b"\x24\x0a\xc4\x10\x00\x0a", b"\x24\x0a\xc4\x10\x00\x0b")
START_A = (0.0, 0.0)
START_B = (35.0, 10.0)
FRAME_BYTES = 240 * 240 * 2
PHYS_MS = 50               # max physics substep
TICK_MS = 100              # logic rate (10 Hz)
MAX_STEP_MS = 2000         # one step() never advances more than this
WALK_MPS = 1.3
TURN_RAD_S = 30.0 * PI / 180.0
AUTO_SPLIT_S = 5
AUTO_CONFIRM_MS = 800
TILT_FLAT = 5.0
BATTERY = 90
HAPTIC_KEEP = 16           # per-watch haptic names kept between reads
_RAD = PI / 180.0
_DEG = 180.0 / PI
_PAIR_PROXY = (P.LOOKING, P.SEEN, P.CONFIRMED, P.CALIBRATE)


def heading_to_world(deg):
    """Page heading (deg clockwise from north) -> world heading (rad CCW from +x)."""
    return wrap((90.0 - deg) * _RAD)


def heading_to_page(rad):
    """World heading (rad CCW from +x) -> page heading (deg clockwise from north), [0, 360)."""
    d = (90.0 - rad * _DEG) % 360.0
    return 0.0 if d >= 360.0 else d


def _calm(name):
    """Channel of two still watches held together: the profile, low noise, no outliers."""
    p = dict(_profile(name))
    p.update(sigma_ff=1.0, sigma_t=0.3, out_p=0.0, loss=0.02, extra_loss=0.0)
    return p


def _cdiff(a, b):
    d = (a - b) % 360.0
    return d - 360.0 if d > 180.0 else d


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
        self.tx = (proto.Beacon(1), proto.Beacon(1))
        self.rxb = proto.Beacon(1)
        self.pbuf = bytearray(proto.SIZE)
        self._proxy = World(Walker(0.0, 0.0, 0.0, name="A"), Walker(1.0, 0.0, PI, name="B"))
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
        self.tilt = [TILT_FLAT, TILT_FLAT]      # accelerometer tilt from flat, per watch
        self.face_up = [True, True]
        for g in self.games:
            g.set_place(self.profile in INDOOR_PROFILES)
        self.t_ms = 0
        self._prof_n = 0
        self._calm = _calm(self.profile)
        self._was_proxy = False
        self._walls_json = None
        self._next_due = [0, 0]
        self._params = [None, None]
        self._framed = [True, True]
        self._hap = [[], []]
        self._pk = [0, 0]
        self.pkts_per_s = [0, 0]
        self._split_t = [None, None]
        self._turn_arrow = [None, None]
        self._turn_ref = [0.0, 0.0]
        self.frames = [0, 0]
        if self.renderers is not None:
            for r in self.renderers:
                r.reset()
        for buf in self.bufs:
            for k in range(0, FRAME_BYTES, 4800):   # black, in place
                buf[k:k + 4800] = bytes(4800)
        self._tick()

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
        p = _profile(name)
        w = self.world
        s = self.sim
        rng = Rng(self.seed).fork(100 + self._prof_n)
        w.obstacles = []
        if p["walls"]:
            add_walls(w, rng.fork(13))
        r = Radio(w, p, rng.fork(14), s.imus)
        t = self.t_ms
        r.next_tx[0] += t          # the scheduler starts at t = 0: move it to now
        r.next_tx[1] += t
        r._t_ms = t
        r.period_ms = s.radio.period_ms
        s.prof = p
        s.radio = r
        self._calm = _calm(name)
        self._walls_json = None

    # ---- clock -------------------------------------------------------------------
    def step(self, dt_ms):
        """Advance ``dt_ms`` of sim time (physics <= 50 ms, logic 10 Hz, render at fps_cap)."""
        dt = int(dt_ms)
        if dt <= 0:
            return
        if dt > MAX_STEP_MS:
            dt = MAX_STEP_MS
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
        g0, g1 = self.games
        return (self.auto_pair and g0.mode == M_PAIRING and g1.mode == M_PAIRING
                and (g0.pair.sub in _PAIR_PROXY or g1.pair.sub in _PAIR_PROXY))

    def _physics(self, h):
        s = h / 1000.0
        self._drive(s)
        g0, g1 = self.games
        sim = self.sim
        r = sim.radio
        hz = g0.beacon_hz
        if g1.beacon_hz > hz:
            hz = g1.beacon_hz
        r.period_ms = 1000 // hz
        t1 = self.t_ms + h
        proxy = self._pair_proxy()
        if proxy:
            px = self._proxy
            px.t = t1 / 1000.0
            p = r.p
            sh = r.sh
            tv = r.tv
            r.world = px
            r.p = self._calm
            r.sh = r.tv = 0.0
            pa, pb = sim.step(s)
            r.world = self.world
            r.p = p
            r.sh = sh
            r.tv = tv
        else:
            pa, pb = sim.step(s)
            if self._was_proxy:
                self._proxy_done()
        self._was_proxy = proxy
        self.t_ms = t1
        self.world.t = t1 / 1000.0          # no float drift on the sim clock
        for pk in pa:
            self._deliver(0, pk)
        for pk in pb:
            self._deliver(1, pk)

    def _proxy_done(self):
        """Proxy just ended: a split that heard 1 m packets starts its estimate afresh."""
        for g in self.games:
            if g.mode == M_PAIRING and g.pair.sub == P.SPLIT:
                g.est.reset()
                g.px.reset()

    def _deliver(self, rx, pk):
        tx = 1 - rx
        b = self.tx[tx]
        b.next_seq()
        self.games[tx].fill_beacon(b, pk.t_ms)
        b.pack_into(self.pbuf)
        self.rxb.unpack_from(self.pbuf)
        self.games[rx].on_packet(pk.t_ms, MACS[tx], pk.rssi, self.rxb)
        self._pk[rx] += 1

    def _drive(self, s):
        """auto_turn: sweep at 30 deg/s clockwise; follow the DIRECTION turn pacer."""
        if not self.auto_turn:
            return
        for i in (0, 1):
            g = self.games[i]
            w = self.world.a if i == 0 else self.world.b
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
            else:
                self._turn_arrow[i] = None

    def _tick(self):
        t = self.t_ms
        rend = self.renderers is not None
        for i in (0, 1):
            g = self.games[i]
            info = self.sim.motion(i)
            g.set_motion(t, info.activity, info.steps, info.step_rate_hz, self.tilt[i], self.face_up[i])
            g.set_battery(t, BATTERY)
            if self.auto_pair:
                self._auto_pair(i, g, t)
            p = g.tick(t)
            if self.auto_pair and self._shorten(i, g) and p.countdown is not None:
                p = _replace(p, countdown=AUTO_SPLIT_S)
            old = self._params[i]
            if rend:
                if old is not None and not self._framed[i] and old.haptic:
                    self._note(i, old.haptic)        # never drawn: keep its event
            elif p.haptic:
                self._note(i, p.haptic)
            self._params[i] = p
            self._framed[i] = False
        if t % 1000 == 0:
            self.pkts_per_s[0] = self._pk[0]
            self.pkts_per_s[1] = self._pk[1]
            self._pk[0] = self._pk[1] = 0

    def _auto_pair(self, i, g, t):
        if g.mode != M_PAIRING:
            return
        pr = g.pair
        if pr.sub == P.SEEN and not pr.confirmed and t - pr.t_sub >= AUTO_CONFIRM_MS:
            g.on_button(t)
        self._shorten(i, g)

    def _shorten(self, i, g):
        """Cut a fresh split countdown to AUTO_SPLIT_S; True if it did now."""
        pr = g.pair
        if g.mode != M_PAIRING or pr.sub != P.SPLIT or pr.t_sub == self._split_t[i]:
            return False
        pr.t_sub = ticks_add(pr.t_sub, -(T.PAIR_SPLIT_S - AUTO_SPLIT_S) * 1000)
        self._split_t[i] = pr.t_sub
        return True

    def _render_due(self):
        rs = self.renderers
        if rs is None:
            return
        t = self.t_ms
        for i in (0, 1):
            nd = self._next_due[i]
            if t < nd:
                continue
            p = self._params[i]
            g = self.games[i]
            r = rs[i]
            if g.runes is not None:
                r.runes = g.runes
            r.sun = g.sun
            r.menu_rows = g.menu_rows
            ev = r.frame(p, self.caps[i], t)
            self._framed[i] = True
            self.frames[i] += 1
            for e in ev:
                self._note(i, e)
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
        """What the accelerometer sees: 'flat' (face up, 5 deg), 'tilted' (50 deg: scans pause
        with HOLD FLAT) or 'down' (wrist lowered: the screen turns off after 2 s)."""
        if posture == "flat":
            self.tilt[i], self.face_up[i] = TILT_FLAT, True
        elif posture == "tilted":
            self.tilt[i], self.face_up[i] = 50.0, True
        elif posture == "down":
            self.tilt[i], self.face_up[i] = 100.0, False
        else:
            raise ValueError("posture: flat, tilted or down")

    def stop(self, i):
        self._walker(i).plan = []

    def tap(self, i, x=120, y=120):
        """Screen tap at (x, y); a tap on a dark screen wakes it (wrist raise)."""
        g = self.games[i]
        if not g.screen_on:
            g.on_wake(self.t_ms)
            return
        g.on_gesture(self.t_ms, G_TAP, int(x), int(y))

    def long_press(self, i):
        self.games[i].on_gesture(self.t_ms, G_LONG_PRESS, 120, 120)

    def swipe(self, i, up=True):
        """Vertical swipe on the screen (MENU: up shows the rows below, down the rows above)."""
        self.games[i].on_gesture(self.t_ms, G_SWIPE_U if up else G_SWIPE_D, 120, 120)

    def button(self, i, long=False):
        self.games[i].on_button(self.t_ms, bool(long))

    def bump(self):
        """Both accelerometers tap at the same instant (watches knocked together)."""
        t = self.t_ms
        self.games[0].on_accel_tap(t)
        self.games[1].on_accel_tap(t)

    # ---- read-outs ---------------------------------------------------------------
    def frame_addr(self, i):
        """Address of watch i's 115,200-byte frame in wasm memory (None on CPython)."""
        return self._addr[i]

    def frame_bytes(self, i):
        """Watch i's frame buffer itself (live, not a copy): 240x240 swapped RGB565."""
        return self.bufs[i]

    def true_bearing_rel_deg(self, i):
        """Bearing to the partner relative to i's heading, clockwise, (-180, 180]."""
        d = -self.world.rel_bearing(i) * _DEG
        return 180.0 if d <= -180.0 else d

    def telemetry(self, i):
        """Small dict for the page; ``haptic`` lists patterns started since the last read."""
        g = self.games[i]
        p = self._params[i]
        rel = self.true_bearing_rel_deg(i)
        ad = p.arrow_deg
        cd = p.cone_deg
        in_cone = None
        if ad is not None and cd is not None:
            in_cone = abs(_cdiff(rel, ad)) <= cd
        hap = self._hap[i]
        if hap:
            self._hap[i] = []
        est = g.est
        return {
            "screen": p.screen, "sub": p.sub, "zone": p.zone, "band": p.dist_band,
            "true_dist_m": _r(self.world.distance(), 2),
            "est_dist_m": _r(est.dist_m), "rssi_last": g.rssi_last,
            "rssi_f": _r(est.rssi_f), "trend": p.trend,
            "arrow_deg": _r(ad), "cone_deg": _r(cd),
            "true_bearing_rel_deg": _r(rel), "in_cone": in_cone,
            "pkts_per_s": self.pkts_per_s[i], "haptic": hap,
            "steps": self.sim.imus[i].steps, "word": p.word, "top_text": p.top_text,
            "backlight": p.backlight, "mode": g.mode,
            "place": "IN" if g.indoor else "OUT",
        }

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
            tp = w.plan[0].target
            tgt = [tp[0], tp[1]] if not hasattr(tp, "x") else [tp.x, tp.y]
        return {"x": round(w.x, 3), "y": round(w.y, 3),
                "heading_deg": round(heading_to_page(w.heading), 2),
                "moving": bool(w.plan), "target": tgt}

    def world_state(self):
        """Walker poses (page headings), walls [x0, y0, x1, y1, dB], profile, t_ms."""
        return {"t_ms": self.t_ms, "walkers": [self._walker_dict(0), self._walker_dict(1)],
                "walls": json.loads(self._walls()), "profile": self.profile}

    def world_state_json(self):
        return json.dumps(self.world_state())

    def telemetry_json(self):
        """One JSON string: {t_ms, watches: [A, B], world: {walkers, walls, profile}}."""
        return '{"t_ms":%d,"watches":%s,"world":{"walkers":%s,"walls":%s,"profile":"%s"}}' % (
            self.t_ms, json.dumps([self.telemetry(0), self.telemetry(1)]),
            json.dumps([self._walker_dict(0), self._walker_dict(1)]), self._walls(),
            self.profile)
