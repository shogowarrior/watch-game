"""Render RenderParams fixtures through the real renderer into PNG snapshots.

One fixture per screen / sub-state of ui-spec §6. Each runs the renderer for
a while at 20 fps (so rings are mid-flight and crossfades settle) and keeps
the last full 240x240 frame.

    python3 tools/render_snapshots.py [name ...]   # writes docs/design/snapshots/*.png
    node tools/mpy/run.mjs tools/render_snapshots.py --emit [name ...]

The renderer needs framebuf, so frames are always rendered by MicroPython
(the WebAssembly port). Its filesystem is in-memory, so under ``--emit`` the
PNGs (encoded in MicroPython by tools/png.py) are printed as base64 between
``@@PNG <name>`` / ``@@END`` markers; the CPython entry point runs that
command and writes the files. ``--bench`` prints per-fixture ms/frame.
On a MicroPython port with a real filesystem, ``--write`` writes directly.
"""

import sys

try:
    from finder.compat import argv
except ImportError:  # CPython entry point: repo root not on sys.path yet
    argv = None

OUT_DIR = "docs/design/snapshots"
T0 = 100000
FPS_MS = 50

# ---- fixtures: name -> (phases [(offset_ms, kw)], run_ms) ---------------------
STATUS_OFF = (82, 76, 4, False)
_hunt = {
    0: dict(screen="FAR", zone=0, intensity=0.12, speed_px_s=40, pulse_period_ms=2400,
            wavelength_px=96, glow_r_px=25, heartbeat="TICK", heartbeat_every=2),
    1: dict(screen="NEAR", zone=1, intensity=0.33, speed_px_s=56, pulse_period_ms=1600,
            wavelength_px=90, glow_r_px=33, heartbeat="TICK"),
    2: dict(screen="WARM", zone=2, intensity=0.55, speed_px_s=80, pulse_period_ms=1000,
            wavelength_px=80, glow_r_px=42, heartbeat="DOUBLE"),
    3: dict(screen="HOT", zone=3, intensity=0.85, speed_px_s=120, pulse_period_ms=500,
            wavelength_px=60, glow_r_px=54, heartbeat="TICK"),
}


def hunt(z, **kw):
    d = dict(_hunt[z])
    d["status"] = STATUS_OFF
    d.update(kw)
    return d


_pair = dict(screen="PAIRING", zone=None, ramp="green", intensity=0.1, speed_px_s=-30,
             pulse_period_ms=3000, wavelength_px=90, glow_r_px=30, glyph="runes",
             status=STATUS_OFF)
_scan = dict(screen="SCANNING", zone=2, intensity=0.5, speed_px_s=80, pulse_period_ms=1000,
             glow_r_px=8, status=STATUS_OFF)
BINS = (0.2, 0.35, 0.6, 0.9, 1.0, 0.75, 0.4, None, 0.15, 0.1, None, 0.05)
_lost = dict(screen="LINK_LOST", zone=1, ramp="grey", intensity=0.33, speed_px_s=-30,
             pulse_period_ms=3000, wavelength_px=90, glow_r_px=16, ring_live=False,
             glyph="seeker", dist_band="~20", dist_stale=True, trend=0, status=STATUS_OFF)


def _m(base, **kw):
    d = dict(base)
    d.update(kw)
    return d


