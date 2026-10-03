"""ui/ renderer: LUT/palette maths, ring timing, heartbeats, text cache, allocation.

Pure maths (LUTs, ring map, palette kernel, PNG) runs on CPython too; the
frame tests need MicroPython framebuf and run in the wasm runner:

    node tools/mpy/run.mjs tests/runner.py test_renderer
"""

import gc
import sys

try:
    import framebuf  # noqa: F401
    HAVE_FB = True
except ImportError:
    HAVE_FB = False
    print("SKIP test_renderer frame tests: framebuf is MicroPython-only")

from ui import GREY, PROX, BG_IRIS, swap16
from ui import field as fld

if HAVE_FB:
    from ui.renderer import FrameCapture, Renderer, make_params
    from ui import text as tx
    from ui import font

T0 = 100000


def _warm(**kw):
    d = dict(screen="WARM", zone=2, intensity=0.55, speed_px_s=80, pulse_period_ms=1000,
             wavelength_px=80, glow_r_px=42, heartbeat="DOUBLE", dist_band="~10")
    d.update(kw)
    return d


def _fr(r, p, cap=None, now=None):
    """Frame on the simulated clock (default p.t_ms), not ticks_ms()."""
    return r.frame(p, cap, p.t_ms if now is None else now)


def _run(r, cap, kw, ms, t0=T0, step=50):
    evs = []
    t = 0
    while t <= ms:
        ev = _fr(r, make_params(t_ms=t0 + t, **kw), cap)
        for e in ev:
            evs.append((t, e))
        t += step
    return evs


def _px(buf, x, y):
    i = (y * 240 + x) * 2
    return buf[i] | (buf[i + 1] << 8)       # framebuf's native (swapped) int


