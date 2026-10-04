"""Warp (ui-spec §4A): stars stream out of the centre over a dark floor.

What it draws, per moment (``Theme.moment``):

- *Live* (FAR-HOT, PAIRING ``split``, SCANNING ``ready``): ``stars[z]``
  (18/30/46/70) stars fly out at ``rate[z]`` (0.45/0.65/0.95/1.45) trips a
  second, each scaled by the star's own period factor. On every live ring
  spawn (the beat) the flight speeds up x(1 + 1.3 e^(-t/170 ms)), so the
  heartbeat and the surge land on the same frame. A ghost beat (``ring_live``
  false) has no surge (no packet, no push) and draws the stars grey at 0.6 of
  their brightness (the ghost ring scale, ``ui.field.GHOST_AMP``) until the
  next live beat.
- *Listening* (SEARCHING, LINK-LOST, PAIRING ``looking``): ``listen_stars``
  (14) drift back toward the centre at ``listen_rate`` (-0.22).
- *Still* (PAIRING ``seen`` / ``confirmed`` / ``calibrate``): ``still_stars``
  (30) hold their places and twinkle.
- *Scan* (SCANNING sweep / result, the DIRECTION turn pacer): ``scan_stars``
  (24) hold still over the live-mirror halo (glow 1 + 5 I, §5.7).
- *Found*: ``found_stars`` (56) slow from ``found_rate`` (1.6) as
  e^(-t/``found_ms``) (400 ms) to a stop at t = 4 x 400 ms (rate under 0.03
  trips a second), then twinkle; gold through the field's hue crossfade.

Each star is a streak from where it was on the last drawn frame to where it is
now, a dim tail (the 60 % nearer where it was) and a bright head, so speed
reads as blur, not as jumps; a star that wraps (leaves the screen and starts
again at the centre, or the reverse when listening) restarts with no streak.
Stars never draw inside the lens and its rim (nor inside the PAIRING calibrate
fill disc): a streak is clipped CLIP_PX (2 px) outside them, and a dot only
grows to 2x2 or a cross 2 px further out. A star is never drawn darker than
the base layer under it (its level is at least the floor and glow at the inner
end of each segment plus 0.5 level for the head, 0.25 for the tail), so stars
come out of the centre glow instead of showing as dark marks.

The base layer is the field's palette kernel in Warp's colours
(``Radial("warp")`` through the renderer's ring map): floor, centre glow (the
field's crossfaded glow radius, ``glow_r_px``), vignette, the lens in
``THEME_IRIS["warp"]`` with its rim, the core dot and the calibrate fill. No
rings and no FOUND standing wave. The floor and glow levels below crossfade
over 600 ms on a moment or zone change (the §4 level crossfade, EASE_IOC), the
floor under the §4 flash limit. MENU holds the moment, the levels and every
star (``dt`` is 0), so only the menu dim moves.

Star count changes never pop: the stars are one fixed list of 70 and a moment
shows stars 0..n-1, so a zone change only adds or drops stars at the end of
the list. While the stars move, a star switches on or off only when it wraps
(it appears at the centre or the edge and leaves the same way); while they
are held (still, scan, a stopped FOUND) it fades over FADE_MS. A wake (first
frame, screen back on, theme switch) shows every star of the moment in place
at once (§8: no intro).

Cost (ui-spec §4A rule 7): the base is the field's own two viper kernels (the
170-entry palette and the mirrored quadrant blit). The stars are one pass of
``star_kernel`` over the 70 stars (flight, count fades, clipping, colours,
changed boxes and up to three segments a star), compiled with
``@micropython.viper`` where the port has it; there it also draws the
segments straight into the frame with framebuf's own line algorithm. Both are
used only after ``kernel_agrees`` has checked them against the plain kernel
and ``FrameBuffer.line`` (state, segments, spans and pixels) at import (the
import, kernels compiled and checked, takes about as long as ui/field.py's).
Elsewhere (CPython, the wasm port) the same source runs as plain Python and
the segments go out as ``fb.line`` calls. Nothing allocates per frame: star
state lives in preallocated int arrays, every curve is an import-time table.

Changed regions (§4A rule 6): the box of each star's old and new streak (one
box when the star moved along its ray, two when it wrapped; none when neither
its place nor its colours changed, so held stars cost nothing), plus, when the
palette changed (``pal_diff``: highest ring index whose colour changed), the
square that holds every pixel of that index or less.

Numbers: THEME_PARAMS["warp"] (counts, rates, surge, found slowdown). Fine
detail the spec leaves open, from the approved mockup (``fWarp``) unless noted:

- base floor / glow (levels): live 0.2 + 0.3 I / 1 + 2.2 I; listening 0.25 /
  0.6; still 0.3 / 1.2; scan 0.25 / the live mirror 1 + 5 I (tokens
  field.scan_sweep glow); found 0.5 / 2.2. Glow radius: the field's.
- star radius r = 8 + 175 u^1.8 px over a trip u = 0..1 (R0_PX, R_SPAN, R_POW),
  brightness (2.2 + 4.8 u)(0.65 + 0.35 I) levels times the vignette at the
  head, + 0.4 (B0, B1, BI0, BI1, S_FLOOR); the tail at half the head's
  brightness; the head two pixels wide past u 0.55 (WIDE_U). A star that has
  not moved a pixel is a dot (2x2 past u 0.55) at half the vignette
  (x (1 + vig) / 2, so a held star still reads near the edge), and while
  twinkling above 0.85 (SPARK) a 3x3 cross, the centre at the head's level and
  the arms at the tail's.
- held stars (still, scan, a stopped FOUND) that sit behind the lens or the
  fill, or past the screen edge, move there unseen to a place between the lens
  (+ LENS_PX) and the edge (- EDGE_PX), a fixed fraction of the way per star
  (the mockup's hash), and fade in, so the moment shows its stars instead of
  hiding most of them behind the lens (r(u) puts half the stars inside r 64).
- each star's trip period factor 0.8 + 0.6 h and start phase h from the
  mockup's integer hash h; directions on the golden angle (137.5 deg, so any
  first n stars are spread evenly round the dial; the mockup hashed them).
- twinkle: brightness x (0.55 + 0.45 sin(t / 260 ms + 1.7 j)) (TW0, TW1, TW_MS).
- head = the 40 % of the streak at its moving end (HEAD_FRAC); head and tail
  at least 0.5 / 0.25 level over the base (D_HEAD, D_TAIL); count fades
  FADE_MS = 400 ms.
"""

