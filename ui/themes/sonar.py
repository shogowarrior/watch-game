"""Sonar (ui-spec §4A): a phosphor beam sweeps the dish over faint range rings.

Two index maps, one per kind of moment:

- **Beam moments** (*Live*, *Listening*, and the frames while beams fade
  out after a change of beam set) draw through a *class x angle* map:
  16-bit entries ``class << 7 | bin``. ``bin`` is one of 128 angle bins
  (2.8125 deg each, clockwise from 12 o'clock); ``class`` is one of ``NC``
  (46) radial bands of the ring index (``EDGES``, ``BAND_STEPS``: the core
  dot 0..6 as one, bands up to 4 px wide inside r 88, where only the centre
  glow varies, up to 3 px over the vignette's slope, r 88-124, up to 8 px
  in the corners; one ring index for each range ring; band edges on the
  lens and rim radii of every glyph, so a settled lens and rim are exact).
  The map is built with a static 4x4 ordered dither (``build_maps``): a
  pixel takes one of the two nearest classes and bins in proportion to
  where it sits between their centres, so neither the bands nor the bins
  show as steps (never across the core-dot edge, a range ring or a lens /
  rim radius). Each frame the palette kernel (``sonar_pal``) colours all
  NC x 128 entries: per bin the trail level of the brightest beam over it,
  per class the base at the band's centre radius, and per entry the theme
  ramp at base + trail x fade x vignette (or the lens, rim, core or
  calibrate-fill colour). The map is symmetric under the four quadrant
  mirrors with the bin XOR'd (top right ``bin``, top left ``bin ^ 127``,
  bottom right ``bin ^ 63``, bottom left ``bin ^ 64``), so on the watch the
  blit kernel (``sonar_blit``) reads only the top-right quadrant (28.8 KB)
  and writes four pixels per entry, one palette lookup each, like the
  field's quadrant blit; elsewhere it is one framebuf palette blit of the
  full map (an RGB565-format frame buffer used as 16-bit indices, a
  palette NC x 128 wide). The kernel is used only once it writes the same
  bands as that blit (self-check on the real buffers).
- **Quiet moments** (*Still*, *Scan*, *Found*) draw as Ripple does: the
  base ``Radial`` palette (ui/themes/base.py: floor, glow, lens, rim, core
  dot, calibrate fill, vignette) plus the range rings and the FOUND ping,
  through the renderer's ring map.

What each moment draws (levels in ramp steps, I = intensity, z = zone):

- Base: floor and centre glow from the field's crossfaded levels (so they
  keep the field's 600 ms crossfades, flash limit and per-screen values)
  mapped onto dimmer levels so the beams read: the field's floor
  0.3 + 1.3 I becomes ``0.25 + 0.6 I`` and its glow 2 + 4 I becomes
  ``1.2 + 3 I`` (``SFL_*``, ``SGL_*``; sun mode keeps the floor at 1.0 or
  more, coming and going at the beam-set rate below); glow radius as the
  field's. FOUND blends to floor 0.6 and glow 2.2
  at radius 90 (``FOUND_*``, mockup) with the field's FOUND crossfade
  weight. Range rings (one ring index each, r ``range_rings_px`` 40 / 80 /
  120) at ``range_level`` 1 + 0.8 I. The ring level moves at most at the
  flash-limit rate, so a moment change never pops them.
- *Live:* ``beams[z]`` (2 / 3 / 4 / 6) beams, evenly spaced, turning
  clockwise, locked to the field's ring spawn: beam j is at
  360 x (beat_age / P + j) / B deg, so a beam crosses 12 o'clock on every
  spawn and a turn takes B x P (4.8 / 4.8 / 4.0 / 3.0 s; at 10 fps a beam
  moves 7.5 / 7.5 / 9 / 12 deg a frame, and its trail one step behind the
  head is still 70 % of its peak or more, so the step reads as a sweep).
  Each beam is a 2 deg head (``HEAD_DEG``) with a 3 deg soft leading edge
  (``LEAD_DEG``, mockup), then a trail that falls off as exp(-d / L) behind
  it, L = ``trail_deg`` 30 + 70 I deg, eased to 0 where it falls under 3 %
  (``TAIL_CUT``, at 3.5 L, so the far tail does not flicker a palette step
  every frame), peak ``trail_amp`` 3 + 4 I above the base (x the saver
  pulse scale on low battery). The trail fades in over ``FADE_PX`` (10 px,
  mockup) outside the rim (over the iris radius while that is less; not
  at all with the lens closed, so the trail meets the core dot instead of
  leaving a dark ring round it). While the lens opens the fade is wider by
  what it still has to open (``H_FW``), so the trails clear out ahead of
  the lens instead of being swallowed by it in one frame. A beam that
  crossed 12 o'clock on a ghost beat (``ring_live`` false) draws its trail
  grey (where it outshines the base) until its next pass.
- *Listening:* one beam turning anticlockwise at ``listen_deg_s`` 60 deg/s
  from 12 o'clock, trail ``LISTEN_TRAIL_DEG`` 22 deg, peak ``LISTEN_AMP``
  2.2 (mockup).
- *Still:* no beams; the range rings breathe ``STILL_RING`` 1.2 + 1.2 x
  (0.5 - 0.5 cos) over ``breathe_ms`` 2.4 s (mockup amplitudes).
- *Scan:* the beams stop where they are and fade out (below); the field's
  live-mirror halo (glow 1 + 5 I, radius 12) shows, mapped as the base.
- *Found:* one ping ring (``BURST_AMP`` 7, lead / trail
  ``FOUND_LEAD_TRAIL_PX`` 3 / 24 px, as the field's burst) runs out from
  the lens (the iris it is opening to, as the field's burst) at
  ``ping_px_s`` 240 px/s from the moment FOUND starts. Like the field's
  rings (§4 rule 3) its lead widens to ``temporal_aa_k`` 1.5 x speed /
  fps when that is more (36 px at 10 fps), so the brightest moving thing
  never jumps a frame behind a hard edge, and it fades in over its first
  ``fadein_px`` 12 px. From the moment FOUND starts the range rings
  breathe ``FOUND_RING`` 2.0 + 1.4 x (0.5 + 0.5 sin) over 2.4 s. A wake
  into FOUND shows no ping (no intro, §8) unless the params carry
  ``burst``.

A change of beam set (moment, zone or period) freezes the visible beams
where they are and fades them out while the new set fades in, slower than
the flash limit: each set's shown peak moves at most half the field's
flash-limit step (``f.flash``: 2 levels per 333 ms, so 1 level per 333 ms;
``XF_K``) a frame toward its target (0 for a set fading out), about 2.3 s
for HOT's 7 levels. Half, because six HOT beams cover the whole dish and
Sonar's dish is brighter than Ripple's, so coincident changes (the lens
opening, the FOUND ping, the hue crossfade) still fit the flash limit. Up
to two sets fade out at once; a third change drops the fainter. The
saver's pulse scale goes through the same slew, its level cap and the sun
floor move at the same rate (the sun's LUT lift is instant, as Ripple's),
and the beams' own intensity (``bi``: trail length and peak) follows I at
most ``f.flash / BI_K`` a frame (the mean trail level moves about BI_K = 6
levels per unit I). So a step in I, a zone or moment change, the saver or
sun mode never moves the dish's mean level faster than the field's floor
may move. A wake shows the current state at its full level, with nothing
fading.

Clocks advance by the base clock's ``dt`` (ms since the last drawn frame,
at most DT_MAX = 250, 0 in MENU), so a missed slot at the 7 / 6 / 5 fps
locks moves the listening beam, the ping and the breathing on by up to
250 ms instead of snapping them; only a wake (base ``clock``) restarts the
listening beam at 12 o'clock and drops the ping. MENU freezes everything
(the field holds the beat) and the moment, zone, intensity, ghost and sun
state stay those of the screen under the MENU (``live``), also for a theme
made in the MENU.

Changed regions: in beam moments the palette kernel compares every entry
with the last frame's and returns the shortest arc of bins holding every
change; the dirty box is the bounding box of that wedge widened by one bin
each side (a dithered pixel shows a bin at most one away from its own),
from the centre out past the corners. One listening beam reports 30-50 %
of the screen; live beams on opposite sides report all of it. In quiet
moments a small kernel (``sonar_diff``) compares the 170 ring palette
entries and the dirty strips are the union of the changed ring indices'
strip spans (``xs``, from the ring map), or the disc of the outermost
changed index when more than 8 changed (after the FOUND ping only the
three breathing rings change). A wake, theme switch or a switch between
the two maps reports everything, and so does a hue crossfade step in a
quiet moment.

Loading (ui/themes/base.py "Loading"): the import only defines the class
edges, layouts and kernel sources. ``load()`` builds the dither and kernel
tables (one step each), compiles each kernel and self-checks it against
its plain version (the palette kernel one checked frame and repeat per
step), ``Sonar()`` only allocates, and ``prepare()`` builds the quadrant
map ``MAP_ROWS`` rows a step, then checks the blit kernel on the real band
buffers (where viper runs) or builds the full map for the framebuf path.
29 steps on a 32-bit unix build with viper (fewer without it: no viper
compiles or blit check; a few more on the framebuf path, which builds the
full map ``FULL_ROWS`` rows a step), each about 1.2 ms or less there
except the module import (about 8-10 ms: compiling this module), which
cannot be split; a second load of the module skips ``load()``. Maps are
per theme object (not kept while another theme draws).

Cost per frame: beam moments: the palette kernel (128 bins x up to 18
beams, then NC x 128 entries, rows of one colour skipped when unchanged,
the level -> colour tables rebuilt only when the cap, dim, lift or LUT
change) and the quadrant blit; quiet moments: Ripple's own palette kernel
and blit plus the diff kernel. Per-frame Python is a few dozen statements
and loops over at most 18 beams. All three kernels are compiled with
@micropython.viper where the port has it, after a self-check against their
plain versions (the same source); plain Python elsewhere (the kinds are in
``PAL_KIND``, ``DIFF_KIND`` and ``Sonar.bkind``). Nothing allocates per
frame. Memory: the quadrant map 28.8 KB, the palette 18.9 KB, tables about
12 KB; the full map (115 KB) only where the blit kernel does not run.
"""

