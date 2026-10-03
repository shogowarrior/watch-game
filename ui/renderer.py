"""Strip renderer: one RenderParams -> a 240x240 frame as 10 strips of 240x24.

The renderer reads *only* RenderParams (ui-spec §3). It owns the time-based
state the contract leaves to it: ring spawns and positions, crossfades,
iris open/close, arrow smoothing, toast motion and phase timers. Each strip
is composed off-screen (field blit through the palette, then overlays whose
bounding boxes intersect the strip) and pushed whole with
``display.push_strip(y0, h, buf)`` (hal/st7789.py API).

    r = Renderer()
    events = r.frame(params, display, ticks_ms())   # display=None: state only
    # events: () or a list of haptic pattern names to start now (heartbeats
    # on live ring spawns, plus params.haptic once per params.t_ms). Treat
    # it as read-only: heartbeat-only lists are reused between frames.

The per-frame path allocates nothing on MicroPython: float params are
converted to ints once per params object (cached by identity), text is
cached per string, and only a frame that starts a ``haptic`` event builds a
new list. Every derived timestamp uses ticks_add, and one-shot animations are
gated by flags, so nothing breaks at the 2^30 ms ticks wrap.

Reads beyond the §3 fields (optional, for the logic to adopt):
  * ``status[4]`` (bool): signal unreliable (§5.5), link bars in status.warn.
  * PAIRING ``calibrate``: top_text ``HOLD STILL`` pauses the fill disc, and
    the fill is held within the second named by ``countdown``.

Where §6 is silent: DIRECTION ``turn`` uses the sweep's live-mirror halo
(glow_amp 1 + 5 I, glow_r 12) whatever ``glow_r_px`` says, and the bottom
slot yields while the pacer wedge is within 70 deg of 6 o'clock.
"""

import framebuf

from finder.compat import const, ticks_add, ticks_diff, ticks_ms
from ui import ACC_FOUND, BG_BASE, GREY, PROX, TEXT_PRI, TEXT_SEC, WARN
from ui import glyphs as gl
from ui import text as tx
from ui.field import (EASE_OC, COS, RingMap, RippleField, ZONE_LEAD, ZONE_TRAIL,
                      ease)

W = const(240)
SH = const(24)             # strip height
NS = const(10)             # strips per frame

# ---- RenderParams -------------------------------------------------------------
FIELDS = (
    "t_ms", "screen", "sub", "zone",
    "ramp", "intensity", "speed_px_s", "pulse_period_ms", "wavelength_px",
    "glow_r_px", "ring_live", "burst",
    "glyph", "arrow_deg", "cone_deg", "arrow_style", "trend", "trend_strong",
    "countdown",
    "dist_band", "dist_stale", "word", "top_text", "banner", "status",
    "sweep",
    "haptic", "heartbeat", "heartbeat_every", "backlight", "fps_cap",
)
DEFAULTS = {
    "t_ms": 0, "screen": "FAR", "sub": None, "zone": 0,
    "ramp": "green", "intensity": 0.1, "speed_px_s": 40.0, "pulse_period_ms": 2400,
    "wavelength_px": None, "glow_r_px": 24.0, "ring_live": True, "burst": False,
    "glyph": "glow", "arrow_deg": None, "cone_deg": None, "arrow_style": None,
    "trend": 0, "trend_strong": False, "countdown": None,
    "dist_band": None, "dist_stale": False, "word": None, "top_text": None,
    "banner": None, "status": (80, 80, 4, False), "sweep": None,
    "haptic": None, "heartbeat": None, "heartbeat_every": 1, "backlight": 0.6,
    "fps_cap": 20,
}

try:
    from finder.render_params import RenderParams
except ImportError:
    RenderParams = None
from collections import namedtuple
_LocalParams = namedtuple("RenderParams", FIELDS)
if RenderParams is None:
    RenderParams = _LocalParams


def make_params(**kw):
    """RenderParams with §3 defaults (fixtures, tests, the simulator)."""
    d = dict(DEFAULTS)
    d.update(kw)
    if d["wavelength_px"] is None:
        d["wavelength_px"] = abs(d["speed_px_s"]) * d["pulse_period_ms"] / 1000.0
    try:
        return RenderParams(**d)
    except TypeError:          # finder's class differs from §3: use ours
        return _LocalParams(**d)