import array
import math

from finder import tuning as T
from finder.compat import const, ticks_diff
from ui.field import COS, EASE_IOC, EXPT, GHOST_AMP, N_IDX, Q8, SIN, VIG, ease
from ui.field import T_GLUT as LUT_GREY, T_LUT as LUT_MIX     # LUT offsets in Radial.tab
from ui.themes.base import M_FOUND, M_LISTEN, M_LIVE, M_SCAN, M_STILL, S_MENU, Radial, Theme

try:
    import framebuf
except ImportError:  # CPython: no frames
    framebuf = None

_P = T.THEME_PARAMS["warp"]
STARS = tuple(_P["stars"])
N_LISTEN = _P["listen_stars"]
N_STILL = _P["still_stars"]
N_SCAN = _P["scan_stars"]
N_FOUND = _P["found_stars"]
NMAX = max(STARS + (N_LISTEN, N_STILL, N_SCAN, N_FOUND))

# rates: Q16 trips per ms, x256 (rate trips/s * 65536 / 1000 * 256)
_RQ = 65536 * 256 / 1000.0
RATE_Q = tuple(int(round(x * _RQ)) for x in _P["rate"])
LISTEN_Q = int(round(_P["listen_rate"] * _RQ))
FOUND_Q = int(round(_P["found_rate"] * _RQ))
# surge after a live beat: 1.3 e^(-age/170 ms), Q8, per 8 ms of beat age
SURGE_STEP = const(8)
SURGE_N = const(128)
SURGE = array.array("h", [int(Q8 * _P["surge"] * math.exp(-SURGE_STEP * k / _P["surge_ms"]) + 0.5)
                          for k in range(SURGE_N)])
# FOUND slowdown e^(-t/400 ms), Q8, per 16 ms; 0 (a stop) from 4 x found_ms
FEXP_STEP = const(16)
FEXP_N = (4 * _P["found_ms"]) // FEXP_STEP
FEXP = array.array("h", [int(Q8 * math.exp(-FEXP_STEP * k / _P["found_ms"]) + 0.5)
                         for k in range(FEXP_N)])

# star radius over a trip (mockup): r = 8 + 175 u^1.8 px
R0_PX = const(8)
R_SPAN = 175
R_POW = 1.8
RFAR = const(2720)          # Q4: 170 px, past the corner (168): off screen at any angle
# star brightness (mockup): (2.2 + 4.8 u)(0.65 + 0.35 I) x vignette + 0.4, Q8 levels.
# The kernel source repeats these as literals (viper takes no globals);
# tests/test_theme_warp.py checks they match.
B0 = const(563)
B1 = const(1229)
BI0 = const(166)
BI1 = const(90)
S_FLOOR = const(102)
WIDE_U = const(36045)       # u > 0.55 (Q16): the head is two pixels wide
HEAD_FRAC = const(102)      # head = 40 % of the streak (Q8)
D_HEAD = const(128)         # head >= base + 0.5 level
D_TAIL = const(64)          # tail >= base + 0.25 level
# twinkle (mockup): x (0.55 + 0.45 sin(t/260 ms + 1.7 j))
TW0 = const(141)
TW1 = const(115)
TW_MS = const(1634)         # 2 pi x 260 ms
FADE_MS = const(400)        # count change while the stars are held
CLIP_PX = const(2)          # streaks start this far outside the rim (pixel rounding and
                            # the wide head's 1 px offset stay off the rim)
EDGE_PX = 3                 # a held star sits this far inside the screen edge...
LENS_PX = const(2)          # ...and this far outside the lens (and fill)
SPARK = const(218)          # twinkle above 0.85: the star flares to a 3x3 cross
NONE = const(999)           # no box

# base levels per moment (mockup), Q8
LIVE_FL_A = const(51)       # floor 0.2 + 0.3 I
LIVE_FL_B = const(77)
LIVE_GL_A = const(256)      # glow 1 + 2.2 I
LIVE_GL_B = const(563)
LISTEN_FL = const(64)       # 0.25
LISTEN_GL = const(154)      # 0.6
STILL_FL = const(77)        # 0.3
STILL_GL = const(307)       # 1.2
SCAN_FL = const(64)         # 0.25
MG_A = int(T.FIELD_SCAN_SWEEP_GLOW_AMP[0] * Q8 + 0.5)    # live mirror 1 + 5 I (§5.7)
MG_B = int(T.FIELD_SCAN_SWEEP_GLOW_AMP[1] * Q8 + 0.5)
FOUND_FL = const(128)       # 0.5
FOUND_GL = const(563)       # 2.2
SUN_FLOOR = const(256)      # sun mode floor >= 1.0, as the field (ui/renderer.py)
RIM_PX = T.IRIS_RIM_PX
NB4 = const(47)             # base-level buckets of 4 px (r 0..187: past the trip end, 183)

