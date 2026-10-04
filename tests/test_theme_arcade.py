"""ui/themes/arcade.py: the Arcade theme's own behaviour (ui-spec §4A "Arcade").

The generic theme contract (dirty coverage, allocation, lens, core dot, MENU
freeze, flash limit) is tests/test_themes.py. Here: the cell tables, the
colour kernel (plain and viper), the closed-form dirty spans, and on
MicroPython (framebuf) the moments: fronts locked to the ring spawn and one
cell a tick, changes only on the 100 ms tick, ghost beats grey, listening
diamonds inward every 2 ticks, twinkles, the scan halo, FOUND, the lens drawn
into the index map, the wake, sun mode and the saver cap.
"""

import array

from finder import tuning as T
from tests import Skip
from ui.field import RippleField

try:
    import framebuf
    HAVE_FB = True
except ImportError:
    framebuf = None
    HAVE_FB = False


def _import_arcade():
    """ui.themes.arcade. On CPython the package's __init__ imports the
    renderer (framebuf), so load the theme modules under a bare package
    stand-in and take it out of sys.modules again (other tests see the real
    package state)."""
    if HAVE_FB:
        from ui.themes import arcade
        return arcade
    import os
    import sys
    import types
    names = ("ui.themes", "ui.themes.base", "ui.themes.arcade")
    saved = [sys.modules.get(n) for n in names]
    pkg = types.ModuleType("ui.themes")
    pkg.__path__ = [os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                 "ui", "themes")]
    sys.modules["ui.themes"] = pkg
    try:
        from ui.themes import arcade
    finally:
        for n, m in zip(names, saved):
            if m is None:
                sys.modules.pop(n, None)
            else:
                sys.modules[n] = m
    return arcade


A = _import_arcade()

if HAVE_FB:
    from tools import render_snapshots as rs
    from tools import render_themes as rt
    from ui.field import build_quadrant
    from ui.renderer import FrameCapture
    from ui.themes import ThemedRenderer
    from ui.themes.base import _disc

P = T.THEME_PARAMS["arcade"]


def _need_fb():
    if not HAVE_FB:
        raise Skip("framebuf is MicroPython-only")


class _FakeR:
    """Just enough renderer for an Arcade without framebuf (tables, spans)."""

    def __init__(self):
        self.field = RippleField()
        self.bands = tuple(bytearray(240 * 60 * 2) for _ in range(4)) if HAVE_FB else None
        self._scr = 2
        self._sub = None
        self.pacer = False
        self._iq = 0
        self._burst_t = None


def _cells():
    """(column, row, d) of every screen cell."""
    out = []
    for r in range(A.NC):
        for c in range(A.NC):
            out.append((c, r, A.CQ[c] + A.CQ[r]))
    return out


def _arrow(z, **kw):
    """A zone hunt with the arrow glyph: iris 64, so d0 = 8 and no core."""
    return rs.hunt(z, glyph="arrow", arrow_deg=30, cone_deg=31, arrow_style="solid_b", **kw)


def _run(phases, ms, step=100, each=None, r=None):
    r = r or ThemedRenderer("arcade", overlays=False)
    cap = FrameCapture()
    rt.run(r, cap, phases, ms, step, each)
    return r, cap


def _px(buf, x, y):
    o = (y * 240 + x) * 2
    return buf[o] | (buf[o + 1] << 8)


def rs_period(z):
    return (2400, 1600, 1000, 500)[z]          # ui-spec §5.3 pulse_period_ms, FAR..HOT


def _front_level(r, q):
    sc = A.FS_A + ((A.FS_B * r._iq) >> 8)
    return ((A.F0, A.F1, A.F2)[q] * sc) >> 8


# ---- tables, kernel, spans (any runtime) -------------------------------------------
def test_params_from_tokens():
    assert A.CELL == P["cell_px"] == 8 and A.TICK == P["tick_ms"] == 100
    assert A.NC == 30 and A.ND == 29 and A.LISTEN_TPC == P["listen_ticks_per_cell"] == 2
    assert (A.F0, A.F1, A.F2) == tuple(int(v * 256 + 0.5) for v in P["front_levels"])
    assert (A.FS_A, A.FS_B) == tuple(int(v * 256 + 0.5) for v in P["front_scale"])
    assert (A.CORE_A, A.CORE_B) == tuple(P["core_cells"])
    for z in range(4):                         # the tick divides every zone period
        assert rs_period(z) % A.TICK == 0


