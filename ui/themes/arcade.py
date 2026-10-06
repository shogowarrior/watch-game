"""Arcade (ui-spec §4A): diamonds of 8 px blocks that step outward 10 times a second.

The screen is a 30x30 grid of 8 px cells (THEME_PARAMS.arcade cell_px). A
cell's ring number ``d`` is its diamond (Manhattan) cell distance from the
centre: with the centre-symmetric cell coordinate ``cx = 14 - c`` (c < 15)
or ``c - 15`` (c >= 15), and ``cy`` the same for rows, ``d = cx + cy``
(0..28; the four centre cells are d 0). The field is a GS8 index map,
symmetric in x and y about (119.5, 119.5), blitted by the base ``QuadMap``
(the field's viper quadrant blit on the watch, the framebuf palette blit
elsewhere). Index = one entry per distinct (d, vignette) pair (79 entries,
``NE``; at most one per quadrant cell, ``E_MAX``), each with the vignette
(tokens.field vignette_stops, the field's ``VIG``) at its cells' centre
radius; then the lens (``LENS_E``) and its rim (``RIM_E``). The checker is
d's parity (in the centre-symmetric grid that is the checkerboard within
each quadrant), so it needs no index bit.

Its cells change only on a 100 ms tick (tick_ms), which divides every zone
period. The tick clock advances by ``dt`` (0 in MENU, so MENU freezes it)
and is re-phased on every ring spawn, so a front steps exactly tick_ms after
its beat. On a tick the level of each ring number is recomputed (29 values,
``_levels``); the colour kernel recolours the palette entries of the ring
numbers whose level changed and returns their range, which maps to strips
and column spans in closed form (``_span``). Between ticks only what moves
per frame in every theme changes: the lens (open and close drawn into the
map, the rim colour one palette entry), the hue crossfade, the menu dim and
the calibrate fill's fade-out (a frame of those three recolours the palette
whole); a burst shows its front on its own frame (the field's burst ring is
off the tick grid); and twinkles and confetti go dark on the frame the lens
target grows over them.

What each moment draws (levels in ramp steps; ``d0`` = ceil(iris / 8), the
ring number at the lens edge on the axes, ``de`` = ceil((iris + rim) x
sqrt 2 / 8), the first one wholly outside the lens):

- Base: floor ``FLOOR`` (0.6, from the approved mockup; at least 1.0 in sun
  mode, the field's sun floor), checker cells below ``CHK_BELOW`` get
  ``CHK_ADD``, all times the vignette. The core dot (§4 rule 2) is the 16 px
  centre block (d 0) at the renderer's ``core`` level, never grey, while the
  iris is closed (also under a ghost front). The PAIRING calibrate fill (§6)
  is in cells too: ring numbers up to ``dfill`` get the fill level (4) and
  the outermost 1.5x (6, the edge) as a floor after the vignette (as the
  field's kernel does), ``dfill`` running from the lens edge (d0) at the
  fill's start radius to the corners (d 28) at its end, so the diamond fills
  the screen in the disc's time.
- *Live:* on each ring spawn a front three cells deep (front_levels 7 / 4.6
  / 2.6 x front_scale 0.62 + 0.38 I) leaves the lens and moves out one cell
  a tick: it starts at the lens edge on the axes (d0), and its diagonal
  sides come out from behind the lens over the next ticks. A ghost beat
  (``ring_live`` false) is grey at the same levels. The ring numbers from
  d0 out through the first 1 + 3 I wholly outside the lens (core_cells:
  d0 .. de + round(3 I)) glow at ``GLOW_A + GLOW_B I`` (2.4 + 2 I, mockup),
  a diamond around the round lens. A burst ring (``p.burst``, read from the
  field's burst slot) is a front two cells deep (``B0`` / ``B1``, 7 / 5,
  mockup) moving two cells a tick.
- *Listening:* on each (inward) ring spawn a grey diamond (``L0..L2``, 4 /
  2.5 / 1.4 with the trail outward, mockup) enters at the corners and moves
  inward one cell every 2 ticks (listen_ticks_per_cell) until it reaches
  the lens; grey on every listening screen, PAIRING looking included (§4A:
  grey diamonds).
- *Still:* ``NTW`` twinkle slots, slot j at phase (tick + j) % ``TW_LIFE``:
  lit for phases 0-2 (``TW_LO`` 2, ``TW_HI`` 3.5, 2, mockup) on a random
  cell drawn for that cycle outside the lens and the fill (a fixed random
  table: deterministic), dark for the other 5, so at most 9 are lit at a
  time. They are drawn as 8x8 ``fill_rect`` blocks over the frame
  (``layer``). Like the confetti they are not vignetted: outside a 92 px
  lens only the dim outer ring is left, where vignetted twinkles would not
  show.
- *Scan:* no new fronts; the ring numbers around the lens (d0 .. de) carry
  the live mirror at ``MIRROR_A + MIRROR_B I`` (2 + 4 I, mockup).
- *Found:* the two ring numbers just outside the check disc (CHECK_DISC_R)
  swap between ``FR_HI`` 5 and ``FR_LO`` 3.5 every 4 ticks (marquee lights),
  the burst front runs out, and ``NCF`` gold confetti blocks fall one cell a
  tick (80 px/s), stepping sideways every 4 ticks (``CF_LV`` 7 / 5 / 6,
  mockup), drawn as 8x8 blocks over the frame (under the check, which the
  renderer draws on top) and never over the lens.

Fronts already travelling keep going when the moment changes, as the
field's rings do. The lens (iris disc in THEME_IRIS, rim as §2: ramp level
clamp(floor + glow_amp, 5, 7) from the field's crossfaded levels, or the
renderer's flat / warn rim, the flat rim as this theme's level 6) is drawn
into the index map itself with framebuf GS8 discs (the base ``_disc`` shape),
only on frames where the iris radius changes; cells it leaves are put back
with one ``fill_rect`` each. A settled lens costs nothing per frame. With
the viper blit the map is the quadrant only (``QuadMap(keep=False)``), and
the lens is drawn into the quadrant.

Loading (base.py "Loading"): the import builds only constants; ``load()``
builds the cell and random tables, compiles the plain colour kernel, then
the viper one, and runs its self-check, one step each; ``prepare()`` builds
the full map from the cells, then the ``QuadMap`` (quadrant and blit
self-check; the full map is dropped when the viper blit runs). ``reset()``
clears the tick, the fronts, the twinkle and confetti slots and the hue (a
fresh theme's state); the map and the lens drawn into it stay.

Cost per frame: the quadrant blit (as Ripple) and, in Still / Found, at most
``NTW`` / ``NCF`` ``fill_rect`` calls; on a tick the 29-value level pass,
the colour kernel (viper on the watch, after a self-check against the plain
version; plain Python elsewhere), a 10-strip span pass and the 24 twinkle
slots (Still). Nothing allocates per frame.
"""

