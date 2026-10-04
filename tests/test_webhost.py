"""sim/webhost.py: the browser simulator's Python host (two watches, one world).

The episode test drags A towards B, walks it the rest of the way with
``walk_to`` and bumps until both watches show FOUND, all within 120 s of sim
time. Frames are only drawn under MicroPython (framebuf); on CPython the
logic, telemetry and controls are checked. The real-mode tests feed
``show_params`` the JSON a real watch's ``rp`` record carries.
"""

import json
import math
import os
import sys

from sim.webhost import TwoWatchSim, heading_to_world, heading_to_page, AUTO_SPLIT_S, TILT_TILTED
from sim.world import PI
from finder import arrow as A
from finder import scan as S
from finder import tuning as T
from finder.haptic_patterns import TOTAL_MS, HB_RESUME_MS
from finder.render_params import from_dict, to_dict
from tests import Skip

MPY = sys.implementation.name == "micropython"
KEYS = ("screen", "sub", "zone", "band", "true_dist_m", "est_dist_m", "rssi_last", "rssi_f",
        "trend", "arrow_deg", "cone_deg", "true_bearing_rel_deg", "in_cone", "cone_ms",
        "cone_hit_pct", "pkts_per_s", "haptic", "steps", "backlight", "place")


def _sample(buf):
    """Cheap fingerprint of a 240x240 frame (every 997th byte)."""
    return bytes(buf[k] for k in range(0, len(buf), 997))


def _run_until(s, cond, max_ms, dt=50):
    end = s.t_ms + max_ms
    while s.t_ms < end:
        s.step(dt)
        if cond(s):
            return True
    return False


def _hunting(s):
    return s.games[0].mode == "HUNT" and s.games[1].mode == "HUNT"


class _Episode:
    def __init__(self, seed=1):
        s = TwoWatchSim(seed=seed)
        self.s = s
        self.prints = []
        self.haptic = ([], [])
        self.screens = ([], [])
        self.left_pairing = None
        self.found = [None, None]
        self.bumps = 0
        self.phase = "wait"
        self._bump_t = None
        self._x = 0.0
        while s.t_ms < 120000:
            self._direct()
            s.step(50)
            self._observe()
            if self.found[0] is not None and self.found[1] is not None:
                break

    def _direct(self):
        s = self.s
        ga, gb = s.games
        ph = self.phase
        if ph == "wait":
            if ga.mode not in ("PAIRING", "SEARCHING") and gb.mode not in ("PAIRING", "SEARCHING"):
                self.phase = "drag"
        elif ph == "drag":                  # a mouse drag: 0.5 m per frame towards (15, 4)
            self._x += 0.5
            s.set_pose(0, self._x, self._x * 4.0 / 15.0, 70.0)
            if self._x >= 15.0:
                self.phase = "walk"
                b = s.world.b
                s.walk_to(0, b.x - 0.8, b.y - 0.3)
        elif ph == "walk":
            if s.world.distance() < 1.5:
                self.phase = "bump"
        elif ph == "bump":
            both_hot = ga.px.zone == 3 and gb.px.zone == 3 and ga.mode == "HUNT" \
                and gb.mode == "HUNT"
            if both_hot and (self._bump_t is None or s.t_ms - self._bump_t >= 2000):
                s.bump()
                self.bumps += 1
                self._bump_t = s.t_ms

    def _observe(self):
        s = self.s
        if s.t_ms % 500 == 0:
            self.prints.append((s.t_ms, _sample(s.frame_bytes(0)), _sample(s.frame_bytes(1))))
        for i in (0, 1):
            t = s.telemetry(i)
            for k in KEYS:
                assert k in t, (i, k)
            for h in t["haptic"]:
                self.haptic[i].append(h)
            sc = self.screens[i]
            if not sc or sc[-1] != t["screen"]:
                sc.append(t["screen"])
            if t["screen"] == "FOUND" and self.found[i] is None:
                self.found[i] = s.t_ms
        if self.left_pairing is None and s.games[0].mode != "PAIRING" \
                and s.games[1].mode != "PAIRING":
            self.left_pairing = s.t_ms


_cache = {}


def _episode():
    if "e" not in _cache:
        _cache["e"] = _Episode(1)
    return _cache["e"]


