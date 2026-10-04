"""ui/themes/fireflies.py: the Fireflies theme (ui-spec §4A "Fireflies").

The generic theme contract (dirty regions, allocation, lens, MENU freeze,
reset, wakes, staged switch) is in tests/test_themes.py. These check the
numbers of the Fireflies paragraph: flies per zone, the blink locked to the
ring spawn with the zone's sync, the orbit shrinking with I, the moments
(listening specks, still glow, frozen scan, gold FOUND ring), the ghost beat
in grey and latched per beat, flies off the lens (and how they follow it
when it opens, closes or the calibrate fill ends), the halo's crossfade on a
moment change, the glide onto the FOUND ring, the saver's blink scale, the
moment under the MENU, tight changed regions, the clock wrap, the staged
load and the fly kernel's viper build agreeing with its plain one. Frames
need framebuf (MicroPython):

    node tools/mpy/run.mjs tests/runner.py test_theme_fireflies
"""

import math

from finder import tuning as T
from tests import Skip

try:
    import framebuf  # noqa: F401
    HAVE_FB = True
except ImportError:
    HAVE_FB = False

if HAVE_FB:
    from tools import render_themes as rt
    from ui.field import EASE_IOC, GHOST_AMP, ease
    from ui.renderer import FrameCapture
    from ui.themes import ThemedRenderer
    from ui.themes import fireflies as F
    from ui.themes.base import ALL, M_FOUND, theme_luts

P = T.THEME_PARAMS["fireflies"]
PERIOD = (2400, 1600, 1000, 500)        # zone ring periods (ui-spec §5.3)


def _need_fb():
    if not HAVE_FB:
        raise Skip("framebuf is MicroPython-only")


def _renderer():
    return ThemedRenderer("fireflies", overlays=False)


def _slot(th, f, j):
    return th.st[f * F.MAXF + j]


def _drawn(th):
    """[(cx, cy, sprite)] of the flies drawn this frame."""
    out = []
    for j in range(th.nn):
        s = _slot(th, F.F_CS, j)
        if s != F.NONE:
            out.append((_slot(th, F.F_CX, j), _slot(th, F.F_CY, j), s))
    return out


def _level(th, j):
    """Fly j's blink level index (0..NE-1), -1 when nothing of it shows."""
    s = _slot(th, F.F_CS, j)
    return -1 if s == F.NONE else (s - th.ib)


def _run(r, cap, phases, t0, t1, step, each=None):
    t = t0
    while t <= t1:
        r.frame(rt.params_at(phases, t), cap, rt.T0 + t)
        if each is not None:
            each(t)
        t += step


def _pos(th):
    """{slot: (cx, cy)} of the flies drawn this frame."""
    out = {}
    for j in range(th.nn):
        if _slot(th, F.F_CS, j) != F.NONE:
            out[j] = (_slot(th, F.F_CX, j), _slot(th, F.F_CY, j))
    return out


def _moved(a, b):
    """Largest per-frame move (px, the larger of |dx| and |dy|) of a fly
    drawn in both position maps."""
    m = 0
    for j in b:
        if j in a:
            m = max(m, abs(b[j][0] - a[j][0]), abs(b[j][1] - a[j][1]))
    return m


def _arrow(z):
    return rt.rs.hunt(z, sub="walk", glyph="arrow", arrow_deg=30, cone_deg=31,
                      arrow_style="solid_b", trend=1)


# ---- tokens (any runtime) ---------------------------------------------------------
def test_tokens_are_the_spec_numbers():
    # ui-spec §4A Fireflies: 5/9/14/22 flies, sync 0.25/0.5/0.8/1.0, rise 90 ms,
    # fall 260 ms, halo 0.9 I, orbit x (1 - 0.45 I), three specks over 7 s,
    # nine still flies, 22 in FOUND breathing over 2.4 s
    assert tuple(P["count"]) == (5, 9, 14, 22)
    assert tuple(P["sync"]) == (0.25, 0.5, 0.8, 1.0)
    assert P["rise_ms"] == 90 and P["fall_ms"] == 260
    assert P["halo"] == 0.9 and P["orbit_shrink"] == 0.45
    assert P["listen_specks"] == 3 and P["listen_ms"] == 7000
    assert P["still_count"] == 9 and P["found_count"] == 22 and P["breathe_ms"] == 2400
    assert len(T.THEME_RAMP_LUT["fireflies"]["green"]) == 64


# ---- module tables (MicroPython: the package imports the renderer) ----------------------
def test_module_reads_the_tokens():
    _need_fb()
    assert F.COUNT == tuple(P["count"])
    assert F.SYNC == tuple(int(s * 256 + 0.5) for s in P["sync"])
    assert (F.RISE_MS, F.FALL_MS, F.N_SPECK, F.LISTEN_MS) == (90, 260, 3, 7000)
    assert (F.N_STILL, F.N_FOUND, F.BREATHE_MS) == (9, 22, 2400)
    assert F.HALO == 230 and F.SHRINK == 115
    assert F.MAXF == 22