# ---- ids ----------------------------------------------------------------------
S_PAIRING = const(0)
S_SEARCHING = const(1)
S_FAR = const(2)
S_HOT = const(5)
S_FOUND = const(6)
S_SCANNING = const(7)
S_LINK_LOST = const(8)
S_MENU = const(9)
SCREENS = {"PAIRING": 0, "SEARCHING": 1, "FAR": 2, "NEAR": 3, "WARM": 4, "HOT": 5,
           "FOUND": 6, "SCANNING": 7, "LINK_LOST": 8, "MENU": 9}

G_GLOW = const(0)
G_SEEKER = const(1)
G_CHEV = const(2)
G_ARROW = const(3)
G_COUNT = const(4)
G_TURN = const(5)
G_CHECK = const(6)
G_RUNES = const(7)
G_BATT = const(8)
G_DOTS = const(9)
GLYPHS = {"glow": 0, "seeker": 1, "chevrons": 2, "arrow": 3, "countdown": 4,
          "turn": 5, "check": 6, "runes": 7, "battery": 8}
IRIS_R = (0, 44, 44, 64, 64, 64, 0, 92, 64, 92)
G_Y0 = (0, 90, 76, 52, 96, 94, 79, 94, 103, 114)     # strip culling boxes
G_Y1 = (0, 150, 165, 190, 145, 147, 162, 147, 137, 126)  # (chevrons: iris r 44)
STYLES = {"solid_a": 1, "solid_b": 2, "outline": 3}

T_NONE = const(0)
T_STATUS = const(1)
T_CHIP = const(2)
T_LAST = const(3)
B_NONE = const(0)
B_TOAST = const(1)
B_WORD = const(2)
B_READOUT = const(3)

WARN_TOP = ("HOLD FLAT", "STAND STILL")
CAL_PAUSE = "HOLD STILL"   # PAIRING calibrate chip while the fill is paused (§6)
CAL_MS = const(3000)       # calibrate fill 64 -> 168 px
ARROW_IN_MS = const(250)   # tokens.motion.arrow_appear
ARROW_OUT_MS = const(600)  # DIRECTION expire shrink
SCAN_BLINK_MS = const(800) # best bin blinks twice (200 ms on/off)
SCAN_MORPH_MS = const(400) # tokens.motion.scan_result_morph
MIRROR_GR = const(3072)    # live-mirror halo glow_r 12 px, Q8 (SCANNING sweep, turn)
MENU_Y = (32, 76, 120, 164)
MENU_MORE_X = 207                           # "more rows" triangles (ui-spec MENU)
MENU_MORE_UP_Y = 36
MENU_MORE_DN_Y = 195
try:
    from array import array as _arr
except ImportError:  # pragma: no cover
    from uarray import array as _arr
_TRI_UP = _arr("h", (0, 5, 3, 0, 6, 5))
_TRI_DN = _arr("h", (0, 0, 6, 0, 3, 5))
_DIGITS = ("0", "1", "2", "3")
_NO_EVENTS = ()


class FrameCapture:
    """Display stand-in that assembles pushed strips into one frame buffer."""

    def __init__(self):
        self.buf = bytearray(W * W * 2)
        self.pushes = 0

    def push_strip(self, y0, h, buf):
        n = W * 2
        self.buf[y0 * n:(y0 + h) * n] = buf
        self.pushes += 1