def test_heading_conversion_round_trips():
    assert abs(heading_to_world(90.0)) < 1e-9                  # east = +x
    assert abs(heading_to_world(0.0) - PI / 2) < 1e-9          # north = +y
    assert abs(abs(heading_to_world(270.0 - 360.0)) - PI) < 1e-9
    for d in (0.0, 10.0, 45.0, 90.0, 179.5, 180.0, 181.0, 270.0, 359.0):
        back = heading_to_page(heading_to_world(d))
        assert abs(((back - d) + 180.0) % 360.0 - 180.0) < 1e-6, (d, back)
    for r in (-3.0, -1.0, 0.0, 0.5, 3.1):
        w = heading_to_world(heading_to_page(r))
        assert abs(math.atan2(math.sin(w - r), math.cos(w - r))) < 1e-9, (r, w)
    s = TwoWatchSim()
    s.set_pose(1, 3.0, -4.0, 135.0)
    w = s.world_state()["walkers"][1]
    assert (w["x"], w["y"]) == (3.0, -4.0) and abs(w["heading_deg"] - 135.0) < 0.01, w
    # heading 135 = south-east: B faces the point one metre SE of it
    b = s.world.b
    assert abs(math.cos(b.heading) - math.sqrt(0.5)) < 1e-9
    assert abs(math.sin(b.heading) + math.sqrt(0.5)) < 1e-9


def test_start_poses_and_bearing():
    s = TwoWatchSim()
    ws = s.world_state()
    a, b = ws["walkers"]
    assert (a["x"], a["y"], a["heading_deg"]) == (0.0, 0.0, 90.0), a
    assert (b["x"], b["y"]) == (35.0, 10.0), b
    # B faces A, so A is dead ahead of B; A (facing east) sees B slightly to the left
    assert abs(s.true_bearing_rel_deg(1)) < 1e-6
    rel = s.true_bearing_rel_deg(0)
    assert abs(rel + math.atan2(10.0, 35.0) * 180.0 / PI) < 1e-6, rel
    s.set_pose(0, 0.0, 0.0, 0.0)             # face north: B is to the right
    assert abs(s.true_bearing_rel_deg(0) - math.atan2(35.0, 10.0) * 180.0 / PI) < 1e-6


def test_telemetry_keys_and_json():
    s = TwoWatchSim()
    for _ in range(40):
        s.step(50)
    d = json.loads(s.telemetry_json())
    assert d["t_ms"] == 2000
    assert len(d["watches"]) == 2
    for t in d["watches"]:
        for k in KEYS:
            assert k in t, k
        assert isinstance(t["haptic"], list)
        assert t["screen"] == "PAIRING"
    assert len(d["world"]["walkers"]) == 2 and d["world"]["walls"] == []
    assert d["world"]["profile"] == "typical"
    ws = s.world_state()
    assert ws["walkers"][0]["x"] == 0.0 and ws["walls"] == []
    assert s.telemetry(0)["pkts_per_s"] >= 5
    assert s.telemetry(0)["cone_ms"] == 0 and s.telemetry(0)["cone_hit_pct"] is None


def test_constants_json_follows_tuning():
    k = json.loads(TwoWatchSim().constants_json())
    assert k["enter_m"] == list(T.ZONE_ENTER_M) and k["exit_m"] == list(T.ZONE_EXIT_M), k
    assert k["dwell_ms"] == list(T.ZONE_DWELL_MS) and k["lost_ms"] == T.LINK_LOST_AFTER_MS
    assert k["menu_close_ms"] == T.MENU_AUTOCLOSE_MS and k["band_edges_m"] == list(T.BAND_EDGES_M)
    assert k["split_s"] == T.PAIR_SPLIT_S and k["haptic_ms"] == TOTAL_MS, k


def test_swipe_scrolls_the_menu_and_place_shows_in_telemetry():
    s = TwoWatchSim()
    for _ in range(20):
        s.step(50)
    g = s.games[0]
    assert s.telemetry(0)["place"] == "OUT" and not g.indoor      # typical: outdoors
    s.long_press(0)
    s.step(100)
    assert g.menu_open and g.menu.top == 0
    s.swipe(0, True)                         # up: rows below come into view
    s.step(100)
    assert g.menu.top == 1 and s.telemetry(0)["sub"] == "0^"
    for _ in range(20):
        s.step(50)                           # (touch burst filter: 3 touches in 1 s)
    s.swipe(0, False)
    s.step(100)
    assert g.menu.top == 0
    # the radio profile sets PLACE on both watches; a menu choice holds until the next change
    s.set_profile("indoor")
    assert s.telemetry(0)["place"] == "IN" and s.telemetry(1)["place"] == "IN"
    g.set_place(False)                       # as the menu row would
    assert s.telemetry(0)["place"] == "OUT" and s.telemetry(1)["place"] == "IN"
    s.set_profile("clean")
    assert s.telemetry(0)["place"] == "OUT" and s.telemetry(1)["place"] == "OUT"