FIXTURES = [
    ("pairing_looking", [(0, _m(_pair, sub="looking", top_text="PAIR", word="LOOKING"))], 2000),
    ("pairing_seen", [(0, _m(_pair, sub="seen", speed_px_s=0, wavelength_px=0, top_text="SAME RUNES?",
                             word="TAP = YES"))], 1200),
    ("pairing_confirmed", [(0, _m(_pair, sub="confirmed", speed_px_s=0, wavelength_px=0,
                                  top_text="WAITING",
                                  word="WAITING"))], 1200),
    ("pairing_calibrate", [(0, _m(_pair, sub="calibrate", speed_px_s=0, wavelength_px=0,
                                  glyph="countdown",
                                  countdown=3, top_text="STAND 1 STEP APART",
                                  word="HOLD STILL")),
                           (1000, _m(_pair, sub="calibrate", speed_px_s=0, wavelength_px=0,
                                  glyph="countdown",
                                     countdown=2, top_text="STAND 1 STEP APART",
                                     word="HOLD STILL"))], 1500),
    ("pairing_split", [(0, hunt(1, screen="PAIRING", sub="split", glyph="countdown",
                                countdown=24, top_text="NO PEEKING", word="SPLIT UP",
                                heartbeat=None))], 2000),
    ("searching", [(0, dict(screen="SEARCHING", zone=None, ramp="grey", intensity=0.15,
                            speed_px_s=-36, pulse_period_ms=3200, wavelength_px=115,
                            glow_r_px=18, ring_live=False, glyph="seeker", word="SEARCHING",
                            status=STATUS_OFF))], 2500),
    ("far_glow_status", [(0, hunt(0, dist_band="~40", status=(64, 71, 4, True)))], 1500),
    ("far_hint", [(0, hunt(0, dist_band="60+", intensity=0.03, top_text="TAP TO SCAN"))], 1500),
    ("far_ghost_rings", [(0, hunt(0, dist_band="~40", ring_live=False))], 1500),
    ("near_warmer", [(0, hunt(1, glyph="chevrons", trend=1, dist_band="~20"))], 1500),
    ("near_warmer_strong", [(0, hunt(1, glyph="chevrons", trend=1, trend_strong=True,
                                     dist_band="~20"))], 1300),
    ("warm_colder", [(0, hunt(2, glyph="chevrons", trend=-1, dist_band="~10"))], 1500),
    ("warm_colder_strong", [(0, hunt(2, glyph="chevrons", trend=-1, trend_strong=True,
                                     dist_band="~10"))], 1200),
    ("warm_arrow_walk", [(0, hunt(2, sub="walk", glyph="arrow", arrow_deg=0, cone_deg=31,
                                  arrow_style="solid_b", trend=1, dist_band="~10"))], 1500),
    # walk with trend 0: the readout keeps its trend-mark slot (no 9 px shift)
    ("warm_arrow_walk_steady", [(0, hunt(2, sub="walk", glyph="arrow", arrow_deg=0,
                                         cone_deg=31, arrow_style="solid_b", trend=0,
                                         dist_band="~10"))], 1500),
    ("hot_glow", [(0, hunt(3, dist_band="~5", top_text="LOOK AROUND"))], 1500),
    ("hot_bump_ready", [(0, hunt(3, dist_band="<3", word="BUMP!", top_text="TAP WATCHES"))],
     1500),
    ("hot_arrow_solid_a", [(0, hunt(3, sub="walk", glyph="arrow", arrow_deg=20, cone_deg=18,
                                    arrow_style="solid_a", dist_band="~5"))], 1500),
    ("direction_reveal", [(0, hunt(2, sub="reveal", glyph="arrow", arrow_deg=120, cone_deg=22,
                                   arrow_style="solid_a", word="4 O'CLOCK"))], 1500),
    ("direction_turn", [(0, hunt(2, sub="turn", glyph="arrow", arrow_deg=60, cone_deg=33,
                                 arrow_style="solid_b", intensity=0.7, word="TURN RIGHT",
                                 sweep=(60, (None,) * 12, None, False),
                                 heartbeat=None))], 1500),
    # pacer near 6 o'clock (turning toward something behind): the wedge
    # would sit under the TURN word, so the bottom slot yields; I_mirror = 1
    ("direction_turn_behind", [(0, hunt(2, sub="turn", glyph="arrow", arrow_deg=10,
                                        cone_deg=33, arrow_style="solid_b", intensity=1.0,
                                        word="TURN RIGHT",
                                        sweep=(170, (None,) * 12, None, False),
                                        heartbeat=None))], 1500),
    ("direction_walk_outline", [(0, hunt(0, sub="walk", glyph="arrow", arrow_deg=0,
                                         cone_deg=52, arrow_style="outline", trend=-1,
                                         dist_band="~40", top_text="TAP TO RESCAN"))], 1500),
    ("found_celebrate", [(0, dict(screen="FOUND", sub="celebrate", zone=3, ramp="gold",
                                  intensity=1.0, speed_px_s=0, pulse_period_ms=1200,
                                  glow_r_px=90, glyph="check", burst=True,
                                  top_text="TIME 12:48", word="FOUND", haptic="FOUND",
                                  status=STATUS_OFF)),
                         (50, dict(screen="FOUND", sub="celebrate", zone=3, ramp="gold",
                                   intensity=1.0, speed_px_s=0, pulse_period_ms=1200,
                                   glow_r_px=90, glyph="check", top_text="TIME 12:48",
                                   word="FOUND", status=STATUS_OFF))], 450),
    ("found_result", [(0, dict(screen="FOUND", sub="result", zone=3, ramp="gold",
                               intensity=1.0, speed_px_s=0, pulse_period_ms=1200,
                               glow_r_px=90, glyph="check", top_text="TIME 12:48",
                               word="TAP=AGAIN", status=STATUS_OFF))], 2500),
    ("scan_ready_flat", [(0, _m(_scan, sub="ready", glyph="countdown", countdown=3,
                                top_text="HOLD AT CHEST", word="TURN RIGHT"))], 1500),
    ("scan_ready_tilted", [(0, _m(_scan, sub="ready", glyph="countdown", countdown=2,
                                  top_text="HOLD FLAT", word="TURN RIGHT"))], 1500),
    ("scan_sweep", [(0, _m(_scan, sub="sweep", glyph="turn", intensity=0.6, glow_r_px=12,
                           sweep=(135, BINS, 4, False)))], 1500),
    # sweep start: every bin still under 4 packets (hollow), the first active
    ("scan_sweep_start", [(0, _m(_scan, sub="sweep", glyph="turn", intensity=0.4,
                                 glow_r_px=12, sweep=(15, (None,) * 12, 0, False)))], 600),
    ("scan_sweep_paused", [(0, _m(_scan, sub="sweep", glyph="turn", intensity=0.3,
                                  glow_r_px=12, sweep=(200, BINS, 6, True)))], 1500),
    ("scan_result_ok", [(0, _m(_scan, sub="result", glyph="turn", intensity=0.6,
                               glow_r_px=12, sweep=(360, BINS, 4, False)))], 450),
    ("scan_result_morph", [(0, _m(_scan, sub="result", glyph="turn", intensity=0.6,
                                  glow_r_px=12, sweep=(360, BINS, 4, False))),
                           (200, _m(_scan, sub="result", glyph="turn", intensity=0.6,
                                    glow_r_px=12, sweep=(360, BINS, None, False))),
                           (400, _m(_scan, sub="result", glyph="turn", intensity=0.6,
                                    glow_r_px=12, sweep=(360, BINS, 4, False))),
                           (600, _m(_scan, sub="result", glyph="turn", intensity=0.6,
                                    glow_r_px=12, sweep=(360, BINS, None, False)))], 1000),
    # no fix: the Game returns to the zone screen at once and raises the toast
    ("scan_result_no_fix", [(0, hunt(2, dist_band="~10",
                                     banner=("NO FIX, TRY AGAIN", "info", False)))], 1500),
    ("link_lost", [(0, _m(_lost, banner=("LOST 0:12", "warn", True)))], 2500),
    # The LAST chip's last-trend mark: RenderParams has no field for it
    # (validate() forces trend 0 in LINK_LOST); the renderer reads ``trend``.
    ("link_lost_last_trend", [(0, _m(_lost, trend=1,
                                     banner=("LOST 0:27 KEEP ON", "warn", True)))], 2500),
    ("link_lost_friend_off", [(0, _m(_lost,
                                     banner=("FRIEND IS OFF", "critical", True)))], 2500),
    ("low_battery_saver", [(0, hunt(1, glyph="battery", word="SAVER ON",
                                    status=(10, 64, 3, True)))], 1500),
    ("low_battery_toast", [(0, hunt(0, dist_band="~40", status=(20, 70, 1, True),
                                    banner=("BATTERY 20%", "warn", False)))], 1500),
    ("relink_burst", [(0, hunt(1, dist_band="~20", burst=True, haptic="CLOSER",
                               banner=("BACK IN RANGE", "info", False))),
                      (50, hunt(1, dist_band="~20",
                                banner=("BACK IN RANGE", "info", False)))], 700),
    ("menu", [(0, hunt(2, dist_band="~10")),
              (1000, dict(screen="MENU", sub="1v", zone=2, intensity=0.55, speed_px_s=80,
                          pulse_period_ms=1000, glow_r_px=42, status=STATUS_OFF))], 1800),
    ("menu_scrolled", [(0, hunt(2, dist_band="~10")),
                       (1000, dict(screen="MENU", sub="2^", zone=2, intensity=0.55, speed_px_s=80,
                                   pulse_period_ms=1000, glow_r_px=42, status=STATUS_OFF))], 1800),
]