# ---- kernel layout ---------------------------------------------------------------
# st (array 'i', 8 a star): phase u (Q16) | shown (Q8) | box x0 y0 x1 y1 (x0 NONE:
# none) | head colour | tail colour.  cst (array 'i', 8 a star): speed factor
# (Q8) | sin | cos (Q14) | twinkle phase (deg) | wide-head dx | dy | u where the
# star nears the screen edge (Q16) | where a held star is placed (Q8 of the way
# from the lens to that edge).
# tab (array 'i'): radius per u >> 6 (Q4 px) @0 | VIG @1024 | SIN @1194 |
# base level per 4 px @1554 (written per frame when the base changes).
# rtab: the Radial's kernel table (the mixed LUT @458, grey @522).
# prm (array 'i'): P_* below.  seg (array 'i', 5 a segment): x0 y0 x1 y1 colour.
ST_N = const(8)
CS_N = const(8)
K_RT = const(0)
K_VIG = const(1024)
K_SIN = const(1194)
K_BL = const(1554)
K_N = const(1601)
P_G = const(0)              # trip step this frame for a factor-1 star, Q16
P_N = const(1)              # stars shown (0..n-1)
P_FSTEP = const(2)          # fade step this frame (held stars), Q8
P_RMIN = const(3)           # innermost star radius, Q4 px
P_TW = const(4)
P_TWD = const(5)            # twinkle clock, deg
P_IFAC = const(6)           # brightness scale (intensity, ghost), Q8
P_VMAX = const(7)
P_DIM = const(8)
P_LIFT = const(9)
P_LUT = const(10)           # offset of the LUT in rtab
P_MARK = const(11)          # 1: widen the spans
P_DRAW = const(12)          # 1: draw the segments into fbuf (viper only)
P_FY0 = const(13)           # fbuf's first frame row
P_FH = const(14)            # fbuf's rows
P_NSEG = const(15)          # out: segments written
P_NMAX = const(16)
P_UMIN = const(17)          # u of the innermost visible radius (held stars move out to it)
P_LEN = const(18)
SEG_MAX = const(3)          # segments a star


def _hash(n):
    """The mockup's integer hash (src.html ``hash``) -> 0..1 (import time only)."""
    n = (n ^ 61) ^ (n >> 16)
    n = (n + (n << 3)) & 0xFFFFFFFF
    n ^= n >> 4
    n = (n * 0x27D4EB2D) & 0xFFFFFFFF
    n ^= n >> 15
    return n / 4294967296.0


def _stars():
    """Per-star constants (cst) and start phases."""
    cst = array.array("i", [0] * (NMAX * CS_N))
    u0 = []
    for j in range(NMAX):
        d = int(j * 137.508 + 0.5) % 360               # golden angle
        sn = SIN[d]
        cs = COS[d]
        o = j * CS_N
        cst[o] = int(256.0 / (0.8 + 0.6 * _hash(j * 3 + 2)) + 0.5)
        cst[o + 1] = sn
        cst[o + 2] = cs
        cst[o + 3] = int(math.degrees(1.7 * j) + 0.5) % 360
        steep = abs(sn) <= abs(cs)                     # second head line across the streak
        cst[o + 4] = 1 if steep else 0
        cst[o + 5] = 0 if steep else 1
        # the screen edge along this direction, less EDGE_PX, as a trip phase
        e = 1e9
        if abs(sn) > 16:
            e = 119.5 * 16384 / abs(sn)
        if abs(cs) > 16:
            e = min(e, 119.5 * 16384 / abs(cs))
        e = min(e, 168.0) - EDGE_PX
        cst[o + 6] = int(65536 * ((e - R0_PX) / R_SPAN) ** (1.0 / R_POW))
        cst[o + 7] = int(256 * _hash(j * 3 + 1))
        u0.append(int(65536 * _hash(j * 3 + 3)) & 0xFFFF)
    return cst, u0


CST, U0 = _stars()
TAB = array.array("i", [0] * K_N)
for _k in range(1024):
    TAB[K_RT + _k] = int(16 * (R0_PX + R_SPAN * ((_k + 0.5) / 1024.0) ** R_POW) + 0.5)
for _k in range(N_IDX):
    TAB[K_VIG + _k] = VIG[_k]
for _k in range(360):
    TAB[K_SIN + _k] = SIN[_k]


def _u_at(rq):
    """Smallest trip phase (Q16, a multiple of 64) whose radius is >= ``rq`` (Q4)."""
    lo = 0
    hi = 1023
    if TAB[K_RT + hi] < rq:
        return 65535
    while lo < hi:
        mid = (lo + hi) >> 1
        if TAB[K_RT + mid] < rq:
            lo = mid + 1
        else:
            hi = mid
    return lo << 6


