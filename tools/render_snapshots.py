"""Render RenderParams fixtures through the real renderer into PNG snapshots.

One fixture per screen / sub-state of ui-spec §6. Each runs the renderer for
a while at 20 fps (so rings are mid-flight and crossfades settle) and keeps
the last full 240x240 frame.

    python3 tools/render_snapshots.py [name ...]   # writes docs/design/snapshots/*.png
    node tools/mpy/run.mjs tools/render_snapshots.py --emit [name ...]

The renderer needs framebuf, so frames are always rendered by MicroPython
(the WebAssembly port). Its filesystem is in-memory, so under ``--emit`` the
PNGs (encoded in MicroPython by tools/png.py) are printed as base64 between
``@@PNG <name>`` / ``@@END`` markers, after an ``@@CRC <name> <crc32>`` line
for the raw frame; the CPython entry point runs that command, writes the
files and the frame CRCs to tests/snapshot_crc.json (which
tests/test_renderer.py renders the fixtures against), and exits 1 if any
named fixture (or, with no names, every fixture) wrote nothing. ``--bench``
prints per-fixture ms/frame.

``make_params`` here is the fixture constructor tests/test_renderer.py uses too.
"""

import sys

try:
    from finder.render_params import make_params as _make_params, validate
except ImportError:  # CPython entry point: repo root not on sys.path yet
    _make_params = validate = None

OUT_DIR = "docs/design/snapshots"
CRC_FILE = "tests/snapshot_crc.json"
T0 = 100000
FPS_MS = 50

# Fixture base: a FAR hunt frame (finder.render_params.DEFAULTS is SEARCHING).
FIXTURE_BASE = {"screen": "FAR", "zone": 0, "ramp": "green", "intensity": 0.1,
                "speed_px_s": 40.0, "pulse_period_ms": 2400, "glow_r_px": 24.0,
                "ring_live": True, "glyph": "glow", "status": (80, 80, 4, False, False)}


def make_params(**kw):
    """finder.render_params.make_params over FIXTURE_BASE."""
    d = dict(FIXTURE_BASE)
    d.update(kw)
    return _make_params(**d)


# ---- fixtures: name -> (phases [(offset_ms, kw)], run_ms) ---------------------
STATUS_OFF = (82, 76, 4, False, False)
_hunt = {
    0: dict(screen="FAR", zone=0, intensity=0.12, speed_px_s=40, pulse_period_ms=2400,
            wavelength_px=96, glow_r_px=25, heartbeat="TICK", heartbeat_every=2),
    1: dict(screen="NEAR", zone=1, intensity=0.33, speed_px_s=56, pulse_period_ms=1600,
            wavelength_px=90, glow_r_px=33, heartbeat="TICK"),
    2: dict(screen="WARM", zone=2, intensity=0.55, speed_px_s=80, pulse_period_ms=1000,
            wavelength_px=80, glow_r_px=42, heartbeat="DOUBLE"),
    3: dict(screen="HOT", zone=3, intensity=0.85, speed_px_s=120, pulse_period_ms=500,
            wavelength_px=60, glow_r_px=54, heartbeat=None),
}


def hunt(z, **kw):
    d = dict(_hunt[z])
    d["status"] = STATUS_OFF
    d.update(kw)
    return d


_pair = dict(screen="PAIRING", zone=None, ramp="green", intensity=0.1, speed_px_s=-30,
             pulse_period_ms=3000, wavelength_px=90, glow_r_px=30, glyph="runes",
             status=STATUS_OFF)
RUNES = (0, 3, 6)
_menu = dict(screen="MENU", zone=2, intensity=0.55, speed_px_s=80, pulse_period_ms=1000,
             glow_r_px=42, status=STATUS_OFF)
MENU_ROWS = ("RESUME", "SUN: OFF", "BUZZ: FULL", "PLACE: OUT")
_scan = dict(screen="SCANNING", zone=2, intensity=0.5, speed_px_s=80, pulse_period_ms=1000,
             glow_r_px=8, status=STATUS_OFF)
BINS = (0.2, 0.35, 0.6, 0.9, 1.0, 0.75, 0.4, None, 0.15, 0.1, None, 0.05)
_lost = dict(screen="LINK_LOST", zone=1, ramp="grey", intensity=0.33, speed_px_s=-30,
             pulse_period_ms=3000, wavelength_px=90, glow_r_px=16, ring_live=False,
             glyph="seeker", dist_band="~20", dist_stale=True, trend=0, status=STATUS_OFF)
