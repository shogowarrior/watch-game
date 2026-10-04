"""ui/themes: the contract every theme keeps (ui-spec §4A, ui/themes/base.py).

Token checks run on CPython too; frames need framebuf (MicroPython):

    node tools/mpy/run.mjs tests/runner.py test_themes

Each theme runs the theme fixtures of tools/render_themes.py at 10 fps with
the overlays off (``ThemedRenderer(name, overlays=False)``), so every pixel
compared is the theme's own field layer. Theme-specific behaviour is tested
in tests/test_theme_<name>.py.
"""

import gc

from finder import tuning as T
from tests import Skip
from ui import swap16

try:
    import framebuf  # noqa: F401
    HAVE_FB = True
except ImportError:
    HAVE_FB = False

if HAVE_FB:
    from tools import render_themes as rt
    from ui.renderer import FrameCapture, Renderer
    from ui.themes import NAMES, ThemedRenderer, make
    from ui.themes.base import ALL, NS, SH, W, theme_luts

ROW = 480                    # bytes per frame row


def _need_fb():
    if not HAVE_FB:
        raise Skip("framebuf is MicroPython-only")


def _px(buf, x, y):
    o = (y * W + x) * 2
    return buf[o] | (buf[o + 1] << 8)


def _luma(c):
    n = swap16(c)
    return (299 * ((n >> 11) << 3) + 587 * (((n >> 5) & 63) << 2) + 114 * ((n & 31) << 3)) // 1000


def _mean_luma(buf):
    s = 0
    n = 0
    for o in range(0, W * W * 2, 14):            # every 7th pixel
        s += _luma(buf[o] | (buf[o + 1] << 8))
        n += 1
    return s // n


def _changed(prev, cur):
    """{strip: (x0, x1)} where ``cur`` differs from ``prev``."""
    mp = memoryview(prev)
    mc = memoryview(cur)
    out = {}
    for y in range(W):
        o = y * ROW
        if mp[o:o + ROW] == mc[o:o + ROW]:
            continue
        x0 = 0
        while mp[o + 2 * x0] == mc[o + 2 * x0] and mp[o + 2 * x0 + 1] == mc[o + 2 * x0 + 1]:
            x0 += 1
        x1 = W - 1
        while mp[o + 2 * x1] == mc[o + 2 * x1] and mp[o + 2 * x1 + 1] == mc[o + 2 * x1 + 1]:
            x1 -= 1
        k = y // SH
        if k in out:
            a, b = out[k]
            out[k] = (min(a, x0), max(b, x1))
        else:
            out[k] = (x0, x1)
    return out


# ---- tokens (any runtime) -----------------------------------------------------------
def test_names_and_labels():
    assert T.THEME_NAMES == ("ripple", "sonar", "tide", "warp", "arcade", "fireflies")
    assert T.THEME_DEFAULT == "ripple"
    for n in T.THEME_NAMES:
        lab = T.THEME_LABELS[n]
        assert lab == n.upper() and len("THEME: " + lab) <= 16, lab
        for ch in "THEME: " + lab:
            assert ch in T.LABEL_CHARS, (lab, ch)
    assert T.THEME_IRIS["ripple"] == T.C_BG_IRIS


def test_theme_luts():
    for n in T.THEME_NAMES[1:]:
        luts = T.THEME_RAMP_LUT[n]
        for r in T.RAMP_NAMES:
            lut = luts[r]
            assert len(lut) == 64
            if n == "arcade":                     # stepped: 8 flat runs, one per stop
                runs = 1
                for j in range(1, 64):
                    if lut[j] != lut[j - 1]:
                        runs += 1
                assert runs <= 8, (n, r, runs)
        # the ramps run dark to bright (level 0 darkest, level 7 brightest)
        for r in T.RAMP_NAMES:
            assert _luma(luts[r][0]) < _luma(luts[r][63]), (n, r)


# ---- frames (MicroPython) ---------------------------------------------------------
def test_ripple_theme_is_the_renderer():
    _need_fb()
    a = Renderer()
    b = ThemedRenderer("ripple")
    ca = FrameCapture()
    cb = FrameCapture()
    for name in ("far", "hot_bump", "warm_arrow", "scan_sweep", "found_celebrate", "searching",
                 "pairing_calibrate", "menu", "far_sun", "saver"):
        _, phases, run_ms = rt.fixture(name)
        rt.run(a, ca, phases, run_ms)
        rt.run(b, cb, phases, run_ms)
        assert ca.buf == cb.buf, name


def test_make_every_name():
    _need_fb()
    r = ThemedRenderer()
    assert r.theme.name == "ripple"
    for n in NAMES:
        r.set_theme(n)
        assert r.theme.name == n
        assert make(n, r).name == n
    assert make("nope", r).name == "ripple"


