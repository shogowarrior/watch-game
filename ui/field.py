"""RippleField: ring-index map, ramp LUTs and the per-frame palette (ui-spec §4).

The background is a GS8 ring-index map (idx = floor(hypot(x-119.5,
y-119.5)), at most 168 at the corners) blitted through a 256x1 RGB565
palette, so every radial effect (glow, rings, iris, vignette, crossfades) is
a rebuild of palette entries 0..169. The hot paths here are integer-only
(Q8 levels: 256 = one ramp step, Q8 radii) so a frame allocates nothing on
MicroPython, where floats are heap objects. Floats are used only at import
and a few per frame.

Pure Python except the optional FrameBuffers, so the maths also runs on
CPython (framebuf is MicroPython-only).
"""

import array
import math

from finder import tuning as T
from finder.compat import const, ticks_add, ticks_diff
from ui import BG_IRIS, swap16

try:
    import framebuf
except ImportError:  # CPython: palette maths only
    framebuf = None

W = const(240)
N_IDX = const(170)          # ring indices 0..169 (the map's max is 168, the corner)
LUT_N = const(64)
MAXR = const(12)            # ring slots
Q8 = const(256)
V7 = const(1792)            # level 7 in Q8


# ---- tables (import time) ---------------------------------------------------
def q8(x):
    """Token level or scale (float) -> Q8 int, for import-time constants."""
    return int(x * Q8 + 0.5)


# Level tokens in Q8 (finder/tuning.py). Plain globals: const() takes literals only.
FL_A = q8(T.FLOOR_A)                     # floor = A + B*I (§4 rule 1)
FL_B = q8(T.FLOOR_B)
GL_A = q8(T.GLOW_AMP_A)                  # glow_amp = A + B*I
GL_B = q8(T.GLOW_AMP_B)
PU_A = q8(T.PULSE_AMP_A)                 # pulse_amp = A + B*I
PU_B = q8(T.PULSE_AMP_B)
GHOST_AMP = q8(T.GHOST_AMP_SCALE)        # ghost ring: 0.6 x pulse_amp (§4 rule 5)
AA_K = q8(T.TEMPORAL_AA_K)               # lead_eff = max(lead, K*|v|/fps) (§4 rule 3)
RIM_MIN = q8(T.IRIS_RIM_MIN_LEVEL)       # iris rim level >= 5 (§2)
FLASH_Q8 = T.FLASH_LIMIT_STEPS * Q8      # <= 2 steps per FLASH_LIMIT_MS (§4, §11)
SB_A = q8(T.STANDING_BREATHE[0])         # standing wave breathes 0.6 + 0.4 sin
SB_B = q8(T.STANDING_BREATHE[1])
# what FOUND shows: the standing wave's mean level, 2.0 + 2.5*0.5*0.6
STAND_MEAN = q8(T.STANDING_BASE + T.STANDING_AMP * 0.5 * T.STANDING_BREATHE[0])
FILL_V = const(1024)        # PAIRING calibrate fill: level 4, its edge 1.5x = level 6 (§6)


class Lut:
    """64-entry ramp LUT: ``c`` = swapped RGB565, ``r/g/b`` = 8-bit channels
    (bit-replicated from the 565 value), for the hue crossfade mix."""

    def __init__(self, lut=None, c=None):
        self.c = array.array("H", [0] * LUT_N) if c is None else c
        self.r = bytearray(LUT_N)
        self.g = bytearray(LUT_N)
        self.b = bytearray(LUT_N)
        if lut is not None:
            for j in range(LUT_N):
                v = lut[j]
                self.c[j] = v
                n = swap16(v)
                r5 = n >> 11
                g6 = (n >> 5) & 63
                b5 = n & 31
                self.r[j] = (r5 << 3) | (r5 >> 2)
                self.g[j] = (g6 << 2) | (g6 >> 4)
                self.b[j] = (b5 << 3) | (b5 >> 2)

    def copy_from(self, o):
        for j in range(LUT_N):
            self.r[j] = o.r[j]
            self.g[j] = o.g[j]
            self.b[j] = o.b[j]
            self.c[j] = o.c[j]


LUTS = {name: Lut(T.RAMP_LUT[name]) for name in T.RAMP_NAMES}

