"""Tide (ui-spec §4A): water fills the dish, and how full it is is the distance.

What it draws
-------------

The water surface sits at a fraction of the screen height set by the readout
band (``THEME_PARAMS["tide"]["levels"]``: ``<3`` 0.86, ``~5`` 0.74, ``~10``
0.62, ``~20`` 0.50, ``~40`` 0.38, ``60+`` 0.26); FOUND ``level_found`` 0.92;
listening with no band ``level_listen`` 0.20; otherwise the last level. It
eases toward a new level with an ``ease_ms`` (450 ms) time constant, so a band
change is one quick step, never a drift. With no last level (a fresh
renderer on a screen without a band, such as SCANNING) it starts at the
farther band of the zone, or at the listening level when there is no zone.

- *Live*: two sine waves of amplitude ``wave_amp_px`` 1.5 + 1.1·z and
  0.6 + 0.5·z px, the first moving right at ``wave_px_s`` 14 + 12·z px/s,
  the second left at 17/21 of that (the mockup's wave pair); on every beat (the
  field's ring spawn, ``beat_age``) a swell of ``swell_px`` 5 + 3·z px rises in
  the middle (a Gaussian, sigma 34 px) and falls back with ``swell_ms`` 240 ms;
  ``bubbles`` 2 + 2·z bubbles rise at ``bubble_px_s`` 22 + 12·z px/s (one
  that rises more than 4 px in a frame, as in HOT at 10 fps, draws a 1 px
  trail at half its level down to where it was: §4A rule 5). A ghost
  beat (its ring spawned with ``ring_live`` false) swells at 0.6x
  (``GHOST_AMP_SCALE``, as a ghost ring) and its foam is grey. ``I`` sets
  the water's brightness (0.55 + 0.45·I), the foam (5.6 + 1.4·I) and the
  bubbles (5.4 + I).
- *Listening*: calm water (waves 0.6 / 0.3 px at 6 px/s) and a drop that
  falls from the top every ``drip_ms`` (3 s), accelerating for 0.6 of the
  cycle (drawn as a streak from where it was on the last frame, so it never
  jumps), then two droplets hop off the surface for 600 ms. Drops alternate
  between x 28 and 211, in the dish's dark margin, so neither the lens
  (r 44-95 in the listening screens) nor the word pill or band readout hides
  the fall or the landing. LINK-LOST's grey comes from the field's ramp
  crossfade.
- *Still*: waves 0.8 / 0.4 px at 8 px/s, no bubbles.
- *Scan*: calm water; its brightness is the live mirror (I = 0.2 + 0.6·I).
- *Found*: gold, glassy water (0.5 / 0.3 px at 5 px/s) at 0.92 after a
  settling swell (14 px, 380 ms, the first 1.5 s of FOUND, not after a
  wake), with a slow shimmer (0.8 levels, cos(y / 2.6), breathing over 2.4 s)
  that fades out over ``shimmer_band_px`` (24 px) under the surface.
- Every moment: the lens (iris disc in ``THEME_IRIS["tide"]`` and its rim),
  the core dot and the PAIRING calibrate fill, the same discs as
  ``Theme.draw_discs`` (see below for how they are drawn). Bubbles and the
  drop pass behind the lens: they are not drawn while they touch it. MENU
  freezes everything (own clocks advance by ``dt``, which is 0 there; the
  swell reads the field's held beat) and dims it through the palette.

How it stays cheap
------------------

- The water body is a static GS8 index map, ``zone * 60 + y // 4``: 60 row
  groups of 4 rows by 4 vignette zones (gains 1.0, 0.7, 0.4, 0.15 following
  ``tokens.field.vignette_stops``, ordered-dithered between zones with a 4x4
  Bayer pattern so the dish darkens smoothly toward the corners). It is
  symmetric left-right, so on the watch the field's viper ``blit_kernel``
  draws it from two 120x120 half maps (rows 0-119, and rows 239-120 flipped,
  since a 60-row band never straddles row 120), once it matches the framebuf
  palette blit on the real buffers; elsewhere it is a framebuf palette blit.
- The 256-entry palette holds the air (0.28, one colour, not vignetted)
  for the row groups above the wave band (the split: the group just above
  this frame's highest crest) and the water gradient
  (1 + 3.4·e^(-d/70))·(0.55 + 0.45·I) x zone gain by depth d below the eased
  surface row for the rest. It is rebuilt (240 entries of plain Python) only
  when the surface row, the brightness step (I in 1/16 steps), the ramp mix,
  the menu dim, the saver cap or sun mode changes; otherwise only the groups
  that cross a moved split are rewritten, and in FOUND the shimmer's 7 row
  groups.
- The moving surface is drawn on the whole frame (``layer``): one filled
  polygon of air from the split row down to the wave (21 vertices, 12 px
  apart, so it spans only the few rows of the wave band), then the foam as a
  2 px polygon outline (the wave line and the same line one row up, closed at
  the screen edges) in three tiers (full width at 0.45x, inside the chord of
  r 112 at 0.72x, inside r 96 at 1x; the inner tiers through column-clipped
  FrameBuffer views of the frame, so one vertex array serves all three). An
  outline costs its length; a filled polygon costs rows x vertices, which is
  why the foam is not filled. Bubbles are filled ellipses, the drop a 1 px
  streak and a 2x3 px ellipse.
- The disc stack (calibrate fill ring and disc, rim, iris, core dot) is
  burned into the index map as palette entries 250-254 whenever a radius
  changes (the map is restored from a pristine copy first; on the half maps
  one quadrant ellipse per disc serves both, since the bottom half map is
  stored flipped), so the band blit draws it like the water and a colour
  change is a palette write. ``draw`` repaints it only over the rows the air
  polygon and the foam just crossed, through preallocated 24-row views of
  the frame. Drawing the discs over the whole frame every frame instead cost
  12-35 us here per lens (two to four large discs, all their pixels written)
  and up to 90 us with the calibrate fill; on the watch every one of those
  pixels is a PSRAM write.
- Dirty strips: the surface band (min..max foam row of the previous and the
  current frame, full width), each bubble's and the drop's old and new box,
  the shimmer rows in FOUND, the lens box while the iris, fill, rim or core
  colour changes, and everything when the palette is rebuilt. After the
  first frames of a moment that is 1-3 of the 10 strips in the calm moments
  (scan, FOUND, pairing, listening) and 3-7 in the live ones (bubbles).

Numbers not in the spec (fine detail, from the approved mockup ``fTide``):
the calm / still / glassy wave sets, the wave numbers 1/38 and 1/17 rad/px,
the swell width, the air, water, foam and bubble levels, the bubble scatter
(x 46-194, a 3 px wobble at 2 rad/s, radius 2-3 px; bubble 5's start phase
moved off bubble 0's), the drop's 0.6 fall
fraction, the FOUND settling swell and the shimmer. Constants below. Not from
the mockup: the drop's x (the mockup's x 120 sits under the lens), its streak,
and the landing (the mockup's two surface dashes fade at the foam's own level,
so they barely show; two droplets hopping into the air read at a glance).
"""

