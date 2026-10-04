"""Warp theme (ui-spec §4A Warp, ui/themes/warp.py): stars stream out of the centre.

The token check runs on any runtime; frames need framebuf (MicroPython):

    node tools/mpy/run.mjs tests/runner.py test_theme_warp

Frames run the theme fixtures of tools/render_themes.py at 10 fps with the
overlays off, so every pixel compared is the theme's own layer.
"""

from finder import tuning as T
from tests import Skip

try:
    import framebuf  # noqa: F401
    HAVE_FB = True
except ImportError:
    HAVE_FB = False

if HAVE_FB:
    from tools import render_themes as rt
    from tools import render_snapshots as rs
    from ui.renderer import FrameCapture
    from ui.themes import ThemedRenderer
    from ui.themes import warp as W

W_ = 240


def _need_fb():
    if not HAVE_FB:
        raise Skip("framebuf is MicroPython-only")


def _loaded():
    """Warp's load steps all done, as make() and ThemedRenderer run them
    (load() is idempotent), for tests that use the module's kernels."""
    _need_fb()
    for _ in W.load():
        pass


def _renderer():
    return ThemedRenderer("warp", overlays=False)


def _run(r, cap, phases, run_ms, each=None):
    r.reset()
    t = 0
    while t <= run_ms:
        ev = r.frame(rt.params_at(phases, t), cap, rt.T0 + t)
        if each is not None:
            each(t, r, ev)
        t += rt.STEP_MS


def _shown(th):
    st = th.st
    return sum(1 for j in range(W.NMAX) if st[j * W.ST_N + 1] > 0)


def _u(th, j):
    return th.st[j * W.ST_N]


def _drawing(kern, bad=0):
    """A star kernel for kernel_agrees from a plain one: ``kern`` runs
    without drawing, then its segments go out as fb.line calls (as the
    wasm path draws them). ``bad`` 1 gets one colour wrong, 2 adds a stray
    pixel."""
    def k(st, cst, tab, rtab, prm, seg, fbuf, sp):
        d = prm[W.P_DRAW]
        prm[W.P_DRAW] = 0
        b = kern(st, cst, tab, rtab, prm, seg, fbuf, sp)
        prm[W.P_DRAW] = d
        if bad == 1:
            seg[4] ^= 1
        if d:
            y0 = prm[W.P_FY0]
            for i in range(prm[W.P_NSEG]):
                o = i * 5
                fbuf.line(seg[o], seg[o + 1] - y0, seg[o + 2], seg[o + 3] - y0, seg[o + 4])
            if bad == 2:
                fbuf.pixel(5, 5, 0x1234)
        return b
    return k


def _lut(name, ramp):
    """The theme's ramp LUT as a list (MicroPython arrays have no ``in``)."""
    from ui.themes.base import theme_luts
    return list(theme_luts(name)[ramp].c)


# ---- tokens (any runtime) ----------------------------------------------------------
def test_tokens_match_the_spec():
    # ui-spec §4A Warp: the numbers the paragraph gives
    p = T.THEME_PARAMS["warp"]
    assert tuple(p["stars"]) == (18, 30, 46, 70)
    assert tuple(p["rate"]) == (0.45, 0.65, 0.95, 1.45)
    assert p["surge"] == 1.3 and p["surge_ms"] == 170
    assert p["listen_stars"] == 14 and p["listen_rate"] == -0.22
    assert p["still_stars"] == 30 and p["scan_stars"] == 24
    assert p["found_stars"] == 56 and p["found_rate"] == 1.6 and p["found_ms"] == 400
    assert "warp" in T.THEME_IRIS and "warp" in T.THEME_RAMP_LUT


