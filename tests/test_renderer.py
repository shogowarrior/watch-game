"""ui/ renderer: LUT/palette maths, ring timing, heartbeats, text cache, allocation.

Pure maths (LUTs, ring map, palette kernel, PNG) runs on CPython too; the
frame tests need MicroPython framebuf and run in the wasm runner:

    node tools/mpy/run.mjs tests/runner.py test_renderer
"""

import array
import gc
import math
import sys

try:
    import framebuf  # noqa: F401
    HAVE_FB = True
except ImportError:
    HAVE_FB = False

from finder import tuning as T
from finder.compat import ticks_add
from tests import Skip
from tools import render_snapshots as rs  # the snapshot fixtures: make_params, hunt, _lost ...
from ui import PROX, BG_IRIS, swap16
from ui import field as fld

make_params = rs.make_params

if HAVE_FB:
    from ui.renderer import FrameCapture, Renderer
    from ui import renderer as R
    from ui import text as tx
    from ui import font

T0 = rs.T0


def _need_fb():
    if not HAVE_FB:
        raise Skip("framebuf is MicroPython-only")
CORE = fld.q8(T.CORE_DOT_LEVEL)
MENU_KW = dict(rs._menu, sub="0", menu_rows=rs.MENU_ROWS)
FOUND_KW = dict(rs._found, sub="celebrate")
HOT_KW = rs.hunt(3, intensity=0.9, glow_r_px=56, dist_band="<3")


def _warm(**kw):
    return rs.hunt(2, dist_band="~10", **kw)


def _fr(r, p, cap=None, now=None):
    """Frame on the simulated clock (default p.t_ms), not ticks_ms()."""
    return r.frame(p, cap, p.t_ms if now is None else now)


def _run(r, cap, kw, ms, t0=T0, step=50):
    """Frames every ``step`` ms for ``ms`` from tick ``t0`` (wrap-aware, like
    the device clock); returns [(offset_ms, event)]."""
    evs = []
    t = 0
    while t <= ms:
        for e in _fr(r, make_params(t_ms=ticks_add(t0, t), **kw), cap):
            evs.append((t, e))
        t += step
    return evs


def _px(buf, x, y):
    i = (y * 240 + x) * 2
    return buf[i] | (buf[i + 1] << 8)       # framebuf's native (swapped) int


def _ring_index(x, y):
    """Reference float formula for the ring-index map."""
    return min(169, int(math.floor(math.sqrt((x - 119.5) ** 2 + (y - 119.5) ** 2))))


# ---- pure maths (CPython + MicroPython) ----------------------------------------
def test_lut_is_the_generated_ramp_lut():
    for name in T.RAMP_NAMES:
        lut = fld.LUTS[name]
        assert tuple(lut.c) == T.RAMP_LUT[name], name
        # r/g/b are the 565 value bit-replicated to 8 bits (crossfade mixing)
        for j in range(64):
            n = swap16(lut.c[j])
            r5, g6, b5 = n >> 11, (n >> 5) & 63, n & 31
            assert (lut.r[j], lut.g[j], lut.b[j]) == ((r5 << 3) | (r5 >> 2), (g6 << 2) | (g6 >> 4),
                                                      (b5 << 3) | (b5 >> 2)), (name, j)


def test_kernel_literals_match_tuning():
    # the palette kernel source keeps a few tokens as literals (viper speed)
    assert fld.q8(T.STANDING_BASE) == 512 and fld.q8(T.STANDING_AMP) == 640
    assert "st = 512 + ((((640 * tp[426 + (i & 31)]" in fld._KSRC
    assert T.STANDING_WAVELENGTH_PX == len(fld.COS32) == 32
    assert T.CORE_DOT_R == 6 and "if i <= 6:" in fld._KSRC


def test_ring_map_matches_hypot():
    half = fld.build_map(120)
    for (x, y) in ((0, 0), (119, 119), (120, 120), (239, 0), (17, 93), (200, 60), (120, 0)):
        yy = y if y < 120 else 239 - y
        assert half[yy * 240 + x] == _ring_index(x, y), (x, y)
    assert max(half) == 168          # corner: 119.5*sqrt(2) = 168.998
    full = fld.build_map(240)
    assert full[239 * 240 + 239] == 168 and full[130 * 240 + 7] == _ring_index(7, 130)


def _field(levels=(768, 0, 1024, 20 * 256), iris=0):
    f = fld.RippleField()
    f.set_levels(0, levels[0], levels[1], levels[2], levels[3], False)
    f.set_iris(0, iris)
    f.set_iris(1000, iris)
    f.started = True
    return f


def test_palette_entries_are_lut_colours():
    f = _field()                       # floor = level 3, no glow, no rings
    f.build(0)
    assert f.pal_arr[10] == fld.LUTS["green"].c[27] == PROX[3]
    # vignette dims the edge: idx 168 -> 0.15 * 3 = 0.45 -> LUT[4]
    assert f.pal_arr[168] == fld.LUTS["green"].c[(((768 * fld.VIG[168]) >> 8) * 9 + 128) >> 8]
    lutset = set(fld.LUTS["green"].c)
    for i in range(170):
        assert f.pal_arr[i] in lutset


def test_core_dot_and_iris_in_palette():
    f = _field(levels=(77, 0, 1024, 20 * 256))
    f.build(0, core=CORE)
    for i in range(7):
        assert f.pal_arr[i] == PROX[6], i          # sun floor: level 6
    assert f.pal_arr[7] != PROX[6]
    f = _field(iris=64)
    f.build(0, core=CORE)
    for i in range(64):
        assert f.pal_arr[i] == BG_IRIS
    assert f.pal_arr[64] == fld.LUTS["green"].c[45]   # rim >= level 5


def test_ring_moves_by_speed_times_dt():
    f = _field()
    f.t = 1000
    k = f.spawn(1000, 44 << 8, 80, 1024, 3, 18, False)
    for dt in (0, 50, 125, 1000):
        assert f.ring_r(k, 1000 + dt) == (44 << 8) + (80 * dt * 256) // 1000, dt
    k2 = f.spawn(1000, 168 << 8, -30, 1024, 3, 22, False)
    assert f.ring_r(k2, 2000) == (138 << 8)


def test_temporal_aa_widens_lead():
    f = _field(levels=(0, 0, 1024, 20 * 256))
    f.t = 0
    f.spawn(-1000, 0, 120, 1792, 3, 14, False)    # ring at r=120
    f.dt = 50                                     # 20 fps -> lead_eff = 9 px
    f.build(0)
    # 6 px ahead of the crest is still lit with lead_eff 9, dark with lead 3
    assert f.pal_arr[126] != f.pal_arr[140]
    f.dt = 5                                      # 200 fps -> lead 3 px
    f.spawn(-1000, 0, 120, 1792, 3, 14, False)
    f.build(0)
    assert f.pal_arr[126] == f.pal_arr[140]


def test_png_roundtrip():
    from tools import png
    rgb = bytearray(4 * 3 * 3)
    for i in range(len(rgb)):
        rgb[i] = (i * 37) & 255
    w, h, out = png.decode_rgb(png.encode(4, 3, rgb))
    assert (w, h) == (4, 3) and out == bytes(rgb)