def test_page_touches_feed_the_burst_filter():
    # each page gesture lands a finger first (Game.on_touch_down), as on the watch
    s = TwoWatchSim()
    for _ in range(20):
        s.step(50)
    g = s.games[0]
    assert g.screen_on and g._touch_block is None
    for _ in range(3):
        s.swipe(0, True)
        s.step(100)
    assert g._touch_block is not None              # 3 touch-downs in 1 s: rain/sleeve


def test_auto_confirm_waits_for_an_open_menu():
    s = TwoWatchSim()
    g = s.games[0]
    assert _run_until(s, lambda s: g.pair.sub == "seen", 3000)
    s.long_press(0)                          # menu opens while A is in SEEN
    for _ in range(12):
        s.step(100)
        # auto-confirm does not press into the menu: the cursor stays on RESUME, A waits in SEEN
        assert g.menu_open and g.menu.sel == 0 and g.pair.sub == "seen", (g.menu.sel, g.pair.sub)
    assert _run_until(s, lambda s: not g.menu_open, 10000)        # the menu closes on its own
    assert g.mode == "PAIRING" and g.pair.sub == "seen"
    assert _run_until(s, lambda s: s.games[0].mode != "PAIRING" and s.games[1].mode != "PAIRING",
                      20000)


def test_manual_pairing_without_auto_pair():
    s = TwoWatchSim()
    s.auto_pair = False
    s.set_pose(1, 1.0, 0.0, 270.0)            # B 1 m east of A, face to face

    def both(sub):
        return lambda s: s.games[0].pair.sub == sub and s.games[1].pair.sub == sub
    assert _run_until(s, both("seen"), 3000)
    s.button(0)                               # both players confirm the runes
    s.button(1)
    assert _run_until(s, both("split"), 15000)
    assert max(p.countdown for p in s._params) > AUTO_SPLIT_S     # the full split, not the demo cut


def test_auto_pair_starts_quickly():
    s = TwoWatchSim()
    split = [None, None]
    peer = []                             # peer RSSI fed to the estimators off the proxy
    for g in s.games:
        def upd(t, rssi, p=None, me=None, pm=None, _up=g.est.update):
            if p is not None and not s._was_proxy:
                peer.append(p)
            return _up(t, rssi, p, me, pm)
        g.est.update = upd
    while s.t_ms < 20000 and not (s.games[0].mode != "PAIRING" and s.games[1].mode != "PAIRING"):
        s.step(50)
        for i in (0, 1):
            pr = s.games[i].pair
            if pr.sub == "split" and split[i] is None:
                split[i] = s.t_ms
                assert s.telemetry(i)["screen"] == "PAIRING"
                assert s._params[i].countdown <= AUTO_SPLIT_S, s._params[i].countdown
    assert split[0] is not None and split[1] is not None, split
    assert s.t_ms < 15000, s.t_ms                 # 30 s split cut to 5 s
    assert s.t_ms - max(split) <= AUTO_SPLIT_S * 1000 + 1500
    # the proxy pairing never leaks 1 m packets: nobody opens HOT 36 m away
    for g in s.games:
        assert g.px.zone != 3, g.px.zone
    assert peer and max(peer) < -60, max(peer)    # ... nor a stale 1 m rssi_last in a beacon
    p0 = s.sim.radio.p0_link
    for k in (0, 1):                      # both 1 m calibrations finish on the proxy (held split/calibrate)
        assert abs(s.games[k].pair.p1m - p0[k]) <= 1.0, (k, s.games[k].pair.p1m, p0[k])


def test_auto_turn_rotates_during_scan():
    s = TwoWatchSim()
    assert _run_until(s, _hunting, 30000)
    s.tap(0)
    assert s.games[0].mode == "SCANNING"
    assert _run_until(s, lambda s: s.games[0].scan.sub == "sweep", 10000)
    h0 = s.world.a.heading
    for _ in range(40):                                   # 2 s of sweep
        s.step(50)
    turned = (h0 - s.world.a.heading) % (2 * PI) * 180.0 / PI
    assert 45.0 <= turned <= 75.0, turned                  # ~60 deg clockwise
    assert s.world.b.heading == math.atan2(-10.0, -35.0)   # B is not scanning
    assert TILT_TILTED > S.TILT_FAULT_DEG
    s.set_posture(0, "tilted")                             # the page's 'tilted 50°' pauses the sweep
    assert _run_until(s, lambda s: s.games[0].scan.paused, 2000)
    h1 = s.world.a.heading
    for _ in range(10):
        s.step(50)
    assert s.games[0].scan.sub == "sweep" and s.games[0].scan.paused
    assert s.world.a.heading == h1                         # auto-turn holds while paused
    s.set_posture(0, "flat")
    assert _run_until(s, lambda s: not s.games[0].scan.paused, 2000)
    s.auto_turn = False
    h2 = s.world.a.heading
    for _ in range(20):
        s.step(50)
    assert s.games[0].scan.sub == "sweep" and s.world.a.heading == h2