def test_cell_tables():
    # ring number = Manhattan distance in the centre-symmetric grid, 0..28
    assert A.CQ[14] == A.CQ[15] == 0 and A.CQ[0] == A.CQ[29] == 14
    for c in range(A.NC):
        assert A.CQ[c] == A.CQ[A.NC - 1 - c]
    assert A.NE == len(A._EV) <= A.LENS_E < A.RIM_E < 256
    for d in range(A.ND):
        assert A._DOFF[d] < A._DOFF[d + 1]          # every ring number has an entry
    for cy in range(A.HC):
        for cx in range(A.HC):
            e = A.QE[cy * A.HC + cx]
            assert A._DOFF[cx + cy] <= e < A._DOFF[cx + cy + 1]
            assert A.NEAR[cy * A.HC + cx] < A.FAR[cy * A.HC + cx]
    assert A._EV[A.QE[0]] == 256                    # no vignette at the centre
    assert all(0 < v <= 256 for v in A._EV)
    # the strips are 3 cell rows each
    for k in range(10):
        rows = [A.CQ[3 * k + i] for i in range(3)]
        assert min(rows) == A.CYMIN[k] and max(rows) == A.CYMIN[k] + 2


def test_map_matches_tables():
    m = A.build_map()
    for c, r, d in _cells():
        e = m[(8 * r + 3) * 240 + 8 * c + 5]
        assert A._DOFF[d] <= e < A._DOFF[d + 1], (c, r, d, e)
        for y in range(8 * r, 8 * r + 8, 7):        # whole 8 px blocks
            for x in range(8 * c, 8 * c + 8, 7):
                assert m[y * 240 + x] == e
        assert m[(239 - 8 * r) * 240 + 239 - 8 * c] == e      # symmetric


def _kernel_tab(mix, grey):
    tab = array.array("H", [0] * A.T_N)
    for i in range(A.NE):
        tab[i] = A._EV[i]
    for i in range(A.ND + 1):
        tab[A.T_DOFF + i] = A._DOFF[i]
    for j in range(64):
        tab[A.T_MIX + j] = mix[j]
        tab[A.T_GREY + j] = grey[j]
    return tab


def _index(v, vmax, dim, lift):
    if v > vmax:
        v = vmax
    if dim != 256:
        v = (v * dim) >> 8
    j = ((v * 9 + 128) >> 8) + lift
    return 63 if j > 63 else j


def test_colour_kernel():
    mix = [0x1000 + j for j in range(64)]
    grey = [0x2000 + j for j in range(64)]
    tab = _kernel_tab(mix, grey)
    for kern in (A.col_kernel_py, A.col_kernel):
        for vmax, dim, lift, fill in ((1792, 256, 0, 0), (1280, 128, 9, 1024), (1792, 200, 0, 700)):
            lv = array.array("H", [0] * (2 * A.ND))
            gy = bytearray(2 * A.ND)
            for d in range(A.ND):
                lv[d] = 100 + 61 * d                # crosses the checker limit 1.5
                gy[d] = (0, 1, 2, 6, 3)[d % 5]
            prm = array.array("i", [vmax, dim, lift, 1, fill])
            pal = array.array("H", [0] * 256)
            assert kern(pal, lv, gy, tab, prm) == (0 << 8) | (A.ND - 1)
            for d in range(A.ND):
                a = lv[d]
                if d & 1 and a < A.CHK_BELOW:
                    a += A.CHK_ADD
                assert lv[A.ND + d] == lv[d] and gy[A.ND + d] == gy[d]
                for e in range(A._DOFF[d], A._DOFF[d + 1]):
                    v = (a * A._EV[e]) >> 8
                    if gy[d] & 2:
                        f = fill + (fill >> 1) if gy[d] & 4 else fill
                        v = f if v < f else v
                    lut = grey if gy[d] & 1 else mix
                    assert pal[e] == lut[_index(v, vmax, dim, lift)], (d, e)
            # unchanged ring numbers are left alone; changed ones reported
            for e in range(256):
                pal[e] = 0
            lv[5] += 1
            gy[9] ^= 1
            prm[3] = 0
            assert kern(pal, lv, gy, tab, prm) == (5 << 8) | 9
            for e in range(A.NE):
                d = 0
                while A._DOFF[d + 1] <= e:
                    d += 1
                assert (pal[e] != 0) == (d in (5, 9)), (e, d)
            assert kern(pal, lv, gy, tab, prm) == 0xFF00