def test_blink_envelope():
    # rise 90 ms, then e^-(t/260 ms): Q8 per 8 ms after the rise starts
    _need_fb()
    tb = F.assets()[4]
    eb = [tb[F.T_EB + k] for k in range(F.NEB)]
    assert eb[0] == 0
    assert eb[5] == int(256 * 40 / 90 + 0.5)            # rising: 40 ms in
    assert eb[11] >= 248                                # 88 ms: the peak
    k = (90 + 260) // 8                                 # one fall time later: 1/e
    assert abs(eb[k] - 256 * math.exp(-(k * 8 - 90) / 260.0)) <= 1
    for k in range(12, F.NEB):
        assert eb[k] <= eb[k - 1]
    assert eb[F.NEB - 1] == 0


def test_sprites():
    _need_fb()
    spr, sh, cor, ch, tb = F.assets()[:5]
    assert len(spr) == F.N_SPR and len(cor) == F.N_SPR
    for i in range(F.NI):
        base = i * F.NE
        assert sh[base] == F.NONE                       # blink level 0: nothing shows
        prev = 0
        for k in range(1, F.NE):
            h = sh[base + k]
            assert h != F.NONE and h >= prev, (i, k)    # grows with the blink
            assert h + 1 <= F.FLY_CLEAR                 # the lens clearance holds
            prev = h
        assert ch[base + F.NE - 1] != F.NONE            # a full blink has a core
        assert ch[base + 1] == F.NONE                   # a faint one does not
    for i in range(1, F.NI):                            # and with I
        assert sh[i * F.NE + F.NE - 1] >= sh[(i - 1) * F.NE + F.NE - 1]
    for k in range(F.NA):
        h = sh[F.SPECK0 + k]
        assert h == F.NONE or h + 1 <= F.SPECK_CLEAR
    # a full blink's centre is at level >= 6 (q >= 27); its rim is dithered
    b, h, b2, h2 = F.make_sprite(lambda d: F.fly_level(d, 1.0, 0.875))
    n = 2 * h + 1
    assert b[h * n + h] >= 27
    assert b2[h2 * (2 * h2 + 1) + h2] >= F.CORE_Q
    lo = [v for v in b if 0 < v < F.QD]
    assert lo and min(lo) >= F.QK


# ---- frames (MicroPython) ---------------------------------------------------------
def test_count_per_zone_and_moment():
    _need_fb()
    r = _renderer()
    cap = FrameCapture()
    for fx, want in (("far", 5), ("near", 9), ("warm_arrow", 14), ("hot", 22), ("hot_bump", 22),
                     ("pairing_seen", 9), ("pairing_calibrate", 9), ("found_result", 22),
                     ("searching", 3), ("pairing_looking", 3), ("link_lost", 3),
                     ("scan_sweep", 14)):
        _, phases, run_ms = rt.fixture(fx)
        rt.run(r, cap, phases, 800)
        assert r.theme.nn == want, (fx, r.theme.nn)


def test_blink_peaks_on_the_beat_at_the_zone_tempo():
    # fly 0 blinks once per ring period with its peak on the ring spawn; in
    # HOT (sync 1.0) every fly blinks with it
    _need_fb()
    r = _renderer()
    cap = FrameCapture()
    for z in range(4):
        _, phases, _ = rt.fixture(("far", "near", "warm_arrow", "hot")[z])
        per = PERIOD[z]
        peaks = []
        seen = []

        def each(t):
            th = r.theme
            f = r.field
            lv = _level(th, 0)
            if th.beat_age(rt.T0 + t) == 0 and t >= 400:
                assert lv == F.NE - 1, (z, t, lv)       # brightest on the spawn frame
                seen.append(t)
            if lv == F.NE - 1 and (not peaks or t - peaks[-1] > 150):
                peaks.append(t)
            if z == 3:
                lvs = [_level(th, j) for j in range(th.nn)]
                assert min(lvs) == max(lvs), (t, lvs)
            assert f.period == per
        r.reset()
        _run(r, cap, phases, 0, 2 * per + 400, 50, each)
        assert len(seen) >= 2, (z, seen)
        gaps = [peaks[k + 1] - peaks[k] for k in range(len(peaks) - 1)]
        assert gaps and min(gaps) == per and max(gaps) == per, (z, peaks)


def test_sync_spreads_the_blinks():
    # peak offset (1 - sync) * h * P: all 0 in HOT, spread over up to 0.75 P in FAR
    _need_fb()
    r = _renderer()
    cap = FrameCapture()
    th = r.theme
    for z, fx in ((0, "far"), (1, "near"), (2, "warm_arrow"), (3, "hot")):
        _, phases, _ = rt.fixture(fx)
        rt.run(r, cap, phases, 300)
        n = F.COUNT[z]
        offs = [_slot(th, F.F_OFF, j) for j in range(n)]
        per = PERIOD[z]
        lim = ((256 - F.SYNC[z]) * per) >> 8
        assert offs[0] == 0
        assert max(offs) <= lim, (z, offs)
        if z == 3:
            assert max(offs) == 0
        else:
            assert len(set(offs)) >= n - 2 and max(offs) > lim // 2, (z, offs)
    # FAR: the flies are seldom lit together
    _, phases, _ = rt.fixture("far")
    r.reset()
    most = [0]

    def each(t):
        lit = sum(1 for j in range(5) if _level(th, j) >= F.NE - 3)
        most[0] = max(most[0], lit)
    _run(r, cap, phases, 0, 2400, 50, each)
    assert most[0] <= 3, most