def test_auto_turn_follows_the_pacer():
    s = TwoWatchSim()
    assert _run_until(s, _hunting, 30000)
    g = s.games[0]
    g.arrow = A.make(90.0, 15.0, s.t_ms)                 # 3 o'clock: turn right 90
    h0 = s.world.a.heading
    assert _run_until(s, lambda s: g.arrow is None or g.arrow.phase in ("lock", "walk"), 10000)
    turned = (h0 - s.world.a.heading) % (2 * PI) * 180.0 / PI
    assert 75.0 <= turned <= 100.0, turned
    t = s.telemetry(0)                     # truth overlay: B is ~16 deg left, the arrow 90 right
    assert t["cone_ms"] > 0 and t["cone_hit_pct"] == 0 and t["in_cone"] is False, t
    g.arrow = A.make(170.0, 15.0, s.t_ms)                # a long turn, cut by a link loss
    h0 = s.world.a.heading
    b = (s.world.b.x, s.world.b.y)
    assert _run_until(s, lambda s: g.arrow.phase == A.PH_TURN, 3000)
    s.set_pose(1, 600.0, 0.0)
    assert _run_until(s, lambda s: g.mode == "LINK_LOST", 10000) and g.arrow.phase == A.PH_TURN
    s.set_pose(1, b[0], b[1])
    assert _run_until(s, lambda s: g.arrow is None or g.arrow.phase in ("lock", "walk"), 20000)
    turned = (h0 - s.world.a.heading) % (2 * PI) * 180.0 / PI
    assert 155.0 <= turned <= 185.0, turned               # the relinked pacer is not counted twice


def test_cone_score_follows_the_true_bearing():
    # truth overlay: an arrow at the true bearing scores inside, its mirror image outside
    for mirror, ok in ((False, True), (True, False)):
        s = TwoWatchSim()
        assert _run_until(s, _hunting, 30000)
        g = s.games[0]
        s.set_pose(0, s.world.a.x, s.world.a.y, 200.0)     # B ~126 deg to the left
        tb = s.true_bearing_rel_deg(0)
        g.arrow = A.make(-tb if mirror else tb, 15.0, s.t_ms)
        assert _run_until(s, lambda s: g.arrow is None or g.arrow.phase in ("lock", "walk"), 10000)
        t = s.telemetry(0)
        assert t["cone_ms"] > 0 and t["in_cone"] is ok, (mirror, t["in_cone"])
        assert (t["cone_hit_pct"] >= 90) if ok else (t["cone_hit_pct"] <= 10), (mirror, t["cone_hit_pct"])


def _end_round(s, i):
    s.button(i, True); s.step(200)              # MENU
    for _ in range(4):                          # down to END ROUND
        s.button(i); s.step(200)
    s.button(i, True); s.step(200)              # SURE? PRESS
    s.button(i); s.step(100)                    # confirm


def test_end_round_during_the_split_keeps_the_real_channel():
    s = TwoWatchSim(seed=3)
    g = s.games
    assert _run_until(s, lambda s: g[0].pair.sub == "split" and g[1].pair.sub == "split"
                      and not s._pair_proxy(), 20000)
    s.step(500)
    _end_round(s, 1)
    assert g[1].pair.sub == "looking"
    while g[0].mode == "PAIRING":               # A's split hears no 1 m packet
        assert not s._pair_proxy(), s.t_ms
        s.step(50)
    assert _run_until(s, _hunting, 40000)       # FRIEND LEFT on A, then both re-pair
    p0 = s.sim.radio.p0_link
    assert max(abs(g[k].pair.p1m - p0[k]) for k in (0, 1)) <= 2.0


def test_set_profile_and_reset():
    s = TwoWatchSim()
    for _ in range(60):
        s.step(50)
    addr = (s.frame_addr(0), s.frame_addr(1))
    bufs = (s.frame_bytes(0), s.frame_bytes(1))
    p0 = list(s.sim.radio.p0_link)
    s.set_profile("indoor")
    assert len(s.world_state()["walls"]) > 10
    assert s.sim.radio.p0_link == p0                    # same devices, new environment
    assert min(s.sim.radio.next_tx) > s.t_ms            # the schedule goes on from now
    for _ in range(20):
        s.step(50)
    assert s.telemetry(0)["pkts_per_s"] <= 25           # the second right after the switch: no burst
    s.set_profile("clean")
    assert s.world_state()["walls"] == [] and s.sim.radio.p0_link == p0
    s.reset(2)
    assert s.t_ms == 0 and s.seed == 2
    assert s.telemetry(0)["screen"] == "PAIRING"
    assert (s.frame_addr(0), s.frame_addr(1)) == addr
    assert s.frame_bytes(0) is bufs[0] and s.frame_bytes(1) is bufs[1]
    if MPY:
        assert addr[0] is not None and addr[0] != addr[1]
    else:
        assert addr == (None, None) and s.renderers is None
    try:
        s.set_profile("space")
        assert False, "bad profile accepted"
    except ValueError:
        pass


