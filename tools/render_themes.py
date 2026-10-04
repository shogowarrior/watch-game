"""Theme previews and cost (ui-spec §4A): every theme through the real renderer.

    python3 tools/render_themes.py [theme ...]          # docs/design/themes/<theme>.png
    python3 tools/render_themes.py --bench [theme ...]  # ms per frame, per theme and moment
    python3 tools/render_themes.py --load [theme ...]   # staged load: steps, longest step
    python3 tools/render_themes.py --mp PATH ...        # run under another MicroPython
                                                        # (a 32-bit unix build runs viper)
    node tools/mpy/run.mjs tools/render_themes.py --emit [theme ...]

Each preview is a 4x2 contact sheet (960x480) of the SHEET moments, each
rendered for its run time at 10 fps (the frame lock on today's watch) and
captured on its last frame. As in tools/render_snapshots.py, frames are
rendered by MicroPython (framebuf); under ``--emit`` the PNG is printed as
base64 between ``@@PNG <theme>`` / ``@@END`` markers and the CPython entry
point writes the files.

``--bench`` times ``ThemedRenderer.frame`` per fixture (no display push; the
overlays included) and prints each theme's ms/frame next to Ripple's, which
is the budget (ui-spec §4A rule 7). Desktop MicroPython is far faster than
the watch, so read the ratios, not the ms (a 32-bit unix build runs the
frame about 150-200x faster than the watch, where the field and overlays
take about 30 ms of a frame). The wasm port has no viper, so there every
kernel runs as plain Python on both sides.

``FIXTURES`` (theme moments, each from a reset) and ``TRANSITIONS`` (moment
changes, crossfades, the MENU, toggles and frame gaps, each from a reset,
with their own frame times) are what tests/test_themes.py runs every theme
on.

``--load`` runs each theme's staged load (ui/themes ``stages``) and prints
its steps and the longest one, against the STEP_US budget (ui/themes/base.py).
"""

import sys

try:
    from tools import render_snapshots as rs
except ImportError:
    if sys.implementation.name == "micropython":   # run as a script: the repo root is cwd
        sys.path.insert(0, ".")
        from tools import render_snapshots as rs
    else:                                           # CPython entry point: no fixtures needed
        rs = None

OUT_DIR = "docs/design/themes"
T0 = 100000
STEP_MS = 100              # 10 fps