def test_orbit_shrinks_with_intensity():
    # radius (46 + 64 h) (1 - 0.45 I) + 18 px
    _need_fb()
    r = _renderer()
    cap = FrameCapture()
    th = r.theme
    means = []
    for iv in (0.1, 0.9):
        phases = [(0, rt.rs.hunt(1, intensity=iv))]
        rt.run(r, cap, phases, 300)
        iq = r._iq
        rs = 256 - ((115 * iq) >> 8)
        tot = 0
        for j in range(F.MAXF):
            want = ((th.r0[j] * rs) >> 8) + 18
            assert _slot(th, F.F_RB, j) == want
            assert 46 <= th.r0[j] <= 110
            tot += want
        means.append(tot)
    ratio = means[1] / means[0]
    assert 0.6 < ratio < 0.75, ratio                    # (0.595 R + 18) / (0.955 R + 18)


def test_flies_stay_off_the_lens():
    # no fly pixel on the open lens (r < 64), nor on a closed iris's core dot
    _need_fb()
    r = _renderer()
    cap = FrameCapture()
    lens = T.THEME_IRIS["fireflies"]
    ring = []
    for y in range(240):
        for x in range(240):
            d = (2 * x - 239) ** 2 + (2 * y - 239) ** 2
            if (2 * 52) ** 2 <= d < (2 * 64) ** 2:
                ring.append((y * 240 + x) * 2)
    for fx in ("warm_arrow", "direction_turn", "scan_sweep"):
        _, phases, run_ms = rt.fixture(fx)

        def each(t):
            if t < 400:                                 # the iris opens over 300 ms
                return
            b = cap.buf
            for o in ring:
                assert b[o] | (b[o + 1] << 8) == lens, (fx, t, o)
            for cx, cy, s in _drawn(r.theme):
                h = r.theme.sh[s]
                d = math.sqrt((cx - 119.5) ** 2 + (cy - 119.5) ** 2)
                assert d - h * 1.415 >= 64 + 3, (fx, t, cx, cy, h)
        r.reset()
        _run(r, cap, phases, 0, run_ms, 100, each)
    for fx in ("far", "hot", "found_result"):           # iris closed: off the core dot
        _, phases, run_ms = rt.fixture(fx)

        def each2(t):
            for cx, cy, s in _drawn(r.theme):
                h = r.theme.sh[s]
                d = math.sqrt((cx - 119.5) ** 2 + (cy - 119.5) ** 2)
                assert d - h * 1.415 > 7, (fx, t, cx, cy, h)
        r.reset()
        _run(r, cap, phases, 0, run_ms, 100, each2)


def test_motion_is_calm():
    # at 10 fps no fly moves more than ~4 px a frame (§4A rule 5)
    _need_fb()
    r = _renderer()
    cap = FrameCapture()
    for fx in ("far", "hot", "pairing_seen", "found_result", "searching"):
        _, phases, run_ms = rt.fixture(fx)
        last = {}

        def each(t):
            th = r.theme
            for j in range(th.nn):
                s = _slot(th, F.F_CS, j)
                if s == F.NONE:
                    last.pop(j, None)
                    continue
                x = _slot(th, F.F_CX, j)
                y = _slot(th, F.F_CY, j)
                if j in last and t >= 400:
                    px, py = last[j]
                    assert abs(x - px) <= 4 and abs(y - py) <= 4, (fx, t, j, (px, py), (x, y))
                last[j] = (x, y)
        r.reset()
        _run(r, cap, phases, 0, run_ms, 100, each)


def test_scan_freezes_dim():
    _need_fb()
    r = _renderer()
    cap = FrameCapture()
    th = r.theme
    # enter the scan from a live WARM field, as the game does
    phases = [(0, rt.rs.hunt(2, dist_band="~10")),
              (1000, dict(rt.rs._scan, sub="sweep", glyph="turn", intensity=0.6, glow_r_px=12,
                          sweep=(135, rt.rs.BINS, 4, False)))]
    rt.run(r, cap, phases, 900)
    n = th.nn
    first = []

    def each(t):
        if t < 1500:                                    # the iris opens to 64
            return
        d = _drawn(th)
        assert th.nn == n == 14
        assert len(d) == n
        dim = th.ib + F.assets()[4][F.T_QE + F.SCAN_E]
        for _, _, s in d:
            assert s == dim
        if not first:
            first.extend(d)
        assert d == first, t                            # frozen
    _run(r, cap, phases, 1000, 2500, 100, each)
    assert first


def test_found_ring_gold_breathing():
    _need_fb()
    r = _renderer()
    cap = FrameCapture()
    th = r.theme
    _, phases, _ = rt.fixture("found_result")
    gold = list(theme_luts("fireflies")["gold"].c)    # (no `in` on arrays in MicroPython)
    levels = {}

    def each(t):
        d = _drawn(th)
        assert th.nn == 22
        for cx, cy, s in d:
            dist = math.sqrt((cx - 119.5) ** 2 + (cy - 119.5) ** 2)
            assert 74 - 1.5 <= dist <= 94 + 1.5, (t, cx, cy)
        if t >= 500:                                    # gold after the 400 ms crossfade
            for q in range(F.QK, F.NQ):
                assert th.fpa[q] in gold, (t, q, hex(th.fpa[q]))
        levels[t] = tuple(_level(th, j) for j in range(22))
    r.reset()
    _run(r, cap, phases, 0, 5200, 100, each)
    for t in range(600, 2700, 100):                     # breathes over 2.4 s
        assert levels[t] == levels[t + 2400], t
    vals = set(levels[t][0] for t in range(600, 3000, 100))
    assert len(vals) >= 3, vals
    lit = [max(levels[t]) for t in levels]
    assert max(lit) < F.NE - 1                          # no entry blink on a wake (§8)