import array
import math

from finder import tuning as T
from finder.compat import const, ticks_diff
from ui import PROX
from ui.field import MAXR, RIM_MIN, V7, VIG
from ui.themes.base import (BH, M_FOUND, M_LISTEN, M_LIVE, M_SCAN, M_STILL, NS, S_MENU, W,
                            QuadMap, Ramps, Theme, _disc, framebuf)

_P = T.THEME_PARAMS["arcade"]


def _q8(x):
    return int(x * 256 + 0.5)


CELL = _P["cell_px"]                    # 8 px cells
TICK = _P["tick_ms"]                    # 100 ms tick
LISTEN_TPC = _P["listen_ticks_per_cell"]
NC = W // CELL                          # 30 cells a side
HC = NC // 2                            # 15 cells from the centre to an edge
ND = 2 * HC - 1                         # ring numbers 0..28
D_IN = ND                               # listening diamonds enter here (just past the corner)
F0, F1, F2 = [_q8(v) for v in _P["front_levels"]]
FS_A = _q8(_P["front_scale"][0])
FS_B = _q8(_P["front_scale"][1])
CORE_A = _P["core_cells"][0]
CORE_B = _P["core_cells"][1]

# Fine detail the spec leaves to the theme (values from the approved mockup)
FLOOR = _q8(0.6)
SUN_FLOOR = const(256)                  # sun mode: floor >= 1.0 (as the field, §8)
CHK_ADD = _q8(0.35)                     # checker cells below 1.5 get +0.35
CHK_BELOW = _q8(1.5)
GLOW_A = _q8(2.4)                       # lens-edge glow 2.4 + 2 I
GLOW_B = _q8(2.0)
B0 = _q8(7.0)                           # burst front 7 / 5, two cells a tick
B1 = _q8(5.0)
L0 = _q8(4.0)                           # listening diamond 4 / 2.5 / 1.4
L1 = _q8(2.5)
L2 = _q8(1.4)
MIRROR_A = _q8(2.0)                     # scan halo 2 + 4 I (the live mirror)
MIRROR_B = _q8(4.0)
FR_HI = _q8(5.0)                        # FOUND marquee rings 5 / 3.5, swap every 4 ticks
FR_LO = _q8(3.5)
TW_LO = _q8(2.0)                        # a twinkle's 3 lit ticks: 2, 3.5, 2
TW_HI = _q8(3.5)
TW_LIFE = const(8)                      # ticks per twinkle cycle (3 lit, 5 dark)
NTW = const(24)                         # twinkle slots (9 cells lit at a time)
CF_LV = (_q8(7.0), _q8(5.0), _q8(6.0))  # confetti levels, by piece % 3
NCF = const(18)                         # confetti blocks
CF_CYCLE = const(36)                    # a block falls 36 cells, then wraps
MAXF = const(12)                        # front slots
RIM_PX = T.IRIS_RIM_PX
CHECK_R = T.CHECK_DISC_R
FILL_R0 = T.IRIS_R["scan"]              # the calibrate fill's start radius (ui/renderer.py)
FILL_R1 = T.FIELD_R_MAX                 # and its end radius
BURST_AMP = _q8(T.BURST_AMP)            # the field's burst ring slot carries this amplitude
L6 = const(1536)                        # level 6: the flat rim (prox.6) in this theme
PROX6 = PROX[6]
IRISC = T.THEME_IRIS["arcade"]

