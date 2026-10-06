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
    from ui.themes.base import (ALL, DT_MAX, M_FOUND, M_LISTEN, M_LIVE, NS, SH, WAKE_MS, _disc,
                                theme_luts)

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


def _pristine(th):
    """The theme's index map without the disc stack, as a full 240x240 map:
    the framebuf path keeps it (``map0``); the viper path keeps only the half
    maps (row y < 120 is ``qt0`` row y, row y >= 120 ``qb0`` row 239 - y, each
    the right half, mirrored)."""
    if th.map0 is not None:
        return bytearray(th.map0)
    m = bytearray(W * W)
    for y in range(W):
        src = th.qt0 if y < 120 else th.qb0
        o = (y if y < 120 else 239 - y) * 120
        for x in range(120):
            v = src[o + x]
            m[y * W + 120 + x] = v
            m[y * W + 119 - x] = v
    return m


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
    for _ in TD.load():                        # idempotent: tables already built are kept
        pass
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
    # one entry per ms of frame time up to DT_MAX (Theme.clock clamps dt there)
    assert len(TD.EASE_K) == DT_MAX + 1, len(TD.EASE_K)
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
    t = 1000 + WAKE_MS + 1
    r.frame(rs.make_params(t_ms=rt.T0 + t, **rs.hunt(3, dist_band="<3")), cap, rt.T0 + t)
    assert r.theme.ypx == _y(0.86), r.theme.ypx
    assert r.theme.dirty == ALL


def test_missed_slot_eases_dt_max():
    # a gap between DT_MAX and WAKE_MS (a missed slot at the 5 fps lock is
    # 400 ms) is no wake: the level eases by DT_MAX of its time constant
    _need_fb()
    r = ThemedRenderer("tide", overlays=False)
    cap = FrameCapture()
    _run(r, cap, [(0, rs.hunt(0, dist_band="~40"))], 1000)
    y0 = r.theme.yq
    y1 = TD.LEVEL_Y["<3"]
    t = 1000 + 400
    r.frame(rs.make_params(t_ms=rt.T0 + t, **rs.hunt(3, dist_band="<3")), cap, rt.T0 + t)
    th = r.theme
    assert not th.wake and th.dt == DT_MAX, (th.wake, th.dt)
    assert th.yq == y0 + (((y1 - y0) * TD.EASE_K[DT_MAX]) >> 8), (th.yq, y0, y1)
    assert th.dirty == ALL                          # the surface row moved: palette rebuilt


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
    # head clears the top edge, which depends on its landing row: +-2 frames;
    # the first one starts half-way through its fall)
    assert len(sides) >= 3, sides
    assert abs(sides[2][0] - sides[1][0] - P["drip_ms"]) <= 200, sides
    assert sides[0][1] + sides[1][1] == W - 1 and sides[2][1] == sides[0][1], sides


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
    # disc stack drawn on it as base._disc draws it (four quadrants)
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
        m = _pristine(th)
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
    m = th.map0
    if m is None:
        return                                      # the half maps are symmetric by design
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


def test_viper_path_drops_the_full_map():
    # the kernel reads only the two half maps and _burn draws into them, so
    # the full 57.6 KB map and its framebuf go once the kernel agrees; the
    # framebuf path keeps the full map and its pristine copy
    _need_fb()
    r = ThemedRenderer("tide", overlays=False)
    th = r.theme
    if th.kind == "viper":
        assert th.idx is None and th.map_fb is None and th.map0 is None
        assert len(th.qt0) == 120 * 120 and len(th.qb0) == 120 * 120
    else:
        assert th.kind == "framebuf", th.kind
        assert len(th.idx) == W * W and th.map0 is not None and th.qt is None
    cap = FrameCapture()
    for fx in ("hot", "pairing_calibrate", "found_result"):
        _, phases, run_ms = rt.fixture(fx)
        _run(r, cap, phases, run_ms)               # every path still draws (no idx needed)


def _menu_scenario(under, cap, until=4000):
    """Ripple under ``under``, the MENU over it from 1100 ms (its params carry
    no band, as the game's), tide chosen in the MENU at 1500 ms, the MENU
    closed at 2600 ms; (t, ypx, sw, mom, dirty) of every tide frame."""
    r = ThemedRenderer("ripple", overlays=False)
    menu = dict(under, screen="MENU", sub="1v", menu_rows=rs.MENU_ROWS, dist_band=None,
                word=None, top_text=None, glyph="glow", speed_px_s=80)
    out = []
    for t in range(0, until + 1, 100):
        kw = menu if 1100 <= t < 2600 else under
        if t == 1500:
            r.set_theme("tide")
        r.frame(rs.make_params(t_ms=rt.T0 + t, **kw), cap, rt.T0 + t)
        th = r.theme
        if th.name == "tide":
            out.append((t, th.ypx, th.sw, th.mom, th.dirty))
    return out


def test_theme_made_in_the_menu():
    # §4A rule 2: a theme chosen in the MENU starts in the moment and at the
    # level of the screen under it, so the water neither eases nor swells
    # when the MENU closes
    _need_fb()
    cap = FrameCapture()
    found = dict(rs._found, sub="result", word="TAP=AGAIN")
    for under, want_y, want_m in ((found, _y(0.92), M_FOUND),
                                  (rs.hunt(3, dist_band="<3"), _y(0.86), M_LIVE),
                                  (rs.hunt(0, dist_band="~40"), _y(0.38), M_LIVE)):
        rows = _menu_scenario(under, cap)
        assert rows[0][0] == 1500
        for t, ypx, sw, mom, _ in rows:
            assert ypx == want_y and mom == want_m, (under["screen"], t, ypx, mom)
            if want_m == M_FOUND:
                assert sw == 0, (t, sw)                 # FOUND is settled: no swell


