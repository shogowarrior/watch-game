"""ui/themes/sonar.py: the Sonar theme (ui-spec §4A "Sonar").

The kernel, band and map tests run on any runtime (the palette kernel and
the blit source run as plain Python there); the frame tests need framebuf:

    node tools/mpy/run.mjs tests/runner.py test_theme_sonar

The generic contract (dirty regions, allocation, lens, MENU, switch) is in
tests/test_themes.py.
"""

import array
import math

from finder import tuning as T
from tests import Skip
from ui import swap16
from ui.field import V7, build_map, q8
from ui.themes import sonar as S
from ui.themes.base import Radial

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
    from ui.themes.base import M_LISTEN, M_LIVE, NS, SH, W, theme_luts

DEG = S.ANG / 360.0            # angle units per degree


def _need_fb():
    if not HAVE_FB:
        raise Skip("framebuf is MicroPython-only")


def _luma(c):
    n = swap16(c)
    return (299 * ((n >> 11) << 3) + 587 * (((n >> 5) & 63) << 2) + 114 * ((n & 31) << 3)) // 1000


# ---- the palette kernel on synthetic frames (any runtime) ---------------------------
def _kernel(beams, iris=0, fl=0, gl=0, ring=0, core=0):
    """Run sonar_pal once on a fresh palette; beams: (deg, dir, L deg, amp
    levels, ghost). Returns (pal, prm, tq)."""
    rad = Radial("sonar")
    rad.ramps.mix.copy_from(rad.ramps.luts["green"])     # the mixed LUT: green
    pal = array.array("H", [0] * S.PAL_N)
    prm = array.array("i", [0] * S.PRM_N)
    tq = array.array("i", [0] * S.T_N)
    for c in range(S.NC):
        tq[3 * S.NBIN + c] = -1
    prm[S.H_NC] = S.NC
    prm[S.H_IRIS] = iris
    prm[S.H_RIMHI] = iris + T.IRIS_RIM_PX if iris else 0
    prm[S.H_FL] = fl
    prm[S.H_GL] = gl
    prm[S.H_GINV] = (64 * 256 * 256) // (30 * 256)
    prm[S.H_RING] = ring
    prm[S.H_VMAX] = V7
    prm[S.H_DIM] = 256
    prm[S.H_RIM] = -1
    prm[S.H_RIMQ] = 5 * 256
    prm[S.H_IRISC] = T.THEME_IRIS["sonar"]
    prm[S.H_CORE] = core
    prm[S.H_PINGR] = -1
    prm[S.H_NB] = len(beams)
    prm[S.H_LT] = 1
    for k in range(len(beams)):
        deg, dr, ldeg, amp, gh = beams[k]
        o = S.H_BM + 6 * k
        prm[o] = int(deg * DEG + 0.5) & (S.ANG - 1)
        prm[o + 1] = dr
        prm[o + 2] = S.HEAD_U
        prm[o + 3] = (32 << 16) // int(ldeg * DEG)
        prm[o + 4] = q8(amp)
        prm[o + 5] = gh
    S.sonar_pal(pal, rad.tab, S.CT, prm, tq)
    return pal, prm, tq


def _trail_ref(bin_, deg, dr, ldeg, amp):
    """The trail level (Q8) the spec and mockup give at the centre of a bin:
    full over the head, exp(-d / L) behind it (eased to 0 under 3 %), a
    linear lead-in ahead."""
    th = (bin_ * 64 + 32) / DEG
    d = ((deg - th) * dr) % 360.0                 # degrees behind the head
    if d >= 360.0 - S.LEAD_U / DEG:
        v = 1.0 - (360.0 - d) / (S.LEAD_U / DEG)
    elif d <= S.HEAD_U / DEG:
        v = 1.0
    else:                                         # cut smoothly at TAIL_CUT
        v = math.exp(-(d - S.HEAD_U / DEG) / ldeg)
        v = max(0.0, (v - S.TAIL_CUT) / (1.0 - S.TAIL_CUT))
    return 256 * amp * v