def test_found_burst_blinks_them_all():
    _need_fb()
    r = _renderer()
    cap = FrameCapture()
    th = r.theme
    found = dict(rt.rs._found, sub="celebrate", word="FOUND")
    phases = [(0, rt.rs.hunt(3, dist_band="<3")),
              (1000, dict(found, burst=True, haptic="FOUND")),
              (1100, found)]
    rt.run(r, cap, phases, 900)
    seen = {}

    def each(t):
        seen[t] = [_level(th, j) for j in range(th.nn)]
    _run(r, cap, phases, 1000, 3000, 50, each)
    assert th.nn == 22
    assert min(seen[1100]) >= F.NE - 2, seen[1100]      # all lit 100 ms after the burst
    assert max(seen[3000]) < F.NE - 1, seen[3000]       # then back to breathing


def test_listening_specks_drift_in_grey():
    _need_fb()
    r = _renderer()
    cap = FrameCapture()
    th = r.theme
    grey = list(theme_luts("fireflies")["grey"].c)
    for fx in ("searching", "pairing_looking", "link_lost"):
        _, phases, _ = rt.fixture(fx)
        track = {}

        def each(t):
            assert th.nn == 3
            for q in range(F.QK, F.NQ):                 # grey, even on PAIRING's green
                assert th.fpa[q] in grey, (fx, q)
            for j in range(3):
                if _slot(th, F.F_CS, j) == F.NONE:
                    track.pop(j, None)
                    continue
                x = _slot(th, F.F_CX, j)
                y = _slot(th, F.F_CY, j)
                d = math.sqrt((x - 119.5) ** 2 + (y - 119.5) ** 2)
                assert d <= 126.5, (fx, t, j, d)
                a = math.atan2(x - 119.5, 119.5 - y)
                if j in track and t > 400:
                    pd, pa = track[j]
                    if abs(a - pa) < 0.3:               # same trip: only inward
                        assert d <= pd + 0.75, (fx, t, j, pd, d)
                track[j] = (d, a)
        r.reset()
        _run(r, cap, phases, 0, 7000, 100, each)
    # 7 s per trip: each speck goes from the edge to the lens once per 7 s
    _, phases, _ = rt.fixture("searching")
    r.reset()
    hidden = {0: [], 1: [], 2: []}           # start of each hidden spell
    shown = [True, True, True]

    def each3(t):
        for j in range(3):
            vis = _slot(th, F.F_CS, j) != F.NONE
            if not vis and shown[j] and t > 0:
                hidden[j].append(t)
            shown[j] = vis
    _run(r, cap, phases, 0, 15000, 100, each3)
    for j in range(3):
        h = hidden[j]
        gaps = [h[k + 1] - h[k] for k in range(len(h) - 1)]
        assert gaps and all(abs(g - 7000) <= 100 for g in gaps), (j, h)


def test_still_glows_softly():
    _need_fb()
    r = _renderer()
    cap = FrameCapture()
    th = r.theme
    _, phases, _ = rt.fixture("pairing_seen")
    lv = []

    def each(t):
        assert th.nn == 9
        lv.append([_level(th, j) for j in range(9)])
    _run(r, cap, phases, 0, 5000, 100, each)
    flat = [v for row in lv for v in row]
    assert max(flat) <= F.NE - 2, max(flat)             # 0.45 + 0.25 at most: never a full blink
    assert min(flat) >= 3, min(flat)                    # 0.45 - 0.25 at least: never out
    assert len(set(row[0] for row in lv)) >= 3          # it does breathe


def test_ghost_beat_is_grey_and_dimmer():
    _need_fb()
    r = _renderer()
    cap = FrameCapture()
    th = r.theme
    green = list(theme_luts("fireflies")["green"].c)
    grey = list(theme_luts("fireflies")["grey"].c)
    top = {}
    for fx, lut in (("far", green), ("far_ghost", grey)):
        _, phases, run_ms = rt.fixture(fx)
        top[fx] = -1

        def each(t):
            for q in range(F.QK, F.NQ):
                assert th.fpa[q] in lut, (fx, q)
            top[fx] = max(top[fx], _level(th, 0))
        r.reset()
        _run(r, cap, phases, 0, run_ms, 50, each)
    assert top["far"] == F.NE - 1
    assert 0 < top["far_ghost"] < F.NE - 1, top


def test_zone_change_adds_and_drops_flies_at_the_end():
    _need_fb()
    r = _renderer()
    cap = FrameCapture()
    th = r.theme
    near = rt.rs.hunt(1, intensity=0.5)
    warm = rt.rs.hunt(2, intensity=0.5)
    phases = [(0, near), (1000, warm), (2000, near)]
    rt.run(r, cap, phases, 900)
    before = [(_slot(th, F.F_CX, j), _slot(th, F.F_CY, j)) for j in range(9)]
    r.frame(rt.params_at(phases, 1000), cap, rt.T0 + 1000)
    assert th.nn == 14
    for j in range(9):                                  # the first nine carry on
        x, y = _slot(th, F.F_CX, j), _slot(th, F.F_CY, j)
        assert abs(x - before[j][0]) <= 4 and abs(y - before[j][1]) <= 4, j
    _run(r, cap, phases, 1100, 2000, 100)
    assert th.nn == 9
    for j in range(9, 14):
        assert _slot(th, F.F_DW, j) == 0                # dropped from the end