def test_wake_shows_found_settled_unless_burst():
    _need_fb()
    r = ThemedRenderer("tide", overlays=False)
    cap = FrameCapture()
    # FOUND entered on a wake whose params carry the burst: the entry, so it swells
    _, phases, _ = rt.fixture("found_celebrate")
    sw = []
    _run(r, cap, phases, 500, each=lambda t, rr: sw.append(rr.theme.sw))
    assert sw[0] == TD.FOUND_SWELL and 0 < sw[-1] < sw[0], sw
    # a wake half-way through the swell (a 700 ms gap): shown settled, no catch-up
    _run(r, cap, [(0, rs.hunt(3, dist_band="<3"))], 500)
    found = dict(rs._found, sub="result", word="TAP=AGAIN")
    _run(r, cap, [(0, found)], 300, t0=600, reset=False)
    assert r.theme.sw > 0, r.theme.sw                 # the swell is under way
    r.frame(rs.make_params(t_ms=rt.T0 + 1600, **found), cap, rt.T0 + 1600)
    assert r.theme.wake and r.theme.sw == 0, (r.theme.wake, r.theme.sw)
    r.frame(rs.make_params(t_ms=rt.T0 + 1700, **found), cap, rt.T0 + 1700)
    assert r.theme.sw == 0


def test_drop_is_never_behind_the_lens():
    # PAIRING looking has the largest listening lens (iris 92, rim to 95);
    # the drop falls and its droplets land outside it, on screen, every cycle
    _need_fb()
    r = ThemedRenderer("tide", overlays=False)
    cap = FrameCapture()
    _, phases, _ = rt.fixture("pairing_looking")
    seen = [0, 0]                                     # falling frames, splash frames

    def each(t, rr):
        th = rr.theme
        if t < 600:                                   # the iris opens
            return
        assert th.lr >= 95, th.lr
        o = 4 * TD.B_DROP
        c = th.dclk % TD.DRIP_MS
        if c < TD.FALL_MS:
            if th.dsy - 3 >= 0:                       # the head is on screen
                assert th.box[o + 3] >= 0, (t, th.dsx, th.dsy, "hidden")
                seen[0] += 1
        elif c - TD.FALL_MS < TD.SPLASH_MS:
            for k in range(2):
                ob = o + 4 + 4 * k
                assert th.box[ob + 3] >= 0, (t, k, "droplet hidden")
                assert 0 <= th.box[ob] and th.box[ob] + 2 <= W - 1, (t, k, th.box[ob])
            seen[1] += 1

    _run(r, cap, phases, 6500, each=each)
    assert seen[0] >= 20 and seen[1] >= 8, seen


def test_fresh_theme_shows_the_drop():
    # a fresh theme starts half-way through a fall, so the listening drop is
    # on screen at once and in the previews (searching and link_lost, captured
    # on their last frame)
    _need_fb()
    r = ThemedRenderer("tide", overlays=False)
    cap = FrameCapture()
    o = 4 * TD.B_DROP
    for fx in ("searching", "link_lost"):
        _, phases, run_ms = rt.fixture(fx)
        first = []
        _run(r, cap, phases, run_ms,
             each=lambda t, rr: first.append(rr.theme.box[o + 3]) if t == 0 else None)
        th = r.theme
        assert first[0] >= 0, (fx, "no drop on the first frame")
        assert th.box[o + 3] >= 0 and 0 <= th.dsy < W, (fx, th.dsy, list(th.box[o:o + 4]))


def test_frozen_menu_reports_nothing():
    # once the MENU's dim has settled nothing moves, so no strip is dirty:
    # over the live water (bubbles), FOUND (shimmer) and listening (the drop)
    _need_fb()
    cap = FrameCapture()
    menu = dict(rs._menu, sub="1v", menu_rows=rs.MENU_ROWS)
    found = dict(rs._found, sub="result", word="TAP=AGAIN")
    for under in (rs.hunt(3, dist_band="~5"), found, rt._kw("searching"),
                  rt._kw("pairing_looking")):
        r = ThemedRenderer("tide", overlays=False)
        phases = [(0, under), (1000, dict(menu, ramp=under.get("ramp", "green")))]
        prev = bytearray(W * W * 2)
        seen = []

        def each(t, rr):
            if t >= 2000:
                seen.append(t)
                assert rr.theme.dirty == 0, (under.get("screen"), t, bin(rr.theme.dirty))
                assert cap.buf == prev, (under.get("screen"), t)
            prev[:] = cap.buf

        _run(r, cap, phases, 3000, each=each)
        assert len(seen) == 11, seen


def test_queue_theme_loads_in_steps():
    # ThemedRenderer.queue_theme loads tide one bounded step per frame (the
    # tables, the map 20 rows a step, the kernel check); once it takes over,
    # it draws exactly as a theme loaded whole
    _need_fb()
    cap = FrameCapture()
    ref = FrameCapture()
    r = ThemedRenderer("ripple", overlays=False)
    r.queue_theme("tide")
    _, phases, _ = rt.fixture("hot")
    t = 0
    n = 0
    while r.loading is not None:
        r.frame(rt.params_at(phases, t), cap, rt.T0 + t)
        t += 100
        n += 1
        assert n < 50
    assert r.theme.name == "tide"
    assert n >= 120 // TD.MAP_ROWS + 2, n             # __init__, the map steps, the check
    whole = ThemedRenderer("tide", overlays=False)
    assert r.theme.kind == whole.theme.kind, (r.theme.kind, whole.theme.kind)
    for fx in ("hot", "searching", "pairing_calibrate"):
        _, phases, run_ms = rt.fixture(fx)
        _run(r, cap, phases, run_ms)
        _run(whole, ref, phases, run_ms)
        assert cap.buf == ref.buf, fx