def test_params_from_tokens():
    p = T.THEME_PARAMS["sonar"]
    assert tuple(S.BEAMS) == (2, 3, 4, 6) == tuple(p["beams"])
    assert S.RINGS == (40, 80, 120)
    assert (S.TRAIL_A, S.TRAIL_B) == (30, 70)
    assert (S.AMP_A, S.AMP_B) == (q8(3), q8(4))
    assert (S.RL_A, S.RL_B) == (q8(1.0), q8(0.8))
    assert S.LISTEN_DEG_S == 60 and S.BREATHE_MS == 2400 and S.PING_PX_S == 240
    # one turn = beams x P: 4.8 / 4.8 / 4.0 / 3.0 s
    turns = tuple(S.BEAMS[z] * T.ZONE_PERIOD_MS[z] for z in range(4))
    assert turns == (4800, 4800, 4000, 3000), turns


def test_class_bands():
    # each range ring is one ring index of its own (drawn exactly, flagged in ct)
    for r in S.RINGS:
        c = S.CLS[r]
        assert S.EDGES[c] == r and S.EDGES[c + 1] == r + 1
        assert S.CT[S.C_RING + c] == 1
    assert sum(S.CT[S.C_RING + c] for c in range(S.NC)) == len(S.RINGS)
    # band edges on every glyph's lens and rim radius, and at the core dot (7)
    for g in ("chevrons", "arrow", "scan", "runes", "seeker"):
        ir = T.IRIS_R[g]
        assert ir in S.EDGES and ir + T.IRIS_RIM_PX in S.EDGES, g
    assert 7 in S.EDGES and S.EDGES[0] == 0 and S.EDGES[-1] == 170
    for c in range(S.NC):
        assert S.EDGES[c + 1] - S.EDGES[c] <= 8
    # the dither picks the class itself or a neighbour, never across a fixed edge
    fixed = {7}
    for r in S.RINGS:
        fixed.add(r)
        fixed.add(r + 1)
    for g in ("chevrons", "arrow", "scan", "runes", "seeker"):
        if T.IRIS_R[g]:
            fixed.add(T.IRIS_R[g])
            fixed.add(T.IRIS_R[g] + T.IRIS_RIM_PX)
    for i in range(170):
        c = S.CLS[i]
        for j in range(16):
            k = S.CLSD[i * 16 + j]
            assert abs(k - c) <= 1, (i, j, k, c)
            if k != c:
                e = S.EDGES[max(k, c)]
                assert e not in fixed, (i, j, e)
    for r in S.RINGS:
        for j in range(16):
            assert S.CLSD[r * 16 + j] == S.CLS[r]


def test_trail_shape():
    # one beam each way: the kernel's per-bin trail is the spec's exp(-d / L)
    for deg, dr in ((90.0, 1), (200.0, -1), (3.0, 1)):
        ldeg, amp = 50.0, 5.0
        _, _, tq = _kernel(((deg, dr, ldeg, amp, 0),))
        for b in range(128):
            want = _trail_ref(b, deg, dr, ldeg, amp)
            assert abs(tq[b] - want) <= 0.04 * want + 4, (deg, dr, b, tq[b], want)
    # the trail lies behind the head: clockwise beams trail anticlockwise
    _, _, tq = _kernel(((90.0, 1, 50.0, 5.0, 0),))
    assert tq[int(80 * 128 / 360)] > 3 * tq[int(100 * 128 / 360)]
    _, _, tq = _kernel(((90.0, -1, 50.0, 5.0, 0),))
    assert tq[int(100 * 128 / 360)] > 3 * tq[int(80 * 128 / 360)]


def test_trail_covers_the_10fps_step():
    # §4A rule 5: a beam moves at most about 12 deg a 100 ms frame (HOT), and
    # its trail one frame step behind the head is still over half its peak,
    # even with the shortest trail (I = 0: 30 deg)
    for z in range(4):
        step = 360.0 * 100 / (S.BEAMS[z] * T.ZONE_PERIOD_MS[z])
        assert step <= 12.0 + 1e-9, (z, step)
        _, _, tq = _kernel(((180.0, 1, float(S.TRAIL_A), 4.0, 0),))
        peak = max(tq[b] for b in range(128))
        b = int((180.0 - step) * 128 / 360)          # the bin one step behind
        assert tq[b] * 2 >= peak, (z, step, tq[b], peak)
    # listening: 60 deg/s is 6 deg a frame, under its 22 deg trail
    _, _, tq = _kernel(((180.0, -1, float(S.LISTEN_TRAIL_DEG), 2.2, 0),))
    peak = max(tq[b] for b in range(128))
    b = int((180.0 + 6.0) * 128 / 360) + 1
    assert tq[b] * 2 >= peak, (tq[b], peak)


