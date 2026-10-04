"""Theme base (ui-spec §4A): a theme draws the field layer of a frame.

The renderer keeps every piece of game-side state in its RippleField (ring
schedule, crossfaded levels, iris, hue crossfade, menu dim, calibrate fill,
standing-wave weight) and steps it on every frame, drawn or not. A theme only
reads that state and the RenderParams, and draws the field:

    th = make("sonar", renderer)             # ui.themes.make
    th.build(p, t, core, rim, vmax, lift)    # once per drawn frame, after the plan
    for k in range(NB):                      # per 240x60 band (motor serviced between)
        th.blit(k * BH, bands[k], band_fbs[k])
    if th.layer:
        th.draw(fb)                          # once over the whole 240x240 frame
    th.dirty, th.spans                       # what changed since the last drawn frame

``build`` arguments are what Renderer._palette computes for the field:
``core`` the core-dot floor level (Q8, 0 = off), ``rim`` -1 for a ramp
coloured iris rim or a swapped RGB565 rim colour, ``vmax`` the level cap (Q8;
the battery saver lowers it), ``lift`` the LUT index lift (sun mode).

Contract (tests/test_themes.py checks it for every theme):

- No allocation per frame once the first frame of a moment has been drawn
  (AGENTS.md hard rule 2): preallocate buffers, keep state in ints, arrays and
  lists of small ints; floats only at import or in ``__init__``.
- ``dirty`` bit k is set when any field pixel in rows 24k..24k+23 may differ
  from the previous drawn frame, and ``spans[2k]``, ``spans[2k + 1]`` are the
  first and last columns that may differ (inclusive). More is allowed, less
  never. The first frame, a wake (a gap of more than WAKE_MS) and a theme
  switch report everything (``everything()``).
- Draws the lens (iris disc in the theme's lens colour, rim as ui-spec §2),
  the core dot (§4 rule 2) and the PAIRING calibrate fill (§6). The Radial
  palette below does all three; Arcade and Tide burn them into their own
  maps with ``_disc``.
- Motion is time-based: ``dt`` (ms since the last drawn frame, at most
  DT_MAX like the field's step, 0 in MENU, where the field is frozen) or the
  beat (``beat_age``: ms since the last ring spawn the field scheduled, so
  heartbeats stay locked to it). A wake (``clock``) shows the current state
  with no catch-up: no ping, swell or slow-down replays.
- Moments come from ``moment`` and the screen's params from ``live``: in
  the MENU both are those of the screen under it (ui-spec §4A rule 2), also
  for a theme made in the MENU.
- ``reset()`` returns the theme to the state of a fresh one (frames after a
  renderer reset do not depend on what was drawn before).

Loading (ui/themes/__init__.py ``stages``): a module import does only cheap
work (constants, small tables). One-time heavy work (kernel compiles and
self-checks, big tables) goes in an optional module-level generator
``load()`` that sets module globals, yields between steps and is idempotent
(a step already done is skipped, also after a load abandoned half-way).
``__init__`` only allocates; heavy construction (map builds, per-buffer
kernel checks) goes in the generator ``prepare()``. Each step should take at
most about STEP_US on a 32-bit desktop MicroPython (roughly 200x that on the
watch), except a single compile, which cannot be split.
- Levels go through the theme's ramps like the field's (``Ramps.color``):
  Q8 levels (256 = one ramp step), capped at ``vmax``, scaled by the menu
  dim, LUT index ((v * 9 + 128) >> 8) + lift.
"""

import array

from finder import tuning as T
from finder.compat import const, ticks_diff
from ui import swap16
from ui.field import (COS32, EASE_IOC, EXPT, LUTS, LUT_N, N_IDX, PROF, Q8, RIM_MIN, SB_A,
                      SB_B, SIN, V7, VIG, Lut, RingMap, build_quadrant, ease, pal_kernel)

try:
    import framebuf
except ImportError:  # CPython: palette maths only
    framebuf = None

