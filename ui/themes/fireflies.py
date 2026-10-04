"""Fireflies (ui-spec §4A): blinking fireflies drift around the lens.

What it draws, per moment (``Theme.moment``; in the MENU the moment, zone,
intensity and beat of the screen under it, ``Theme.live``):

- Base, every moment: the theme's Radial palette through the renderer's ring
  map: the dark floor (the field's floor at I = 0, ``FL_A``; sun mode lifts
  it to 1.0 as for Ripple, at the field's flash-limit rate) and a soft halo
  of 0.9·I (THEME_PARAMS ``halo``) centred on C, falling off as
  e^-(r/70 px)^2 (``HALO_R``); outside an open lens it starts at the halo's
  level at the lens edge. On a moment change floor, halo level and halo
  radius crossfade over the field's 600 ms level crossfade (in_out_cubic,
  §5.3, ``XF_MS``) from what was shown; otherwise they follow their targets.
  Lens, rim, core dot, calibrate fill and vignette come from
  ``Radial.setup``.
- *Live:* ``count[z]`` fireflies (5/9/14/22, FAR-HOT) drift around the lens
  on deterministic paths: angle turning at 0.10-0.22 rad/s either way, radius
  ``(46 + 64·h)·(1 - 0.45·I) + 18`` px (``orbit_shrink``) with a ±20 % radial
  wobble at 0.08-0.18 rad/s (h: a per-fly hash; the mockup's numbers). Each
  blinks once per ring period P: it rises over 90 ms to its peak and then
  decays as e^-(t/260 ms) (``rise_ms``, ``fall_ms``), so in WARM and HOT a
  fly never quite goes out between beats. Its peak is at
  ``beat + (1 - sync)·h·P`` (``sync`` 0.25/0.5/0.8/1.0): fly 0 peaks on every
  ring spawn (the beat, ``beat_age``), and in HOT they all flash together on
  it. A ghost beat blinks grey at 0.6 amplitude (the field's ghost scale):
  whether a beat is a ghost is read once per ring spawn from the field's ring
  (``r_ghost``) and held until the next spawn, so blinks in flight never
  change colour between beats. The battery saver scales the blink amplitude
  by 0.7 (§8 ``pulse_amp`` x 0.7), easing in and out by at most the whole
  amplitude per 600 ms (0.7 to 1.0 takes about 180 ms). A zone change adds or
  drops flies at the end of one fixed list; the others carry on.
- *Listening:* three grey specks (``listen_specks``) drift in from the edge
  (r 125) to the lens over 7 s each (``listen_ms``), a third of a trip apart,
  fading in over the first 10 % and out over the last 15 %, each trip at a
  new angle (grey on every listening screen, PAIRING looking included). They
  do not blink.
- *Still:* nine fireflies (``still_count``) at r 60-90 drift slowly (0.3x the
  live angular rates) and glow softly, 0.45 ± 0.25 over 4.8 s (1.3 rad/s).
- *Scan:* the flies freeze where they are, dimmed to blink level 0.25; the
  halo becomes the live mirror (glow 1 + 5·I over ``glow_r_px``, as the
  field's).
- *Found:* 22 fireflies (``found_count``) circle the check at r 84 ± 10
  (0.2 rad/s round, the wobble at 0.8 rad/s) in the gold ramp, breathing
  0.5 ± 0.25 over 2.4 s (``breathe_ms``), neighbours 0.3 rad of phase apart.
  Entering FOUND (not on a wake) the flies take the ring's 22 places in their
  angular order, the ring turned to where they are, and each fly that was
  showing glides to its place from where it was over the 400 ms gold
  crossfade (in_out_cubic, ``GLIDE_MS``), the short way round in angle and
  straight in radius, so it never crosses the core dot (at 10 fps a fly
  moves up to about 30 px a frame at the glide's middle). The FOUND
  ``burst`` makes every fly blink once (not on a wake: §8, no intro).

The lens: flies and specks never draw on the lens, its rim or the core dot
(r <= 6): a path that comes closer than ``lens + 12 + 24`` px is squeezed
into the 24 px band beyond ``lens + 12`` (specks stop at ``lens + 6``).
"lens" is the larger of the lens edge (iris + rim) and the core dot, and,
while the PAIRING calibrate fill is on (``fill_v`` at the fill's level), the
fill's edge. It is state (``edge``): when the lens grows it is taken at once,
so the flies near it are pushed out with the opening edge (the iris opens
over 300 ms, eased out: its edge moves up to 40 px in the first 10 fps
frame, a fly near it up to about 22 px; a fly never moves further than the
edge); when it shrinks it follows at most 30 px/s (``LENS_SLEW``: 3 px a
frame at 10 fps, so with its own drift a fly moves at most about 4 px), so
the flies drift back in. During the calibrate fill they stay ahead of its
edge, so the still flies leave the dish as it fills; when the fill ends
(it fades out over about 0.6 s) they come back in from beyond its edge at
that rate.

How it keeps cost down:

- The base palette is rebuilt (viper ``pal_kernel`` on the watch) only when
  its inputs change: the halo level is quantised to 1/32 level and the FOUND
  standing wave is off, so in a steady moment the palette is reused and the
  changed regions are only the flies' boxes.
- Fireflies are prebuilt GS8 sprites (``NI`` intensity steps x ``NE`` blink
  levels, plus ``NA`` speck levels; built with floats once per process by
  ``load``), each cropped to its visible disc (up to 17x17, none when nothing
  shows), blitted over the full frame with ``fb.blit(sprite, x, y, KEY,
  pal)`` through one 32-entry RGB565 palette: a sprite pixel holds its level
  in 2/9-level steps (q), and palette entry q is the theme's mixed ramp (grey
  for a ghost beat and for the specks) at ``B + v_q``, B the floor-plus-halo
  level under the flies (additive, as the mockup). Entries below ``QK``
  (level < 1) are the key colour, where the base shows through (framebuf
  compares the key after the palette lookup). framebuf replaces pixels, it
  cannot take a maximum, so where two flies overlap the later one's faint rim
  would cut a dark crescent into the other: rim pixels at levels 1..2 are
  drawn on a 2x2 ordered dither (opaque with probability level - 1; at the
  watch's 0.16 mm pixels it reads as a soft edge), and the bright cores
  (level >= 5) are blitted again after every fly. The palette is rebuilt
  only when the ramp mix, menu dim, vmax, lift, B or the grey choice change.
- Per fly per frame (phase step, path, squeeze, FOUND glide, blink level,
  sprite, changed box) is one integer kernel over all flies
  (``fly_kernel``), compiled with ``@micropython.viper`` where the port has
  it and used only after a self-check against the same source run as plain
  Python (``kernel_agrees``), as ui/field.py does. Motion uses phase
  accumulators (2^20 per turn) advanced by ``dt`` (0 in MENU, so everything
  freezes; the blink reads ``beat_age``, which the field holds in MENU) and
  a 1024-entry Q14 sine table.
- Changed regions: the old and new sprite box of every fly that moved or
  changed sprite (every fly box when the fly palette changed), everything
  when the base palette changed (crossfades, menu dim, iris animating, halo
  level or fill changes) or on a wake.

Loading (base.py "Loading"): the import builds only small tables. ``load()``
builds the sprites (one intensity step a step), the kernel's tables (three
steps), compiles the plain and the viper kernel (one step each) and runs the
self-check, keeping each result in module globals so a load abandoned half
way resumes where it stopped. ``Fireflies.__init__`` allocates;
``prepare()`` fills the per-fly constants (hashes) and checks the buffers'
alignment for the viper kernel.

Sprite shape (fine detail the spec does not give; the mockup's): level
``0.9·e^-(d/1.8)^2 + e·(6.2 [d <= 1.4 + 1.4·I] + (2.6 + 2·I)·e^-(d/(4.5 + 3·I))^2)``
for blink level e, clipped at r 11.5; a speck is
``a·(2.6·e^-(d/1.8)^2 + 0.6·e^-(d/5)^2)``.
"""