def test_ghost_beam_draws_grey():
    # a beam that crossed 12 o'clock on a ghost beat draws its trail grey
    pal, _, tq = _kernel(((90.0, 1, 40.0, 5.0, 0), (270.0, 1, 40.0, 5.0, 1)), fl=64, gl=0)
    grey = set(pal[S.P_GT + v] for v in range(V7 + 1))
    green = set(pal[S.P_LT + v] for v in range(V7 + 1))
    assert pal[S.P_GT + V7] not in green and pal[S.P_LT + V7] not in grey
    c = S.CLS[100]
    for deg, want in ((88.0, green), (268.0, grey)):
        b = int(deg * 128 / 360)
        assert tq[128 + b] == (1 if want is grey else 0)
        assert pal[c * 128 + b] in want, (deg, hex(pal[c * 128 + b]))
    # where the base outshines the ghost trail the base keeps its colour
    b = int(120.0 * 128 / 360)
    assert pal[c * 128 + b] in green


def test_lens_rim_and_core_classes():
    # a settled lens and rim are exact: whole classes in the lens / rim colour
    pal, _, _ = _kernel(((0.0, 1, 60.0, 6.0, 0),), iris=64, fl=256, gl=512)
    irisc = T.THEME_IRIS["sonar"]
    rim = pal[S.P_LT + 5 * 256]
    for c in range(S.NC):
        row = set(pal[c * 128 + b] for b in range(128))
        if S.EDGES[c + 1] <= 64:
            assert row == {irisc}, c
        elif S.EDGES[c + 1] <= 67:
            assert row == {rim}, c
        else:
            assert irisc not in row, c
    # iris closed: the core dot (r < 7) is at least the core level, and a
    # beam's trail reaches the centre (no fade without a rim)
    core = 6 * 256
    pal, _, tq = _kernel(((0.0, 1, 60.0, 7.0, 0),), core=core)
    floor = pal[S.P_LT + core]
    top = pal[S.P_LT + V7]
    lv = {}
    for v in range(V7 + 1):
        lv.setdefault(pal[S.P_LT + v], v)
    for b in range(128):
        assert lv[pal[b]] >= lv[floor], b
    assert pal[127] == top                          # the head bin saturates at the centre


def test_kernels_agree():
    # the compiled kernels (viper where the port has it) agree with their
    # plain versions on the self-check frames; plain Python elsewhere
    assert S.PAL_KIND in ("viper", "python"), S.PAL_KIND
    assert S.DIFF_KIND in ("viper", "python"), S.DIFF_KIND
    assert S.pal_agrees(S._plain(S._PSRC, "sonar_pal"), S.sonar_pal)
    assert S.diff_agrees(S._plain(S._DSRC, "sonar_diff"), S.sonar_diff)


def test_palette_changed_arc():
    # the kernel reports the shortest arc of bins holding every change
    rad = Radial("sonar")
    rad.ramps.mix.copy_from(rad.ramps.luts["green"])
    pal, prm, tq = _kernel(((200.0, -1, 22.0, 2.2, 0),), fl=128)
    old = array.array("H", pal)
    prm[S.H_LT] = 0
    prm[S.H_BM] = (prm[S.H_BM] - int(6 * DEG)) & (S.ANG - 1)
    S.sonar_pal(pal, rad.tab, S.CT, prm, tq)
    a0, an = prm[S.O_A0], prm[S.O_AN]
    assert 0 < an < 64, an
    for b in range(128):
        ch = False
        for c in range(S.NC):
            ch = ch or pal[c * 128 + b] != old[c * 128 + b]
        assert tq[256 + b] == (1 if ch else 0)
        if ch:
            assert (b - a0) % 128 < an, (b, a0, an)