# Vignette (tokens.field.vignette_stops), Q8 per ring index.
VIG_STOPS = T.VIGNETTE_STOPS


def _vig(i):
    for k in range(len(VIG_STOPS) - 1):
        i0, v0 = VIG_STOPS[k]
        i1, v1 = VIG_STOPS[k + 1]
        if i <= i1:
            return v0 + (v1 - v0) * (i - i0) / (i1 - i0)
    return VIG_STOPS[-1][1]


VIG = array.array("h", [int(_vig(i) * 256 + 0.5) for i in range(N_IDX)])
# e^-(n/64)^2, Q8
EXPT = array.array("h", [int(256 * math.exp(-(n / 64.0) ** 2) + 0.5) for n in range(256)])


def _ss(x):
    return x * x * (3 - 2 * x)


# 1 - smoothstep(k/64), Q8, k 0..64
PROF = array.array("h", [int(256 * (1 - _ss(k / 64.0)) + 0.5) for k in range(65)])
# 0.5 + 0.5 cos(2 pi i / 32), Q8 (FOUND standing wave)
COS32 = array.array("h", [int(256 * (0.5 + 0.5 * math.cos(2 * math.pi * i / 32)) + 0.5)
                          for i in range(32)])
# sin/cos per integer degree, Q14
SIN = array.array("h", [int(round(16384 * math.sin(math.radians(d)))) for d in range(360)])
COS = array.array("h", [int(round(16384 * math.cos(math.radians(d)))) for d in range(360)])
# easing tables, Q8 at t = k/64
EASE_IOC = array.array("h", [int(256 * (4 * t ** 3 if t < 0.5 else 1 - (-2 * t + 2) ** 3 / 2) + 0.5)
                             for t in [k / 64.0 for k in range(65)]])
EASE_OC = array.array("h", [int(256 * (1 - (1 - k / 64.0) ** 3) + 0.5) for k in range(65)])
EASE_IC = array.array("h", [int(256 * (k / 64.0) ** 3 + 0.5) for k in range(65)])