def test_changed_regions_are_the_fly_boxes():
    # a steady FAR field: the base palette is reused and only the boxes of
    # flies that changed are reported, and they cover the flies drawn
    _need_fb()
    r = _renderer()
    cap = FrameCapture()
    th = r.theme
    _, phases, run_ms = rt.fixture("far")
    widths = []

    def each(t):
        if t < 700:
            return
        assert th.dirty != ALL, t
        w = 0
        for k in range(10):
            if th.dirty & (1 << k):
                w += th.spans[2 * k + 1] - th.spans[2 * k] + 1
        widths.append(w)
    r.reset()
    _run(r, cap, phases, 0, run_ms, 100, each)
    assert max(widths) <= 6 * 17 * 2, widths            # a few 17 px boxes, never the field
    assert sum(widths) / len(widths) < 120, widths


def test_saver_and_sun_reach_the_flies():
    # the saver caps a fly at level 5, sun mode lifts it a stop (as Ripple)
    _need_fb()
    r = _renderer()
    cap = FrameCapture()
    th = r.theme
    green = list(theme_luts("fireflies")["green"].c)
    _, phases, run_ms = rt.fixture("saver")
    rt.run(r, cap, phases, run_ms)
    cap_j = (5 * 256 * 9 + 128) >> 8
    for q in range(F.QK, F.NQ):
        assert th.fpa[q] in [green[j] for j in range(cap_j + 1)], q
    _, phases, run_ms = rt.fixture("far_sun")
    rt.run(r, cap, phases, run_ms)
    assert th.fk[3] == 9
    assert th.fpa[F.QK] == green[((th.fk[0] + F.LQ[F.QK]) * 9 + 128 >> 8) + 9]


def test_menu_holds_flies_and_dims_them():
    _need_fb()
    r = _renderer()
    cap = FrameCapture()
    th = r.theme
    _, phases, _ = rt.fixture("menu")
    rt.run(r, cap, phases, 900)
    lit = th.fpa[F.NQ - 1]
    rt.run(r, cap, phases, 2400)
    d = _drawn(th)
    assert d and th.nn == 14
    assert th.fpa[F.NQ - 1] != lit                      # dimmed with the field
    for t in range(2500, 3001, 100):
        r.frame(rt.params_at(phases, t), cap, rt.T0 + t)
        assert _drawn(th) == d
        assert th.dirty == 0, (t, th.dirty)             # nothing changes at all


def test_fly_kernel_viper_agrees_with_plain():
    # where the port has viper (the watch, a 32-bit unix build) the kernel
    # must draw exactly what the plain kernel (wasm, CPython) draws
    _need_fb()
    spr, sh, cor, ch, tb, kern, py, kind = F.assets()
    assert F.kernel_agrees(py, py, tb)
    if kern is py:
        raise Skip("no viper on this port (%s)" % kind)
    assert kind == "viper"
    assert F.kernel_agrees(py, kern, tb)
    a = _renderer()
    b = _renderer()
    b.theme.kern = py
    ca = FrameCapture()
    cb = FrameCapture()
    for fx in ("hot", "found_celebrate", "searching", "pairing_calibrate", "scan_sweep"):
        _, phases, run_ms = rt.fixture(fx)
        a.reset()
        b.reset()
        t = 0
        while t <= run_ms:
            p = rt.params_at(phases, t)
            a.frame(p, ca, rt.T0 + t)
            b.frame(p, cb, rt.T0 + t)
            assert ca.buf == cb.buf, (fx, t)
            assert a.theme.dirty == b.theme.dirty and a.theme.spans == b.theme.spans, (fx, t)
            t += 100


# ---- review round fixes ------------------------------------------------------------
def test_closing_lens_lets_the_flies_drift_back():
    # an opening lens is taken at once (no fly on it); a closing one is
    # followed at <= 30 px/s, so at 10 fps no fly moves more than 4 px a
    # frame when the iris closes (it used to jump 35 px)
    _need_fb()
    r = _renderer()
    cap = FrameCapture()
    th = r.theme
    for z in range(4):
        phases = [(0, rt.rs.hunt(z)), (1000, _arrow(z)), (2000, rt.rs.hunt(z))]
        r.reset()
        last = None
        edges = []
        t = 0
        while t <= 4500:
            r.frame(rt.params_at(phases, t), cap, rt.T0 + t)
            iris = r.field.iris
            if 1000 <= t < 2000:
                assert th.edge == max(iris + T.IRIS_RIM_PX, F.CORE_R), (z, t, th.edge, iris)
            pos = _pos(th)
            if t >= 2000:
                edges.append(th.edge)
                assert _moved(last, pos) <= 4, (z, t, _moved(last, pos))
            last = pos
            t += 100
        drops = [edges[k] - edges[k + 1] for k in range(len(edges) - 1)]
        assert max(drops) == 3 and min(drops) >= 0, (z, drops)
        assert edges[0] == 64 + T.IRIS_RIM_PX and edges[-1] == F.CORE_R, (z, edges)