def _angle(x, y):
    """Clockwise from 12 o'clock, degrees, of pixel (x, y)'s centre."""
    return math.degrees(math.atan2(x - 119.5, 119.5 - y)) % 360.0


def test_quadrant_map():
    # each pixel's bin is within a bin of its true angle (the dither moves it
    # at most one), its class within one of its ring index's, and the four
    # quadrant mirrors (bin, ^127, ^63, ^64) give the mirrored angles
    idx = build_map(240)
    q, xs = S.build_maps(idx)
    m = S.build_full16(q)
    for y in range(0, 240, 3):
        for x in range(0, 240, 3):
            e = m[y * 240 + x]
            b = e & 127
            c = e >> 7
            u = _angle(x, y) * 128 / 360.0 - 0.5          # in bins, centre to centre
            d = (b - u) % 128.0
            assert d <= 1.0 or d >= 127.0, (x, y, b, u)
            i = idx[y * 240 + x]
            assert abs(c - S.CLS[i]) <= 1, (x, y, c, i)
            if i in S.RINGS:
                assert c == S.CLS[i]
    # spans: a ring index's first column in each strip (mirrored at 239 - x)
    ns = S.NS
    want = {}
    for y in range(240):
        k = y // S.SH
        for x in range(240):
            i = idx[y * 240 + x]
            if i in (5, 40, 119, 160):
                lo, hi = want.get((i, k), (255, -1))
                want[(i, k)] = (min(lo, x), max(hi, x))
    for i in (5, 40, 119, 160):
        for k in range(ns):
            lo, hi = want.get((i, k), (255, -1))
            assert xs[i * ns + k] == lo, (i, k, xs[i * ns + k], lo)
            if hi >= 0:
                assert hi == 239 - lo, (i, k)


def test_blit_source_matches_full_map():
    # the blit kernel's source, run plain: every band equals a palette lookup
    # of the full map (the XOR mirrors per quadrant), as the framebuf path
    q, _ = S.build_maps(build_map(120))
    m = S.build_full16(q)
    pal = array.array("H", [(i * 4099 + 0x1235) & 0xFFFF for i in range(S.NCB)])
    qw = array.array("I", [q[2 * i] | (q[2 * i + 1] << 16) for i in range(7200)])
    blit = S._plain(S._BSRC, "sonar_blit")
    for y0 in (0, 60, 120, 180):
        dst = array.array("I", [0] * 7200)
        blit(dst, qw, pal, y0, 60)
        for row in range(0, 60, 7):
            for x in range(0, 240, 5):
                w = dst[row * 120 + (x >> 1)]
                got = (w >> 16) if x & 1 else (w & 0xFFFF)
                assert got == pal[m[(y0 + row) * 240 + x]], (y0, row, x)


# ---- frames (MicroPython) ---------------------------------------------------------
def _beams(th):
    """Current beams' angles (deg) and direction from the theme's kernel params."""
    out = []
    for j in range(th.cb):
        out.append(th.prm[S.H_BM + 6 * j] / DEG)
    return out


def _lum_at(buf, deg, r):
    a = math.radians(deg)
    x = int(119.5 + r * math.sin(a))
    y = int(119.5 - r * math.cos(a))
    o = (y * 240 + x) * 2
    return _luma(buf[o] | (buf[o + 1] << 8))


