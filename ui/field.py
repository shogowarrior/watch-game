"""RippleField: ring-index map, ramp LUTs and the per-frame palette (ui-spec §4).

The background is a GS8 ring-index map (idx = min(169, floor(hypot(x-119.5,
y-119.5)))) blitted through a 256x1 RGB565 palette, so every radial effect
(glow, rings, iris, vignette, crossfades) is a rebuild of palette entries
0..169. The hot paths here are integer-only (Q8 levels: 256 = one ramp
step, Q8 radii) so a frame allocates nothing on MicroPython, where floats
are heap objects. Floats are used only at import and a few per frame.

Pure Python except the optional FrameBuffers, so the maths also runs on
CPython (framebuf is MicroPython-only).
"""

import array
import math

from finder.compat import const, ticks_diff
from ui import BG_IRIS, RAMP_HEX, swap16

try:
    import framebuf
except ImportError:  # CPython: palette maths only
    framebuf = None

W = const(240)
N_IDX = const(170)          # ring indices 0..169 (corner = 169)
LUT_N = const(64)
MAXR = const(12)            # ring slots
Q8 = const(256)
V7 = const(1792)            # level 7 in Q8


# ---- tables (import time) ---------------------------------------------------
def _snap(c8, bits):
    m = (1 << bits) - 1
    return (c8 * m + 127) // 255


def lut_entry(stops, j):
    """Snapped 8-bit (r, g, b) of LUT[j]: linear 8-bit sRGB between stops
    (position j/63*7 = j/9), rounded half up, then snapped to RGB565 and
    bit-replicated back to 8 bits."""
    k = j // 9
    f = j - k * 9
    a = stops[k]
    b = stops[k + 1] if k < 7 else a
    out = []
    for sh, bits in ((16, 5), (8, 6), (0, 5)):
        ca = (a >> sh) & 0xFF
        cb = (b >> sh) & 0xFF
        c = (ca * (9 - f) * 2 + cb * f * 2 + 9) // 18
        s = _snap(c, bits)
        out.append((s << (8 - bits)) | (s >> (2 * bits - 8)))
    return out[0], out[1], out[2]


def pack_sw(r, g, b):
    """8-bit rgb (already 565-exact or not) -> byte-swapped RGB565."""
    return swap16(((r >> 3) << 11) | ((g >> 2) << 5) | (b >> 3))


class Lut:
    """64-entry ramp LUT: ``c`` = swapped RGB565, ``r/g/b`` = snapped 8-bit."""

    def __init__(self, stops=None, c=None):
        self.c = array.array("H", [0] * LUT_N) if c is None else c
        self.r = bytearray(LUT_N)
        self.g = bytearray(LUT_N)
        self.b = bytearray(LUT_N)
        if stops is not None:
            for j in range(LUT_N):
                r, g, b = lut_entry(stops, j)
                self.r[j] = r
                self.g[j] = g
                self.b[j] = b
                self.c[j] = pack_sw(r, g, b)

    def copy_from(self, o):
        for j in range(LUT_N):
            self.r[j] = o.r[j]
            self.g[j] = o.g[j]
            self.b[j] = o.b[j]
            self.c[j] = o.c[j]


LUTS = {name: Lut(stops) for name, stops in RAMP_HEX.items()}

# Vignette (tokens.field.vignette_stops), Q8 per ring index.
VIG_STOPS = ((0, 1.0), (88, 1.0), (120, 0.4), (168, 0.15))


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
EASE_SINE = array.array("h", [int(256 * (-(math.cos(math.pi * k / 64.0) - 1) / 2) + 0.5)
                              for k in range(65)])