import array
import math

from finder import tuning as T
from finder.compat import const
from ui.field import MAXR, N_IDX, RIM_MIN, SIN, V7, VIG, _aligned, blit_kernel
from ui.themes.base import (BH, M_FOUND, M_LISTEN, M_LIVE, M_SCAN, M_STILL, S_MENU, SH, W, WAKE_MS,
                            Ramps, Theme, _disc, framebuf)

P = T.THEME_PARAMS["tide"]


def _q8(x):
    """Non-negative token level / px (float) -> Q8 int (import time only)."""
    return int(x * 256 + 0.5)


# ---- level: surface row from the top, Q8 px ----------------------------------------
def _yq(level):
    return int(W * (1.0 - level) * 256 + 0.5)


LEVEL_Y = {b: _yq(lv) for b, lv in P["levels"].items()}
BAND_Y = tuple(LEVEL_Y[b] for b in T.BAND_LABELS)
Y_FOUND = _yq(P["level_found"])
Y_LISTEN = _yq(P["level_listen"])
EASE_MS = P["ease_ms"]
# 1 - e^(-dt/450) per ms of frame time (dt <= WAKE_MS; a longer gap is a wake: snap)
EASE_K = array.array("H", [int(256 * (1.0 - math.exp(-d / EASE_MS)) + 0.5)
                           for d in range(WAKE_MS + 1)])
SNAP_Q8 = const(96)          # within 3/8 px of the target: snap, so the easing tail ends

# ---- waves, swell, bubbles, drip (z = zone 0..3) -----------------------------------
_A1, _A2 = P["wave_amp_px"]
A1Q = tuple(_q8(_A1[0] + _A1[1] * z) for z in range(4))
A2Q = tuple(_q8(_A2[0] + _A2[1] * z) for z in range(4))
WAVE_V = tuple(P["wave_px_s"][0] + P["wave_px_s"][1] * z for z in range(4))
SWELL_Q = tuple(_q8(P["swell_px"][0] + P["swell_px"][1] * z) for z in range(4))
SWELL_MS = P["swell_ms"]
SWX = array.array("H", [int(256 * math.exp(-k * 8 / SWELL_MS) + 0.5) for k in range(256)])
GHOST_Q = _q8(T.GHOST_AMP_SCALE)             # a ghost beat swells like a ghost ring
BUB_N = tuple(P["bubbles"][0] + P["bubbles"][1] * z for z in range(4))
BUB_V = tuple(P["bubble_px_s"][0] + P["bubble_px_s"][1] * z for z in range(4))
DRIP_MS = P["drip_ms"]
SHIM_PX = P["shimmer_band_px"]

# mockup fine detail (fTide): wave sets (A1, A2 Q8 px, speed px/s) per calm moment
CALM = (_q8(0.6), _q8(0.3), 6)               # listening, scan
STILL = (_q8(0.8), _q8(0.4), 8)
GLASS = (_q8(0.5), _q8(0.3), 5)              # FOUND
FOUND_SWELL = _q8(14.0)                      # FOUND settles: 14 px, e^(-t/380 ms), 1.5 s
FOUND_SWELL_MS = 380
FOUND_SWELL_END = const(1500)
FSW = array.array("H", [int(256 * math.exp(-k * 16 / FOUND_SWELL_MS) + 0.5) for k in range(94)])
AIR_V = _q8(0.28)                            # air above the water
BR_A = _q8(0.55)                             # water brightness 0.55 + 0.45 I
BR_B = _q8(0.45)
FOAM_A = _q8(5.6)                            # foam 5.6 + 1.4 I
FOAM_B = _q8(1.4)
BUB_A = _q8(5.4)                             # bubbles 5.4 + I
BTR_Q = _q8(0.5)                             # a bubble's trail at half its level
STREAK_PX = const(4)                         # §4A rule 5: a longer step a frame gets a trail
DROP_V = _q8(6.0)                            # the drop's head; its streak 3.5
TAIL_V = _q8(3.5)
SCAN_I0 = _q8(0.2)                           # scan: I = 0.2 + 0.6 * mirror
SCAN_I1 = _q8(0.6)
SHIM_AMP = _q8(0.8)                          # FOUND shimmer, levels
SHIM_MS = T.STANDING_PERIOD_MS               # 2400 ms (mockup: 2.6 rad/s = 2417 ms)
IQ_HYST = const(12)                          # brightness steps of 1/16 I, +-12/256 hysteresis