# ---- pure maths (CPython + MicroPython) ----------------------------------------
def _ref_lut(stops):
    out = []
    for j in range(64):
        pos = j * 7 / 63.0
        k = min(6, int(pos))
        f = pos - k
        a, b = stops[k], stops[k + 1]
        ch = []
        for sh, bits in ((16, 5), (8, 6), (0, 5)):
            c = ((a >> sh) & 255) * (1 - f) + ((b >> sh) & 255) * f
            c8 = int(c + 0.5 + 1e-9)
            ch.append((c8 * ((1 << bits) - 1) + 127) // 255)
        out.append(swap16((ch[0] << 11) | (ch[1] << 5) | ch[2]))
    return out


def test_lut_is_swapped_snapped_interpolation():
    import ui
    from ui import RAMP_HEX
    for name in ("green", "gold", "grey"):
        ref = _ref_lut(RAMP_HEX[name])
        got = list(fld.LUTS[name].c)
        assert got == ref, name
        # stops land exactly on the token colours (LUT[9k] = stop k)
        for k in range(8):
            assert got[9 * k] == ui.sw(RAMP_HEX[name][k]), (name, k)
    assert fld.LUTS["green"].c[0] == PROX[0] and fld.LUTS["green"].c[63] == PROX[7]
    assert fld.LUTS["green"].c[27] == PROX[3] == 0x871B
    assert fld.LUTS["grey"].c[54] == GREY[6]


def test_colour_constants_match_tokens():
    try:
        import json
        with open("docs/design/tokens.json") as f:
            tok = json.load(f)
    except (OSError, ImportError):
        return  # wasm runner does not ship docs/
    import ui
    col = tok["color"]
    for name, hx in ui.HEX.items():
        assert int(col[name]["rgb565_swapped"], 16) == ui.sw(hx), name
        assert int(col[name]["hex"][1:], 16) == hx, name
    for ramp, names in tok["ramp"].items():
        if ramp in ui.RAMP_HEX:
            for k, n in enumerate(names):
                assert int(col[n]["hex"][1:], 16) == ui.RAMP_HEX[ramp][k], n
    stops = tok["field"]["vignette_stops"]
    assert tuple((a, b) for a, b in stops) == fld.VIG_STOPS


def test_ring_map_matches_hypot():
    half = fld.build_map(120)
    for (x, y) in ((0, 0), (119, 119), (120, 120), (239, 0), (17, 93), (200, 60), (120, 0)):
        yy = y if y < 120 else 239 - y
        assert half[yy * 240 + x] == fld.ring_index(x, y), (x, y)
    assert max(half) == 168          # corner: 119.5*sqrt(2) = 168.998
    full = fld.build_map(240)
    assert full[239 * 240 + 239] == 168 and full[130 * 240 + 7] == fld.ring_index(7, 130)


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
    f.build(0, core=True)
    for i in range(7):
        assert f.pal_arr[i] == PROX[6], i          # sun floor: level 6
    assert f.pal_arr[7] != PROX[6]
    f = _field(iris=64)
    f.build(0, core=True)
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
    sys.path.insert(0, "tools")
    import png
    rgb = bytearray(4 * 3 * 3)
    for i in range(len(rgb)):
        rgb[i] = (i * 37) & 255
    try:
        import zlib
        zlib.decompress
    except (ImportError, AttributeError):
        return
    w, h, out = png.decode_rgb(png.encode(4, 3, rgb))
    assert (w, h) == (4, 3) and out == bytes(rgb)
    assert png.decode_rgb(b"\x89PNG\r\n\x1a\n" + png._chunk(b"IHDR", b"\0\0\0\1\0\0\0\1\x08\2\0\0\0")
                          + png._chunk(b"IDAT", png._stored(b"\0\1\2\3"))
                          + png._chunk(b"IEND", b""))[2] == b"\1\2\3"


# ---- frame tests (MicroPython framebuf) ------------------------------------------
def test_blit_key_semantics_probe():
    if not HAVE_FB:
        return
    assert tx.KEY_POST is True     # MicroPython 1.29: key compared after palette


def test_frame_pushes_ten_strips_and_colours():
    if not HAVE_FB:
        return
    r = Renderer()
    cap = FrameCapture()
    _run(r, cap, _warm(glyph="arrow", arrow_deg=0, cone_deg=31, arrow_style="solid_b"), 500)
    n0 = cap.pushes
    _fr(r, make_params(t_ms=T0 + 550, **_warm(glyph="arrow", arrow_deg=0, cone_deg=31,
                                                arrow_style="solid_b")), cap)
    assert cap.pushes - n0 == 10
    assert _px(cap.buf, 160, 160) == BG_IRIS            # inside iris r 64, off the dart
    assert _px(cap.buf, 120, 90) == PROX[6]             # solid_b dart body
    assert _px(cap.buf, 139, 68) == PROX[3]             # solid_b beam, beside the tip
    # every field pixel is the palette colour of its ring index
    pal = r.field.pal_arr
    for (x, y) in ((5, 120), (230, 60), (119, 3), (40, 200)):
        assert _px(cap.buf, x, y) == pal[fld.ring_index(x, y)], (x, y)


def test_core_dot_level_in_frame():
    if not HAVE_FB:
        return
    r = Renderer()
    cap = FrameCapture()
    _run(r, cap, dict(screen="FAR", zone=0, intensity=0.0, glow_r_px=20), 300)
    lut = fld.LUTS["green"].c
    for (x, y) in ((120, 120), (116, 120), (120, 124)):
        # level 6 floor, or higher where a crest just spawned (core lift)
        assert _px(cap.buf, x, y) in list(lut)[54:], (x, y)
    assert r.field.iris == 0


def test_half_and_full_map_frames_identical():
    if not HAVE_FB:
        return
    kw = _warm(glyph="chevrons", trend=-1, trend_strong=True, top_text="TAP TO SCAN")
    a = Renderer()
    b = Renderer(full_map=True)
    ca = FrameCapture()
    cb = FrameCapture()
    _run(a, ca, kw, 700)
    _run(b, cb, kw, 700)
    assert ca.buf == cb.buf


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
    if not HAVE_FB:
        return
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
    if not HAVE_FB:
        return
    r = Renderer()
    cap = FrameCapture()
    kw = _warm(pulse_period_ms=500, heartbeat="TICK", heartbeat_every=1)
    ev = _run(r, cap, kw, 2000)
    ticks = [t for t, e in ev if e == "TICK"]
    assert len(ticks) == 5, ticks            # spawns at 0, 500, ..., 2000
    assert ticks == [0, 500, 1000, 1500, 2000]
    r.reset()
    ev = _run(r, cap, _warm(pulse_period_ms=500, heartbeat="TICK", ring_live=False), 2000)
    assert ev == []                           # ghost rings: no haptic
    assert r.field.ghosts == 1
    r.reset()
    ev = _run(r, cap, _warm(pulse_period_ms=500, heartbeat="TICK", heartbeat_every=2), 2000)
    assert len(ev) == 2                       # FAR-style: every 2nd ring
    r.reset()                                 # screen off: state advances, no display
    n = 0
    for k in range(41):
        n += len(_fr(r, make_params(t_ms=T0 + 50 * k, **kw)))
    assert n == 5


def test_haptic_event_and_burst_once_per_params():
    if not HAVE_FB:
        return
    r = Renderer()
    cap = FrameCapture()
    p = make_params(t_ms=T0, **_warm(burst=True, haptic="CLOSER", heartbeat=None))
    assert _fr(r, p, cap) == ["CLOSER"]
    n_on = sum(r.field.r_on)
    assert _fr(r, p, cap, T0 + 50) == ()     # same params: no repeat
    assert sum(r.field.r_on) == n_on
    bursts = [k for k in range(fld.MAXR) if r.field.r_on[k] and r.field.r_amp[k] == 1792]
    assert len(bursts) == 1 and r.field.r_v[bursts[0]] == 160


def test_menu_freezes_rings_and_dims():
    if not HAVE_FB:
        return
    r = Renderer()
    cap = FrameCapture()
    _run(r, cap, _warm(), 600)
    menu = dict(screen="MENU", sub="0", intensity=0.55, speed_px_s=80, pulse_period_ms=1000)
    _run(r, cap, menu, 700, t0=T0 + 650)
    rs = [r.field.ring_r(k, T0 + 1350) for k in range(fld.MAXR) if r.field.r_on[k]]
    _run(r, cap, menu, 500, t0=T0 + 1400)
    rs2 = [r.field.ring_r(k, T0 + 1900) for k in range(fld.MAXR) if r.field.r_on[k]]
    assert rs == rs2 and rs
    assert r.field.dim == 128


def test_ramp_crossfade_to_gold():
    if not HAVE_FB:
        return
    r = Renderer()
    cap = FrameCapture()
    _run(r, cap, _warm(), 200)
    found = dict(screen="FOUND", sub="celebrate", ramp="gold", intensity=1.0, speed_px_s=0,
                 glow_r_px=90, glyph="check")
    _run(r, cap, found, 200, t0=T0 + 250)
    mid = list(r.field.mix.c)
    assert mid != list(fld.LUTS["gold"].c) and mid != list(fld.LUTS["green"].c)
    _run(r, cap, found, 300, t0=T0 + 500)     # 400 ms crossfade into FOUND
    assert list(r.field.mix.c) == list(fld.LUTS["gold"].c)


def test_renders_finder_render_params():
    if not HAVE_FB:
        return
    try:
        from finder import render_params as rp
    except ImportError:
        return
    r = Renderer()
    cap = FrameCapture()
    p = rp.make_params(t_ms=T0)                       # a valid SEARCHING frame
    assert rp.validate(p) == []
    assert _fr(r, p, cap) == ()
    assert cap.pushes == 10
    hot = dict(screen="HOT", zone=3, ramp="green", intensity=0.9, speed_px_s=120,
               pulse_period_ms=500, glow_r_px=56, ring_live=True, glyph="glow",
               dist_band="<3", word="BUMP!", heartbeat="TICK", status=(55, None, 4, True))
    assert rp.validate(rp.make_params(t_ms=T0, **hot)) == []
    for k in range(1, 33):          # iris 44 -> 0 (300 ms), grey -> green (1500 ms)
        _fr(r, rp.make_params(t_ms=T0 + 50 * k, **hot), cap)
    assert r.field.iris == 0
    assert _px(cap.buf, 120, 120) in (PROX[6], PROX[7])


def test_rings_interpolate_between_10hz_params():
    # §3/§4 rule 3: logic at 10 Hz, render at 20 fps. Each params object is
    # drawn twice; the render clock (now), not p.t_ms, must move the rings.
    if not HAVE_FB:
        return
    r = Renderer()
    cap = FrameCapture()
    hot = dict(screen="HOT", zone=3, intensity=0.9, speed_px_s=120, pulse_period_ms=500,
               glow_r_px=56, ring_live=True, heartbeat="TICK", dist_band="<3")
    f = r.field
    p = None
    prev = None
    steps = 0
    for k in range(40):
        if k % 2 == 0:
            p = make_params(t_ms=T0 + 50 * k, **hot)
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


def test_frame_default_clock_is_ticks_ms():
    if not HAVE_FB:
        return
    from finder.compat import ticks_add, ticks_diff, ticks_ms
    r = Renderer()
    p = make_params(t_ms=ticks_add(ticks_ms(), 500000), **_warm())
    r.frame(p)
    r.frame(p)                                        # same params, clock moves on
    assert 0 <= ticks_diff(ticks_ms(), r.field.t) < 5000, r.field.t


def test_text_cache_reuse():
    if not HAVE_FB:
        return
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
    if not HAVE_FB:
        return
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


def test_no_allocation_growth_over_100_frames():
    if not HAVE_FB:
        return
    r = Renderer()
    cap = FrameCapture()
    kw = _warm(glyph="arrow", arrow_deg=30, cone_deg=31, arrow_style="solid_b", trend=1,
               top_text="TAP TO SCAN", status=(64, 71, 3, False))
    ps = [make_params(t_ms=T0 + 50 * k, **kw) for k in range(130)]
    for k in range(30):
        _fr(r, ps[k], cap)
    gc.collect()
    free0 = gc.mem_free()
    a0 = gc.mem_alloc()
    for k in range(30, 130):
        _fr(r, ps[k], cap)
    grown = gc.mem_alloc() - a0
    gc.collect()
    free1 = gc.mem_free()
    assert abs(free1 - free0) < 512, (free0, free1)
    # float params are converted once per params object and heartbeat event
    # lists are reused, so a steady frame loop allocates nothing
    assert grown < 512, grown


# ---- review round 1 regressions --------------------------------------------------
def test_viper_self_check_covers_standing_wave():
    # a kernel that differs only in the FOUND standing-wave branch must fail
    ns = {"ptr16": lambda x: x, "ptr32": lambda x: x}
    py = {"ptr16": lambda x: x, "ptr32": lambda x: x}
    exec(fld._KSRC, py)
    assert fld.kernel_agrees(py["pal_kernel"], py["pal_kernel"])
    for a, b in (("tp[426 + (i & 31)]", "tp[426 + (i & 15)]"), ("* sb) >> 8)", "* sb) >> 7)")):
        src = fld._KSRC.replace(a, b)
        assert src != fld._KSRC, a
        exec(src, ns)
        assert not fld.kernel_agrees(py["pal_kernel"], ns["pal_kernel"]), b
    assert any(v[fld.P_STAND] and v[fld.P_RIM] >= 0 for v in fld.CHECK_PRMS)


def _run_ticks(r, cap, kw, t0, n, step=50):
    """n frames from tick t0 using wrap-aware ticks_add (the device clock)."""
    from finder.compat import ticks_add
    evs = []
    for k in range(n):
        t = ticks_add(t0, k * step)
        for e in _fr(r, make_params(t_ms=t, **kw), cap):
            evs.append((k * step, e))
    return evs


def _ticks_ok(r):
    f = r.field
    vals = [f.next_spawn, f.last_spawn, f.xf_t0, f.iris_t0] + \
        [f.r_t0[k] for k in range(fld.MAXR) if f.r_on[k]]
    return all(0 <= v < (1 << 30) for v in vals), vals


def test_rings_and_heartbeats_steady_across_ticks_wrap():
    if not HAVE_FB:
        return
    r = Renderer()
    kw = _warm(pulse_period_ms=500, heartbeat="TICK", heartbeat_every=1)
    ev = _run_ticks(r, None, kw, (1 << 30) - 5000, 201)      # 10 s across the wrap
    ticks = [t for t, e in ev if e == "TICK"]
    assert len(ticks) == 21, ticks                           # every 500 ms, no stall
    ok, vals = _ticks_ok(r)
    assert ok, vals
    # MENU hold shifts ring clocks across the wrap too
    menu = dict(screen="MENU", sub="0", intensity=0.55, speed_px_s=80, pulse_period_ms=500)
    _run_ticks(r, None, menu, (1 << 30) - 300, 20)
    ok, vals = _ticks_ok(r)
    assert ok, vals
    ev = _run_ticks(r, None, kw, 1500, 41)
    assert len([1 for t, e in ev if e == "TICK"]) >= 4


def test_no_phantom_arrow_or_dark_field_at_high_ticks():
    if not HAVE_FB:
        return
    from finder.compat import ticks_add
    cap = FrameCapture()
    r = Renderer()
    t0 = (1 << 29) + 5000
    _run_ticks(r, cap, _warm(), t0, 60)
    assert not r.arrow
    f = r.field
    assert f.fl > 77 and f.gl > 512 and f.pu > 1024, (f.fl, f.gl, f.pu)
    # a stale expire timestamp 2^29 ms old must not revive the dart
    r = Renderer()
    arrow = _warm(glyph="arrow", arrow_deg=40, cone_deg=31, arrow_style="solid_b")
    _run_ticks(r, cap, arrow, 1000, 10)
    _run_ticks(r, cap, _warm(), 1500, 20)
    assert not r.arrow
    _run_ticks(r, cap, _warm(), ticks_add(ticks_add(1500, 1 << 28), (1 << 28) + 100), 5)
    assert not r.arrow


def test_arrow_expire_shrink_only_on_same_zone_screen():
    if not HAVE_FB:
        return
    cap = FrameCapture()
    arrow = dict(glyph="arrow", arrow_deg=0, cone_deg=20, arrow_style="solid_a")
    r = Renderer()
    _run(r, cap, _warm(screen="HOT", zone=3, **arrow), 1000)
    found = dict(screen="FOUND", sub="celebrate", ramp="gold", intensity=1.0,
                 speed_px_s=0, glow_r_px=90, glyph="check")
    _fr(r, make_params(t_ms=T0 + 1050, **found), cap)
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


def test_calibrate_fill_follows_countdown_not_clock():
    if not HAVE_FB:
        return
    cap = FrameCapture()
    r = Renderer()
    cal = dict(screen="PAIRING", sub="calibrate", glyph="countdown", countdown=3,
               top_text="STAND 1 STEP APART", word="HOLD STILL", speed_px_s=0)
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


def test_unreliable_status_bars_warn():
    if not HAVE_FB:
        return
    from ui import TEXT_SEC, WARN
    cap = FrameCapture()
    r = Renderer()
    _fr(r, make_params(t_ms=T0, **_warm(status=(80, 80, 3, True))), cap)
    assert _px(cap.buf, 110, 27) == TEXT_SEC
    _fr(r, make_params(t_ms=T0 + 50, **_warm(status=(80, 80, 3, True, True))), cap)
    assert _px(cap.buf, 110, 27) == WARN


def test_hint_chip_outranks_pinned_status():
    if not HAVE_FB:
        return
    from ui.renderer import T_CHIP, T_STATUS
    cap = FrameCapture()
    r = Renderer()
    _fr(r, make_params(t_ms=T0, **_warm(status=(15, 80, 4, True),
                                         top_text="TAP WATCHES")), cap)
    assert r.top == T_CHIP
    _fr(r, make_params(t_ms=T0 + 50, screen="LINK_LOST", ramp="grey", speed_px_s=-30,
                        pulse_period_ms=3000, glyph="seeker", dist_band="~20",
                        dist_stale=True, status=(15, 80, 0, True)), cap)
    assert r.top == T_STATUS                          # strip outranks LAST


def test_sticky_banner_rises_once():
    if not HAVE_FB:
        return
    cap = FrameCapture()
    r = Renderer()
    lost = dict(screen="LINK_LOST", ramp="grey", speed_px_s=-30, pulse_period_ms=3000,
                glyph="seeker")
    dys = []
    for k in range(60):
        s = "LOST 0:%02d" % (k // 20)
        _fr(r, make_params(t_ms=T0 + 50 * k, banner=(s, "warn", True), **lost), cap)
        dys.append(r.bot_dy)
    assert dys[0] == 12 and max(dys[5:]) == 0, dys
    # a different non-sticky toast is a new toast and rises again
    _fr(r, make_params(t_ms=T0 + 3000, banner=("SCAN AGAIN", "info", False), **lost), cap)
    _fr(r, make_params(t_ms=T0 + 3050, banner=("BACK IN RANGE", "info", False), **lost), cap)
    assert r.bot_dy == 12


def test_scan_result_morphs_best_bin_into_dart():
    if not HAVE_FB:
        return
    cap = FrameCapture()
    r = Renderer()
    bins = [0.2] * 12
    bins[3] = 1.0
    res = dict(screen="SCANNING", sub="result", glyph="turn", intensity=0.5,
               speed_px_s=80, pulse_period_ms=1000)
    for k in range(25):                                  # 0..1200 ms
        e = 50 * k
        act = 3 if (e < 800 and not (e // 200) & 1) else None
        _fr(r, make_params(t_ms=T0 + e, sweep=(360.0, bins, act, False), **res), cap)
        if e == 700:
            assert not r.arrow and r.morph == -1
        if e == 900:
            assert r.arrow and r.morph == 3 and 0 < r.morph_e < 256
    assert r.morph == 3 and r.morph_e == 256
    # dart at 90 deg (bin 3): body right of centre, not the turn glyph
    assert _px(cap.buf, 150, 120) in (PROX[7], PROX[6]), hex(_px(cap.buf, 150, 120))
    # reveal: no second scale-in; the dart glides from 90 deg toward theta
    _fr(r, make_params(t_ms=T0 + 1250, **_warm(sub="reveal", glyph="arrow", arrow_deg=100,
                                                cone_deg=31, arrow_style="solid_a")), cap)
    assert r.arrow and not r._a_grow
    assert 90 * 16 <= r._a_q < 100 * 16


# ---- snapshot review round (visual defects) --------------------------------------
def _turn(pacer, inten=1.0):
    return _warm(sub="turn", glyph="arrow", arrow_deg=40, cone_deg=33, arrow_style="solid_b",
                 intensity=inten, word="TURN RIGHT", heartbeat=None,
                 sweep=(pacer, (None,) * 12, None, False))


def test_turn_halo_is_live_mirror_not_zone_glow():
    if not HAVE_FB:
        return
    cap = FrameCapture()
    r = Renderer()
    _run(r, cap, _turn(60), 1000)
    assert r.field.gr == 12 * 256, r.field.gr          # glow_r 12, not 20 + 40 I
    assert r.field.gl == 256 + 5 * 256                  # glow_amp 1 + 5 I at I = 1
    # the pacer has a bg.base keyline: 1 px outside its r 110 edge at 60 deg
    assert _px(cap.buf, 120 + 96, 120 - 56) == 0, hex(_px(cap.buf, 216, 64))
    assert _px(cap.buf, 120 + 87, 120 - 50) == PROX[6]


def test_turn_pacer_behind_yields_bottom_slot():
    if not HAVE_FB:
        return
    from ui.renderer import B_NONE, B_WORD, T_NONE
    cap = FrameCapture()
    r = Renderer()
    _run(r, cap, _turn(60), 200)
    assert r.bot == B_WORD and r.top == T_NONE
    _run(r, cap, _turn(170), 200, t0=T0 + 250)
    assert r.bot == B_NONE
    assert _px(cap.buf, 120, 215) == PROX[6]            # the wedge, not the word pill


def test_readout_keeps_mark_slot_while_arrow_up():
    if not HAVE_FB:
        return
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
    f.set_levels(0, 77 + ((333 * iq) >> 8), 512 + 4 * iq, 1024 + ((384 * iq) >> 8),
                 42 * 256, False)
    f.set_iris(0, 0)
    f.started = True
    f.spawn(0, 0, 80, f.pu, 3, 18, False)
    for t in (100, 150, 200):
        f.build(t, core=True)
        core = min(lvl[f.pal_arr[i]] for i in range(7))
        ring = max(lvl[f.pal_arr[i]] for i in range(7, 19))
        assert core >= ring, (t, core, ring)


def test_digit_one_flag_joins_stem():
    if not HAVE_FB:
        return
    rows = font.char_rows("1")
    stem = rows[0]
    for r in (1, 2):                          # flag grows left from the stem top
        assert rows[r] & stem == stem and rows[r] & ~stem & 0xFF, r
    assert rows[1] & ~stem & 0xFF & (rows[2] << 1) or rows[1] & rows[2]


def test_menu_backplate_hides_frozen_rings():
    if not HAVE_FB:
        return
    from ui import BG_BASE
    cap = FrameCapture()
    r = Renderer()
    _run(r, cap, _warm(), 600)
    menu = dict(screen="MENU", sub="0", intensity=0.55, speed_px_s=80, pulse_period_ms=1000)
    _run(r, cap, menu, 300, t0=T0 + 650)
    for y in (73, 117, 161):                   # the 4 px gaps between rows
        for x in (40, 120, 200):
            assert _px(cap.buf, x, y) == BG_BASE, (x, y)


def test_menu_scroll_triangles_and_highlight():
    """ui-spec MENU: triangles at x 207-213 (up y 36-41, down y 195-200) only when
    rows are hidden that way; the PROX[5] border marks the visible index in ``sub``."""
    if not HAVE_FB:
        return
    from ui import TEXT_SEC
    menu = dict(intensity=0.55, speed_px_s=80, pulse_period_ms=1000, screen="MENU")
    for sub, up, dn, k_sel in (("0v", False, True, 0), ("3^", True, False, 3),
                               ("2^v", True, True, 2), ("1", False, False, 1)):
        cap = FrameCapture()
        r = Renderer()
        _run(r, cap, dict(sub=sub, **menu), 100)
        assert (_px(cap.buf, 210, 39) == TEXT_SEC) == up, sub
        assert (_px(cap.buf, 210, 197) == TEXT_SEC) == dn, sub
        for k in range(4):
            y = (32, 76, 120, 164)[k] + 20             # row's left border, mid-height
            assert (_px(cap.buf, 24, y) == PROX[5]) == (k == k_sel), (sub, k, hex(_px(cap.buf, 24, y)))


def test_scan_active_bin_drawn_over_wedge():
    if not HAVE_FB:
        return
    cap = FrameCapture()
    r = Renderer()
    bins = [0.5] * 12
    sw = dict(screen="SCANNING", sub="sweep", glyph="turn", intensity=0.5, glow_r_px=12,
              speed_px_s=80, pulse_period_ms=1000)
    _run(r, cap, dict(sweep=(90, bins, 3, False), **sw), 200)
    # bin 3 (90 deg) sits mid-wedge: its prox.5 bar shows at r 75 on the wedge
    assert _px(cap.buf, 195, 120) == PROX[5], hex(_px(cap.buf, 195, 120))
    assert _px(cap.buf, 225, 120) == PROX[6]               # wedge lit past the bar


def test_no_allocation_turn_sweep_menu():
    if not HAVE_FB:
        return
    bins = [0.2, None, 0.6, 0.9, 1.0, 0.75, 0.4, None, 0.15, 0.1, None, 0.05]
    cases = (
        _turn(170),
        dict(screen="SCANNING", sub="sweep", glyph="turn", intensity=0.5, glow_r_px=12,
             speed_px_s=80, pulse_period_ms=1000, sweep=(100, bins, 3, False)),
        dict(screen="MENU", sub="1", intensity=0.55, speed_px_s=80, pulse_period_ms=1000),
        # the subs Game really emits: suffix parsing + scroll triangles (fb.poly)
        dict(screen="MENU", sub="0v", intensity=0.55, speed_px_s=80, pulse_period_ms=1000),
        dict(screen="MENU", sub="3^", intensity=0.55, speed_px_s=80, pulse_period_ms=1000),
        dict(screen="MENU", sub="2^v", intensity=0.55, speed_px_s=80, pulse_period_ms=1000),
        _warm(sub="walk", glyph="arrow", arrow_deg=0, cone_deg=31, arrow_style="solid_b",
              trend=0),
    )
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