def test_flies_come_back_after_the_calibrate_fill():
    # the fill is the lens only while it is on: on the split's first frame
    # (the fill fading) the clearance already follows the lens in, so the
    # live flies come back from beyond the fill's edge a few px a frame
    # (they used to stay hidden for the 600 ms fade, then all jump in 92 px)
    _need_fb()
    r = _renderer()
    cap = FrameCapture()
    th = r.theme
    split = rt.rs.hunt(1, screen="PAIRING", sub="split", glyph="countdown", countdown=30)
    phases = [(0, dict(rt.rs._cal, countdown=3)), (1000, dict(rt.rs._cal, countdown=2)),
              (2000, dict(rt.rs._cal, countdown=1)), (3000, split)]
    r.reset()
    f = r.field
    last = None
    shown = []
    fill_edge = 0
    t = 0
    while t <= 6500:
        r.frame(rt.params_at(phases, t), cap, rt.T0 + t)
        if t == 2900:
            fill_edge = f.fill_r + 3
            assert f.fill_v == F.FILL_V and th.edge == fill_edge, (th.edge, fill_edge)
        if t == 3000:
            assert 0 < f.fill_v < F.FILL_V                  # fading out
            assert th.edge == fill_edge - 3, th.edge        # and the flies follow the lens in
        pos = _pos(th)
        if t >= 3000:
            assert _moved(last, pos) <= 5, (t, _moved(last, pos))
            n = 0                                           # centres on the screen, lit or not
            for j in range(th.nn):
                if 0 <= _slot(th, F.F_CX, j) < 240 and 0 <= _slot(th, F.F_CY, j) < 240:
                    n += 1
            shown.append(n)
        last = pos
        t += 100
    assert shown[0] == 0 and shown[-1] == 9, shown          # all nine are back


def test_ghost_is_read_once_per_beat():
    # whether a beat is a ghost is the ring's own flag, read once per spawn
    # and held to the next: ring_live dropping or rising mid-period changes
    # nothing until the next spawn (it used to turn every blink in flight
    # grey at once)
    _need_fb()
    r = _renderer()
    cap = FrameCapture()
    th = r.theme
    f = r.field
    top_green = theme_luts("fireflies")["green"].c[63]
    top_grey = theme_luts("fireflies")["grey"].c[63]
    live = rt.rs.hunt(1)                                    # NEAR: one beat per 1.6 s
    ghost = dict(live, ring_live=False)
    r.reset()
    spawns = []
    state = [0, 0]                                          # last spawn, frame time

    def frame(kw):
        t = state[1]
        r.frame(rt.rs.make_params(t_ms=rt.T0 + t, **kw), cap, rt.T0 + t)
        if f.last_spawn != state[0]:
            state[0] = f.last_spawn
            spawns.append(t)
        state[1] = t + 50
    while len(spawns) < 3:
        frame(live)
    while len(spawns) < 4:                                  # ring_live drops after a live spawn
        frame(ghost)
        if len(spawns) < 4:
            assert th.fpa[F.NQ - 1] == top_green and th.pm[F.P_GAMP] == 256, state
    assert th.fpa[F.NQ - 1] == top_grey                     # the ghost ring's own frame
    k = len(spawns)
    while len(spawns) == k:                                 # the next beat is a ghost: grey
        frame(ghost if state[1] < spawns[-1] + 400 else live)
        if len(spawns) == k:                                # (ring_live is back after 400 ms)
            assert th.fpa[F.NQ - 1] == top_grey and th.pm[F.P_GAMP] == GHOST_AMP, state
    assert th.fpa[F.NQ - 1] == top_green and th.pm[F.P_GAMP] == 256      # live again


def test_switched_in_the_menu_over_found():
    # a Fireflies made in the MENU over FOUND starts in FOUND with the FOUND
    # screen's intensity, not the MENU params' (§4A rule 2), in gold, and
    # nothing jumps when the MENU closes
    _need_fb()
    cap = FrameCapture()
    found = dict(rt.rs._found, sub="result", word="FOUND 1:48",
                 top_text="BUTTON: PLAY AGAIN")
    menu = dict(rt.rs._menu, sub="1v", menu_rows=rt.rs.MENU_ROWS, ramp="gold", intensity=0.3)
    phases = [(0, found), (600, menu), (1600, found)]
    gold = list(theme_luts("fireflies")["gold"].c)
    r = ThemedRenderer("ripple", overlays=False)
    last = None
    t = 0
    while t <= 2400:
        if t == 1000:
            r.set_theme("fireflies")
        r.frame(rt.params_at(phases, t), cap, rt.T0 + t)
        th = r.theme
        if t >= 1000:
            assert th.name == "fireflies" and th.m == M_FOUND and th.nn == 22, (t, th.m, th.nn)
            assert th.ib == (F.NI - 1) * F.NE, (t, th.ib)  # I = 1.0 under the MENU too
            for q in range(F.QK, F.NQ):
                assert th.fpa[q] in gold, (t, q)
            pos = _pos(th)
            assert len(pos) == 22
            if last is not None:
                assert _moved(last, pos) <= 4, (t, _moved(last, pos))
            last = pos
        t += 100