def test_colour_kernel_viper_agrees():
    # the watch runs the viper kernel only after this same self-check
    assert A.kernel_agrees(A.col_kernel_py, A.col_kernel_py)
    assert A.kernel_agrees(A.col_kernel_py, A.col_kernel), A.KERNEL
    assert A.KERNEL in ("viper", "python")


def test_span_is_exact_for_ring_ranges():
    # dirty strips and column spans of ring numbers lo..hi, in closed form,
    # equal the cells' own boxes: never less (contract), never more (tight)
    th = A.Arcade(_FakeR())
    cells = _cells()
    for lo in range(A.ND):
        for hi in range(lo, A.ND):
            th.clear_dirty()
            th._span(lo, hi)
            want = {}
            for c, r, d in cells:
                if lo <= d <= hi:
                    k = r // 3
                    a, b = want.get(k, (255, 0))
                    want[k] = (min(a, 8 * c), max(b, 8 * c + 7))
            mask = 0
            for k in want:
                mask |= 1 << k
                assert (th.spans[2 * k], th.spans[2 * k + 1]) == want[k], (lo, hi, k)
            assert th.dirty == mask, (lo, hi, hex(th.dirty), hex(mask))


def test_random_tables_deterministic():
    assert A.RND == A._rnd_table()
    assert len(A.CF_COL) == len(A.CF_OFF) == A.NCF
    assert all(c < A.NC for c in A.CF_COL)


# ---- frames (MicroPython) ---------------------------------------------------------
def test_fronts_locked_to_ring_spawns():
    # Live: a front 3 cells deep (7 / 4.6 / 2.6 x (0.62 + 0.38 I)) leaves the
    # lens edge on each ring spawn and moves out one cell a tick, so fronts sit
    # P / 100 ms cells apart (24 / 16 / 10 / 5, FAR..HOT)
    _need_fb()
    for z in range(4):
        per = rs._hunt[z]["pulse_period_ms"]
        ph = [(0, _arrow(z))]
        st = {"last": None, "prev": None, "spawns": 0}

        def each(t, r, cap):
            th = r.theme
            d0 = 8
            heads = [d for d in range(A.ND) if th.lv[d] == _front_level(r, 0)]
            if t > 0 and r.field.last_spawn != st["last"]:
                st["spawns"] += 1
                assert heads and heads[0] == d0, (z, t, heads)
            for i in range(1, len(heads)):
                assert heads[i] - heads[i - 1] == per // A.TICK, (z, t, heads)
            if st["prev"] is not None:               # one cell a tick
                moved = [h + 1 for h in st["prev"] if h + 1 < A.ND]
                assert moved == [h for h in heads if h != d0], (z, t, st["prev"], heads)
                for h in heads:                      # the 3-deep profile behind each head
                    if h - 1 >= d0:                  # (the brighter of it and the glow)
                        assert th.lv[h - 1] >= _front_level(r, 1)
                    if h - 2 >= d0:
                        assert th.lv[h - 2] >= _front_level(r, 2)
            st["prev"] = heads
            st["last"] = r.field.last_spawn
        _run(ph, 2 * per + 600, each=each)
        assert st["spawns"] >= 2, z


def test_glow_depth_and_core():
    # the cells next to the lens glow 1 + 3 I cells deep at 2.4 + 2 I; with
    # the iris closed the centre block is the core dot (level >= 6)
    _need_fb()
    for z in (0, 3):
        r, cap = _run([(0, rs.hunt(z))], 700)
        th = r.theme
        iq = r._iq
        depth = A.CORE_A + ((A.CORE_B * iq + 128) >> 8)
        gv = A.GLOW_A + ((A.GLOW_B * iq) >> 8)
        assert th.lv[0] >= T.CORE_DOT_LEVEL * 256
        for d in range(1, depth):
            assert th.lv[d] >= gv, (z, d)
        assert depth == 1 + int(3 * rs._hunt[z]["intensity"] + 0.5)