def test_episode_drag_walk_bump_found():
    e = _episode()
    assert e.found[0] is not None and e.found[1] is not None, (e.phase, e.screens)
    assert e.found[0] <= 120000 and abs(e.found[0] - e.found[1]) <= 1000, e.found
    assert e.left_pairing is not None and e.left_pairing < 15000, e.left_pairing
    assert e.bumps >= 1
    for i in (0, 1):
        assert e.screens[i][0] == "PAIRING" and e.screens[i][-1] == "FOUND", e.screens[i]
        assert "HOT" in e.screens[i], e.screens[i]
        assert "FOUND" in e.haptic[i], e.haptic[i][-10:]


def test_episode_frames_change():
    if not MPY:
        raise Skip("framebuf: frames only under MicroPython")
    e = _episode()
    s = e.s
    assert s.frames[0] > 100 and s.frames[1] > 100
    fa = set(p[1] for p in e.prints)
    fb = set(p[2] for p in e.prints)
    assert len(fa) > len(e.prints) // 2 and len(fb) > len(e.prints) // 2, (len(fa), len(fb))
    last = e.prints[-1]
    assert last[1] != bytes(len(last[1])), "frame A is blank"
    # renderer heartbeats reach the haptic read-out too, not only the events
    assert len(e.haptic[0]) > 10


def test_haptic_readout_follows_the_player():
    if not MPY:
        raise Skip("framebuf: heartbeats come from the renderer")
    s = TwoWatchSim()
    assert _run_until(s, _hunting, 30000)
    seen = []
    for _ in range(80):                           # 4 s: the rings beat
        s.step(50)
        seen += s.telemetry(0)["haptic"]
    assert seen
    s.players[0].play_named("FOUND", s.t_ms)      # as the game's event would
    end = s.t_ms + TOTAL_MS["FOUND"] + HB_RESUME_MS
    while s.t_ms < end:                           # §7: no heartbeat during the event and 1 s after
        s.step(50)
        assert s.telemetry(0)["haptic"] == [], s.t_ms
    assert _run_until(s, lambda s: s.telemetry(0)["haptic"], 3000)    # then they resume


def test_dark_screen_draws_nothing_and_a_tap_wakes_it():
    s = TwoWatchSim()
    assert _run_until(s, _hunting, 30000)
    s.set_posture(0, "down")
    assert _run_until(s, lambda s: not s.games[0].screen_on, 12000)  # wrist down -> screen off
    if MPY:
        s.step(100)
        n = list(s.frames)
        shot = _sample(s.frame_bytes(0))
        for _ in range(20):
            s.step(50)
        assert s.frames[0] == n[0] and s.frames[1] > n[1], (n, s.frames)     # §8: rendering stops
        assert s.renderers[0]._dark and _sample(s.frame_bytes(0)) == shot
    s.tap(0)                                      # a tap on the dark screen wakes it (wrist still down)
    assert s.games[0].screen_on
    s.set_posture(0, "flat")
    if MPY:
        n0 = s.frames[0]
        assert _run_until(s, lambda s: s.frames[0] > n0, 500)          # the next frame snaps, no intro
        assert not s.renderers[0]._dark
    s.set_posture(0, "tilted")                    # 50 deg: not face-up, not lowered (§8): stays on
    assert not _run_until(s, lambda s: not s.games[0].screen_on, 12000)
    s.set_posture(0, "down")
    assert _run_until(s, lambda s: not s.games[0].screen_on, 12000)
    try:
        s.set_posture(0, "sideways")
        assert False
    except ValueError:
        pass


# ---- real watches (debug mode) ------------------------------------------------------

def _rp_json(s, i):
    """Watch i's current params as an ``rp`` record's ``p`` (JSON text)."""
    return json.dumps(to_dict(s._params[i], True))


def _blank(buf):
    for k in range(0, len(buf), 4800):
        if buf[k:k + 4800] != bytes(len(buf[k:k + 4800])):
            return False
    return True