# wave geometry: vertices every 12 px; wave 1 k = 1/38 rad/px moving right at v,
# wave 2 k = 1/17 rad/px moving left at 17/21 v (phase rate v/21 rad/s), phase 1.3 rad
STEP = const(12)
NV = const(21)                               # x = 0, 12, ..., 228, 239
VX = tuple(min(W - 1, i * STEP) for i in range(NV))
_D1 = 180.0 / (38 * math.pi)                 # deg per px
_D2 = 180.0 / (17 * math.pi)
K1 = int(STEP * _D1 * 256 + 0.5)             # Q8 deg per vertex
K2 = int(STEP * _D2 * 256 + 0.5)
R1 = int(_D1 * 256 + 0.5)                    # Q8 deg per px of travel
R2 = int(180.0 / (21 * math.pi) * 256 + 0.5)
PH2_0 = int(math.degrees(1.3) * 256)
PH_WRAP = const(92160)                       # 360 deg, Q8
# four turns of SIN, so a vertex's phase needs no "% 360" (phases stay below 360 deg + 20 steps)
SIN4 = array.array("h", list(SIN) * 4)
assert 360 + (((NV - 1) * (K1 if K1 > K2 else K2)) >> 8) < len(SIN4)
SWELL_SIG = 34.0                             # swell Gaussian width, px
GW = array.array("H", [int(256 * math.exp(-((VX[i] - 119.5) / SWELL_SIG) ** 2) + 0.5)
                       for i in range(NV)])

# the index map: zone * NG + row group
G = const(4)                                 # rows per palette group
NG = const(60)
NZ = const(4)
GAIN = (256, 179, 102, 38)                   # zone gains 1.0, 0.7, 0.4, 0.15 (vignette stops)
BAYER = bytes((0, 8, 2, 10, 12, 4, 14, 6, 3, 11, 1, 9, 15, 7, 13, 5))


def _zpos16(g):
    """Vignette gain (Q8) -> zone position x 16 (0..48), linear between GAIN."""
    for k in range(NZ - 1):
        if g >= GAIN[k + 1]:
            f = (GAIN[k] - g) / (GAIN[k] - GAIN[k + 1])
            return int(16 * (k + max(0.0, f)) + 0.5)
    return 16 * (NZ - 1)


ZQ = bytes([_zpos16(VIG[i]) for i in range(N_IDX)])
# water level by depth below the surface (Q8): 1 + 3.4 e^(-d/70), d 0..240
WL = array.array("H", [int(256 * (1.0 + 3.4 * math.exp(-d / 70.0)) + 0.5) for d in range(241)])
# shimmer pattern per row group: cos(y / 2.6) at the group's centre row, Q8
SHC = array.array("h", [int(round(256 * math.cos((g * G + 1.5) / 2.6))) for g in range(NG)])

# foam tiers: full width x 0.45, inside the chord of r 112 x 0.72, inside r 96 x 1
FOAM_R = (112, 96)
FOAM_T = (_q8(0.45), _q8(0.72), 256)


def _clip_k(rad, y):
    """Clip window index k (columns 4k .. 239 - 4k) inside the chord of radius
    ``rad`` on row ``y``; 255 when the row misses the circle."""
    dy = y - 119
    h2 = rad * rad - dy * dy
    if h2 <= 0:
        return 255
    k = (120 - int(math.sqrt(h2)) + 3) // 4
    return 255 if k > 29 else k


KCLIP = tuple(bytes([_clip_k(rad, y) for y in range(W)]) for rad in FOAM_R)

# bubbles: fixed scatter (the mockup's hash): x, start phase (Q8 of the water height)
NBUB = const(8)
BX = (68, 150, 101, 186, 136, 73, 109, 82)
BH0 = (198, 61, 247, 104, 234, 140, 209, 20)  # bubble 5: the hash's 192 rode on bubble 0
WOB_PX = const(3)
WOB_RATE = const(29)                         # 2 rad/s = 0.1146 deg/ms, Q8
# drip
DRIP_X = (28, 211)                           # alternate sides (symmetric about 119.5)
FALL_MS = DRIP_MS * 3 // 5
SPLASH_MS = const(600)
SPLASH_DX = const(24)                        # droplets fly 24 px out, hop 6 px high
SPLASH_HOP = const(6)

# boxes (x0, y0, x1, y1) per frame: bubbles 0..7, the drop 8, droplets 9, 10
NBOX = const(11)
B_DROP = const(8)

# the disc stack burned into the map (Theme.draw_discs' discs, in its order):
# palette entries I_DISC + (fill ring, fill, rim, iris, core dot)
I_DISC = const(250)
IRIS_C = T.THEME_IRIS["tide"]
NL = const(5)
HV = const(24)                               # rows of each lens-redraw view