class Renderer:
    """RenderParams -> strips. ``full_map=True`` trades 28.8 KB for speed."""

    def __init__(self, full_map=False):
        self.buf = bytearray(W * SH * 2)
        self.fb = framebuf.FrameBuffer(self.buf, W, SH, framebuf.RGB565)
        self.field = RippleField()
        self.map = RingMap(self.buf, SH, full_map)
        self.tc = tx.TextCache()
        # settings outside the §3 contract (menu / pairing state)
        self.runes = (0, 3, 6)
        self.sun = False
        self.menu_rows = ["RESUME", "SUN: OFF", "BUZZ: FULL", "PLACE: OUT"]   # Game.menu_rows window
        self._last = {}
        self._ev1 = {}
        self._binq = bytearray(12)      # bins as Q8 (255 = none)
        self._bino = [None] * 12        # the float objects they came from
        self.last_us = 0
        self.reset()

    def reset(self):
        self.field.reset()
        self._iv = self._gv = self._ad = None
        for k in range(12):
            self._bino[k] = 0           # force re-read
        self._iq = self._gq = self._adq = 0
        self._scr = -1
        self._sub = None
        self._sub_t0 = 0
        self._burst_t = None
        self._hap_t = None
        self._toast_s = None
        self._toast_sev = None
        self._toast_st = False
        self._toast_t0 = 0
        self._cal_ms = 0                # calibrate fill progress (ms of 3000)
        # arrow: flags gate every timestamp, so a stale t0 is never compared
        self._a_on = False
        self._a_grow = False            # appear scale-in running (_a_t0)
        self._a_shrink = False          # expire shrink running (_a_off_t0)
        self._a_scr = -1                # screen the arrow was last shown on
        self._a_q = 0
        self._a_tgt = 0
        self._a_t0 = 0
        self._a_off_t0 = 0
        self._a_style = 1
        self._a_cone = 0
        self._morph_k = -1              # SCANNING result: best bin seen blinking
        self._morphed = False           # the morph drew the dart: hand over
        self._morph_a = 0               # its angle, Q4 deg
        self.morph = -1
        self.morph_e = 0
        # per-frame plan
        self.g = G_GLOW
        self.arrow = False
        self.beam = None
        self.sweep = False
        self.pacer = False
        self.top = T_NONE
        self.bot = B_NONE
        self.bot_slot = False

    # ---- state ---------------------------------------------------------------
    def _step(self, p, t):
        f = self.field
        first = not f.started
        if first:
            cap = p.fps_cap or 20
            f.dt = 1000 // cap
        else:
            dt = ticks_diff(t, f.t)
            if dt < 1:
                dt = 1
            elif dt > 250:
                dt = 250
            f.dt = (f.dt * 3 + dt) >> 2
        prev_t = f.t
        f.t = t
        scr = SCREENS.get(p.screen, S_FAR)
        sub = p.sub
        changed = scr != self._scr or sub != self._sub
        if changed:
            self._sub_t0 = t
            self._morph_k = -1
        self._scr = scr
        self._sub = sub
        if scr == S_PAIRING and sub == "calibrate":
            self._cal_fill(p, t, prev_t, changed or first)
        if scr == S_MENU and not first:
            self._hold(ticks_diff(t, prev_t))
        f.set_ramp(p.ramp, t, 0 if first else (400 if p.ramp == "gold" else 1500))
        # levels from intensity (§4 rule 1) + per-screen overrides (§6)
        iv = p.intensity                    # float -> Q8 once per params object
        if iv is not self._iv:
            self._iv = iv
            iq = int(iv * 256)
            self._iq = 0 if iq < 0 else (256 if iq > 256 else iq)
        iq = self._iq
        fl = 77 + ((333 * iq) >> 8)
        glw = 512 + 4 * iq
        pu = 1024 + ((384 * iq) >> 8)
        gv = p.glow_r_px
        if gv is not self._gv:
            self._gv = gv
            self._gq = int(gv * 256)
        gr = self._gq
        if scr == S_PAIRING:
            if sub == "looking":
                pu = 512
            elif sub == "seen" or sub == "confirmed":
                fl = 102
                deg = (t % 2400) * 360 // 2400
                glw = 384 + ((384 * (16384 - COS[deg])) >> 15)
        elif scr == S_SEARCHING:
            glw = 384
            pu = 640
        elif scr == S_LINK_LOST:
            pu = 384
        elif scr == S_SCANNING:
            if sub == "ready" or sub is None:
                pu = (pu * 102) >> 8
            else:
                glw = 256 + 5 * iq
        elif sub == "turn" and S_FAR <= scr <= S_HOT:
            # DIRECTION turn: the halo is the live mirror, as in the sweep
            # (§5.7, §6): glow_amp 1 + 5 I, glow_r 12, so the halo stays
            # inside the pacer's r 70 instead of lighting r 64..124 behind it
            glw = 256 + 5 * iq
            if gr > MIRROR_GR:
                gr = MIRROR_GR
        st = p.status
        if st is not None and st[0] is not None and st[0] <= 10:
            pu = (pu * 179) >> 8                    # saver: pulse_amp x0.7
        if self.sun and fl < 256:
            fl = 256
        if scr != S_MENU:
            f.set_levels(t, fl, glw, pu, gr, changed and not first)
        g = GLYPHS.get(p.glyph, G_GLOW)
        if scr == S_PAIRING and sub == "looking":
            g = G_DOTS
        self.g = g
        if scr != S_MENU:
            f.set_iris(t, IRIS_R[g])
        f.set_dim(128 if scr == S_MENU else 256)
        # rings
        hb = 0
        v = int(p.speed_px_s)
        period = p.pulse_period_ms or 0
        live = p.ring_live
        zone = p.zone
        if S_FAR <= scr <= S_HOT and zone is not None and 0 <= zone <= 3:
            lead = ZONE_LEAD[zone]
            trail = ZONE_TRAIL[zone]
        else:
            lead = 3
            trail = 24 if scr == S_FOUND else 22
        spawn_ok = (v != 0 and period > 0 and scr != S_MENU and scr != S_FOUND and
                    not (scr == S_SCANNING and sub != "ready" and sub is not None))
        r0 = (f.iris_to << 8) if v > 0 else (168 << 8)
        if spawn_ok:
            if period != f.period:
                if f.period and not first:
                    ago = ticks_diff(t, f.last_spawn)
                    if 0 <= ago < f.period:        # recent spawn: re-time the next
                        nxt = ticks_add(f.last_spawn, period)
                        if ticks_diff(nxt, f.next_spawn) < 0:
                            f.next_spawn = nxt
                f.period = period
            if first:
                self._prime(t, r0, v, period, lead, trail, live)
                f.next_spawn = t
            lag = ticks_diff(t, f.next_spawn)
            if lag >= 0:
                if lag >= period:                  # frames were skipped
                    f.next_spawn = ticks_add(t, -(lag % period))
                t0 = f.next_spawn
                amp = f.pu if live else (f.pu * 154) >> 8
                f.spawn(t0, r0, v, amp, lead, trail, not live)
                f.last_spawn = t0
                f.next_spawn = ticks_add(t0, period)
                if live:
                    f.spawns += 1
                    every = p.heartbeat_every or 1
                    if p.heartbeat and f.spawns % every == 0:
                        hb = 1
        elif scr != S_MENU:
            f.next_spawn = t
        if p.burst and p.t_ms != self._burst_t:
            self._burst_t = p.t_ms
            bv = 240 if (scr == S_FOUND or v == 0) else 2 * (v if v > 0 else -v)
            f.spawn(t, f.iris_to << 8, bv, 1792, lead, trail, False)
        f._cull(t)
        f.started = True
        # events
        hap = p.haptic
        if hap and p.t_ms != self._hap_t:
            self._hap_t = p.t_ms
            return [hap, p.heartbeat] if hb else [hap]
        if hb:
            ev = self._ev1.get(p.heartbeat)     # reused 1-item list: no allocation
            if ev is None:
                ev = [p.heartbeat]
                self._ev1[p.heartbeat] = ev
            return ev
        return _NO_EVENTS

    def _hold(self, dt):
        """MENU: freeze rings in place (shift their clocks, wrap-safe)."""
        f = self.field
        for k in range(len(f.r_t0)):
            f.r_t0[k] = ticks_add(f.r_t0[k], dt)
        f.next_spawn = ticks_add(f.next_spawn, dt)
        f.last_spawn = ticks_add(f.last_spawn, dt)
        f.xf_t0 = ticks_add(f.xf_t0, dt)
        f.iris_t0 = ticks_add(f.iris_t0, dt)

    def _cal_fill(self, p, t, prev_t, restart):
        """PAIRING calibrate fill clock: advances with frame time except while
        the logic shows the pause chip, and is held inside the second the
        countdown digit names (digit d: (3-d) s .. (4-d) s), so it tracks the
        Calibrator's paused ``fill_ms`` rather than the wall clock."""
        if restart:
            c = 0
        else:
            c = self._cal_ms
            if p.top_text != CAL_PAUSE:
                d = ticks_diff(t, prev_t)
                c += 0 if d < 0 else (250 if d > 250 else d)
        cd = p.countdown
        if cd is not None and 1 <= cd <= 3:
            lo = (3 - cd) * 1000
            if c < lo:
                c = lo
            elif c > lo + 1000:
                c = lo + 1000
        self._cal_ms = CAL_MS if c > CAL_MS else c

    def _prime(self, t, r0, v, period, lead, trail, live):
        """First frame: place rings already mid-flight (wake shows no intro)."""
        f = self.field
        amp = f.pu if live else (f.pu * 154) >> 8
        for k in range(1, 8):
            age = k * period
            r = r0 + (v * age * 256) // 1000
            if (v > 0 and r > (170 + trail) * 256) or (v < 0 and r < f.iris_to * 256):
                break
            f.spawn(ticks_add(t, -age), r0, v, amp, lead, trail, not live)
            f.last_spawn = ticks_add(t, -age)

    # ---- overlay plan ----------------------------------------------------------
    def _plan(self, p, t):
        f = self.field
        scr = self._scr
        sub = self._sub
        g = self.g
        # arrow: smoothing, appear/expire scale
        self.arrow = False
        self.morph = -1
        scale = 256
        ad = p.arrow_deg
        if ad is not None and scr != S_LINK_LOST:
            if ad is not self._ad:
                self._ad = ad
                self._adq = int(ad * 16) % 5760
            tgt = self._adq
            if not self._a_on:
                self._a_on = True
                self._a_shrink = False
                self._a_tgt = tgt
                if self._morphed:                  # the scan morph drew the dart:
                    self._morphed = False          # glide on from its angle
                    self._a_q = self._morph_a
                    self._a_grow = False
                else:
                    self._a_q = tgt
                    self._a_t0 = t
                    self._a_grow = True
            else:
                d = tgt - self._a_tgt
                if d > 2880:
                    d -= 5760
                elif d < -2880:
                    d += 5760
                if d >= 64 or d <= -64 or tgt == 0 or sub == "turn":
                    self._a_tgt = tgt              # 4 deg deadband
            d = self._a_tgt - self._a_q
            if d > 2880:
                d -= 5760
            elif d < -2880:
                d += 5760
            if d:
                dt = f.dt
                step = (d * ((dt << 8) // (dt + 200))) >> 8    # tau 200 ms
                if step == 0 or -8 < d < 8:
                    step = d
                self._a_q = (self._a_q + step) % 5760
            self._a_style = STYLES.get(p.arrow_style, 1)
            self._a_cone = int(p.cone_deg) if p.cone_deg is not None else 0
            self._a_scr = scr
            if self._a_grow:
                age = ticks_diff(t, self._a_t0)
                if age >= ARROW_IN_MS:
                    self._a_grow = False
                else:
                    scale = 154 + ((102 * ease(EASE_OC, age, ARROW_IN_MS)) >> 8)
            self.arrow = True
        else:
            if scr != S_SCANNING:
                self._morphed = False
            zone_ctx = (S_FAR <= scr <= S_HOT and scr == self._a_scr and
                        (g == G_GLOW or g == G_CHEV or g == G_ARROW))
            if self._a_on:
                # §6 DIRECTION expire: the dart shrinks into C only while the
                # same zone screen stays up; any other screen hides it at once
                self._a_on = False
                self._a_shrink = zone_ctx
                self._a_off_t0 = t
            if self._a_shrink:
                age = ticks_diff(t, self._a_off_t0)
                if not zone_ctx or age >= ARROW_OUT_MS:
                    self._a_shrink = False
                else:
                    scale = 256 - ease(EASE_OC, age, ARROW_OUT_MS)
                    self.arrow = scale > 8
        if self.arrow:
            self.beam = gl.prep_arrow((self._a_q + 8) >> 4, self._a_cone, scale)
        elif scr == S_SCANNING and sub == "result":
            self._plan_morph(p, t)
        # sweep / pacer
        sw = p.sweep
        self.sweep = scr == S_SCANNING and sw is not None and sub != "ready"
        self.pacer = sub == "turn" and sw is not None and scr != S_SCANNING
        if self.sweep or self.pacer:
            gl.prep_wedge(int(sw[0]))
        # top slot
        self.top = T_NONE
        sup_top = scr == S_MENU or (scr == S_SCANNING and sub == "sweep") or self.pacer
        if not sup_top:
            # transient action hints outrank a pinned StatusStrip (low battery
            # or unreliable pins it for minutes); the strip outranks LAST (§6)
            st = p.status
            if p.top_text:
                self.top = T_CHIP
                self.top_s = p.top_text
                self.top_c = WARN if p.top_text in WARN_TOP else TEXT_PRI
            elif st is not None and st[3]:
                self.top = T_STATUS
            elif p.dist_stale and p.dist_band:
                self.top = T_LAST
                s = self._last.get(p.dist_band)
                if s is None:
                    s = "LAST " + p.dist_band + "M"
                    self._last[p.dist_band] = s
                self.top_s = s
                self.top_c = tx.GREY6
        # bottom slot: banner > toast > word > readout. Suppressed wherever a
        # wedge covers it: the sweep, and the turn pacer in its lower sector
        # (otherwise a turn toward something behind hides the pacer under
        # the TURN word for its last 60 deg or more)
        self.bot = B_NONE
        if not (scr == S_MENU or (scr == S_SCANNING and sub == "sweep") or
                (self.pacer and gl.wedge_hits_bottom(int(sw[0])))):
            bn = p.banner
            if bn:
                self.bot = B_TOAST
                # rise when a banner appears, or a new toast replaces one; a
                # sticky banner's text updates (LOST 0:12 -> 0:13) stay put
                if (self._toast_s is None or bn[1] != self._toast_sev or
                        bn[2] != self._toast_st or (not bn[2] and bn[0] != self._toast_s)):
                    self._toast_t0 = t
                self._toast_s = bn[0]
                self._toast_sev = bn[1]
                self._toast_st = bn[2]
                self.bot_s = bn[0]
                self.bot_c = tx.SEV_COL.get(bn[1], WARN)
                self.bot_dy = 12 - ((12 * ease(EASE_OC, ticks_diff(t, self._toast_t0), 200)) >> 8)
            else:
                self._toast_s = None
                if p.word:
                    self.bot = B_WORD
                    self.bot_s = p.word
                    self.bot_c = self._word_col(p, scr, sub)
                elif p.dist_band and S_FAR <= scr <= S_HOT and not p.dist_stale:
                    self.bot = B_READOUT
                    self.bot_s = p.dist_band
                    # with the arrow up the readout carries a trend mark: its
                    # slot is kept while the trend is 0 so the numerals do
                    # not shift 9 px each time the mark comes and goes (§1)
                    self.bot_slot = g == G_ARROW and self.arrow
                    self.bot_mark = p.trend if self.bot_slot else 0

    def _plan_morph(self, p, t):
        """SCANNING result: after the best bin's blink, the bar retracts while
        the dart grows at its bin angle (§6, 400 ms out_cubic)."""
        sw = p.sweep
        if sw is None:
            return
        if sw[2] is not None:
            self._morph_k = sw[2]
        k = self._morph_k
        if k < 0:
            return
        e_ms = ticks_diff(t, self._sub_t0) - SCAN_BLINK_MS
        if e_ms < 0:
            return
        e = ease(EASE_OC, e_ms, SCAN_MORPH_MS)
        self.morph = k
        self.morph_e = e
        self._morph_a = k * 480                    # k * 30 deg, Q4
        self._morphed = True
        self._a_style = 1
        self._a_cone = 0
        self.arrow = True
        self.beam = gl.prep_arrow(k * 30, 0, 26 + ((230 * e) >> 8))

    def _word_col(self, p, scr, sub):
        w = p.word
        if scr == S_SEARCHING:
            return GREY[7]
        if w == "FOUND":
            return ACC_FOUND
        if w == "BUMP!":
            return PROX[7]
        if scr == S_PAIRING and sub == "looking":
            return TEXT_SEC
        return TEXT_PRI

    # ---- drawing ---------------------------------------------------------------
    def _strip(self, p, t, y0):
        fb = self.fb
        y1 = y0 + SH
        self.map.blit(y0, self.field.pal)
        g = self.g
        scr = self._scr
        # beam / sweep wedge layer
        if (self.sweep or self.pacer) and y0 < 232 and y1 > 8:
            wedge = self.pacer or self._sub == "sweep"
            if self.sweep:
                self._draw_bins(p, y0, 0 if wedge else 2)
            if wedge:
                sw = p.sweep
                gl.draw_wedge(fb, y0, WARN if sw[3] else PROX[6])
                if self.sweep:
                    self._draw_bins(p, y0, 1)
        # glyph layer
        if self.arrow:
            if y0 < 190 and y1 > 52:
                gl.draw_arrow(fb, y0, self._a_style, self.beam)
        elif g != G_GLOW and y0 < G_Y1[g] and y1 > G_Y0[g]:
            if g == G_CHEV:
                gl.draw_chevrons(fb, y0, p.trend, p.trend_strong, gl.chevron_nudge(t))
            elif g == G_SEEKER:
                gl.draw_seeker(fb, y0)
            elif g == G_COUNT:
                if p.countdown is not None:
                    tx.draw_countdown(self.tc, fb, y0, self.tc.num(p.countdown))
            elif g == G_TURN:
                gl.draw_turn(fb, y0, p.word == "TURN LEFT")
            elif g == G_CHECK:
                gl.draw_check(fb, y0)
            elif g == G_RUNES:
                self._draw_runes(p, t, y0)
            elif g == G_BATT:
                st = p.status
                gl.draw_battery(fb, y0, st[0] if st is not None else None)
            elif g == G_DOTS:
                gl.draw_dots(fb, y0)
        # top slot (y 12..35)
        top = self.top
        if top != T_NONE and y0 < 36:
            if top == T_STATUS:
                st = p.status
                tx.draw_status(self.tc, fb, y0, st[0], st[1], st[2],
                               len(st) > 4 and st[4])
            elif top == T_CHIP:
                tx.draw_top_chip(self.tc, fb, y0, self.top_s, self.top_c)
            else:
                tx.draw_top_chip(self.tc, fb, y0, self.top_s, self.top_c, p.trend)
        # bottom slot (y 186..225, toasts rise from +12)
        bot = self.bot
        if bot != B_NONE and y1 > 186:
            if bot == B_TOAST:
                tx.draw_toast(self.tc, fb, y0, self.bot_s, self.bot_c, self.bot_dy)
            elif bot == B_WORD:
                tx.draw_word(self.tc, fb, y0, self.bot_s, self.bot_c)
            else:
                tx.draw_readout(self.tc, fb, y0, self.bot_s, self.bot_mark, self.bot_slot)
        if scr == S_MENU and y1 > 32 and y0 < 204:
            # opaque backplate: no frozen ring arcs show in the 4 px gaps
            gl.rrect(fb, 24, 32 - y0, 192, 172, 8, BG_BASE)
            sel = p.sub
            k_sel = -1                                # sub = visible index + optional "^"/"v"
            if sel:
                for k in range(4):
                    if sel.startswith(_DIGITS[k]):    # no slicing: the frame loop must not allocate
                        k_sel = k
            rows = self.menu_rows
            for k in range(4):
                y = MENU_Y[k]
                if y < y1 and y + 40 > y0:
                    tx.draw_menu_row(self.tc, fb, y0, y, rows[k], k_sel == k)
            if sel:
                if "^" in sel and MENU_MORE_UP_Y < y1 and MENU_MORE_UP_Y + 6 > y0:
                    fb.poly(MENU_MORE_X, MENU_MORE_UP_Y - y0, _TRI_UP, TEXT_SEC, True)
                if "v" in sel and MENU_MORE_DN_Y < y1 and MENU_MORE_DN_Y + 6 > y0:
                    fb.poly(MENU_MORE_X, MENU_MORE_DN_Y - y0, _TRI_DN, TEXT_SEC, True)

    def _draw_bins(self, p, y0, which):
        """Scan bins. finder/scan.py updates ``bins`` in place and blinks the
        best bin in ``result`` by toggling ``active_bin``, so floats are
        re-read per slot (converted only when that slot's object changed).

        ``which``: 0 = all but the active bin (under the wedge), 1 = only the
        active bin (drawn over the wedge: the wedge always covers the bin
        being sampled, which would hide its highlight), 2 = all.
        """
        fb = self.fb
        sw = p.sweep
        bins = sw[1]
        if bins is None:
            return
        act = sw[2]
        act_c = PROX[5] if self._sub == "sweep" else PROX[7]
        bq = self._binq
        bo = self._bino
        y1 = y0 + SH
        for k in range(12):
            if which == 1:
                if k != act:
                    continue
            elif which == 0 and k == act:
                continue
            if gl.BIN_Y0[k] >= y1 or gl.BIN_Y1[k] <= y0:
                continue
            b = bins[k]
            if b is not bo[k]:
                bo[k] = b
                bq[k] = 255 if b is None else min(254, max(0, int(b * 254)))
            q = bq[k]
            if k == self.morph:                    # retracting into the dart
                if self.morph_e < 230 and q != 255:
                    gl.draw_bin(fb, y0, k, (q * (256 - self.morph_e)) >> 8, act_c)
            elif q == 255:
                gl.draw_bin_hollow(fb, y0, k, act_c if k == act else PROX[3])
            elif which == 1:                       # over the wedge
                gl.draw_bin(fb, y0, k, q, act_c, BG_BASE, False)
            else:
                gl.draw_bin(fb, y0, k, q, act_c if k == act else PROX[3])

    def _draw_runes(self, p, t, y0):
        fb = self.fb
        ids = getattr(p, "runes", None) or self.runes
        sub = self._sub
        age = ticks_diff(t, self._sub_t0)
        for k in range(3):
            if sub == "confirmed":
                c = gl.RUNE_OK
            elif sub == "seen" and age < 150 * (k + 1):
                c = gl.RUNE_WAIT
            else:
                c = gl.RUNE_COL
            gl.draw_rune(fb, y0, ids[k], gl.RUNE_X[k], c)

    def _palette(self, p, t):
        f = self.field
        scr = self._scr
        sub = self._sub
        rim = -1
        fill_r = 0
        if scr == S_SCANNING and (sub == "ready" or sub is None):
            flat = p.top_text not in WARN_TOP and not (p.sweep is not None and p.sweep[3])
            rim = PROX[6] if flat else WARN
        elif scr == S_PAIRING and sub == "calibrate":
            fill_r = 64 + (104 * self._cal_ms) // CAL_MS
        st = p.status
        saver = st is not None and st[0] is not None and st[0] <= 10
        f.build(t, core=(self.g == G_GLOW and S_FAR <= scr <= S_HOT),
                rim=rim, fill_r=fill_r, standing=(scr == S_FOUND),
                vmax=1280 if saver else 1792, lift=9 if self.sun else 0)

    # ---- public ------------------------------------------------------------------
    def frame(self, p, display=None, now=None):
        """Advance to ``now`` (default ticks_ms()); draw + push if ``display``.

        ``now`` is the render clock, not ``p.t_ms``: params arrive at 10 Hz
        and the renderer interpolates between them (§3, §4 rule 3), so a
        params object is normally drawn for several frames. Offline callers
        (tests, snapshots) pass their own simulated clock.
        Returns haptic events to start now: () or a list of pattern names.
        """
        t = ticks_ms() if now is None else now
        ev = self._step(p, t)
        if display is not None:
            self._plan(p, t)
            self._palette(p, t)
            buf = self.buf
            for s in range(NS):
                y0 = s * SH
                self._strip(p, t, y0)
                display.push_strip(y0, SH, buf)
        return ev