import array
import math

from finder import tuning as T
from finder.compat import const, ticks_diff
from ui.field import AA_K, COS, FL_A, GL_A, N_IDX, PROF, Q8, RIM_MIN, SIN, V7, VIG, q8
from ui.themes.base import (BH, M_FOUND, M_LISTEN, M_LIVE, M_STILL, NS, S_MENU, SH, W, Radial,
                            Theme)

try:
    import framebuf
except ImportError:  # CPython: palette maths only
    framebuf = None

# ---- numbers (THEME_PARAMS["sonar"], ui-spec §4A) ---------------------------------
_P = T.THEME_PARAMS["sonar"]
BEAMS = bytes(_P["beams"])                       # per zone FAR..HOT
TRAIL_A, TRAIL_B = _P["trail_deg"]               # L = A + B I deg
AMP_A = q8(_P["trail_amp"][0])                   # peak = A + B I levels
AMP_B = q8(_P["trail_amp"][1])
RINGS = tuple(_P["range_rings_px"])
RL_A = q8(_P["range_level"][0])                  # range rings 1 + 0.8 I
RL_B = q8(_P["range_level"][1])
LISTEN_DEG_S = _P["listen_deg_s"]
BREATHE_MS = _P["breathe_ms"]
PING_PX_S = _P["ping_px_s"]
PING_AMP = q8(T.BURST_AMP)                       # the field's burst ring: 7, lead / trail 3 / 24
PING_LEAD, PING_TRAIL = T.FOUND_LEAD_TRAIL_PX
PING_FADE = T.FADEIN_PX                          # fade-in over the first 12 px (§4 rule 3)
SAVER_PU = q8(T.SAVER_PULSE_SCALE)
SUN_FLOOR = const(256)                           # sun mode floor >= 1.0 (ui-spec §8)
BI_K = const(6)              # mean trail level change per unit I (levels): bi slews at flash / 6
XF_K = const(128)            # beam sets, sun floor and saver cap fade at XF_K / 256 of it

# fine detail the spec does not give: the approved mockup's numbers
HEAD_DEG = 2                 # beam head width (full level)
LEAD_DEG = 3                 # soft leading edge ahead of the head
TAIL_CUT = 1.0 / 32          # not in the mockup: the trail ends where exp(-d / L) is 3 % (3.5 L)
FADE_PX = const(10)          # trail fade-in outside the rim
LISTEN_TRAIL_DEG = 22
LISTEN_AMP = q8(2.2)
SFL_A = q8(0.25)             # floor 0.25 + 0.6 I from the field's 0.3 + 1.3 I
SFL_K = q8(0.6 / T.FLOOR_B)
SGL_A = q8(1.2)              # glow 1.2 + 3 I from the field's 2 + 4 I
SGL_K = q8(3.0 / T.GLOW_AMP_B)
STILL_RING = (q8(1.2), q8(1.2))      # still: rings 1.2 + 1.2 x breathe
FOUND_RING = (q8(2.0), q8(1.4))      # found: rings 2.0 + 1.4 x breathe
FOUND_FL = q8(0.6)
FOUND_GL = q8(2.2)
FOUND_GR = 90 * Q8

# ---- class x angle map ----------------------------------------------------------
NBIN = const(128)            # angle bins per turn (32 per quadrant)
ANG = const(8192)            # angle units per turn (64 per bin)
HEAD_U = (HEAD_DEG * ANG + 180) // 360
LEAD_U = (LEAD_DEG * ANG + 180) // 360
NF = const(2)                # beam sets fading out at once
MAXB = const(18)             # beams in the kernel: 6 current + 6 per fading set
MAP_ROWS = const(12)         # quadrant map rows built per prepare() step
FULL_ROWS = const(24)        # full-map rows (framebuf path) per prepare() step


BAND_STEPS = ((7, 7), (88, 4), (124, 3), (N_IDX, 8))   # (zone end, widest band) px


def _fixed():
    """Radii no class band or dither may straddle: the core dot (7), each
    range ring (one index) and every glyph's lens and rim radius."""
    fx = {7}
    for r in RINGS:
        fx.add(r)
        fx.add(r + 1)
    for g in ("chevrons", "arrow", "scan", "runes", "seeker"):
        ir = T.IRIS_R[g]
        if ir:
            fx.add(ir)
            fx.add(ir + T.IRIS_RIM_PX)
    return fx