class Tide(Theme):
    name = "tide"
    layer = True

    def __init__(self, r):
        Theme.__init__(self, r)
        self.ramps = Ramps("tide")
        self.pal_arr = array.array("H", [0] * 256)
        self.pal = (framebuf.FrameBuffer(self.pal_arr, 256, 1, framebuf.RGB565)
                    if framebuf is not None else None)
        self.kern = None
        self.kind = None
        self._build_map()
        # pristine copies of the map the disc stack is burned into (restored
        # before each new burn), and the row views the stack is redrawn
        # through over the surface band
        self.lay = array.array("h", [0] * NL)
        self.lr = 0
        self.qt_fb = self.qb_fb = None
        self.map0 = None
        self.rows = [None] * NG
        if framebuf is not None:
            if self.kern is not None:
                self.qt0 = bytes(self.qt)
                self.qb0 = bytes(self.qb)
                self.qt_fb = framebuf.FrameBuffer(self.qt, 120, 120, framebuf.GS8)
                self.qb_fb = framebuf.FrameBuffer(self.qb, 120, 120, framebuf.GS8)
            else:
                self.map0 = bytes(self.idx)
            mv = memoryview(r.buf)
            for g in range(NG):
                y0 = g * G
                self.rows[g] = framebuf.FrameBuffer(mv[y0 * 2 * W:], W, min(HV, W - y0),
                                                    framebuf.RGB565)
        # polygons: x preset, y written per frame
        air = array.array("h", [0] * (2 * (NV + 2)))
        air[0] = 0
        air[2] = W - 1
        for i in range(NV):
            air[2 * (NV + 1 - i)] = VX[i]
        self.air = air
        rib = array.array("h", [0] * (4 * NV))
        for i in range(NV):
            rib[2 * i] = VX[i]
            rib[2 * (2 * NV - 1 - i)] = VX[i]
        self.rib = rib
        self.box = array.array("h", [0] * (4 * NBOX))
        self.pbox = array.array("h", [0] * (4 * NBOX))
        self.bpos = [0] * NBUB
        self.btl = bytearray(NBUB)              # trail length under each bubble, px
        self.disc = array.array("i", [0] * 5)
        self.clips = [None] * 30
        if framebuf is not None:
            mv = memoryview(r.buf)
            for k in range(1, 30):
                self.clips[k] = framebuf.FrameBuffer(mv[8 * k:], W - 8 * k, W, framebuf.RGB565, W)
        self._fresh()

    def _fresh(self):
        """Start state (a new theme or a renderer reset): no level yet."""
        self.mom = -1
        self.z = 0
        self.iqq = -1
        self.yq = -1
        self.ypx = -1
        self.ph1 = 0
        self.ph2 = PH2_0
        self.wob = 0
        self.dclk = 0
        self.dpc = -1               # drip phase on the last frame (-1: not falling)
        self.dpy = 0
        self.sclk = 0
        self.fage = FOUND_SWELL_END
        self.ls = None              # last spawn whose ring was looked up
        self.beat_out = 0
        self.ghost = 0
        for j in range(NBUB):
            self.bpos[j] = BH0[j] * 120         # Q8 px: a fraction of ~120 px of water
        self.pb0 = 0
        self.pb1 = -1
        self.nb = 0
        self.kin = 255
        self.kmid = 255
        self.sig_y = -1
        self.sig_br = -1
        self.sig_dim = -1
        self.sig_vmax = -1
        self.sig_lift = -1
        self.sig_shim = False
        self.shq = -1
        ds = self.disc
        for i in range(5):
            ds[i] = -1                  # iris, fill r, rim, core and fill colours drawn
        self.disc_r = 0
        for i in range(NL):
            self.lay[i] = -1            # burn the stack (from the pristine map) on the next frame
        self.lr = 0
        self.fv_q = -1
        self.c_ring = 0
        self.c_fill = 0
        self.ymax = 0
        self.split = 0
        self.br = 0
        self.ghost_now = 0
        self.dsx = 0                # the drop: x, head y, streak top
        self.dsy = 0
        self.dst = 0
        self.drip = False
        self.sw = 0
        for i in range(0, 4 * NBOX, 4):
            self.box[i + 2] = -1
            self.box[i + 3] = -1
            self.pbox[i + 2] = -1
            self.pbox[i + 3] = -1
        self.core = 0
        self.rim_q = -1
        self.rim_c = 0
        self.core_c = 0
        self.c_air = 0
        self.c_foam0 = self.c_foam1 = self.c_foam2 = 0
        self.c_bub = 0
        self.c_btr = 0
        self.c_drop = 0
        self.c_tail = 0
        self.c_spl = 0

    def reset(self):
        Theme.reset(self)
        self.ramps.snap()
        self._fresh()

    # ---- the index map --------------------------------------------------------------
    def _build_map(self):
        """GS8 map ``zone * NG + y // G`` (zone: the vignette, Bayer-dithered),
        left-right symmetric, with its two half maps for the viper kernel."""
        rm = self.r.map.idx
        full = bytearray(W * W)
        kern = blit_kernel if framebuf is not None else None
        qt = bytearray(120 * 120) if kern is not None else None
        qb = bytearray(120 * 120) if kern is not None else None
        zq = ZQ
        bay = BAYER
        for qy in range(120):
            gt = qy >> 2
            gb = (239 - qy) >> 2
            rt = qy * W + 120
            rb = (239 - qy) * W + 120
            bq = (qy & 3) << 2
            o = qy * 120
            for qx in range(120):
                z = (zq[rm[rt + qx]] + bay[bq + (qx & 3)]) >> 4
                it = z * NG + gt
                ib = z * NG + gb
                full[rt + qx] = it
                full[rt - 1 - qx] = it
                full[rb + qx] = ib
                full[rb - 1 - qx] = ib
                if qt is not None:
                    qt[o + qx] = it
                    qb[o + qx] = ib
        self.idx = full
        self.qt = qt
        self.qb = qb
        if framebuf is None:
            return
        self.map_fb = framebuf.FrameBuffer(full, W, W, framebuf.GS8)
        self.kind = "framebuf"
        if kern is not None:
            ok = _aligned(qt) and _aligned(qb)
            for b in self.r.bands:
                ok = ok and _aligned(b)
            if ok and self.agrees(kern):
                self.kern = kern
                self.kind = "viper"
            else:
                self.qt = self.qb = None
                self.kind = "framebuf (kernel self-check failed)"

    def agrees(self, kern):
        """True if ``kern`` draws all four bands exactly as the framebuf
        palette blit, through a palette of distinct colours, into the
        renderer's first band buffer (left dirty: the next frame redraws it)."""
        arr = array.array("H", [(i * 4099 + 0x1235) & 0xFFFF for i in range(256)])
        pal = framebuf.FrameBuffer(arr, 256, 1, framebuf.RGB565)
        buf = self.r.bands[0]
        fb = self.r.band_fbs[0]
        for y0 in range(0, W, BH):
            fb.blit(self.map_fb, 0, -y0, -1, pal)
            want = bytes(buf)
            fb.fill(0x5A5A)
            kern(buf, self.qt if y0 < 120 else self.qb, arr, y0, BH)
            if bytes(buf) != want:
                return False
        return True

    # ---- palette --------------------------------------------------------------------
    def _rows(self, g0, g1, vmax, dim, lift):
        """Palette entries of row groups g0..g1-1 (every zone)."""
        c = self.ramps.mix.c
        pal = self.pal_arr
        air = self.c_air
        ys = self.ypx
        split = self.split
        br = self.br
        shq = self.shq if self.sig_shim else 0
        for g in range(g0, g1):
            if g < split:
                pal[g] = air
                pal[NG + g] = air
                pal[2 * NG + g] = air
                pal[3 * NG + g] = air
                continue
            d = g * G + 2 - ys
            if d < 0:
                d = 0
            elif d > 240:
                d = 240
            v = (WL[d] * br) >> 8
            if shq and d < SHIM_PX:
                v += (((shq * SHC[g]) >> 8) * (SHIM_PX - d)) // SHIM_PX
                if v < 0:
                    v = 0
            for z in range(NZ):
                vz = (v * GAIN[z]) >> 8
                if vz > vmax:
                    vz = vmax
                if dim != 256:
                    vz = (vz * dim) >> 8
                j = ((vz * 9 + 128) >> 8) + lift
                pal[z * NG + g] = c[63 if j > 63 else j]

    # ---- per frame ------------------------------------------------------------------
    def _beat(self):
        """Look up the ring of the field's last spawn once: outward? ghost?"""
        f = self.f
        ls = f.last_spawn
        if ls == self.ls:
            return
        self.ls = ls
        self.beat_out = 0
        self.ghost = 0
        for k in range(MAXR):
            if f.r_on[k] and f.r_t0[k] == ls and f.r_v[k] > 0:
                self.beat_out = 1
                self.ghost = f.r_ghost[k]
                return


    def _surf(self, x):
        """Foam top row at column ``x`` (this frame's vertex at or left of it)."""
        i = x // STEP
        if i < 0:
            i = 0
        elif i > NV - 1:
            i = NV - 1
        return self.rib[2 * i + 1]

    def _colours(self, vmax, dim, lift):
        """Foam tiers, bubbles and the drop in the mixed ramp (grey foam on a ghost beat)."""
        ramps = self.ramps
        iqq = self.iqq
        foam = FOAM_A + ((FOAM_B * iqq) >> 8)
        if self.ghost_now:
            self.c_foam0 = ramps.grey_color((foam * FOAM_T[0]) >> 8, vmax, dim, lift)
            self.c_foam1 = ramps.grey_color((foam * FOAM_T[1]) >> 8, vmax, dim, lift)
            self.c_foam2 = ramps.grey_color(foam, vmax, dim, lift)
        else:
            self.c_foam0 = ramps.color((foam * FOAM_T[0]) >> 8, vmax, dim, lift)
            self.c_foam1 = ramps.color((foam * FOAM_T[1]) >> 8, vmax, dim, lift)
            self.c_foam2 = ramps.color(foam, vmax, dim, lift)
        self.c_bub = ramps.color(BUB_A + iqq, vmax, dim, lift)
        self.c_btr = ramps.color(((BUB_A + iqq) * BTR_Q) >> 8, vmax, dim, lift)
        self.c_drop = ramps.color(DROP_V, vmax, dim, lift)
        self.c_tail = ramps.color(TAIL_V, vmax, dim, lift)

    def _mark2(self, o):
        """Mark box ``o`` of this frame and of the last one (as one box when
        they are close), then keep this frame's as the last."""
        box = self.box
        pb = self.pbox
        a = box[o + 3] >= 0
        b = pb[o + 3] >= 0
        if not (a or b):
            return
        if a and b and box[o + 1] - pb[o + 3] < SH and pb[o + 1] - box[o + 3] < SH:
            self.mark(box[o] if box[o] < pb[o] else pb[o],
                      box[o + 1] if box[o + 1] < pb[o + 1] else pb[o + 1],
                      box[o + 2] if box[o + 2] > pb[o + 2] else pb[o + 2],
                      box[o + 3] if box[o + 3] > pb[o + 3] else pb[o + 3])
        else:
            if a:
                self.mark(box[o], box[o + 1], box[o + 2], box[o + 3])
            if b:
                self.mark(pb[o], pb[o + 1], pb[o + 2], pb[o + 3])
        pb[o] = box[o]
        pb[o + 1] = box[o + 1]
        pb[o + 2] = box[o + 2]
        pb[o + 3] = box[o + 3]

    def build(self, p, t, core, rim, vmax, lift):
        dt = self.clock(p, t)
        wake = self.wake
        f = self.f
        ramps = self.ramps
        mixing = ramps.dur > 0 or ramps.ramp != f.ramp
        ramps.follow(f, t)
        menu = self.r._scr == S_MENU
        # moment, zone and brightness step (held in MENU)
        if menu and self.mom >= 0:
            mom = self.mom
            z = self.z
        else:
            mom = self.moment(p)
            z = self.zone(p)
            iq = self.iq()
            if mom == M_SCAN:
                iq = SCAN_I0 + ((SCAN_I1 * iq) >> 8)
            q = self.iqq
            if wake or q < 0 or iq > q + IQ_HYST or iq < q - IQ_HYST:
                self.iqq = ((iq + 8) >> 4) << 4
            if mom != self.mom:
                if mom == M_FOUND:
                    self.fage = FOUND_SWELL_END if (wake or self.mom < 0) else 0
                self.dpc = -1
            self.mom = mom
            self.z = z
        # level target (§4A Tide) and its easing
        tq = -1
        if not menu:
            b = p.dist_band
            if b is not None:
                tq = LEVEL_Y.get(b, -1)
            elif mom == M_FOUND:
                tq = Y_FOUND
            elif mom == M_LISTEN:
                tq = Y_LISTEN
        if tq < 0 and self.yq < 0:
            zz = p.zone
            tq = BAND_Y[T.ZONE_BANDS[zz][1]] if (zz is not None and 0 <= zz <= 3) else Y_LISTEN
        if tq >= 0:
            if wake or self.yq < 0:
                self.yq = tq
            elif dt:
                d = tq - self.yq
                if -SNAP_Q8 < d < SNAP_Q8:
                    self.yq = tq
                else:
                    self.yq += (d * EASE_K[dt]) >> 8
        ypx = (self.yq + 128) >> 8
        self.ypx = ypx
        # waves per moment
        sw = 0
        nb = 0
        bv = 0
        gh = 0
        if mom == M_LIVE:
            a1 = A1Q[z]
            a2 = A2Q[z]
            spd = WAVE_V[z]
            self._beat()
            age = self.beat_age(t)
            if self.beat_out and age < 2048:
                sw = (SWELL_Q[z] * SWX[age >> 3]) >> 8
                if self.ghost:
                    sw = (sw * GHOST_Q) >> 8
            gh = self.ghost
            nb = BUB_N[z]
            bv = BUB_V[z]
        elif mom == M_FOUND:
            a1, a2, spd = GLASS
            fa = self.fage
            if fa < FOUND_SWELL_END:
                sw = (FOUND_SWELL * FSW[fa >> 4]) >> 8
                fa += dt
                self.fage = fa if fa < FOUND_SWELL_END else FOUND_SWELL_END
        elif mom == M_STILL:
            a1, a2, spd = STILL
        else:
            a1, a2, spd = CALM
        self.sw = sw
        if dt:
            self.ph1 = (self.ph1 - (dt * spd * R1) // 1000) % PH_WRAP
            self.ph2 = (self.ph2 + (dt * spd * R2) // 1000) % PH_WRAP
        # surface: the air polygon's lower edge and the foam outline (rows s-1, s)
        yq = ypx << 8
        p1 = self.ph1
        p2 = self.ph2
        air = self.air
        rib = self.rib
        ymin = 999
        ymax = -999
        sn = SIN4
        gw = GW
        k1 = K1
        k2 = K2
        yq += 128
        ia = 2 * NV + 3                         # air: right to left after the two top corners
        ib = 4 * NV - 1                         # foam: s - 1 left to right, then s right to left
        for i in range(NV):
            s = (yq + ((a1 * sn[p1 >> 8]) >> 14) + ((a2 * sn[p2 >> 8]) >> 14)
                 - ((sw * gw[i]) >> 8)) >> 8
            if s < 1:
                s = 1
            if s < ymin:
                ymin = s
            if s > ymax:
                ymax = s
            air[ia] = s
            rib[2 * i + 1] = s - 1
            rib[ib] = s
            ia -= 2
            ib -= 2
            p1 += k1
            p2 += k2
        # palette rows above the band are air; the air polygon starts there
        split = (ymin - 2) // G
        if split < 0:
            split = 0
        air[1] = split * G
        air[3] = split * G
        self.ymax = ymax
        # palette: rebuilt only when it changes as a whole
        dim = f.dim
        br = BR_A + ((BR_B * self.iqq) >> 8)
        self.br = br
        shim = mom == M_FOUND
        shq = 0
        if shim:
            self.sclk = (self.sclk + dt) % SHIM_MS
            shq = (SHIM_AMP * (128 + ((128 * SIN[self.sclk * 360 // SHIM_MS]) >> 14))) >> 8
        glob = (wake or mixing or ypx != self.sig_y or br != self.sig_br or dim != self.sig_dim or
                vmax != self.sig_vmax or lift != self.sig_lift or shim != self.sig_shim)
        if glob:
            self.everything()
            self.sig_y = ypx
            self.sig_br = br
            self.sig_dim = dim
            self.sig_vmax = vmax
            self.sig_lift = lift
            self.sig_shim = shim
            self.shq = shq
            self.split = split
            self.c_air = ramps.color(AIR_V, vmax, dim, lift)
            self._rows(0, NG, vmax, dim, lift)
            pal = self.pal_arr
            for i in range(NZ * NG, I_DISC):
                pal[i] = self.c_air
            self.ghost_now = gh
            self._colours(vmax, dim, lift)
        else:
            self.clear_dirty()
            o = self.split
            if split != o:
                self.split = split
                g0 = split if split < o else o
                g1 = o if split < o else split
                self._rows(g0, g1, vmax, dim, lift)
                self.mark(0, g0 * G, W - 1, g1 * G - 1)
            if shim and shq != self.shq:
                self.shq = shq
                g0 = ypx // G
                g1 = (ypx + SHIM_PX) // G + 1
                if g1 > NG:
                    g1 = NG
                self._rows(g0, g1, vmax, dim, lift)
                self.mark(0, g0 * G, W - 1, g1 * G - 1)
            if gh != self.ghost_now:
                self.ghost_now = gh
                self._colours(vmax, dim, lift)
        # lens (iris disc and rim), core dot, calibrate fill: Theme.draw_discs'
        # discs, burned into the map (palette entries I_DISC..) so the blit
        # draws them; draw() redraws them only over the surface band. Their
        # box is marked while they change.
        iris = f.iris
        fr = f.fill_r if f.fill_v else 0
        crim = 0
        if iris > 0:
            crim = rim
            if rim < 0:
                q = f.fl + f.gl
                q = V7 if q > V7 else (RIM_MIN if q < RIM_MIN else q)
                if q != self.rim_q or glob:
                    self.rim_q = q
                    self.rim_c = ramps.color(q, vmax, dim, lift)
                crim = self.rim_c
        ccore = -1
        if core and iris == 0:
            if core != self.core or glob:
                self.core_c = ramps.color(core, vmax, dim, lift)
            ccore = self.core_c
        cfill = -1
        if fr:
            fv = f.fill_v
            if fv != self.fv_q or glob:
                self.fv_q = fv
                self.c_ring = ramps.color(fv + (fv >> 1), vmax, dim, lift)
                self.c_fill = ramps.color(fv, vmax, dim, lift)
            cfill = self.c_fill
        dr = iris + T.IRIS_RIM_PX if iris > 0 else (7 if ccore >= 0 else 0)
        if fr and fr + 3 > dr:
            dr = fr + 3
        ds = self.disc
        if (glob or ds[0] != iris or ds[1] != fr or ds[2] != crim or ds[3] != ccore or
                ds[4] != cfill):
            ds[0] = iris
            ds[1] = fr
            ds[2] = crim
            ds[3] = ccore
            ds[4] = cfill
            pal = self.pal_arr
            pal[I_DISC] = self.c_ring
            pal[I_DISC + 1] = self.c_fill
            pal[I_DISC + 2] = crim
            pal[I_DISC + 3] = IRIS_C
            pal[I_DISC + 4] = ccore if ccore >= 0 else 0
            m = dr if dr > self.disc_r else self.disc_r
            if m > 0:
                self.mark(119 - m, 119 - m, 120 + m, 120 + m)
            lay = self.lay
            r0 = fr + 3 if fr else 0
            r2 = iris + T.IRIS_RIM_PX if iris > 0 else 0
            r3 = iris if iris > 0 else 0
            r4 = 7 if ccore >= 0 else 0
            if lay[0] != r0 or lay[1] != fr or lay[2] != r2 or lay[3] != r3 or lay[4] != r4:
                lay[0] = r0
                lay[1] = fr
                lay[2] = r2
                lay[3] = r3
                lay[4] = r4
                self._burn()
        self.disc_r = dr
        self.lr = dr
        # surface band: this frame's and the last one's
        b0 = ymin - 3
        b1 = ymax + 3
        o = self.pb0
        q = self.pb1
        if q < o:
            self.mark(0, b0, W - 1, b1)
        elif o <= b1 + SH and q >= b0 - SH:
            self.mark(0, b0 if b0 < o else o, W - 1, b1 if b1 > q else q)
        else:
            self.mark(0, b0, W - 1, b1)
            self.mark(0, o, W - 1, q)
        self.pb0 = b0
        self.pb1 = b1
        kc = KCLIP
        self.kmid = kc[0][ypx] if 0 <= ypx < W else 255
        self.kin = kc[1][ypx] if 0 <= ypx < W else 255
        box = self.box
        pb = self.pbox
        # bubbles (live): rise from below the screen, hidden from the surface band up
        n = self.nb
        self.nb = nb
        if nb > n:
            n = nb
        if nb:
            cyc = (W + 6 - ypx) << 8
            bp = self.bpos
            if dt:
                adv = (bv * dt * 256) // 1000
                for j in range(nb):
                    ps = bp[j] + adv
                    if ps >= cyc:
                        ps %= cyc
                    bp[j] = ps
                self.wob = (self.wob + dt * WOB_RATE) % PH_WRAP
            wb = self.wob >> 8
            sb = ymax + 4
            lr = self.lr
            lim = 2 * lr + 2                    # bubbles hide while they touch the lens
        for j in range(n):
            o = 4 * j
            y1 = pb[o + 3]
            if j < nb:
                ps = bp[j]
                if ps >= cyc:
                    ps %= cyc
                rr = 2 + (j & 1)
                y = W + 2 - (ps >> 8)
                x = BX[j] + ((WOB_PX * sn[(wb + 57 * j) % 360]) >> 14)
                dx = 2 * x - 239
                dy = 2 * y - 239
                q = lim + 2 * rr
                if sb < y - rr < W and (lr <= 0 or dx * dx + dy * dy > q * q):
                    box[o] = x - rr
                    box[o + 1] = y - rr
                    box[o + 2] = x + rr
                    box[o + 3] = y + rr
                    tl = 0
                    # a bubble moves a few px a frame: one box covers old and new
                    # (the old one with its trail)
                    if y1 >= 0 and y - rr <= pb[o + 1] < y + rr + SH:
                        st = pb[o + 1] + rr - y         # risen since the last frame
                        if st > STREAK_PX:              # trail down to the old centre
                            tl = st - rr
                            dy += 2 * st
                            if lr > 0 and dx * dx + dy * dy <= lim * lim:
                                tl = 0
                        x0 = pb[o]
                        self.mark(x0 if x0 < x - rr else x - rr, y - rr,
                                  x0 + 2 * rr if x0 + 2 * rr > x + rr else x + rr, y1)
                    else:
                        self.mark(x - rr, y - rr, x + rr, y + rr)
                        if y1 >= 0:
                            self.mark(pb[o], pb[o + 1], pb[o + 2], y1)
                    self.btl[j] = tl
                    pb[o] = x - rr
                    pb[o + 1] = y - rr
                    pb[o + 2] = x + rr
                    pb[o + 3] = y + rr + tl
                    continue
            box[o + 3] = -1
            if y1 >= 0:
                self.mark(pb[o], pb[o + 1], pb[o + 2], y1)
                pb[o + 3] = -1
        # the drip (listening)
        o = 4 * B_DROP
        if mom == M_LISTEN or self.drip:
            self.drip = mom == M_LISTEN
            for i in range(o, 4 * NBOX, 4):
                box[i + 2] = -1
                box[i + 3] = -1
            if self.drip:
                self._drip(dt, vmax, dim, lift)
                for i in range(o, 4 * NBOX, 4):
                    if box[i + 3] >= 0 and not self._off_lens(box[i], box[i + 1], box[i + 2],
                                                              box[i + 3]):
                        box[i + 3] = -1
            self._mark2(o)
            self._mark2(o + 4)
            self._mark2(o + 8)
        self.core = core

    def _burn(self):
        """Burn the disc stack (radii ``lay``) into the map, from its pristine
        copy: on the half maps as their top-right quadrant (the kernel mirrors
        it; the bottom half map is flipped, so the same quadrant serves), or
        as Theme's four-quadrant disc on the full map."""
        lay = self.lay
        qt = self.qt_fb
        if qt is not None:
            self.qt[:] = self.qt0
            self.qb[:] = self.qb0
            qb = self.qb_fb
            for i in range(NL):
                r = lay[i] - 1
                if r >= 0:
                    qt.ellipse(0, 119, r, r, I_DISC + i, True, 1)
                    qb.ellipse(0, 119, r, r, I_DISC + i, True, 1)
        elif self.map0 is not None:
            self.idx[:] = self.map0
            for i in range(NL):
                if lay[i] > 0:
                    _disc(self.map_fb, lay[i], I_DISC + i)

    def _off_lens(self, x0, y0, x1, y1):
        """True if the box misses the disc stack (with a 1 px margin)."""
        r = self.lr
        if r <= 0:
            return True
        a = 2 * x0 - 239
        b = 239 - 2 * x1
        dx = a if a > 0 else (b if b > 0 else 0)
        a = 2 * y0 - 239
        b = 239 - 2 * y1
        dy = a if a > 0 else (b if b > 0 else 0)
        r = 2 * r + 2
        return dx * dx + dy * dy > r * r

    def _stack(self, fb, vy, h):
        """The disc stack on ``fb``, whose row 0 is frame row ``vy`` (``h`` rows)."""
        lay = self.lay
        pal = self.pal_arr
        top = vy < 120
        bot = vy + h > 120
        cy = 119 - vy
        for i in range(NL):
            r = lay[i] - 1
            if r >= 0:
                c = pal[I_DISC + i]
                if top:
                    fb.ellipse(120, cy, r, r, c, True, 1)
                    fb.ellipse(119, cy, r, r, c, True, 2)
                if bot:
                    fb.ellipse(119, cy + 1, r, r, c, True, 4)
                    fb.ellipse(120, cy + 1, r, r, c, True, 8)

    def _drip(self, dt, vmax, dim, lift):
        """Drop boxes for this frame: the falling drop (head and streak), or
        the two droplets after it lands."""
        box = self.box
        o = 4 * B_DROP
        c = (self.dclk + dt) % (2 * DRIP_MS)
        self.dclk = c
        side = 0
        if c >= DRIP_MS:
            side = 1
            c -= DRIP_MS
        x = DRIP_X[side]
        land = self._surf(x) - 3
        if c < FALL_MS:
            hy = (((land + 6) * c) // FALL_MS) * c // FALL_MS - 6
            py = self.dpy if 0 <= self.dpc <= c else hy
            self.dpc = c
            self.dpy = hy
            self.dsx = x
            self.dsy = hy
            self.dst = py
            box[o] = x - 2
            box[o + 1] = py if py < hy - 3 else hy - 3
            box[o + 2] = x + 2
            box[o + 3] = hy + 3
            return
        self.dpc = -1
        a = c - FALL_MS
        if a >= SPLASH_MS:
            return
        dx = 4 + (SPLASH_DX * a) // SPLASH_MS
        hop = (4 * SPLASH_HOP * a * (SPLASH_MS - a)) // (SPLASH_MS * SPLASH_MS)
        self.c_spl = self.ramps.color(DROP_V - (768 * a) // SPLASH_MS, vmax, dim, lift)
        for k in range(2):
            xd = x - dx if k == 0 else x + dx
            yd = self._surf(xd) - 1 - hop
            ob = o + 4 + 4 * k
            box[ob] = xd - 1
            box[ob + 1] = yd - 1
            box[ob + 2] = xd + 1
            box[ob + 3] = yd

    def blit(self, y0, buf, fb):
        k = self.kern
        if k is not None:
            k(buf, self.qt if y0 < 120 else self.qb, self.pal_arr, y0, BH)
            return
        fb.blit(self.map_fb, 0, -y0, -1, self.pal)

    def draw(self, fb):
        fb.poly(0, 0, self.air, self.c_air, True)
        rib = self.rib
        fb.poly(0, 0, rib, self.c_foam0, False)
        own = fb is self.r.fb
        k = self.kmid
        if k != 255:
            if k and own:
                self.clips[k].poly(-4 * k, 0, rib, self.c_foam1, False)
            else:
                fb.poly(0, 0, rib, self.c_foam1, False)
        k = self.kin
        if k != 255:
            if k and own:
                self.clips[k].poly(-4 * k, 0, rib, self.c_foam2, False)
            else:
                fb.poly(0, 0, rib, self.c_foam2, False)
        # the disc stack is in the map; redraw it where the surface band
        # crossed it (rows of the air polygon and the foam)
        r = self.lr
        if r > 0:
            a = self.air[1]
            b = self.ymax
            if a < 119 - r:
                a = 119 - r
            if b > 120 + r:
                b = 120 + r
            if a <= b:
                if own:
                    g = a >> 2
                    while g < NG and g * G <= b:
                        self._stack(self.rows[g], g * G, HV)
                        g += HV // G
                else:
                    self._stack(fb, 0, W)
        box = self.box
        cb = self.c_bub
        btl = self.btl
        for j in range(self.nb):
            o = 4 * j
            if box[o + 3] >= 0:
                rr = (box[o + 2] - box[o]) >> 1
                x = box[o] + rr
                fb.ellipse(x, box[o + 1] + rr, rr, rr, cb, True)
                if btl[j]:
                    fb.vline(x, box[o + 3] + 1, btl[j], self.c_btr)
        if self.drip:
            o = 4 * B_DROP
            if box[o + 3] >= 0:
                x = self.dsx
                hy = self.dsy
                py = self.dst
                if py < hy - 3:
                    fb.vline(x, py, hy - 3 - py, self.c_tail)
                fb.ellipse(x, hy, 2, 3, self.c_drop, True)
            for k in range(2):
                ob = o + 4 + 4 * k
                if box[ob + 3] >= 0:
                    fb.fill_rect(box[ob], box[ob + 1], 3, 2, self.c_spl)