# front kinds
K_LIVE = const(1)
K_GHOST = const(2)
K_BURST = const(3)
K_IN = const(4)

# palette: cell entries 0..NE-1 (NE <= E_MAX, one per quadrant cell at most),
# then the lens and its rim at fixed indices
E_MAX = HC * HC
LENS_E = const(254)                     # palette entry of the iris disc
RIM_E = const(255)                      # and of its rim
CQ = bytes([HC - 1 - c if c < HC else c - HC for c in range(NC)])   # cell column/row -> cx/cy
# smallest cy in each 24-row dirty strip (3 cell rows: cy .. cy + 2)
CYMIN = bytes([min(CQ[3 * k], CQ[3 * k + 1], CQ[3 * k + 2]) for k in range(NS)])

# colour kernel table (array 'H'): EV @0 (NE of E_MAX) | DOFF @T_DOFF (ND + 1) |
# mixed LUT @T_MIX (64) | grey LUT @T_GREY (64)
T_DOFF = E_MAX
T_MIX = T_DOFF + ND + 1
T_GREY = T_MIX + 64
T_N = T_GREY + 64

# ---- tables (load(): set once, in a step of their own) ------------------------
QE = None           # quadrant cell -> palette entry
_EV = None          # entry -> vignette (Q8)
_DOFF = None        # first entry of each ring number (ND + 1)
NEAR = None         # nearest / farthest pixel radius of each quadrant cell
FAR = None
NE = 0              # cell entries
RND = None          # 256 fixed random bytes
CF_COL = None       # confetti column and start offset per block
CF_OFF = None


def _cell_tables():
    """One entry per (d, vignette) pair, sorted by d: quadrant cell ->
    entry, entry vignette (Q8), first entry of each d, and the nearest and
    farthest pixel radius of each quadrant cell."""
    vig = []
    for cy in range(HC):
        for cx in range(HC):
            i = int(CELL * math.sqrt((cx + 0.5) ** 2 + (cy + 0.5) ** 2))
            vig.append(VIG[i if i < len(VIG) else len(VIG) - 1])
    qe = bytearray(HC * HC)
    ev = []
    doff = [0] * (ND + 1)
    for d in range(ND):
        doff[d] = len(ev)
        vals = []
        for cy in range(HC):
            cx = d - cy
            if 0 <= cx < HC and vig[cy * HC + cx] not in vals:
                vals.append(vig[cy * HC + cx])
        vals.sort()
        vals.reverse()
        base = len(ev)
        ev.extend(vals)
        for cy in range(HC):
            cx = d - cy
            if 0 <= cx < HC:
                qe[cy * HC + cx] = base + vals.index(vig[cy * HC + cx])
    doff[ND] = len(ev)
    near = bytearray(HC * HC)
    far = bytearray(HC * HC)
    for cy in range(HC):
        for cx in range(HC):
            near[cy * HC + cx] = int(CELL * math.sqrt(cx * cx + cy * cy))
            far[cy * HC + cx] = int(CELL * math.sqrt((cx + 1) ** 2 + (cy + 1) ** 2) + 0.999)
    return qe, ev, doff, near, far


def _rnd_table():
    """256 bytes from a fixed LCG: deterministic twinkles and confetti."""
    out = bytearray(256)
    s = 12345
    for i in range(256):
        s = (s * 1103515245 + 12345) & 0x7FFFFFFF
        out[i] = (s >> 16) & 0xFF
    return out


def build_map():
    """Full 240x240 GS8 map of cell entries: each cell row of the
    centre-symmetric grid built once, then copied to its 8 screen rows above
    the centre and their mirror below."""
    full = bytearray(W * W)
    row = bytearray(W)
    for cy in range(HC):
        for cx in range(HC):
            e = QE[cy * HC + cx]
            a = 120 + CELL * cx
            b = 112 - CELL * cx
            for k in range(CELL):
                row[a + k] = e
                row[b + k] = e
        y0 = (112 - CELL * cy) * W      # the cell row above the centre ...
        y1 = (120 + CELL * cy) * W      # ... and its mirror below
        for k in range(CELL):
            full[y0 + k * W:y0 + (k + 1) * W] = row
            full[y1 + k * W:y1 + (k + 1) * W] = row
    return full