# ---- star kernel ------------------------------------------------------------------
# One pass over the stars: flight (wraps switch stars on / off), held-star fades,
# radius, clip at prm[P_RMIN], brightness and colours, segments into ``seg``,
# changed boxes into the strip spans ``sp`` (returns the strip bits), then, with
# prm[P_DRAW], the segments drawn into ``fbuf`` exactly as FrameBuffer.line
# draws them. Literals are the module constants above (viper sees no globals).
_SSRC = """
def star_kernel(st, cst, tab, rtab, prm, seg, fbuf, sp) -> int:
    sv = ptr32(st)
    cv = ptr32(cst)
    tv = ptr32(tab)
    rv = ptr16(rtab)
    pv = ptr32(prm)
    gv = ptr32(seg)
    spv = ptr8(sp)
    g = pv[0]
    n = pv[1]
    fstep = pv[2]
    rmin = pv[3]
    tw = pv[4]
    twd = pv[5]
    ifac = pv[6]
    vmax = pv[7]
    dim = pv[8]
    lift = pv[9]
    lut = pv[10]
    mk = pv[11]
    nmax = pv[16]
    umin = pv[17]
    bits = 0
    ns = 0
    j = 0
    while j < nmax:
        so = j << 3
        co = j << 3
        u0 = sv[so]
        u = u0
        wr = 0
        if g != 0:
            u = u0 + ((g * cv[co]) >> 8)
            if u > 65535:
                u -= 65536
                wr = 1
            elif u < 0:
                u += 65536
                wr = 1
            sv[so] = u
        o = sv[so + 1]
        if wr:
            if j < n:
                o = 256
            else:
                o = 0
            sv[so + 1] = o
        elif fstep:
            if j < n:
                ue = cv[co + 6]
                rel = 0
                if u > ue:
                    rel = 1
                if tv[u >> 6] < rmin:
                    rel = 1
                if rel:
                    if umin < ue:
                        u = umin + (((ue - umin) * cv[co + 7]) >> 8)
                        sv[so] = u
                        o = 0
                if o < 256:
                    o += fstep
                    if o > 256:
                        o = 256
                    sv[so + 1] = o
            elif o > 0:
                o -= fstep
                if o < 0:
                    o = 0
                sv[so + 1] = o
        x0 = 999
        y0 = 0
        x1 = 0
        y1 = 0
        ch = 0
        ct = 0
        if o > 0:
            r1 = tv[u >> 6]
            r0 = r1
            if g != 0:
                if wr == 0:
                    r0 = tv[u0 >> 6]
            out = 1
            ra = r0
            rb = r1
            if r0 > r1:
                ra = r1
                rb = r0
                out = 0
            if rb >= rmin:
                if ra < 2720:
                    if ra < rmin:
                        ra = rmin
                    b = ((563 + ((1229 * u) >> 16)) * ifac) >> 8
                    tf = 0
                    if tw:
                        s = twd + cv[co + 3]
                        if s >= 360:
                            s -= 360
                        tf = 141 + ((115 * tv[1194 + s]) >> 14)
                        b = (b * tf) >> 8
                    if o < 256:
                        b = (b * o) >> 8
                    rh = ra
                    if out:
                        rh = rb
                    rh = rh >> 4
                    if rh > 169:
                        rh = 169
                    vg = tv[1024 + rh]
                    sn = cv[co + 1]
                    cs = cv[co + 2]
                    xb = (1920 + ((rb * sn) >> 14)) >> 4
                    yb = (1920 - ((rb * cs) >> 14)) >> 4
                    wx = 0
                    wy = 0
                    if u > 36045:
                        wx = cv[co + 4]
                        wy = cv[co + 5]
                    go = ns * 5
                    if rb - ra < 16:
                        b = (b * (256 + vg)) >> 9
                        v = b + 102
                        q = tv[1554 + (ra >> 6)] + 128
                        if v < q:
                            v = q
                        if v > vmax:
                            v = vmax
                        if dim != 256:
                            v = (v * dim) >> 8
                        q = ((v * 9 + 128) >> 8) + lift
                        if q > 63:
                            q = 63
                        ch = int(rv[lut + q])
                        ct = ch
                        x0 = xb
                        y0 = yb
                        x1 = xb
                        y1 = yb
                        sk = 0
                        if rb >= rmin + 32:
                            if tf > 218:
                                sk = 2
                            elif wx + wy > 0:
                                sk = 1
                        if sk == 2:
                            v = (b >> 1) + 102
                            q = tv[1554 + (ra >> 6)] + 64
                            if v < q:
                                v = q
                            if v > vmax:
                                v = vmax
                            if dim != 256:
                                v = (v * dim) >> 8
                            q = ((v * 9 + 128) >> 8) + lift
                            if q > 63:
                                q = 63
                            ct = int(rv[lut + q])
                            x0 = xb - 1
                            y0 = yb - 1
                            x1 = xb + 1
                            y1 = yb + 1
                            gv[go] = x0
                            gv[go + 1] = yb
                            gv[go + 2] = x1
                            gv[go + 3] = yb
                            gv[go + 4] = ct
                            gv[go + 5] = xb
                            gv[go + 6] = y0
                            gv[go + 7] = xb
                            gv[go + 8] = y1
                            gv[go + 9] = ct
                            gv[go + 10] = xb
                            gv[go + 11] = yb
                            gv[go + 12] = xb
                            gv[go + 13] = yb
                            gv[go + 14] = ch
                            ns += 3
                        elif sk == 1:
                            x1 = xb + 1
                            y1 = yb + 1
                            gv[go] = xb
                            gv[go + 1] = yb
                            gv[go + 2] = x1
                            gv[go + 3] = yb
                            gv[go + 4] = ch
                            gv[go + 5] = xb
                            gv[go + 6] = y1
                            gv[go + 7] = x1
                            gv[go + 8] = y1
                            gv[go + 9] = ch
                            ns += 2
                        else:
                            gv[go] = xb
                            gv[go + 1] = yb
                            gv[go + 2] = xb
                            gv[go + 3] = yb
                            gv[go + 4] = ch
                            ns += 1
                    else:
                        b = (b * vg) >> 8
                        xa = (1920 + ((ra * sn) >> 14)) >> 4
                        ya = (1920 - ((ra * cs) >> 14)) >> 4
                        d = ((rb - ra) * 102) >> 8
                        if out:
                            rm = rb - d
                            qh = tv[1554 + (rm >> 6)]
                            qt = tv[1554 + (ra >> 6)]
                        else:
                            rm = ra + d
                            qh = tv[1554 + (ra >> 6)]
                            qt = tv[1554 + (rm >> 6)]
                        xm = (1920 + ((rm * sn) >> 14)) >> 4
                        ym = (1920 - ((rm * cs) >> 14)) >> 4
                        v = b + 102
                        qh += 128
                        if v < qh:
                            v = qh
                        if v > vmax:
                            v = vmax
                        if dim != 256:
                            v = (v * dim) >> 8
                        q = ((v * 9 + 128) >> 8) + lift
                        if q > 63:
                            q = 63
                        ch = int(rv[lut + q])
                        v = (b >> 1) + 102
                        qt += 64
                        if v < qt:
                            v = qt
                        if v > vmax:
                            v = vmax
                        if dim != 256:
                            v = (v * dim) >> 8
                        q = ((v * 9 + 128) >> 8) + lift
                        if q > 63:
                            q = 63
                        ct = int(rv[lut + q])
                        xh = xb
                        yh = yb
                        xt = xa
                        yt = ya
                        if out == 0:
                            xh = xa
                            yh = ya
                            xt = xb
                            yt = yb
                        gv[go] = xt
                        gv[go + 1] = yt
                        gv[go + 2] = xm
                        gv[go + 3] = ym
                        gv[go + 4] = ct
                        gv[go + 5] = xm
                        gv[go + 6] = ym
                        gv[go + 7] = xh
                        gv[go + 8] = yh
                        gv[go + 9] = ch
                        ns += 2
                        x0 = xa
                        x1 = xb
                        if xb < xa:
                            x0 = xb
                            x1 = xa
                        y0 = ya
                        y1 = yb
                        if yb < ya:
                            y0 = yb
                            y1 = ya
                        if wx + wy > 0:
                            gv[go + 10] = xm + wx
                            gv[go + 11] = ym + wy
                            gv[go + 12] = xh + wx
                            gv[go + 13] = yh + wy
                            gv[go + 14] = ch
                            ns += 1
                            x1 += wx
                            y1 += wy
        ox0 = sv[so + 2]
        nb = 0
        ax0 = 0
        ay0 = 0
        ax1 = 0
        ay1 = 0
        cx0 = 0
        cy0 = 0
        cx1 = 0
        cy1 = 0
        if x0 == 999:
            if ox0 != 999:
                ax0 = ox0
                ay0 = sv[so + 3]
                ax1 = sv[so + 4]
                ay1 = sv[so + 5]
                nb = 1
                sv[so + 2] = 999
        else:
            same = 0
            if x0 == ox0:
                if y0 == sv[so + 3]:
                    if x1 == sv[so + 4]:
                        if y1 == sv[so + 5]:
                            if ch == sv[so + 6]:
                                if ct == sv[so + 7]:
                                    same = 1
            if same == 0:
                ax0 = x0
                ay0 = y0
                ax1 = x1
                ay1 = y1
                nb = 1
                if ox0 != 999:
                    if wr:
                        cx0 = ox0
                        cy0 = sv[so + 3]
                        cx1 = sv[so + 4]
                        cy1 = sv[so + 5]
                        nb = 2
                    else:
                        if ox0 < ax0:
                            ax0 = ox0
                        if sv[so + 3] < ay0:
                            ay0 = sv[so + 3]
                        if sv[so + 4] > ax1:
                            ax1 = sv[so + 4]
                        if sv[so + 5] > ay1:
                            ay1 = sv[so + 5]
                sv[so + 2] = x0
                sv[so + 3] = y0
                sv[so + 4] = x1
                sv[so + 5] = y1
                sv[so + 6] = ch
                sv[so + 7] = ct
        if mk:
            k = 0
            while k < nb:
                if k == 1:
                    ax0 = cx0
                    ay0 = cy0
                    ax1 = cx1
                    ay1 = cy1
                k += 1
                if ax1 < 0:
                    continue
                if ay1 < 0:
                    continue
                if ax0 > 239:
                    continue
                if ay0 > 239:
                    continue
                if ax0 < 0:
                    ax0 = 0
                if ax1 > 239:
                    ax1 = 239
                if ay0 < 0:
                    ay0 = 0
                if ay1 > 239:
                    ay1 = 239
                kk = ay0 // 24
                k1 = ay1 // 24
                while kk <= k1:
                    bits |= 1 << kk
                    i2 = kk + kk
                    if ax0 < int(spv[i2]):
                        spv[i2] = ax0
                    if ax1 > int(spv[i2 + 1]):
                        spv[i2 + 1] = ax1
                    kk += 1
        j += 1
    pv[15] = ns
    if pv[12]:
        fv = ptr16(fbuf)
        fy0 = pv[13]
        fh = pv[14]
        i = 0
        while i < ns:
            go = i * 5
            i += 1
            px = gv[go]
            py = gv[go + 1]
            qx = gv[go + 2]
            qy = gv[go + 3]
            c = gv[go + 4]
            dx = qx - px
            sx = 1
            if dx <= 0:
                dx = 0 - dx
                sx = -1
            dy = qy - py
            sy = 1
            if dy <= 0:
                dy = 0 - dy
                sy = -1
            steep = 0
            if dy > dx:
                tmp = px
                px = py
                py = tmp
                tmp = dx
                dx = dy
                dy = tmp
                tmp = sx
                sx = sy
                sy = tmp
                steep = 1
            e = 2 * dy - dx
            m = 0
            while m < dx:
                xx = px
                yy = py
                if steep:
                    xx = py
                    yy = px
                yy -= fy0
                if xx >= 0:
                    if xx < 240:
                        if yy >= 0:
                            if yy < fh:
                                fv[yy * 240 + xx] = c
                while e >= 0:
                    py += sy
                    e -= 2 * dx
                px += sx
                e += 2 * dy
                m += 1
            qy -= fy0
            if qx >= 0:
                if qx < 240:
                    if qy >= 0:
                        if qy < fh:
                            fv[qy * 240 + qx] = c
    return bits
"""