def test_live_beams_locked_to_the_spawn():
    # beam j sits at 360 (beat_age / P + j) / B: one beam crosses 12 o'clock on
    # every ring spawn, all turn clockwise, a turn takes B x P
    _need_fb()
    r = ThemedRenderer("sonar", overlays=False)
    cap = FrameCapture()
    for z in range(4):
        b = S.BEAMS[z]
        seen = []

        def each(t, r, cap, z=z, b=b, seen=seen):
            th = r.theme
            if t < 700:                              # beam-set fade-in
                return
            assert th.cls and th.cb == b and th.prm[S.H_NB] == b, (z, t)
            per = r.field.period
            assert per == T.ZONE_PERIOD_MS[z]
            age = min(th.beat_age(rt.T0 + t), per)
            angs = _beams(th)
            for j in range(b):
                want = (360.0 * (age / per + j) / b) % 360.0
                d = (angs[j] - want) % 360.0
                assert d < 0.1 or d > 359.9, (z, t, j, angs[j], want)
                assert th.prm[S.H_BM + 6 * j + 1] == 1
            seen.append(age)

        rt.run(r, cap, [(0, rs.hunt(z))], 700 + 2 * T.ZONE_PERIOD_MS[z], each=each)
        assert min(seen) < 100, (z, seen)            # a frame right after a spawn

    # and on screen: bright just behind each head, dark just ahead (FAR, NEAR)
    for z in (0, 1):
        _, phases, run_ms = rt.fixture(("far", "near")[z])
        rt.run(r, cap, phases, run_ms)
        for a in _beams(r.theme):
            behind = _lum_at(cap.buf, a - 6, 100)
            ahead = _lum_at(cap.buf, a + 9, 100)
            far_back = _lum_at(cap.buf, a - 40, 100)
            assert behind > ahead + 20 and behind > far_back > ahead, (
                z, a, behind, far_back, ahead)


def test_listening_beam():
    # one beam turning anticlockwise at 60 deg/s from 12 o'clock, its short
    # trail on the clockwise side
    _need_fb()
    r = ThemedRenderer("sonar", overlays=False)
    cap = FrameCapture()
    _, phases, run_ms = rt.fixture("searching")
    got = []

    def each(t, r, cap):
        th = r.theme
        assert th.mom == M_LISTEN and th.cb == 1 and th.cdir == -1
        got.append(_beams(th)[0])

    rt.run(r, cap, phases, run_ms, each=each)
    for n in range(len(got)):
        want = (-60.0 * n * rt.STEP_MS / 1000) % 360.0
        d = (got[n] - want) % 360.0
        assert d < 0.1 or d > 359.9, (n, got[n], want)
    a = got[-1]
    assert _lum_at(cap.buf, a + 5, 100) > _lum_at(cap.buf, a - 9, 100) + 10
    th = r.theme
    ldeg = (32 << 16) / th.prm[S.H_BM + 3] / DEG
    assert abs(ldeg - S.LISTEN_TRAIL_DEG) < 0.5, ldeg


def test_trail_and_peak_follow_intensity():
    # L = 30 + 70 I deg, peak 3 + 4 I levels (saver off, fade-in done)
    _need_fb()
    r = ThemedRenderer("sonar", overlays=False)
    cap = FrameCapture()
    for fx in ("far", "near", "warm_arrow", "hot"):
        _, phases, run_ms = rt.fixture(fx)
        rt.run(r, cap, phases, run_ms)
        th = r.theme
        i = th.iq() / 256.0
        ldeg = (32 << 16) / th.prm[S.H_BM + 3] / DEG
        assert abs(ldeg - (30 + 70 * i)) < 0.5, (fx, ldeg, i)
        amp = th.prm[S.H_BM + 4] / 256.0
        assert abs(amp - (3 + 4 * i)) < 0.02, (fx, amp, i)


def _ring_px(r, i, deg):
    """A pixel of ring index ``i`` on the ray at ``deg``."""
    idx = r.map.idx
    a = math.radians(deg)
    for k in range(8):
        rr = i + k / 8.0
        x = int(119.5 + rr * math.sin(a))
        y = int(119.5 - rr * math.cos(a))
        if idx[y * 240 + x] == i:
            return x, y
    raise AssertionError((i, deg))


def test_range_rings():
    # rings at r 40 / 80 / 120, level 1 + 0.8 I, brighter than the base next to them
    _need_fb()
    r = ThemedRenderer("sonar", overlays=False)
    cap = FrameCapture()
    _, phases, run_ms = rt.fixture("far")
    rt.run(r, cap, phases, run_ms)
    th = r.theme
    assert th.ring == S.RL_A + ((S.RL_B * th.iq()) >> 8)
    a = _beams(th)[0] + 20                     # 160 deg behind the other beam: no trail
    buf = cap.buf
    for ring in S.RINGS:
        lum = []
        for i in (ring - 3, ring, ring + 3):
            x, y = _ring_px(r, i, a)
            o = (y * 240 + x) * 2
            lum.append(_luma(buf[o] | (buf[o + 1] << 8)))
        assert lum[1] > lum[0] and lum[1] > lum[2], (ring, lum)


