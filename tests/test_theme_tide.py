"""Tide theme (ui-spec §4A Tide, ui/themes/tide.py): water fills the dish.

The token check runs on any runtime; everything else needs framebuf
(MicroPython; ui.themes imports the renderer):

    node tools/mpy/run.mjs tests/runner.py test_theme_tide

Frames run at 10 fps (tools/render_themes.py fixtures and params) with the
overlays off, so every pixel compared is the theme's own layer.
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
    from tools import render_snapshots as rs
    from tools import render_themes as rt
    from ui.renderer import FrameCapture
    from ui.themes import ThemedRenderer
    from ui.themes import tide as TD
    from ui.themes.base import M_FOUND, M_LISTEN, M_LIVE, NS, SH, _disc, theme_luts

W = 240
ROW = 480
P = T.THEME_PARAMS["tide"]
BAND_ZONE = {"<3": 3, "~5": 3, "~10": 2, "~20": 1, "~40": 0, "60+": 0}


def _need_fb():
    if not HAVE_FB:
        raise Skip("framebuf is MicroPython-only")


def _px(buf, x, y):
    o = (y * W + x) * 2
    return buf[o] | (buf[o + 1] << 8)


def _in(c, lut):
    for j in range(64):
        if lut[j] == c:
            return True
    return False


def _run(r, cap, phases, run_ms, each=None, t0=0, reset=True):
    """Frames of ``phases`` from ``t0`` to ``t0 + run_ms`` at 10 fps."""
    if reset:
        r.reset()
    t = 0
    while t <= run_ms:
        r.frame(rt.params_at(phases, t), cap, rt.T0 + t0 + t)
        if each is not None:
            each(t0 + t, r)
        t += rt.STEP_MS


def _y(level):
    return int(W * (1.0 - level) + 0.5)


def _changed_rows(prev, cur):
    rows = []
    mp = memoryview(prev)
    mc = memoryview(cur)
    for y in range(W):
        o = y * ROW
        if mp[o:o + ROW] != mc[o:o + ROW]:
            rows.append(y)
    return rows


# ---- tokens (any runtime) ---------------------------------------------------------------
def test_tokens_match_spec():
    # ui-spec §4A Tide paragraph
    assert P["levels"] == {"<3": 0.86, "~5": 0.74, "~10": 0.62, "~20": 0.50, "~40": 0.38,
                           "60+": 0.26}
    assert P["level_found"] == 0.92 and P["level_listen"] == 0.20 and P["ease_ms"] == 450
    a1, a2 = P["wave_amp_px"]
    assert tuple(a1) == (1.5, 1.1) and tuple(a2) == (0.6, 0.5)
    assert tuple(P["wave_px_s"]) == (14, 12) and tuple(P["swell_px"]) == (5, 3)
    assert P["swell_ms"] == 240 and tuple(P["bubbles"]) == (2, 2)
    assert tuple(P["bubble_px_s"]) == (22, 12)
    assert P["drip_ms"] == 3000 and P["shimmer_band_px"] == 24
    # the closer the band, the higher the water
    lv = [P["levels"][b] for b in T.BAND_LABELS]
    for k in range(len(lv) - 1):
        assert lv[k] > lv[k + 1], lv


# ---- tables (MicroPython: the module imports the renderer) ------------------------------
def test_tables():
    _need_fb()
    for b, lv in P["levels"].items():
        assert TD.LEVEL_Y[b] == int(W * (1 - lv) * 256 + 0.5), b
    assert TD.Y_FOUND == int(W * (1 - P["level_found"]) * 256 + 0.5)
    assert TD.Y_LISTEN == int(W * (1 - P["level_listen"]) * 256 + 0.5)
    for z in range(4):
        assert TD.A1Q[z] == int((1.5 + 1.1 * z) * 256 + 0.5)
        assert TD.A2Q[z] == int((0.6 + 0.5 * z) * 256 + 0.5)
        assert TD.WAVE_V[z] == 14 + 12 * z
        assert TD.SWELL_Q[z] == int((5 + 3 * z) * 256 + 0.5)
        assert TD.BUB_N[z] == 2 + 2 * z and TD.BUB_V[z] == 22 + 12 * z
    for d in (1, 50, 100, 250):
        assert abs(TD.EASE_K[d] - 256 * (1 - math.exp(-d / 450.0))) <= 1, d
    assert TD.SWX[0] == 256 and abs(TD.SWX[30] - 256 * math.exp(-240 / 240.0)) <= 1
    # vignette zones: none inside r 88, then monotone out to the corner
    for i in range(89):
        assert TD.ZQ[i] == 0, i
    for i in range(1, len(TD.ZQ)):
        assert TD.ZQ[i] >= TD.ZQ[i - 1], i
    assert TD.ZQ[168] == 48
    # water: brightest under the surface, darker with depth
    assert TD.WL[0] == int(256 * 4.4 + 0.5)
    for d in range(1, 241):
        assert TD.WL[d] <= TD.WL[d - 1]


# ---- frames ------------------------------------------------------------------------------
def test_level_per_band():
    # the surface sits at the band's fraction of the screen height
    _need_fb()
    r = ThemedRenderer("tide", overlays=False)
    cap = FrameCapture()
    air = None
    for b in T.BAND_LABELS:
        z = BAND_ZONE[b]
        _run(r, cap, [(0, rs.hunt(z, dist_band=b))], 600)
        th = r.theme
        y = _y(P["levels"][b])
        assert abs(th.ypx - y) <= 1, (b, th.ypx, y)
        # air above the band, water (brighter) under it, away from the lens
        a = _px(cap.buf, 20, y - 30)
        wv = _px(cap.buf, 120, y + 20) if y + 20 < W else _px(cap.buf, 120, W - 1)
        assert a == th.c_air, (b, hex(a))
        assert wv != th.c_air, b
        if air is None:
            air = a
        assert a == air, b                        # one air colour at every level


def test_found_listen_and_last_level():
    _need_fb()
    r = ThemedRenderer("tide", overlays=False)
    cap = FrameCapture()
    for fx, want in (("found_result", _y(0.92)), ("searching", _y(0.20)),
                     ("pairing_looking", _y(0.20)), ("link_lost", _y(0.50)),
                     ("scan_sweep", _y(0.62))):       # fresh, no band: the zone's (WARM: ~10)
        _, phases, run_ms = rt.fixture(fx)
        _run(r, cap, phases, 600)
        assert abs(r.theme.ypx - want) <= 1, (fx, r.theme.ypx, want)
    # otherwise the last level: HOT ~5, then SCANNING (no band) keeps it
    _run(r, cap, [(0, rs.hunt(3, dist_band="~5"))], 1000)
    _, phases, _ = rt.fixture("scan_sweep")
    _run(r, cap, phases, 1500, t0=1100, reset=False)
    assert abs(r.theme.ypx - _y(0.74)) <= 1, r.theme.ypx
    # MENU holds it too
    _run(r, cap, [(0, rs.hunt(2, dist_band="~10"))], 1500)
    _, phases, _ = rt.fixture("menu")
    _run(r, cap, [(0, phases[1][1])], 1000, t0=1600, reset=False)
    assert abs(r.theme.ypx - _y(0.62)) <= 1, r.theme.ypx


def test_band_change_is_one_eased_step():
    # 450 ms time constant: one quick step, monotone, never past the target
    _need_fb()
    r = ThemedRenderer("tide", overlays=False)
    cap = FrameCapture()
    _run(r, cap, [(0, rs.hunt(0, dist_band="~40"))], 1000)
    y0 = r.theme.ypx
    y1 = _y(0.50)
    ys = []
    _run(r, cap, [(0, rs.hunt(1, dist_band="~20"))], 2500, t0=1100, reset=False,
         each=lambda t, rr: ys.append(rr.theme.yq))
    assert y0 == _y(0.38)
    for k in range(1, len(ys)):
        assert ys[k] <= ys[k - 1], ys               # rising water: y only falls
        assert ys[k] >= y1 * 256 - 128, ys          # never past the target
    # ys[k] is k + 1 frames (100 ms each) after the change: after 500 ms,
    # 1 - e^(-500/450) = 67 % of the step is done
    q = (y0 * 256 - ys[4]) / ((y0 - y1) * 256.0)
    assert 0.64 <= q <= 0.70, q
    assert (ys[20] + 128) >> 8 == y1, ys            # settled within 2 s
    assert ys[-1] == y1 * 256 or abs(ys[-1] - y1 * 256) < 128


def test_wake_snaps_level():
    _need_fb()
    r = ThemedRenderer("tide", overlays=False)
    cap = FrameCapture()
    _run(r, cap, [(0, rs.hunt(0, dist_band="~40"))], 1000)
    # a gap longer than WAKE_MS: the next frame is the current state, no easing
    r.frame(rs.make_params(t_ms=rt.T0 + 2000, **rs.hunt(3, dist_band="<3")), cap, rt.T0 + 2000)
    assert r.theme.ypx == _y(0.86), r.theme.ypx
    assert r.theme.dirty == (1 << NS) - 1


def test_swell_locked_to_ring_spawn():
    # the beat is the field's ring spawn: the swell peaks (5 + 3z px) on the
    # spawn frame, once per zone period, and falls back with 240 ms
    _need_fb()
    r = ThemedRenderer("tide", overlays=False)
    cap = FrameCapture()
    for z in range(4):
        period = T.ZONE_PERIOD_MS[z]
        peaks = []
        sws = []

        def each(t, rr):
            th = rr.theme
            sws.append(th.sw)
            if rr.field.last_spawn == rt.T0 + t:
                assert th.sw == TD.SWELL_Q[z], (z, t, th.sw)
                peaks.append(t)
            elif th.sw == TD.SWELL_Q[z]:
                raise AssertionError(("peak off the spawn", z, t))

        _run(r, cap, [(0, rs.hunt(z))], 2 * period + 100, each=each)
        assert len(peaks) >= 2, (z, peaks)
        for k in range(1, len(peaks)):
            assert peaks[k] - peaks[k - 1] == period, (z, peaks)
        # 100 ms after a peak: e^(-100/240) of it
        i = peaks[1] // 100 + 1
        want = TD.SWELL_Q[z] * math.exp(-100 / 240.0)
        assert abs(sws[i] - want) <= 0.03 * TD.SWELL_Q[z] + 2, (z, sws[i], want)


def test_waves_and_bubbles_per_zone():
    _need_fb()
    r = ThemedRenderer("tide", overlays=False)
    cap = FrameCapture()
    for z in range(4):
        _run(r, cap, [(0, rs.hunt(z))], 500)
        th = r.theme
        assert th.mom == M_LIVE and th.nb == 2 + 2 * z, (z, th.nb)
        ph1 = th.ph1
        bp = list(th.bpos)
        r.frame(rs.make_params(t_ms=rt.T0 + 600, **rs.hunt(z)), cap, rt.T0 + 600)
        # wave 1 moves (14 + 12 z) px/s: its phase moves that many px of 1/38 rad
        d = (ph1 - th.ph1) % TD.PH_WRAP
        assert d == (100 * (14 + 12 * z) * TD.R1) // 1000, (z, d)
        # bubbles rise at (22 + 12 z) px/s (Q8 px; a wrap restarts at the bottom)
        adv = ((22 + 12 * z) * 100 * 256) // 1000
        for j in range(th.nb):
            assert th.bpos[j] - bp[j] == adv or th.bpos[j] < bp[j], (z, j)


def test_ghost_beat_is_grey():
    _need_fb()
    r = ThemedRenderer("tide", overlays=False)
    cap = FrameCapture()
    grey = theme_luts("tide")["grey"].c
    green = theme_luts("tide")["green"].c
    _, phases, run_ms = rt.fixture("far_ghost")
    seen = []
    _run(r, cap, phases, 2400, each=lambda t, rr: seen.append(rr.theme.sw))
    th = r.theme
    assert th.ghost_now == 1
    assert _in(th.c_foam2, grey) and not _in(th.c_foam2, green), hex(th.c_foam2)
    ghost_peak = (TD.SWELL_Q[0] * TD.GHOST_Q) >> 8
    assert max(seen) == ghost_peak, (max(seen), ghost_peak)
    _run(r, cap, [(0, rs.hunt(0, dist_band="~40"))], 500)
    assert r.theme.ghost_now == 0 and _in(r.theme.c_foam2, green)


def test_listening_drip():
    # calm water and a drop every 3 s, falling (as a streak) then landing
    _need_fb()
    r = ThemedRenderer("tide", overlays=False)
    cap = FrameCapture()
    _, phases, _ = rt.fixture("searching")
    heads = []

    def each(t, rr):
        th = rr.theme
        assert th.mom == M_LISTEN and th.nb == 0
        o = 4 * TD.B_DROP
        if th.box[o + 3] >= 0:
            heads.append((t, th.dsx, th.dsy, th.dst))

    _run(r, cap, phases, 6000, each=each)
    assert len(heads) >= 20, len(heads)
    sides = []
    for k in range(1, len(heads)):
        t0, x0, y0, s0 = heads[k - 1]
        t1, x1, y1, s1 = heads[k]
        if x1 == x0 and t1 - t0 == 100:
            assert y1 >= y0, heads                  # falls
            assert s1 <= y0 + 1, heads              # the streak covers the step
            assert y1 - y0 <= 40
        if not sides or sides[-1][1] != x1:
            sides.append((t1, x1))
    # one drop every drip_ms, alternating sides (each first shows once its
    # head clears the top edge, which depends on its landing row: +-2 frames)
    assert len(sides) >= 2, sides
    assert abs(sides[1][0] - sides[0][0] - P["drip_ms"]) <= 200, sides
    assert sides[0][1] + sides[1][1] == W - 1, sides


def test_found_gold_glassy_shimmer_band():
    # FOUND: gold water at 0.92; after the crossfade only the surface band and
    # the shimmer (24 px under the surface) change
    _need_fb()
    r = ThemedRenderer("tide", overlays=False)
    cap = FrameCapture()
    gold = theme_luts("tide")["gold"].c
    _, phases, _ = rt.fixture("found_result")
    prev = bytearray(W * W * 2)
    rows = []

    def each(t, rr):
        if t >= 800:
            rows.extend(_changed_rows(prev, cap.buf))
            th = rr.theme
            for k in range(NS):
                y = k * SH
                if not (th.dirty & (1 << k)):
                    assert y > th.ypx + 28 or y + SH < th.ypx - 6, (t, k)
        prev[:] = cap.buf

    _run(r, cap, phases, 2500, each=each)
    th = r.theme
    assert th.mom == M_FOUND and th.ypx == _y(0.92)
    assert _in(_px(cap.buf, 120, 60), gold), hex(_px(cap.buf, 120, 60))
    assert rows, "the shimmer moves"
    ys = th.ypx
    for y in rows:
        assert ys - 6 <= y <= ys + P["shimmer_band_px"] + 4, (y, ys)


def test_found_entry_swell_not_after_wake():
    _need_fb()
    r = ThemedRenderer("tide", overlays=False)
    cap = FrameCapture()
    _run(r, cap, [(0, rs.hunt(3, dist_band="<3"))], 500)
    _, phases, _ = rt.fixture("found_result")
    sw = []
    _run(r, cap, phases, 1600, t0=600, reset=False, each=lambda t, rr: sw.append(rr.theme.sw))
    assert sw[0] == TD.FOUND_SWELL, sw[:3]          # FOUND settles with a swell...
    assert sw[1] < sw[0] and sw[-1] == 0, sw         # ...that dies within 1.5 s
    _run(r, cap, phases, 300)                         # FOUND from a wake: no intro
    assert r.theme.sw == 0


def test_calm_moments_redraw_few_strips():
    # §4A rule 6: the cheapest theme to redraw; calm moments touch a few strips
    _need_fb()
    r = ThemedRenderer("tide", overlays=False)
    cap = FrameCapture()
    for fx, most in (("far", 4), ("searching", 4), ("link_lost", 4), ("scan_sweep", 1),
                     ("found_result", 2), ("pairing_seen", 2)):
        _, phases, run_ms = rt.fixture(fx)
        n = [0, 0]

        def each(t, rr):
            if t >= 700:
                d = rr.theme.dirty
                c = 0
                for k in range(NS):
                    c += (d >> k) & 1
                n[0] += c
                n[1] += 1

        _run(r, cap, phases, run_ms, each=each)
        assert n[0] <= most * n[1], (fx, n[0] / n[1])


def test_blit_matches_framebuf_path():
    # the viper half-map kernel (32-bit ports with viper) or the framebuf
    # palette blit must equal a straight palette blit of the full map with the
    # disc stack drawn on it as Theme.draw_discs draws it (four quadrants)
    _need_fb()
    r = ThemedRenderer("tide", overlays=False)
    cap = FrameCapture()
    want = bytearray(W * 60 * 2)
    wfb = framebuf.FrameBuffer(want, W, 60, framebuf.RGB565)
    seen = [0] * TD.NL
    for fx, ms in (("hot", 800), ("warm_arrow", 1500), ("pairing_calibrate", 1500)):
        _, phases, _ = rt.fixture(fx)
        _run(r, cap, phases, ms)
        th = r.theme
        m = bytearray(th.map0 if th.map0 is not None else th.idx)
        mfb = framebuf.FrameBuffer(m, W, W, framebuf.GS8)
        for i in range(TD.NL):
            if th.lay[i] > 0:
                seen[i] += 1
                _disc(mfb, th.lay[i], TD.I_DISC + i)
        for k in range(4):
            th.blit(k * 60, r.bands[k], r.band_fbs[k])
            wfb.blit(mfb, 0, -k * 60, -1, th.pal)
            assert bytes(r.bands[k]) == bytes(want), (fx, th.kind, k)
    assert th.kind in ("viper", "framebuf"), th.kind
    assert seen[1] and seen[2] and seen[3] and seen[4], seen   # fill, rim, iris, core dot
    # the map is left-right symmetric (the kernel mirrors it)
    m = th.idx
    for y in (0, 37, 119, 120, 201, 239):
        for x in range(0, 120, 7):
            assert m[y * W + x] == m[y * W + 239 - x], (x, y)


def test_lens_stays_whole_over_the_surface():
    # the disc stack lives in the map; where the surface band (air polygon,
    # foam) crosses it, it is redrawn, and bubbles and the drop never draw
    # over it: with the overlays off the lens is one colour inside, rim around
    _need_fb()
    r = ThemedRenderer("tide", overlays=False)
    cap = FrameCapture()
    crossed = [0]

    def each(t, rr):
        th = rr.theme
        ir = th.f.iris
        if t < 500 or ir <= 0:
            return
        if th.air[1] <= 119 + ir and th.ymax >= 120 - ir:
            crossed[0] += 1
        rim = th.pal_arr[TD.I_DISC + 2]
        for y in range(120 - ir, 120 + ir, 2):
            for x in range(120 - ir, 120 + ir, 3):
                d2 = (2 * x - 239) ** 2 + (2 * y - 239) ** 2
                if d2 < (2 * ir - 3) ** 2:
                    assert _px(cap.buf, x, y) == T.THEME_IRIS["tide"], (t, x, y)
                elif (2 * ir + 2) ** 2 < d2 < (2 * ir + 2 * T.IRIS_RIM_PX - 3) ** 2:
                    assert _px(cap.buf, x, y) == rim, (t, x, y)

    for fx in ("warm_arrow", "searching", "link_lost", "scan_sweep"):
        _, phases, run_ms = rt.fixture(fx)
        _run(r, cap, phases, run_ms, each=each)
    for z, band in ((1, "~20"), (3, "~5")):                # surface through the lens
        _run(r, cap, [(0, rs.hunt(z, sub="walk", glyph="arrow", arrow_deg=30, cone_deg=31,
                                  arrow_style="solid_b", dist_band=band))], 2500, each=each)
    assert crossed[0] >= 40, crossed


def test_fast_bubbles_leave_a_trail():
    # §4A rule 5: at 10 fps a HOT bubble (58 px/s) rises 5-6 px a frame, so it
    # draws a trail down to where it was; FAR bubbles (2.2 px a frame) do not
    _need_fb()
    r = ThemedRenderer("tide", overlays=False)
    cap = FrameCapture()
    for z, band, want in ((3, "~5", True), (0, "~40", False)):
        last = {}
        trails = [0]

        def each(t, rr):
            th = rr.theme
            for j in range(th.nb):
                o = 4 * j
                if th.box[o + 3] < 0:
                    last.pop(j, None)
                    continue
                top = th.box[o + 1]
                rad = (th.box[o + 2] - th.box[o]) >> 1
                tl = th.btl[j]
                if j in last and 0 < last[j] - top < SH:
                    st = last[j] - top
                    if st > TD.STREAK_PX:
                        assert tl == st - rad or tl == 0, (t, j, st, tl)    # 0: tip on the lens
                        if tl:
                            trails[0] += 1
                            x = th.box[o] + rad
                            y = th.box[o + 3] + tl
                            free = True                     # no other bubble over the tip
                            for k in range(th.nb):
                                b = 4 * k
                                if k != j and th.box[b + 3] >= 0:
                                    free = free and not (th.box[b] <= x <= th.box[b + 2] and
                                                         th.box[b + 1] <= y <= th.box[b + 3])
                            if free and y < W:
                                assert _px(cap.buf, x, y) == th.c_btr, (t, j)
                    else:
                        assert tl == 0, (t, j, st, tl)
                last[j] = top

        _run(r, cap, [(0, rs.hunt(z, dist_band=band))], 3000, each=each)
        assert (trails[0] > 10) == want, (z, trails)