_cal = dict(_pair, sub="calibrate", speed_px_s=0, wavelength_px=0, glyph="countdown",
            top_text="STAND 1 STEP APART", word="HOLD STILL")
_found = dict(screen="FOUND", zone=3, ramp="gold", intensity=1.0, speed_px_s=0,
              pulse_period_ms=1200, glow_r_px=90, glyph="check", top_text="TIME 12:48",
              status=STATUS_OFF)
_result = dict(_scan, sub="result", glyph="turn", intensity=0.6, glow_r_px=12)
# how-to cards keep the looking field: inward listening rings, never live (§6 PAIRING)
_howto = dict(_pair, sub="howto", ring_live=False)
HOWTO = (dict(_howto, glyph="runes", runes=RUNES, top_text="HOW TO PLAY 1/4", word="PAIR UP"),
         dict(_howto, glyph="countdown", countdown=30, top_text="HOW TO PLAY 2/4",
              word="SPLIT UP"),
         dict(_howto, glyph="chevrons", trend=1, top_text="HOW TO PLAY 3/4",
              word="GET CLOSER"),
         dict(_howto, glyph="bump", bump_icons=0, top_text="HOW TO PLAY 4/4", word="BUMP!"))

FIXTURES = [
    # no partner yet, so Game sends ring_live False; inward rings are
    # listening rings all the same (§4 rule 4), never ghosts
    ("pairing_looking", [(0, dict(_pair, sub="looking", top_text="START OTHER WATCH", word="LOOKING",
                                  ring_live=False))], 2000),
    # 5 s into looking: the hint toast, once (§6 PAIRING)
    ("pairing_looking_hint", [(0, dict(_pair, sub="looking", top_text="START OTHER WATCH",
                                       word="LOOKING", ring_live=False,
                                       banner=("SWIPE: HOW TO PLAY", "info", False)))], 2000),
    ("pairing_howto_1", [(0, HOWTO[0])], 2000),
    ("pairing_howto_2", [(0, HOWTO[1])], 2000),
    ("pairing_howto_3", [(0, HOWTO[2])], 2000),
    ("pairing_howto_4", [(0, HOWTO[3])], 2000),
    ("pairing_seen", [(0, dict(_pair, sub="seen", speed_px_s=0, wavelength_px=0, runes=RUNES,
                               top_text="SAME RUNES?", word="BUMP = YES"))], 1200),
    # a bump only this watch felt: the felt-it toast keeps the question visible (§6 PAIRING)
    ("pairing_bump_one", [(0, dict(_pair, sub="seen", speed_px_s=0, wavelength_px=0,
                                   runes=RUNES, top_text="SAME RUNES?", word="BUMP = YES",
                                   banner=("ONLY YOU FELT IT", "info", False)))], 1200),
    ("pairing_confirmed", [(0, dict(_pair, sub="confirmed", speed_px_s=0, wavelength_px=0,
                                    runes=RUNES, top_text="WAITING FOR FRIEND",
                                    word="YOU'RE IN"))], 1200),
    ("pairing_calibrate", [(0, dict(_cal, countdown=3)), (1000, dict(_cal, countdown=2))], 1500),
    ("pairing_split", [(0, hunt(1, screen="PAIRING", sub="split", glyph="countdown",
                                countdown=24, top_text="NO PEEKING", word="SPLIT UP",
                                heartbeat=None))], 2000),
    # every split start raises the NEW ROUND toast over SPLIT UP (§6 PAIRING)
    ("pairing_split_new_round", [(0, hunt(1, screen="PAIRING", sub="split", glyph="countdown",
                                          countdown=30, top_text="NO PEEKING", word="SPLIT UP",
                                          heartbeat=None,
                                          banner=("NEW ROUND", "info", False)))], 1000),
    ("pairing_split_tap", [(0, hunt(1, screen="PAIRING", sub="split", glyph="countdown",
                                    countdown=21, top_text="TAP WHEN READY", word="SPLIT UP",
                                    heartbeat=None))], 2000),
    ("pairing_split_friend_ready", [(0, hunt(1, screen="PAIRING", sub="split", glyph="countdown",
                                             countdown=14, top_text="FRIEND READY",
                                             word="SPLIT UP", heartbeat=None))], 2000),
    ("pairing_split_ready", [(0, hunt(1, screen="PAIRING", sub="split", glyph="countdown",
                                      countdown=12, top_text="WAITING FOR FRIEND",
                                      word="READY", heartbeat=None))], 2000),
    ("pairing_split_both_ready", [(0, hunt(1, screen="PAIRING", sub="split", glyph="countdown",
                                           countdown=3, top_text="BOTH READY",
                                           word="READY", heartbeat=None))], 2000),
    ("searching", [(0, dict(screen="SEARCHING", zone=None, ramp="grey", intensity=0.15,
                            speed_px_s=-36, pulse_period_ms=3200, wavelength_px=115,
                            glow_r_px=18, ring_live=False, glyph="seeker", word="SEARCHING",
                            status=STATUS_OFF))], 2500),
    # the round's hunt begins with no partner heard yet: the goal, for 4 s
    ("searching_round_start", [(0, dict(screen="SEARCHING", zone=None, ramp="grey",
                                        intensity=0.15, speed_px_s=-36, pulse_period_ms=3200,
                                        wavelength_px=115, glow_r_px=18, ring_live=False,
                                        glyph="seeker", word="SEARCHING",
                                        top_text="FIND YOUR FRIEND", status=STATUS_OFF))], 2500),
    ("far_glow_status", [(0, hunt(0, dist_band="~40", status=(64, 71, 4, True, False)))], 1500),
    # sun mode (§8): floor >= 1.0 and the ramp LUT lifted one stop
    ("far_glow_sun", [(0, hunt(0, dist_band="~40", sun=True))], 1500),
    ("far_hint", [(0, hunt(0, dist_band="60+", intensity=0.03, top_text="TAP TO SCAN"))], 1500),
    # the round's first FAR teaches the ripple tempo instead of TAP TO SCAN
    ("far_teach", [(0, hunt(0, dist_band="60+", intensity=0.05,
                            top_text="FASTER IS CLOSER"))], 1500),
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
    ("hot_glow", [(0, hunt(3, dist_band="~5", top_text="LOOK UP"))], 1500),
    # a tap in HOT does nothing: the toast says what does (§8 Ignored taps)
    ("hot_ignored_tap", [(0, hunt(3, dist_band="~5",
                                  banner=("PRESS 2X TO SCAN", "info", False)))], 1500),
    # the bump view (§6 HOT): bump_icons bit 0 you lit, bit 1 the friend lit, bit 2 friend grey
    ("hot_bump_ready", [(0, hunt(3, dist_band="<3", glyph="bump", bump_icons=0, word="BUMP!",
                                 top_text="BUMP WRISTS"))], 1500),
    ("hot_bump_only_you", [(0, hunt(3, dist_band="<3", glyph="bump", bump_icons=1,
                                    word="BUMP!", top_text="ONLY YOU FELT IT"))], 1500),
    ("hot_bump_friend_felt", [(0, hunt(3, dist_band="<3", glyph="bump", bump_icons=2,
                                       word="BUMP!", top_text="FRIEND FELT IT"))], 1500),
    ("hot_bump_wait_friend", [(0, hunt(3, dist_band="<3", glyph="bump", bump_icons=4,
                                       top_text="FRIEND NOT READY"))], 1500),
    ("hot_arrow_solid_a", [(0, hunt(3, sub="walk", glyph="arrow", arrow_deg=20, cone_deg=18,
                                    arrow_style="solid_a", dist_band="~5"))], 1500),
    ("direction_reveal", [(0, hunt(2, sub="reveal", glyph="arrow", arrow_deg=120, cone_deg=22,
                                   arrow_style="solid_a", word="4 O'CLOCK"))], 1500),
    # turn: the halo is the live mirror, glow_r 12 as in the sweep (§5.7)
    ("direction_turn", [(0, hunt(2, sub="turn", glyph="arrow", arrow_deg=60, cone_deg=33,
                                 arrow_style="solid_b", intensity=0.7, glow_r_px=12,
                                 word="TURN RIGHT",
                                 sweep=(60, (None,) * 12, None, False),
                                 heartbeat=None))], 1500),
    # pacer near 6 o'clock (turning toward something behind): the wedge
    # would sit under the TURN word, so the bottom slot yields; I_mirror = 1
    ("direction_turn_behind", [(0, hunt(2, sub="turn", glyph="arrow", arrow_deg=10,
                                        cone_deg=33, arrow_style="solid_b", intensity=1.0,
                                        glow_r_px=12, word="TURN RIGHT",
                                        sweep=(170, (None,) * 12, None, False),
                                        heartbeat=None))], 1500),
    ("direction_walk_outline", [(0, hunt(0, sub="walk", glyph="arrow", arrow_deg=0,
                                         cone_deg=52, arrow_style="outline", trend=-1,
                                         dist_band="~40", top_text="TAP TO RESCAN"))], 1500),
    ("found_celebrate", [(0, dict(_found, sub="celebrate", word="FOUND", burst=True,
                                  haptic="FOUND")),
                         (50, dict(_found, sub="celebrate", word="FOUND"))], 450),
    # result: FOUND and the round time in gold, until a button press (§6 FOUND)
    ("found_result", [(0, dict(_found, sub="result", word="FOUND 1:48",
                               top_text="BUTTON: PLAY AGAIN"))], 2500),
    ("found_ignored_tap", [(0, dict(_found, sub="result", word="FOUND 1:48",
                                    top_text="BUTTON: PLAY AGAIN",
                                    banner=("PRESS THE BUTTON", "info", False)))], 1500),
    ("found_result_long", [(0, dict(_found, sub="result", word="FOUND12:48",
                                    top_text="BUTTON: PLAY AGAIN"))], 2500),
    ("scan_ready_flat", [(0, dict(_scan, sub="ready", glyph="countdown", countdown=3,
                                  top_text="HOLD AT CHEST", word="TURN RIGHT"))], 1500),
    ("scan_ready_tilted", [(0, dict(_scan, sub="ready", glyph="countdown", countdown=2,
                                    top_text="HOLD FLAT", word="TURN RIGHT"))], 1500),
    ("scan_sweep", [(0, dict(_scan, sub="sweep", glyph="turn", intensity=0.6, glow_r_px=12,
                             sweep=(135, BINS, 4, False)))], 1500),
    # sweep start: every bin still under 4 packets (hollow), the first active
    ("scan_sweep_start", [(0, dict(_scan, sub="sweep", glyph="turn", intensity=0.4,
                                   glow_r_px=12, sweep=(15, (None,) * 12, 0, False)))], 600),
    ("scan_sweep_paused", [(0, dict(_scan, sub="sweep", glyph="turn", intensity=0.3,
                                    glow_r_px=12, sweep=(200, BINS, 6, True)))], 1500),
    # result: slot 0 is theta (131 deg, in best bin 4), the angle the bin morphs into
    ("scan_result_ok", [(0, dict(_result, sweep=(131, BINS, 4, False)))], 450),
    ("scan_result_morph", [(0, dict(_result, sweep=(131, BINS, 4, False))),
                           (200, dict(_result, sweep=(131, BINS, None, False))),
                           (400, dict(_result, sweep=(131, BINS, 4, False))),
                           (600, dict(_result, sweep=(131, BINS, None, False)))], 1000),
    # no fix: the Game returns to the zone screen at once and raises the toast
    ("scan_result_no_fix", [(0, hunt(2, dist_band="~10",
                                     banner=("NO FIX, TRY AGAIN", "info", False)))], 1500),
    ("link_lost", [(0, dict(_lost, banner=("SIGNAL LOST", "warn", True)))], 2500),
    # The LAST chip's last-trend mark (§6 LINK-LOST): drawn from ``trend``,
    # the last trend before the loss (§3).
    ("link_lost_last_trend", [(0, dict(_lost, trend=1,
                                       banner=("LOST: KEEP ON", "warn", True)))], 2500),
    ("link_lost_friend_off", [(0, dict(_lost,
                                       banner=("FRIEND IS OFF", "critical", True)))], 2500),
    ("low_battery_saver", [(0, hunt(1, glyph="battery", word="SAVER ON",
                                    status=(10, 64, 3, True, False)))], 1500),
    ("low_battery_toast", [(0, hunt(0, dist_band="~40", status=(20, 70, 1, True, False),
                                    banner=("BATTERY 20%", "warn", False)))], 1500),
    ("relink_burst", [(0, hunt(1, dist_band="~20", burst=True, haptic="CLOSER",
                               banner=("BACK IN RANGE", "info", False))),
                      (50, hunt(1, dist_band="~20",
                                banner=("BACK IN RANGE", "info", False)))], 700),
    # menu_rows: Menu.rows (finder/menu.py), a 4-row window into the 6-row list
    ("menu", [(0, hunt(2, dist_band="~10")),
              (1000, dict(_menu, sub="1v", menu_rows=MENU_ROWS))], 1800),
    ("menu_scrolled", [(0, hunt(2, dist_band="~10")),
                       (1000, dict(_menu, sub="1^",
                                   menu_rows=("BUZZ: FULL", "PLACE: IN", "THEME: RIPPLE",
                                              "END ROUND")))], 1800),
    # the THEME row (ui-spec §4A Choosing) on its longest label; this renderer
    # draws Ripple whatever ``theme`` says (docs/design/themes/ for the others)
    ("menu_theme", [(0, hunt(2, dist_band="~10")),
                    (1000, dict(_menu, sub="3^v", theme="fireflies",
                                menu_rows=("SUN: OFF", "BUZZ: FULL", "PLACE: OUT",
                                           "THEME: FIREFLIES")))], 1800),
]