def test_still_breathes():
    # still (PAIRING seen): the range rings breathe 1.2 + 1.2 x (0.5 - 0.5 cos) over 2.4 s
    _need_fb()
    r = ThemedRenderer("sonar", overlays=False)
    cap = FrameCapture()
    _, phases, _ = rt.fixture("pairing_seen")
    lv = {}

    def each(t, r, cap):
        assert not r.theme.cls                       # no beams in still
        lv[t] = r.theme.ring

    rt.run(r, cap, phases, 6000, each=each)
    vals = [lv[t] for t in lv if t >= 1200]
    assert abs(min(vals) - q8(1.2)) <= 8 and abs(max(vals) - q8(2.4)) <= 8, (min(vals), max(vals))
    for t in range(1200, 3600, 100):
        assert abs(lv[t] - lv[t + S.BREATHE_MS]) <= 4, t


def test_found_ping_then_breathe():
    # FOUND: one ping ring runs out at 240 px/s, then the rings breathe
    # 2.0 + 1.4 x (0.5 + 0.5 sin) over 2.4 s; a wake into FOUND has no ping
    _need_fb()
    r = ThemedRenderer("sonar", overlays=False)
    cap = FrameCapture()
    _, phases, _ = rt.fixture("found_celebrate")
    edge = {}
    rings = []

    def each(t, r, cap):
        th = r.theme
        assert not th.cls
        if th.ping:
            pal = th.rad.pal_arr
            top = max(_luma(pal[i]) for i in range(170))
            e = 0
            for i in range(170):
                if _luma(pal[i]) == top:
                    e = i
            edge[t] = e
        elif t >= 1500:
            rings.append(th.ring)

    rt.run(r, cap, phases, 5000, each=each)
    assert 0 in edge and 1200 not in edge, sorted(edge)
    d = edge[400] - edge[200]
    assert abs(d - 48) <= 3, (edge[200], edge[400])
    lo, hi = min(rings), max(rings)
    assert abs(lo - q8(2.0)) <= 8 and abs(hi - q8(3.4)) <= 8, (lo, hi)
    # a wake straight into FOUND (no burst): no ping
    _, phases, run_ms = rt.fixture("found_result")

    def each2(t, r, cap):
        assert not r.theme.ping

    rt.run(r, cap, phases, run_ms, each=each2)


def test_ghost_beat_is_grey():
    # ring_live false: the beam that crossed 12 o'clock on that beat draws grey
    _need_fb()
    r = ThemedRenderer("sonar", overlays=False)
    cap = FrameCapture()
    luts = theme_luts("sonar")
    grey = set(luts["grey"].c)
    green = set(luts["green"].c)
    for fx, want in (("far_ghost", grey), ("far", green)):
        _, phases, run_ms = rt.fixture(fx)
        rt.run(r, cap, phases, run_ms)
        for a in _beams(r.theme):
            rad = math.radians(a - 3)
            x = int(119.5 + 100 * math.sin(rad))
            y = int(119.5 - 100 * math.cos(rad))
            o = (y * 240 + x) * 2
            c = cap.buf[o] | (cap.buf[o + 1] << 8)
            assert c in want, (fx, a, hex(c))


def test_scan_stops_the_beams():
    # live -> scan: the beams freeze and fade out over 600 ms, then the quiet map
    _need_fb()
    r = ThemedRenderer("sonar", overlays=False)
    cap = FrameCapture()
    _, sc, _ = rt.fixture("scan_sweep")
    phases = [(0, rs.hunt(2)), (1000, sc[0][1])]
    st = {}

    def each(t, r, cap):
        th = r.theme
        st[t] = (th.cls, th.on, th.cb, [th.oang[j] for j in range(th.on)])

    rt.run(r, cap, phases, 2000, each=each)
    assert st[900][0] and st[900][2] == S.BEAMS[2]
    assert st[1100][1] == S.BEAMS[2] and st[1100][2] == 0
    assert st[1100][3] == st[1300][3]                # frozen while fading
    assert not st[1700][0]                           # gone: the halo only