# Visible menu rows per fixture (Game.menu_rows: a 4-row window into the 5-row list).
MENU_ROWS = {
    "menu": ["RESUME", "SUN: OFF", "BUZZ: FULL", "PLACE: OUT"],
    "menu_scrolled": ["SUN: OFF", "BUZZ: FULL", "PLACE: IN", "END ROUND"],
}


def render_fixture(r, cap, name, phases, run_ms, make_params, bench=None):
    """Run a fixture; returns the number of heartbeat/haptic events seen."""
    import time
    r.reset()
    if name in MENU_ROWS:
        r.menu_rows = MENU_ROWS[name]
    events = 0
    k = 0
    kw = phases[0][1]
    us = 0
    n = 0
    t = 0
    while t <= run_ms:
        while k + 1 < len(phases) and phases[k + 1][0] <= t:
            k += 1
        kw = phases[k][1]
        p = make_params(t_ms=T0 + t, **kw)
        t_us = time.ticks_us()
        ev = r.frame(p, cap, T0 + t)
        us += time.ticks_diff(time.ticks_us(), t_us)
        n += 1
        events += len(ev)
        t += FPS_MS
    if bench is not None:
        bench.append((name, us / n / 1000.0))
    return events


def _mpy_main(args):
    import binascii
    import gc
    here = "tools"
    if here not in sys.path:
        sys.path.append(here)
    import png
    from ui.renderer import FrameCapture, Renderer, make_params
    emit = "--emit" in args
    write = "--write" in args
    bench = [] if "--bench" in args else None
    sel = [a for a in args[1:] if not a.startswith("-")]
    try:
        from finder.render_params import validate
    except ImportError:
        validate = None
    r = Renderer()
    cap = FrameCapture()
    for name, phases, run_ms in FIXTURES:
        if sel and name not in sel:
            continue
        if validate is not None:
            for _, kw in phases:
                for err in validate(make_params(t_ms=T0, **kw)):
                    print("WARN %s: %s" % (name, err))
        render_fixture(r, cap, name, phases, run_ms, make_params, bench)
        if not (emit or write):
            continue
        data = png.encode(240, 240, png.rgb565sw_to_rgb(cap.buf, 240, 240))
        if write:
            with open(OUT_DIR + "/" + name + ".png", "wb") as f:
                f.write(data)
            print("wrote", name)
        else:
            print("@@PNG " + name)
            for i in range(0, len(data), 57):
                sys.stdout.write(binascii.b2a_base64(data[i:i + 57]).decode())
            print("@@END")
        gc.collect()
    if bench:
        tot = 0.0
        for name, ms in bench:
            print("bench %-24s %6.2f ms/frame" % (name, ms))
            tot += ms
        print("bench mean %.2f ms/frame" % (tot / len(bench)))


def _cpython_main(args):
    import base64
    import os
    import subprocess
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    cmd = ["node", os.path.join(root, "tools", "mpy", "run.mjs"),
           "tools/render_snapshots.py", "--emit"] + args[1:]
    out = subprocess.run(cmd, cwd=root, check=True, capture_output=True, text=True).stdout
    os.makedirs(os.path.join(root, OUT_DIR), exist_ok=True)
    name = None
    chunks = []
    n = 0
    for line in out.splitlines():
        if line.startswith("@@PNG "):
            name = line[6:].strip()
            chunks = []
        elif line == "@@END" and name:
            with open(os.path.join(root, OUT_DIR, name + ".png"), "wb") as f:
                f.write(base64.b64decode("".join(chunks)))
            n += 1
            name = None
        elif name:
            chunks.append(line.strip())
        elif line.strip():
            print(line)
    print("wrote %d snapshots to %s" % (n, OUT_DIR))


if __name__ == "__main__":
    if sys.implementation.name == "micropython":
        _mpy_main(argv(globals()))
    else:
        _cpython_main(sys.argv)