# ---- kernels ----------------------------------------------------------------------
def test_kernel_literals_are_the_constants():
    # viper sees no globals: the star kernel spells the module constants out
    _need_fb()
    src = W._SSRC
    for frag in ("%d + ((%d * u) >> 16)" % (W.B0, W.B1),
                 "%d + ((%d * tv[%d + s]) >> 14)" % (W.TW0, W.TW1, W.K_SIN),
                 "tf > %d" % W.SPARK, "u > %d" % W.WIDE_U,
                 "((rb - ra) * %d) >> 8" % W.HEAD_FRAC,
                 "qh += %d" % W.D_HEAD, "qt += %d" % W.D_TAIL, "v = b + %d" % W.S_FLOOR,
                 "(b >> 1) + %d" % W.S_FLOOR, "tv[%d + rh]" % W.K_VIG,
                 "tv[%d + (ra >> 6)]" % W.K_BL, "if ra < %d" % W.RFAR,
                 "so = j << 3", "co = j << 3", "rv[lut + q]"):
        assert frag in src, frag
    assert W.ST_N == 8 and W.CS_N == 8 and W.NMAX == 70
    # 2 px of extra room for a 2x2 dot or a cross: 32 in Q4
    assert "rb >= rmin + 32" in src
    assert W.K_VIG == W.K_RT + 1024 and W.K_SIN == W.K_VIG + 170 and W.K_BL == W.K_SIN + 360


def test_kernels_agree():
    # where viper compiled (the watch, a 32-bit unix build) it is used only
    # after matching the plain kernel and FrameBuffer.line; elsewhere plain
    _loaded()
    assert W.KERNEL in ("viper", "python"), W.KERNEL        # never "self-check failed"
    assert W.DIFF_KIND in ("viper", "python"), W.DIFF_KIND
    if W.KERNEL == "viper":
        assert W.star_kernel is not W.star_kernel_py
        assert W.kernel_agrees(W.star_kernel_py, W.star_kernel)
    else:
        assert W.star_kernel is W.star_kernel_py
    if W.DIFF_KIND == "viper":
        assert W.diff_agrees(W.pal_diff_py, W.pal_diff)
    # the check itself: a kernel that draws its segments with fb.line passes,
    # one that gets a single colour wrong, or draws one stray pixel, is caught
    assert W.kernel_agrees(W.star_kernel_py, _drawing(W.star_kernel_py))
    assert not W.kernel_agrees(W.star_kernel_py, _drawing(W.star_kernel_py, 1))
    assert not W.kernel_agrees(W.star_kernel_py, _drawing(W.star_kernel_py, 2))


def test_self_check_covers_the_cap_and_unchanged_stars():
    # a kernel without the saver cap, or one that reports unchanged stars as
    # changed, fails the self-check (the listening frame's cap bites, and
    # the held frame runs twice on the same state)
    _loaded()
    src = W._SSRC
    assert W.kernel_agrees(W.star_kernel_py, _drawing(W._plain(src, "star_kernel")))
    for a, b in (("if v > vmax:", "if v > vmax + 99999:"),
                 ("            if same == 0:\n", "            if same >= 0:\n")):
        assert a in src, a
        bad = W._plain(src.replace(a, b), "star_kernel")
        assert not W.kernel_agrees(W.star_kernel_py, _drawing(bad)), a


def test_loads_in_steps():
    # queue_theme loads Warp one step a frame while Ripple keeps drawing (the
    # module's load() runs each _step once, also when a load is abandoned
    # half-way and queued again), then draws as a theme loaded whole
    _loaded()
    done = []
    step = W._step

    def counted(k):
        done.append(k)
        return step(k)

    W._stage = 0
    W._step = counted
    try:
        r = ThemedRenderer("ripple", overlays=False)
        cap = FrameCapture()
        _, phases, run_ms = rt.fixture("hot")
        r.queue_theme("warp")
        t = 0
        while r.loading is not None:
            n0 = len(done)
            r.frame(rt.params_at(phases, t), cap, rt.T0 + t)
            assert len(done) - n0 <= 1, done            # at most one load step a frame
            assert r.loading is None or r.theme.name == "ripple"
            t += rt.STEP_MS
            if t == 600:                                # abandoned, then queued again
                r.queue_theme("ripple")
                assert r.loading is None
                r.queue_theme("warp")
            assert t < 5000, done
        assert r.theme.name == "warp"
        assert W._stage == W._NSTEPS and done[-1] == W._S_END, done
        assert len(set(done)) == len(done) and len(done) >= 8, done    # each step once
        assert W.KERNEL in ("viper", "python"), W.KERNEL
    finally:
        W._step = step
    b = ThemedRenderer("warp", overlays=False)
    cb = FrameCapture()
    rt.run(r, cap, phases, run_ms)
    rt.run(b, cb, phases, run_ms)
    assert cap.buf == cb.buf