# name -> (phases [(offset_ms, kw)], run_ms); kw over render_snapshots.FIXTURE_BASE
if rs is not None:
    _hunt = rs.hunt
    _result = dict(rs._found, sub="result", word="FOUND 1:48", top_text="BUTTON: PLAY AGAIN")
    FIXTURES = [
        ("far", [(0, _hunt(0, dist_band="~40"))], 2400),
        ("near", [(0, _hunt(1, dist_band="~20"))], 1600),
        ("warm_arrow", [(0, _hunt(2, sub="walk", glyph="arrow", arrow_deg=30, cone_deg=31,
                                  arrow_style="solid_b", trend=1, dist_band="~10"))], 1500),
        ("hot", [(0, _hunt(3, dist_band="~5"))], 1500),
        ("hot_bump", [(0, _hunt(3, dist_band="<3", word="BUMP!", top_text="BUMP WRISTS"))], 1500),
        ("far_ghost", [(0, _hunt(0, dist_band="~40", ring_live=False))], 2400),
        ("searching", [(0, dict(screen="SEARCHING", zone=None, ramp="grey", intensity=0.15,
                                speed_px_s=-36, pulse_period_ms=3200, wavelength_px=115,
                                glow_r_px=18, ring_live=False, glyph="seeker", word="SEARCHING",
                                status=rs.STATUS_OFF))], 3200),
        ("pairing_looking", [(0, dict(rs._pair, sub="looking", top_text="START OTHER WATCH",
                                      word="LOOKING", ring_live=False))], 3000),
        ("pairing_seen", [(0, dict(rs._pair, sub="seen", speed_px_s=0, wavelength_px=0,
                                   runes=rs.RUNES, top_text="SAME RUNES?", word="BUMP = YES"))],
         1500),
        ("pairing_calibrate", [(0, dict(rs._cal, countdown=3)),
                               (1000, dict(rs._cal, countdown=2))], 1500),
        ("scan_sweep", [(0, dict(rs._scan, sub="sweep", glyph="turn", intensity=0.6,
                                 glow_r_px=12, sweep=(135, rs.BINS, 4, False)))], 1500),
        ("direction_turn", [(0, _hunt(2, sub="turn", glyph="arrow", arrow_deg=60, cone_deg=33,
                                      arrow_style="solid_b", intensity=0.7, glow_r_px=12,
                                      word="TURN RIGHT", sweep=(60, (None,) * 12, None, False),
                                      heartbeat=None))], 1500),
        ("found_celebrate", [(0, dict(rs._found, sub="celebrate", word="FOUND", burst=True,
                                      haptic="FOUND")),
                             (100, dict(rs._found, sub="celebrate", word="FOUND"))], 600),
        ("found_result", [(0, _result)], 2500),
        ("link_lost", [(0, dict(rs._lost, banner=("LOST 0:12", "warn", True)))], 3000),
        ("menu", [(0, _hunt(2, dist_band="~10")),
                  (1000, dict(rs._menu, sub="1v", menu_rows=rs.MENU_ROWS))], 2000),
        ("far_sun", [(0, _hunt(0, dist_band="~40", sun=True))], 1500),
        ("saver", [(0, _hunt(1, glyph="battery", word="SAVER ON",
                             status=(10, 64, 3, True, False)))], 1500),
    ]
    def _kw(name):
        for f in FIXTURES:
            if f[0] == name:
                return f[1][0][1]
        raise KeyError(name)

    def _times(run_ms, step=STEP_MS, skip=()):
        return tuple(t for t in range(0, run_ms + 1, step) if t not in skip)

    _menu_found = dict(rs._menu, ramp="gold", zone=3, intensity=1.0, speed_px_s=0,
                       pulse_period_ms=1200, glow_r_px=90, sub="1v", menu_rows=rs.MENU_ROWS)
    _menu_scan = dict(rs._menu, sub="1v", menu_rows=rs.MENU_ROWS, intensity=0.6, glow_r_px=12)
    _split = _hunt(1, screen="PAIRING", sub="split", glyph="countdown", countdown=24,
                   top_text="NO PEEKING", word="SPLIT UP", heartbeat=None)
    _saver = (10, 64, 3, True, False)
    # name -> (phases, frame offsets ms)
    TRANSITIONS = [
        ("hot_to_found", [(0, _hunt(3, dist_band="<3")),
                          (800, dict(rs._found, sub="celebrate", word="FOUND", burst=True)),
                          (900, dict(rs._found, sub="celebrate", word="FOUND")),
                          (1400, _result)], _times(2600)),
        ("found_to_far", [(0, _result),
                          (800, _hunt(0, dist_band="~40"))], _times(2800)),
        ("far_to_searching", [(0, _hunt(0, dist_band="~40")),
                              (800, _kw("searching"))], _times(2800)),
        ("searching_to_hot", [(0, _kw("searching")),
                              (800, _hunt(3, dist_band="~5"))], _times(2400)),
        ("zones_up_down", [(0, _hunt(0, dist_band="~40")), (700, _hunt(1, dist_band="~20")),
                           (1400, _hunt(2, dist_band="~10", intensity=0.45)),
                           (1800, _hunt(2, dist_band="~10", intensity=0.65)),
                           (2300, _hunt(3, dist_band="<3")), (3000, _hunt(1, dist_band="~20"))],
         _times(3600)),
        ("glow_arrow_glow", [(0, _hunt(2, dist_band="~10")), (600, _kw("warm_arrow")),
                             (1500, _hunt(2, dist_band="~10"))], _times(2400)),
        ("hot_scan_hot", [(0, _hunt(3, dist_band="~5")), (600, _kw("scan_sweep")),
                          (1600, _kw("direction_turn")), (2400, _hunt(3, dist_band="~5"))],
         _times(3200)),
        ("calibrate_to_split", [(0, dict(rs._cal, countdown=1)), (700, _split)], _times(2400)),
        ("ghost_beats", [(0, _hunt(2, dist_band="~10")),
                         (500, _hunt(2, dist_band="~10", ring_live=False)),
                         (1900, _hunt(2, dist_band="~10"))], _times(3000)),
        ("menu_over_hunt", [(0, _hunt(2, dist_band="~10")),
                            (600, dict(rs._menu, sub="1v", menu_rows=rs.MENU_ROWS)),
                            (1600, _hunt(2, dist_band="~10"))], _times(2400)),
        ("menu_over_found", [(0, _result),
                             (600, _menu_found),
                             (1600, _result)],
         _times(2400)),
        ("menu_over_scan", [(0, _kw("scan_sweep")),
                            (600, dict(_menu_scan, screen="MENU")),
                            (1400, _kw("scan_sweep"))], _times(2000)),
        ("sun_and_saver", [(0, _hunt(1, dist_band="~20")),
                           (500, _hunt(1, dist_band="~20", sun=True)),
                           (1100, _hunt(1, dist_band="~20", status=_saver)),
                           (1700, _hunt(1, dist_band="~20"))], _times(2300)),
        # frame gaps: one wake (a 700 ms gap), the 20 fps lock, a missed slot
        # at the 7 fps lock (286 ms, not a wake)
        ("gap_wake", [(0, _hunt(3, dist_band="~5")),
                      (600, _hunt(1, dist_band="~20"))], _times(1800, skip=range(300, 1000))),
        ("fast_20fps", [(0, _kw("hot_bump"))], _times(1200, 50)),
        ("slow_7fps", [(0, _kw("searching")), (1100, _hunt(2, dist_band="~10"))],
         tuple(t for t in range(0, 2300, 143) if t not in (572, 1430))),
    ]