# ---- palette change detector --------------------------------------------------
# Highest index < n where ``cur`` differs from ``prev`` (-1 if none), copying
# ``cur`` into ``prev`` as it goes.
_DSRC = """
def pal_diff(cur, prev, n: int) -> int:
    cp = ptr16(cur)
    pp = ptr16(prev)
    hi = -1
    i = 0
    while i < n:
        c = cp[i]
        if c != pp[i]:
            pp[i] = c
            hi = i
        i += 1
    return hi
"""


def _ident(x):
    return x


def _compile(src, name):
    """(plain, viper or None) for one kernel source."""
    ns = {"ptr8": _ident, "ptr16": _ident, "ptr32": _ident}
    exec(src, ns)
    try:
        vs = {}
        exec("@micropython.viper" + src, vs)
    except Exception:  # noqa: BLE001 - no viper (CPython, wasm): plain Python
        return ns[name], None
    return ns[name], vs[name]


def diff_agrees(ka, kb):
    """True if pal_diff kernels ``ka`` and ``kb`` give the same result and
    copies on synthetic palettes (none, one, many and the last entry changed)."""
    for case in range(4):
        out = []
        for fn in (ka, kb):
            cur = array.array("H", [(i * 4099 + 7) & 0xFFFF for i in range(256)])
            prev = array.array("H", cur)
            if case == 1:
                prev[37] ^= 0x0100
            elif case == 2:
                for i in range(0, 170, 7):
                    prev[i] = (prev[i] + 1) & 0xFFFF
            elif case == 3:
                prev[169] = 0
                prev[200] = 1                     # past n: not looked at
            h = fn(cur, prev, N_IDX)
            out.append((h, bytes(prev)))
        if out[0] != out[1]:
            return False
    return True