def test_snapshot_tool_fails_loudly():
    # CPython entry point: a MicroPython error or an unknown fixture name
    # must surface (stderr, exit 1), not print "wrote 0 snapshots" and exit 0
    if sys.implementation.name != "cpython":
        raise Skip("the snapshot tool's CPython entry point")
    import subprocess

    class Proc:
        def __init__(self, rc, out, err):
            self.returncode, self.stdout, self.stderr = rc, out, err

    import io
    run, out, err = subprocess.run, sys.stdout, sys.stderr
    try:
        for proc in (Proc(1, "", "MicroPython traceback\n"), Proc(0, "", "")):
            subprocess.run = lambda *a, **k: proc
            sys.stdout = io.StringIO()
            sys.stderr = io.StringIO()
            try:
                rs._cpython_main(["render_snapshots.py", "no_such_fixture"])
                assert False, "no exit"
            except SystemExit as e:
                assert e.code == 1, e.code
            msg = sys.stderr.getvalue()
            assert ("MicroPython traceback" if proc.returncode else "no_such_fixture") in msg, msg
    finally:
        subprocess.run, sys.stdout, sys.stderr = run, out, err


# ---- frame tests (MicroPython framebuf) ------------------------------------------
def test_blit_key_semantics_probe():
    # ui/text.py relies on MicroPython comparing the blit key after the
    # palette lookup: a source 0 mapped to the key colour stays transparent
    _need_fb()
    import framebuf
    src = framebuf.FrameBuffer(bytearray(1), 8, 1, framebuf.MONO_HLSB)   # all 0
    pal = framebuf.FrameBuffer(bytearray(4), 2, 1, framebuf.RGB565)
    pal.pixel(0, 0, 0x1234)
    dst = framebuf.FrameBuffer(bytearray(2), 1, 1, framebuf.RGB565)
    dst.pixel(0, 0, 0x5555)
    dst.blit(src, 0, 0, 0x1234, pal)
    assert dst.pixel(0, 0) == 0x5555


def test_renderer_tables_match_tuning():
    _need_fb()
    from finder import game
    from ui import renderer as R
    # the const ids are the index in the §3 lists (FAR..HOT ranges rely on the order)
    for name, sid in (("PAIRING", R.S_PAIRING), ("SEARCHING", R.S_SEARCHING), ("FAR", R.S_FAR),
                      ("HOT", R.S_HOT), ("FOUND", R.S_FOUND), ("SCANNING", R.S_SCANNING),
                      ("LINK_LOST", R.S_LINK_LOST), ("MENU", R.S_MENU)):
        assert R.SCREENS[name] == sid == T.SCREENS.index(name), name
    for name, gid in (("glow", R.G_GLOW), ("seeker", R.G_SEEKER), ("chevrons", R.G_CHEV),
                      ("arrow", R.G_ARROW), ("countdown", R.G_COUNT), ("turn", R.G_TURN),
                      ("check", R.G_CHECK), ("runes", R.G_RUNES), ("battery", R.G_BATT)):
        assert R.GLYPHS[name] == gid, name
    assert R.G_DOTS == len(T.GLYPHS)
    # copy the renderer keys on that lives in finder/game.py
    assert (R.W_FOUND, R.W_BUMP) == (game.W_FOUND, game.W_BUMP)


def test_frame_pushes_four_bands_and_colours():
    _need_fb()
    r = Renderer()
    cap = FrameCapture()
    _run(r, cap, _warm(glyph="arrow", arrow_deg=0, cone_deg=31, arrow_style="solid_b"), 500)
    n0 = cap.pushes
    _fr(r, make_params(t_ms=T0 + 550, **_warm(glyph="arrow", arrow_deg=0, cone_deg=31,
                                                arrow_style="solid_b")), cap)
    assert cap.pushes - n0 == R.NB == 4
    assert _px(cap.buf, 160, 160) == BG_IRIS            # inside iris r 64, off the dart
    assert _px(cap.buf, 120, 90) == PROX[6]             # solid_b dart body
    assert _px(cap.buf, 139, 68) == PROX[3]             # solid_b beam, beside the tip
    # every field pixel is the palette colour of its ring index
    pal = r.field.pal_arr
    for (x, y) in ((5, 120), (230, 60), (119, 3), (40, 200)):
        assert _px(cap.buf, x, y) == pal[_ring_index(x, y)], (x, y)


def test_core_dot_level_in_frame():
    _need_fb()
    r = Renderer()
    cap = FrameCapture()
    _run(r, cap, dict(screen="FAR", zone=0, intensity=0.0, glow_r_px=20), 300)
    lut = fld.LUTS["green"].c
    for (x, y) in ((120, 120), (116, 120), (120, 124)):
        # level 6 floor, or higher where a crest just spawned (core lift)
        assert _px(cap.buf, x, y) in list(lut)[54:], (x, y)
    assert r.field.iris == 0


def test_field_bands_match_the_full_map():
    """A field-only frame, drawn band by band, equals the full ring map
    palette-blitted in one go."""
    _need_fb()
    r = Renderer()
    cap = FrameCapture()
    _run(r, cap, rs.hunt(2, status=None), 700)
    ref = bytearray(240 * 240 * 2)
    full = framebuf.FrameBuffer(fld.build_map(240), 240, 240, framebuf.GS8)
    framebuf.FrameBuffer(ref, 240, 240, framebuf.RGB565).blit(full, 0, 0, -1, r.field.pal)
    assert cap.buf == ref
    # The overlays are not in this frame: the snapshot CRCs
    # (test_fixtures_match_snapshots) catch overlays drawn in the wrong place.