def test_real_mode_stops_the_world_and_resumes_it():
    s = TwoWatchSim()
    assert _run_until(s, _hunting, 30000)
    s.walk_to(0, 30.0, 8.0)
    s.step(500)
    t0 = s.t_ms
    a = (s.world.a.x, s.world.a.y)
    params = list(s._params)
    s.real_mode(True)
    for _ in range(40):
        s.step(50)
    assert s.t_ms == t0 and s.real_ms == 2000, (s.t_ms, s.real_ms)
    assert (s.world.a.x, s.world.a.y) == a                 # no physics
    assert s._params[0] is params[0] and s._params[1] is params[1]     # no logic ticks
    s.real_mode(True)                                      # again: no change
    assert s.real_ms == 2000
    s.real_mode(False)
    s.step(500)
    assert s.t_ms == t0 + 500 and (s.world.a.x, s.world.a.y) != a     # resumes where it stopped
    assert s._params[0] is not params[0]
    assert s.games[0].mode == "HUNT"                       # no catch-up gap: the link held


def test_show_params_takes_valid_params_only():
    s = TwoWatchSim()
    assert _run_until(s, _hunting, 30000)
    js = _rp_json(s, 0)
    try:
        s.show_params(0, js)
        assert False, "show_params outside real mode"
    except ValueError:
        pass
    s.real_mode(True)
    assert s._shown == [None, None]
    s.show_params(1, js)
    assert s._shown[1] == from_dict(json.loads(js)) and s._shown[0] is None
    d = json.loads(js)
    d["added_later"] = 1                   # a field from a newer watch build is ignored
    s.show_params(0, json.dumps(d))
    assert s._shown[0] == s._shown[1]
    kept = s._shown[0]
    for bad in ("{", "[1, 2]", '{"screen": "NOPE"}', '{"screen": ["WARM"]}',
                '{"intensity": "high"}', '{"screen": "WARM"}'):
        try:
            s.show_params(0, bad)
            assert False, bad
        except ValueError as e:
            assert str(e).startswith("params: "), (bad, e)
        assert s._shown[0] is kept, bad    # a bad record keeps the last good screen
    s.real_mode(False)
    assert s._shown == [None, None]        # back in the sim: its own screens again


def test_show_params_draws_what_the_renderer_draws():
    if not MPY:
        raise Skip("framebuf: frames only under MicroPython")
    from ui.renderer import Renderer, FrameCapture
    s = TwoWatchSim()
    assert _run_until(s, _hunting, 30000)
    assert not _blank(s.frame_bytes(0))
    js = _rp_json(s, 0)
    s.real_mode(True)
    assert _blank(s.frame_bytes(0)) and _blank(s.frame_bytes(1))     # no sim screen left over
    n = list(s.frames)
    s.step(200)
    assert s.frames == n                   # nothing to draw until a record arrives
    s.show_params(0, js)
    p = from_dict(json.loads(js))
    per = 1000 // p.fps_cap                # one frame per step
    r = Renderer()
    cap = FrameCapture()
    t = s.real_ms
    for _ in range(30):
        s.step(per)
        t += per
        r.frame(p, cap, t)
    assert s.frames[0] == n[0] + 30 and s.frames[1] == n[1]
    assert bytes(s.frame_bytes(0)) == bytes(cap.buf)      # the same frame, ring for ring
    assert _blank(s.frame_bytes(1))
    s.real_mode(False)
    assert _blank(s.frame_bytes(0))
    s.step(100)
    assert not _blank(s.frame_bytes(0)) and not _blank(s.frame_bytes(1))


def test_page_follows_the_debug_contract():
    """web/sim/index.html asks the bridge's endpoints and calls the host's real-mode methods."""
    if MPY:
        raise Skip("reads web/sim/index.html and tools/debug_server.py (CPython)")
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(root, "web", "sim", "index.html"), encoding="utf-8") as f:
        page = f.read()
    with open(os.path.join(root, "tools", "debug_server.py"), encoding="utf-8") as f:
        server = f.read()
    for path in ("/debug/status", "/events"):
        assert ("'.%s'" % path) in page and ('"%s"' % path) in server, path
    for name in ("real_mode", "show_params"):
        assert ("call('%s'" % name in page or "SIM.%s(" % name in page), name
        assert callable(getattr(TwoWatchSim, name)), name
    # a line a watch printed on its USB port: {src, rx, line}
    assert '"line": data.decode' in server and "typeof msg.line === 'string') onPrinted(msg)" in page


