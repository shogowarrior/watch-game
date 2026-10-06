"""ui/themes: the contract every theme keeps (ui-spec §4A, ui/themes/base.py).

Token checks run on CPython too; frames need framebuf (MicroPython):

    node tools/mpy/run.mjs tests/runner.py test_themes

Each theme runs the theme fixtures of tools/render_themes.py (FIXTURES at
10 fps, TRANSITIONS at their own frame times) with the overlays off
(``ThemedRenderer(name, overlays=False)``), so every pixel compared is the
theme's own field layer. Theme-specific behaviour is tested in
tests/test_theme_<name>.py.
"""

from finder import tuning as T
from finder.render_params import replace as rp_replace
from tests import Skip
from ui import swap16

try:
    import framebuf
    HAVE_FB = True
except ImportError:
    HAVE_FB = False

if HAVE_FB:
    import micropython
    from tools import render_snapshots as rs
    from tools import render_themes as rt
    from ui.renderer import FrameCapture, Renderer
    from ui.themes import NAMES, ThemedRenderer, make
    from ui.themes.base import ALL, M_FOUND, M_SCAN, NS, SH, W, WAKE_MS, theme_luts

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


_LUMA = []                   # [palette FrameBuffer, sample rows, their FrameBuffer]
LSTEP = 7                    # _mean_luma samples every 7th row