W = const(240)
BH = const(60)              # renderer band height (ui/renderer.py BH)
NB = const(4)
SH = const(24)              # dirty-strip height
NS = const(10)
ALL = const(0x3FF)          # every dirty strip
WAKE_MS = const(450)        # a longer gap between drawn frames is a wake: no catch-up
                            # (one missed slot at the slowest 5 fps lock is 400 ms)
DT_MAX = const(250)         # motion advances at most this per frame (as the field's)
STEP_US = const(1500)       # load step budget on a 32-bit desktop build (~0.3 s on the watch)

# moments (ui-spec §4A rule 2)
M_LIVE = const(0)
M_LISTEN = const(1)
M_STILL = const(2)
M_SCAN = const(3)
M_FOUND = const(4)

S_FOUND = T.SCREENS.index("FOUND")
S_SCANNING = T.SCREENS.index("SCANNING")
S_MENU = T.SCREENS.index("MENU")

# palette kernel layout (ui/field.py T_*, P_*): tab VIG @0 | EXPT @170 |
# COS32 @426 | LUT @458 | grey LUT @522
_T_EXP = const(170)
_T_COS = const(426)
_T_LUT = const(458)
_T_GLUT = const(522)
_T_N = const(586)
_P_N = const(18)


def theme_luts(name):
    """{ramp: Lut} for a theme (tokens.themes; ripple: tokens.ramp)."""
    if name == "ripple":
        return LUTS
    src = T.THEME_RAMP_LUT[name]
    return {r: Lut(src[r]) for r in T.RAMP_NAMES}


class Ramps:
    """A theme's green / gold / grey LUTs and their live mix, which follows
    the field's hue crossfade (ui-spec §4: 1500 ms, 400 ms into FOUND).

    ``mix.c`` can be a view into a palette kernel table (Radial does this)."""

    def __init__(self, name, mix_c=None):
        self.luts = theme_luts(name)
        self.grey = self.luts["grey"]
        self.mix = Lut(c=mix_c)
        self.frm = Lut()
        self.to = self.luts["green"]
        self.ramp = None
        self.dur = 0

    def follow(self, f, t):
        """Track ``f`` (the RippleField): a ramp change starts the same
        crossfade (read live from f.ramp_t0, so MENU holds it); a new
        theme, or one after ``snap()``, starts on the field's ramp."""
        if f.ramp != self.ramp:
            snap = self.ramp is None
            self.frm.copy_from(self.mix)
            self.ramp = f.ramp
            self.to = self.luts.get(f.ramp) or self.luts["green"]
            self.dur = 0 if snap else f.ramp_dur
            if self.dur <= 0:
                self.mix.copy_from(self.to)
        if self.dur <= 0:
            return
        e = ease(EASE_IOC, ticks_diff(t, f.ramp_t0), self.dur)
        m = self.mix
        b = self.to
        if e >= 256:
            m.copy_from(b)
            self.dur = 0
            return
        a = self.frm
        for j in range(LUT_N):
            r = a.r[j] + (((b.r[j] - a.r[j]) * e) >> 8)
            g = a.g[j] + (((b.g[j] - a.g[j]) * e) >> 8)
            bb = a.b[j] + (((b.b[j] - a.b[j]) * e) >> 8)
            m.r[j] = r
            m.g[j] = g
            m.b[j] = bb
            m.c[j] = swap16(((r >> 3) << 11) | ((g >> 2) << 5) | (bb >> 3))

    def snap(self):
        """Next follow() starts from the field's current ramp (theme switch)."""
        self.ramp = None

    def index(self, v, vmax, dim, lift):
        """Q8 level -> LUT index, exactly as the field's palette kernel."""
        if v > vmax:
            v = vmax
        if dim != 256:
            v = (v * dim) >> 8
        j = ((v * 9 + 128) >> 8) + lift
        return 63 if j > 63 else (0 if j < 0 else j)

    def color(self, v, vmax, dim, lift):
        """Q8 level -> swapped RGB565 in the mixed ramp."""
        return self.mix.c[self.index(v, vmax, dim, lift)]

    def grey_color(self, v, vmax, dim, lift):
        return self.grey.c[self.index(v, vmax, dim, lift)]