import array
import math

from finder import tuning as T
from finder.compat import const
from ui.field import (EASE_IOC, EXPT, FILL_V, FL_A, GHOST_AMP, MAXR, Q8, _aligned, ease,
                      q8)
from ui.themes.base import (ALL, M_FOUND, M_LISTEN, M_LIVE, M_SCAN, M_STILL, S_MENU, Radial,
                            Theme)

try:
    import framebuf
except ImportError:  # CPython: maths and tables only
    framebuf = None

# ---- THEME_PARAMS (tokens.themes.fireflies) ---------------------------------
_P = T.THEME_PARAMS["fireflies"]
COUNT = tuple(_P["count"])               # flies per zone, FAR..HOT
SYNC = tuple(q8(s) for s in _P["sync"])  # blink phase lock per zone, Q8
RISE_MS = _P["rise_ms"]
FALL_MS = _P["fall_ms"]
HALO = q8(_P["halo"])                    # halo level = 0.9 * I, Q8
SHRINK = q8(_P["orbit_shrink"])          # orbit x (1 - 0.45 I)
N_SPECK = _P["listen_specks"]
LISTEN_MS = _P["listen_ms"]
N_STILL = _P["still_count"]
N_FOUND = _P["found_count"]
BREATHE_MS = _P["breathe_ms"]
MAXF = max(max(COUNT), N_STILL, N_FOUND, N_SPECK)

# ---- tokens shared with the field --------------------------------------------
MG_A = q8(T.FIELD_SCAN_SWEEP_GLOW_AMP[0])   # live-mirror halo 1 + 5 I (§5.7)
MG_B = q8(T.FIELD_SCAN_SWEEP_GLOW_AMP[1])
SAVER_PU = q8(T.SAVER_PULSE_SCALE)          # saver: blink amplitude x 0.7 (§8 pulse_amp)
XF_MS = T.ZONE_CROSSFADE_MS                 # level crossfade on a moment change (§5.3)
GLIDE_MS = T.HUE_CROSSFADE_FOUND_MS         # glide onto the FOUND ring with the gold crossfade
SUN_FLOOR = const(256)      # sun mode floor >= 1.0 (ui/renderer.py SUN_FLOOR, §8)
CORE_R = const(7)           # the core dot covers ring indices 0..6 (§4 rule 2)

# ---- fine detail (the mockup's numbers; the spec does not give them) ----------
HALO_R = const(70)          # halo e^-(r/70)^2
ORBIT_R0 = 46               # orbit radius (46 + 64 h) (1 - 0.45 I) + 18
ORBIT_RH = 64
ORBIT_MIN = const(18)
WOB = q8(0.2)               # radial wobble +-20 %
W1 = (0.10, 0.12)           # angular rate 0.10 + 0.12 h rad/s, either way
W2 = (0.08, 0.10)           # wobble rate 0.08 + 0.10 h rad/s
STILL_R = (60, 30)          # still radius 60 + 30 h, wobble +-10 %
STILL_WOB = q8(0.1)
STILL_K = q8(0.3)           # still drift: 0.3 x the live rates
STILL_E = (q8(0.45), q8(0.25))   # still glow 0.45 +- 0.25 at 1.3 rad/s, 1 rad apart
SCAN_E = q8(0.25)           # frozen flies' blink level
FOUND_R = const(84)         # found ring r 84 +- 10, 0.2 rad/s round, wobble 0.8 rad/s
FOUND_WOB = const(10)
FOUND_E = (q8(0.5), q8(0.25))    # breathing 0.5 +- 0.25, 0.3 rad apart
SPECK_R0 = const(125)       # specks start here (the dish edge)
FLY_CLEAR = const(12)       # fly sprite half-size (<= 8) + room: lens clearance
SPECK_CLEAR = const(6)      # speck sprite half-size (<= 2) + room
SQUEEZE = const(24)         # a path inside lens + clear + 24 is squeezed into this band
LENS_SLEW = const(30)       # px/s: the clearance follows a shrinking lens at most this fast
N_SANG = const(64)          # speck trip angles (a table, indexed by trip)