# Self-check frames for the star kernel: (g, n, fstep, rmin, tw, twd, ifac,
# vmax, dim, lift, lut): outward flight with wraps and count changes, the same
# twinkling and dimmed with a lens, inward flight with a lift and the saver
# cap, held stars fading in and out.
CHECK_PRMS = (
    (30000, 46, 0, 128, 0, 0, 256, 1792, 256, 0, LUT_MIX),
    (21000, 18, 0, 1088, 1, 77, 154, 1792, 128, 0, LUT_MIX),
    (-9000, 14, 0, 128, 0, 0, 200, 1280, 256, 9, LUT_GREY),
    (0, 30, 64, 480, 1, 300, 230, 1792, 200, 0, LUT_MIX),
)
CHECK_FH = const(40)
CHECK_Y0 = (0, 100, 200)


def _check_state(case):
    """Star state for self-check frame ``case``: phases, shown levels and old
    boxes that hit every branch (wraps, fades, no old box, unchanged)."""
    st = array.array("i", [0] * (NMAX * ST_N))
    for j in range(NMAX):
        o = j * ST_N
        st[o] = (U0[j] * (case + 3)) & 0xFFFF
        v = (j * 37 * (case + 1)) % 300 if case == 3 else (256 if (j + case) % 3 else 0)
        st[o + 1] = v if v < 256 else 256
        st[o + 2] = NONE if (j + case) % 4 == 0 else (j * 7) % 230
        st[o + 3] = (j * 13) % 230
        st[o + 4] = st[o + 2] + 5
        st[o + 5] = st[o + 3] + 7
    return st


def _check_prm(case, draw, y0):
    prm = array.array("i", [0] * P_LEN)
    vals = CHECK_PRMS[case]
    for i in range(len(vals)):
        prm[i] = vals[i]
    prm[P_MARK] = 1
    prm[P_DRAW] = draw
    prm[P_FY0] = y0
    prm[P_FH] = CHECK_FH
    prm[P_NMAX] = NMAX
    prm[P_UMIN] = _u_at(prm[P_RMIN] + 32)
    return prm


def _check_spans():
    sp = bytearray(20)
    for k in range(10):
        sp[2 * k] = 255
    return sp