def test_halo_crossfades_with_the_moment():
    # on a moment change floor and halo crossfade over the field's 600 ms
    # (in_out_cubic, §5.3) from what was shown (they used to ease over
    # ~150 ms: 1.4 levels in one frame into a scan)
    _need_fb()
    r = _renderer()
    cap = FrameCapture()
    th = r.theme
    near = rt.rs.hunt(2, dist_band="~10")
    scan = dict(rt.rs._scan, sub="sweep", glyph="turn", intensity=0.6, glow_r_px=12,
                sweep=(135, rt.rs.BINS, 4, False))
    phases = [(0, near), (1000, scan), (2000, near)]
    r.reset()
    g0 = g1 = 0
    t = 0
    while t <= 2800:
        r.frame(rt.params_at(phases, t), cap, rt.T0 + t)
        assert th.fl == F.FL_A
        if t == 900:
            g0 = th.gl
        if 1000 <= t <= 1800:                               # into the scan: the live mirror
            tgt = F.MG_A + ((F.MG_B * r._iq) >> 8)
            e = ease(EASE_IOC, t - 1000, F.XF_MS)
            want = g0 + (((tgt - g0) * e) >> 8)
            assert th.gl == (want + 4) & ~7, (t, th.gl, want)
        if t == 1900:
            g1 = th.gl
        if t == 2000:
            assert th.gl == g1                              # out again: from what was shown
        if t == 2100:
            assert abs(th.gl - g1) < 64, (th.gl, g1)        # ... easing (1/4 level a frame)
        if t >= 2600:                                       # ... to the halo, 0.9 I
            assert th.gl == (((F.HALO * r._iq) >> 8) + 4) & ~7, (t, th.gl)
        t += 100


def test_flies_glide_onto_the_found_ring():
    # HOT -> FOUND: the flies take the ring's places in their angular order
    # (breathing 0.3 rad apart by place) and glide there over the 400 ms gold
    # crossfade, never onto the lens (they used to jump up to 160 px); a
    # wake into FOUND has no glide
    _need_fb()
    r = _renderer()
    cap = FrameCapture()
    th = r.theme
    found = dict(rt.rs._found, sub="celebrate", word="FOUND")
    phases = [(0, rt.rs.hunt(3, dist_band="<3")), (1000, dict(found, burst=True)), (1100, found)]
    rt.run(r, cap, phases, 900)
    before = _pos(th)
    ang = [_slot(th, F.F_PA, j) for j in range(22)]
    assert len(before) == 22
    r.frame(rt.params_at(phases, 1000), cap, rt.T0 + 1000)
    assert th.gliding and _pos(th) == before               # the FOUND frame: where they were
    rk = [_slot(th, F.F_RK, j) for j in range(22)]
    order = sorted(range(22), key=lambda j: (ang[j], j))
    assert [rk[j] for j in order] == list(range(22)), rk
    for j in range(22):
        assert _slot(th, F.F_EO, j) == (rk[j] * F.STEP_FOUND_E) & 1023
    last = before
    t = 1100
    while t <= 1700:
        r.frame(rt.params_at(phases, t), cap, rt.T0 + t)
        pos = _pos(th)
        assert _moved(last, pos) <= 32, (t, _moved(last, pos))
        for x, y in pos.values():
            assert (x - 119.5) ** 2 + (y - 119.5) ** 2 >= 18 ** 2, (t, x, y)
        if t >= 1400:
            assert not th.gliding
            for x, y in pos.values():
                d = math.sqrt((x - 119.5) ** 2 + (y - 119.5) ** 2)
                assert 72.5 <= d <= 95.5, (t, x, y)
        last = pos
        t += 100
    # places 1/22 turn apart, in the order the flies came in
    a = [_slot(th, F.F_PA, j) for j in range(22)]
    for j in range(22):
        k = order[(rk[j] + 1) % 22]
        gap = (a[k] - a[j]) & 1023
        assert 44 <= gap <= 49, (j, k, gap)
    _, phases, _ = rt.fixture("found_result")             # a wake into FOUND: no glide
    r.reset()

    def each(t):
        assert not th.gliding, t
    _run(r, cap, phases, 0, 600, 100, each)


def test_saver_scales_the_blink_amplitude():
    # battery saver: pulse_amp x 0.7 (§8) on the blink, eased in and out by
    # at most 256 per 600 ms (no flash)
    _need_fb()
    r = _renderer()
    cap = FrameCapture()
    th = r.theme
    near = rt.rs.hunt(1)
    saver = dict(near, status=(10, 64, 3, True, False))
    phases = [(0, near), (1000, saver), (2500, near)]
    r.reset()
    sv = []
    t = 0
    while t <= 3500:
        r.frame(rt.params_at(phases, t), cap, rt.T0 + t)
        sv.append((t, th.sv, th.pm[F.P_GAMP]))
        t += 100
    step = (256 * 100) // F.XF_MS + 1
    for k in range(1, len(sv)):
        assert abs(sv[k][1] - sv[k - 1][1]) <= step, sv[k]
    for t, v, g in sv:
        if t < 1000 or t >= 2800:
            assert v == 256 and g == 256, (t, v, g)
        if 1300 <= t < 2500:
            assert v == F.SAVER_PU == 179 and g == 179, (t, v, g)
    peak = {}
    for name, kw in (("on", near), ("saver", saver)):
        r.reset()
        hi = -1
        t = 0
        while t <= 4000:
            r.frame(rt.rs.make_params(t_ms=rt.T0 + t, **kw), cap, rt.T0 + t)
            hi = max(hi, _level(th, 0))
            t += 20
        peak[name] = hi
    assert peak["saver"] < peak["on"] == F.NE - 1, peak