def _contract(name):
    """Run every theme fixture on ``name``; check dirty, flash and allocation."""
    r = ThemedRenderer(name, overlays=False)
    cap = FrameCapture()
    prev = bytearray(W * W * 2)
    for fx, phases, run_ms in rt.FIXTURES:
        ps = [rt.params_at(phases, t) for t in range(0, run_ms + 1, rt.STEP_MS)]
        r.reset()
        lum = -1
        grown = 0
        for n in range(len(ps)):
            t = n * rt.STEP_MS
            gc.collect()
            gc.disable()
            a0 = gc.mem_alloc()
            r.frame(ps[n], cap, rt.T0 + t)
            if n >= 4:                       # a moment's first frames may build caches
                grown += gc.mem_alloc() - a0
            gc.enable()
            th = r.theme
            if n > 0:
                ch = _changed(prev, cap.buf)
                for k in ch:
                    x0, x1 = ch[k]
                    assert th.dirty & (1 << k), (name, fx, t, "strip", k, "changed, not dirty")
                    assert th.spans[2 * k] <= x0 and th.spans[2 * k + 1] >= x1, (
                        name, fx, t, "strip", k, (x0, x1), (th.spans[2 * k], th.spans[2 * k + 1]))
            else:
                assert th.dirty == ALL, (name, fx, "first frame reports everything")
            # no full-field flash (§4A rule 5): once a moment has settled (the
            # iris opens and levels crossfade over the first 300-600 ms), the
            # mean luma moves little per 100 ms frame
            m = _mean_luma(cap.buf)
            if n >= 7 and fx not in ("found_celebrate",):
                assert abs(m - lum) <= 12, (name, fx, t, lum, m)
            lum = m
            prev[:] = cap.buf
        # steady frames allocate nothing (hard rule 2)
        assert grown < 512, (name, fx, grown)


def test_contract_ripple():
    _need_fb()
    _contract("ripple")


def test_contract_sonar():
    _need_fb()
    _contract("sonar")


def test_contract_tide():
    _need_fb()
    _contract("tide")


def test_contract_warp():
    _need_fb()
    _contract("warp")


def test_contract_arcade():
    _need_fb()
    _contract("arcade")


def test_contract_fireflies():
    _need_fb()
    _contract("fireflies")


def test_deterministic():
    _need_fb()
    for n in NAMES:
        a = ThemedRenderer(n, overlays=False)
        b = ThemedRenderer(n, overlays=False)
        ca = FrameCapture()
        cb = FrameCapture()
        for fx in ("hot", "searching"):
            _, phases, run_ms = rt.fixture(fx)
            rt.run(a, ca, phases, run_ms)
            rt.run(b, cb, phases, run_ms)
            assert ca.buf == cb.buf, (n, fx)


def test_lens_and_core_dot():
    # §4A rule 4: every theme draws the lens in its colour and the core dot
    _need_fb()
    cap = FrameCapture()
    for n in NAMES:
        r = ThemedRenderer(n, overlays=False)
        _, phases, run_ms = rt.fixture("warm_arrow")       # iris 64, open after 300 ms
        rt.run(r, cap, phases, run_ms)
        c = T.THEME_IRIS[n]
        for x, y in ((119, 119), (120, 120), (120, 70), (170, 120), (120, 170), (70, 120)):
            assert _px(cap.buf, x, y) == c, (n, x, y, hex(_px(cap.buf, x, y)), hex(c))
        assert _px(cap.buf, 120, 50) != c, n                # outside the lens and rim
        _, phases, run_ms = rt.fixture("far")               # glow glyph: iris closed, core dot
        rt.run(r, cap, phases, run_ms)
        bright = theme_luts(n)["green"].c
        for x, y in ((119, 119), (120, 120), (116, 120), (123, 119)):
            px = _px(cap.buf, x, y)
            ok = False
            for j in range(54, 64):                         # level >= 6
                ok = ok or px == bright[j]
            assert ok, (n, x, y, hex(px))


def test_menu_freezes():
    # MENU freezes the field (§6); every theme's motion stops with it
    _need_fb()
    cap = FrameCapture()
    for n in NAMES:
        r = ThemedRenderer(n, overlays=False)
        _, phases, run_ms = rt.fixture("menu")
        rt.run(r, cap, phases, 2400)                        # dim settled
        a = bytes(cap.buf)
        for t in range(2500, 3001, 100):
            r.frame(rt.params_at(phases, t), cap, rt.T0 + t)
            assert cap.buf == a, (n, t)


def test_switch_reports_everything():
    _need_fb()
    cap = FrameCapture()
    r = ThemedRenderer("ripple", overlays=False)
    _, phases, run_ms = rt.fixture("hot")
    t = 0
    for n in NAMES[1:] + NAMES[:1]:
        for _ in range(3):
            r.frame(rt.params_at(phases, t), cap, rt.T0 + t)
            t += 100
        r.set_theme(n)
        r.frame(rt.params_at(phases, t), cap, rt.T0 + t)
        t += 100
        assert r.theme.dirty == ALL, n


def test_themes_differ_from_ripple():
    _need_fb()
    cap = FrameCapture()
    ref = None
    for n in NAMES:
        r = ThemedRenderer(n, overlays=False)
        _, phases, run_ms = rt.fixture("hot")
        rt.run(r, cap, phases, run_ms)
        if ref is None:
            ref = bytes(cap.buf)
        else:
            assert cap.buf != ref, n