def ease(tab, age, dur):
    """Q8 eased progress of ``age`` ms into ``dur`` ms."""
    if age >= dur:
        return 256
    if age <= 0:
        return 0
    return tab[(age << 6) // dur]



# ---- palette kernel -----------------------------------------------------------
# One fused pass over ring indices 0..169: base (floor + halo glow, or the FOUND
# standing wave) + ring accumulators, vignette, core dot / calibrate fill / iris
# and rim, level cap, menu dim, LUT lookup, ghost channel. It also clears the
# accumulators for the next frame. The same source is compiled with
# @micropython.viper where the port supports it (the ESP32 build) and as plain
# Python otherwise (CPython, the wasm port), via identity pointer shims.
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
P_CORE = const(4)
P_FL = const(5)
P_GL = const(6)
P_GINV = const(7)
P_VMAX = const(8)
P_DIM = const(9)
P_LIFT = const(10)
P_RIM = const(11)
P_STAND = const(12)
P_GHOST = const(13)
P_IRISC = const(14)
P_RIMQ = const(15)
P_SB = const(16)
P_N = const(17)

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
    standing = qp[12]
    gh = qp[13]
    irisc = qp[14]
    rim_q = qp[15]
    sb = qp[16]
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
            if standing:
                base = 512 + ((((640 * tp[426 + (i & 31)]) >> 8) * sb) >> 8)
            else:
                n = ((i - iris) * ginv) >> 8
                if n > 255:
                    n = 255
                base = fl + ((gl * tp[170 + n]) >> 8)
            v = ((base + a) * tp[i]) >> 8
            if core:
                if i <= 6:
                    if v < 1536:
                        v = 1536
            if i < fill_hi:
                if i < fill_r:
                    if v < 1024:
                        v = 1024
                elif v < 1536:
                    v = 1536
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
                j = (g * 9 + 128) >> 8
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


# Self-check frames: rings + ghosts + fill + core with a ramp rim, then the
# FOUND standing wave with a colour rim and the menu dim.
CHECK_PRMS = (
    (30, 33, 90, 93, 1, 300, 900, 900, 1600, 200, 2, -1, 0, 1, 0x6100, 1400, 200),
    (40, 43, 0, 0, 0, 300, 900, 900, 1792, 128, 0, 0x1234, 1, 1, 0x6100, 1400, 97),
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
            v = k if k < 169 else 169
            buf[base + x] = v
            buf[base + 239 - x] = v
    for y in range(120, rows):             # mirror rows for the full map
        s = (239 - y) * W
        buf[y * W:(y + 1) * W] = buf[s:s + W]
    return buf


def ring_index(x, y):
    """Reference float formula (tests)."""
    return min(169, int(math.floor(math.sqrt((x - 119.5) ** 2 + (y - 119.5) ** 2))))


class RingMap:
    """Ring-index map blitted into RGB565 strips through a palette.

    ``full=False`` (default) keeps only the top half (28.8 KB): top strips are
    one offset blit, bottom strips blit row by row from the mirrored row
    (y -> 239 - y) into 240x1 row FrameBuffers over the strip. ``full=True``
    keeps the whole 57.6 KB map and does one offset blit per strip.
    """

    def __init__(self, strip_buf, strip_h, full=False):
        self.full = full
        rows = 240 if full else 120
        self.idx = build_map(rows)
        self.h = strip_h
        if framebuf is None:
            return
        self.map_fb = framebuf.FrameBuffer(self.idx, W, rows, framebuf.GS8)
        self.strip_fb = framebuf.FrameBuffer(strip_buf, W, strip_h, framebuf.RGB565)
        mv = memoryview(strip_buf)
        self.rows = [framebuf.FrameBuffer(mv[k * W * 2:(k + 1) * W * 2], W, 1, framebuf.RGB565)
                     for k in range(strip_h)]

    def blit(self, y0, pal):
        """Paint strip rows y0..y0+h-1 of the field into the strip buffer."""
        if self.full or y0 + self.h <= 120:
            self.strip_fb.blit(self.map_fb, 0, -y0, -1, pal)
            return
        rows = self.rows
        mfb = self.map_fb
        for k in range(self.h):
            y = y0 + k
            src = y if y < 120 else 239 - y
            rows[k].blit(mfb, 0, -src, -1, pal)


# ---- screen tables ----------------------------------------------------------
# lead / trail px per zone (thresholds.zone_tempo; FAR..HOT)
ZONE_LEAD = (3, 3, 3, 3)
ZONE_TRAIL = (22, 20, 18, 14)


class RippleField:
    """Ring state + palette builder. ``step()`` once per frame, then ``build()``."""

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
        self.mix.copy_from(LUTS["green"])
        self.ramp = "green"
        self.ramp_to = LUTS["green"]
        self.ramp_t0 = 0
        self.ramp_dur = 0
        self.reset()

    def reset(self):
        self.mix.copy_from(LUTS["green"])
        self.ramp = "green"
        self.ramp_to = LUTS["green"]
        self.ramp_dur = 0
        for k in range(MAXR):
            self.r_on[k] = 0
        self.started = False
        self.next_spawn = 0
        self.last_spawn = 0
        self.period = 0
        self.spawns = 0
        self.dt = 50                 # smoothed frame interval, ms
        self.t = 0
        # displayed levels (Q8) and crossfade snapshot
        self.fl = 77
        self.gl = 512
        self.pu = 1024
        self.gr = 20 * Q8
        self.xf = (0, 0, 0, 0)
        self.xf_on = False           # crossfade running (xf_t0 read only then)
        self.xf_t0 = 0
        self.dim = 256
        self.iris = 0
        self.iris_from = 0
        self.iris_to = 0
        self.iris_on = False         # iris tween running (iris_t0 read only then)
        self.iris_t0 = 0
        self.ghosts = 0

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

    def ring_r(self, k, t):
        """Ring k radius at t, Q8 px (fractional: temporal AA needs it)."""
        va = self.r_v[k] * ticks_diff(t, self.r_t0[k])
        q = va // 1000                   # split so no product leaves small-int range
        return self.r_r0[k] + (q << 8) + (((va - q * 1000) << 8) // 1000)

    def _cull(self, t):
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
            e = ease(EASE_IOC, ticks_diff(t, self.xf_t0), 600)
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
        # flash limit: full-field floor moves <= 2 steps / 333 ms
        step = (1536 * self.dt) // 1000 + 1
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

    def set_iris(self, t, r):
        if r != self.iris_to:
            self.iris_from = self.iris
            self.iris_to = r
            self.iris_t0 = t
            self.iris_on = True
        if not self.iris_on:
            self.iris = self.iris_to
            return
        e = ease(EASE_OC, ticks_diff(t, self.iris_t0), 300)
        if e >= 256:
            self.iris_on = False
        self.iris = self.iris_from + (((self.iris_to - self.iris_from) * e) >> 8)

    def set_dim(self, target):
        """Menu dim, slewed at 128/600 ms (under the flash limit)."""
        step = (128 * self.dt) // 600 + 1
        d = target - self.dim
        self.dim = target if -step <= d <= step else self.dim + (step if d > 0 else -step)

    # ---- palette -----------------------------------------------------------
    def build(self, t, core=False, rim=-1, fill_r=0, standing=False,
              vmax=V7, lift=0):
        """Rebuild palette entries 0..169 for time t.

        core: iris-closed sun floor (indices 0..6 >= level 6).
        rim: -1 = ramp-coloured rim, else a swapped RGB565 rim colour.
        fill_r: PAIRING calibrate fill radius (px, 0 = off).
        standing: FOUND standing wave instead of floor + glow.
        vmax: level cap (Q8; saver = 5). lift: LUT index lift (sun mode).
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
            aa = (384 * av * dt) // 1000             # 1.5*|v|/fps, Q8 px
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
            fade = dist // 12                          # 12 px fade-in, Q8
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
        if rim_q < 1280:
            rim_q = 1280
        q = self.prm
        q[P_IRIS] = iris
        q[P_RIM_HI] = iris + 3 if iris > 0 else 0
        q[P_FILL_R] = fill_r
        q[P_FILL_HI] = fill_r + 3 if fill_r > 0 else 0
        q[P_CORE] = 1 if core else 0
        q[P_FL] = self.fl
        q[P_GL] = self.gl
        q[P_GINV] = (64 * Q8 * Q8) // self.gr
        q[P_VMAX] = vmax
        q[P_DIM] = self.dim
        q[P_LIFT] = lift
        q[P_RIM] = rim
        q[P_STAND] = 1 if standing else 0
        q[P_GHOST] = self.ghosts
        q[P_IRISC] = BG_IRIS
        q[P_RIMQ] = rim_q
        q[P_SB] = 154 + ((102 * SIN[(t % 2400) * 360 // 2400]) >> 14)   # 0.6+0.4 sin
        pal_kernel(self.pal_arr, acc, self.tab, q)