# ---- sprites -----------------------------------------------------------------
NE = const(10)              # blink levels
ELEV = (0.0, 0.04, 0.08, 0.13, 0.2, 0.28, 0.38, 0.52, 0.72, 1.0)
NI = const(4)               # intensity steps: I = (i + 0.5) / NI
NA = const(5)               # speck amplitude levels a = (k + 1) / NA
SPECK0 = NI * NE            # first speck sprite id
N_SPR = SPECK0 + NA
NONE = const(255)           # slot not drawn / empty sprite
NQ = const(32)              # sprite level steps (2/9 level each)
QK = const(5)               # q < QK (fly level < 1.0) is transparent
CLIP2 = const(132)          # sprites are clipped at r 11.5 (d^2 <= 132)
KEY = const(0x1F00)         # swapped pure blue: no theme ramp holds it
QD = const(9)               # q < QD (level < 2) is drawn on an ordered dither
CORE_Q = const(23)          # q >= CORE_Q (level >= 5) is also in the core sprite
BAYER = (0.125, 0.625, 0.375, 0.875)   # 2x2 thresholds, index (dx & 1) | (dy & 1) << 1
LQ = array.array("h", [q * 512 // 9 for q in range(NQ)])   # q -> Q8 level above B

# ---- clock -------------------------------------------------------------------
PH_BITS = const(20)         # phase: 2^20 per turn


def _gcd(a, b):
    while b:
        a, b = b, a % b
    return a


def _lcm(a, b):
    return a // _gcd(a, b) * b


# theme clock wrap: whole breaths, and whole cycles of the speck trip angles
# (a trip is LISTEN_MS, its angle is entry trip % N_SANG of a table)
CLK_WRAP = _lcm(BREATHE_MS, LISTEN_MS * N_SANG)


def _rate(w):
    """rad/s -> phase units per ms, Q4."""
    return int(w * (1 << PH_BITS) * 16 / (2 * math.pi * 1000) + 0.5)


RATE_STILL_E = _rate(1.3)
RATE_FOUND = _rate(0.2)
RATE_FOUND_WOB = _rate(0.8)
RAD_1024 = 163              # 1 rad in 1024ths of a turn
STEP_FOUND_E = 49           # 0.3 rad


def _hash(n):
    """32-bit integer hash -> [0, 1) (per-fly randomness)."""
    n &= 0xFFFFFFFF
    n = (n ^ 61) ^ (n >> 16)
    n = (n + (n << 3)) & 0xFFFFFFFF
    n ^= n >> 4
    n = (n * 0x27D4EB2D) & 0xFFFFFFFF
    n ^= n >> 15
    return n / 4294967296.0


def blink(s):
    """Blink level (0..1) ``s`` ms after its rise starts."""
    return s / RISE_MS if s < RISE_MS else math.exp(-(s - RISE_MS) / FALL_MS)


def fly_level(d, e, i):
    """Fly level at distance ``d`` px for blink level ``e`` and intensity ``i``."""
    v = 0.9 * math.exp(-(d / 1.8) ** 2)
    core = 1.4 + 1.4 * i
    v += e * ((6.2 if d <= core else 0.0) + (2.6 + 2.0 * i) * math.exp(-(d / (4.5 + 3.0 * i)) ** 2))
    return v


def speck_level(d, a):
    return a * (2.6 * math.exp(-(d / 1.8) ** 2) + 0.6 * math.exp(-(d / 5.0) ** 2))


def level_q(v):
    """Fly level -> sprite value q (2/9-level steps, 0..NQ-1)."""
    q = int(v * 4.5 + 0.5)
    return NQ - 1 if q > NQ - 1 else q


# distinct squared distances of pixel offsets inside the clip, ascending
D2 = sorted(set(dx * dx + dy * dy for dx in range(12) for dy in range(12)
                if dx * dx + dy * dy <= CLIP2))


def make_sprite(level):
    """[bytes, h, core bytes, core h]: GS8 sprites of q per pixel (0 =
    transparent, (2h + 1) square) for ``level(d)`` (decreasing in d). Levels
    1..2 (q QK..QD-1) are opaque with probability (level - 1) on a 2x2
    ordered dither, so a fly's faint rim neither ends in an edge nor blacks
    out a neighbour's glow. The core sprite holds only the pixels at level
    >= 5 (blitted again after every fly, so no rim covers a core). h is NONE
    (with one transparent pixel) when nothing shows."""
    qd = bytearray(CLIP2 + 1)
    dmax = -1
    cmax = -1
    for d2 in D2:                       # level falls with d: stop at the first miss
        q = level_q(level(math.sqrt(d2)))
        if q < QK:
            break
        qd[d2] = q
        dmax = d2
        if q >= CORE_Q:
            cmax = d2
    out = []
    for lim, lo in ((dmax, QK), (cmax, CORE_Q)):
        if lim < 0:
            out.append(bytearray(1))
            out.append(NONE)
            continue
        h = 0
        while (h + 1) * (h + 1) <= lim:
            h += 1
        n = 2 * h + 1
        b = bytearray(n * n)
        for dy in range(h + 1):         # one quadrant, mirrored (the dither is too)
            r0 = (h - dy) * n + h
            r1 = (h + dy) * n + h
            for dx in range(h + 1):
                d2 = dx * dx + dy * dy
                if d2 > lim:
                    continue
                v = qd[d2]
                if v < lo:
                    continue
                if v < QD and (v * 2 / 9.0 - 1.0) <= BAYER[(dx & 1) | ((dy & 1) << 1)]:
                    continue
                b[r0 + dx] = v
                b[r0 - dx] = v
                b[r1 + dx] = v
                b[r1 - dx] = v
        out.append(b)
        out.append(h)
    return out


# ---- the fly kernel ------------------------------------------------------------
# Slot state: one array 'i' of fields x MAXF (field f of slot j at f*MAXF + j).
F_PH1 = const(0)            # angle phase (live and still)
F_PH2 = const(1)            # wobble phase
F_W1 = const(2)             # rates, Q4 phase units per ms
F_W2 = const(3)
F_RB = const(4)             # live radius and wobble (per I)
F_RW = const(5)
F_RBS = const(6)            # still radius and wobble
F_RWS = const(7)
F_FP1 = const(8)            # found: phases, rates, radius, wobble
F_FP2 = const(9)
F_FW1 = const(10)
F_FW2 = const(11)
F_FRB = const(12)
F_FRW = const(13)
F_OFF = const(14)           # blink peak offset after the beat, ms
F_CX = const(15)            # this frame: centre, sprite (NONE: not drawn), top-left
F_CY = const(16)
F_CS = const(17)
F_TX = const(18)
F_TY = const(19)
F_LX = const(20)            # last drawn frame: centre, sprite, drawn
F_LY = const(21)
F_LS = const(22)
F_DW = const(23)
F_PA = const(24)            # this frame's path: angle (1024 per turn), radius px
F_PR = const(25)
F_GA = const(26)            # FOUND glide: start angle and radius, on (1) / off (0)
F_GR = const(27)
F_GO = const(28)
F_EO = const(29)            # sine level phase (1024 a turn): still j x 1 rad, found place x 0.3 rad
F_RK = const(30)            # place on the FOUND ring (0..N_FOUND-1, in angular order)
N_F = const(31)
# Tables: one array 'i': SN (Q14 sine, 1024 per turn) | EB (blink level Q8
# per 8 ms after the rise starts) | QE (Q8 level -> blink index) | SH (half
# size per sprite, NONE = empty).
NEB = const(326)
T_EB = const(1024)
T_QE = T_EB + NEB
T_SH = T_QE + 257
T_N = T_SH + N_SPR
# Parameters (array 'i'):
P_N = const(0)              # slots to compute
P_TOP = const(1)            # slots to settle (max of this and last frame)
P_DTK = const(2)            # phase step: dt x rate scale (Q8); >> 12 with the Q4 rates
P_LIM = const(3)            # lens + clearance, px
P_SQZ = const(4)            # (SQUEEZE << 16) // (lim + SQUEEZE)
P_LIM2 = const(5)           # lim + SQUEEZE
P_MODE = const(6)           # 0 beat blink, 1 sine level, 2 positions given (specks)
P_BEAT = const(7)           # ms since the ring spawn
P_PER = const(8)            # ring period, ms
P_RISE = const(9)
P_GAMP = const(10)          # blink amplitude, Q8 (ghost 0.6, saver x 0.7)
P_IB = const(11)            # sprite id of blink level 0 (intensity step x NE)
P_E0 = const(12)            # sine level e0 + e1 sin(base + offset_j), Q8 (offset: F_EO)
P_E1 = const(13)
P_EBS = const(14)
P_EFL = const(15)           # level floor (FOUND entry blink)
P_ALL = const(16)           # everything is dirty already: skip the boxes
P_PCH = const(17)           # fly palette changed: every drawn box is dirty
P_OP1 = const(18)           # geometry field offsets: phases, rates, radius, wobble
P_OP2 = const(19)
P_OW1 = const(20)
P_OW2 = const(21)
P_ORB = const(22)
P_ORW = const(23)
P_DIRTY = const(24)         # in/out: dirty strip bits (spans: the sp bytearray)
P_GE = const(25)            # FOUND glide progress, Q8 (256: no glide)
P_NN = const(26)

_FSRC = """
def fly_kernel(st, tb, pm, sp):
    s = ptr32(st)
    t = ptr32(tb)
    q = ptr32(pm)
    o = ptr8(sp)
    n = q[0]
    top = q[1]
    dtk = q[2]
    lim = q[3]
    sqz = q[4]
    lim2 = q[5]
    mode = q[6]
    beat = q[7]
    per = q[8]
    rise = q[9]
    gamp = q[10]
    ib = q[11]
    e0 = q[12]
    e1 = q[13]
    ebs = q[14]
    efl = q[15]
    allm = q[16]
    pch = q[17]
    op1 = q[18]
    op2 = q[19]
    ow1 = q[20]
    ow2 = q[21]
    orb = q[22]
    orw = q[23]
    dirty = q[24]
    ge = q[25]
    j = 0
    if mode != 2:
        while j < n:
            a = s[op1 + j]
            b = s[op2 + j]
            if dtk != 0:
                a = (a + ((s[ow1 + j] * dtk) >> 12)) & 0xFFFFF
                b = (b + ((s[ow2 + j] * dtk) >> 12)) & 0xFFFFF
                s[op1 + j] = a
                s[op2 + j] = b
            rho = s[orb + j] + ((s[orw + j] * t[b >> 10]) >> 14)
            if rho < lim2:
                rho = lim + ((rho * sqz) >> 16)
            a = a >> 10
            if ge < 256:
                if s[%(GO)d + j] != 0:
                    g = s[%(GR)d + j]
                    rho = g + (((rho - g) * ge) >> 8)
                    if rho < lim:
                        rho = lim
                    g = s[%(GA)d + j]
                    b = ((a - g + 512) & 1023) - 512
                    a = (g + ((b * ge) >> 8)) & 1023
            s[%(PA)d + j] = a
            s[%(PR)d + j] = rho
            s[%(CX)d + j] = 120 + ((rho * t[a]) >> 14)
            s[%(CY)d + j] = 120 - ((rho * t[(a + 256) & 1023]) >> 14)
            if mode == 0:
                u = beat - s[%(OFF)d + j] + rise
                while u < 0:
                    u += per
                while u >= per:
                    u -= per
                k = u >> 3
                e = 0
                if k < %(NEB)d:
                    e = t[%(EB)d + k]
                if u < rise:
                    k = (u + per) >> 3
                    if k < %(NEB)d:
                        if t[%(EB)d + k] > e:
                            e = t[%(EB)d + k]
                e = (e * gamp) >> 8
            else:
                e = e0 + ((e1 * t[(ebs + s[%(EO)d + j]) & 1023]) >> 14)
                if efl > e:
                    e = efl
            if e > 256:
                e = 256
            if e < 0:
                e = 0
            sid = ib + t[%(QE)d + e]
            if t[%(SH)d + sid] == 255:
                sid = 255
            s[%(CS)d + j] = sid
            j += 1
    j = 0
    while j < top:
        mk = 0
        x0 = 0
        y0 = 0
        x1 = 0
        y1 = 0
        sid = 255
        if j < n:
            sid = s[%(CS)d + j]
        if sid == 255:
            s[%(CS)d + j] = 255
            if s[%(DW)d + j] != 0:
                h = t[%(SH)d + s[%(LS)d + j]]
                x0 = s[%(LX)d + j] - h
                y0 = s[%(LY)d + j] - h
                x1 = s[%(LX)d + j] + h
                y1 = s[%(LY)d + j] + h
                mk = 1
                s[%(DW)d + j] = 0
        else:
            x = s[%(CX)d + j]
            y = s[%(CY)d + j]
            h = t[%(SH)d + sid]
            x0 = x - h
            y0 = y - h
            x1 = x + h
            y1 = y + h
            s[%(TX)d + j] = x0
            s[%(TY)d + j] = y0
            if s[%(DW)d + j] != 0:
                ox = s[%(LX)d + j]
                oy = s[%(LY)d + j]
                ol = s[%(LS)d + j]
                if ((ox - x) | (oy - y) | (ol - sid) | pch) != 0:
                    h = t[%(SH)d + ol]
                    if ox - h < x0:
                        x0 = ox - h
                    if oy - h < y0:
                        y0 = oy - h
                    if ox + h > x1:
                        x1 = ox + h
                    if oy + h > y1:
                        y1 = oy + h
                    mk = 1
            else:
                mk = 1
            s[%(LX)d + j] = x
            s[%(LY)d + j] = y
            s[%(LS)d + j] = sid
            s[%(DW)d + j] = 1
        if mk != 0:
            if allm == 0:
                if x0 < 0:
                    x0 = 0
                if y0 < 0:
                    y0 = 0
                if x1 > 239:
                    x1 = 239
                if y1 > 239:
                    y1 = 239
                if x0 <= x1:
                    if y0 <= y1:
                        k = (y0 * 2731) >> 16
                        k1 = (y1 * 2731) >> 16
                        while k <= k1:
                            dirty = dirty | (1 << k)
                            if x0 < o[2 * k]:
                                o[2 * k] = x0
                            if x1 > o[2 * k + 1]:
                                o[2 * k + 1] = x1
                            k += 1
        j += 1
    q[24] = dirty
""" % {"CX": F_CX * MAXF, "CY": F_CY * MAXF, "CS": F_CS * MAXF, "TX": F_TX * MAXF,
       "TY": F_TY * MAXF, "LX": F_LX * MAXF, "LY": F_LY * MAXF, "LS": F_LS * MAXF,
       "DW": F_DW * MAXF, "OFF": F_OFF * MAXF, "PA": F_PA * MAXF, "PR": F_PR * MAXF,
       "GA": F_GA * MAXF, "GR": F_GR * MAXF, "GO": F_GO * MAXF, "EO": F_EO * MAXF,
       "NEB": NEB, "EB": T_EB, "QE": T_QE, "SH": T_SH}


def _ident(x):
    return x


def kernel_agrees(ka, kb, tb):
    """True if kernels ``ka`` and ``kb`` leave identical slot state,
    parameters and spans on synthetic frames covering every mode and branch
    (negative phases and rates, empty sprites, boxes off the screen, ghost
    and flash levels, glides both ways round and inside the lens, settled,
    changed and dropped slots)."""
    st0 = array.array("i", [0] * (N_F * MAXF))
    for j in range(MAXF):
        st0[F_PH1 * MAXF + j] = (j * 70001) & 0xFFFFF
        st0[F_PH2 * MAXF + j] = (j * 30011) & 0xFFFFF
        st0[F_W1 * MAXF + j] = (j * 97) % 1200 - 600
        st0[F_W2 * MAXF + j] = 200 + j * 31
        st0[F_RB * MAXF + j] = 20 + j * 7
        st0[F_RW * MAXF + j] = j * 3 - 30
        st0[F_OFF * MAXF + j] = (j * 211) % 2600
        st0[F_CX * MAXF + j] = j * 13 - 20
        st0[F_CY * MAXF + j] = 250 - j * 12
        v = (j * 5) % N_SPR
        st0[F_CS * MAXF + j] = v if j % 4 and tb[T_SH + v] != NONE else NONE
        v = (j * 3) % N_SPR
        st0[F_LS * MAXF + j] = v if tb[T_SH + v] != NONE else N_SPR - 1
        st0[F_LX * MAXF + j] = j * 11
        st0[F_LY * MAXF + j] = 239 - j * 9
        st0[F_DW * MAXF + j] = j % 3
        st0[F_GA * MAXF + j] = (j * 389) & 1023
        st0[F_GR * MAXF + j] = 5 + j * 9
        st0[F_GO * MAXF + j] = 1 if j % 5 else 0
        st0[F_EO * MAXF + j] = (j * 163 + 900) % 1500 - 200
    geo = (F_PH1 * MAXF, F_PH2 * MAXF, F_W1 * MAXF, F_W2 * MAXF, F_RB * MAXF, F_RW * MAXF)
    cases = (
        # mode, dtk, lim, beat, per, gamp, ib, e0, e1, ebs, efl, allm, pch, n, top, ge
        (0, 25600, 19, 37, 500, 256, 30, 0, 0, 0, 0, 0, 1, 22, 22, 256),
        (0, 7700, 79, 2390, 2400, 154, 0, 0, 0, 0, 0, 0, 0, 9, 22, 100),
        (1, 25600, 150, 0, 1000, 256, 30, 128, 64, 1000, 250, 1, 0, 14, 20, 0),
        (1, 0, 31, 0, 1000, 179, 10, 128, 64, 77, 0, 0, 1, 22, 22, 201),
        (2, 0, 52, 0, 1000, 256, 0, 0, 0, 0, 0, 0, 0, 3, 22, 256),
    )
    for c in cases:
        lim = c[2]
        vals = (c[13], c[14], c[1], lim, (SQUEEZE << 16) // (lim + SQUEEZE), lim + SQUEEZE,
                c[0], c[3], c[4], RISE_MS, c[5], c[6], c[7], c[8], c[9], c[10], c[11],
                c[12]) + geo + (0x20, c[15])
        out = []
        for fn in (ka, kb):
            st = array.array("i", st0)
            pm = array.array("i", vals)
            sp = bytearray(b"\x64\x8c\xff\x00" * 5)
            fn(st, tb, pm, sp)
            out.append(bytes(st) + bytes(pm) + bytes(sp))
        if out[0] != out[1]:
            return False
    return True


# ---- the one-time load (module globals; ui/themes ``stages``) ------------------
_SPR = [None] * N_SPR       # sprites (GS8 framebufs; bytes on CPython), id i * NE + k
_COR = [None] * N_SPR       # their bright cores
_SH = bytearray(N_SPR)      # half-size per sprite (NONE = empty)
_CH = bytearray(N_SPR)
_nspr = 0                   # sprites built
_tb = None                  # the kernel's tables
_tbs = 0                    # table steps done
_PYK = None                 # the plain kernel
_VK = None                  # its viper build (None: none)
_vtried = False
_KERN = None                # the kernel to run (viper once it agrees)
_KIND = None


def _put(n, level):
    """Build sprite ``n`` (and its core) from ``level(d)``."""
    global _nspr
    b, h, b2, h2 = make_sprite(level)
    if framebuf is not None:
        k = 1 if h == NONE else 2 * h + 1
        b = framebuf.FrameBuffer(b, k, k, framebuf.GS8)
        k = 1 if h2 == NONE else 2 * h2 + 1
        b2 = framebuf.FrameBuffer(b2, k, k, framebuf.GS8)
    _SPR[n] = b
    _COR[n] = b2
    _SH[n] = h
    _CH[n] = h2
    _nspr = n + 1


def _sprites():
    """One step: the rest of the current intensity step's sprites (or the specks)."""
    n = _nspr
    if n < SPECK0:
        i = n // NE
        iv = (i + 0.5) / NI
        for k in range(n % NE, NE):
            _put(i * NE + k, lambda d, ev=ELEV[k]: fly_level(d, ev, iv))
    else:
        for k in range(n - SPECK0, NA):
            _put(SPECK0 + k, lambda d, av=(k + 1) / NA: speck_level(d, av))


def _table(tb, step):
    """Fill the kernel's tables: step 0 the sine, 1 the blink envelope,
    2 level -> blink index and the sprite half-sizes."""
    if step == 0:
        for k in range(257):                # quarter wave, mirrored
            v = int(round(16384 * math.sin(2 * math.pi * k / 1024)))
            tb[k] = v
            tb[512 - k] = v
            tb[(512 + k) & 1023] = -v
            tb[(1024 - k) & 1023] = -v
    elif step == 1:
        for k in range(NEB):
            tb[T_EB + k] = min(255, int(256 * blink(k * 8) + 0.5))
    else:
        best = 0                            # nearest blink level (ELEV ascending)
        for v in range(257):
            while best + 1 < NE and abs(ELEV[best + 1] * 256 - v) <= abs(ELEV[best] * 256 - v):
                best += 1
            tb[T_QE + v] = best
        for k in range(N_SPR):
            tb[T_SH + k] = _SH[k]


def load():
    """The one-time work, one bounded step per ``yield`` (base.py
    "Loading"): sprites per intensity step, the tables, the plain kernel
    (a compile), the viper build (a compile) and its self-check. Each step
    already done is skipped, also after a load abandoned half way."""
    global _tb, _tbs, _PYK, _VK, _vtried, _KERN, _KIND
    while _nspr < N_SPR:
        _sprites()
        yield
    if _tb is None:
        _tb = array.array("i", [0] * T_N)
    while _tbs < 3:
        _table(_tb, _tbs)
        _tbs += 1
        yield
    if _PYK is None:
        ns = {"ptr32": _ident, "ptr8": _ident}
        exec(_FSRC, ns)
        _PYK = ns["fly_kernel"]
        yield
    if not _vtried:
        try:
            vs = {}
            exec("@micropython.viper" + _FSRC, vs)
            _VK = vs["fly_kernel"]
        except Exception:  # noqa: BLE001 - no viper (CPython, wasm): plain Python
            _VK = None
        _vtried = True
        yield
    if _KERN is None:
        if _VK is None:
            _KIND = "python"
            _KERN = _PYK
        elif kernel_agrees(_PYK, _VK, _tb):
            _KIND = "viper"
            _KERN = _VK
        else:
            _KIND = "python (viper self-check failed)"
            _KERN = _PYK
        yield


def assets():
    """(sprites, half-sizes, core sprites, their half-sizes, tables, kernel,
    plain kernel, kind), loading whatever is not loaded yet."""
    for _ in load():
        pass
    return _SPR, _SH, _COR, _CH, _tb, _KERN, _PYK, _KIND


def _q8i(v):
    """A 0..1 float -> Q8 int, clamped, as the renderer converts intensity
    (only on a wake in the MENU, from the params of the screen under it)."""
    q = int(v * 256)
    return 0 if q < 0 else (256 if q > 256 else q)


class Fireflies(Theme):
    name = "fireflies"
    layer = True

    def __init__(self, r):
        Theme.__init__(self, r)
        self.rad = Radial(self.name)
        self.ramps = self.rad.ramps
        self.pv = array.array("i", [0] * len(self.rad.prm))
        # sprites: id i * NE + k (live), SPECK0 + k (specks)
        self.spr, self.sh, self.cor, self.ch, self.tb, self.kern, py, self.kind = assets()
        # fly palette (q -> colour) and its inputs
        self.fpa = array.array("H", [KEY] * NQ)
        self.fpal = (framebuf.FrameBuffer(self.fpa, NQ, 1, framebuf.RGB565)
                     if framebuf is not None else None)
        self.fk = array.array("i", [-1] * 5)    # B, dim, vmax, lift, grey
        self.st = array.array("i", [0] * (N_F * MAXF))      # slot state
        self.ph0 = array.array("i", [0] * (4 * MAXF))       # fresh phases (prepare)
        self.r0 = bytearray(MAXF)                           # orbit base radius per fly
        self.oh = bytearray(MAXF)                           # blink offset hash per fly
        self.sang = array.array("h", [0] * N_SANG)          # speck trip angles
        self.od = bytearray(MAXF)                           # slots in angular order
        self.pm = array.array("i", [0] * P_NN)
        self.pm[P_RISE] = RISE_MS
        self.reset()

    def prepare(self):
        """Per-fly constants (a hash per slot; the mockup's seeds), then the
        viper kernel's buffer alignment check."""
        st = self.st
        ph0 = self.ph0
        for j in range(MAXF):
            s = j * 11
            w = _rate(W1[0] + W1[1] * _hash(s + 2))
            st[F_W1 * MAXF + j] = -w if _hash(s + 9) < 0.5 else w
            st[F_W2 * MAXF + j] = _rate(W2[0] + W2[1] * _hash(s + 3))
            ph0[j] = int(_hash(s + 4) * (1 << PH_BITS))
            ph0[MAXF + j] = int(_hash(s + 6) * (1 << PH_BITS))
            ph0[2 * MAXF + j] = (j << PH_BITS) // N_FOUND
            ph0[3 * MAXF + j] = ((j * RAD_1024) << 10) & 0xFFFFF
            rs = STILL_R[0] + int(STILL_R[1] * _hash(j * 7 + 4))
            st[F_RBS * MAXF + j] = rs
            st[F_RWS * MAXF + j] = (rs * STILL_WOB) >> 8
            st[F_FW1 * MAXF + j] = RATE_FOUND
            st[F_FW2 * MAXF + j] = RATE_FOUND_WOB
            st[F_FRB * MAXF + j] = FOUND_R
            st[F_FRW * MAXF + j] = FOUND_WOB
            self.r0[j] = ORBIT_R0 + int(ORBIT_RH * _hash(j * 11 + 1))
            self.oh[j] = 0 if j == 0 else int(256 * _hash(j * 11 + 5))
        yield
        for k in range(N_SANG):
            self.sang[k] = int(1024 * _hash(k * 7 + 3))
        if self.kern is not _PYK and not (_aligned(st) and _aligned(self.pm) and
                                          _aligned(self.tb)):
            self.kern = _PYK
            self.kind = "python (unaligned)"
        self.reset()

    def reset(self):
        """Back to the state of a fresh Fireflies (renderer reset): phases,
        slots, clocks, latches; the next frame is a wake."""
        Theme.reset(self)
        st = self.st
        ph0 = self.ph0
        for j in range(MAXF):
            st[F_PH1 * MAXF + j] = ph0[j]
            st[F_PH2 * MAXF + j] = ph0[MAXF + j]
            st[F_FP1 * MAXF + j] = ph0[2 * MAXF + j]
            st[F_FP2 * MAXF + j] = ph0[3 * MAXF + j]
        for i in range(F_CX * MAXF, N_F * MAXF):
            st[i] = 0
        for j in range(MAXF):
            st[F_CS * MAXF + j] = NONE
            st[F_RK * MAXF + j] = j
        self.ramps.snap()
        for i in range(5):
            self.fk[i] = -1
        self.rb_iq = -1             # intensity the live radii were made for
        self.off_z = -1             # zone and period the blink offsets were made for
        self.off_p = -1
        self.nn = 0                 # slots in use (this frame, after build)
        self.m = -1
        self.z = 0
        self.iqv = 0
        self.ib = 0
        self.n_live = COUNT[0]
        self.clk = 0
        self.m_t0 = 0
        self.flash = False
        self.fl_t0 = 0
        self._burst_t = None
        self.sa = 0                 # still glow phase
        self.fl = FL_A              # shown floor, halo level, halo radius (Q8)
        self.gl = 0
        self.gr = HALO_R * Q8
        self.xf_on = False          # level crossfade: running, age, start levels
        self.xf_ms = 0
        self.xf0 = FL_A
        self.xf1 = 0
        self.xf2 = HALO_R * Q8
        self.edge = CORE_R          # lens edge the flies keep clear of (slewed), px
        self.ls = 0                 # field.last_spawn as last seen
        self.gh = 0                 # that beat is a ghost
        self.sv = 256               # saver blink scale, Q8 (slewed)
        self.gliding = False        # FOUND glide running, its age
        self.gms = 0

    # ---- per-frame helpers -----------------------------------------------------
    def _radii(self, iq):
        """Live orbit radius and wobble per slot for intensity ``iq``."""
        rs = 256 - ((SHRINK * iq) >> 8)
        r0 = self.r0
        st = self.st
        for j in range(MAXF):
            v = ((r0[j] * rs) >> 8) + ORBIT_MIN
            st[F_RB * MAXF + j] = v
            st[F_RW * MAXF + j] = (v * WOB) >> 8
        self.rb_iq = iq

    def _offsets(self, z, per):
        """Blink peak offset per slot: (1 - sync) * h * P (fly 0: 0)."""
        k = 256 - SYNC[z]
        oh = self.oh
        st = self.st
        for j in range(MAXF):
            st[F_OFF * MAXF + j] = (k * oh[j] * per) >> 16
        self.off_z = z
        self.off_p = per

    def _glide_in(self, prev):
        """Entering FOUND (not on a wake): the flies take the ring's places in
        their angular order, the ring turned to where they are (the mean
        offset of the flies that showed), so each glides only to a place
        near it; each fly drawn last frame glides there from its path
        (angle, radius; kernel ``ge``). Specks and flies that showed nothing
        start on the ring. The breathing and wobble phases follow the place,
        so neighbours on the ring stay 0.3 rad (1 rad) apart."""
        st = self.st
        od = self.od
        on = 0 if prev == M_LISTEN else 1
        n = N_FOUND
        ga = F_GA * MAXF
        for j in range(n):
            go = on if st[F_DW * MAXF + j] else 0
            st[F_GO * MAXF + j] = go
            st[ga + j] = st[F_PA * MAXF + j] if go else (st[F_FP1 * MAXF + j] >> 10)
            st[F_GR * MAXF + j] = st[F_PR * MAXF + j]
        for j in range(n):                      # insertion sort by start angle
            a = st[ga + j]
            i = j
            while i > 0 and st[ga + od[i - 1]] > a:
                od[i] = od[i - 1]
                i -= 1
            od[i] = j
        ref = -1
        tot = 0
        c = 0
        for i in range(n):                      # the ring's turn, 1024 per turn
            j = od[i]
            if st[F_GO * MAXF + j]:
                o = st[ga + j] - (i << 10) // n
                if ref < 0:
                    ref = o & 1023
                tot += ((o - ref + 512) & 1023) - 512
                c += 1
        base = (ref + tot // c) & 1023 if c else 0
        for i in range(n):
            j = od[i]
            st[F_RK * MAXF + j] = i
            st[F_FP1 * MAXF + j] = ((base << 10) + ((i << PH_BITS) // n)) & 0xFFFFF
            st[F_FP2 * MAXF + j] = ((i * RAD_1024) << 10) & 0xFFFFF
        self.gliding = True
        self.gms = 0

    def _beat(self, lp):
        """Whether the field's last ring spawn was a ghost, looked up once
        per spawn (the ring's own flag) and held until the next."""
        f = self.f
        ls = f.last_spawn
        self.ls = ls
        g = 0 if lp.ring_live else 1
        for k in range(MAXR):
            if f.r_on[k] and f.r_t0[k] == ls:
                g = f.r_ghost[k]
                break
        self.gh = g

    def _fly_palette(self, b, vmax, dim, lift, grey):
        """Rebuild the sprite palette if an input changed; True if it did."""
        fk = self.fk
        if fk[0] == b and fk[1] == dim and fk[2] == vmax and fk[3] == lift and fk[4] == grey:
            return False
        fk[0] = b
        fk[1] = dim
        fk[2] = vmax
        fk[3] = lift
        fk[4] = grey
        c = self.ramps.grey.c if grey else self.ramps.mix.c
        pa = self.fpa
        lq = LQ
        for q in range(QK, NQ):
            v = b + lq[q]
            if v > vmax:
                v = vmax
            if dim != 256:
                v = (v * dim) >> 8
            j = ((v * 9 + 128) >> 8) + lift
            col = c[63 if j > 63 else j]
            pa[q] = col if col != KEY else KEY ^ 0x0100
        return True

    @staticmethod
    def _istep(iq):
        i = (iq * NI) >> 8
        return (NI - 1 if i > NI - 1 else i) * NE

    # ---- the interface ---------------------------------------------------------
    def build(self, p, t, core, rim, vmax, lift):
        dt = self.clock(p, t)
        f = self.f
        r = self.r
        wake = self.wake
        menu = r._scr == S_MENU
        lp = self.live(p)                     # in the MENU: the screen under it
        xr = False                            # the level crossfade restarts this frame
        if wake or not menu:                  # MENU: hold the moment as it was
            m = self.moment(p)
            z = self.zone(lp)
            iq = self.iq() if lp is p else _q8i(lp.intensity)
            if m != self.m or wake:
                if m == M_SCAN and (wake or self.m != M_LIVE):
                    # frozen flies: the live ones before, else the zone's
                    self.n_live = COUNT[z]
                    self._radii(iq)
                    self.ib = self._istep(iq)
                self.gliding = False
                if wake:
                    self.xf_on = False
                else:
                    self.xf_on = True         # crossfade from what was shown
                    self.xf_ms = 0
                    self.xf0 = self.fl
                    self.xf1 = self.gl
                    self.xf2 = self.gr
                    xr = True
                    if m == M_FOUND:
                        self._glide_in(self.m)
                st = self.st
                if m == M_STILL:                # level phases: 1 rad apart by slot
                    for j in range(MAXF):
                        st[F_EO * MAXF + j] = (j * RAD_1024) & 1023
                elif m == M_FOUND:              # 0.3 rad apart by place on the ring
                    for j in range(MAXF):
                        st[F_EO * MAXF + j] = (st[F_RK * MAXF + j] * STEP_FOUND_E) & 1023
                self.m_t0 = self.clk
                self.flash = False
            if p.burst and p.t_ms != self._burst_t:
                self._burst_t = p.t_ms
                if m == M_FOUND and not wake:
                    self.flash = True
                    self.fl_t0 = self.clk
            self.m = m
            self.z = z
            self.iqv = iq
        else:
            m = self.m
            z = self.z
            iq = self.iqv
        # the beat: ghost or live, once per ring spawn (the field shifts
        # last_spawn while the MENU holds it)
        if menu and not wake:
            self.ls = f.last_spawn
        elif wake or f.last_spawn != self.ls:
            self._beat(lp)
        ghost = self.gh if m == M_LIVE else 0
        clk = (self.clk + dt) % CLK_WRAP
        self.clk = clk
        # ---- base palette ------------------------------------------------------
        rm = self.ramps
        was = rm.ramp
        lut_ch = rm.dur > 0
        rm.follow(f, t)
        if rm.ramp != was:
            lut_ch = True
        iris = f.iris
        if wake or not menu:                  # MENU: the levels hold, as the field's
            if m == M_SCAN:
                glt = MG_A + ((MG_B * iq) >> 8)
                grt = r._gq if lp is p else int(lp.glow_r_px * 256)
                if grt < 2 * Q8:
                    grt = 2 * Q8
            else:
                glt = (HALO * iq) >> 8
                k = (iris * 64) // HALO_R
                if k:
                    glt = (glt * EXPT[k if k < 256 else 255]) >> 8
                grt = HALO_R * Q8
            flt = FL_A
            if p.sun and flt < SUN_FLOOR:
                flt = SUN_FLOOR
            if not wake:
                if self.xf_on:
                    if not xr:
                        self.xf_ms += dt
                    e = ease(EASE_IOC, self.xf_ms, XF_MS)
                    if e >= 256:
                        self.xf_on = False
                    else:
                        flt = self.xf0 + (((flt - self.xf0) * e) >> 8)
                        glt = self.xf1 + (((glt - self.xf1) * e) >> 8)
                        grt = self.xf2 + (((grt - self.xf2) * e) >> 8)
                k = f.flash                   # the floor: the field's flash limit
                if flt > self.fl + k:
                    flt = self.fl + k
                elif flt < self.fl - k:
                    flt = self.fl - k
            self.fl = flt
            self.gl = (glt + 4) & ~7          # 1/32 level steps: a steady palette
            self.gr = grt
        rad = self.rad
        rad.setup(f, t, core, rim, vmax, lift, self.fl, self.gl, self.gr, 0, 0)
        q = rad.prm
        q[16] = 0                              # standing wave off: its phase is unused
        pv = self.pv
        ch = wake or lut_ch
        for i in range(len(pv)):
            if q[i] != pv[i]:
                pv[i] = q[i]
                ch = True
        if ch:
            rad.run()
            self.everything()
        else:
            self.clear_dirty()
        # ---- fly palette -------------------------------------------------------
        b = self.fl
        if m != M_SCAN:                          # floor + the halo at r ~70 px
            b += (self.gl * EXPT[64]) >> 8
        b = ((b + 14) * 9 // 256) * 256 // 9     # 1/9 level steps
        if lut_ch or wake:
            self.fk[0] = -1                      # the ramp mix moved: rebuild
        pal_ch = self._fly_palette(b, vmax, f.dim, lift, 1 if (ghost or m == M_LISTEN) else 0)
        # ---- the lens the flies keep clear of ------------------------------------
        lens = iris + T.IRIS_RIM_PX if iris > 0 else 0
        if lens < CORE_R:
            lens = CORE_R
        if f.fill_v == FILL_V and f.fill_r + 3 > lens:   # the calibrate fill, while on
            lens = f.fill_r + 3
        e = self.edge
        if wake or lens >= e:
            e = lens                             # growing: at once (pushes the flies out)
        elif dt:
            k = (dt * LENS_SLEW + 500) // 1000   # shrinking: at most LENS_SLEW px/s
            e -= k if k > 0 else 1
            if e < lens:
                e = lens
        self.edge = e
        lim = e + FLY_CLEAR
        # ---- saver: blink amplitude x 0.7, eased (no flash) ------------------------
        k = SAVER_PU if r._saver else 256
        if wake:
            self.sv = k
        elif dt:
            d = k - self.sv
            s = (Q8 * dt) // XF_MS + 1
            self.sv = k if -s <= d <= s else self.sv + (s if d > 0 else -s)
        # ---- flies ---------------------------------------------------------------
        pm = self.pm
        st = self.st
        pm[P_GE] = 256
        if m == M_LISTEN:
            n = self._specks(clk, e + SPECK_CLEAR)
            pm[P_MODE] = 2
        else:
            pm[P_MODE] = 1
            pm[P_EFL] = 0
            if m == M_LIVE:
                n = COUNT[z]
                self.n_live = n
                if iq != self.rb_iq:
                    self._radii(iq)
                self.ib = self._istep(iq)
                per = f.period or lp.pulse_period_ms or 1000
                if z != self.off_z or per != self.off_p:
                    self._offsets(z, per)
                pm[P_MODE] = 0
                pm[P_DTK] = dt << 8
                pm[P_BEAT] = self.beat_age(t) % per      # the kernel subtracts at most a period
                pm[P_PER] = per
                pm[P_GAMP] = ((GHOST_AMP if ghost else 256) * self.sv) >> 8
                self._geom(F_PH1, F_PH2, F_W1, F_W2, F_RB, F_RW)
            elif m == M_STILL:
                n = N_STILL
                self.ib = self._istep(iq)
                self.sa = (self.sa + ((RATE_STILL_E * dt) >> 4)) & 0xFFFFF
                pm[P_DTK] = dt * STILL_K
                pm[P_E0] = STILL_E[0]
                pm[P_E1] = STILL_E[1]
                pm[P_EBS] = self.sa >> 10
                self._geom(F_PH1, F_PH2, F_W1, F_W2, F_RBS, F_RWS)
            elif m == M_SCAN:
                n = self.n_live
                pm[P_DTK] = 0
                pm[P_E0] = SCAN_E
                pm[P_E1] = 0
                self._geom(F_PH1, F_PH2, F_W1, F_W2, F_RB, F_RW)
            else:                               # FOUND
                n = N_FOUND
                self.ib = self._istep(iq)
                age = (clk - self.m_t0) % CLK_WRAP
                fe = 0
                if self.flash:
                    k = ((clk - self.fl_t0) % CLK_WRAP) >> 3
                    if k < NEB:
                        fe = self.tb[T_EB + k]
                    else:
                        self.flash = False
                if self.gliding:
                    if not xr:
                        self.gms += dt
                    k = ease(EASE_IOC, self.gms, GLIDE_MS)
                    if k >= 256:
                        self.gliding = False
                    pm[P_GE] = k
                pm[P_DTK] = dt << 8
                pm[P_E0] = FOUND_E[0]
                pm[P_E1] = FOUND_E[1]
                pm[P_EBS] = ((age % BREATHE_MS) << 10) // BREATHE_MS
                pm[P_EFL] = fe
                self._geom(F_FP1, F_FP2, F_FW1, F_FW2, F_FRB, F_FRW)
            pm[P_IB] = self.ib
        pm[P_N] = n
        pm[P_TOP] = n if n > self.nn else self.nn
        pm[P_LIM] = lim
        pm[P_LIM2] = lim + SQUEEZE
        pm[P_SQZ] = (SQUEEZE << 16) // (lim + SQUEEZE)
        pm[P_ALL] = 1 if self.dirty == ALL else 0
        pm[P_PCH] = 1 if pal_ch else 0
        pm[P_DIRTY] = self.dirty
        self.kern(st, self.tb, pm, self.spans)
        self.dirty = pm[P_DIRTY]
        self.nn = n

    def _geom(self, p1, p2, w1, w2, rb, rw):
        pm = self.pm
        pm[P_OP1] = p1 * MAXF
        pm[P_OP2] = p2 * MAXF
        pm[P_OW1] = w1 * MAXF
        pm[P_OW2] = w2 * MAXF
        pm[P_ORB] = rb * MAXF
        pm[P_ORW] = rw * MAXF

    def _specks(self, clk, lim):
        """Three specks drifting from the edge to the lens over LISTEN_MS:
        their centres and sprites into the slot state (kernel mode 2)."""
        st = self.st
        tb = self.tb
        span = SPECK_R0 - lim
        if span < 0:
            span = 0
        for j in range(N_SPECK):
            c = clk + (j * LISTEN_MS) // N_SPECK
            cyc = c // LISTEN_MS
            u = ((c - cyc * LISTEN_MS) << 8) // LISTEN_MS     # progress, Q8
            a = u * 10                                        # fade in over 10 %
            k = ((256 - u) * 27) >> 2                         # out over the last 15 %
            if k < a:
                a = k
            if a > 256:
                a = 256
            k = (a * NA + 128) >> 8
            s = SPECK0 + k - 1 if k > 0 else NONE
            if s != NONE and self.sh[s] == NONE:
                s = NONE
            st[F_CS * MAXF + j] = s
            if s == NONE:
                continue
            rho = lim + ((span * (256 - u)) >> 8)
            ang = self.sang[(j * 17 + cyc * 101) & (N_SANG - 1)]
            st[F_CX * MAXF + j] = 120 + ((rho * tb[ang]) >> 14)
            st[F_CY * MAXF + j] = 120 - ((rho * tb[(ang + 256) & 1023]) >> 14)
        return N_SPECK

    def blit(self, y0, buf, fb):
        rad = self.rad
        self.r.map.blit(y0, rad.pal, rad.pal_arr, buf, fb)

    def draw(self, fb):
        pal = self.fpal
        st = self.st
        nn = self.nn
        spr = self.spr
        for j in range(nn):
            s = st[F_CS * MAXF + j]
            if s != NONE:
                fb.blit(spr[s], st[F_TX * MAXF + j], st[F_TY * MAXF + j], KEY, pal)
        cor = self.cor                  # bright cores again, over every rim
        ch = self.ch
        for j in range(nn):
            s = st[F_CS * MAXF + j]
            if s != NONE:
                h = ch[s]
                if h != NONE:
                    fb.blit(cor[s], st[F_CX * MAXF + j] - h, st[F_CY * MAXF + j] - h, KEY, pal)
