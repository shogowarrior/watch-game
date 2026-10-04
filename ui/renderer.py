"""Strip renderer: one RenderParams -> a 240x240 frame as 10 strips of 240x24.

The renderer reads *only* RenderParams (ui-spec §3). It owns the time-based
state the contract leaves to it: ring spawns and positions, crossfades, iris
open/close, arrow smoothing, the wedge glide between 10 Hz params, toast
motion and phase timers. Each strip is composed off-screen (field blit
through the palette, then overlays whose bounding boxes intersect the strip)
and pushed whole with ``display.push_strip(y0, h, buf)`` (hal/st7789.py API).
Strips go out top to bottom, so the panel takes them as one window, all from
one buffer (the display has sent a strip when ``push_strip`` returns).

    r = Renderer()
    beats = r.frame(params, display, ticks_ms())    # display=None: state only
    # beats: () or a one-item list, the heartbeat pattern to start now (on a
    # live ring spawn). Treat it as read-only: the lists are reused. The
    # caller plays ``params.haptic`` itself (HapticPlayer.play_named) and
    # passes these to HapticPlayer.heartbeat.

With the screen off (``display=None``) the field keeps time; the first drawn
frame after that is the current state with no intro (§8): no arrow scale-in,
glide or expire shrink, and a toast already up sits at rest.

The per-frame path allocates nothing on MicroPython: float params are
converted to ints once per params object (cached by identity), text is
cached per string, and the heartbeat lists are reused. Every derived
timestamp uses ticks_add, and one-shot animations are
gated by flags, so nothing breaks at the 2^30 ms ticks wrap. Token values
come from finder/tuning.py (except the chip/StatusStrip geometry literals
listed in ui/__init__.py); ui-spec-only values carry their § in a comment.

PAIRING ``calibrate``: the fill disc pauses while top_text is HINT_HOLD_STILL
(the §6 calibration gate) and is held within the second ``countdown`` names.

ui-spec §6 rules drawn here: the DIRECTION ``turn`` word yields the bottom
slot while the pacer wedge is within 70 deg of 6 o'clock (it would cover the
wedge), and strong colder chevrons sit at y 107 / 133 (ui/glyphs.py).
"""

from array import array as _arr

import framebuf

from finder import tuning as T
from finder.compat import const, ticks_add, ticks_diff, ticks_ms
from finder.pairing import HINT_HOLD_STILL as CAL_PAUSE   # calibrate chip: the fill is paused
from finder.scan import HINT_FLAT, BLINK_MS as SCAN_BLINK_PHASE_MS  # the logic's blink phase (§6 result)
from ui import ACC_FOUND, BG_BASE, GREY, PROX, TEXT_PRI, TEXT_SEC, WARN
from ui import glyphs as gl
from ui import text as tx
from ui.field import (COS, EASE_IC, EASE_OC, FL_A, FL_B, GL_A, GL_B, PU_A, PU_B, STAND_MEAN,
                      V7, RingMap, RippleField, ZONE_LEAD, ZONE_TRAIL, ease, q8)

W = const(240)
SH = const(24)             # strip height
NS = const(10)             # strips per frame

# ---- ids: index in T.SCREENS / T.GLYPHS (tests/test_renderer.py checks the consts) ----
S_PAIRING = const(0)
S_SEARCHING = const(1)
S_FAR = const(2)
S_HOT = const(5)
S_FOUND = const(6)
S_SCANNING = const(7)
S_LINK_LOST = const(8)
S_MENU = const(9)
SCREENS = {n: i for i, n in enumerate(T.SCREENS)}

G_GLOW = const(0)
G_SEEKER = const(1)
G_CHEV = const(2)
G_ARROW = const(3)
G_COUNT = const(4)
G_TURN = const(5)
G_CHECK = const(6)
G_RUNES = const(7)
G_BATT = const(8)
G_BUMP = const(9)
G_DOTS = const(10)          # renderer-only: the PAIRING looking dots
GLYPHS = {n: i for i, n in enumerate(T.GLYPHS)}
_IR = T.IRIS_R              # iris radius per glyph id (§2; countdown, turn, battery: as scan)
IRIS_R = (_IR["none"], _IR["seeker"], _IR["chevrons"], _IR["arrow"], _IR["scan"],
          _IR["scan"], _IR["none"], _IR["runes"], _IR["scan"], _IR["bump"], _IR["runes"])