def test_pal_diff():
    _loaded()
    import array
    for fn in (W.pal_diff_py, W.pal_diff):
        cur = array.array("H", range(256))
        prev = array.array("H", cur)
        assert fn(cur, prev, 170) == -1
        prev[12] = 0
        prev[99] = 1
        prev[180] = 2                              # past n: ignored, not copied
        assert fn(cur, prev, 170) == 99
        assert prev[12] == 12 and prev[99] == 99 and prev[180] == 2
        assert fn(cur, prev, 170) == -1


def test_plain_path_matches():
    # the same frames with the plain kernel and fb.line (the wasm path)
    _need_fb()
    a = _renderer()
    b = _renderer()
    b.theme.kern = W.star_kernel_py
    b.theme.kdraw = 0
    ca = FrameCapture()
    cb = FrameCapture()
    for fx in ("hot", "found_celebrate", "searching"):
        _, phases, run_ms = rt.fixture(fx)
        da = []
        db = []
        _run(a, ca, phases, run_ms, lambda t, r, ev: da.append((r.theme.dirty, bytes(r.theme.spans))))
        _run(b, cb, phases, run_ms, lambda t, r, ev: db.append((r.theme.dirty, bytes(r.theme.spans))))
        assert ca.buf == cb.buf, fx
        assert da == db, fx


# ---- counts and motion ---------------------------------------------------------------
def test_star_count_per_moment():
    _need_fb()
    r = _renderer()
    cap = FrameCapture()
    want = (("far", 18), ("near", 30), ("warm_arrow", 46), ("hot", 70), ("searching", 14),
            ("link_lost", 14), ("pairing_seen", 30), ("scan_sweep", 24), ("direction_turn", 24),
            ("found_result", 56))
    for fx, n in want:
        _, phases, run_ms = rt.fixture(fx)
        _run(r, cap, phases, run_ms)
        assert _shown(r.theme) == n, (fx, _shown(r.theme), n)
        assert r.theme.prm[W.P_N] == n, fx


def test_zone_change_never_pops():
    # HOT -> FAR drops stars 18..69 only as each one wraps (leaves the
    # screen), FAR -> HOT adds stars only as each one wraps (at the centre)
    _need_fb()
    r = _renderer()
    cap = FrameCapture()
    phases = [(0, rs.hunt(3, dist_band="~5")), (1000, rs.hunt(0, dist_band="~40")),
              (7000, rs.hunt(3, dist_band="~5"))]
    seen = {"drop": 0, "add": 0}
    prev = [None]

    def each(t, r, ev):
        th = r.theme
        st = th.st
        now = [(st[j * W.ST_N], st[j * W.ST_N + 1]) for j in range(W.NMAX)]
        if prev[0] is not None:
            for j in range(W.NMAX):
                u0, o0 = prev[0][j]
                u1, o1 = now[j]
                if o0 and not o1:              # dropped: only on a wrap (u ran past 1)
                    assert u1 < u0 and j >= 18, (t, j, u0, u1)
                    seen["drop"] += 1
                if o1 and not o0:              # added: only on a wrap, at the centre
                    assert u1 < u0 and W.TAB[u1 >> 6] < 64 * 16, (t, j, u0, u1)
                    seen["add"] += 1
        if t == 6900:
            assert _shown(th) == 18, _shown(th)
        prev[0] = now

    _run(r, cap, phases, 12000, each)
    assert seen["drop"] == 52 and seen["add"] == 52, seen
    assert _shown(r.theme) == 70


def test_rate_per_zone():
    # no surge on a ghost beat, so a factor-1 star flies rate[z] trips a second
    _need_fb()
    r = _renderer()
    cap = FrameCapture()
    for z in range(4):
        phases = [(0, rs.hunt(z, ring_live=False))]
        steps = []
        _run(r, cap, phases, 2000, lambda t, r, ev: steps.append(r.theme.prm[W.P_G]))
        trips = sum(steps[1:11]) / 65536.0      # 10 frames of 100 ms
        assert abs(trips - T.THEME_PARAMS["warp"]["rate"][z]) < 0.005, (z, trips)
        assert r.theme.surge == 0