def _mean_luma(buf):
    """Mean luma of every 7th row, at C speed and allocating (almost)
    nothing: a palette blit turns each swapped RGB565 value of the sampled
    rows into its luma, and sum() adds them. The contract calls this on
    every frame of every theme, so it must cost less than the frame it
    checks (a pixel loop cost more), and on the wasm port nothing a test
    allocates is freed until the run ends (gc.collect does nothing there)."""
    if not _LUMA:
        lut = bytearray(65536)
        for c in range(65536):
            lut[c] = _luma(c)
        # 65535 is the widest FrameBuffer; colour 0xffff reads lut[65535],
        # which the buffer still holds
        pal = framebuf.FrameBuffer(lut, 65535, 1, framebuf.GS8)
        rows = bytearray(W * ((W + LSTEP - 1) // LSTEP))
        _LUMA.extend((pal, rows, framebuf.FrameBuffer(rows, W, len(rows) // W, framebuf.GS8)))
    pal, rows, rfb = _LUMA
    n = len(rows) // W
    rfb.blit(framebuf.FrameBuffer(buf, W, n, framebuf.RGB565, W * LSTEP), 0, 0, -1, pal)
    return sum(rows) // len(rows)


def _frame_locked(r, p, cap, now):
    """Draw one frame with the heap locked; False if it tried to allocate.
    Any allocation raises MemoryError at once, so this checks hard rule 2
    per frame for free (gc.mem_alloc walks the whole heap: two calls cost
    more than the frame they measure)."""
    micropython.heap_lock()
    try:
        r.frame(p, cap, now)
    except MemoryError:
        return False
    finally:
        micropython.heap_unlock()
    return True


def _changed(prev, cur):
    """{strip: (x0, x1)} where ``cur`` differs from ``prev``. Slice compares
    run in C (a pixel loop cost more than the frame it checks): a changed
    row widens its strip's span only past an unequal prefix or suffix, and
    the new edge is found by bisection."""
    mp = memoryview(prev)
    mc = memoryview(cur)
    out = {}
    for y in range(W):
        o = y * ROW
        if mp[o:o + ROW] == mc[o:o + ROW]:
            continue
        k = y // SH
        x0, x1 = out[k] if k in out else (W, -1)
        e = o + 2 * x0
        if x0 > 0 and not mp[o:e] == mc[o:e]:
            lo, hi = 0, x0           # pixels [0, lo) equal, [0, hi) not
            while hi - lo > 1:
                m = (lo + hi) >> 1
                e = o + 2 * m
                if mp[o:e] == mc[o:e]:
                    lo = m
                else:
                    hi = m
            x0 = lo
        e = o + 2 * (x1 + 1)
        if x1 < W - 1 and not mp[e:o + ROW] == mc[e:o + ROW]:
            lo, hi = x1 + 1, W       # pixels [lo, W) not equal, [hi, W) equal
            while hi - lo > 1:
                m = (lo + hi) >> 1
                e = o + 2 * m
                if mp[e:o + ROW] == mc[e:o + ROW]:
                    hi = m
                else:
                    lo = m
            x1 = lo
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


def _items():
    """(name, phases, frame offsets) of every fixture and transition."""
    out = []
    for fx, phases, run_ms in rt.FIXTURES:
        out.append((fx, phases, tuple(range(0, run_ms + 1, rt.STEP_MS))))
    return out + rt.TRANSITIONS


def _phase_of(phases, t):
    k = 0
    while k + 1 < len(phases) and phases[k + 1][0] <= t:
        k += 1
    return k


_REF = {}                    # item -> Ripple's mean luma per frame (the flash reference;
                             # test_contract_ripple fills it, a contract run alone renders it)


def _ripple_lumas(fx, phases, times):
    if fx not in _REF:
        r = ThemedRenderer("ripple", overlays=False)
        cap = FrameCapture()
        out = []
        for t in times:
            r.frame(rt.params_at(phases, t), cap, rt.T0 + t)
            out.append(_mean_luma(cap.buf))
        _REF[fx] = out
    return _REF[fx]


def _contract(name):
    """Run every theme fixture and transition on ``name``; check dirty,
    flash and allocation."""
    r = ThemedRenderer(name, overlays=False)
    cap = FrameCapture()
    _, phases, run_ms = rt.fixture("near")     # warm the renderer's one-off buffers
    rt.run(r, cap, phases, run_ms)
    prev = bytearray(W * W * 2)
    for fx, phases, times in _items():
        ps = [rt.params_at(phases, t) for t in times]
        ref = _ripple_lumas(fx, phases, times) if name != "ripple" else None
        mine = []
        r.reset()
        lum = -1
        k_was = -1
        age = 0
        for n in range(len(ps)):
            t = times[n]
            k = _phase_of(phases, t)
            age = age + 1 if k == k_was else 0
            k_was = k
            gap = t - times[n - 1] if n > 0 else 0
            wake = n == 0 or gap > WAKE_MS
            # steady frames allocate nothing (hard rule 2); a moment's first
            # frames may build caches
            if age >= 3 and not wake:
                assert _frame_locked(r, ps[n], cap, rt.T0 + t), (name, fx, t, "allocated")
            else:
                r.frame(ps[n], cap, rt.T0 + t)
            th = r.theme
            if wake:
                assert th.dirty == ALL, (name, fx, t, "a wake reports everything")
            else:
                ch = _changed(prev, cap.buf)
                for kk in ch:
                    x0, x1 = ch[kk]
                    assert th.dirty & (1 << kk), (name, fx, t, "strip", kk, "changed, not dirty")
                    assert th.spans[2 * kk] <= x0 and th.spans[2 * kk + 1] >= x1, (
                        name, fx, t, "strip", kk, (x0, x1),
                        (th.spans[2 * kk], th.spans[2 * kk + 1]))
            # no full-field flash (§4A rule 5): per frame the mean luma moves
            # no more than Ripple's does on the same params, plus 12
            m = _mean_luma(cap.buf)
            mine.append(m)
            if ref is not None and not wake:
                d = m - lum
                dr = ref[n] - ref[n - 1]
                assert abs(d) <= abs(dr) + 12, (name, fx, t, lum, m, "ripple", ref[n - 1], ref[n])
            lum = m
            prev[:] = cap.buf
        if ref is None:
            _REF[fx] = mine                      # Ripple's run is the reference


def test_frame_lock_catches_an_allocation():
    # the contract's allocation check itself: a frame that allocates fails it
    _need_fb()

    class Allocates:
        def frame(self, p, cap, now):
            self.last = [p, cap, now]

    class Still:
        def frame(self, p, cap, now):
            pass

    assert not _frame_locked(Allocates(), 1, 2, 3)
    assert _frame_locked(Still(), 1, 2, 3)
    x = [1, 2]                      # and the heap is unlocked after either
    assert len(x) == 2


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


# ---- the panel: a frame pushes only the strips that changed (ui-spec §4A rule 6) ----------
def _overlay_items():
    """Overlay changes over fields that may hold still: chevrons nudging and
    flipping, the readout's band, chip and word text, the StatusStrip, a
    toast rising and falling, the LAST chip's mark, bump icons, runes
    turning, the MENU scrolling. 20 fps where a motion is short."""
    h = rs.hunt
    lost = dict(rs._lost, banner=("LOST 0:12", "warn", True))
    rows2 = ("SUN: OFF", "BUZZ: FULL", "PLACE: OUT", "THEME: TIDE")
    seen = dict(rs._pair, sub="seen", speed_px_s=0, wavelength_px=0, runes=rs.RUNES,
                top_text="SAME RUNES?")
    return [
        ("chevrons", [(0, h(1, glyph="chevrons", trend=1, dist_band="~20")),
                      (700, h(1, glyph="chevrons", trend=1, trend_strong=True, dist_band="~20")),
                      (1400, h(1, glyph="chevrons", trend=-1, dist_band="~20"))],
         tuple(range(0, 2101, 100))),
        ("slot_text", [(0, h(0, dist_band="~40")), (500, h(0, dist_band="60+")),
                       (900, h(0, dist_band="60+", top_text="LOOK UP")),
                       (1300, h(0, dist_band="60+", word="TURN")),
                       (1700, h(0, dist_band="~40", status=(9, 64, 1, True, True))),
                       (2100, h(0, dist_band="~40", status=(9, 63, 2, True, False)))],
         tuple(range(0, 2501, 100))),
        ("toast", [(0, h(1, dist_band="~20")),
                   (300, h(1, dist_band="~20", banner=("FRIEND IS SCANNING", "info", False))),
                   (1200, h(1, dist_band="~20"))], tuple(range(0, 1801, 50))),
        ("lost_chip", [(0, lost), (900, dict(lost, trend=1)), (1500, dict(lost, banner=None))],
         tuple(range(0, 2101, 100))),
        ("bump_icons", [(0, h(3, glyph="bump", bump_icons=0, dist_band="<3", word="BUMP!")),
                        (500, h(3, glyph="bump", bump_icons=1, dist_band="<3", word="BUMP!")),
                        (900, h(3, glyph="bump", bump_icons=3, dist_band="<3", word="BUMP!"))],
         tuple(range(0, 1401, 100))),
        ("runes", [(0, dict(rs._pair, sub="looking", glyph="glow", word="LOOKING")),
                   (300, seen), (1000, dict(seen, sub="confirmed"))], tuple(range(0, 1501, 50))),
        ("menu_scroll", [(0, dict(rs._menu, sub="0v", menu_rows=rs.MENU_ROWS)),
                         (500, dict(rs._menu, sub="1v", menu_rows=rs.MENU_ROWS)),
                         (900, dict(rs._menu, sub="3^", menu_rows=rows2)),
                         (1300, h(2, dist_band="~10"))], tuple(range(0, 1801, 100))),
    ]


def _panel(name):
    """Every fixture, transition and overlay item with the overlays on, into
    a panel that keeps only what was pushed (FrameCapture): after each frame
    it must hold the frame drawn. Steady frames allocate nothing. Returns
    the mean number of strips pushed per frame."""
    r = ThemedRenderer(name)
    cap = FrameCapture()
    _, phases, run_ms = rt.fixture("near")     # warm the renderer's one-off buffers
    rt.run(r, cap, phases, run_ms)
    n = sent = 0
    for fx, phases, times in _items() + _overlay_items():
        r.reset()
        k_was = -1
        age = 0
        for i in range(len(times)):
            t = times[i]
            p = rt.params_at(phases, t)
            k = _phase_of(phases, t)
            age = age + 1 if k == k_was else 0
            k_was = k
            if age >= 3 and i > 0 and t - times[i - 1] <= WAKE_MS:
                assert _frame_locked(r, p, cap, rt.T0 + t), (name, fx, t, "allocated")
            else:
                r.frame(p, cap, rt.T0 + t)
            if cap.buf != r.buf:
                assert False, (name, fx, t, "sent", hex(r.sent), "stale", _changed(cap.buf, r.buf))
            n += 1
            m = r.sent
            while m:
                sent += m & 1
                m >>= 1
    return sent / n


def test_panel_ripple():
    _need_fb()
    assert _panel("ripple") > 9                 # its rings cross nearly every strip each frame


def test_panel_sonar():
    _need_fb()
    _panel("sonar")


def test_panel_tide():
    _need_fb()
    _panel("tide")


def test_panel_warp():
    _need_fb()
    _panel("warp")


def test_panel_arcade():
    _need_fb()
    _panel("arcade")


def test_panel_fireflies():
    _need_fb()
    _panel("fireflies")


def test_still_frames_send_little():
    # a frozen MENU sends nothing once its dim has settled; a field that moves
    # in a few places sends only those strips (Tide's surface, a few flies)
    _need_fb()
    cap = FrameCapture()
    for n in NAMES:
        r = ThemedRenderer(n)
        _, phases, run_ms = rt.fixture("menu")
        rt.run(r, cap, phases, 2400)
        for t in range(2500, 3001, 100):
            r.frame(rt.params_at(phases, t), cap, rt.T0 + t)
            assert r.sent == 0, (n, t, hex(r.sent))
    for n in ("tide", "fireflies"):
        r = ThemedRenderer(n)
        _, phases, run_ms = rt.fixture("far")
        rt.run(r, cap, phases, 300)
        k = 0
        for t in range(400, run_ms + 1, 100):
            r.frame(rt.params_at(phases, t), cap, rt.T0 + t)
            m = r.sent
            while m:
                k += m & 1
                m >>= 1
        assert k * 100 < 5 * (run_ms - 300), (n, k)     # fewer than half the strips


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


def test_reset_is_fresh():
    # after a renderer reset a theme draws as a fresh one: frames do not
    # depend on what ran before (previews and tests stay order-free)
    _need_fb()
    ca = FrameCapture()
    cb = FrameCapture()
    _, ph2, run2 = rt.fixture("hot")
    for n in NAMES:
        b = ThemedRenderer(n, overlays=False)
        rt.run(b, cb, ph2, run2)
        for fx in ("pairing_seen", "found_result", "searching", "far"):
            _, phases, run_ms = rt.fixture(fx)
            a = ThemedRenderer(n, overlays=False)
            rt.run(a, ca, phases, run_ms)
            rt.run(b, cb, phases, run_ms)        # after HOT and the fixtures before it
            assert ca.buf == cb.buf, (n, fx)


def test_menu_keeps_the_moment():
    # §4A rule 2: in the MENU a theme keeps the moment of the screen under
    # it, also one chosen there (the MENU's params carry speed 0 over FOUND)
    _need_fb()
    cap = FrameCapture()
    for fx, want in (("menu_over_found", M_FOUND), ("menu_over_scan", M_SCAN)):
        phases, times = None, None
        for it in rt.TRANSITIONS:
            if it[0] == fx:
                phases, times = it[1], it[2]
        for n in NAMES:
            r = ThemedRenderer("ripple", overlays=False)
            for t in times:
                if t > 1200:
                    break
                p = rt.params_at(phases, t)
                if t == 1000:
                    r.set_theme(n)
                r.frame(p, cap, rt.T0 + t)
            assert r.theme.name == n
            assert r.theme.moment(p) == want, (n, fx, r.theme.moment(p))


def test_queue_theme_swaps_when_loaded():
    _need_fb()
    cap = FrameCapture()
    _, phases, run_ms = rt.fixture("hot")
    for n in NAMES[1:]:
        r = ThemedRenderer("ripple", overlays=False)
        old = r.theme
        r.queue_theme(n)
        assert r.loading == n
        t = 0
        while r.loading is not None:
            assert r.theme is old, n                # the old theme keeps drawing
            r.frame(rt.params_at(phases, t), cap, rt.T0 + t)
            t += 100
            assert t < 10000, (n, "load never finished")
        assert r.theme.name == n
        r.frame(rt.params_at(phases, t), cap, rt.T0 + t)
        assert r.theme.dirty == ALL, n              # the first frame is the new theme, whole
        r.queue_theme(n)                            # same name: nothing to do
        assert r.loading is None


def test_unknown_name_is_ripple_once():
    _need_fb()
    r = ThemedRenderer("bogus")
    th = r.theme
    assert th.name == "ripple"
    for name in ("bogus", None, "Sonar", "ripple"):
        r.set_theme(name)
        assert r.theme is th, name
        r.queue_theme(name)
        assert r.loading is None, name


def test_dark_frames_wake_the_theme():
    _need_fb()
    cap = FrameCapture()
    _, phases, run_ms = rt.fixture("hot")
    for n in NAMES:
        r = ThemedRenderer(n, overlays=False)
        for t in range(0, 600, 100):
            r.frame(rt.params_at(phases, t), cap, rt.T0 + t)
        r.frame(rt.params_at(phases, 600), None, rt.T0 + 600)     # screen off
        r.frame(rt.params_at(phases, 700), cap, rt.T0 + 700)
        assert r.theme.dirty == ALL, n


def _themed(phases, t, name):
    return rp_replace(rt.params_at(phases, t), theme=name)


def test_follow_loads_the_params_theme_in_steps():
    # the game's renderers follow RenderParams.theme (ui-spec §3, §4A Choosing):
    # a new name loads one step per frame while the old theme keeps drawing; a
    # newer name replaces a load under way; an unknown one is Ripple, no load
    _need_fb()
    cap = FrameCapture()
    _, phases, run_ms = rt.fixture("hot")
    r = ThemedRenderer(follow=True, overlays=False)
    old = r.theme
    t = 0
    r.frame(_themed(phases, t, "sonar"), cap, rt.T0 + t)
    assert r.loading == "sonar" and r.theme is old
    t += 100
    r.frame(_themed(phases, t, "tide"), cap, rt.T0 + t)        # changed its mind
    assert r.loading == "tide" and r.theme is old
    while r.loading is not None:
        t += 100
        r.frame(_themed(phases, t, "tide"), cap, rt.T0 + t)
        assert r.loading is None or r.theme is old
        assert t < 10000, "load never finished"
    assert r.theme.name == "tide"
    tide = r.theme
    for name in ("tide", "sheen"):                               # same, then unknown
        t += 100
        r.frame(_themed(phases, t, name), cap, rt.T0 + t)
    assert r.loading is None or r.loading == "ripple"
    while r.loading is not None:
        t += 100
        r.frame(_themed(phases, t, "sheen"), cap, rt.T0 + t)
    assert r.theme.name == "ripple" and r.theme is not tide
    t += 100
    r.frame(_themed(phases, t, "sheen"), cap, rt.T0 + t)
    assert r.loading is None                                     # no reload every frame
    f = ThemedRenderer("warp")                                   # previews: params ignored
    f.frame(_themed(phases, 0, "arcade"), cap, rt.T0)
    assert f.loading is None and f.theme.name == "warp"


def test_follow_in_the_menu_starts_in_the_moment_under_it():
    # a theme picked in the MENU (params.theme changes on a MENU frame) loads
    # there and starts in the moment of the screen under the MENU (§4A rule 2)
    _need_fb()
    cap = FrameCapture()
    phases = times = None
    for it in rt.TRANSITIONS:
        if it[0] == "menu_over_found":
            phases, times = it[1], it[2]
    for n in NAMES[1:]:
        r = ThemedRenderer(follow=True, overlays=False)
        name = "ripple"
        for t in times:
            if t > 1200:
                break
            p = rt.params_at(phases, t)
            if p.screen == "MENU":
                name = n
            r.frame(rp_replace(p, theme=name), cap, rt.T0 + t)
        t = 1200
        while r.loading is not None:
            t += 100
            p = rp_replace(rt.params_at(phases, 1200), theme=n)
            r.frame(p, cap, rt.T0 + t)
        assert r.theme.name == n and p.screen == "MENU", (n, p.screen)
        assert r.theme.moment(p) == M_FOUND, (n, r.theme.moment(p))


def test_follow_frames_allocate_nothing():
    # following params.theme costs one compare a frame: no allocation (rule 7)
    _need_fb()
    cap = FrameCapture()
    _, phases, run_ms = rt.fixture("hot")
    r = ThemedRenderer("tide", follow=True, overlays=False)
    ps = [_themed(phases, k * 100, "tide") for k in range(8)]
    for k in range(4):
        r.frame(ps[k], cap, rt.T0 + k * 100)
    for k in range(4, 8):
        assert _frame_locked(r, ps[k], cap, rt.T0 + k * 100), k