def kernel_agrees(ka, kb):
    """True if star kernels ``ka`` (plain, the reference) and ``kb`` agree on
    every CHECK_PRMS frame: the star state, the segments, the spans and the
    strip bits; and if ``kb`` drawing its own segments (prm[P_DRAW]) into a
    frame band (a FrameBuffer, as ``draw`` passes it) gives the same pixels as
    FrameBuffer.line drawing ``ka``'s, at three band offsets."""
    if framebuf is None:
        return False
    n = NMAX * SEG_MAX * 5
    seg = array.array("i", [0] * n)
    sg2 = array.array("i", [0] * n)
    tab = array.array("i", TAB)
    for k in range(NB4):
        tab[K_BL + k] = 900 - k * 19
    rtab = array.array("H", [(i * 2731 + 0x1357) & 0xFFFF for i in range(600)])
    buf = bytearray(240 * CHECK_FH * 2)
    fb = framebuf.FrameBuffer(buf, 240, CHECK_FH, framebuf.RGB565)
    ref = bytearray(240 * CHECK_FH * 2)
    rfb = framebuf.FrameBuffer(ref, 240, CHECK_FH, framebuf.RGB565)
    for case in range(len(CHECK_PRMS)):
        st = _check_state(case)
        prm = _check_prm(case, 0, 0)
        sp = _check_spans()
        for i in range(n):
            seg[i] = 0
        bits = ka(st, CST, tab, rtab, prm, seg, rfb, sp)
        ns = prm[P_NSEG]
        if ns < 12:                              # the frame must exercise the drawing
            return False
        want = (bits, ns, bytes(st), bytes(sp), bytes(seg))
        for y0 in CHECK_Y0:
            st = _check_state(case)
            prm = _check_prm(case, 1, y0)
            sp = _check_spans()
            for i in range(n):
                sg2[i] = 0
            fb.fill(0x5A5A)
            bits = kb(st, CST, tab, rtab, prm, sg2, fb, sp)
            if (bits, prm[P_NSEG], bytes(st), bytes(sp), bytes(sg2)) != want:
                return False
            rfb.fill(0x5A5A)
            for i in range(ns):
                o = i * 5
                rfb.line(seg[o], seg[o + 1] - y0, seg[o + 2], seg[o + 3] - y0, seg[o + 4])
            if buf != ref:
                return False
    return True


def _kernels():
    dpy, dvp = _compile(_DSRC, "pal_diff")
    spy, svp = _compile(_SSRC, "star_kernel")
    dk, dkind = dpy, "python"
    if dvp is not None:
        if diff_agrees(dpy, dvp):
            dk, dkind = dvp, "viper"
        else:
            dkind = "python (viper self-check failed)"
    sk, skind = spy, "python"
    if svp is not None:
        if kernel_agrees(spy, svp):
            sk, skind = svp, "viper"
        else:
            skind = "python (viper self-check failed)"
    return dpy, dk, dkind, spy, sk, skind


pal_diff_py, pal_diff, DIFF_KIND, star_kernel_py, star_kernel, KERNEL = _kernels()