def test_surge_on_each_live_beat():
    # the surge is x(1 + 1.3 e^(-t/170)) from the field's ring spawn, so it
    # starts on the frame whose heartbeat plays (WARM: DOUBLE every ring)
    _need_fb()
    r = _renderer()
    cap = FrameCapture()
    _, phases, run_ms = rt.fixture("warm_arrow")
    phases = [(0, dict(phases[0][1], glyph="glow", arrow_deg=None))]
    rec = []
    _run(r, cap, phases, 4000, lambda t, r, ev: rec.append(
        (t, len(ev), r.theme.surge, r.theme.prm[W.P_G], rt.T0 + t - r.field.last_spawn)))
    beats = 0
    for t, nev, surge, g, age in rec[1:]:
        want = W.SURGE[age // 8] if age < 1024 else 0
        assert surge == want, (t, age, surge)
        if nev:
            beats += 1
            assert age == 0 and surge == 333, (t, age, surge)      # 1.3 in Q8
        # flight speed on the beat vs 500 ms later
        if age == 0 and t + 500 <= 4000:
            g2 = [x[3] for x in rec if x[0] == t + 500][0]
            ratio = g / float(g2)
            assert abs(ratio - 2.3 / (1 + 1.3 * 2.718281828 ** (-500 / 170.0))) < 0.05, ratio
    assert beats == 4, beats
    # HOT: a beat every 500 ms
    _, phases, run_ms = rt.fixture("hot")
    ages = []
    _run(r, cap, phases, 2000, lambda t, r, ev: ages.append(rt.T0 + t - r.field.last_spawn))
    assert ages[1:] == [100, 200, 300, 400, 0] * 4, ages


def test_ghost_beat_is_grey_and_flat():
    _need_fb()
    r = _renderer()
    cap = FrameCapture()
    grey = _lut("warp", "grey")
    green = _lut("warp", "green")
    for fx, lut, flat in (("far_ghost", grey, True), ("far", green, False)):
        _, phases, run_ms = rt.fixture(fx)
        gs = []
        _run(r, cap, phases, run_ms, lambda t, r, ev: gs.append(r.theme.prm[W.P_G]))
        th = r.theme
        assert (len(set(gs[1:])) == 1) == flat, (fx, gs)
        n = 0
        for j in range(W.NMAX):
            o = j * W.ST_N
            if th.st[o + 2] != W.NONE:
                assert th.st[o + 6] in lut and th.st[o + 7] in lut, (fx, j)
                n += 1
        assert n >= 6, (fx, n)


def test_listening_drifts_inward():
    _need_fb()
    r = _renderer()
    cap = FrameCapture()
    _, phases, run_ms = rt.fixture("searching")
    us = []
    _run(r, cap, phases, run_ms, lambda t, r, ev: us.append([_u(r.theme, j) for j in range(14)]))
    assert r.theme.prm[W.P_G] < 0
    inward = 0
    for k in range(1, len(us)):
        for j in range(14):
            if us[k][j] < us[k - 1][j]:
                inward += 1
            else:
                assert us[k][j] > 60000, (k, j)    # only a wrap back to the edge goes up
    assert inward > 14 * (len(us) - 2)


def test_held_moments():
    # still: in place, twinkling; scan: in place and unchanged, so after the
    # iris has opened and the stars have faded in nothing is reported dirty
    _need_fb()
    r = _renderer()
    cap = FrameCapture()
    for fx, twinkle in (("pairing_seen", True), ("scan_sweep", False), ("direction_turn", False)):
        _, phases, run_ms = rt.fixture(fx)
        rec = []
        _run(r, cap, phases, run_ms, lambda t, r, ev: rec.append(
            (t, [_u(r.theme, j) for j in range(W.NMAX)], r.theme.dirty,
             [r.theme.st[j * W.ST_N + 6] for j in range(W.NMAX)])))
        assert r.theme.prm[W.P_G] == 0 and r.theme.prm[W.P_TW] == (1 if twinkle else 0)
        late = [x for x in rec if x[0] >= 1000]
        for a, b in zip(late, late[1:]):
            assert a[1] == b[1], fx                     # nobody moves
        recolour = sum(1 for a, b in zip(late, late[1:]) if a[3] != b[3])
        if twinkle:
            assert recolour == len(late) - 1, fx
        else:
            assert recolour == 0, fx
            assert all(x[2] == 0 for x in late), (fx, [x[2] for x in late])
        # every shown star is on screen, outside the lens and its rim
        th = r.theme
        rmin = r.field.iris + T.IRIS_RIM_PX
        for j in range(W.NMAX):
            o = j * W.ST_N
            if th.st[o + 1]:
                rq = W.TAB[th.st[o] >> 6]
                assert rq >= rmin * 16, (fx, j, rq >> 4)
                assert th.st[o + 2] != W.NONE and 0 <= th.st[o + 2] and th.st[o + 4] < 240, (fx, j)


def test_wake_shows_held_stars_at_once():
    # §8: the first frame after the screen was off is the current state, so
    # held stars are all in view at full level, with no fade-in
    _need_fb()
    r = _renderer()
    cap = FrameCapture()
    _, phases, _ = rt.fixture("scan_sweep")
    _run(r, cap, phases, 1000)
    for t in range(1100, 2000, 100):
        r.frame(rt.params_at(phases, t), None, rt.T0 + t)
    r.frame(rt.params_at(phases, 2000), cap, rt.T0 + 2000)
    th = r.theme
    assert th.wake
    st = th.st
    full = [j for j in range(W.NMAX) if st[j * W.ST_N + 1] == 256 and st[j * W.ST_N + 2] != W.NONE]
    assert len(full) == 24 and full == list(range(24)), full


def test_found_slows_to_a_stop_and_twinkles_gold():
    _need_fb()
    r = _renderer()
    cap = FrameCapture()
    _, phases, _ = rt.fixture("found_celebrate")
    phases = phases + [(1500, dict(rs._found, sub="result", word="FOUND 1:48",
                                   top_text="BUTTON: PLAY AGAIN"))]
    rec = []
    _run(r, cap, phases, 3000, lambda t, r, ev: rec.append((t, r.theme.prm[W.P_G])))
    gs = [g for t, g in rec if 0 < t < 1600]
    assert gs[0] > 0 and all(b <= a for a, b in zip(gs, gs[1:])), gs
    # 1.6 e^(-t/400) trips a second, 100 ms frames
    g1 = [g for t, g in rec if t == 100][0]
    want = 1.6 * 2.718281828 ** (-100 / 400.0) * 0.1
    assert abs(g1 / 65536.0 - want) < 0.003, (g1 / 65536.0, want)
    assert all(g == 0 for t, g in rec if t >= 1600)
    th = r.theme
    assert th.prm[W.P_TW] == 1 and _shown(th) == 56
    gold = _lut("warp", "gold")
    for j in range(W.NMAX):
        o = j * W.ST_N
        if th.st[o + 2] != W.NONE:
            assert th.st[o + 6] in gold, j


def test_menu_holds_the_stars():
    _need_fb()
    r = _renderer()
    cap = FrameCapture()
    _, phases, run_ms = rt.fixture("menu")
    rec = []
    _run(r, cap, phases, run_ms, lambda t, r, ev: rec.append(
        (t, [r.theme.st[j * W.ST_N] for j in range(W.NMAX)],
         [r.theme.st[j * W.ST_N + 1] for j in range(W.NMAX)])))
    menu = [x for x in rec if x[0] >= 1000]
    for a, b in zip(menu, menu[1:]):
        assert a[1] == b[1] and a[2] == b[2], (a[0], b[0])
    assert rec[5][1] != rec[9][1]                      # they flew before the menu


def test_a_step_over_a_trip_wraps():
    # a 250 ms frame (the longest step the clock gives) on a HOT beat moves
    # the fastest stars more than a whole trip: the phase wraps mod 1 and the
    # star restarts as on any wrap, never runs past the end of its trip
    _need_fb()
    r = _renderer()
    cap = FrameCapture()
    hot = rs.hunt(3, dist_band="~5")
    fast = max(W.CST[j * W.CS_N] for j in range(W.NMAX))
    over = 0
    r.reset()
    for t in range(0, 5001, 250):
        r.frame(rs.make_params(t_ms=rt.T0 + t, **hot), cap, rt.T0 + t)
        th = r.theme
        if (th.prm[W.P_G] * fast) >> 8 > 65535:
            over += 1
        for j in range(W.NMAX):
            u = th.st[j * W.ST_N]
            assert 0 <= u <= 65535, (t, j, u)
    assert over > 0, "no step over a trip"


def test_no_surge_from_a_listening_spawn():
    # SEARCHING's inward rings are no beats: FAR right after one does not
    # surge until its own first ring (outward, live) spawns, then does
    _need_fb()
    r = _renderer()
    cap = FrameCapture()
    _, sp, _ = rt.fixture("searching")
    phases = [(0, sp[0][1]), (3300, rs.hunt(0, dist_band="~40"))]
    rec = []

    def each(t, r, ev):
        f = r.field
        out = 0
        for k in range(len(f.r_on)):
            if f.r_on[k] and f.r_t0[k] == f.last_spawn and f.r_v[k] > 0:
                out = 1
        rec.append((t, r.theme.surge, out, rt.T0 + t - f.last_spawn))

    _run(r, cap, phases, 6000, each)
    far = [x for x in rec if x[0] >= 3300]
    assert far[0][3] < 1000                      # the inward spawn is young
    first = [x[0] for x in far if x[2]]
    assert first, "FAR never spawned a ring"
    for t, surge, out, age in far:
        if t < first[0]:
            assert surge == 0, (t, surge, age)
    beat = [x for x in far if x[0] == first[0]][0]
    assert beat[1] == W.SURGE[beat[3] // W.SURGE_STEP] and beat[1] > 0, beat


def test_reset_restores_a_fresh_theme():
    # after a renderer reset the stars start where a fresh theme starts
    # them, with every clock and latch cleared, so frames match a fresh one's
    _need_fb()
    a = _renderer()
    b = _renderer()
    cap = FrameCapture()
    _, phases, run_ms = rt.fixture("hot")
    _run(a, cap, phases, run_ms)
    a.reset()
    ta = a.theme
    tb = b.theme
    assert bytes(ta.st) == bytes(tb.st)
    for k in ("twc", "fage", "ls", "ghost", "lbeat", "surge", "key", "xf_on", "n"):
        assert getattr(ta, k) == getattr(tb, k), k
    ca = FrameCapture()
    cb = FrameCapture()
    for fx in ("pairing_seen", "far"):
        _, phases, run_ms = rt.fixture(fx)
        _run(a, ca, phases, run_ms)
        b = _renderer()
        _run(b, cb, phases, run_ms)
        assert ca.buf == cb.buf, fx


def test_wake_into_found_never_replays_the_slowdown():
    # a wake shows FOUND as it is (§4A rule 6): stopped when the renderer
    # did not see FOUND start, where the slow-down is by then when it did
    # (FOUND began in a dark spell); only the burst frame starts it at 1.6
    _need_fb()
    r = _renderer()
    cap = FrameCapture()
    cel = dict(rs._found, sub="celebrate", word="FOUND")
    gs = []
    _run(r, cap, [(0, cel)], 1000, lambda t, r, ev: gs.append(r.theme.prm[W.P_G]))
    assert all(g == 0 for g in gs), gs
    r.reset()
    hot = rs.hunt(3, dist_band="<3")
    for t in range(0, 900, 100):
        kw = hot if t < 500 else (dict(cel, burst=True) if t == 500 else cel)
        r.frame(rs.make_params(t_ms=rt.T0 + t, **kw), cap if t < 500 or t >= 800 else None,
                rt.T0 + t)
    assert r.theme.wake
    r.frame(rs.make_params(t_ms=rt.T0 + 900, **cel), cap, rt.T0 + 900)
    want = 1.6 * 2.718281828 ** (-400 / 400.0) * 0.1        # 400 ms into FOUND
    assert abs(r.theme.prm[W.P_G] / 65536.0 - want) < 0.003, r.theme.prm[W.P_G]


def test_made_in_the_menu_takes_the_screen_under_it():
    # a Warp chosen in the MENU over HOT shows HOT's 70 stars at HOT's
    # brightness (the MENU's own params say WARM), and keeps them when the
    # MENU closes
    _need_fb()
    r = ThemedRenderer("ripple", overlays=False)
    cap = FrameCapture()
    hot = rs.hunt(3, dist_band="~5")
    phases = [(0, hot), (600, dict(rs._menu, sub="1v", menu_rows=rs.MENU_ROWS)), (1600, hot)]
    ifac = W.BI0 + ((W.BI1 * int(hot["intensity"] * 256)) >> 8)
    for t in range(0, 2100, 100):
        if t == 1000:
            r.set_theme("warp")
        r.frame(rt.params_at(phases, t), cap, rt.T0 + t)
        if t >= 1000:
            th = r.theme
            assert th.n == 70 and th.prm[W.P_N] == 70 and th.m == W.M_LIVE, (t, th.n)
            assert th.prm[W.P_IFAC] == ifac, (t, th.prm[W.P_IFAC], ifac)
            if t >= 1600:
                assert not th.xf_on, t


def test_held_stars_move_once_while_the_iris_opens():
    # a held star behind the lens moves out once, on the wake, past the size
    # the lens is opening to; none moves again while the iris opens
    _need_fb()
    r = _renderer()
    cap = FrameCapture()
    for fx in ("scan_sweep", "direction_turn", "pairing_seen"):
        _, phases, run_ms = rt.fixture(fx)
        rec = []
        _run(r, cap, phases, run_ms, lambda t, r, ev: rec.append(
            (t, r.field.iris, [_u(r.theme, j) for j in range(W.NMAX)])))
        assert rec[0][1] < rec[-1][1], (fx, rec[0][1], rec[-1][1])      # the iris opened
        for a, b in zip(rec, rec[1:]):
            assert a[2] == b[2], (fx, b[0], [j for j in range(W.NMAX) if a[2][j] != b[2][j]])


def test_calibrate_fill_covers_stars_in_place():
    # the PAIRING calibrate fill grows over the held stars: they stay where
    # they are, hidden while under it, instead of being moved out ahead of
    # it again and again (only the lens moves held stars)
    _need_fb()
    r = _renderer()
    cap = FrameCapture()
    _, ph, _ = rt.fixture("pairing_seen")
    phases = [(0, ph[0][1])] + [(1000 * (k + 1), dict(rs._cal, countdown=3 - k)) for k in range(3)]
    rec = []
    _run(r, cap, phases, 4000, lambda t, r, ev: rec.append(
        (t, [_u(r.theme, j) for j in range(W.N_STILL)],
         r.field.fill_r if r.field.fill_v else 0,
         [r.theme.st[j * W.ST_N + 2] for j in range(W.N_STILL)])))
    for a, b in zip(rec, rec[1:]):
        if b[0] >= 1000:
            assert a[1] == b[1], (b[0], [j for j in range(W.N_STILL) if a[1][j] != b[1][j]])
    t, us, fr, boxes = [x for x in rec if x[0] == 2500][0]      # half-way: some covered
    lim = (fr + 3 + W.CLIP_PX) * 16
    under = [j for j in range(W.N_STILL) if W.TAB[us[j] >> 6] < lim]
    assert len(under) >= 3, (fr, under)
    assert all(boxes[j] == W.NONE for j in under), under
    assert any(boxes[j] != W.NONE for j in range(W.N_STILL))
    assert all(b == W.NONE for b in rec[-1][3])                 # all covered by the end


def test_steady_frames_allocate_nothing():
    # hard rule 2: once a moment's first frames are drawn a frame allocates
    # nothing (the floor and glow targets used to come back as a tuple)
    _need_fb()
    import micropython
    r = _renderer()
    cap = FrameCapture()
    for fx in ("near", "warm_arrow"):                # the renderer's one-off buffers (events)
        _, phases, run_ms = rt.fixture(fx)
        _run(r, cap, phases, run_ms)
    for fx in ("far", "hot", "far_ghost", "searching", "pairing_seen", "scan_sweep",
               "found_result", "saver"):
        _, phases, run_ms = rt.fixture(fx)
        ps = [rt.params_at(phases, t) for t in range(0, run_ms + 1, rt.STEP_MS)]
        r.reset()
        for n in range(len(ps)):
            if n < 3:
                r.frame(ps[n], cap, rt.T0 + n * rt.STEP_MS)
                continue
            ok = False              # heap locked: any allocation raises at once
            micropython.heap_lock()
            try:
                r.frame(ps[n], cap, rt.T0 + n * rt.STEP_MS)
                ok = True
            except MemoryError:
                pass
            finally:
                micropython.heap_unlock()
            assert ok, (fx, n, "allocated")


# ---- drawing -------------------------------------------------------------------------
def test_streaks_cover_the_step():
    # a moving star's box spans where it was and where it is (blur, not jumps)
    _need_fb()
    r = _renderer()
    cap = FrameCapture()
    _, phases, run_ms = rt.fixture("hot")
    prev = [None]
    checked = [0]

    def each(t, r, ev):
        th = r.theme
        now = [_u(th, j) for j in range(W.NMAX)]
        if prev[0] is not None:
            for j in range(W.NMAX):
                u0 = prev[0][j]
                u1 = now[j]
                o = j * W.ST_N
                if u1 < u0 or th.st[o + 2] == W.NONE:
                    continue                     # wrapped, or not drawn
                r0 = W.TAB[u0 >> 6]
                r1 = W.TAB[u1 >> 6]
                if r0 < th.prm[W.P_RMIN] or r1 >= W.RFAR or r1 - r0 < 16:
                    continue                     # clipped, off screen, or a dot
                c = j * W.CS_N
                for rq in (r0, r1):
                    x = (1920 + ((rq * W.CST[c + 1]) >> 14)) >> 4
                    y = (1920 - ((rq * W.CST[c + 2]) >> 14)) >> 4
                    assert th.st[o + 2] <= x <= th.st[o + 4], (t, j)
                    assert th.st[o + 3] <= y <= th.st[o + 5], (t, j)
                if r1 - r0 >= 64:
                    checked[0] += 1
        prev[0] = now

    _run(r, cap, phases, run_ms, each)
    assert checked[0] > 100, checked[0]


def test_never_inside_the_lens_or_fill():
    # on every frame, the iris opening included, every pixel of ring index
    # below the rim's outer edge (and the calibrate fill's edge) shows the
    # base palette: no star is drawn there
    _need_fb()
    r = _renderer()
    cap = FrameCapture()
    idx = r.map.idx
    seen = [0]
    for fx in ("warm_arrow", "scan_sweep", "pairing_seen", "pairing_calibrate", "direction_turn"):
        _, phases, run_ms = rt.fixture(fx)

        def each(t, r, ev):
            seen[0] += 1
            f = r.field
            lim = f.iris + T.IRIS_RIM_PX if f.iris else 7
            if f.fill_v and f.fill_r + 3 > lim:
                lim = f.fill_r + 3
            pal = r.theme.rad.pal_arr
            buf = cap.buf
            lo = 120 - lim - 1
            hi = 120 + lim + 1
            for y in range(max(0, lo), min(W_, hi)):
                for x in range(max(0, lo), min(W_, hi)):
                    i = idx[y * W_ + x]
                    if i < lim:
                        o = (y * W_ + x) * 2
                        assert buf[o] | (buf[o + 1] << 8) == pal[i], (fx, t, x, y, i)

        _run(r, cap, phases, run_ms, each)
    assert seen[0] == 5 * 16, seen[0]                  # every frame of the five fixtures


def test_stars_are_drawn_and_brighter_than_the_base():
    # in HOT, streak pixels stand out of the floor and glow, never darker
    _need_fb()
    r = _renderer()
    cap = FrameCapture()
    _, phases, run_ms = rt.fixture("hot")
    _run(r, cap, phases, run_ms)
    pal = r.theme.rad.pal_arr
    idx = r.map.idx
    lut = _lut("warp", "green")
    buf = cap.buf
    lit = 0
    for o in range(0, W_ * W_):
        c = buf[2 * o] | (buf[2 * o + 1] << 8)
        b = pal[idx[o]]
        if c != b:
            lit += 1
            assert c in lut and b in lut and lut.index(c) > lut.index(b), (o % W_, o // W_)
    assert 600 < lit < 6000, lit