# strip culling boxes: tests/test_renderer.py checks they cover every glyph
G_Y0 = (0, 90, 76, 52, 96, 94, 79, 94, 103, 63, 114)
G_Y1 = (0, 150, 165, 190, 145, 147, 162, 147, 137, 152, 126)

T_NONE = const(0)
T_STATUS = const(1)
T_CHIP = const(2)
T_LAST = const(3)
B_NONE = const(0)
B_TOAST = const(1)
B_WORD = const(2)
B_READOUT = const(3)

# Display copy the renderer keys on. The literals mirror finder/game.py
# (tests/test_renderer.py checks them).
WARN_TOP = (HINT_FLAT,)             # chips in status.warn; scan-ready rim warn
W_FOUND = "FOUND"                    # words starting with it in accent.found
W_BUMP = "BUMP!"                     # word in prox.7

# token values (finder/tuning.py) as plain ints; Q8 = 256 per ramp step
LOGIC_MS = T.LOGIC_MS      # params arrive at 10 Hz (§3)
CAL_MS = T.CAL_WINDOW_MS   # calibrate fill r 64 -> 168 over the calibrate window
CAL_N = (CAL_MS + 999) // 1000   # countdown digits, as finder.pairing.Calibrator.digit
FILL_R0 = T.IRIS_R["scan"]
R_MAX = T.FIELD_R_MAX      # ring-map max index: inward rings spawn and the fill ends here
ARROW_IN_MS = T.ARROW_APPEAR_MS
ARROW_IN_S0 = q8(T.ARROW_APPEAR_SCALE)       # scale-in from 0.6
ARROW_OUT_MS = T.ARROW_EXPIRE_MS             # DIRECTION expire shrink
ARROW_TAU_MS = T.ARROW_ANGLE_TAU_MS
ARROW_DEADBAND = int(T.ARROW_ANGLE_DEADBAND_DEG * 16)   # Q4 deg
TOAST_PX = T.TOAST_IN_PX                     # toasts rise in from / fall out to +12 px
PU_LOOKING = q8(T.FIELD_PAIRING_LOOKING[5])
PU_SEARCHING = q8(T.FIELD_SEARCHING[5])
PU_LOST = q8(T.FIELD_LINK_LOST[5])
PU_READY = q8(T.FIELD_SCAN_READY_PULSE_SCALE)
MG_A = q8(T.FIELD_SCAN_SWEEP_GLOW_AMP[0])    # live-mirror halo glow_amp = 1 + 5 I (§5.7)
MG_B = q8(T.FIELD_SCAN_SWEEP_GLOW_AMP[1])
SEEN_A = q8(T.PAIRING_SEEN_BREATHE_AMP[0])   # PAIRING seen halo breathes 1.5 <-> 3.0
SEEN_B = q8(T.PAIRING_SEEN_BREATHE_AMP[1])
SEEN_MS = T.BREATHE_PAIRING_MS
SEEN_FL = q8(T.PAIRING_SEEN_FLOOR)           # PAIRING seen / confirmed floor (§6)
SEARCH_GL = q8(T.SEARCHING_GLOW_AMP)         # SEARCHING glow_amp (§6)
RING_LEAD, RING_TRAIL = T.FIELD_LEAD_TRAIL_PX     # rings without a zone tempo
FOUND_LEAD, FOUND_TRAIL = T.FOUND_LEAD_TRAIL_PX   # the FOUND burst ring
SAVER_PU = q8(T.SAVER_PULSE_SCALE)
SAVER_VMAX = q8(T.SAVER_V_MAX)
MENU_DIM = q8(T.MENU_PALETTE_SCALE)
CORE_V = q8(T.CORE_DOT_LEVEL)
BURST_AMP = q8(T.BURST_AMP)
BURST_V_FOUND = int(T.FOUND_BURST_SPEED_PX_S)
BURST_MULT = int(T.BURST_SPEED_MULT)
SUN_FLOOR = const(256)     # sun mode: floor >= 1.0 and the LUT lifted one stop (§8)
SUN_LIFT = const(9)
MENU_Y = T.MENU_ROWS_Y
MENU_ROW_H = T.MENU_ROW_H
MENU_MORE_X = 207                           # "more rows" triangles (ui-spec MENU)
MENU_MORE_UP_Y = 36
MENU_MORE_DN_Y = 195
_TRI_UP = _arr("h", (0, 5, 3, 0, 6, 5))
_TRI_DN = _arr("h", (0, 0, 6, 0, 3, 5))
_DIGITS = ("0", "1", "2", "3")
_NO_EVENTS = ()


