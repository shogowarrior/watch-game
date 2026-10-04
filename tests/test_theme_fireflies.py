"""ui/themes/fireflies.py: the Fireflies theme (ui-spec §4A "Fireflies").

The generic theme contract (dirty regions, allocation, lens, MENU freeze)
is in tests/test_themes.py. These check the numbers of the Fireflies
paragraph: flies per zone, the blink locked to the ring spawn with the
zone's sync, the orbit shrinking with I, the moments (listening specks,
still glow, frozen scan, gold FOUND ring), the ghost beat in grey, flies
off the lens, tight changed regions, and the fly kernel's viper build
agreeing with its plain one. Frames need framebuf (MicroPython):

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
    from ui.renderer import FrameCapture
    from ui.themes import ThemedRenderer
    from ui.themes import fireflies as F
    from ui.themes.base import ALL, theme_luts

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