def render_fixture(r, cap, name, phases, run_ms, bench=None):
    """Run a fixture at 20 fps; ``cap`` holds its last frame."""
    import time
    r.reset()
    k = 0
    us = 0
    n = 0
    t = 0
    while t <= run_ms:
        while k + 1 < len(phases) and phases[k + 1][0] <= t:
            k += 1
        p = make_params(t_ms=T0 + t, **phases[k][1])
        t_us = time.ticks_us()
        r.frame(p, cap, T0 + t)
        us += time.ticks_diff(time.ticks_us(), t_us)
        n += 1
        t += FPS_MS
    if bench is not None:
        bench.append((name, us / n / 1000.0))


def _mpy_main(args):
    import binascii
    import gc
    from tools import png
    from ui.renderer import FrameCapture, Renderer
    emit = "--emit" in args
    bench = [] if "--bench" in args else None
    sel = [a for a in args[1:] if not a.startswith("-")]
    r = Renderer()
    cap = FrameCapture()
    for name, phases, run_ms in FIXTURES:
        if sel and name not in sel:
            continue
        for _, kw in phases:
            for err in validate(make_params(t_ms=T0, **kw)):
                print("WARN %s: %s" % (name, err))
        render_fixture(r, cap, name, phases, run_ms, bench)
        if not emit:
            continue
        print("@@CRC %s %08x" % (name, binascii.crc32(cap.buf)))
        data = png.encode(240, 240, png.rgb565sw_to_rgb(cap.buf, 240, 240))
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
    import json
    import os
    import subprocess
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    cmd = ["node", os.path.join(root, "tools", "mpy", "run.mjs"),
           "tools/render_snapshots.py", "--emit"] + args[1:]
    proc = subprocess.run(cmd, cwd=root, capture_output=True, text=True)
    if proc.stderr:
        sys.stderr.write(proc.stderr)          # the MicroPython traceback, if any
    if proc.returncode != 0:
        sys.exit(proc.returncode)
    want = [a for a in args[1:] if not a.startswith("-")]
    seen = []
    crc_path = os.path.join(root, CRC_FILE)
    crc = {}
    if want and os.path.exists(crc_path):      # a partial run updates its fixtures only
        with open(crc_path) as f:
            crc = json.load(f)
    os.makedirs(os.path.join(root, OUT_DIR), exist_ok=True)
    name = None
    chunks = []
    for line in proc.stdout.splitlines():
        if line.startswith("@@CRC "):
            k, v = line[6:].split()
            crc[k] = int(v, 16)
        elif line.startswith("@@PNG "):
            name = line[6:].strip()
            chunks = []
        elif line == "@@END" and name:
            with open(os.path.join(root, OUT_DIR, name + ".png"), "wb") as f:
                f.write(base64.b64decode("".join(chunks)))
            seen.append(name)
            name = None
        elif name:
            chunks.append(line.strip())
        elif line.strip():
            print(line)
    if seen:
        with open(crc_path, "w") as f:
            json.dump(crc, f, indent=1, sort_keys=True)
            f.write("\n")
    print("wrote %d snapshots to %s" % (len(seen), OUT_DIR))
    missing = [w for w in want if w not in seen]
    if missing or not seen:
        sys.stderr.write("no snapshot written for: %s (unknown fixture?)\n"
                         % (", ".join(missing) or "any fixture"))
        sys.exit(1)


if __name__ == "__main__":
    if sys.implementation.name == "micropython":
        _mpy_main(sys.argv)
    else:
        _cpython_main(sys.argv)