def _wrap_q4(d):
    """Angle difference in Q4 deg (5760 = 360) -> the short way, -2880..2880."""
    return d - 5760 if d > 2880 else (d + 5760 if d < -2880 else d)


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
    """RenderParams -> strips, each drawn in ``buf`` and pushed top to bottom."""

    def __init__(self):
        self.buf = bytearray(W * SH * 2)
        self.fb = framebuf.FrameBuffer(self.buf, W, SH, framebuf.RGB565)
        self.field = RippleField()
        self.map = RingMap(SH, (self.buf,))
        self.tc = tx.TextCache()
        self._last = {}
        self._ev1 = {}
        self._binq = bytearray(12)      # bins as Q8 (255 = none)
        self._bino = [0] * 12           # the objects they came from (0 -> Q8 0)
        self.reset()

    def reset(self):
        self.field.reset()
        self._iv = self._gv = self._ad = None
        self._iq = self._gq = self._adq = 0
        self._scr = -1
        self._sub = None
        self._sub_t0 = 0
        self._saver = False
        self._dark = False              # display=None frames since the last drawn one
        self._burst_t = None
        self._toast_s = None
        self._toast_sev = None
        self._toast_st = False
        self._toast_out = False         # exit running (from _toast_t0)
        self._toast_t0 = 0
        self._cal_ms = 0                # calibrate fill progress (ms of 3000)
        self._sw_o = None               # wedge glide: last sweep tuple seen,
        self._sw_a = 0                  # the angle it glides from and to (deg)
        self._sw_b = 0
        self._sw_t = 0
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
        prev_t = f.tick(t, p.fps_cap)
        scr = SCREENS.get(p.screen, S_FAR)
        sub = p.sub
        changed = scr != self._scr or sub != self._sub
        if changed:
            self._sub_t0 = t
            self._morph_k = -1
            if p.sweep is not None:             # a new wedge starts at its angle
                self._wedge_reset(p.sweep)
        self._scr = scr
        self._sub = sub
        menu = scr == S_MENU
        st = p.status
        self._saver = st is not None and st[0] is not None and st[0] <= T.BATT_CRITICAL_PCT
        cal = scr == S_PAIRING and sub == "calibrate"
        if cal:
            self._cal_fill(p, t, prev_t, changed or first)
        if menu and not first:
            f.hold(ticks_diff(t, prev_t))
        f.set_ramp(p.ramp, t, 0 if first else (
            T.HUE_CROSSFADE_FOUND_MS if p.ramp == "gold" else T.HUE_CROSSFADE_MS))
        # levels from intensity (§4 rule 1) + per-screen overrides (§6)
        iv = p.intensity                    # float -> Q8 once per params object
        if iv is not self._iv:
            self._iv = iv
            iq = int(iv * 256)
            self._iq = 0 if iq < 0 else (256 if iq > 256 else iq)
        iq = self._iq
        fl = FL_A + ((FL_B * iq) >> 8)
        glw = GL_A + ((GL_B * iq) >> 8)
        pu = PU_A + ((PU_B * iq) >> 8)
        gv = p.glow_r_px
        if gv is not self._gv:
            self._gv = gv
            self._gq = int(gv * 256)
        if scr == S_PAIRING:
            if sub == "looking":
                pu = PU_LOOKING
            elif sub == "seen" or sub == "confirmed":
                fl = SEEN_FL
                deg = (t % SEEN_MS) * 360 // SEEN_MS
                glw = SEEN_A + (((SEEN_B - SEEN_A) * (16384 - COS[deg])) >> 15)
        elif scr == S_SEARCHING:
            glw = SEARCH_GL
            pu = PU_SEARCHING
        elif scr == S_LINK_LOST:
            pu = PU_LOST
        elif scr == S_FOUND:
            # the standing wave hides these: target its mean, so the level
            # crossfade out of FOUND starts from the field that was shown
            fl = STAND_MEAN
            glw = 0
        elif scr == S_SCANNING:
            if sub == "ready" or sub is None:
                pu = (pu * PU_READY) >> 8
            else:
                glw = MG_A + ((MG_B * iq) >> 8)
        elif sub == "turn" and p.sweep is not None and S_FAR <= scr <= S_HOT:
            # only the guided turn (pacer wedge) has the live mirror; the
            # static FACE IT frames (also sub "turn") keep the zone levels
            glw = MG_A + ((MG_B * iq) >> 8)
        if self._saver:
            pu = (pu * SAVER_PU) >> 8
        if p.sun and fl < SUN_FLOOR:
            fl = SUN_FLOOR
        g = GLYPHS.get(p.glyph, G_GLOW)
        if scr == S_PAIRING and sub == "looking":
            g = G_DOTS
        self.g = g
        if not menu:                        # MENU: the field is frozen
            f.set_levels(t, fl, glw, pu, self._gq, changed and not first)
            f.set_iris(t, IRIS_R[g])
            f.set_fill(cal, FILL_R0 + ((R_MAX - FILL_R0) * self._cal_ms) // CAL_MS)
            f.set_stand(scr == S_FOUND)
        f.set_dim(MENU_DIM if menu else 256)
        # rings
        hb = 0
        v = int(p.speed_px_s)
        period = p.pulse_period_ms or 0
        zone = p.zone
        if zone is not None and 0 <= zone <= 3 and v > 0:
            # outward rings at a zone tempo (FAR..HOT, PAIRING split,
            # SCANNING ready): the zone's lead / trail too (§5.3)
            lead = ZONE_LEAD[zone]
            trail = ZONE_TRAIL[zone]
        elif scr == S_FOUND:
            lead = FOUND_LEAD
            trail = FOUND_TRAIL
        else:
            lead = RING_LEAD
            trail = RING_TRAIL
        if (v != 0 and period > 0 and not menu and scr != S_FOUND and
                not (scr == S_SCANNING and sub != "ready" and sub is not None)):
            r0 = (f.iris_to << 8) if v > 0 else (R_MAX << 8)
            # inward rings are listening rings (§4 rule 4): never ghosts
            if (f.schedule(t, period, r0, v, lead, trail, p.ring_live or v < 0, first) and
                    p.heartbeat and f.spawns % (p.heartbeat_every or 1) == 0):
                hb = 1
        elif not menu:
            f.idle(t)
        if p.burst and p.t_ms != self._burst_t:
            self._burst_t = p.t_ms
            bv = BURST_V_FOUND if (scr == S_FOUND or v == 0) else BURST_MULT * (v if v > 0 else -v)
            f.spawn(t, f.iris_to << 8, bv, BURST_AMP, lead, trail, False)
        f.cull(t)
        f.started = True
        if hb:
            ev = self._ev1.get(p.heartbeat)     # reused 1-item list: no allocation
            if ev is None:
                ev = [p.heartbeat]
                self._ev1[p.heartbeat] = ev
            return ev
        return _NO_EVENTS

    def _cal_fill(self, p, t, prev_t, restart):
        """PAIRING calibrate fill clock: advances with frame time except while
        the logic shows the pause chip, and is held inside the second the
        countdown digit names (digit d of n: (n-d) s .. (n-d+1) s)."""
        if restart:
            c = 0
        else:
            c = self._cal_ms
            if p.top_text != CAL_PAUSE:
                d = ticks_diff(t, prev_t)
                c += 0 if d < 0 else (250 if d > 250 else d)
        cd = p.countdown
        if cd is not None and 1 <= cd <= CAL_N:
            lo = (CAL_N - cd) * 1000
            if c < lo:
                c = lo
            elif c > lo + 1000:
                c = lo + 1000
        self._cal_ms = CAL_MS if c > CAL_MS else c

    # ---- overlay plan ----------------------------------------------------------
    def _snap(self, p, t):
        """First drawn frame after display=None frames: the current state with
        no intro (§8). The arrow sits at its angle (or is gone: one that
        expired while dark never shrinks on wake), a banner is at rest."""
        self._a_grow = False
        self._a_shrink = False
        self._morphed = False
        ad = p.arrow_deg
        self._a_on = ad is not None and self._scr != S_LINK_LOST
        if self._a_on:
            self._set_ad(ad)
            self._a_q = self._a_tgt = self._adq
            self._a_scr = self._scr
        bn = p.banner
        self._toast_out = False
        self._toast_s = bn[0] if bn else None
        if bn:
            self._toast_sev = bn[1]
            self._toast_st = bn[2]
            self._toast_t0 = ticks_add(t, -T.TOAST_IN_MS)
        if p.sweep is not None:
            self._wedge_reset(p.sweep)

    def _set_ad(self, ad):
        """Arrow angle param -> Q4 deg, converted once per float object."""
        self._ad = ad
        self._adq = int(ad * 16) % 5760

    def _wedge_reset(self, sw):
        """A new wedge (or the first after a wake) starts at its angle, no glide."""
        self._sw_o = sw
        self._sw_a = self._sw_b = int(sw[0])

    def _wedge_deg(self, sw, t):
        """Sweep / pacer wedge angle on the render clock: each new 10 Hz angle
        is reached by gliding linearly from the last one over one logic tick
        (§3 interpolation; tokens sweep_rotation is linear), integer-only, the
        short way across 0/360, held while paused."""
        if sw is not self._sw_o:
            self._sw_o = sw
            a = int(sw[0])
            if a != self._sw_b:
                self._sw_a = self._sw_b
                self._sw_b = a
                self._sw_t = t
        b = self._sw_b
        if self._sw_a == b:
            return b
        age = ticks_diff(t, self._sw_t)
        if sw[3] or age >= LOGIC_MS:            # paused, or the glide is done:
            self._sw_a = b                      # a stale _sw_t is never read again
            return b
        d = b - self._sw_a
        if d > 180:
            d -= 360
        elif d < -180:
            d += 360
        return self._sw_a + (d * age) // LOGIC_MS

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
                self._set_ad(ad)
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
                d = _wrap_q4(tgt - self._a_tgt)
                if d >= ARROW_DEADBAND or d <= -ARROW_DEADBAND or tgt == 0 or sub == "turn":
                    self._a_tgt = tgt
            d = _wrap_q4(self._a_tgt - self._a_q)
            if d:
                dt = f.dt
                step = (d * ((dt << 8) // (dt + ARROW_TAU_MS))) >> 8
                if step == 0 or -8 < d < 8:
                    step = d
                self._a_q = (self._a_q + step) % 5760
            self._a_style = gl.STYLES.get(p.arrow_style, 1)
            self._a_cone = int(p.cone_deg) if p.cone_deg is not None else 0
            self._a_scr = scr
            if self._a_grow:
                age = ticks_diff(t, self._a_t0)
                if age >= ARROW_IN_MS:
                    self._a_grow = False
                else:
                    e = ease(EASE_OC, age, ARROW_IN_MS)
                    scale = ARROW_IN_S0 + (((256 - ARROW_IN_S0) * e) >> 8)
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
        wd = 0
        if self.sweep or self.pacer:
            wd = self._wedge_deg(sw, t)
            gl.prep_wedge(wd)
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
                self.top_c = GREY[6]
        # bottom slot: banner > toast > word > readout. Suppressed wherever a
        # wedge covers it: the sweep, and the turn pacer in its lower sector
        # (otherwise a turn toward something behind hides the pacer under
        # the TURN word for its last 60 deg or more)
        self.bot = B_NONE
        if (scr == S_MENU or (scr == S_SCANNING and sub == "sweep") or
                (self.pacer and gl.wedge_hits_bottom(wd))):
            self._toast_s = None                   # hidden: one still up rises again
            self._toast_out = False
            return
        bn = p.banner
        if bn:
            # rise when a banner appears, or a new toast replaces one; a
            # sticky banner's text updates (LOST 0:12 -> 0:13) stay put
            if (self._toast_s is None or self._toast_out or bn[1] != self._toast_sev or
                    bn[2] != self._toast_st or (not bn[2] and bn[0] != self._toast_s)):
                self._toast_t0 = t
            self._toast_out = False
            self._toast_s = bn[0]
            self._toast_sev = bn[1]
            self._toast_st = bn[2]
            self.bot = B_TOAST
            self.bot_s = bn[0]
            self.bot_c = tx.SEV_COL.get(bn[1], WARN)
            e = ease(EASE_OC, ticks_diff(t, self._toast_t0), T.TOAST_IN_MS)
            self.bot_dy = TOAST_PX - ((TOAST_PX * e) >> 8)
            return
        if self._toast_s is not None:
            # toast_out: the last toast (bot_s / bot_c) falls 12 px, 150 ms in_cubic
            if not self._toast_out:
                self._toast_out = True
                self._toast_t0 = t
            age = ticks_diff(t, self._toast_t0)
            if age < T.TOAST_OUT_MS:
                self.bot = B_TOAST
                self.bot_dy = (TOAST_PX * ease(EASE_IC, age, T.TOAST_OUT_MS)) >> 8
                return
            self._toast_s = None
            self._toast_out = False
        if p.word:
            self.bot = B_WORD
            self.bot_s = p.word
            self.bot_c = self._word_col(p, scr, sub)
        elif p.dist_band and S_FAR <= scr <= S_HOT and not p.dist_stale:
            self.bot = B_READOUT
            self.bot_s = p.dist_band
            # with the arrow up the readout carries a trend mark: its slot is
            # kept while the trend is 0 so the numerals do not shift 9 px
            # each time the mark comes and goes (§1)
            self.bot_slot = g == G_ARROW and self.arrow
            self.bot_mark = p.trend if self.bot_slot else 0

    def _plan_morph(self, p, t):
        """SCANNING result: after the best bin's blink, the bar retracts while
        the dart grows at θ (sweep slot 0; §6, 400 ms out_cubic)."""
        sw = p.sweep
        if sw is None:
            return
        if sw[2] is not None:
            self._morph_k = sw[2]
        k = self._morph_k
        if k < 0:
            return
        e_ms = ticks_diff(t, self._sub_t0) - SCAN_BLINK_PHASE_MS
        if e_ms < 0:
            return
        e = ease(EASE_OC, e_ms, T.SCAN_MORPH_MS)
        self.morph = k
        self.morph_e = e
        a = int(sw[0]) % 360
        self._morph_a = a * 16                     # Q4
        self._morphed = True
        self._a_style = 1
        self._a_cone = 0
        self.arrow = True
        self.beam = gl.prep_arrow(a, 0, 26 + ((230 * e) >> 8))

    def _word_col(self, p, scr, sub):
        w = p.word
        if scr == S_SEARCHING:
            return GREY[7]
        if w.startswith(W_FOUND):           # FOUND and FOUND 1:48 (§6 FOUND)
            return ACC_FOUND
        if w == W_BUMP:
            return PROX[7]
        if scr == S_PAIRING and sub == "looking":
            return TEXT_SEC
        return TEXT_PRI

    # ---- drawing ---------------------------------------------------------------
    def _strip(self, p, t, y0, fb):
        """Overlays for the strip at ``y0`` onto its field in ``fb``."""
        y1 = y0 + SH
        g = self.g
        scr = self._scr
        # beam / sweep wedge layer
        if (self.sweep or self.pacer) and y0 < 232 and y1 > 8:
            wedge = self.pacer or self._sub == "sweep"
            if self.sweep:
                self._draw_bins(p, fb, y0, 0 if wedge else 2)
            if wedge:
                sw = p.sweep
                gl.draw_wedge(fb, y0, WARN if sw[3] else PROX[6])
                if self.sweep:
                    self._draw_bins(p, fb, y0, 1)
        # glyph layer
        if self.arrow:
            if y0 < G_Y1[G_ARROW] and y1 > G_Y0[G_ARROW]:
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
                gl.draw_turn(fb, y0)
            elif g == G_CHECK:
                gl.draw_check(fb, y0)
            elif g == G_RUNES:
                self._draw_runes(p, fb, t, y0)
            elif g == G_BATT:
                st = p.status
                gl.draw_battery(fb, y0, st[0] if st is not None else None)
            elif g == G_BUMP:
                gl.draw_bump(fb, y0, y1, p.bump_icons)
            elif g == G_DOTS:
                gl.draw_dots(fb, y0)
        # top slot (y 12..35)
        top = self.top
        if top != T_NONE and y0 < 36:
            if top == T_STATUS:
                st = p.status
                tx.draw_status(self.tc, fb, y0, st[0], st[1], st[2], st[4])
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
            rows = p.menu_rows
            for k in range(4):
                y = MENU_Y[k]
                if y < y1 and y + MENU_ROW_H > y0:
                    tx.draw_menu_row(self.tc, fb, y0, y, rows[k], k_sel == k)
            if sel:
                if "^" in sel and MENU_MORE_UP_Y < y1 and MENU_MORE_UP_Y + 6 > y0:
                    fb.poly(MENU_MORE_X, MENU_MORE_UP_Y - y0, _TRI_UP, TEXT_SEC, True)
                if "v" in sel and MENU_MORE_DN_Y < y1 and MENU_MORE_DN_Y + 6 > y0:
                    fb.poly(MENU_MORE_X, MENU_MORE_DN_Y - y0, _TRI_DN, TEXT_SEC, True)

    def _draw_bins(self, p, fb, y0, which):
        """Scan bins. Game passes a per-tick tuple of the scan's bins (the
        float objects are reused while a bin is unchanged) and blinks the best
        bin in ``result`` by toggling ``active_bin``, so each slot is
        converted only when its object changed.

        ``which``: 0 = all but the active bin (under the wedge), 1 = only the
        active bin (drawn over the wedge: the wedge always covers the bin
        being sampled, which would hide its highlight), 2 = all.
        """
        sw = p.sweep
        bins = sw[1]
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

    def _draw_runes(self, p, fb, t, y0):
        ids = p.runes
        if ids is None:
            return
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
        scr = self._scr
        sub = self._sub
        rim = -1
        if scr == S_SCANNING and (sub == "ready" or sub is None):
            flat = p.top_text not in WARN_TOP and not (p.sweep is not None and p.sweep[3])
            rim = PROX[6] if flat else WARN
        core = CORE_V if (self.g == G_GLOW and S_FAR <= scr <= S_HOT) else 0
        self.field.build(t, core, rim, SAVER_VMAX if self._saver else V7,
                         SUN_LIFT if p.sun else 0)

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
        if display is None:
            self._dark = True
            return ev
        if self._dark:
            self._dark = False
            self._snap(p, t)
        self._plan(p, t)
        self._palette(p, t)
        pal = self.field.pal
        arr = self.field.pal_arr
        m = self.map
        buf = self.buf
        fb = self.fb
        for s in range(NS):
            y0 = s * SH
            m.blit(y0, pal, arr, buf, fb)
            self._strip(p, t, y0, fb)
            display.push_strip(y0, SH, buf)
        return ev