def ease(tab, age, dur):
    """Q8 eased progress of ``age`` ms into ``dur`` ms."""
    if age >= dur:
        return 256
    if age <= 0:
        return 0
    return tab[(age << 6) // dur]


# ---- palette kernel -----------------------------------------------------------
# One fused pass over ring indices 0..169: base (floor + halo glow, blended
# toward the FOUND standing wave by its weight) + ring accumulators, vignette,
# core dot / calibrate fill / iris and rim, level cap, menu dim, LUT lookup,
# ghost channel. It also clears the accumulators for the next frame. The same
# source is compiled with @micropython.viper where the port supports it (the
# ESP32 build) and as plain Python otherwise (CPython, the wasm port), via
# identity pointer shims. Literals in it are tokens: the standing wave 2.0 +
# 2.5*cos32 (512, 640, wavelength 32) and the core dot r 6 (test_renderer
# checks them against finder/tuning.py).
#
# tab (array 'H'): VIG @0 (170) | EXPT @170 (256) | COS32 @426 (32) |
#                  mixed LUT @458 (64) | grey LUT @522 (64)
# prm (array 'i'): see P_* below.  acc (array 'i'): rings @0, ghosts @170.
T_VIG = const(0)
T_EXP = const(170)
T_COS = const(426)
T_LUT = const(458)
T_GLUT = const(522)
T_N = const(586)
P_IRIS = const(0)
P_RIM_HI = const(1)
P_FILL_R = const(2)
P_FILL_HI = const(3)
P_CORE = const(4)       # core dot floor level (0 = off)
P_FL = const(5)
P_GL = const(6)
P_GINV = const(7)
P_VMAX = const(8)
P_DIM = const(9)
P_LIFT = const(10)
P_RIM = const(11)
P_STAND_W = const(12)   # standing-wave weight, Q8 0..256
P_GHOST = const(13)
P_IRISC = const(14)
P_RIMQ = const(15)
P_SB = const(16)
P_FILL_V = const(17)    # calibrate fill floor level (0 = off)
P_N = const(18)

_KSRC = """
def pal_kernel(pal, acc, tab, prm):
    pp = ptr16(pal)
    ap = ptr32(acc)
    tp = ptr16(tab)
    qp = ptr32(prm)
    iris = qp[0]
    rim_hi = qp[1]
    fill_r = qp[2]
    fill_hi = qp[3]
    core = qp[4]
    fl = qp[5]
    gl = qp[6]
    ginv = qp[7]
    vmax = qp[8]
    dim = qp[9]
    lift = qp[10]
    rim = qp[11]
    sw = qp[12]
    gh = qp[13]
    irisc = qp[14]
    rim_q = qp[15]
    sb = qp[16]
    fill_v = qp[17]
    fill_e = fill_v + (fill_v >> 1)
    i = 0
    while i < 170:
        a = ap[i]
        ap[i] = 0
        g = ap[170 + i]
        ap[170 + i] = 0
        if i < iris:
            pp[i] = irisc
            i += 1
            continue
        if i < rim_hi:
            if rim >= 0:
                pp[i] = rim
                i += 1
                continue
            v = rim_q
        else:
            n = ((i - iris) * ginv) >> 8
            if n > 255:
                n = 255
            base = fl + ((gl * tp[170 + n]) >> 8)
            if sw:
                st = 512 + ((((640 * tp[426 + (i & 31)]) >> 8) * sb) >> 8)
                base += ((st - base) * sw) >> 8
            v = ((base + a) * tp[i]) >> 8
            if i <= 6:
                if v < core:
                    v = core
            if i < fill_hi:
                if i < fill_r:
                    if v < fill_v:
                        v = fill_v
                elif v < fill_e:
                    v = fill_e
        if v > vmax:
            v = vmax
        vd = v
        if dim != 256:
            vd = (v * dim) >> 8
        j = ((vd * 9 + 128) >> 8) + lift
        if j > 63:
            j = 63
        if j < 0:
            j = 0
        c = tp[458 + j]
        if gh:
            g = (g * tp[i]) >> 8
            if g > v:
                if dim != 256:
                    g = (g * dim) >> 8
                j = ((g * 9 + 128) >> 8) + lift
                if j > 63:
                    j = 63
                c = tp[522 + j]
        pp[i] = c
        i += 1
"""


def _ident(x):
    return x


def _compile_kernel():
    ns = {"ptr16": _ident, "ptr32": _ident}
    exec(_KSRC, ns)
    py = ns["pal_kernel"]
    try:
        vs = {}
        exec("@micropython.viper" + _KSRC, vs)
    except Exception:  # noqa: BLE001 - no viper (CPython, wasm): plain Python
        return py, "python"
    # Viper is only available on the device, so prove it matches the Python
    # kernel on the CHECK_PRMS frames (every branch) before use.
    if not kernel_agrees(py, vs["pal_kernel"]):
        return py, "python (viper self-check failed)"
    return vs["pal_kernel"], "viper"


# Self-check frames: rings + ghosts + fill with a ramp rim; a half-faded fill
# and the FOUND standing wave part-blended, with a colour rim and the menu
# dim; the iris closed, so the core dot is drawn, and floor and glow 0, so
# the ghosts win (dimmed and lifted).
CHECK_PRMS = (
    (30, 33, 90, 93, 1536, 300, 900, 900, 1600, 200, 2, -1, 0, 1, 0x6100, 1400, 200, 1024),
    (40, 43, 100, 103, 0, 300, 900, 900, 1792, 128, 0, 0x1234, 160, 1, 0x6100, 1400, 97, 512),
    (0, 0, 0, 0, 1536, 0, 0, 900, 1792, 200, 1, -1, 0, 1, 0x6100, 1400, 97, 0),
)


def kernel_agrees(ka, kb):
    """True if kernels ``ka`` and ``kb`` give identical palettes and cleared
    accumulators on every CHECK_PRMS frame (synthetic tables)."""
    tab = array.array("H", [(i * 37 + 11) & 0xFFFF for i in range(T_N)])
    for i in range(T_N - 128):
        tab[i] &= 0x1FF
    for vals in CHECK_PRMS:
        prm = array.array("i", vals)
        out = []
        for fn in (ka, kb):
            acc = array.array("i", [(i * 29) % 700 for i in range(2 * N_IDX)])
            pal = array.array("H", [0] * 256)
            fn(pal, acc, tab, prm)
            out.append(bytes(pal) + bytes(acc))
        if out[0] != out[1]:
            return False
    return True


pal_kernel, KERNEL = _compile_kernel()


# ---- ring-index map ---------------------------------------------------------
def build_map(rows):
    """GS8 index bytes for the top ``rows`` rows (120 = half map, 240 = full).

    Integer-only: idx = floor(sqrt(D)/2) with D = (2x-239)^2 + (2y-239)^2.
    """
    buf = bytearray(W * rows)
    for y in range(min(rows, 120)):
        dy = 2 * y - 239
        dy2 = dy * dy
        base = y * W
        k = 0
        for x in range(120, W):
            dx = 2 * x - 239
            d = dx * dx + dy2
            while (2 * k + 2) * (2 * k + 2) <= d:
                k += 1
            buf[base + x] = k              # k <= 168 (the corner)
            buf[base + 239 - x] = k
    for y in range(120, rows):             # mirror rows for the full map
        s = (239 - y) * W
        buf[y * W:(y + 1) * W] = buf[s:s + W]
    return buf


# ---- strip blit kernel --------------------------------------------------------
# Map bytes off..off+n-1 (whole rows) through the palette into ``dst``, four
# pixels per pass: one 32-bit load of map bytes, two 32-bit stores of pixel
# pairs (little-endian, as framebuf stores RGB565). Compiled with
# @micropython.viper where the port supports it (the ESP32 build; RingMap
# checks it against the framebuf path before use); elsewhere RingMap uses the
# framebuf palette blit.
_BSRC = """
def blit_kernel(dst, idx, pal, off: int, n: int):
    dp = ptr32(dst)
    ip = ptr32(idx)
    pp = ptr16(pal)
    i = off >> 2
    e = i + (n >> 2)
    d = 0
    while i < e:
        w = ip[i]
        dp[d] = pp[w & 0xFF] | (pp[(w >> 8) & 0xFF] << 16)
        dp[d + 1] = pp[(w >> 16) & 0xFF] | (pp[(w >> 24) & 0xFF] << 16)
        d += 2
        i += 1
"""


def _compile_blit():
    try:
        vs = {}
        exec("@micropython.viper" + _BSRC, vs)
    except Exception:  # noqa: BLE001 - no viper (CPython, wasm)
        return None
    return vs["blit_kernel"]


blit_kernel = _compile_blit()


def _aligned(b):
    """True if ``b``'s bytes start on a 4-byte boundary (32-bit loads and
    stores on the ESP32 fault otherwise); True where there is no uctypes."""
    try:
        import uctypes
    except ImportError:
        return True
    return uctypes.addressof(b) & 3 == 0


class RingMap:
    """Ring-index map (240x240 GS8, 57.6 KB) blitted into RGB565 strips
    through a palette, top to bottom: with the viper ``blit_kernel`` (``kind``
    "viper") once it matches the framebuf path on ``bufs`` (the strip
    buffers it will write), else a framebuf palette blit (``kind``
    "framebuf").
    """

    def __init__(self, strip_h, bufs, kernel=None):
        self.idx = build_map(W)
        self.h = strip_h
        self.n = W * strip_h
        self.kern = None
        self.kind = "framebuf"
        if framebuf is None:
            self.kind = None
            return
        self.map_fb = framebuf.FrameBuffer(self.idx, W, W, framebuf.GS8)
        k = blit_kernel if kernel is None else kernel
        if k is not None:
            ok = _aligned(self.idx)
            for b in bufs:
                ok = ok and _aligned(b)
            if ok and self.agrees(k, bufs[0]):
                self.kern = k
                self.kind = "viper" if kernel is None else "kernel"
            else:
                self.kind = "framebuf (kernel self-check failed)"

    def agrees(self, kern, buf):
        """True if ``kern`` writes the same strips into ``buf`` as the
        framebuf path, at the top, the centre and the bottom, through a
        palette of distinct colours. Leaves ``buf`` dirty (the next frame
        redraws it)."""
        arr = array.array("H", [(i * 4099 + 0x1235) & 0xFFFF for i in range(256)])
        pal = framebuf.FrameBuffer(arr, 256, 1, framebuf.RGB565)
        fb = framebuf.FrameBuffer(buf, W, self.h, framebuf.RGB565)
        k = self.kern
        try:
            for y0 in (0, 120 - self.h // 2, W - self.h):
                self.kern = None
                self.blit(y0, pal, arr, buf, fb)
                want = bytes(buf)
                fb.fill(0x5A5A)               # so a pixel the kernel skips shows
                kern(buf, self.idx, arr, y0 * W, self.n)
                if bytes(buf) != want:
                    return False
            return True
        finally:
            self.kern = k

    def blit(self, y0, pal, arr, buf, fb):
        """Field rows y0..y0+h-1 into the strip ``buf`` (``fb`` its
        FrameBuffer). ``pal``: the palette as a framebuf, ``arr``: its array."""
        k = self.kern
        if k is not None:
            k(buf, self.idx, arr, y0 * W, self.n)
            return
        fb.blit(self.map_fb, 0, -y0, -1, pal)


# ---- screen tables ----------------------------------------------------------
# lead / trail px per zone (thresholds.zone_tempo; FAR..HOT)
ZONE_LEAD = T.ZONE_LEAD_PX
ZONE_TRAIL = T.ZONE_TRAIL_PX


class RippleField:
    """Ring state, level and palette state. Per frame: ``tick()``, then the
    ``set_*`` targets, ``schedule()`` (or ``idle()``), ``cull()``; ``build()``
    on drawn frames."""

    def __init__(self):
        self.pal_arr = array.array("H", [0] * 256)
        self.pal = (framebuf.FrameBuffer(self.pal_arr, 256, 1, framebuf.RGB565)
                    if framebuf is not None else None)
        self._acc = array.array("i", [0] * (2 * N_IDX))   # rings | ghosts
        self.tab = array.array("H", [0] * T_N)
        self.prm = array.array("i", [0] * P_N)
        tab = self.tab
        for i in range(N_IDX):
            tab[T_VIG + i] = VIG[i]
        for i in range(256):
            tab[T_EXP + i] = EXPT[i]
        for i in range(32):
            tab[T_COS + i] = COS32[i]
        for j in range(LUT_N):
            tab[T_GLUT + j] = LUTS["grey"].c[j]
        # ring slots (lists: small ints never allocate)
        self.r_on = bytearray(MAXR)
        self.r_ghost = bytearray(MAXR)
        self.r_t0 = [0] * MAXR
        self.r_r0 = [0] * MAXR       # spawn radius, Q8 px
        self.r_v = [0] * MAXR        # speed, px/s (int, signed)
        self.r_amp = [0] * MAXR      # amplitude, Q8 levels
        self.r_lead = [0] * MAXR     # px (edge in the travel direction)
        self.r_trail = [0] * MAXR
        # LUT mixing (hue crossfade)
        self.mix = Lut(c=memoryview(self.tab)[T_LUT:T_LUT + LUT_N])
        self.frm = Lut()
        self.reset()

    def reset(self):
        self.mix.copy_from(LUTS["green"])
        self.ramp = "green"
        self.ramp_to = LUTS["green"]
        self.ramp_t0 = 0
        self.ramp_dur = 0
        for k in range(MAXR):
            self.r_on[k] = 0
        self.started = False
        self.next_spawn = 0
        self.last_spawn = 0
        self.period = 0
        self.spawns = 0
        self.dt = 50                 # smoothed frame interval, ms
        self.flash = 0               # flash-limit level slew per frame, Q8
        self.t = 0
        # displayed levels (Q8) and crossfade snapshot
        self.fl = FL_A
        self.gl = GL_A
        self.pu = PU_A
        self.gr = 20 * Q8
        self.xf = (0, 0, 0, 0)
        self.xf_on = False           # crossfade running (xf_t0 read only then)
        self.xf_t0 = 0
        self.dim = 256
        self.fill_r = 0              # calibrate fill radius, px
        self.fill_v = 0              # its floor level, Q8 (0 = off)
        self.stand_w = 0             # FOUND standing-wave weight, Q8
        self.iris = 0
        self.iris_from = 0
        self.iris_to = 0
        self.iris_on = False         # iris tween running (iris_t0 read only then)
        self.iris_t0 = 0
        self.ghosts = 0
        self.stand_t0 = 0            # standing-wave breathing clock (MENU holds it)

    # ---- clocks ------------------------------------------------------------
    def tick(self, t, fps_cap):
        """Move the frame clock to ``t``: smoothed frame interval ``dt`` and
        the flash-limit slew ``flash`` per frame. Returns the previous t."""
        if self.started:
            dt = ticks_diff(t, self.t)
            dt = 1 if dt < 1 else (250 if dt > 250 else dt)
            self.dt = (self.dt * 3 + dt) >> 2
        else:
            self.dt = 1000 // (fps_cap or T.FPS_TARGET)
        self.flash = (FLASH_Q8 * self.dt) // T.FLASH_LIMIT_MS + 1
        prev = self.t
        self.t = t
        return prev

    def hold(self, dt):
        """MENU: freeze rings, the ring schedule, crossfades, the iris and the
        standing-wave breathing by shifting their clocks ``dt`` ms (wrap-safe)."""
        for k in range(MAXR):
            self.r_t0[k] = ticks_add(self.r_t0[k], dt)
        self.next_spawn = ticks_add(self.next_spawn, dt)
        self.last_spawn = ticks_add(self.last_spawn, dt)
        self.xf_t0 = ticks_add(self.xf_t0, dt)
        self.iris_t0 = ticks_add(self.iris_t0, dt)
        self.ramp_t0 = ticks_add(self.ramp_t0, dt)
        self.stand_t0 = ticks_add(self.stand_t0, dt)

    # ---- ring bookkeeping --------------------------------------------------
    def spawn(self, t0, r0q, v, amp, lead, trail, ghost):
        k = 0
        oldest = 0
        best = -0x3FFFFFFF
        while k < MAXR:
            if not self.r_on[k]:
                break
            age = ticks_diff(t0, self.r_t0[k])
            if age > best:           # reuse the oldest slot when full
                best = age
                oldest = k
            k += 1
        if k == MAXR:
            k = oldest
        self.r_on[k] = 1
        self.r_ghost[k] = 1 if ghost else 0
        self.r_t0[k] = t0
        self.r_r0[k] = r0q
        self.r_v[k] = v
        self.r_amp[k] = amp
        self.r_lead[k] = lead
        self.r_trail[k] = trail
        return k

    def schedule(self, t, period, r0q, v, lead, trail, live, first):
        """Spawn the ring due by ``t`` (one per ``period`` ms; a ghost at
        0.6 x pulse_amp unless ``live``). ``first``: place rings already
        mid-flight, so a wake shows no intro. Returns 1 when a live ring
        spawned this frame (its heartbeat is due)."""
        if period != self.period:
            if self.period and not first:
                ago = ticks_diff(t, self.last_spawn)
                if 0 <= ago < self.period:        # recent spawn: re-time the next
                    nxt = ticks_add(self.last_spawn, period)
                    if ticks_diff(nxt, self.next_spawn) < 0:
                        self.next_spawn = nxt
            self.period = period
        amp = self.pu if live else (self.pu * GHOST_AMP) >> 8
        if first:
            for k in range(1, 8):
                age = k * period
                r = r0q + (v * age * 256) // 1000
                if (v > 0 and r > (170 + trail) * Q8) or (v < 0 and r < self.iris_to * Q8):
                    break
                self.last_spawn = ticks_add(t, -age)
                self.spawn(self.last_spawn, r0q, v, amp, lead, trail, not live)
            self.next_spawn = t
        lag = ticks_diff(t, self.next_spawn)
        if lag < 0:
            return 0
        if lag >= period:                          # frames were skipped
            self.next_spawn = ticks_add(t, -(lag % period))
        t0 = self.next_spawn
        self.spawn(t0, r0q, v, amp, lead, trail, not live)
        self.last_spawn = t0
        self.next_spawn = ticks_add(t0, period)
        if not live:
            return 0
        self.spawns += 1
        return 1

    def idle(self, t):
        """No ring schedule this frame: the next scheduled frame spawns at once."""
        self.next_spawn = t

    def ring_r(self, k, t):
        """Ring k radius at t, Q8 px (fractional: temporal AA needs it)."""
        va = self.r_v[k] * ticks_diff(t, self.r_t0[k])
        q = va // 1000                   # split so no product leaves small-int range
        return self.r_r0[k] + (q << 8) + (((va - q * 1000) << 8) // 1000)

    def cull(self, t):
        """Drop rings that have left the field; note whether ghosts remain."""
        ir = self.iris * Q8
        g = 0
        for k in range(MAXR):
            if not self.r_on[k]:
                continue
            rq = self.ring_r(k, t)
            v = self.r_v[k]
            age = ticks_diff(t, self.r_t0[k])
            if (v > 0 and rq - self.r_trail[k] * Q8 > N_IDX * Q8) or \
                    (v < 0 and rq + 64 * Q8 < ir) or age > 20000 or \
                    (v < 0 and rq < -self.r_trail[k] * Q8):
                self.r_on[k] = 0
            elif self.r_ghost[k]:
                g = 1
        self.ghosts = g

    # ---- per-frame state ---------------------------------------------------
    def set_ramp(self, name, t, dur):
        if name == self.ramp:
            return
        self.frm.copy_from(self.mix)
        self.ramp = name
        self.ramp_to = LUTS.get(name) or LUTS["green"]
        self.ramp_t0 = t
        self.ramp_dur = dur
        if dur <= 0:
            self.mix.copy_from(self.ramp_to)

    def _mix_ramp(self, t):
        if self.ramp_dur <= 0:
            return
        age = ticks_diff(t, self.ramp_t0)
        e = ease(EASE_IOC, age, self.ramp_dur)
        a = self.frm
        b = self.ramp_to
        m = self.mix
        if e >= 256:
            m.copy_from(b)
            self.ramp_dur = 0
            return
        for j in range(LUT_N):
            r = a.r[j] + (((b.r[j] - a.r[j]) * e) >> 8)
            g = a.g[j] + (((b.g[j] - a.g[j]) * e) >> 8)
            bb = a.b[j] + (((b.b[j] - a.b[j]) * e) >> 8)
            m.r[j] = r
            m.g[j] = g
            m.b[j] = bb
            m.c[j] = swap16(((r >> 3) << 11) | ((g >> 2) << 5) | (bb >> 3))

    def set_levels(self, t, fl, gl, pu, gr, restart):
        """Crossfade (600 ms in_out_cubic) the displayed levels toward targets."""
        if restart:
            self.xf = (self.fl, self.gl, self.pu, self.gr)
            self.xf_t0 = t
            self.xf_on = True
        if self.xf_on:
            e = ease(EASE_IOC, ticks_diff(t, self.xf_t0), T.ZONE_CROSSFADE_MS)
            if e >= 256:
                self.xf_on = False       # never compare a stale t0 again
        else:
            e = 256
        x = self.xf
        if e < 256:
            fl = x[0] + (((fl - x[0]) * e) >> 8)
            gl = x[1] + (((gl - x[1]) * e) >> 8)
            pu = x[2] + (((pu - x[2]) * e) >> 8)
            gr = x[3] + (((gr - x[3]) * e) >> 8)
        # flash limit: the full-field floor moves <= 2 steps / 333 ms
        step = self.flash
        d = fl - self.fl
        if self.started:
            if d > step:
                fl = self.fl + step
            elif d < -step:
                fl = self.fl - step
        self.fl = fl
        self.gl = gl
        self.pu = pu
        self.gr = gr if gr > 2 * Q8 else 2 * Q8

    def set_fill(self, on, r):
        """PAIRING calibrate fill disc (§6): floor level 4 inside ``r`` px
        while ``on``. When it ends it fades out at its last radius at the
        flash-limit rate instead of dropping the whole field in one frame."""
        if on:
            self.fill_r = r
            self.fill_v = FILL_V
        elif self.fill_v:
            v = self.fill_v - self.flash
            self.fill_v = v if v > 0 else 0

    def set_stand(self, on):
        """FOUND standing-wave weight: crossfades with the 600 ms state
        crossfade (§4), from and to the levels set_levels shows."""
        if not self.started:
            self.stand_w = Q8 if on else 0
            return
        step = (Q8 * self.dt) // T.ZONE_CROSSFADE_MS + 1
        w = self.stand_w + (step if on else -step)
        self.stand_w = Q8 if w > Q8 else (w if w > 0 else 0)

    def set_iris(self, t, r):
        if r != self.iris_to:
            self.iris_from = self.iris
            self.iris_to = r
            self.iris_t0 = t
            self.iris_on = True
        if not self.iris_on:
            self.iris = self.iris_to
            return
        e = ease(EASE_OC, ticks_diff(t, self.iris_t0), T.IRIS_ANIM_MS)
        if e >= 256:
            self.iris_on = False
        self.iris = self.iris_from + (((self.iris_to - self.iris_from) * e) >> 8)

    def set_dim(self, target):
        """Menu dim, slewed at 128 per 600 ms crossfade (under the flash limit)."""
        step = (128 * self.dt) // T.ZONE_CROSSFADE_MS + 1
        d = target - self.dim
        self.dim = target if -step <= d <= step else self.dim + (step if d > 0 else -step)

    # ---- palette -----------------------------------------------------------
    def build(self, t, core=0, rim=-1, vmax=V7, lift=0):
        """Rebuild palette entries 0..169 for time t.

        core: core-dot floor level (Q8, 0 = off) for indices 0..6.
        rim: -1 = ramp-coloured rim, else a swapped RGB565 rim colour.
        vmax: level cap (Q8; saver = 5). lift: LUT index lift (sun mode).
        The calibrate fill and the standing wave come from set_fill/set_stand.
        """
        self._mix_ramp(t)
        acc = self._acc
        dt = self.dt
        prof = PROF
        for k in range(MAXR):
            if not self.r_on[k]:
                continue
            rq = self.ring_r(k, t)
            v = self.r_v[k]
            av = v if v > 0 else -v
            lead = self.r_lead[k] * Q8
            aa = (AA_K * av * dt) // 1000            # 1.5*|v|/fps, Q8 px
            if aa > lead:
                lead = aa
            trail = self.r_trail[k] * Q8
            if v > 0:
                outer = lead
                inner = trail
            else:
                outer = trail
                inner = lead
            dist = rq - self.r_r0[k]
            if dist < 0:
                dist = -dist
            fade = dist // T.FADEIN_PX                 # 12 px fade-in, Q8
            if fade > 256:
                fade = 256
            w = (self.r_amp[k] * fade) >> 8
            if w <= 0:
                continue
            i0 = (rq - inner + 255) >> 8
            i1 = (rq + outer) >> 8
            if i0 < 0:
                i0 = 0
            if i1 > N_IDX - 1:
                i1 = N_IDX - 1
            off = N_IDX if self.r_ghost[k] else 0
            for i in range(i0, i1 + 1):
                dq = (i << 8) - rq
                if dq >= 0:
                    kk = (dq << 6) // outer
                else:
                    kk = ((-dq) << 6) // inner
                if kk < 64:
                    acc[off + i] += (w * prof[kk]) >> 8
        iris = self.iris
        if core and iris == 0:
            # the core dot is a floor (§4 rule 2); a crest newly spawned at C
            # (r 7..18 at level 7 for ~250 ms) would ring it as a dimmer
            # "pupil". Hold the ring term non-increasing outward over r 0..12
            # (running max inward), so the new crest fills the disc and then
            # detaches as a ring, with no dark dot or dark hairline inside.
            m = 0
            i = 12
            while i >= 0:
                if acc[i] > m:
                    m = acc[i]
                else:
                    acc[i] = m
                i -= 1
        rim_q = self.fl + self.gl
        if rim_q > V7:
            rim_q = V7
        if rim_q < RIM_MIN:
            rim_q = RIM_MIN
        fr = self.fill_r if self.fill_v else 0
        q = self.prm
        q[P_IRIS] = iris
        q[P_RIM_HI] = iris + T.IRIS_RIM_PX if iris > 0 else 0
        q[P_FILL_R] = fr
        q[P_FILL_HI] = fr + 3 if fr > 0 else 0
        q[P_CORE] = core
        q[P_FL] = self.fl
        q[P_GL] = self.gl
        q[P_GINV] = (64 * Q8 * Q8) // self.gr
        q[P_VMAX] = vmax
        q[P_DIM] = self.dim
        q[P_LIFT] = lift
        q[P_RIM] = rim
        q[P_STAND_W] = self.stand_w
        q[P_GHOST] = self.ghosts
        q[P_IRISC] = BG_IRIS
        q[P_RIMQ] = rim_q
        ph = ticks_diff(t, self.stand_t0) % T.STANDING_PERIOD_MS
        q[P_SB] = SB_A + ((SB_B * SIN[ph * 360 // T.STANDING_PERIOD_MS]) >> 14)
        q[P_FILL_V] = self.fill_v
        pal_kernel(self.pal_arr, acc, self.tab, q)