class Radial:
    """The field's palette kernel (ui/field.py pal_kernel, viper on the
    watch) in a theme's colours: 256 palette entries for the ring-index map,
    entries 0..169 by radius. Floor, centre glow (or the halo outside the
    iris), FOUND standing wave, vignette, core dot, calibrate fill, lens and
    rim come from ``setup``; extra radial terms go into ``acc`` (Q8 levels per
    ring index, ``add_ring``) before ``run``. Blit the
    result with the renderer's RingMap: ``r.map.blit(y0, rad.pal, rad.pal_arr,
    buf, fb)``."""

    def __init__(self, name):
        tab = array.array("H", [0] * _T_N)
        for i in range(N_IDX):
            tab[i] = VIG[i]
        for i in range(256):
            tab[_T_EXP + i] = EXPT[i]
        for i in range(32):
            tab[_T_COS + i] = COS32[i]
        self.tab = tab
        self.ramps = Ramps(name, memoryview(tab)[_T_LUT:_T_LUT + LUT_N])
        g = self.ramps.grey
        for j in range(LUT_N):
            tab[_T_GLUT + j] = g.c[j]
        self.prm = array.array("i", [0] * _P_N)
        self.acc = array.array("i", [0] * (2 * N_IDX))   # levels | ghost channel
        self.pal_arr = array.array("H", [0] * 256)
        self.pal = (framebuf.FrameBuffer(self.pal_arr, 256, 1, framebuf.RGB565)
                    if framebuf is not None else None)
        self.irisc = T.THEME_IRIS[name]

    def setup(self, f, t, core, rim, vmax, lift, fl, gl, gr, stand_w=-1, ghosts=0):
        """Kernel parameters from the field ``f`` with theme levels ``fl``
        (floor), ``gl`` (glow amplitude), ``gr`` (glow radius, Q8 px).
        ``stand_w`` -1 = the field's FOUND standing-wave weight. ``ghosts``:
        1 when the ghost channel (acc[170:]) holds grey rings."""
        iris = f.iris
        rim_q = fl + gl
        if rim_q > V7:
            rim_q = V7
        if rim_q < RIM_MIN:
            rim_q = RIM_MIN
        fr = f.fill_r if f.fill_v else 0
        q = self.prm
        q[0] = iris
        q[1] = iris + T.IRIS_RIM_PX if iris > 0 else 0
        q[2] = fr
        q[3] = fr + 3 if fr > 0 else 0
        q[4] = core
        q[5] = fl
        q[6] = gl
        q[7] = (64 * Q8 * Q8) // (gr if gr > 2 * Q8 else 2 * Q8)
        q[8] = vmax
        q[9] = f.dim
        q[10] = lift
        q[11] = rim
        q[12] = f.stand_w if stand_w < 0 else stand_w
        q[13] = ghosts
        q[14] = self.irisc
        q[15] = rim_q
        ph = ticks_diff(t, f.stand_t0) % T.STANDING_PERIOD_MS
        q[16] = SB_A + ((SB_B * SIN[ph * 360 // T.STANDING_PERIOD_MS]) >> 14)
        q[17] = f.fill_v

    def add_ring(self, rq, amp, lead, trail, ghost=False):
        """One ring profile (1 - smoothstep, as the field's rings) centred
        on radius ``rq`` (Q8 px), peak ``amp`` (Q8 levels), ``lead`` /
        ``trail`` px outward / inward (>= 1)."""
        acc = self.acc
        lead *= Q8
        trail *= Q8
        i0 = (rq - trail + 255) >> 8
        i1 = (rq + lead) >> 8
        if i0 < 0:
            i0 = 0
        if i1 > N_IDX - 1:
            i1 = N_IDX - 1
        off = N_IDX if ghost else 0
        prof = PROF
        for i in range(i0, i1 + 1):
            dq = (i << 8) - rq
            if dq >= 0:
                kk = (dq << 6) // lead
            else:
                kk = ((-dq) << 6) // trail
            if kk < 64:
                acc[off + i] += (amp * prof[kk]) >> 8

    def run(self):
        """Rebuild palette entries 0..169 (and clear ``acc``)."""
        pal_kernel(self.pal_arr, self.acc, self.tab, self.prm)


class QuadMap(RingMap):
    """A theme's own GS8 index map that is symmetric in x and y about
    (119.5, 119.5), blitted through a palette like the ring map (the same
    viper quadrant kernel on the watch once it matches the framebuf path,
    else a framebuf palette blit). ``idx``: the full 240x240 map. With
    ``keep=False`` the full map (57.6 KB) and its framebuf are dropped once
    the kernel runs, which reads only the quadrant ``q``; draw into ``q``
    then."""

    def __init__(self, idx, band_h, bufs, keep=True):
        self.idx = idx
        self.h = band_h
        self.kern = None
        self.q = None
        self.kind = "framebuf"
        if framebuf is None:
            self.kind = None
            return
        self.map_fb = framebuf.FrameBuffer(self.idx, W, W, framebuf.GS8)
        from ui.field import blit_kernel, _aligned
        if blit_kernel is not None:
            self.q = build_quadrant(self.idx)
            ok = _aligned(self.q)
            for b in bufs:
                ok = ok and _aligned(b)
            if ok and self.agrees(blit_kernel, bufs[0]):
                self.kern = blit_kernel
                self.kind = "viper"
                if not keep:
                    self.idx = None
                    self.map_fb = None
            else:
                self.q = None
                self.kind = "framebuf (kernel self-check failed)"


def quadrant_mirror(q, rows=120):
    """Full 240-row map from a top-right quadrant builder's bytes ``q``
    (rows 0..119, columns 120..239), mirrored in x and y."""
    full = bytearray(W * W)
    for y in range(rows):
        src = y * 120
        base = y * W
        for x in range(120):
            v = q[src + x]
            full[base + 120 + x] = v
            full[base + 119 - x] = v
    for y in range(120, W):
        s = (239 - y) * W
        full[y * W:(y + 1) * W] = full[s:s + W]
    return full


def _disc(fb, r, c):
    """Filled disc of pixels whose centres lie within about ``r`` of
    (119.5, 119.5): four quadrant ellipses, so it is symmetric like the ring
    map (framebuf circles centre on a pixel)."""
    if r <= 0:
        return
    rr = r - 1
    fb.ellipse(120, 119, rr, rr, c, True, 1)       # Q1 top right
    fb.ellipse(119, 119, rr, rr, c, True, 2)       # Q2 top left
    fb.ellipse(119, 120, rr, rr, c, True, 4)       # Q3 bottom left
    fb.ellipse(120, 120, rr, rr, c, True, 8)       # Q4 bottom right


def moment_of(r, p):
    """The moment of params ``p`` as renderer ``r`` plans them (§4A rule 2),
    ignoring the MENU (see Theme.moment)."""
    scr = r._scr
    if scr == S_FOUND:
        return M_FOUND
    sub = r._sub
    if r.pacer or (scr == S_SCANNING and sub is not None and sub != "ready"):
        return M_SCAN
    v = p.speed_px_s
    if v > 0:
        return M_LIVE
    if v < 0:
        return M_LISTEN
    return M_STILL


class Theme:
    """Base class. Subclasses set ``name`` and implement ``build`` and
    ``blit`` (and ``draw`` with ``layer = True``). See the module docstring."""

    name = "ripple"
    layer = False

    def __init__(self, r):
        self.r = r                  # the renderer (field, map, band buffers, plan state)
        self.f = r.field
        self.dirty = ALL
        self.spans = bytearray(2 * NS)
        self.t = 0
        self.dt = 0
        self.wake = True
        self.started = False
        self.everything()

    def reset(self):
        """Renderer reset: the next frame starts fresh (a wake)."""
        self.started = False

    # ---- per-frame helpers ---------------------------------------------------
    def clock(self, p, t):
        """Advance the theme clock to ``t``: ``dt`` = ms since the last drawn
        frame, at most DT_MAX (0 in MENU, where the field is frozen);
        ``wake`` on the first frame (after ``__init__``, ``reset()`` or a
        dark spell, ui/themes ThemedRenderer.frame) or after a gap over
        WAKE_MS: snap eased state, report everything."""
        d = ticks_diff(t, self.t)
        if not self.started or d < 0 or d > WAKE_MS:
            self.wake = True
            d = 0
        else:
            self.wake = False
            if d > DT_MAX:
                d = DT_MAX
        if self.r._scr == S_MENU:
            d = 0
        self.t = t
        self.dt = d
        self.started = True
        return d

    def moment(self, p):
        """M_LIVE / M_LISTEN / M_STILL / M_SCAN / M_FOUND (ui-spec §4A rule 2).
        In the MENU: the moment of the screen under it (the renderer's
        ``m_live``), or with none yet FOUND if the params are gold (the game
        sends gold only in FOUND), else by speed."""
        r = self.r
        if r._scr == S_MENU:
            m = getattr(r, "m_live", -1)
            if m >= 0:
                return m
            if p.ramp == "gold":
                return M_FOUND
        return moment_of(r, p)

    def live(self, p):
        """The RenderParams of the screen: in the MENU, those of the last
        frame drawn under it (band, sub, glyph...), when there was one."""
        r = self.r
        if r._scr == S_MENU:
            q = getattr(r, "p_live", None)
            if q is not None:
                return q
        return p

    def zone(self, p):
        """Zone 0..3 (FAR..HOT), 0 when unknown."""
        z = p.zone
        return z if z is not None and 0 <= z <= 3 else 0

    def iq(self):
        """Intensity, Q8 0..256 (converted once per params object by the renderer)."""
        return self.r._iq

    def beat_age(self, t):
        """ms since the field's last ring spawn (the beat): 0..period while
        rings are scheduled, growing without bound when none are (Still,
        FOUND, right after a reset)."""
        f = self.f
        a = ticks_diff(t, f.last_spawn)
        return 0 if a < 0 else a

    def everything(self):
        """Report every pixel as changed."""
        self.dirty = ALL
        sp = self.spans
        for k in range(NS):
            sp[2 * k] = 0
            sp[2 * k + 1] = W - 1

    def clear_dirty(self):
        self.dirty = 0
        sp = self.spans
        for k in range(NS):
            sp[2 * k] = 255
            sp[2 * k + 1] = 0

    def mark(self, x0, y0, x1, y1):
        """Add the box x0..x1, y0..y1 (inclusive, clipped) to ``dirty``."""
        if x1 < 0 or y1 < 0 or x0 > W - 1 or y0 > W - 1:
            return
        if x0 < 0:
            x0 = 0
        if y0 < 0:
            y0 = 0
        if x1 > W - 1:
            x1 = W - 1
        if y1 > W - 1:
            y1 = W - 1
        sp = self.spans
        d = self.dirty
        for k in range(y0 // SH, y1 // SH + 1):
            d |= 1 << k
            if x0 < sp[2 * k]:
                sp[2 * k] = x0
            if x1 > sp[2 * k + 1]:
                sp[2 * k + 1] = x1
        self.dirty = d

    # ---- the interface -------------------------------------------------------
    def prepare(self):
        """Heavy construction in steps of at most about STEP_US (a
        generator: yield between steps). ``make`` runs it whole."""
        return
        yield

    def build(self, p, t, core, rim, vmax, lift):
        raise NotImplementedError

    def blit(self, y0, buf, fb):
        raise NotImplementedError

    def draw(self, fb):
        pass