class Warp(Theme):
    name = "warp"
    layer = True

    def __init__(self, r):
        Theme.__init__(self, r)
        self.rad = Radial("warp")
        self.ramps = self.rad.ramps
        self.prev = array.array("H", [0] * 256)
        st = array.array("i", [0] * (NMAX * ST_N))
        for j in range(NMAX):
            st[j * ST_N] = U0[j]
            st[j * ST_N + 2] = NONE
        self.st = st
        self.tab = array.array("i", TAB)
        self.prm = array.array("i", [0] * P_LEN)
        self.prm[P_NMAX] = NMAX
        self.prm[P_FH] = 240
        self.seg = array.array("i", [0] * (NMAX * SEG_MAX * 5))
        self.kern = star_kernel
        self.kdraw = 1 if KERNEL == "viper" else 0
        self.bkey = array.array("i", [-1] * 4)     # fl, gl, gr, iris the base table is for
        self.m = M_LIVE
        self.z = 0
        self.key = -1
        self.fl = 0
        self.gl = 0
        self.xf_on = False
        self.xf_ms = 0
        self.xf_fl = 0
        self.xf_gl = 0
        self.twc = 0                       # twinkle clock, ms mod TW_MS
        self.fage = 0                      # ms into FOUND
        self.ls = 0                        # the beat (last ring spawn) last seen
        self.ghost = 0                     # that beat was a ghost
        self.surge = 0                     # Q8
        self.n = 0

    # ---- per frame -------------------------------------------------------------
    def _targets(self, p, m, iq):
        """Theme floor and glow for moment ``m`` (Q8 levels)."""
        if m == M_LIVE:
            fl = LIVE_FL_A + ((LIVE_FL_B * iq) >> 8)
            gl = LIVE_GL_A + ((LIVE_GL_B * iq) >> 8)
        elif m == M_LISTEN:
            fl = LISTEN_FL
            gl = LISTEN_GL
        elif m == M_STILL:
            fl = STILL_FL
            gl = STILL_GL
        elif m == M_SCAN:
            fl = SCAN_FL
            gl = MG_A + ((MG_B * iq) >> 8)
        else:
            fl = FOUND_FL
            gl = FOUND_GL
        if p.sun and fl < SUN_FLOOR:
            fl = SUN_FLOOR
        return fl, gl

    def build(self, p, t, core, rim, vmax, lift):
        dt = self.clock(p, t)
        wake = self.wake
        f = self.f
        r = self.r
        q = self.prm
        if wake or r._scr != S_MENU:
            # MENU holds the moment, the levels and the stars (dt is 0)
            m = self.moment(p)
            z = self.zone(p)
            iq = self.iq()
            key = m * 4 + (z if m == M_LIVE else 0)
            if wake:
                if m == M_FOUND:
                    # a wake shows FOUND as it is now (§8): slowing only
                    # while the celebration that started it is young
                    self.fage = (ticks_diff(t, r._sub_t0) if r._sub == "celebrate"
                                 else FEXP_N * FEXP_STEP)
            elif key != self.key:
                self.xf_fl = self.fl
                self.xf_gl = self.gl
                self.xf_ms = 0
                self.xf_on = True
                self.fage = 0
            elif m == M_FOUND:
                self.fage += dt
            self.key = key
            self.m = m
            self.z = z
            fl, gl = self._targets(p, m, iq)
            if wake:
                self.xf_on = False
            elif self.xf_on:
                self.xf_ms += dt
                e = ease(EASE_IOC, self.xf_ms, T.ZONE_CROSSFADE_MS)
                if e >= 256:
                    self.xf_on = False
                else:
                    fl = self.xf_fl + (((fl - self.xf_fl) * e) >> 8)
                    gl = self.xf_gl + (((gl - self.xf_gl) * e) >> 8)
            if not wake:                        # flash limit on the floor (§4)
                d = fl - self.fl
                st = f.flash
                if d > st:
                    fl = self.fl + st
                elif d < -st:
                    fl = self.fl - st
            self.fl = fl
            self.gl = gl
            # stars: count, flight step, twinkle, colour scale
            surge = 0
            if m == M_LIVE:
                n = STARS[z]
                ls = f.last_spawn
                if wake or ls != self.ls:
                    self.ls = ls
                    gh = 0 if p.ring_live else 1
                    for k in range(len(f.r_on)):
                        if f.r_on[k] and f.r_t0[k] == ls:
                            gh = f.r_ghost[k]
                            break
                    self.ghost = gh
                if not self.ghost:
                    a = self.beat_age(t) // SURGE_STEP
                    surge = SURGE[a] if a < SURGE_N else 0
                rq = RATE_Q[z]
            elif m == M_LISTEN:
                n = N_LISTEN
                rq = LISTEN_Q
            elif m == M_STILL:
                n = N_STILL
                rq = 0
            elif m == M_SCAN:
                n = N_SCAN
                rq = 0
            else:
                n = N_FOUND
                k = self.fage // FEXP_STEP
                rq = (FOUND_Q * FEXP[k]) >> 8 if 0 <= k < FEXP_N else 0
            self.surge = surge
            self.n = n
            g = (((dt * rq) >> 8) * (256 + surge)) >> 8
            q[P_G] = g
            q[P_N] = n
            fs = 0
            if rq == 0:                         # held: count changes fade, hidden stars move out
                if wake:
                    fs = 256                    # a wake shows them in place at once (§8)
                elif dt > 0:
                    fs = (dt * 256) // FADE_MS
                    if fs < 1:
                        fs = 1
            q[P_FSTEP] = fs
            q[P_TW] = 1 if (m == M_STILL or m == M_FOUND) else 0
            ifac = BI0 + ((BI1 * iq) >> 8)
            lut = LUT_MIX
            if m == M_LIVE and self.ghost:
                ifac = (ifac * GHOST_AMP) >> 8
                lut = LUT_GREY
            q[P_IFAC] = ifac
            q[P_LUT] = lut
        else:
            q[P_G] = 0
            q[P_FSTEP] = 0
        self.twc = (self.twc + dt) % TW_MS
        q[P_TWD] = (self.twc * 360) // TW_MS
        if wake:
            n = self.n
            st = self.st
            for j in range(NMAX):
                st[j * ST_N + 1] = 256 if j < n else 0
        q[P_VMAX] = vmax
        q[P_LIFT] = lift
        q[P_DIM] = f.dim
        # base layer
        self.ramps.follow(f, t)
        rad = self.rad
        gr = f.gr
        rad.setup(f, t, core, rim, vmax, lift, self.fl, self.gl, gr, 0)
        rad.run()
        iris = f.iris
        rmin = (iris + RIM_PX + CLIP_PX) if iris > 0 else R0_PX
        if f.fill_v and f.fill_r + 3 + CLIP_PX > rmin:
            rmin = f.fill_r + 3 + CLIP_PX
        q[P_RMIN] = rmin << 4
        q[P_UMIN] = _u_at((rmin + LENS_PX) << 4) if q[P_FSTEP] else 0
        bk = self.bkey
        if bk[0] != self.fl or bk[1] != self.gl or bk[2] != gr or bk[3] != iris:
            bk[0] = self.fl
            bk[1] = self.gl
            bk[2] = gr
            bk[3] = iris
            self._base_levels(iris, gr)
        hi = pal_diff(rad.pal_arr, self.prev, N_IDX)
        if wake:
            self.everything()
            q[P_MARK] = 0
        else:
            self.clear_dirty()
            q[P_MARK] = 1
            if hi >= 0:
                self.mark(119 - hi, 119 - hi, 120 + hi, 120 + hi)

    def _base_levels(self, iris, gr):
        """Floor + glow (x vignette) at the inner edge of each 4 px bucket: a
        star segment is never drawn below the base at its inner end."""
        fl = self.fl
        gl = self.gl
        ginv = (64 * Q8 * Q8) // (gr if gr > 2 * Q8 else 2 * Q8)
        tb = self.tab
        for k in range(NB4):
            ri = k << 2
            nn = ((ri - iris) * ginv) >> 8
            if nn < 0:
                nn = 0
            elif nn > 255:
                nn = 255
            tb[K_BL + k] = ((fl + ((gl * EXPT[nn]) >> 8)) *
                            VIG[ri if ri < N_IDX else N_IDX - 1]) >> 8

    def blit(self, y0, buf, fb):
        rad = self.rad
        self.r.map.blit(y0, rad.pal, rad.pal_arr, buf, fb)

    def draw(self, fb):
        q = self.prm
        q[P_DRAW] = self.kdraw
        self.dirty |= self.kern(self.st, CST, self.tab, self.rad.tab, q, self.seg, fb,
                                self.spans)
        if not self.kdraw:
            sg = self.seg
            i = 0
            e = q[P_NSEG] * 5
            while i < e:
                fb.line(sg[i], sg[i + 1], sg[i + 2], sg[i + 3], sg[i + 4])
                i += 5