else:
    FIXTURES = []
    TRANSITIONS = []

SHEET = ("far", "near", "warm_arrow", "hot_bump", "scan_sweep", "found_result", "searching",
         "link_lost")


def fixture(name):
    for f in FIXTURES:
        if f[0] == name:
            return f
    raise KeyError(name)


def params_at(phases, t):
    """RenderParams of ``phases`` at offset ``t`` ms."""
    k = 0
    while k + 1 < len(phases) and phases[k + 1][0] <= t:
        k += 1
    return rs.make_params(t_ms=T0 + t, **phases[k][1])


def run(r, cap, phases, run_ms, step=STEP_MS, each=None):
    """Render ``phases`` from a reset for ``run_ms`` at ``step`` ms a frame;
    ``each(t, r, cap)`` after every frame. ``cap`` holds the last frame."""
    r.reset()
    t = 0
    while t <= run_ms:
        r.frame(params_at(phases, t), cap, T0 + t)
        if each is not None:
            each(t, r, cap)
        t += step


def _sheet(theme, cap_cls, renderer_cls):
    """960x480 RGB8 contact sheet of the SHEET moments for ``theme``."""
    from tools import png
    r = renderer_cls(theme)
    cap = cap_cls()
    out = bytearray(960 * 480 * 3)
    for n, name in enumerate(SHEET):
        _, phases, run_ms = fixture(name)
        run(r, cap, phases, run_ms)
        rgb = png.rgb565sw_to_rgb(cap.buf, 240, 240)
        ox = (n % 4) * 240
        oy = (n // 4) * 240
        for y in range(240):
            o = ((oy + y) * 960 + ox) * 3
            out[o:o + 720] = rgb[y * 720:(y + 1) * 720]
    return out


def _bench(names):
    import time
    from ui.renderer import FrameCapture
    from ui.themes import ThemedRenderer
    cap = FrameCapture()
    rows = {}
    for theme in ["ripple"] + [n for n in names if n != "ripple"]:
        r = ThemedRenderer(theme)
        for name, phases, run_ms in FIXTURES:
            us = 0
            n = 0
            r.reset()
            t = 0
            while t <= run_ms:
                p = params_at(phases, t)
                t0 = time.ticks_us()
                r.frame(p, cap, T0 + t)
                if t > 0:                       # the first frame builds caches
                    us += time.ticks_diff(time.ticks_us(), t0)
                    n += 1
                t += STEP_MS
            rows[(theme, name)] = us / max(1, n) / 1000.0
    themes = ["ripple"] + [n for n in names if n != "ripple"]
    print("bench %-18s" % "fixture" + "".join(" %10s" % th for th in themes))
    tot = [0.0] * len(themes)
    for name, _, _ in FIXTURES:
        line = "bench %-18s" % name
        for i, th in enumerate(themes):
            ms = rows[(th, name)]
            tot[i] += ms
            line += " %10.2f" % ms
        print(line)
    n = len(FIXTURES)
    print("bench %-18s" % "mean" + "".join(" %10.2f" % (x / n) for x in tot))
    print("bench %-18s" % "x ripple" + "".join(" %10.2f" % (x / tot[0]) for x in tot))


def _load(names):
    """Each theme's staged load from a fresh renderer: step count, the
    longest step and the total, in ms (a step's first run: module import,
    kernel compiles, self-checks)."""
    import time
    from ui.themes import ThemedRenderer, stages
    from ui.themes.base import STEP_US
    print("load %-10s %6s %9s %9s  (budget %.1f ms a step; the import is one step)"
          % ("theme", "steps", "max ms", "total ms", STEP_US / 1000))
    r = ThemedRenderer("ripple")
    for theme in names:
        out = [None]
        g = stages(theme, r, out)
        n = 0
        mx = 0
        tot = 0
        while True:
            t0 = time.ticks_us()
            try:
                next(g)
            except StopIteration:              # the last step does work too
                d = time.ticks_diff(time.ticks_us(), t0)
                tot += d
                n += 1
                if d > mx:
                    mx = d
                break
            d = time.ticks_diff(time.ticks_us(), t0)
            tot += d
            n += 1
            if d > mx:
                mx = d
        print("load %-10s %6d %9.2f %9.2f" % (theme, n, mx / 1000, tot / 1000))
        out[0] = None


def _mpy_main(args):
    import binascii
    import gc
    from finder import tuning as T
    from ui.renderer import FrameCapture
    from ui.themes import ThemedRenderer
    sel = [a for a in args[1:] if not a.startswith("-")]
    names = [n for n in T.THEME_NAMES if not sel or n in sel]
    if "--bench" in args:
        _bench(names)
        return
    if "--load" in args:
        _load(names)
        return
    for theme in names:
        rgb = _sheet(theme, FrameCapture, ThemedRenderer)
        if "--emit" not in args:
            continue
        from tools import png
        data = png.encode(960, 480, rgb)
        rgb = None
        print("@@PNG " + theme)
        for i in range(0, len(data), 57):
            sys.stdout.write(binascii.b2a_base64(data[i:i + 57]).decode())
        print("@@END")
        gc.collect()


def _cpython_main(args):
    import base64
    import os
    import subprocess
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    rest = list(args[1:])
    mp = None
    if "--mp" in rest:
        i = rest.index("--mp")
        mp = rest[i + 1]
        del rest[i:i + 2]
    if mp:
        cmd = [mp, "-X", "heapsize=64M", "tools/render_themes.py", "--emit"] + rest
    else:
        cmd = ["node", os.path.join(root, "tools", "mpy", "run.mjs"),
               "tools/render_themes.py", "--emit"] + rest
    proc = subprocess.run(cmd, cwd=root, capture_output=True, text=True)
    if proc.stderr:
        sys.stderr.write(proc.stderr)
    if proc.returncode != 0:
        sys.exit(proc.returncode)
    os.makedirs(os.path.join(root, OUT_DIR), exist_ok=True)
    seen = []
    name = None
    chunks = []
    for line in proc.stdout.splitlines():
        if line.startswith("@@PNG "):
            name = line[6:].strip()
            chunks = []
        elif line == "@@END" and name:
            path = os.path.join(root, OUT_DIR, name + ".png")
            with open(path, "wb") as f:
                f.write(base64.b64decode("".join(chunks)))
            seen.append(name)
            print("wrote " + os.path.relpath(path, root))
            name = None
        elif name:
            chunks.append(line.strip())
        elif line.strip():
            print(line)
    if "--bench" not in rest and "--load" not in rest and not seen:
        print("render_themes: no previews written")
        sys.exit(1)


if __name__ == "__main__":
    if sys.implementation.name == "micropython":
        _mpy_main(sys.argv)
    else:
        _cpython_main(sys.argv)