# ---- colour kernel ------------------------------------------------------------
# For each ring number whose level (lv[d], flags gy[d]) differs from the one
# last coloured (lv[ND + d], gy[ND + d]), or every one when prm[3] (full):
# store it as coloured, add the checker, then for each of its entries: level x
# vignette, the calibrate fill floor prm[4] (flag 2; x 1.5 with flag 4, the
# edge) after the vignette as in the field's kernel, the level cap prm[0], the
# menu dim prm[1], the LUT index with the sun lift prm[2], the grey (flag 1)
# or mixed LUT. Returns (first << 8) | last changed ring number, 0xFF00 when
# none. Literals are the module's layout numbers
# (substituted below). Compiled with @micropython.viper where the port has it
# (the watch), else plain Python through identity pointer shims (as
# ui/field.py pal_kernel).
_KSRC = """
def col_kernel(pal, lv, gy, tab, prm) -> int:
    pp = ptr16(pal)
    lp = ptr16(lv)
    gp = ptr8(gy)
    tp = ptr16(tab)
    qp = ptr32(prm)
    vmax = qp[0]
    dim = qp[1]
    lift = qp[2]
    full = qp[3]
    fill = qp[4]
    edge = fill + (fill >> 1)
    lo = 255
    hi = 0
    d = 0
    while d < ND_:
        a = lp[d]
        g = gp[d]
        if full or a != lp[ND_ + d] or g != gp[ND_ + d]:
            lp[ND_ + d] = a
            gp[ND_ + d] = g
            if lo == 255:
                lo = d
            hi = d
            if d & 1:
                if a < CHK_BELOW_:
                    a += CHK_ADD_
            base = T_MIX_
            if g & 1:
                base = T_GREY_
            fv = 0
            if g & 2:
                fv = fill
                if g & 4:
                    fv = edge
            e = tp[T_DOFF_ + d]
            e1 = tp[T_DOFF_ + 1 + d]
            while e < e1:
                v = (a * tp[e]) >> 8
                if v < fv:
                    v = fv
                if v > vmax:
                    v = vmax
                if dim != 256:
                    v = (v * dim) >> 8
                j = ((v * 9 + 128) >> 8) + lift
                if j > 63:
                    j = 63
                pp[e] = tp[base + j]
                e += 1
        d += 1
    return (lo << 8) | hi
"""
for _k, _v in (("ND_", ND), ("CHK_BELOW_", CHK_BELOW), ("CHK_ADD_", CHK_ADD),
               ("T_MIX_", T_MIX), ("T_GREY_", T_GREY), ("T_DOFF_", T_DOFF)):
    _KSRC = _KSRC.replace(_k, str(_v))

col_kernel = None       # load(): the kernel the theme runs
col_kernel_py = None    # load(): the plain one
KERNEL = None           # load(): "viper", "python" or "python (viper self-check failed)"
_vk = None              # load(): the viper kernel before its self-check (False: none)


def _ident(x):
    return x


# self-check cases: (vmax, dim, lift, full, fill); the last one with levels
# past the cap at small d (the LUT clamp under the sun lift) and every flag
# combination (grey, fill, fill edge)
_CASES = ((1792, 256, 0, 0, 1024), (1280, 128, 9, 0, 0), (1792, 200, 3, 1, 700),
          (1792, 256, 9, 1, 1024))


def kernel_agrees(ka, kb):
    """True if colour kernels ``ka`` and ``kb`` give the same palette, state
    and result on synthetic frames (changed and unchanged ring numbers, grey,
    the fill and its edge, the checker, dim, lift, the cap, the LUT clamp,
    full)."""
    tab = array.array("H", [(i * 4099 + 0x1235) & 0xFFFF for i in range(T_N)])
    for i in range(NE):
        tab[i] = _EV[i]
    for i in range(ND + 1):
        tab[T_DOFF + i] = _DOFF[i]
    for c in range(len(_CASES)):
        out = []
        for fn in (ka, kb):
            if c < 3:
                lv = array.array("H", [(d * 97) % 1900 for d in range(2 * ND)])
                gy = bytearray([(d * 7 >> 2) % 7 for d in range(2 * ND)])
            else:
                lv = array.array("H", [(d * 211) % 2100 for d in range(2 * ND)])
                gy = bytearray([d % 8 for d in range(2 * ND)])
            for d in range(0, ND, 3):            # some unchanged
                lv[ND + d] = lv[d]
                gy[ND + d] = gy[d]
            pal = array.array("H", [0] * 256)
            r = fn(pal, lv, gy, tab, array.array("i", _CASES[c]))
            out.append(bytes(pal) + bytes(lv) + bytes(gy) + bytes([r >> 8, r & 255]))
        if out[0] != out[1]:
            return False
    return True


def load():
    """One-time work in steps (base.py "Loading"; a generator): the tables,
    the plain colour kernel, the viper one, its self-check. Idempotent: a
    step already done is skipped, also after a load abandoned half-way."""
    global QE, _EV, _DOFF, NEAR, FAR, NE, RND, CF_COL, CF_OFF
    global col_kernel, col_kernel_py, KERNEL, _vk
    if QE is None:
        qe, ev, doff, near, far = _cell_tables()
        rnd = _rnd_table()
        CF_COL = bytes([(rnd[(5 * j + 1) & 255] * NC) >> 8 for j in range(NCF)])
        CF_OFF = bytes([(rnd[(5 * j + 2) & 255] * 40) >> 8 for j in range(NCF)])
        RND = rnd
        _EV = ev
        _DOFF = doff
        NEAR = near
        FAR = far
        NE = len(ev)
        QE = qe                             # last: the step is done
        yield
    if col_kernel_py is None:
        ns = {"ptr8": _ident, "ptr16": _ident, "ptr32": _ident}
        exec(_KSRC, ns)
        col_kernel_py = ns["col_kernel"]
        yield
    if KERNEL is None:
        if _vk is None:
            try:
                vs = {}
                exec("@micropython.viper" + _KSRC, vs)
                _vk = vs["col_kernel"]
            except Exception:  # noqa: BLE001 - no viper (CPython, wasm): plain Python
                _vk = False
            yield
        if _vk is False:
            col_kernel = col_kernel_py
            KERNEL = "python"
        elif kernel_agrees(col_kernel_py, _vk):
            col_kernel = _vk
            KERNEL = "viper"
        else:
            col_kernel = col_kernel_py
            KERNEL = "python (viper self-check failed)"
        _vk = None
        yield