def test_changes_only_on_ticks():
    # 20 fps: the picture changes only on the 100 ms tick (beat-aligned);
    # the frames between report nothing and are identical
    _need_fb()
    r = ThemedRenderer("arcade", overlays=False)
    cap = FrameCapture()
    _, ph, _ = rt.fixture("hot")
    prev = None
    ticks = 0
    for t in range(0, 2001, 50):
        r.frame(rt.params_at(ph, t), cap, rt.T0 + t)
        th = r.theme
        if t >= 700:                                  # iris and levels settled
            if t % 100:
                assert th.dirty == 0, t
                assert cap.buf == prev, t
            elif cap.buf != prev:
                ticks += 1
        prev = bytes(cap.buf)
    assert ticks >= 10


def test_ghost_beat_is_grey():
    _need_fb()
    r, cap = _run([(0, _arrow(0, ring_live=False))], 900)
    th = r.theme
    heads = [d for d in range(A.ND) if th.lv[d] == _front_level(r, 0)]
    assert heads
    grey = th.ramps.grey.c
    for h in heads:
        assert th.gy[h] & 1
        for e in range(A._DOFF[h], A._DOFF[h + 1]):
            j = _index((th.lv[h] * A._EV[e]) >> 8, th.prm[0], th.prm[1], th.prm[2])
            assert th.pal_arr[e] == grey[j]
    # a live beat is not grey
    r, cap = _run([(0, _arrow(0))], 900)
    th = r.theme
    for d in range(A.ND):
        assert th.gy[d] & 1 == 0


def test_listening_diamonds_march_inward():
    # grey diamonds enter at the corners on each (inward) ring spawn and
    # move in one cell every 2 ticks until they reach the lens
    _need_fb()
    _, ph, _ = rt.fixture("searching")         # iris 44: d0 = 6
    seen = []

    def each(t, r, cap):
        th = r.theme
        heads = [d for d in range(A.ND) if th.lv[d] == A.L0 and th.gy[d] & 1]
        for h in heads:
            assert h >= 6
            assert th.lv[h + 1] == A.L1 if h + 1 < A.ND else True
        seen.append((t, heads))
    _run(ph, 4000, each=each)
    # follow the newest diamond: one cell inward every 200 ms (2 ticks)
    track = [(t, h[-1]) for t, h in seen if h]
    assert len(track) > 10
    moves = []
    best = 0
    for (t0, h0), (t1, h1) in zip(track, track[1:]):
        if h1 > h0:                                 # a new diamond entered
            moves = []
            continue
        assert h0 - h1 <= 1, (t0, h0, t1, h1)
        if h1 == h0 - 1:
            if moves:
                assert t1 - moves[-1] == 100 * A.LISTEN_TPC, (moves, t1)
            moves.append(t1)
            best = max(best, len(moves))
    assert best >= 8


def test_twinkles_in_still():
    _need_fb()
    _, ph, _ = rt.fixture("pairing_seen")      # iris 92
    lit = []

    def each(t, r, cap):
        th = r.theme
        if t < 400:
            return
        assert th.mom == A.M_STILL
        assert th.tw_on <= (A.NTW * 3 + A.TW_LIFE - 1) // A.TW_LIFE
        assert th.layer == (th.tw_on > 0)
        n = 0
        for j in range(A.NTW):
            if th.tw_l[j]:
                n += 1
                assert A.NEAR[th.tw_q[j]] > 92 + A.RIM_PX       # outside the lens
                x = th.tw_x[j] * 8 + 3
                y = th.tw_y[j] * 8 + 3
                assert _px(cap.buf, x, y) == th.tw_c[th.tw_l[j] - 1]
        assert n == th.tw_on
        lit.append(n)
    _run(ph, 2400, each=each)
    assert max(lit) >= 3