def test_bands_go_top_to_bottom_after_the_whole_frame_is_drawn():
    """The frame is drawn whole (field band by band, then the overlays, with
    ``service`` after each), then pushed top to bottom (one panel window),
    each band a view of the one frame buffer."""
    _need_fb()
    r = Renderer()
    seen = []

    class D:
        def push_strip(self, y0, h, buf):
            seen.append((y0, h, buf is r.bands[y0 // h]))

        def service(self):
            seen.append("svc")

    _run(r, D(), rs.hunt(2, status=None), 0)
    assert seen == ["svc"] * 5 + [(60 * k, 60, True) for k in range(4)], seen
    assert len(r.buf) == 240 * 240 * 2


class _P16:
    """ptr16 stand-in over a bytearray (little-endian, as on the ESP32)."""

    def __init__(self, b):
        self.b = b

    def __getitem__(self, i):
        return self.b[2 * i] | self.b[2 * i + 1] << 8

    def __setitem__(self, i, v):
        self.b[2 * i] = v & 255
        self.b[2 * i + 1] = (v >> 8) & 255


class _P32:
    """ptr32 stand-in over a bytearray (little-endian, as on the ESP32)."""

    def __init__(self, b):
        self.b = b

    def __getitem__(self, i):
        b = self.b
        j = 4 * i
        return b[j] | b[j + 1] << 8 | b[j + 2] << 16 | b[j + 3] << 24

    def __setitem__(self, i, v):
        b = self.b
        j = 4 * i
        b[j] = v & 255
        b[j + 1] = (v >> 8) & 255
        b[j + 2] = (v >> 16) & 255
        b[j + 3] = (v >> 24) & 255


def _py_blit_kernel(src=None):
    """ui.field's blit kernel source (or ``src``) as plain Python (viper is
    device-only)."""
    ns = {"ptr16": lambda b: b if isinstance(b, array.array) else _P16(b),
          "ptr32": _P32}
    exec(fld._BSRC if src is None else src, ns)
    return ns["blit_kernel"]


def test_blit_kernel_matches_the_framebuf_path():
    """The viper strip blit, run as Python, passes RingMap's self-check and
    draws the same frame from the map's quadrant; kernels that read the
    wrong rows, swap a pixel pair, skip a row or mirror the left half
    without reversing it are refused."""
    _need_fb()
    k = _py_blit_kernel()
    r = Renderer()
    assert r.map.kind in ("framebuf", "viper"), r.map.kind
    r.map = fld.RingMap(R.BH, r.bands, kernel=k)
    assert r.map.kind == "kernel" and r.map.kern is k
    assert len(r.map.q) == 120 * 120
    cap = FrameCapture()
    _run(r, cap, rs.hunt(2, status=None), 300)
    ref = bytearray(240 * 240 * 2)
    full = framebuf.FrameBuffer(fld.build_map(240), 240, 240, framebuf.GS8)
    framebuf.FrameBuffer(ref, 240, 240, framebuf.RGB565).blit(full, 0, 0, -1, r.field.pal)
    assert cap.buf == ref

    def mirrored(dst, q, pal, y0, h):         # a bottom band from its top mirror, not reversed
        k(dst, q, pal, y0 if y0 < 120 else 240 - h - y0, h)

    def swapped(dst, q, pal, y0, h):
        k(dst, q, pal, y0, h)
        for i in range(0, len(dst), 4):
            a = dst[i:i + 2]
            dst[i:i + 2] = dst[i + 2:i + 4]
            dst[i + 2:i + 4] = a

    def short(dst, q, pal, y0, h):
        k(dst, q, pal, y0, h - 1)

    src = fld._BSRC.replace("dp[lt] = c3 | (c2 << 16)", "dp[lt] = c2 | (c3 << 16)")
    assert src != fld._BSRC
    unreversed = _py_blit_kernel(src)          # left half: one pixel pair in the wrong order

    for bad in (mirrored, swapped, short, unreversed):
        m = fld.RingMap(R.BH, r.bands, kernel=bad)
        assert m.kern is None and m.q is None and "failed" in m.kind, m.kind


def _crest(r):
    """Outermost ring index (>= 40) of the brightest palette level."""
    lut = list(fld.LUTS["green"].c)
    best = -1
    at = -1
    for i in range(40, 170):
        j = lut.index(r.field.pal_arr[i])
        if j >= best:
            best = j
            at = i
    return at


def test_ring_crest_travels_at_speed():
    _need_fb()
    r = Renderer()
    cap = FrameCapture()
    kw = dict(screen="NEAR", zone=1, intensity=0.0, speed_px_s=80, pulse_period_ms=4000,
              glow_r_px=8, glyph="glow")
    _run(r, cap, kw, 900)                 # ring spawned at t=0 is at ~72 px
    c0 = _crest(r)
    _run(r, cap, kw, 450, t0=T0 + 950)    # 500 ms later at 80 px/s: +40 px
    c1 = _crest(r)
    assert 36 <= c1 - c0 <= 44, (c0, c1)


def test_heartbeat_on_live_spawns_only():
    _need_fb()
    r = Renderer()
    cap = FrameCapture()
    kw = _warm(pulse_period_ms=500, heartbeat="TICK", heartbeat_every=1)
    ev = _run(r, cap, kw, 2000)
    ticks = [t for t, e in ev if e == "TICK"]
    assert ticks == [0, 500, 1000, 1500, 2000]
    r.reset()
    ev = _run(r, cap, _warm(pulse_period_ms=500, heartbeat="TICK", ring_live=False), 2000)
    assert ev == []                           # ghost rings: no haptic
    assert r.field.ghosts == 1
    r.reset()                                 # LINK-LOST: no packets, yet its inward
    ev = _run(r, cap, rs._lost, 2000)         # rings listen (§4 rule 4): never ghosts
    assert ev == [] and r.field.ghosts == 0 and any(r.field.r_on)
    r.reset()
    ev = _run(r, cap, _warm(pulse_period_ms=500, heartbeat="TICK", heartbeat_every=2), 2000)
    assert len(ev) == 2                       # FAR-style: every 2nd ring
    r.reset()                                 # screen off: state advances, no display
    assert len(_run(r, None, kw, 2000)) == 5


def test_burst_once_per_params_and_no_event_echo():
    _need_fb()
    r = Renderer()
    cap = FrameCapture()
    p = make_params(t_ms=T0, **_warm(burst=True, haptic="CLOSER", heartbeat=None))
    assert _fr(r, p, cap) == ()               # params.haptic is the caller's to play
    n_on = sum(r.field.r_on)
    assert _fr(r, p, cap, T0 + 50) == ()      # same params: no second burst
    assert sum(r.field.r_on) == n_on
    bursts = [k for k in range(fld.MAXR) if r.field.r_on[k] and r.field.r_amp[k] == 1792]
    assert len(bursts) == 1 and r.field.r_v[bursts[0]] == 160


def test_menu_freezes_rings_and_dims():
    _need_fb()
    r = Renderer()
    cap = FrameCapture()
    _run(r, cap, _warm(), 600)
    _run(r, cap, MENU_KW, 700, t0=T0 + 650)
    rs1 = [r.field.ring_r(k, T0 + 1350) for k in range(fld.MAXR) if r.field.r_on[k]]
    _run(r, cap, MENU_KW, 500, t0=T0 + 1400)
    rs2 = [r.field.ring_r(k, T0 + 1900) for k in range(fld.MAXR) if r.field.r_on[k]]
    assert rs1 == rs2 and rs1
    assert r.field.dim == 128


def test_menu_freezes_hue_crossfade():
    # ui-spec MENU: the field is frozen, so a hue crossfade running when the
    # menu opens (green -> grey on link loss) waits behind it
    _need_fb()
    r = Renderer()
    cap = FrameCapture()
    _run(r, cap, _warm(), 500)
    lost = dict(rs._lost, banner=None)
    _run(r, cap, lost, 300, t0=T0 + 550)                  # 1500 ms crossfade under way
    mix0 = list(r.field.mix.c)
    assert mix0 != list(fld.LUTS["grey"].c) and mix0 != list(fld.LUTS["green"].c)
    _run(r, cap, dict(MENU_KW, ramp="grey"), 2000, t0=T0 + 900)
    assert list(r.field.mix.c) == mix0
    _run(r, cap, lost, 1500, t0=T0 + 2950)
    assert list(r.field.mix.c) == list(fld.LUTS["grey"].c)


def test_ramp_crossfade_to_gold():
    _need_fb()
    r = Renderer()
    cap = FrameCapture()
    _run(r, cap, _warm(), 200)
    _run(r, cap, FOUND_KW, 200, t0=T0 + 250)
    mid = list(r.field.mix.c)
    assert mid != list(fld.LUTS["gold"].c) and mid != list(fld.LUTS["green"].c)
    _run(r, cap, FOUND_KW, 300, t0=T0 + 500)     # 400 ms crossfade into FOUND
    assert list(r.field.mix.c) == list(fld.LUTS["gold"].c)


def test_renders_finder_render_params():
    _need_fb()
    from finder import render_params as rp
    r = Renderer()
    cap = FrameCapture()
    p = rp.make_params(t_ms=T0)                       # a valid SEARCHING frame
    assert rp.validate(p) == []
    assert _fr(r, p, cap) == ()
    assert cap.pushes == 4
    hot = dict(HOT_KW, ramp="green", ring_live=True, glyph="glow", word="BUMP!",
               status=(55, None, 4, True, False))
    assert rp.validate(rp.make_params(t_ms=T0, **hot)) == []
    for k in range(1, 33):          # iris 44 -> 0 (300 ms), grey -> green (1500 ms)
        _fr(r, rp.make_params(t_ms=T0 + 50 * k, **hot), cap)
    assert r.field.iris == 0
    assert _px(cap.buf, 120, 120) in (PROX[6], PROX[7])


def test_rings_interpolate_between_10hz_params():
    # §3/§4 rule 3: logic at 10 Hz, render at 20 fps. Each params object is
    # drawn twice; the render clock (now), not p.t_ms, must move the rings.
    _need_fb()
    r = Renderer()
    cap = FrameCapture()
    f = r.field
    p = None
    prev = None
    steps = 0
    for k in range(40):
        if k % 2 == 0:
            p = make_params(t_ms=T0 + 50 * k, **HOT_KW)
        now = T0 + 50 * k
        r.frame(p, cap, now)
        assert f.t == now
        rad = [f.ring_r(i, f.t) if f.r_on[i] and not f.r_ghost[i] else None
               for i in range(len(f.r_on))]
        if prev is not None and k >= 10:
            for i in range(len(rad)):
                if rad[i] is not None and prev[i] is not None and f.r_t0[i] == t0s[i]:
                    d = rad[i] - prev[i]              # Q8 px per 50 ms frame
                    assert 5 * 256 <= d <= 7 * 256, (k, i, d)
                    steps += 1
        prev = rad
        t0s = list(f.r_t0)
    assert steps > 20, steps
    assert 45 <= f.dt <= 55, f.dt                     # frame EMA sees 50 ms


def test_wedge_glides_between_10hz_params():
    # §3: the renderer interpolates; the sweep wedge turns linearly at
    # 30 deg/s instead of jumping 3 deg every second frame
    _need_fb()
    from ui import glyphs as gl
    cap = FrameCapture()

    def wedge(deg):
        gl.prep_wedge(deg)
        return list(gl._wedge)

    r = Renderer()
    sw = dict(rs._scan, sub="sweep", glyph="turn", glow_r_px=12)
    for k in range(4):                        # params at 10 Hz, frames at 20 fps
        p = make_params(t_ms=T0 + 100 * k, sweep=(90.0 + 3 * k, rs.BINS, 3, False), **sw)
        a = 90 + 3 * (k - 1) if k else 90         # glides over one tick: 100 ms behind
        _fr(r, p, cap, T0 + 100 * k)
        assert list(gl._wedge) == wedge(a), k
        _fr(r, p, cap, T0 + 100 * k + 50)
        assert list(gl._wedge) == wedge(a + 1 if k else a), k     # 1.5 deg in
    # paused: holds at the logic's angle
    p = make_params(t_ms=T0 + 400, sweep=(102.0, rs.BINS, 3, True), **sw)
    _fr(r, p, cap, T0 + 450)
    assert list(gl._wedge) == wedge(102)
    # the DIRECTION pacer turning left crosses 0 the short way
    r = Renderer()
    _fr(r, make_params(t_ms=T0, **_turn(2)), cap)
    _fr(r, make_params(t_ms=T0 + 100, **_turn(359)), cap, T0 + 100)
    _fr(r, make_params(t_ms=T0 + 100, **_turn(359)), cap, T0 + 150)
    assert list(gl._wedge) == wedge(0)


def test_frame_default_clock_is_ticks_ms():
    _need_fb()
    from finder.compat import ticks_diff, ticks_ms
    r = Renderer()
    p = make_params(t_ms=ticks_add(ticks_ms(), 500000), **_warm())
    r.frame(p)
    r.frame(p)                                        # same params, clock moves on
    assert 0 <= ticks_diff(ticks_ms(), r.field.t) < 5000, r.field.t


def test_text_cache_reuse():
    _need_fb()
    tc = tx.TextCache()
    a = tc.get(font.WORD, "~10")
    b = tc.get(font.WORD, "~10")
    assert a is b and tc.misses == 1
    assert a[1] == 48 and a[2] == 32
    assert tc.get(font.LABEL, "~10") is not a and tc.misses == 2
    assert tc.pal(PROX[6]) is tc.pal(PROX[6])
    r = Renderer()
    cap = FrameCapture()
    _run(r, cap, _warm(top_text="TAP TO SCAN"), 200)
    m = r.tc.misses
    _run(r, cap, _warm(top_text="TAP TO SCAN"), 500, t0=T0 + 250)
    assert r.tc.misses == m


def test_font_scaling_shapes():
    _need_fb()
    buf, w, h = font.render(font.DISPLAY, "1")
    assert (w, h) == (24, 48) and len(buf) == 3 * 48
    rows = font.char_rows("1")
    # every source pixel becomes a solid 3x6 block
    for r in range(8):
        for c in range(8):
            on = bool(rows[r] & (0x80 >> c))
            x = c * 3 + 1
            y = r * 6 + 2
            got = bool(buf[y * 3 + x // 8] & (0x80 >> (x % 8)))
            assert got == on, (r, c)


# ---- review round 1 regressions --------------------------------------------------
def test_viper_self_check_covers_branches():
    # a kernel that differs in any one branch (FOUND standing wave, fill,
    # core dot, ghost channel) must fail the self-check
    ns = {"ptr16": lambda x: x, "ptr32": lambda x: x}
    py = {"ptr16": lambda x: x, "ptr32": lambda x: x}
    exec(fld._KSRC, py)
    assert fld.kernel_agrees(py["pal_kernel"], py["pal_kernel"])
    for a, b in (("tp[426 + (i & 31)]", "tp[426 + (i & 15)]"), ("* sb) >> 8)", "* sb) >> 7)"),
                 ("* sw) >> 8", "* sw) >> 7"), ("fill_v + (fill_v >> 1)", "fill_v + (fill_v >> 2)"),
                 ("if i <= 6:", "if i <= 5:"), ("g = (g * dim) >> 8", "g = (g * dim) >> 7"),
                 ("c = tp[522 + j]", "c = tp[521 + j]"),
                 ("j = ((g * 9 + 128) >> 8) + lift", "j = ((g * 9 + 120) >> 8) + lift"),
                 ("j = ((g * 9 + 128) >> 8) + lift", "j = (g * 9 + 128) >> 8")):
        src = fld._KSRC.replace(a, b)
        assert src != fld._KSRC, a
        exec(src, ns)
        assert not fld.kernel_agrees(py["pal_kernel"], ns["pal_kernel"]), b
    assert any(0 < v[fld.P_STAND_W] < 256 and v[fld.P_RIM] >= 0 for v in fld.CHECK_PRMS)


def _ticks_ok(r):
    f = r.field
    vals = [f.next_spawn, f.last_spawn, f.xf_t0, f.iris_t0, f.ramp_t0, f.stand_t0] + \
        [f.r_t0[k] for k in range(fld.MAXR) if f.r_on[k]]
    return all(0 <= v < (1 << 30) for v in vals), vals


def test_rings_and_heartbeats_steady_across_ticks_wrap():
    _need_fb()
    r = Renderer()
    kw = _warm(pulse_period_ms=500, heartbeat="TICK", heartbeat_every=1)
    ev = _run(r, None, kw, 10000, t0=(1 << 30) - 5000)       # 10 s across the wrap
    ticks = [t for t, e in ev if e == "TICK"]
    assert len(ticks) == 21, ticks                           # every 500 ms, no stall
    ok, vals = _ticks_ok(r)
    assert ok, vals
    # MENU hold shifts ring clocks across the wrap too
    _run(r, None, dict(MENU_KW, pulse_period_ms=500), 950, t0=(1 << 30) - 300)
    ok, vals = _ticks_ok(r)
    assert ok, vals
    ev = _run(r, None, kw, 2000, t0=1500)
    assert len([1 for t, e in ev if e == "TICK"]) >= 4


def test_no_phantom_arrow_or_dark_field_at_high_ticks():
    _need_fb()
    cap = FrameCapture()
    r = Renderer()
    _run(r, cap, _warm(), 2950, t0=(1 << 29) + 5000)
    assert not r.arrow
    f = r.field
    assert f.fl > 77 and f.gl > 512 and f.pu > 1024, (f.fl, f.gl, f.pu)
    # a stale expire timestamp 2^29 ms old must not revive the dart
    r = Renderer()
    arrow = _warm(glyph="arrow", arrow_deg=40, cone_deg=31, arrow_style="solid_b")
    _run(r, cap, arrow, 450, t0=1000)
    _run(r, cap, _warm(), 950, t0=1500)
    assert not r.arrow
    _run(r, cap, _warm(), 200, t0=ticks_add(ticks_add(1500, 1 << 28), (1 << 28) + 100))
    assert not r.arrow


def test_arrow_expire_shrink_only_on_same_zone_screen():
    _need_fb()
    cap = FrameCapture()
    arrow = dict(glyph="arrow", arrow_deg=0, cone_deg=20, arrow_style="solid_a")
    r = Renderer()
    _run(r, cap, _warm(screen="HOT", zone=3, **arrow), 1000)
    _fr(r, make_params(t_ms=T0 + 1050, **FOUND_KW), cap)
    assert not r.arrow
    from ui import ACC_FOUND
    assert _px(cap.buf, 120, 84) == ACC_FOUND
    # tap-to-rescan: the countdown is visible at once
    r = Renderer()
    _run(r, cap, _warm(**arrow), 1000)
    _fr(r, make_params(t_ms=T0 + 1050, screen="SCANNING", sub="ready", glyph="countdown",
                        countdown=3, intensity=0.55, speed_px_s=80,
                        pulse_period_ms=1000), cap)
    assert not r.arrow
    # a real expire (same zone screen, arrow dropped) still shrinks over 600 ms
    r = Renderer()
    _run(r, cap, _warm(**arrow), 1000)
    _fr(r, make_params(t_ms=T0 + 1050, **_warm()), cap)
    assert r.arrow
    _run(r, cap, _warm(), 700, t0=T0 + 1100)
    assert not r.arrow


def test_wake_shows_current_state_without_intro():
    # §8: the first frame after the screen was off is the current state. An
    # arrow dropped while off stays gone (no expire shrink after the gap,
    # §12), one re-aimed while off sits at its new angle at full size, and a
    # banner raised while off is at rest.
    _need_fb()
    cap = FrameCapture()
    walk = dict(sub="walk", glyph="arrow", cone_deg=31, arrow_style="solid_b")
    r = Renderer()
    _run(r, cap, _warm(arrow_deg=90, **walk), 1000)
    assert _px(cap.buf, 155, 120) == PROX[6]               # the dart, pointing right
    _run(r, None, _warm(arrow_deg=90, **walk), 1950, t0=T0 + 1050)
    _run(r, None, _warm(), 30000, t0=T0 + 3050)
    _fr(r, make_params(t_ms=T0 + 33100, **_warm()), cap)
    assert not r.arrow
    assert _px(cap.buf, 155, 120) == r.field.pal_arr[_ring_index(155, 120)]   # field, no dart
    # at ticks >= 2^29 the banner clock's reset value 0 is in the future:
    # the wake must still put a banner raised while dark at rest
    r = Renderer()
    t1 = (1 << 29) + 5000
    _run(r, cap, _warm(arrow_deg=0, **walk), 1000, t0=t1)
    _run(r, None, _warm(arrow_deg=90, banner=("SCAN AGAIN", "info", False), **walk),
         1000, t0=ticks_add(t1, 1050))
    _fr(r, make_params(t_ms=ticks_add(t1, 2100),
                       **_warm(arrow_deg=90, banner=("SCAN AGAIN", "info", False), **walk)), cap)
    assert r.arrow and not r._a_grow and r._a_q == 90 * 16
    assert _px(cap.buf, 155, 120) == PROX[6]
    assert r.bot_dy == 0


def test_toast_falls_out():
    # tokens motion toast_out: 150 ms in_cubic, then the word/readout returns
    _need_fb()
    from ui.renderer import B_READOUT, B_TOAST
    cap = FrameCapture()
    r = Renderer()
    _run(r, cap, _warm(banner=("BACK IN RANGE", "info", False)), 500)
    dys = []
    for k in range(4):
        _fr(r, make_params(t_ms=T0 + 550 + 50 * k, **_warm()), cap)
        dys.append(r.bot_dy if r.bot == B_TOAST else None)
    assert dys[0] == 0 and dys[2] > 0 and dys[3] is None, dys
    assert r.bot == B_READOUT
    # a new banner during the exit rises in again
    _fr(r, make_params(t_ms=T0 + 800, **_warm(banner=("SCAN AGAIN", "info", False))), cap)
    _fr(r, make_params(t_ms=T0 + 850, **_warm()), cap)
    _fr(r, make_params(t_ms=T0 + 900, **_warm(banner=("SCAN AGAIN", "info", False))), cap)
    assert r.bot == B_TOAST and r.bot_dy == 12


def _level_probe():
    """Wrap the palette kernel so each build also records the area-weighted
    mean field level (ramp steps): the same kernel run on copies with
    identity LUTs, the iris as level 0 and the rim at its ramp level."""
    m = fld.build_map(240)
    wts = [0] * 170
    for b in m:
        wts[b] += 1
    orig = fld.pal_kernel
    means = []

    def probe(pal, acc, tab, prm):
        tid = array.array("H", tab)
        for j in range(64):
            tid[fld.T_LUT + j] = j
            tid[fld.T_GLUT + j] = j
        q = array.array("i", prm)
        q[fld.P_IRISC] = 0
        q[fld.P_RIM] = -1
        lv = array.array("H", [0] * 256)
        orig(lv, array.array("i", acc), tid, q)
        means.append(sum(wts[i] * lv[i] for i in range(170)) / (9.0 * 240 * 240))
        orig(pal, acc, tab, prm)

    return probe, orig, means


def test_flash_limit_across_transitions():
    # §4 / §11: no full-field change > 2 ramp steps in 333 ms. The calibrate
    # fill and the FOUND standing wave used to switch off in one frame.
    _need_fb()
    cal = rs._cal
    split = dict(rs._pair, sub="split", glyph="countdown", countdown=24, top_text="NO PEEKING",
                 word="SPLIT UP")
    hot_split = dict(split, zone=3, intensity=0.9, speed_px_s=120, pulse_period_ms=500,
                     wavelength_px=None, glow_r_px=56)
    probe, orig, means = _level_probe()
    fld.pal_kernel = probe
    try:
        for phases in (
                [(0, dict(cal, countdown=3)), (1000, dict(cal, countdown=2)),
                 (2000, dict(cal, countdown=1)), (3000, split)],
                [(0, HOT_KW), (1000, dict(FOUND_KW, burst=True)), (1050, FOUND_KW),
                 (4000, hot_split)],
                [(0, FOUND_KW), (3000, split)]):
            del means[:]
            r = Renderer()
            cap = FrameCapture()
            for k in range(len(phases)):
                end = phases[k + 1][0] if k + 1 < len(phases) else phases[k][0] + 1500
                t = phases[k][0]
                while t < end:
                    _fr(r, make_params(t_ms=T0 + t, **phases[k][1]), cap)
                    t += 50
            for i in range(len(means)):
                for j in range(i + 1, min(i + 7, len(means))):      # windows <= 300 ms
                    assert abs(means[j] - means[i]) <= 2.0, (phases[-1][0], i, j, means[i], means[j])
    finally:
        fld.pal_kernel = orig


def test_calibrate_fill_follows_countdown_not_clock():
    _need_fb()
    cap = FrameCapture()
    r = Renderer()
    cal = dict(rs._cal, countdown=3)
    _run(r, cap, cal, 500)
    fr = r.field.prm[fld.P_FILL_R]
    assert 78 <= fr <= 82, fr                        # ~500 ms of 3000
    cal["top_text"] = "HOLD STILL"                   # fill paused
    _run(r, cap, cal, 4000, t0=T0 + 550)
    assert r.field.prm[fld.P_FILL_R] == fr
    cal["top_text"] = "STAND 1 STEP APART"           # digit 3 caps at 1 s
    _run(r, cap, cal, 3000, t0=T0 + 4600)
    assert r.field.prm[fld.P_FILL_R] <= 64 + 104 // 3 + 1
    for k, cd in enumerate((2, 1)):
        cal["countdown"] = cd
        _run(r, cap, cal, 950, t0=T0 + 7650 + 1000 * k)
    assert r.field.prm[fld.P_FILL_R] >= 166
    # another calibrate.window_ms: digits n..1 (Calibrator.digit), the fill only grows
    from ui import renderer as R
    saved = R.CAL_MS, R.CAL_N
    R.CAL_MS, R.CAL_N = 5000, 5
    try:
        r.reset()
        fr = []
        for k in range(5):
            cal["countdown"] = 5 - k
            _run(r, cap, cal, 950, t0=T0 + 1000 * k)
            fr.append(r.field.prm[fld.P_FILL_R])
    finally:
        R.CAL_MS, R.CAL_N = saved
    assert fr == sorted(fr) and fr[-1] >= 166, fr


def test_unreliable_status_bars_warn():
    _need_fb()
    from ui import TEXT_SEC, WARN
    cap = FrameCapture()
    r = Renderer()
    # §5.5: while unreliable the pinned strip shows the link bars in status.warn
    _fr(r, make_params(t_ms=T0, **_warm(status=(80, 80, 3, True, False))), cap)
    assert _px(cap.buf, 110, 27) == TEXT_SEC
    _fr(r, make_params(t_ms=T0 + 50, **_warm(status=(80, 80, 3, True, True))), cap)
    assert _px(cap.buf, 110, 27) == WARN


def test_hint_chip_outranks_pinned_status():
    _need_fb()
    from ui.renderer import T_CHIP, T_STATUS
    cap = FrameCapture()
    r = Renderer()
    _fr(r, make_params(t_ms=T0, **_warm(status=(15, 80, 4, True, False),
                                         top_text="TAP WATCHES")), cap)
    assert r.top == T_CHIP
    _fr(r, make_params(t_ms=T0 + 50, **dict(rs._lost, status=(15, 80, 0, True, False))), cap)
    assert r.top == T_STATUS                          # strip outranks LAST


def test_sticky_banner_rises_once():
    _need_fb()
    cap = FrameCapture()
    r = Renderer()
    dys = []
    for k in range(60):
        s = "LOST 0:%02d" % (k // 20)
        _fr(r, make_params(t_ms=T0 + 50 * k, **dict(rs._lost, banner=(s, "warn", True))), cap)
        dys.append(r.bot_dy)
    assert dys[0] == 12 and max(dys[5:]) == 0, dys
    # a different non-sticky toast is a new toast and rises again
    for k, s in enumerate(("SCAN AGAIN", "BACK IN RANGE")):
        _fr(r, make_params(t_ms=T0 + 3000 + 50 * k,
                           **dict(rs._lost, banner=(s, "info", False))), cap)
    assert r.bot_dy == 12


def test_scan_result_morphs_best_bin_into_dart():
    _need_fb()
    cap = FrameCapture()
    r = Renderer()
    bins = [0.2] * 12
    bins[3] = 1.0
    res = rs._result
    for k in range(25):                                  # 0..1200 ms
        e = 50 * k
        act = 3 if (e < 800 and not (e // 200) & 1) else None
        _fr(r, make_params(t_ms=T0 + e, sweep=(100.0, bins, act, False), **res), cap)
        if e == 700:
            assert not r.arrow and r.morph == -1
        if e == 900:
            assert r.arrow and r.morph == 3 and 0 < r.morph_e < 256
    assert r.morph == 3 and r.morph_e == 256
    # dart at theta 100 deg (bin 3): body right of centre, not the turn glyph
    assert _px(cap.buf, 150, 120) in (PROX[7], PROX[6]), hex(_px(cap.buf, 150, 120))
    # reveal: no second scale-in; the dart is already at theta
    _fr(r, make_params(t_ms=T0 + 1250, **_warm(sub="reveal", glyph="arrow", arrow_deg=100,
                                                cone_deg=31, arrow_style="solid_a")), cap)
    assert r.arrow and not r._a_grow
    assert r._a_q == 100 * 16


# ---- snapshot review round (visual defects) --------------------------------------
def _turn(pacer):
    # the halo is the live mirror: Game sends glow_r 12 in turn as in the sweep
    return _warm(sub="turn", glyph="arrow", arrow_deg=40, cone_deg=33, arrow_style="solid_b",
                 intensity=1.0, glow_r_px=12, word="TURN RIGHT", heartbeat=None,
                 sweep=(pacer, (None,) * 12, None, False))


def test_turn_halo_is_live_mirror_not_zone_glow():
    _need_fb()
    cap = FrameCapture()
    r = Renderer()
    _run(r, cap, _turn(60), 1000)
    assert r.field.gr == 12 * 256, r.field.gr
    assert r.field.gl == 256 + 5 * 256                  # glow_amp 1 + 5 I at I = 1
    # the pacer has a bg.base keyline: 1 px outside its r 110 edge at 60 deg
    assert _px(cap.buf, 120 + 96, 120 - 56) == 0, hex(_px(cap.buf, 216, 64))
    assert _px(cap.buf, 120 + 87, 120 - 50) == PROX[6]


def test_static_face_it_keeps_zone_glow():
    # static arrow mode: FACE IT is sub "turn" too but has no pacer sweep, so
    # its halo keeps the zone levels (§4 rule 1), not the live mirror
    _need_fb()
    cap = FrameCapture()
    r = Renderer()
    kw = _warm(sub="turn", glyph="arrow", arrow_deg=40, cone_deg=33, arrow_style="solid_b",
               word="FACE IT", heartbeat=None)
    _run(r, cap, kw, 1000)
    iq = int(make_params(**kw).intensity * 256)
    assert r.field.gl == fld.GL_A + ((fld.GL_B * iq) >> 8), r.field.gl


def test_turn_pacer_behind_yields_bottom_slot():
    _need_fb()
    from ui.renderer import B_NONE, B_WORD, T_NONE
    cap = FrameCapture()
    r = Renderer()
    _run(r, cap, _turn(60), 200)
    assert r.bot == B_WORD and r.top == T_NONE
    _run(r, cap, _turn(170), 200, t0=T0 + 250)
    assert r.bot == B_NONE
    assert _px(cap.buf, 120, 215) == PROX[6]            # the wedge, not the word pill


def test_readout_keeps_mark_slot_while_arrow_up():
    _need_fb()
    cap = FrameCapture()
    r = Renderer()
    walk = dict(sub="walk", glyph="arrow", arrow_deg=0, cone_deg=31, arrow_style="solid_b")
    _run(r, cap, _warm(trend=1, **walk), 400)
    a = bytes(cap.buf[196 * 480:216 * 480])
    _run(r, cap, _warm(trend=0, **walk), 200, t0=T0 + 450)
    assert r.bot_slot and r.bot_mark == 0
    b = bytes(cap.buf[196 * 480:216 * 480])
    # the numerals hold still: only the mark pixels differ
    x0 = 120 - tx.readout_width("~10", True) // 2 + 12 + 18
    for x in range(x0, x0 + 48, 3):
        for y in range(0, 20, 3):
            i = (y * 240 + x) * 2
            assert a[i:i + 2] == b[i:i + 2], (x, y)


def test_colder_chevrons_hollow_and_apart():
    from ui import glyphs as gl
    out = gl.CHEV_DN_OUT
    xs = [out[k] for k in range(0, len(out), 2)]
    ys = [out[k] for k in range(1, len(out), 2)]
    # bevelled: nothing sticks out past the +2 px band (was a 4 px mitre spike)
    assert max(xs) <= 26 and min(xs) >= -26 and min(ys) >= -16, (xs, ys)
    inn = gl.CHEV_DN_IN
    notch = min(inn[k + 1] for k in range(0, len(inn), 2) if inn[k] == 0) - 2
    # strong copies: the upper copy's apex clears the lower copy's notch
    assert 2 * gl.CHEV_DN_STRONG_DY - (max(ys) - notch) >= 3


def test_core_never_dimmer_than_new_crest():
    f = fld.RippleField()
    lut = fld.LUTS["green"].c
    lvl = {lut[j]: j for j in range(64)}
    iq = 140
    f.dt = 50
    f.set_levels(0, fld.FL_A + ((fld.FL_B * iq) >> 8), fld.GL_A + ((fld.GL_B * iq) >> 8),
                 fld.PU_A + ((fld.PU_B * iq) >> 8), 42 * 256, False)
    f.set_iris(0, 0)
    f.started = True
    f.spawn(0, 0, 80, f.pu, 3, 18, False)
    for t in (100, 150, 200):
        f.build(t, core=CORE)
        core = min(lvl[f.pal_arr[i]] for i in range(7))
        ring = max(lvl[f.pal_arr[i]] for i in range(7, 19))
        assert core >= ring, (t, core, ring)


def test_digit_one_flag_joins_stem():
    _need_fb()
    rows = font.char_rows("1")
    stem = rows[0]
    for r in (1, 2):                          # flag grows left from the stem top
        assert rows[r] & stem == stem and rows[r] & ~stem & 0xFF, r
    # the flag is a diagonal: each row's flag pixel sits left of the one below
    assert rows[1] & ~stem & 0xFF & (rows[2] << 1)


def test_menu_backplate_hides_frozen_rings():
    _need_fb()
    from ui import BG_BASE
    cap = FrameCapture()
    r = Renderer()
    _run(r, cap, _warm(), 600)
    _run(r, cap, MENU_KW, 300, t0=T0 + 650)
    for y in (73, 117, 161):                   # the 4 px gaps between rows
        for x in (40, 120, 200):
            assert _px(cap.buf, x, y) == BG_BASE, (x, y)


def test_menu_scroll_triangles_and_highlight():
    """ui-spec MENU: triangles at x 207-213 (up y 36-41, down y 195-200) only when
    rows are hidden that way; the PROX[5] border marks the visible index in ``sub``."""
    _need_fb()
    from ui import TEXT_SEC
    for sub, up, dn, k_sel in (("0v", False, True, 0), ("3^", True, False, 3),
                               ("2^v", True, True, 2), ("1", False, False, 1)):
        cap = FrameCapture()
        r = Renderer()
        _run(r, cap, dict(MENU_KW, sub=sub), 100)
        assert (_px(cap.buf, 210, 39) == TEXT_SEC) == up, sub
        assert (_px(cap.buf, 210, 197) == TEXT_SEC) == dn, sub
        for k in range(4):
            y = T.MENU_ROWS_Y[k] + 20                  # row's left border, mid-height
            assert (_px(cap.buf, 24, y) == PROX[5]) == (k == k_sel), (sub, k, hex(_px(cap.buf, 24, y)))


def test_scan_active_bin_drawn_over_wedge():
    _need_fb()
    cap = FrameCapture()
    r = Renderer()
    bins = [0.5] * 12
    sw = dict(rs._scan, sub="sweep", glyph="turn", glow_r_px=12)
    _run(r, cap, dict(sweep=(90, bins, 3, False), **sw), 200)
    # bin 3 (90 deg) sits mid-wedge: its prox.5 bar shows at r 75 on the wedge
    assert _px(cap.buf, 195, 120) == PROX[5], hex(_px(cap.buf, 195, 120))
    assert _px(cap.buf, 225, 120) == PROX[6]               # wedge lit past the bar


def test_no_allocation_steady_frames():
    _need_fb()
    bins = [0.2, None, 0.6, 0.9, 1.0, 0.75, 0.4, None, 0.15, 0.1, None, 0.05]
    cases = (
        _warm(glyph="arrow", arrow_deg=30, cone_deg=31, arrow_style="solid_b", trend=1,
              top_text="TAP TO SCAN", status=(64, 71, 3, False, False)),
        _turn(170),
        dict(rs._scan, sub="sweep", glyph="turn", glow_r_px=12, sweep=(100, bins, 3, False)),
        dict(MENU_KW, sub="1"),
        # the subs Game really emits: suffix parsing + scroll triangles (fb.poly)
        dict(MENU_KW, sub="0v"),
        dict(MENU_KW, sub="3^"),
        dict(MENU_KW, sub="2^v"),
        _warm(sub="walk", glyph="arrow", arrow_deg=0, cone_deg=31, arrow_style="solid_b",
              trend=0),
    )
    # float params are converted once per params object and heartbeat event
    # lists are reused, so a steady frame loop allocates nothing
    for kw in cases:
        r = Renderer()
        cap = FrameCapture()
        ps = [make_params(t_ms=T0 + 50 * k, **kw) for k in range(80)]
        for k in range(30):
            _fr(r, ps[k], cap)
        gc.collect()
        a0 = gc.mem_alloc()
        for k in range(30, 80):
            _fr(r, ps[k], cap)
        grown = gc.mem_alloc() - a0
        assert grown < 512, (kw.get("screen"), kw.get("sub"), grown)


# ---- review round 2 regressions --------------------------------------------------
def _luma(c):
    n = swap16(c)
    return 299 * ((n >> 11) << 1) + 587 * ((n >> 5) & 63) + 114 * ((n & 31) << 1)


def test_sun_ghost_rings_lifted_with_field():
    # §8 sun mode lifts the ramp LUT one stop; a ghost ring (§4 rule 5: drawn
    # where v_ghost > v) must be lifted too, or it is a dark notch in the field.
    # An outward ghost (inward rings never are) over a grey field, so the luma
    # compare is like for like: grey over green differs in hue, sun or not.
    _need_fb()
    orig = fld.pal_kernel
    won = []
    darker = []

    def probe(pal, acc, tab, prm):
        q = array.array("i", prm)
        q[fld.P_GHOST] = 0
        plain = array.array("H", [0] * 256)
        orig(plain, array.array("i", acc), tab, q)
        orig(pal, acc, tab, prm)
        for i in range(170):
            if pal[i] != plain[i]:
                won.append(i)
                if _luma(pal[i]) < _luma(plain[i]):
                    darker.append(i)

    fld.pal_kernel = probe
    try:
        _run(Renderer(), FrameCapture(),
             _warm(ring_live=False, ramp="grey", sun=True), 5000)
    finally:
        fld.pal_kernel = orig
    assert won and not darker, (len(won), darker[:10])


def test_menu_freezes_found_standing_wave():
    # ui-spec MENU: the field is frozen, so the FOUND standing wave stops
    # breathing under a menu opened from FOUND
    _need_fb()
    r = Renderer()
    cap = FrameCapture()
    _run(r, cap, dict(FOUND_KW, sub="result"), 3000)
    menu = dict(MENU_KW, ramp="gold", zone=3, speed_px_s=0, pulse_period_ms=1200)
    _run(r, cap, menu, 700, t0=T0 + 3050)                 # past the 600 ms dim slew
    pal = list(r.field.pal_arr)
    for k in range(12):
        _fr(r, make_params(t_ms=T0 + 3800 + 100 * k, **menu), cap)
        assert list(r.field.pal_arr) == pal, k


def test_zone_tempo_rings_use_zone_widths():
    # §5.3: lead / trail are part of the zone tempo, also where Game plays it
    # outside FAR..HOT (PAIRING split, SCANNING ready)
    _need_fb()
    for kw, z in ((rs.hunt(3, screen="PAIRING", sub="split", glyph="countdown", countdown=24,
                           heartbeat=None), 3),
                  (dict(rs._scan, sub="ready", glyph="countdown", countdown=3), 2)):
        r = Renderer()
        _run(r, None, kw, 1500)
        f = r.field
        rings = [k for k in range(fld.MAXR) if f.r_on[k]]
        assert rings, kw["screen"]
        for k in rings:
            assert (f.r_lead[k], f.r_trail[k]) == (T.ZONE_LEAD_PX[z], T.ZONE_TRAIL_PX[z]), \
                (kw["screen"], f.r_lead[k], f.r_trail[k])


def test_ring_schedule_retimes_on_period_change():
    # §5.3 tempo change: a shorter period times the next ring from the last
    # spawn instead of waiting out the old period
    f = fld.RippleField()
    assert f.schedule(0, 2400, 64 << 8, 40, 3, 22, True, False) == 1
    assert f.next_spawn == 2400
    assert f.schedule(300, 500, 64 << 8, 56, 3, 20, True, False) == 0
    assert f.next_spawn == 500
    assert f.schedule(500, 500, 64 << 8, 56, 3, 20, True, False) == 1 and f.last_spawn == 500


def test_wake_wedge_at_current_angle():
    # §8: a wedge that moved while the screen was off is drawn at its current
    # angle on wake, not glided from the angle before the gap
    _need_fb()
    from ui import glyphs as gl
    cap = FrameCapture()
    r = Renderer()
    _run(r, cap, _turn(60), 200)
    _run(r, None, _turn(150), 500, t0=T0 + 250)
    _fr(r, make_params(t_ms=T0 + 800, **_turn(150)), cap)
    got = list(gl._wedge)
    gl.prep_wedge(150)
    assert got == list(gl._wedge)


def test_glyph_culling_boxes_cover_glyphs():
    # G_Y0/G_Y1 are typed in, the glyph geometry comes from tokens: a box
    # that stops covering its glyph clips it at a strip edge
    _need_fb()
    from ui import renderer as R
    cases = list(rs.FIXTURES) + [       # plus the widest beam all round
        ("arrow_%d" % d, [(0, _warm(glyph="arrow", arrow_deg=d, cone_deg=60,
                                    arrow_style="outline"))], 400) for d in range(0, 360, 45)]
    r = Renderer()
    a = FrameCapture()
    b = FrameCapture()
    y0, y1 = R.G_Y0, R.G_Y1
    for name, phases, run_ms in cases:
        rs.render_fixture(r, a, name, phases, run_ms)
        R.G_Y0 = (0,) * 10
        R.G_Y1 = (240,) * 10
        try:
            rs.render_fixture(r, b, name, phases, run_ms)
        finally:
            R.G_Y0, R.G_Y1 = y0, y1
        assert a.buf == b.buf, name


def test_fixtures_match_snapshots():
    # every snapshot fixture still renders the frame its PNG was made from
    # (frame CRCs in tests/snapshot_crc.json, written by render_snapshots)
    _need_fb()
    import binascii
    import json
    try:
        with open(rs.CRC_FILE) as f:
            crc = json.load(f)
    except OSError:
        raise AssertionError(rs.CRC_FILE + " missing: run python3 tools/render_snapshots.py")
    r = Renderer()
    cap = FrameCapture()
    stale = []
    for name, phases, run_ms in rs.FIXTURES:
        rs.render_fixture(r, cap, name, phases, run_ms)
        if crc.get(name) != binascii.crc32(cap.buf):
            stale.append(name)
    assert not stale, "re-render the snapshots (python3 tools/render_snapshots.py): %s" % stale