def _edges():
    """Class band edges: fixed at the core dot (0..6), the vignette's knees
    (88, 124), each range ring (one index) and every glyph's lens and rim
    radii; the gaps split evenly into bands no wider than BAND_STEPS."""
    fx = _fixed()
    fx.add(0)
    fx.add(N_IDX)
    for z in BAND_STEPS:
        fx.add(z[0])
    fx = sorted(fx)
    e = set(fx)
    for k in range(len(fx) - 1):
        a = fx[k]
        b = fx[k + 1]
        st = 8
        for z in BAND_STEPS:
            if a < z[0]:
                st = z[1]
                break
        n = (b - a + st - 1) // st
        for i in range(1, n):
            e.add(a + (b - a) * i // n)
    return sorted(e)


EDGES = _edges()
NC = len(EDGES) - 1          # radial classes
CLS = bytearray(N_IDX)       # ring index -> class
for _c in range(NC):
    for _i in range(EDGES[_c], EDGES[_c + 1]):
        CLS[_i] = _c

# Static ordered dither (4x4 Bayer, phase j = 0..15 -> offset (j + 0.5) / 16):
# a pixel takes one of the two nearest classes / bins, in proportion to where
# it sits between their centres, so neither the 3-4 px bands nor the 2.8 deg
# bins show as steps. Never across the core-dot edge (7), a range ring or a
# glyph's lens and rim radii, so those stay exact.
BAYER = (0, 8, 2, 10, 12, 4, 14, 6, 3, 11, 1, 9, 15, 7, 13, 5)

# built by load() (one step each): CLSD, ANG_C / ANG_S, CT
CLSD = None
ANG_C = None
ANG_S = None
CT = None


def _cls_dither():
    """CLSD[i * 16 + j]: the class of ring index i at Bayer phase j."""
    rc = [(EDGES[c] + EDGES[c + 1] - 1) >> 1 for c in range(NC)]
    fixed = _fixed()
    cd = bytearray(N_IDX * 16)
    for i in range(N_IDX):
        c = CLS[i]
        lo = c if i >= rc[c] else c - 1
        hi = lo + 1
        for j in range(16):
            k = c
            if lo >= 0 and hi < NC and EDGES[hi] not in fixed:
                k = hi if (i - rc[lo]) * 32 > (2 * j + 1) * (rc[hi] - rc[lo]) else lo
            cd[i * 16 + j] = k
    return cd


def _ang_dither():
    """Per Bayer phase j, the angle at which a top-right quadrant pixel
    moves into bin m, m = 0..32, at (m + 0.5 - (j + 0.5) / 16) bins, as Q16
    (cos, sin) so the test is ``dx * cos >= dy * sin`` (right also past
    90 deg). A pixel short of threshold 0 is in bin -1 (127, left of 12
    o'clock), one past threshold 32 in bin 32 (below 3 o'clock); the XOR
    mirrors keep both right."""
    tc = array.array("i", bytes(4 * 16 * 33))
    ts = array.array("i", bytes(4 * 16 * 33))
    for j in range(16):
        for m in range(33):
            a = math.radians((m + 0.5 - (j + 0.5) / 16) * 90.0 / 32)
            tc[j * 33 + m] = int(round(65536 * math.cos(a)))
            ts[j * 33 + m] = int(round(65536 * math.sin(a)))
    return tc, ts


# static kernel table ct ('H'): decay exp(-k/32) | PROF | class lo | hi | vig | ring flag
C_EXP = const(0)
C_PROF = const(256)
C_LO = 321
C_HI = C_LO + NC
C_VIG = C_HI + NC
C_RING = C_VIG + NC
C_N = C_RING + NC

# prm ('i') layout
H_NC = const(0)
H_IRIS = const(1)
H_RIMHI = const(2)
H_FL = const(3)
H_GL = const(4)
H_GINV = const(5)
H_RING = const(6)
H_VMAX = const(7)
H_DIM = const(8)
H_LIFT = const(9)
H_RIM = const(10)
H_RIMQ = const(11)
H_IRISC = const(12)
H_CORE = const(13)
H_FILLR = const(14)
H_FILLV = const(15)
H_PINGR = const(16)          # ping radius, Q8 px (-1 = off)
H_PINGA = const(17)          # ping amplitude, Q8 levels (faded in)
H_NB = const(18)             # beams
H_LT = const(19)             # 1: rebuild the level -> colour tables (cap, dim, lift, LUT)
O_A0 = const(20)             # out: first bin of the changed arc
O_AN = const(21)             # out: bins in it (0 = nothing changed)
O_NCH = const(22)            # out: changed bins
H_PINGL = const(23)          # ping lead, Q8 px (>= 1: max(3 px, 1.5 x speed / fps))
H_FW = const(24)             # trail fade-in width outside the rim, px (0: none)
H_BM = const(26)             # beams: angle, dir (+1 cw / -1 acw), head, 1/L (Q16 x 32), amp, ghost
PRM_N = H_BM + 6 * MAXB

# pal ('H'): the NC x 128 palette | level -> colour (mixed LUT) | (grey LUT), levels 0..V7
NCB = NC * NBIN
P_LT = NCB
P_GT = NCB + V7 + 1
PAL_N = NCB + 2 * (V7 + 1)
# tq ('i'): trail per bin | ghost per bin | changed per bin | per class: its colour when
# the whole row is one colour (lens, rim), else -1
T_N = 3 * NBIN + NC

# rad.tab ('H', ui/themes/base.py): EXPT @170, mixed LUT @458, grey LUT @522

_PSRC = """
def sonar_pal(pal, rt, ct, prm, tq):
    pp = ptr16(pal)
    rp = ptr16(rt)
    cp = ptr16(ct)
    qp = ptr32(prm)
    tp = ptr32(tq)
    nb = qp[18]
    anyg = 0
    b = 0
    while b < 128:
        th = (b << 6) + 32
        best = 0
        g = 0
        k = 0
        while k < nb:
            o = 26 + k * 6
            d = ((qp[o] - th) * qp[o + 1]) & 8191
            hd = qp[o + 2]
            if d >= %(LEAD_AT)d:
                v = 256 - (((8192 - d) << 8) // %(LEAD_U)d)
            elif d <= hd:
                v = 256
            else:
                kk = ((d - hd) * qp[o + 3]) >> 16
                v = 0
                if kk < 256:
                    v = int(cp[kk])
            tv = (qp[o + 4] * v) >> 8
            if tv > best:
                best = tv
                g = qp[o + 5]
            k += 1
        tp[b] = best
        tp[128 + b] = g
        if g:
            anyg = 1
        tp[256 + b] = 0
        b += 1
    vmax = qp[7]
    if qp[19]:
        dim = qp[8]
        lift = qp[9]
        v = 0
        while v <= %(V7)d:
            x = v
            if x > vmax:
                x = vmax
            if dim != 256:
                x = (x * dim) >> 8
            j = ((x * 9 + 128) >> 8) + lift
            if j > 63:
                j = 63
            pp[%(P_LT)d + v] = rp[458 + j]
            pp[%(P_GT)d + v] = rp[522 + j]
            v += 1
    nc = qp[0]
    iris = qp[1]
    rimhi = qp[2]
    fl = qp[3]
    gl = qp[4]
    ginv = qp[5]
    ring = qp[6]
    rim = qp[10]
    rimq = qp[11]
    irisc = qp[12]
    core = qp[13]
    fr = qp[14]
    fv = qp[15]
    fe = fv + (fv >> 1)
    fhi = 0
    if fr > 0:
        fhi = fr + 3
    pr = qp[16]
    pa = qp[17]
    pl = qp[23]
    c = 0
    while c < nc:
        lo = int(cp[%(C_LO)d + c])
        hi = int(cp[%(C_HI)d + c])
        rc = (lo + hi - 1) >> 1
        o = c << 7
        col = -1
        w = 0
        if rc < iris:
            col = irisc
        elif rc < rimhi:
            col = rim
            if rim < 0:
                col = int(pp[%(P_LT)d + rimq])
        else:
            n = ((rc - iris) * ginv) >> 8
            if n > 255:
                n = 255
            base = fl + ((gl * int(rp[170 + n])) >> 8)
            if int(cp[%(C_RING)d + c]):
                base += ring
            if pr >= 0:
                dq = (rc << 8) - pr
                if dq >= 0:
                    kk = (dq << 6) // pl
                else:
                    kk = ((0 - dq) << 6) // %(TRAILQ)d
                if kk < 64:
                    base += (pa * int(cp[%(C_PROF)d + kk])) >> 8
            vig = int(cp[%(C_VIG)d + c])
            v0 = (base * vig) >> 8
            vf = 0
            if iris == 0:
                if hi <= 7:
                    vf = core
            if rc < fhi:
                if rc < fr:
                    if fv > vf:
                        vf = fv
                elif fe > vf:
                    vf = fe
            fw = qp[24]
            x = rc - rimhi
            if x >= fw:
                w = vig
            elif x > 0:
                w = ((((x * x * (3 * fw - 2 * x)) << 8) // (fw * fw * fw)) * vig) >> 8
            if w == 0:
                v = v0
                if v < vf:
                    v = vf
                if v > %(V7)d:
                    v = %(V7)d
                col = int(pp[%(P_LT)d + v])
        if col >= 0:
            if col != tp[%(T_LAST)d + c]:
                tp[%(T_LAST)d + c] = col
                b = 0
                while b < 128:
                    if int(pp[o + b]) != col:
                        pp[o + b] = col
                        tp[256 + b] = 1
                    b += 1
            c += 1
            continue
        tp[%(T_LAST)d + c] = -1
        b = 0
        if anyg:
            while b < 128:
                tv = (tp[b] * w) >> 8
                v = v0 + tv
                if v < vf:
                    v = vf
                if v > %(V7)d:
                    v = %(V7)d
                col = int(pp[%(P_LT)d + v])
                if tp[128 + b]:
                    if tv > v0:
                        col = int(pp[%(P_GT)d + v])
                if int(pp[o + b]) != col:
                    pp[o + b] = col
                    tp[256 + b] = 1
                b += 1
        else:
            while b < 128:
                v = v0 + ((tp[b] * w) >> 8)
                if v < vf:
                    v = vf
                if v > %(V7)d:
                    v = %(V7)d
                col = int(pp[%(P_LT)d + v])
                if int(pp[o + b]) != col:
                    pp[o + b] = col
                    tp[256 + b] = 1
                b += 1
        c += 1
    nch = 0
    b = 0
    while b < 128:
        nch += tp[256 + b]
        b += 1
    qp[22] = nch
    if nch == 0:
        qp[20] = 0
        qp[21] = 0
        return
    if nch == 128:
        qp[20] = 0
        qp[21] = 128
        return
    run = 0
    bl = 0
    be = 0
    b = 0
    while b < 256:
        if tp[256 + (b & 127)]:
            run = 0
        else:
            run += 1
            if run > bl:
                bl = run
                be = b
        b += 1
    qp[20] = (be + 1) & 127
    qp[21] = 128 - bl
""" % {"LEAD_AT": ANG - LEAD_U, "LEAD_U": LEAD_U, "C_LO": C_LO, "C_HI": C_HI, "C_RING": C_RING,
       "C_PROF": C_PROF, "C_VIG": C_VIG, "TRAILQ": PING_TRAIL * Q8, "V7": V7,
       "P_LT": P_LT, "P_GT": P_GT, "T_LAST": 3 * NBIN}

_DSRC = """
def sonar_diff(a, b, out):
    ap = ptr16(a)
    bp = ptr16(b)
    op = ptr32(out)
    n = 0
    lo = 255
    hi = 0
    i = 0
    while i < 170:
        x = int(ap[i])
        if x != int(bp[i]):
            bp[i] = x
            if n < 8:
                op[3 + n] = i
            n += 1
            if i < lo:
                lo = i
            hi = i
        i += 1
    op[0] = n
    op[1] = lo
    op[2] = hi
"""

# Field rows y0..y0+h-1 from the class x angle quadrant map ``q`` (16-bit
# entries, two per 32-bit load): row y reads quadrant row y (y < 120, bins
# as stored on the right, ^ 127 on the left) or 239 - y (^ 63 right, ^ 64
# left); each entry's colour lands at x and its mirror 239 - x.
_BSRC = """
def sonar_blit(dst, q, pal, y0: int, h: int):
    dp = ptr32(dst)
    qp = ptr32(q)
    pp = ptr16(pal)
    row = 0
    while row < h:
        y = y0 + row
        rt = row * 120 + 60
        lt = rt - 1
        if y < 120:
            i = y * 60
            e = i + 60
            while i < e:
                w = qp[i]
                a = w & 0xFFFF
                b = (w >> 16) & 0xFFFF
                dp[rt] = pp[a] | (pp[b] << 16)
                dp[lt] = pp[b ^ 127] | (pp[a ^ 127] << 16)
                w = qp[i + 1]
                a = w & 0xFFFF
                b = (w >> 16) & 0xFFFF
                dp[rt + 1] = pp[a] | (pp[b] << 16)
                dp[lt - 1] = pp[b ^ 127] | (pp[a ^ 127] << 16)
                rt += 2
                lt -= 2
                i += 2
        else:
            i = (239 - y) * 60
            e = i + 60
            while i < e:
                w = qp[i]
                a = w & 0xFFFF
                b = (w >> 16) & 0xFFFF
                dp[rt] = pp[a ^ 63] | (pp[b ^ 63] << 16)
                dp[lt] = pp[b ^ 64] | (pp[a ^ 64] << 16)
                w = qp[i + 1]
                a = w & 0xFFFF
                b = (w >> 16) & 0xFFFF
                dp[rt + 1] = pp[a ^ 63] | (pp[b ^ 63] << 16)
                dp[lt - 1] = pp[b ^ 64] | (pp[a ^ 64] << 16)
                rt += 2
                lt -= 2
                i += 2
        row += 1
"""


def _ident(x):
    return x


def _plain(src, name):
    ns = {"ptr16": _ident, "ptr32": _ident}
    exec(src, ns)
    return ns[name]


def _viper(src, name):
    try:
        vs = {}
        exec("@micropython.viper" + src, vs)
    except Exception:  # noqa: BLE001 - no viper (CPython, wasm)
        return None
    return vs[name]


def _ctab():
    ct = array.array("H", bytes(2 * C_N))
    for k in range(256):             # exp(-k / 32), eased to 0 where it falls under TAIL_CUT
        v = (math.exp(-k / 32.0) - TAIL_CUT) / (1.0 - TAIL_CUT)
        ct[C_EXP + k] = int(256 * v + 0.5) if v > 0 else 0
    for k in range(65):
        ct[C_PROF + k] = PROF[k]
    for c in range(NC):
        lo = EDGES[c]
        hi = EDGES[c + 1]
        ct[C_LO + c] = lo
        ct[C_HI + c] = hi
        ct[C_VIG + c] = VIG[(lo + hi - 1) >> 1]
        ct[C_RING + c] = 1 if (hi == lo + 1 and lo in RINGS) else 0
    return ct


def _check_rtab():
    rt = array.array("H", [(i * 37 + 11) & 0xFFFF for i in range(586)])
    for i in range(458):
        rt[i] &= 0x1FF
    return rt


# Self-check frames for the palette kernel: (header, beams, ping lead in Q8,
# trail fade width in px). Between them they reach every branch: lens, flat
# and ramp rims, core floor, the fill and its edge (the last frame: fill
# radius 86 under a dim ping, so the r 88 class sits on fr + 2, the last
# index of the 3 px edge, where the edge level beats the base), the ping
# inward and outward with a narrow and a wide lead, menu dim and sun lift,
# ghosts, both directions, the leading edge, the decay table's end, and the
# trail fade with the iris closed (0), opening (6), open (10) and widened
# while the lens opens (30).
_CHECK = (
    ((NC, 0, 0, 300, 900, 900, 400, 1792, 256, 0, -1, 1400, 0x6200, 1536, 0, 0, 3000, 1792),
     ((0, 1, 46, 3000, 1500, 0), (4096, 1, 46, 1500, 900, 1), (8000, -1, 46, 800, 600, 0)),
     2304, 0),
    ((NC, 6, 9, 300, 900, 900, 400, 1792, 256, 0, -1, 1400, 0x6200, 0, 0, 0, -1, 0),
     ((2000, 1, 46, 3000, 1500, 0), (7000, -1, 46, 1200, 1700, 1)), 768, 6),
    ((NC, 64, 67, 200, 1300, 2000, 300, 1280, 128, 9, 0x1234, 1400, 0x6200, 0, 90, 1024, -1, 0),
     ((100, 1, 200, 3000, 1800, 1), (6000, 1, 46, 400, 1200, 0)), 768, 10),
    ((NC, 44, 47, 0, 0, 900, 0, 1792, 128, 9, -1, 1300, 0x6200, 0, 86, 1024, 20000, 1792),
     (), 9216, 30),
)


# the classes the self-check runs (their radii hit every branch of _CHECK)
CK_R = (3, 10, 14, 30, 45, 65, 68, 76, 80, 88, 92, 150)


def _check_ct():
    """CT with the first len(CK_R) class slots holding the classes of CK_R."""
    ck = array.array("H", CT)
    for s in range(len(CK_R)):
        c = CLS[CK_R[s]]
        for off in (C_LO, C_HI, C_VIG, C_RING):
            ck[off + s] = CT[off + c]
    return ck


def _pal_check(ka, kb, ok):
    """pal_agrees in steps (a generator): yields after the set-up and after
    each kernel run pair; ``ok[0]`` is True once every run agreed."""
    ok[0] = False
    rt = _check_rtab()
    ck = _check_ct()
    st = []
    for fn in (ka, kb):
        tq = array.array("i", [7] * T_N)
        for c in range(NC):
            tq[3 * NBIN + c] = -1
        st.append((fn, array.array("H", range(PAL_N)), array.array("i", bytes(4 * PRM_N)), tq))
    yield
    lk = None
    for hdr, beams, pl, fw in _CHECK:
        k = (hdr[H_VMAX], hdr[H_DIM], hdr[H_LIFT])
        lt = 1 if k != lk else 0
        lk = k
        for fn, pal, prm, tq in st:
            for i in range(len(hdr)):
                prm[i] = hdr[i]
            prm[H_NC] = len(CK_R)
            prm[H_NB] = len(beams)
            prm[H_LT] = lt
            prm[H_PINGL] = pl
            prm[H_FW] = fw
            for j in range(len(beams)):
                for m in range(6):
                    prm[H_BM + 6 * j + m] = beams[j][m]
        for rep in range(2):
            for fn, pal, prm, tq in st:
                if rep:
                    prm[H_LT] = 0
                    for j in range(len(beams)):
                        prm[H_BM + 6 * j] += 100
                fn(pal, rt, ck, prm, tq)
            a = st[0]
            b = st[1]
            if a[1] != b[1] or a[2] != b[2] or a[3] != b[3]:
                return
            yield
    ok[0] = True


def pal_agrees(ka, kb):
    """True if palette kernels ``ka`` and ``kb`` leave the same palette,
    work arrays and outputs after each _CHECK frame (synthetic tables, the
    CK_R classes), run in sequence on the same arrays, each frame twice
    (the second time with the beams moved and the colour tables kept)."""
    ok = [False]
    for _ in _pal_check(ka, kb, ok):
        pass
    return ok[0]


def diff_agrees(ka, kb):
    res = []
    for fn in (ka, kb):
        a = array.array("H", [(i * 7) & 0xFF for i in range(N_IDX)])
        b = array.array("H", a)
        for i in (3, 40, 41, 120, 169):
            b[i] ^= 0x55
        out = array.array("i", [0] * 12)
        fn(a, b, out)
        c = array.array("H", a)
        for i in range(0, N_IDX, 7):
            c[i] ^= 1
        out2 = array.array("i", [0] * 12)
        fn(c, b, out2)
        res.append(bytes(b) + bytes(out) + bytes(out2))
    return res[0] == res[1]


def _compile(src, name, agrees):
    py = _plain(src, name)
    vp = _viper(src, name)
    if vp is None:
        return py, "python"
    if not agrees(py, vp):
        return py, "python (viper self-check failed)"
    return vp, "viper"


# the kernels, set by load(): sonar_pal / sonar_diff (viper after their
# self-check, else plain), _BLIT_V (viper, None without it; each theme object
# checks it on its own buffers) and _BLIT_PY (its plain version, for that check)
sonar_pal = None
PAL_KIND = None
sonar_diff = None
DIFF_KIND = None
_BLIT_V = None
_BLIT_PY = None
_PAL_PY = None               # palette kernel compiles waiting for the self-check
_PAL_VP = None               # (False: no viper on this port)


def load():
    """One-time work in steps (a generator, ui/themes/base.py "Loading"):
    the dither and kernel tables, each kernel's compiles and self-check.
    Idempotent: a step already done is skipped, also after a load
    abandoned half-way (a palette self-check cut short starts over)."""
    global CLSD, ANG_C, ANG_S, CT, sonar_pal, PAL_KIND, sonar_diff, DIFF_KIND
    global _BLIT_V, _BLIT_PY, _PAL_PY, _PAL_VP
    if CLSD is None:
        CLSD = _cls_dither()
        yield
    if ANG_S is None:
        tc, ts = _ang_dither()
        ANG_C = tc
        ANG_S = ts
        yield
    if CT is None:
        CT = _ctab()
        yield
    if sonar_pal is None:
        if _PAL_PY is None:
            _PAL_PY = _plain(_PSRC, "sonar_pal")
            yield
        if _PAL_VP is None:
            vp = _viper(_PSRC, "sonar_pal")
            _PAL_VP = False if vp is None else vp
            yield
        if _PAL_VP is False:
            PAL_KIND = "python"
            sonar_pal = _PAL_PY
        else:
            ok = [False]
            for _ in _pal_check(_PAL_PY, _PAL_VP, ok):
                yield
            PAL_KIND = "viper" if ok[0] else "python (viper self-check failed)"
            sonar_pal = _PAL_VP if ok[0] else _PAL_PY
        _PAL_PY = None
        _PAL_VP = None
    if sonar_diff is None:
        k, kind = _compile(_DSRC, "sonar_diff", diff_agrees)
        DIFF_KIND = kind
        sonar_diff = k
        yield
    if _BLIT_PY is None:
        _BLIT_V = _viper(_BSRC, "sonar_blit")
        _BLIT_PY = _plain(_BSRC, "sonar_blit")
        yield


def loaded():
    """Run load() to the end (tests, tools; ``ui.themes.make`` does it too)."""
    for _ in load():
        pass


# ---- maps (built per theme object) -----------------------------------------------
def build_rows(idx, q, xs, r0, r1):
    """Quadrant map rows r0..r1-1 into ``q`` and ``xs`` (see build_maps)."""
    tc = ANG_C
    ts = ANG_S
    cd = CLSD
    for row in range(r0, r1 if r1 < 120 else 120):
        dy = 2 * (119 - row) + 1
        base = row * W + 120
        o = row * 120
        rb = (row & 3) * 4
        ab = ((row + 1) & 3) * 4
        r0_ = BAYER[rb]
        r1_ = BAYER[rb + 1]
        r2_ = BAYER[rb + 2]
        r3_ = BAYER[rb + 3]
        a0 = BAYER[ab + 2] * 33 + 1
        a1 = BAYER[ab + 3] * 33 + 1
        a2 = BAYER[ab] * 33 + 1
        a3 = BAYER[ab + 1] * 33 + 1
        k0 = k1 = k2 = k3 = -1
        st = row // SH
        sb = (239 - row) // SH
        for x in range(0, 120, 4):
            dx = 2 * x + 1
            while k0 < 32 and dx * tc[a0 + k0] >= dy * ts[a0 + k0]:
                k0 += 1
            while k1 < 32 and (dx + 2) * tc[a1 + k1] >= dy * ts[a1 + k1]:
                k1 += 1
            while k2 < 32 and (dx + 4) * tc[a2 + k2] >= dy * ts[a2 + k2]:
                k2 += 1
            while k3 < 32 and (dx + 6) * tc[a3 + k3] >= dy * ts[a3 + k3]:
                k3 += 1
            b = base + x
            e = o + x
            c = 119 - x                       # the mirrored column on the left
            i = idx[b]
            q[e] = (cd[i * 16 + r0_] << 7) | (k0 & 127)
            i *= NS
            if c < xs[i + st]:
                xs[i + st] = c
                xs[i + sb] = c
            c -= 1
            i = idx[b + 1]
            q[e + 1] = (cd[i * 16 + r1_] << 7) | (k1 & 127)
            i *= NS
            if c < xs[i + st]:
                xs[i + st] = c
                xs[i + sb] = c
            c -= 1
            i = idx[b + 2]
            q[e + 2] = (cd[i * 16 + r2_] << 7) | (k2 & 127)
            i *= NS
            if c < xs[i + st]:
                xs[i + st] = c
                xs[i + sb] = c
            c -= 1
            i = idx[b + 3]
            q[e + 3] = (cd[i * 16 + r3_] << 7) | (k3 & 127)
            i *= NS
            if c < xs[i + st]:
                xs[i + st] = c
                xs[i + sb] = c


def build_maps(idx):
    """(q16, xs) from the ring-index map ``idx`` (after load()).

    q16: the top-right quadrant (rows 0..119, columns 120..239) of the class
    x angle map, ``class << 7 | bin``: the class of the pixel's ring index
    and the 2.8125 deg bin of its centre's angle clockwise from 12 o'clock
    (0..31, or 127 / 32 just across the axes), both through the ordered
    dither (CLSD; ANG_C / ANG_S, walked by one pointer per column phase, as
    the angle grows along a row). xs[i * NS + k]: the first column of ring
    index i in strip k (255 = none; the last is 239 - that, the map is
    symmetric). Integer-only; unrolled by the 4 column phases. A theme
    builds it MAP_ROWS rows a step (``build_rows``, Sonar.prepare)."""
    q = array.array("H", bytes(2 * 14400))
    xs = bytearray(b"\xff" * (N_IDX * NS))
    build_rows(idx, q, xs, 0, 120)
    return q, xs


def full_rows(q, m, r0, r1):
    """Full-map rows r0..r1-1 and their mirrors 239-r1+1..239-r0 into ``m``."""
    for row in range(r0, r1 if r1 < 120 else 120):
        o = row * 120
        t = row * W
        bt = (239 - row) * W
        for x in range(120):
            e = q[o + x]
            m[t + 120 + x] = e
            m[t + 119 - x] = e ^ 127
            m[bt + 120 + x] = e ^ 63
            m[bt + 119 - x] = e ^ 64


def build_full16(q):
    """The full 240x240 map from the quadrant (bins XOR'd per quadrant)."""
    m = array.array("H", bytes(2 * W * W))
    full_rows(q, m, 0, 120)
    return m


class Sonar(Theme):
    name = "sonar"

    def __init__(self, r):
        # allocations only: the maps are built and the blit kernel checked
        # in prepare(), after load() has set the module's tables and kernels
        Theme.__init__(self, r)
        self.rad = Radial("sonar")
        self.ramps = self.rad.ramps
        self.irisc = T.THEME_IRIS["sonar"]
        self.q16 = array.array("H", bytes(2 * 14400))
        self.xs = bytearray(b"\xff" * (N_IDX * NS))
        # the palette starts as distinct colours for the blit self-check; the
        # first beam frame rewrites every entry (all rows marked unknown)
        self.pal2 = array.array("H", range(PAL_N))
        self.prm = array.array("i", bytes(4 * PRM_N))
        self.tq = array.array("i", bytes(4 * T_N))
        self.prev = array.array("H", bytes(2 * N_IDX))  # last ring palette (quiet moments)
        self.dout = array.array("i", bytes(4 * 12))
        self.bk = None
        self.m16_fb = None
        self.pal2_fb = None
        self.bkind = None
        if framebuf is not None:
            self.pal2_fb = framebuf.FrameBuffer(self.pal2, NCB, 1, framebuf.RGB565)
        # beams: the current set (angles per frame) and NF sets fading out
        self.cang = array.array("i", bytes(4 * 6))
        self.gh = bytearray(6)                          # ghost flag per current beam
        self.fn = bytearray(NF)                         # beams in each fading set (0 = free)
        self.fdir = array.array("i", bytes(4 * NF))
        self.flinv = array.array("i", bytes(4 * NF))
        self.fa = array.array("i", bytes(4 * NF))      # its shown peak, Q8 levels
        self.fang = array.array("i", bytes(4 * 6 * NF))
        self.fgh = bytearray(6 * NF)
        self.reset()

    def prepare(self):
        """The class x angle map MAP_ROWS rows a step, then the blit kernel's
        check on the real band buffers, or (framebuf path) the full map."""
        r = self.r
        idx = r.map.idx
        for r0 in range(0, 120, MAP_ROWS):
            build_rows(idx, self.q16, self.xs, r0, r0 + MAP_ROWS)
            yield
        if framebuf is None:
            return
        k = _BLIT_V
        if k is not None:
            from ui.field import _aligned
            ok = _aligned(self.q16) and _aligned(self.pal2)
            for b in r.bands:
                ok = ok and _aligned(b)
            if ok and self.blit_agrees(k, r.bands[0]):
                self.bk = k
                self.bkind = "viper"
            else:
                self.bkind = "framebuf (kernel self-check failed)"
            yield
        if self.bk is None:                     # the full map: only for the framebuf path
            m = array.array("H", bytes(2 * W * W))
            yield
            for r0 in range(0, 120, FULL_ROWS):
                full_rows(self.q16, m, r0, r0 + FULL_ROWS)
                yield
            self.m16_fb = framebuf.FrameBuffer(m, W, W, framebuf.RGB565)
            if self.bkind is None:
                self.bkind = "framebuf"

    def reset(self):
        Theme.reset(self)
        self.ramps.snap()
        self.mom = M_LIVE
        self.cls = False             # this frame drew through the class x angle map
        self.cb = 0                  # current set: beams, dir, period, L (angle units)
        self.cdir = 0
        self.cper = 0
        self.cl = 1
        self.ca = 0                  # its shown peak, Q8 levels (slewed toward its target)
        for s in range(NF):          # no set fading out
            self.fn[s] = 0
            self.fa[s] = 0
        self.bi = 0                  # the beams' intensity, Q8 (slewed toward I)
        self.lms = 0                 # listening beam clock (ms into its turn)
        self.ls = 0                  # the field's last spawn we saw
        self.brth = 0                # breathing clock, ms mod breathe_ms
        self.ring = 0                # range ring level (slewed)
        self.ping = False
        self.page = 0                # ms since the ping started
        self.pr0 = 0                 # its start radius, Q8 px
        self.pr = -1                 # this frame's: radius (Q8 px, -1 = none), peak
        self.pa = 0                  # (faded in, Q8 levels) and lead (Q8 px)
        self.plq = PING_LEAD * Q8
        tq = self.tq
        for c in range(NC):          # every palette row is redrawn on the next frame
            tq[3 * NBIN + c] = -1
        self.lt = -1                 # vmax, dim, lift the colour tables were built for
        self.iqv = 0                 # the screen's params (held in MENU)
        self.z = 0
        self.ghost = 0
        self.sun = False
        self.sunf = 0                # sun floor, slewed (Q8 levels)
        self.vmx = V7                # level cap, slewed toward the renderer's (saver)

    def blit_agrees(self, kern, buf):
        """True if ``kern`` writes the same rows into the band ``buf`` as its
        plain version (the same source; tests/test_theme_sonar.py checks
        that against the framebuf path) does into a word array: 4 rows at
        the top, across the middle and at the bottom, from the real quadrant
        map through ``pal2``'s distinct start colours. Leaves ``buf`` dirty
        (the next frame redraws it)."""
        q = self.q16
        qw = array.array("I", bytes(4 * 7200))
        for row in (0, 1, 2, 3, 118, 119):             # the quadrant rows those bands read
            o = row * 60
            for i in range(o, o + 60):
                qw[i] = q[2 * i] | (q[2 * i + 1] << 16)
        py = _BLIT_PY
        dst = array.array("I", bytes(4 * 480))
        mv = memoryview(buf)
        junk = b"\x5a" * 1920                          # so a pixel the kernel skips shows
        for y0 in (0, 118, 236):
            py(dst, qw, self.pal2, y0, 4)
            mv[0:1920] = junk
            kern(buf, q, self.pal2, y0, 4)
            if bytes(mv[0:1920]) != bytes(dst):
                return False
        return True

    # ---- per frame -------------------------------------------------------------
    def build(self, p, t, core, rim, vmax, lift):
        dt = self.clock(p, t)
        wake = self.wake
        f = self.f
        menu = self.r._scr == S_MENU
        pm = self.mom
        m = pm if (menu and not wake) else self.moment(p)
        self.mom = m
        if wake or not menu:                 # MENU holds the screen's params
            if menu:                         # a wake in the MENU: those of the screen under it
                lp = self.live(p)
                iq = int(lp.intensity * 256)
                self.iqv = 0 if iq < 0 else (256 if iq > 256 else iq)
            else:
                lp = p
                self.iqv = self.iq()
            self.z = self.zone(lp)
            self.ghost = 0 if lp.ring_live else 1
            self.sun = lp.sun
        iq = self.iqv
        step = 0 if menu else f.flash        # the flash-limit slew per frame (0: frozen)
        fs = (step * XF_K + 255) >> 8        # Sonar's own fades: half that
        # the sun floor and the saver's cap come and go at that rate (the
        # sun's LUT lift is instant, as Ripple's)
        st = SUN_FLOOR if self.sun else 0
        if wake:
            self.sunf = st
            self.vmx = vmax
        elif fs:
            d = st - self.sunf
            self.sunf = st if -fs <= d <= fs else self.sunf + (fs if d > 0 else -fs)
            d = vmax - self.vmx
            self.vmx = vmax if -fs <= d <= fs else self.vmx + (fs if d > 0 else -fs)
        vmax = self.vmx
        rm = self.ramps
        xf = rm.dur > 0
        rp = rm.ramp
        rm.follow(f, t)
        lut = xf or rm.dur > 0 or rm.ramp != rp
        # base levels: the field's, mapped onto Sonar's (module docstring)
        fl = SFL_A + (((f.fl - FL_A) * SFL_K) >> 8)
        if fl < 0:
            fl = 0
        if fl < self.sunf:
            fl = self.sunf
        gl = SGL_A + (((f.gl - GL_A) * SGL_K) >> 8)
        if gl < 0:
            gl = 0
        gr = f.gr
        sw = f.stand_w                       # FOUND crossfade weight
        if sw:
            fl += ((FOUND_FL - fl) * sw) >> 8
            gl += ((FOUND_GL - gl) * sw) >> 8
            gr += ((FOUND_GR - gr) * sw) >> 8
        # range rings
        self.brth = (self.brth + dt) % BREATHE_MS
        deg = self.brth * 360 // BREATHE_MS
        if m == M_STILL:
            rt = STILL_RING[0] + ((STILL_RING[1] * ((16384 - COS[deg]) >> 7)) >> 8)
        elif m == M_FOUND:
            rt = FOUND_RING[0] + ((FOUND_RING[1] * ((16384 + SIN[deg]) >> 7)) >> 8)
        else:
            rt = RL_A + ((RL_B * iq) >> 8)
        if wake:
            self.ring = rt
        elif step:
            d = rt - self.ring
            self.ring = rt if -step <= d <= step else self.ring + (step if d > 0 else -step)
        # FOUND ping: from the lens it opens to, as the field's burst
        if m == M_FOUND:
            if pm != M_FOUND or wake:
                self.ping = (not wake) or p.burst
                self.page = 0
                self.pr0 = f.iris_to << 8
            elif self.ping:
                self.page += dt
        else:
            self.ping = False
        pr = -1
        pa = 0
        plq = PING_LEAD * Q8
        if self.ping:
            run = ((PING_PX_S * self.page) << 8) // 1000       # Q8 px travelled
            pr = self.pr0 + run
            if pr > (N_IDX + PING_TRAIL) << 8:
                self.ping = False
                pr = -1
            else:
                fade = run // PING_FADE                         # 12 px fade-in, Q8
                pa = PING_AMP if fade >= 256 else (PING_AMP * fade) >> 8
                aa = (AA_K * PING_PX_S * f.dt) // 1000          # 1.5 x speed / fps, Q8 px
                if aa > plq:
                    plq = aa
        self.pr = pr
        self.pa = pa
        self.plq = plq
        nb = self._beams(p, t, m, dt, wake, menu, iq, step, fs)
        cls = nb > 0
        sw_path = cls != self.cls
        self.cls = cls
        if cls:
            q = self.prm
            q[H_NC] = NC
            iris = f.iris
            q[H_IRIS] = iris
            q[H_RIMHI] = iris + T.IRIS_RIM_PX if iris > 0 else 0
            q[H_FL] = fl
            q[H_GL] = gl
            q[H_GINV] = (64 * Q8 * Q8) // (gr if gr > 2 * Q8 else 2 * Q8)
            q[H_RING] = self.ring
            q[H_VMAX] = vmax
            q[H_DIM] = f.dim
            q[H_LIFT] = lift
            q[H_RIM] = rim
            rq = fl + gl
            q[H_RIMQ] = V7 if rq > V7 else (RIM_MIN if rq < RIM_MIN else rq)
            q[H_IRISC] = self.irisc
            q[H_CORE] = core
            q[H_FILLR] = f.fill_r if f.fill_v else 0
            q[H_FILLV] = f.fill_v
            q[H_PINGR] = pr
            q[H_PINGA] = pa
            q[H_PINGL] = plq
            # the trail fades in outside the rim over FADE_PX (over the iris
            # radius while that is less), widened by what the lens still has
            # to open, so an opening lens clears the trails ahead of it
            fw = iris if iris < FADE_PX else FADE_PX
            if iris > 0 and f.iris_to > iris:
                fw += f.iris_to - iris
            q[H_FW] = fw
            q[H_NB] = nb
            lk = (vmax << 17) | (f.dim << 8) | lift
            q[H_LT] = 1 if (lut or wake or sw_path or lk != self.lt) else 0
            self.lt = lk
            sonar_pal(self.pal2, self.rad.tab, CT, q, self.tq)
            if wake or sw_path:
                self.everything()
            else:
                self.clear_dirty()
                if q[O_AN]:     # one bin more each side: the dither (build_maps)
                    self._wedge((q[O_A0] - 1) & (NBIN - 1), q[O_AN] + 2)
            return
        rad = self.rad
        rad.setup(f, t, core, rim, vmax, lift, fl, gl, gr, 0)
        acc = rad.acc
        ring = self.ring
        for k in range(len(RINGS)):
            acc[RINGS[k]] += ring
        if pr >= 0 and pa > 0:
            rad.add_ring(pr, pa, (plq + 128) >> 8, PING_TRAIL)
        rad.run()
        o = self.dout
        sonar_diff(rad.pal_arr, self.prev, o)
        if wake or sw_path or lut:
            self.everything()
            return
        self.clear_dirty()
        n = o[0]
        if n > 8:
            h = o[2] + 1
            self.mark(119 - h, 119 - h, 120 + h, 120 + h)
        elif n:
            xs = self.xs
            sp = self.spans
            dd = self.dirty
            for j in range(n):
                b = o[3 + j] * NS
                for k in range(NS):
                    x0 = xs[b + k]
                    if x0 != 255:
                        dd |= 1 << k
                        if x0 < sp[2 * k]:
                            sp[2 * k] = x0
                        if 239 - x0 > sp[2 * k + 1]:
                            sp[2 * k + 1] = 239 - x0
            self.dirty = dd

    def _beams(self, p, t, m, dt, wake, menu, iq, step, fs):
        """Update the beam sets and write them into prm; returns how many
        beams the kernel draws (0: a quiet frame). ``step``: the flash-limit
        slew this frame, ``fs`` the beam sets' (both 0 in MENU)."""
        f = self.f
        z = self.z
        if m == M_LIVE:
            nb = BEAMS[z]
            dr = 1
            per = f.period or p.pulse_period_ms or 1000
        elif m == M_LISTEN:
            nb = 1
            dr = -1
            per = 0
        else:
            nb = 0
            dr = 0
            per = 0
        # the beams' intensity follows I no faster than the flash limit allows
        if wake:
            self.bi = iq
        elif step:
            s = step // BI_K + 1
            d = iq - self.bi
            self.bi = iq if -s <= d <= s else self.bi + (s if d > 0 else -s)
        # the sets fading out drop by the slew; a set at 0 is gone
        fn = self.fn
        fa = self.fa
        for s in range(NF):
            if fn[s]:
                if wake:
                    fn[s] = 0
                elif fs:
                    a = fa[s] - fs
                    if a > 0:
                        fa[s] = a
                    else:
                        fn[s] = 0
        ghost = self.ghost
        if wake or nb != self.cb or dr != self.cdir or per != self.cper:
            if self.cb and self.ca > 0 and not wake:
                # the visible set freezes where it is and fades out from its
                # shown level (both slots busy: the fainter one is dropped)
                if not fn[0]:
                    s = 0
                elif not fn[1]:
                    s = 1
                elif fa[0] <= fa[1]:
                    s = 0
                else:
                    s = 1
                o = s * 6
                for j in range(self.cb):
                    self.fang[o + j] = self.cang[j]
                    self.fgh[o + j] = self.gh[j]
                fn[s] = self.cb
                self.fdir[s] = self.cdir
                self.flinv[s] = (32 << 16) // self.cl
                fa[s] = self.ca
            self.cb = nb
            self.cdir = dr
            self.cper = per
            self.ca = 0
            self.lms = 0
            g = ghost if dr > 0 else 0
            for j in range(6):
                self.gh[j] = g
        # the beat: rotate the ghost flags as each beam takes the next one's place
        ls = f.last_spawn
        if ls != self.ls:
            if nb and dr > 0 and not menu and not wake and ticks_diff(ls, self.ls) > 0:
                gh = self.gh
                for j in range(nb - 1, 0, -1):
                    gh[j] = gh[j - 1]
                gh[0] = ghost
            self.ls = ls
        q = self.prm
        n = 0
        if nb:
            if dr > 0:
                age = self.beat_age(t)
                if age > per:
                    age = per
                ph = (age * ANG) // per
                for j in range(nb):
                    self.cang[j] = ((ph + j * ANG) // nb) & (ANG - 1)
                lq = TRAIL_A * Q8 + TRAIL_B * self.bi
                ta = AMP_A + ((AMP_B * self.bi) >> 8)
            else:
                pt = 360000 // LISTEN_DEG_S
                self.lms = (self.lms + dt) % pt
                self.cang[0] = (ANG - (self.lms * ANG) // pt) & (ANG - 1)
                lq = LISTEN_TRAIL_DEG * Q8
                ta = LISTEN_AMP
            if self.r._saver:
                ta = (ta * SAVER_PU) >> 8
            self.cl = (lq * ANG) // (360 * Q8)
            if wake:
                self.ca = ta
            elif fs:
                d = ta - self.ca
                self.ca = ta if -fs <= d <= fs else self.ca + (fs if d > 0 else -fs)
            a = self.ca
            linv = (32 << 16) // self.cl
            for j in range(nb):
                o = H_BM + 6 * n
                q[o] = self.cang[j]
                q[o + 1] = dr
                q[o + 2] = HEAD_U
                q[o + 3] = linv
                q[o + 4] = a
                q[o + 5] = self.gh[j] if dr > 0 else 0
                n += 1
        for s in range(NF):
            k = fn[s]
            if k:
                a = fa[s]
                dr = self.fdir[s]
                linv = self.flinv[s]
                o6 = s * 6
                for j in range(k):
                    o = H_BM + 6 * n
                    q[o] = self.fang[o6 + j]
                    q[o + 1] = dr
                    q[o + 2] = HEAD_U
                    q[o + 3] = linv
                    q[o + 4] = a
                    q[o + 5] = self.fgh[o6 + j] if dr > 0 else 0
                    n += 1
        return n

    def _wedge(self, a0, an):
        """Mark the bounding box of the sector of bins a0 .. a0 + an - 1
        (from the centre out past the corners)."""
        if an >= 96:
            self.everything()
            return
        d0 = (a0 * 360) >> 7
        d1 = ((a0 + an) * 360 + 127) >> 7
        x0 = 119
        x1 = 120
        y0 = 119
        y1 = 120
        for k in range(6):
            if k < 2:
                d = d1 if k else d0
            else:
                d = (k - 2) * 90
                if (d - d0) % 360 > d1 - d0:
                    continue
            d %= 360
            x = 119 + ((170 * SIN[d]) >> 14)
            y = 119 - ((170 * COS[d]) >> 14)
            if x - 1 < x0:
                x0 = x - 1
            if x + 2 > x1:
                x1 = x + 2
            if y - 1 < y0:
                y0 = y - 1
            if y + 2 > y1:
                y1 = y + 2
        self.mark(x0, y0, x1, y1)

    def blit(self, y0, buf, fb):
        if not self.cls:
            rad = self.rad
            self.r.map.blit(y0, rad.pal, rad.pal_arr, buf, fb)
            return
        k = self.bk
        if k is not None:
            k(buf, self.q16, self.pal2, y0, BH)
            return
        fb.blit(self.m16_fb, 0, -y0, -1, self.pal2_fb)