def test_scan_halo_is_the_live_mirror():
    _need_fb()
    _, ph, run_ms = rt.fixture("scan_sweep")   # iris 64
    r, cap = _run(ph, run_ms)
    th = r.theme
    assert th.mom == A.M_SCAN
    v = A.MIRROR_A + ((A.MIRROR_B * r._iq) >> 8)
    de = ((64 + A.RIM_PX) * 181 + 1023) >> 10
    for d in range(8, de + 1):
        assert th.lv[d] == v, d
    for d in range(de + 1, A.ND):
        assert th.lv[d] < v, d


def test_found_confetti_and_marquee():
    _need_fb()
    _, ph, _ = rt.fixture("found_result")
    st = {"prev": None, "ring": []}

    def each(t, r, cap):
        th = r.theme
        if t < 500:
            return
        assert th.mom == A.M_FOUND and th.ramps.ramp == "gold"
        assert th.cf_on > 0 and th.layer
        dl = (T.CHECK_DISC_R + 7) // 8
        st["ring"].append((th.tk, th.lv[dl + 1], th.lv[dl + 2]))
        ys = bytes(th.cf_y)
        if st["prev"] is not None:
            for j in range(A.NCF):                 # one cell a tick, down
                a = st["prev"][j]
                b = ys[j]
                if a != 255 and b != 255:
                    assert b == a + 1, (t, j, a, b)
        top = {}                                   # the block drawn last in a cell shows
        for j in range(A.NCF):
            if ys[j] != 255:
                top[(th.cf_x[j], ys[j])] = j
        for (cx, cy), j in top.items():
            assert _px(cap.buf, cx * 8 + 4, cy * 8 + 4) == th.cf_c[j % 3]
        st["prev"] = ys
    _run(ph, 2500, each=each)
    for tk, a, b in st["ring"]:                    # marquee: 5 / 3.5, swapping every 4 ticks
        hi = (tk >> 2) & 1
        assert (a, b) == ((A.FR_HI, A.FR_LO) if hi else (A.FR_LO, A.FR_HI))
    assert len(set((a, b) for _, a, b in st["ring"])) == 2


def _lens_map(th, rad):
    """The pristine index map with a lens of radius ``rad`` drawn fresh."""
    full = A.build_map()
    if th.quad:
        q = build_quadrant(full)
        fb = framebuf.FrameBuffer(q, 120, 120, framebuf.GS8)
        if rad:
            fb.ellipse(0, 119, rad + A.RIM_PX - 1, rad + A.RIM_PX - 1, A.RIM_E, True, 1)
            fb.ellipse(0, 119, rad - 1, rad - 1, A.LENS_E, True, 1)
        return bytes(q)
    fb = framebuf.FrameBuffer(full, 240, 240, framebuf.GS8)
    if rad:
        _disc(fb, rad + A.RIM_PX, A.RIM_E)
        _disc(fb, rad, A.LENS_E)
    return bytes(full)


def _lens_phases():
    seen = dict(rs._pair, sub="seen", speed_px_s=0, wavelength_px=0, runes=rs.RUNES)
    return [(0, seen),                                  # 92
            (600, _arrow(2)),                           # 64
            (1200, rs.hunt(1, glyph="chevrons", trend=1)),   # 44
            (1800, rs.hunt(3)),                         # 0
            (2400, _arrow(1)),                          # 64 again
            (3000, rs.hunt(0))]                         # 0


def test_lens_drawn_into_the_map():
    # the lens is drawn into the index map when the iris moves; cells it
    # leaves are put back exactly, so a settled lens is the pristine map plus
    # the lens, and a closed one the pristine map
    _need_fb()
    ph = _lens_phases()
    checks = {500: 92, 1100: 64, 1700: 44, 2300: 0, 2900: 64, 3500: 0}

    def each(t, r, cap):
        th = r.theme
        if t in checks:
            rad = checks[t]
            assert th.lr == rad == r.field.iris, (t, th.lr)
            got = bytes(th.map.q) if th.quad else bytes(th.map.idx)
            assert got == _lens_map(th, rad), t
            if rad:
                assert _px(cap.buf, 120, 120 - rad + 6) == T.THEME_IRIS["arcade"]
                assert _px(cap.buf, 120, 120 - rad - 1) == th.rimc
    _run(ph, 3500, each=each)