def test_page_waiting_and_unavailable_name_the_usb_commands():
    """The waiting how-to is USB first (plug in, deploy with --port and --debug, the bridge with
    --serial, the local page), then one paragraph on Wi-Fi; the unavailable note names the
    bridge with --serial."""
    if MPY:
        raise Skip("reads web/sim/index.html (CPython)")
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(root, "web", "sim", "index.html"), encoding="utf-8") as f:
        page = f.read()
    k = page.index('<div class="howto" id="howto">')
    howto = page[k:page.index("</div>", k)]
    steps = howto[howto.index("<ol>"):howto.index("</ol>")]
    order = [steps.index(s) for s in (
        "Plug both watches", "python3 tools/deploy.py --port P --debug A",
        "python3 tools/deploy.py --port P --debug B", "python3 tools/debug_server.py --serial",
        "http://localhost:8765/local.html")]
    assert order == sorted(order), order
    wifi = howto[howto.index("</ol>"):]
    for s in ("Over Wi-Fi instead", "python3 tools/wifi_setup.py", "--debug A --wifi",
              "--debug B --wifi"):
        assert s in wifi, s
    assert "secrets.py" not in howto and "--demo --serial" in wifi
    k = page.index("\nfunction markReal(")
    mark = page[k:page.index("\n}\n", k)]
    assert "python3 tools/debug_server.py --serial</code>" in mark and "USB ports or Wi-Fi" in mark