def _area(th):
    a = 0
    for k in range(NS):
        if th.dirty & (1 << k):
            a += (th.spans[2 * k + 1] - th.spans[2 * k] + 1) * SH
    return a


def test_dirty_is_tight():
    # one listening beam reports its wedge, not the screen; after the FOUND
    # ping only the breathing rings change
    _need_fb()
    r = ThemedRenderer("sonar", overlays=False)
    cap = FrameCapture()
    _, phases, run_ms = rt.fixture("searching")
    areas = []

    def each(t, r, cap):
        if t >= 800:
            areas.append(_area(r.theme))

    rt.run(r, cap, phases, run_ms, each=each)
    assert max(areas) < 0.6 * W * W and min(areas) > 0, (min(areas), max(areas))
    _, phases, _ = rt.fixture("found_celebrate")
    n = []

    def each2(t, r, cap):
        th = r.theme
        if t >= 1500:
            k = th.dout[0]
            assert k <= len(S.RINGS), (t, k)
            for j in range(k):
                assert th.dout[3 + j] in S.RINGS
            n.append(k)

    rt.run(r, cap, phases, 4000, each=each2)
    assert max(n) == len(S.RINGS)


def test_menu_holds_the_beams():
    # MENU freezes the beams and keeps the moment (generic pixel check in test_themes)
    _need_fb()
    r = ThemedRenderer("sonar", overlays=False)
    cap = FrameCapture()
    _, phases, _ = rt.fixture("menu")
    st = {}

    def each(t, r, cap):
        th = r.theme
        st[t] = (th.mom, _beams(th), th.ring)

    rt.run(r, cap, phases, 2000, each=each)
    for t in range(1100, 2001, 100):
        assert st[t] == st[1000], t
    assert st[1000][0] == M_LIVE


def test_lens_rim_and_core_exact():
    # the settled lens and rim are whole discs (no dither across their edges);
    # the core dot (r < 7) is level 6 or more with the iris closed
    _need_fb()
    r = ThemedRenderer("sonar", overlays=False)
    cap = FrameCapture()
    idx = r.map.idx
    _, phases, run_ms = rt.fixture("warm_arrow")
    rt.run(r, cap, phases, run_ms)
    buf = cap.buf
    irisc = T.THEME_IRIS["sonar"]
    rim = set()
    for o in range(0, 240 * 240):
        i = idx[o]
        if i < 70:
            c = buf[2 * o] | (buf[2 * o + 1] << 8)
            if i < 64:
                assert c == irisc, o
            elif i < 67:
                rim.add(c)
            else:
                assert c != irisc, o
    assert len(rim) == 1, rim
    bright = theme_luts("sonar")["green"].c
    hi = set(bright[j] for j in range(54, 64))
    for fx in ("far", "hot"):
        _, phases, run_ms = rt.fixture(fx)
        rt.run(r, cap, phases, run_ms)
        for o in range(0, 240 * 240):
            if idx[o] < 7:
                c = cap.buf[2 * o] | (cap.buf[2 * o + 1] << 8)
                assert c in hi, (fx, o, hex(c))


def test_blit_kernel_matches_framebuf():
    # where viper runs, the quadrant kernel draws what the framebuf palette
    # blit of the full map draws, on real frames
    _need_fb()
    if S._BLIT_V is None:
        raise Skip("no viper on this port (the plain source is tested above)")
    r = ThemedRenderer("sonar", overlays=False)
    th = r.theme
    assert th.bkind == "viper", th.bkind
    full = framebuf.FrameBuffer(S.build_full16(th.q16), W, W, framebuf.RGB565)
    cap = FrameCapture()
    for fx in ("near", "hot", "searching"):
        _, phases, run_ms = rt.fixture(fx)
        rt.run(r, cap, phases, run_ms)
        for k in range(4):
            buf = r.bands[k]
            th.blit(k * 60, buf, r.band_fbs[k])
            want = bytes(buf)
            r.band_fbs[k].blit(full, 0, -k * 60, -1, th.pal2_fb)
            assert bytes(buf) == want, (fx, k)