def test_viper_and_framebuf_map_paths_agree():
    # the quadrant (viper blit) and the full map (framebuf blit) carry the
    # same lens: frames are identical through opening and closing
    _need_fb()
    a = ThemedRenderer("arcade", overlays=False)
    b = ThemedRenderer("arcade", overlays=False)
    th = b.theme
    th.map.kern = None                       # force the framebuf palette blit
    th.quad = False
    th.mfb = th.map.map_fb
    ca = FrameCapture()
    cb = FrameCapture()
    ph = _lens_phases()
    for t in range(0, 3501, 100):
        p = rt.params_at(ph, t)
        a.frame(p, ca, rt.T0 + t)
        b.frame(p, cb, rt.T0 + t)
        assert ca.buf == cb.buf, t


def test_calibrate_fill_in_cells():
    # PAIRING calibrate: ring numbers up to dfill get the fill level (4) and
    # the outermost 1.5x (the edge, 6) as a floor after the vignette; dfill
    # runs from the lens edge to the corners as the fill radius grows
    _need_fb()
    cal = dict(rs._cal, countdown=3)
    ph = [(0, cal), (1000, dict(cal, countdown=2)), (2000, dict(cal, countdown=1))]
    st = {"df": -1}

    def each(t, r, cap):
        th = r.theme
        df = th.dfill
        assert df >= st["df"], t
        st["df"] = df
        if df < 0:
            return
        for d in range(df):
            assert th.gy[d] & 2 and not th.gy[d] & 4
        assert th.gy[df] & 6 == 6
        lut = th.ramps.mix.c
        for e in range(A._DOFF[df], A._DOFF[df + 1]):      # the edge, unvignetted floor
            assert th.pal_arr[e] == lut[_index(1536, th.prm[0], th.prm[1], th.prm[2])] or \
                th.lv[df] * A._EV[e] >> 8 > 1536
    _run(ph, 3000, each=each)
    assert st["df"] == A.ND - 1                         # the fill reached the corners


def test_wake_places_fronts_mid_flight():
    # after the screen was off, the first frame shows the fronts where the
    # beat puts them (no intro), as the field places its rings
    _need_fb()
    r = ThemedRenderer("arcade", overlays=False)
    cap = FrameCapture()
    ph = [(0, _arrow(3))]
    for t in range(0, 1001, 100):
        r.frame(rt.params_at(ph, t), cap, rt.T0 + t)
    for t in range(1100, 2900, 100):
        r.frame(rt.params_at(ph, t), None, rt.T0 + t)     # dark: state only
    t = 2900
    r.frame(rt.params_at(ph, t), cap, rt.T0 + t)
    th = r.theme
    age = r.theme.beat_age(rt.T0 + t)
    want = []
    while 8 + age // 100 < A.ND:
        want.append(8 + age // 100)
        age += 500
    heads = [d for d in range(A.ND) if th.lv[d] == _front_level(r, 0)]
    assert heads == sorted(want), (heads, want)


def test_menu_holds_the_tick():
    _need_fb()
    _, ph, _ = rt.fixture("menu")
    tks = []

    def each(t, r, cap):
        if t >= 1100:
            tks.append(r.theme.tk)
    _run(ph, 2000, each=each)
    assert len(set(tks)) == 1


def test_sun_and_saver():
    # sun: the floor is at least 1.0 and the LUT lifted (as the field);
    # saver: no colour above the level cap
    _need_fb()
    _, ph, run_ms = rt.fixture("far_sun")
    r, cap = _run(ph, run_ms)
    th = r.theme
    assert th.prm[2] == 9
    assert min(th.lv[d] for d in range(A.ND)) >= 256
    _, ph, run_ms = rt.fixture("saver")
    r, cap = _run(ph, run_ms)
    th = r.theme
    jmax = _index(10000, th.prm[0], 256, 0)
    ok = set(th.ramps.mix.c[j] for j in range(jmax + 1))
    ok |= set(th.ramps.grey.c[j] for j in range(jmax + 1))
    for e in range(A.NE):
        assert th.pal_arr[e] in ok, e