def test_clock_wraps_on_whole_cycles():
    # the theme clock wraps on whole breaths and whole cycles of the speck
    # trip angles, derived from the tokens (it was a hand-made constant)
    from ui.themes import fireflies as FF
    a, b = FF.BREATHE_MS, FF.LISTEN_MS * FF.N_SANG
    g = a
    k = b
    while k:
        g, k = k, g % k
    assert FF.CLK_WRAP == a // g * b < (1 << 30)
    if not HAVE_FB:
        return
    # across the wrap the specks and the breathing carry on as if it were not
    # there: a theme whose clock is 300 ms short of the wrap shows, frame for
    # frame, the levels and specks one shows 300 ms earlier
    cap = FrameCapture()
    for fx in ("searching", "found_result"):
        _, phases, _ = rt.fixture(fx)
        ra = _renderer()
        rb = _renderer()
        seen = {}
        t = 0
        while t <= 2000:
            p = rt.params_at(phases, t)
            ra.frame(p, cap, rt.T0 + t)
            rb.frame(p, cap, rt.T0 + t)
            if t == 0:
                rb.theme.clk = F.CLK_WRAP - 300
                rb.theme.m_t0 = 0
            ta = ra.theme
            tb = rb.theme
            seen[t] = [_level(ta, j) for j in range(ta.nn)]
            if fx == "searching":
                seen[t] = [(_slot(ta, F.F_CX, j), _slot(ta, F.F_CY, j), _slot(ta, F.F_CS, j))
                           for j in range(3)]
                cur = [(_slot(tb, F.F_CX, j), _slot(tb, F.F_CY, j), _slot(tb, F.F_CS, j))
                       for j in range(3)]
            else:
                cur = [_level(tb, j) for j in range(tb.nn)]
            if t >= 700:                                    # (the iris has opened in both)
                assert cur == seen[t - 300], (fx, t, cur, seen[t - 300])
            t += 100
        assert rb.theme.clk == 2000 - 300                   # it did wrap


def test_shared_helpers_are_the_shared_ones():
    from ui import field
    from ui.themes import base
    from ui.themes import fireflies as FF
    assert FF._aligned is field._aligned
    assert FF.ALL == base.ALL
    if HAVE_FB:
        assert not hasattr(_renderer().theme, "np")


_LOADED = ("_SPR", "_COR", "_SH", "_CH", "_nspr", "_tb", "_tbs", "_PYK", "_VK", "_vtried",
           "_KERN", "_KIND")


def _forget():
    """Make the module load from scratch (fresh objects: themes already made
    keep theirs)."""
    F._SPR = [None] * F.N_SPR
    F._COR = [None] * F.N_SPR
    F._SH = bytearray(F.N_SPR)
    F._CH = bytearray(F.N_SPR)
    F._nspr = 0
    F._tb = None
    F._tbs = 0
    F._PYK = None
    F._VK = None
    F._vtried = False
    F._KERN = None
    F._KIND = None


def _queued(r, cap, phases):
    """Frames until the queued theme takes over."""
    n = 0
    while r.loading is not None:
        r.frame(rt.params_at(phases, n * 100), cap, rt.T0 + n * 100)
        n += 1
        assert n < 100, "load never finished"
    return n


def test_staged_load_resumes_and_draws_whole():
    # loading through ThemedRenderer.queue_theme: the old theme draws until
    # the last step, a load abandoned half way resumes without redoing a
    # step, and the theme then draws exactly as one loaded whole
    _need_fb()
    saved = [getattr(F, k) for k in _LOADED]
    _, phases, _ = rt.fixture("hot")
    ca = FrameCapture()
    cb = FrameCapture()
    cc = FrameCapture()
    try:
        _forget()
        a = ThemedRenderer("ripple", overlays=False)
        a.queue_theme("fireflies")
        full = _queued(a, ca, phases)
        assert a.theme.name == "fireflies" and full >= 12, full
        _forget()
        b = ThemedRenderer("ripple", overlays=False)
        b.queue_theme("fireflies")
        for k in range(7):                                  # the import and six load steps
            b.frame(rt.params_at(phases, k * 100), cb, rt.T0 + k * 100)
        assert b.loading == "fireflies" and b.theme.name == "ripple"
        assert F._nspr == F.N_SPR and F._tbs == 1 and F._PYK is None
        b.set_theme("ripple")                               # abandoned
        assert b.loading is None
        b.queue_theme("fireflies")
        assert _queued(b, cb, phases) == full - 6           # the six are not redone
        # what it built is what the first load built
        assert bytes(F._SH) == bytes(saved[2]) and bytes(F._CH) == bytes(saved[3])
        assert list(F._tb) == list(saved[5]) and F._KIND == saved[11]
        for n in range(F.N_SPR):
            k = 1 if F._SH[n] == F.NONE else 2 * F._SH[n] + 1
            for y in range(k):
                for x in range(k):
                    assert F._SPR[n].pixel(x, y) == saved[0][n].pixel(x, y), (n, x, y)
        c = ThemedRenderer("fireflies", overlays=False)
        for fx in ("hot", "found_celebrate", "searching"):
            _, ph, ms = rt.fixture(fx)
            rt.run(a, ca, ph, ms)
            rt.run(b, cb, ph, ms)
            rt.run(c, cc, ph, ms)
            assert ca.buf == cc.buf and cb.buf == cc.buf, fx
    finally:
        for k, v in zip(_LOADED, saved):
            setattr(F, k, v)