class Arcade(Theme):
    name = "arcade"
    layer = False           # set on tick and full frames: twinkles / confetti to draw

    def __init__(self, r):
        Theme.__init__(self, r)
        tab = array.array("H", [0] * T_N)
        for i in range(NE):
            tab[i] = _EV[i]
        for i in range(ND + 1):
            tab[T_DOFF + i] = _DOFF[i]
        self.tab = tab
        self.ramps = Ramps("arcade", memoryview(tab)[T_MIX:T_MIX + 64])
        g = self.ramps.grey.c
        for j in range(64):
            tab[T_GREY + j] = g[j]
        self.prm = array.array("i", [V7, 256, 0, 1, 0])   # vmax, dim, lift, full, fill
        self.pal_arr = array.array("H", [0] * 256)
        self.pal_arr[LENS_E] = IRISC
        self.pal = (framebuf.FrameBuffer(self.pal_arr, 256, 1, framebuf.RGB565)
                    if framebuf is not None else None)
        self.map = None                              # prepare(): the QuadMap
        self.mfb = None                              # GS8 framebuffer the lens is drawn into
        self.quad = False                            # True: that is the quadrant (viper blit)
        self.lr = 0                                  # lens radius drawn into the map
        self.lv = array.array("H", [0] * (2 * ND))   # level per ring number | as coloured
        self.gy = bytearray(2 * ND)                  # flags | as coloured: 1 grey, 2 fill, 4 edge
        self.fa = [0] * MAXF                         # front ages, ms
        self.fk = bytearray(MAXF)                    # front kinds (0 = free)
        self.tw_e = array.array("i", [-1] * NTW)     # twinkle: epoch its cell is for
        self.tw_x = bytearray(NTW)                   # cell column / row
        self.tw_y = bytearray(NTW)
        self.tw_q = bytearray(NTW)                   # quadrant cell (255: none yet)
        self.tw_l = bytearray(NTW)                   # lit: 0 dark, 1 low, 2 high
        self.tw_c = array.array("H", [0] * 2)       # colours: low, high
        self.cf_x = bytearray(NCF)
        self.cf_y = bytearray(NCF)                   # 255 = not shown
        self.cf_c = array.array("H", [0] * 3)
        self._clear()

    def _clear(self):
        """The motion state of a fresh theme (``__init__``, ``reset``); the
        map and the lens drawn into it stay."""
        fk = self.fk
        fa = self.fa
        for k in range(MAXF):
            fk[k] = 0
            fa[k] = 0
        for j in range(NTW):
            self.tw_e[j] = -1
            self.tw_x[j] = 0
            self.tw_y[j] = 0
            self.tw_q[j] = 255
            self.tw_l[j] = 0
        self.tw_on = 0                               # twinkles lit
        for j in range(NCF):
            self.cf_x[j] = 0
            self.cf_y[j] = 255
        self.cf_on = 0                               # confetti blocks shown
        self.tk = 0                                  # tick counter
        self.sub = 0                                 # ms into the tick
        self.ls = 0                                  # field.last_spawn as last seen
        self.bt = None                               # renderer._burst_t as last seen
        self.mom = M_LIVE
        self.dfill = -1                              # calibrate fill ring number (-1 off)
        self.lim = -1                                # radius the blocks keep clear of (-1: no lens)
        self.rimc = -1                               # rim colour in the palette
        self.layer = False

    def reset(self):
        Theme.reset(self)
        self._clear()
        self.ramps.snap()

    def prepare(self):
        """The index map in steps (base.py "Loading"): the full map from
        the cells, then the ``QuadMap`` (its quadrant and the viper blit's
        self-check), which drops the full map when the kernel runs."""
        if framebuf is None:
            return
        full = build_map()
        yield
        m = QuadMap(full, BH, self.r.bands, keep=False)
        full = None
        self.map = m
        if m.kern is not None:                       # the blit reads the quadrant
            self.quad = True
            self.mfb = framebuf.FrameBuffer(m.q, 120, 120, framebuf.GS8)
        else:
            self.mfb = m.map_fb

    # ---- fronts -------------------------------------------------------------
    def _add(self, kind, age):
        fk = self.fk
        fa = self.fa
        k = 0
        old = 0
        best = -1
        while k < MAXF:
            if not fk[k]:
                break
            if fa[k] > best:
                best = fa[k]
                old = k
            k += 1
        if k == MAXF:
            k = old
        fk[k] = kind
        fa[k] = age

    def _resync(self, p, t, mom, d0):
        """Wake / first frame: the fronts already in flight (as the field
        places its rings, no intro) and the tick phase."""
        f = self.f
        fk = self.fk
        for k in range(MAXF):
            fk[k] = 0
        a = self.beat_age(t)
        per = f.period
        if per > 0 and (mom == M_LIVE or mom == M_LISTEN):
            live = self.live(p).ring_live           # in the MENU: the screen under it
            kind = K_IN if mom == M_LISTEN else (K_LIVE if live else K_GHOST)
            age = a
            n = 0
            while n < MAXF and age < 30000:
                s = age // TICK
                if kind == K_IN:
                    if D_IN - s // LISTEN_TPC < d0:
                        break
                elif d0 + s - 2 >= ND:
                    break
                self._add(kind, age)
                age += per
                n += 1
        self.ls = f.last_spawn
        self.bt = self.r._burst_t
        self._burst(t)
        self.sub = a % TICK

    def _burst(self, t):
        """Add the field's newest burst ring (if still in flight) as a front."""
        f = self.f
        best = -1
        for k in range(MAXR):
            if f.r_on[k] and f.r_amp[k] == BURST_AMP and f.r_v[k] > 0:
                a = ticks_diff(t, f.r_t0[k])
                if 0 <= a < 2000 and (best < 0 or a < best):
                    best = a
        if best >= 0:
            self._add(K_BURST, best)

    # ---- per tick -------------------------------------------------------------
    def _levels(self, p, mom, d0, core, iris):
        """Level and grey flag of every ring number for this tick."""
        lv = self.lv
        gy = self.gy
        fl = SUN_FLOOR if (p.sun and FLOOR < SUN_FLOOR) else FLOOR
        for d in range(ND):
            lv[d] = fl
            gy[d] = 0
        iq = self.iq()
        de = ((iris + RIM_PX) * 181 + 1023) >> 10 if iris > 0 else 0   # ceil(r sqrt2 / 8)
        if mom == M_LIVE:
            v = GLOW_A + ((GLOW_B * iq) >> 8)
            d1 = de + CORE_A + ((CORE_B * iq + 128) >> 8)
            for d in range(d0, d1 if d1 < ND else ND):
                if lv[d] < v:
                    lv[d] = v
        elif mom == M_SCAN:
            v = MIRROR_A + ((MIRROR_B * iq) >> 8)
            for d in range(d0, de + 1 if de < ND else ND):
                if lv[d] < v:
                    lv[d] = v
        elif mom == M_FOUND:
            d = (CHECK_R + CELL - 1) // CELL
            if d0 > d:
                d = d0
            if d + 2 < ND:
                hi = (self.tk >> 2) & 1
                lv[d + 1] = FR_HI if hi else FR_LO
                lv[d + 2] = FR_LO if hi else FR_HI
        # fronts (in every moment: rings in flight keep going)
        sc = FS_A + ((FS_B * iq) >> 8)
        a0 = (F0 * sc) >> 8
        a1 = (F1 * sc) >> 8
        a2 = (F2 * sc) >> 8
        fk = self.fk
        fa = self.fa
        for k in range(MAXF):
            kind = fk[k]
            if not kind:
                continue
            s = fa[k] // TICK
            if kind == K_IN:
                h = D_IN - s // LISTEN_TPC
                if h < d0:
                    fk[k] = 0
                    continue
                if h < ND and lv[h] < L0:
                    lv[h] = L0
                    gy[h] = 1
                h += 1
                if h < ND and lv[h] < L1:
                    lv[h] = L1
                    gy[h] = 1
                h += 1
                if h < ND and lv[h] < L2:
                    lv[h] = L2
                    gy[h] = 1
            elif kind == K_BURST:
                h = d0 + 2 * s
                if h > ND:
                    fk[k] = 0
                    continue
                if h < ND and lv[h] < B0:
                    lv[h] = B0
                    gy[h] = 0
                h -= 1
                if 0 <= h < ND and lv[h] < B1:
                    lv[h] = B1
                    gy[h] = 0
            else:
                h = d0 + s
                if h > ND + 1:
                    fk[k] = 0
                    continue
                g = 1 if kind == K_GHOST else 0
                if h < ND and lv[h] < a0:
                    lv[h] = a0
                    gy[h] = g
                h -= 1
                if 0 <= h < ND and lv[h] < a1:
                    lv[h] = a1
                    gy[h] = g
                h -= 1
                if 0 <= h < ND and lv[h] < a2:
                    lv[h] = a2
                    gy[h] = g
        # calibrate fill (§6): the fill level as a floor after the vignette
        # (the kernel, prm[4]), its outermost ring number at 1.5x (the edge)
        f = self.f
        df = -1
        if f.fill_v:
            df = d0 + ((f.fill_r - FILL_R0) * (ND - 1 - d0)) // (FILL_R1 - FILL_R0)
            df = ND - 1 if df > ND - 1 else (d0 if df < d0 else df)
            for d in range(df):
                gy[d] |= 2
            gy[df] |= 6
        self.dfill = df
        # the core dot: at least the core level and never grey (a ghost
        # front's head on d 0 can be brighter than the core, but is grey)
        if core and iris == 0 and (lv[0] < core or gy[0] & 1):
            if lv[0] < core:
                lv[0] = core
            gy[0] = 0

    def _span(self, lo, hi):
        """Mark every strip and column span that ring numbers lo..hi touch."""
        sp = self.spans
        dt = self.dirty
        for k in range(NS):
            cm = CYMIN[k]
            if hi < cm or lo > cm + HC + 1:
                continue
            cx = hi - cm
            if cx > HC - 1:
                cx = HC - 1
            x0 = 112 - CELL * cx
            dt |= 1 << k
            if x0 < sp[2 * k]:
                sp[2 * k] = x0
            if 239 - x0 > sp[2 * k + 1]:
                sp[2 * k + 1] = 239 - x0
        self.dirty = dt

    def _cell(self, x, y):
        """Mark screen cell (column x, row y) dirty (a cell sits in one strip)."""
        k = y // 3
        sp = self.spans
        self.dirty |= 1 << k
        x *= CELL
        if x < sp[2 * k]:
            sp[2 * k] = x
        if x + CELL - 1 > sp[2 * k + 1]:
            sp[2 * k + 1] = x + CELL - 1

    def _twinkles(self, on, lim):
        """Twinkle slots for this tick (``on``: the Still moment). Slot j is
        at phase (tick + j) % TW_LIFE: lit low, high, low on phases 0-2 on a
        cell drawn for its cycle (dark while the lens, ``lim``, or the fill
        covers it), dark on 3-7. Every slot is visited, so a frame that
        advances several ticks (a slow frame lock, a missed slot) leaves no
        twinkle lit past its 3 ticks; ``tw_on`` counts the lit ones."""
        if not on and not self.tw_on:
            return
        tl = self.tw_l
        tx = self.tw_x
        ty = self.tw_y
        n = 0
        if on:
            tk = self.tk
            df = self.dfill
            te = self.tw_e
            tq = self.tw_q
            rm = self.ramps
            prm = self.prm
            self.tw_c[0] = rm.color(TW_LO, prm[0], prm[1], prm[2])
            self.tw_c[1] = rm.color(TW_HI, prm[0], prm[1], prm[2])
        for j in range(NTW):
            old = tl[j]
            lit = 0
            if on:
                ph = (tk + j) % TW_LIFE
                if ph < 3:
                    e = (tk + j) // TW_LIFE
                    if te[j] != e:                     # a new cell for this cycle
                        te[j] = e
                        h = (RND[(j * 7 + e) & 255] << 8) | RND[(e * 13 + j * 29 + 101) & 255]
                        for _ in range(2):             # a second draw if it is hidden
                            c = h % (NC * NC)
                            q = CQ[c // NC] * HC + CQ[c % NC]
                            if NEAR[q] > lim and q // HC + q % HC > df:
                                break
                            h = (h * 5 + 0x3B) & 0xFFFF
                        if old:                        # the last cycle's cell goes dark
                            self._cell(tx[j], ty[j])
                            old = 0
                            tl[j] = 0
                        tx[j] = c % NC
                        ty[j] = c // NC
                        tq[j] = q
                    q = tq[j]
                    if NEAR[q] > lim and q // HC + q % HC > df:
                        lit = 2 if ph == 1 else 1
                        n += 1
            if lit != old:
                self._cell(tx[j], ty[j])
                tl[j] = lit
        self.tw_on = n

    def _confetti(self, on, recolour, lim):
        """Confetti blocks for this tick (``on``: FOUND), hidden where they
        would cover the lens (``lim``); marks the old and the new cell of
        every block that moved."""
        if recolour or (on and not self.cf_on):
            rm = self.ramps
            for i in range(3):
                self.cf_c[i] = rm.color(CF_LV[i], self.prm[0], self.prm[1], self.prm[2])
        if not on and not self.cf_on:
            return
        tk = self.tk
        cx = self.cf_x
        cy = self.cf_y
        n = 0
        for j in range(NCF):
            ox = cx[j]
            oy = cy[j]
            x = ox
            y = 255
            if on:
                y = (tk + CF_OFF[j]) % CF_CYCLE - 3
                if 0 <= y < NC:
                    x = (CF_COL[j] + (((tk + j) >> 2) & 1)) % NC
                    if NEAR[CQ[y] * HC + CQ[x]] > lim:
                        n += 1
                    else:
                        y = 255
                else:
                    y = 255
            if y == oy and x == ox:
                continue
            if oy != 255:
                self._cell(ox, oy)
            if y != 255:
                self._cell(x, y)
            cy[j] = y
            cx[j] = x
        self.cf_on = n

    # ---- lens -----------------------------------------------------------------
    def _lens(self, rn):
        """Draw the lens of radius ``rn`` (0 = none) into the index map: put
        back the cells the old lens covered that the new one does not, then
        the rim and iris discs (the base ``_disc`` shape)."""
        ro = self.lr
        fb = self.mfb
        quad = self.quad
        if rn < ro:
            out = ro + RIM_PX + 2                  # cells with a pixel inside the old rim
            inn = rn + RIM_PX - 2 if rn > 0 else 0  # cells wholly inside the new one
            m = (out + CELL - 1) // CELL
            if m > HC:
                m = HC
            for cy in range(m):
                for cx in range(m):
                    q = cy * HC + cx
                    if NEAR[q] >= out or FAR[q] <= inn:
                        continue
                    e = QE[q]
                    x = CELL * cx
                    y = 112 - CELL * cy
                    if quad:
                        fb.fill_rect(x, y, CELL, CELL, e)
                    else:
                        fb.fill_rect(120 + x, y, CELL, CELL, e)
                        fb.fill_rect(112 - x, y, CELL, CELL, e)
                        fb.fill_rect(120 + x, 232 - y, CELL, CELL, e)
                        fb.fill_rect(112 - x, 232 - y, CELL, CELL, e)
        if rn > 0:
            if quad:                               # the kernel mirrors Q1 to the others
                fb.ellipse(0, 119, rn + RIM_PX - 1, rn + RIM_PX - 1, RIM_E, True, 1)
                fb.ellipse(0, 119, rn - 1, rn - 1, LENS_E, True, 1)
            else:
                _disc(fb, rn + RIM_PX, RIM_E)
                _disc(fb, rn, LENS_E)
        self.lr = rn

    # ---- the interface ------------------------------------------------------
    def build(self, p, t, core, rim, vmax, lift):
        dt = self.clock(p, t)
        f = self.f
        rm = self.ramps
        r0 = rm.ramp
        x0 = rm.dur
        rm.follow(f, t)
        prm = self.prm
        dim = f.dim
        full = (self.wake or r0 != rm.ramp or x0 > 0 or rm.dur > 0 or
                dim != prm[1] or vmax != prm[0] or lift != prm[2] or f.fill_v != prm[4])
        prm[0] = vmax
        prm[1] = dim
        prm[2] = lift
        prm[4] = f.fill_v
        self.clear_dirty()
        mom = self.moment(p)
        iris = f.iris_to
        d0 = (iris + CELL - 1) // CELL
        tick = False
        if self.wake:
            self._resync(p, t, mom, d0)
            tick = True
        else:
            if dt:
                fk = self.fk
                fa = self.fa
                for k in range(MAXF):
                    if fk[k]:
                        fa[k] += dt
                s = self.sub + dt
                if s >= TICK:                       # one or more ticks (dt <= DT_MAX)
                    n = s // TICK
                    self.tk = (self.tk + n) & 0xFFFFF
                    s -= n * TICK
                    tick = True
                self.sub = s
            if self.r._scr == S_MENU:
                self.ls = f.last_spawn              # the field shifts it while it holds
            elif f.last_spawn != self.ls:
                self.ls = f.last_spawn
                a = self.beat_age(t)
                if mom == M_LIVE:
                    self._add(K_LIVE if p.ring_live else K_GHOST, a)
                elif mom == M_LISTEN:
                    self._add(K_IN, a)
                self.sub = a % TICK
                tick = True
            if self.r._burst_t != self.bt:
                self.bt = self.r._burst_t
                self._burst(t)
                tick = True
        if tick:
            self.mom = mom
            self._levels(p, mom, d0, core, iris)
        if tick or full:
            prm[3] = 1 if full else 0
            ch = col_kernel(self.pal_arr, self.lv, self.gy, self.tab, prm)
            if full:
                self.everything()
            elif ch >> 8 != 255:
                self._span(ch >> 8, ch & 0xFF)
        # lens: the rim colour (palette) and the radius (index map)
        if rim < 0:
            q = f.fl + f.gl
            q = V7 if q > V7 else (RIM_MIN if q < RIM_MIN else q)
            rim = rm.color(q, vmax, dim, lift)
        elif rim == PROX6:
            rim = rm.color(L6, vmax, dim, lift)
        ir = f.iris
        lr = self.lr
        if rim != self.rimc or ir != lr:
            if ir != lr or lr:                       # a closed lens shows no rim
                a = lr if lr > ir else ir
                a += RIM_PX + 2
                self.mark(120 - a, 120 - a, 119 + a, 119 + a)
            self.pal_arr[RIM_E] = rim
            self.rimc = rim
            if ir != lr and self.mfb is not None:
                self._lens(ir)
        # twinkles and confetti: on a tick, a full frame, or when the lens
        # (or its target) grows past what they were last kept clear of
        lim = (ir if ir > iris else iris) + RIM_PX + 1 if (ir or iris) else -1
        if tick or full or lim > self.lim:
            self.lim = lim
            self._twinkles(self.mom == M_STILL, lim)
            self._confetti(self.mom == M_FOUND, full, lim)
            self.layer = self.tw_on > 0 or self.cf_on > 0
        if self.wake:
            self.everything()

    def blit(self, y0, buf, fb):
        self.map.blit(y0, self.pal, self.pal_arr, buf, fb)

    def draw(self, fb):
        if self.tw_on:
            tl = self.tw_l
            tc = self.tw_c
            for j in range(NTW):
                if tl[j]:
                    fb.fill_rect(self.tw_x[j] * CELL, self.tw_y[j] * CELL, CELL, CELL,
                                 tc[tl[j] - 1])
        if self.cf_on:
            cy = self.cf_y
            cc = self.cf_c
            for j in range(NCF):
                if cy[j] != 255:
                    fb.fill_rect(self.cf_x[j] * CELL, cy[j] * CELL, CELL, CELL, cc[j % 3])