def test_page_real_mode_shows_usb_ports_and_printed_text():
    """Text a watch printed on its USB port goes to the raw log, marked with the port, and is
    not a record; "Sent from" names the USB port or the Wi-Fi address; a silent USB watch is
    asked about its cable, a Wi-Fi one about the Wi-Fi."""
    if MPY:
        raise Skip("runs the page's own functions in node (CPython)")
    import shutil
    import subprocess
    node = shutil.which("node")
    if not node:
        raise Skip("needs node")
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(root, "web", "sim", "index.html"), encoding="utf-8") as f:
        page = f.read()
    stubs = "let pageError = null, printedOn = null, rawDirty = false;\n"   # LOG_N's line has CLASH_MS
    script = stubs + _page_js(page, ("LOG_N", "rawLog", "esc", "num", "real", "realSilent", "ago",
                                     "heardAgo", "chSplit", "viaWifi", "sentFrom", "realWhy",
                                     "pushLog", "logLine", "onPrinted", "howtoLead")) + """
const out = { lead0: howtoLead(printedOn) };
onPrinted({ src: 'cu.usbserial-022152D1', rx: 1, line: 'Traceback (most recent call last):' });
logLine('cu.usbserial-022152D1', { dev: 'A', ev: 's' });
logLine('192.168.1.40', { dev: 'B', ev: 's' });
out.log = rawLog.map((l) => l.slice(10));                     // without the time
out.printedOn = printedOn;
out.lead1 = howtoLead(printedOn);
out.from = [sentFrom('cu.usbserial-022152D1'), sentFrom('192.168.1.40'), sentFrom('pts/3'),
            sentFrom(null), sentFrom('<b>')];
out.why = [realWhy(0, { heard: 0, src: 'cu.usbserial-1', clash: null }, 5000, true),
           realWhy(1, { heard: 0, src: '10.0.0.7', clash: null }, 5000, true)];
for (let k = 0; k < 60; k++) onPrinted({ src: 'ttyUSB0', line: 'n' + k });
out.kept = [rawLog.length, rawLog[0].slice(10), rawLog[rawLog.length - 1].slice(10)];
console.log(JSON.stringify(out));
"""
    r = subprocess.run([node, "-e", script], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    assert out["log"] == ["cu.usbserial-022152D1  printed: Traceback (most recent call last):",
                          'cu.usbserial-022152D1  {"dev":"A","ev":"s"}',
                          '192.168.1.40  {"dev":"B","ev":"s"}'], out["log"]
    assert out["printedOn"] == "cu.usbserial-022152D1"
    assert out["lead0"].startswith("Nothing has arrived from a watch yet."), out["lead0"]
    assert out["lead1"].startswith("USB port cu.usbserial-022152D1 sends text but no watch "
                                   "records yet"), out["lead1"]
    assert out["from"] == ["USB port cu.usbserial-022152D1", "Wi-Fi 192.168.1.40", "USB port pts/3",
                           "–", "USB port &lt;b&gt;"], out["from"]
    assert "USB cable plugged in" in out["why"][0] and "Wi-Fi" not in out["why"][0], out["why"]
    assert "same Wi-Fi" in out["why"][1] and "USB" not in out["why"][1], out["why"]
    assert out["kept"] == [50, "ttyUSB0  printed: n10", "ttyUSB0  printed: n59"], out["kept"]


def _page_js(page, names):
    """The page's top-level ``function NAME(`` blocks (one line, or to the first ``}`` at
    column 0) and ``const NAME = ...;`` lines, in the order given."""
    out = []
    for name in names:
        k = page.find("\nfunction %s(" % name)
        if k >= 0:
            line = page[k + 1:page.index("\n", k + 1)]
            one = line.count("{") == line.count("}")
            out.append(line if one else page[k + 1:page.index("\n}\n", k) + 2])
            continue
        k = page.find("\nconst %s = " % name)
        assert k >= 0, name
        out.append(page[k + 1:page.index("\n", k + 1)])
    return "\n".join(out)


def test_page_real_mode_shows_a_failed_start():
    """A MicroPython or bundle failure while Real watches is shown: #boot (in the Simulator
    card) is hidden, so the watch cards must not keep saying Live or 'next message'."""
    if MPY:
        raise Skip("runs the page's own functions in node (CPython)")
    import shutil
    import subprocess
    node = shutil.which("node")
    if not node:
        raise Skip("needs node")
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(root, "web", "sim", "index.html"), encoding="utf-8") as f:
        page = f.read()
    assert "\nlet pageError = null;\n" in page
    stubs = (
        "let pageError = null; const SILENT_MS = 3000, CLASH_MS = 10000;\n"
        "function setStatus() {}\n"
        "const boot = { hidden: true, innerHTML: '', appendChild() {} };\n"
        "const document = { createElement: () => ({ appendChild() {} }) };\n")
    script = stubs + _page_js(page, ("fail", "num", "real", "realSilent", "ago", "heardAgo",
                                     "chSplit", "viaWifi", "realWhy", "waitText")) + """
const w = { heard: 0, clash: null, bad: null, rp: null, shown: false };
const out = { before: [realWhy(0, w, 400, true), waitText(0, w)] };
w.shown = true; out.drawn = waitText(0, w); w.shown = false;
fail('Could not load the watch code bundle (py/bundle.json).', 'HTTP 404');
out.err = pageError;
out.after = [realWhy(0, w, 400, true), waitText(0, w)];
w.shown = true; out.drawnAfter = waitText(1, w);
out.waiting = [realWhy(1, { heard: null }, 400, true), waitText(1, { heard: null, shown: false })];
out.silent = realWhy(0, w, 5000, true);
console.log(JSON.stringify(out));
"""
    r = subprocess.run([node, "-e", script], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    assert out["before"] == ["Live: last heard just now.", "The screen comes with its next message"], out
    assert out["drawn"] is None
    assert out["err"] == "Could not load the watch code bundle (py/bundle.json)."
    why, wait = out["after"]
    assert not why.startswith("Live") and "could not run its screen code" in why, why
    assert out["err"] in why and "Reload the page" in why, why
    assert wait == "This page could not run its screen code", wait
    assert out["drawnAfter"] == wait                     # a screen drawn before no longer follows
    assert out["waiting"][0].startswith("Waiting for watch B") and out["waiting"][1] == wait, out
    assert out["silent"].startswith("Last heard 5.0 s ago. Is the watch on"), out["silent"]


def test_page_real_mode_names_a_channel_split():
    """Both watches live on different Wi-Fi channels (rp.ch; a mesh or an extender can do it)
    cannot hear each other: both cards say why. Equal channels, a null ch (the fake watches,
    older records) or a silent watch say nothing about it."""
    if MPY:
        raise Skip("runs the page's own functions in node (CPython)")
    import shutil
    import subprocess
    node = shutil.which("node")
    if not node:
        raise Skip("needs node")
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(root, "web", "sim", "index.html"), encoding="utf-8") as f:
        page = f.read()
    stubs = "let pageError = null; const SILENT_MS = 3000, CLASH_MS = 10000;\n"
    script = stubs + _page_js(page, ("num", "real", "realSilent", "ago", "heardAgo",
                                     "chSplit", "viaWifi", "realWhy")) + """
const why = () => [realWhy(0, real[0], 400, true), realWhy(1, real[1], 400, true)];
Object.assign(real[0], { heard: 0, rp: { ev: 'rp', ch: 1 } });
Object.assign(real[1], { heard: 100, rp: { ev: 'rp', ch: 6 } });
const out = { split: why() };
real[1].rp.ch = 1; out.same = why();
real[1].rp.ch = null; out.fake = why();
delete real[1].rp.ch; out.old = why();
real[1].rp.ch = 6; out.splitAgain = why();
real[0].heard = -5000; out.silent = why();
real[0].heard = 0; real[1].rp = null; out.noRp = why();
console.log(JSON.stringify(out));
"""
    r = subprocess.run([node, "-e", script], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    msg = ("The two watches joined different parts of your Wi-Fi (different channels), so they "
           "cannot hear each other. Use a network with one access point.")
    assert out["split"] == [msg, msg] and out["splitAgain"] == [msg, msg], out
    for k in ("same", "fake", "old", "noRp"):
        assert [w.startswith("Live") for w in out[k]] == [True, True], (k, out[k])
    assert out["silent"][0].startswith("Last heard 5.4 s ago. Is the watch on"), out["silent"]
    assert out["silent"][1].startswith("Live"), out["silent"]
