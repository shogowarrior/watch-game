"""finder.game / finder.pairing / finder.session unit tests (no radio sim).

A ``Rig`` drives one ``Game`` with a fake partner (beacons built by hand) and
a fake estimator whose distance the test sets. Every RenderParams produced is
checked with ``validate``.
"""

from finder import proto
from finder import tuning as T
from finder.game import Game, M_HUNT, M_SCANNING, M_FOUND, M_LINK_LOST, M_SEARCHING, M_PAIRING
from finder.pairing import Calibrator, Pairing, rune_ids, fnv1a32
from finder.session import (PeerView, LiveMirror, fmt_mss, screen_code, SC_PAIRING, SC_FAR,
                            SC_HOT, SC_FOUND, SC_SCANNING, ST_PRESS, ST_GOODBYE, ST_CONFIRMED)
from finder.render_params import validate
from finder.estimators.base import ACT_STILL, ACT_WALK
from finder import arrow as A

MAC_A = b"\x24\x0a\xc4\x10\x00\x0a"
MAC_B = b"\x24\x0a\xc4\x10\x00\x0b"
SC_WARM = 4


class FakeEst:
    """Estimator stand-in: ``fixed`` is the distance reported after any packet."""

    name = "fake"

    def __init__(self, d=40.0):
        self.fixed = d
        self.cal = None
        self.n = 0
        self.reset()

    def reset(self):
        self.rssi_f = None
        self.rssi_var = 4.0
        self.rate_db_s = 0.0
        self.dist_m = None
        self.dist_lo_m = None
        self.dist_hi_m = None
        self.trend = 0
        self.trend_conf = 0.0

    def calibrate(self, p):
        self.cal = p

    def update(self, t, rssi, peer_rssi=None, my=None, peer=None):
        self.n += 1
        self.rssi_f = float(rssi)
        self.dist_m = self.fixed


class Rig:
    """One Game plus a scripted partner; ``run`` ticks at 10 Hz and validates."""

    def __init__(self, d=40.0, mac=MAC_A, peer=MAC_B, battery=90):
        self.est = FakeEst(d)
        self.g = Game(mac, est=self.est, battery=battery)
        self.peer = peer
        self.t = 0
        self.b = proto.Beacon(1)
        self.state = SC_PAIRING
        self.flags = 0
        self.bump_ago = proto.BUMP_NONE
        self.peer_bat = 80
        self.peer_act = ACT_STILL
        self.rssi = -60
        self.params = []
        self.activity = ACT_STILL
        self.steps = 0
        self.tilt = 5.0
        self.face_up = True
        self.n_pk = 0

    def packet(self, t=None, rssi=None):
        b = self.b
        b.state = self.state
        b.flags = self.flags
        b.bump_ago_ms = self.bump_ago
        b.battery = self.peer_bat
        b.activity = self.peer_act
        b.rssi_last = -61
        b.rssi_filt = -60
        self.g.on_packet(self.t if t is None else t, self.peer,
                         self.rssi if rssi is None else rssi, b)

    def run(self, ms, every=100, packets=True, drop=0):
        """Advance ``ms`` in 100 ms ticks; one packet every ``every`` ms (at its
        own time), skipping every ``drop``-th packet when ``drop`` is set."""
        end = self.t + ms
        last = []
        while self.t < end:
            t0 = self.t
            self.t += 100
            if packets and every:
                k = t0 - t0 % every + every
                while k <= self.t:
                    self.n_pk += 1
                    if not drop or self.n_pk % drop:
                        self.packet(t=k)
                    k += every
            self.g.set_motion(self.t, self.activity, self.steps, 0.0, self.tilt, self.face_up)
            p = self.g.tick(self.t)
            v = validate(p)
            assert not v, (self.t, p.screen, p.sub, v)
            self.params.append(p)
            last.append(p)
        return last

    @property
    def p(self):
        return self.params[-1]

    def haptics(self, since=0):
        return [p.haptic for p in self.params if p.haptic and p.t_ms > since]


def paired_rig(d=40.0, **kw):
    """A Rig taken through pairing (button confirm) and the split into the hunt."""
    r = Rig(d=d, **kw)
    r.rssi = -50
    r.run(600)
    assert r.g.pair.sub == "seen", r.g.pair.sub
    r.g.on_button(r.t)
    r.state = SC_PAIRING | ST_CONFIRMED
    r.rssi = -45
    r.run(4500)
    assert r.g.pair.sub == "split", r.g.pair.sub
    r.rssi = -60
    r.run(31500)
    assert r.g.mode == M_HUNT, r.g.mode
    return r


def hot_rig(d=2.0):
    r = paired_rig(d=d)
    r.state = SC_HOT
    r.run(2000)
    assert r.p.screen == "HOT"
    return r


# ---- pairing helpers ------------------------------------------------------------

def test_runes_symmetric_and_deterministic():
    a = rune_ids(MAC_A, MAC_B)
    assert a == rune_ids(MAC_B, MAC_A)
    assert len(a) == 3 and all(0 <= x <= 7 for x in a)
    assert fnv1a32(b"") == 0x811C9DC5
    assert fnv1a32(b"a") == 0xE40C292C          # published FNV-1a test vector
    seen = set()
    for i in range(64):
        seen.add(rune_ids(MAC_A, bytes((0, 0, 0, 0, 1, i))))
    assert len(seen) > 20


def test_calibrator_mean_clamp_pause_and_skip():
    c = Calibrator()
    c.reset(0)
    t = 0
    while not c.done:
        t += 50
        c.add(t, -47 if (t // 50) % 2 else -49)
        c.update(t)
    assert not c.skipped and abs(c.p1m - (-48.0)) < 0.6, c.p1m
    assert 3000 <= t <= 4500, t
    # clamp to nominal +-6
    c.reset(0)
    t = 0
    while not c.done:
        t += 50
        c.add(t, -30)
        c.update(t)
    assert c.p1m == T.P1M_NOMINAL_DBM + T.CAL_CLAMP_DB
    # unstable (sd > 4 dB) pauses the fill; after 10 s the nominal is used
    c.reset(0)
    t = 0
    while not c.done:
        t += 50
        c.add(t, -40 if (t // 50) % 2 else -52)
        c.update(t)
        assert c.fill_ms == 0
    assert c.skipped and c.p1m == T.P1M_NOMINAL_DBM and t == T.CAL_SKIP_AFTER_MS


def test_pairing_ignores_weak_or_non_pairing_senders():
    p = Pairing(MAC_A)
    for i in range(10):
        p.on_candidate(100 * i, MAC_B, -75)                  # too weak
        p.on_candidate(100 * i, MAC_B, -50, peer_pairing=False)
    assert p.sub == "looking"
    for i in range(3):
        p.on_candidate(1000 + 100 * i, MAC_B, -50)
    assert p.sub == "seen" and p.peer_mac == MAC_B and p.runes == rune_ids(MAC_A, MAC_B)
    assert p.update(1300) == "DOUBLE"
    assert p.update(7000) is None and p.sub == "looking"   # partner silent 5 s


def test_session_helpers():
    assert fmt_mss(12000) == "0:12" and fmt_mss(87000) == "1:27"
    assert fmt_mss(599999) == "9:59" and fmt_mss(600000) == "10M+"
    assert screen_code("PAIRING") == SC_PAIRING and screen_code("HOT") == SC_HOT
    assert screen_code("FOUND") == SC_FOUND and screen_code("SCANNING") == SC_SCANNING
    pv = PeerView()
    b = proto.Beacon(1)
    b.bump_ago_ms = 250
    b.set_flags(taps=1)
    b.steps = 65530
    pv.on_beacon(10000, b)
    assert pv.tap_t == 10000 - 250 - 2
    b.steps = 4                       # wraps: +10 steps
    b.bump_ago_ms = 1250
    pv.on_beacon(11000, b)
    assert pv.tap_t == 10000 - 250 - 2 and pv.motion.steps == 10
    b.bump_ago_ms = 40
    b.set_flags(taps=2)
    pv.on_beacon(11100, b)
    assert pv.tap_t == 11100 - 42
    assert not pv.live3(11100 + 3000)
    m = LiveMirror()
    m.reset(0)
    for i in range(20):
        m.add(50 * i, -70 + (i % 10))
        assert 0.0 <= m.value <= 1.0


# ---- pairing flow via Game --------------------------------------------------------

def test_pairing_flow_button_confirm_calibrate_split():
    r = Rig()
    r.rssi = -50
    r.run(600)
    g = r.g
    assert r.p.screen == "PAIRING" and r.p.sub == "seen" and r.p.glyph == "runes"
    assert "DOUBLE" in r.haptics()
    assert g.runes == rune_ids(MAC_A, MAC_B)
    r.g.on_button(r.t)
    r.run(300)
    assert r.p.sub == "confirmed" and r.p.word == "WAITING"
    assert g.state_byte & ST_CONFIRMED
    r.state = SC_PAIRING | ST_CONFIRMED
    r.rssi = -44
    r.run(200)
    assert r.p.sub == "calibrate" and r.p.glyph == "countdown" and r.p.countdown == 3
    r.run(4000)
    assert r.p.sub == "split" and r.est.cal == -44.0
    assert r.haptics().count("TICK") >= 3 and "CLOSER" in r.haptics()
    assert r.p.top_text == "NO PEEKING" and r.p.word == "SPLIT UP" and r.p.heartbeat is None
    assert r.p.countdown == 30
    t0 = r.t
    r.rssi = -60
    n = 0
    while r.p.word != "GO":
        r.run(100)
        n += 1
        assert n < 310
    assert r.p.countdown == 0 and r.p.haptic == "CLOSER"
    assert r.haptics(t0) == ["TICK", "TICK", "TICK", "CLOSER"], r.haptics(t0)
    r.run(1000)
    assert g.mode == M_HUNT and r.p.screen == "FAR" and r.p.dist_band == "~40"


def test_split_without_link_goes_to_searching_then_zone_on_three_packets():
    r = Rig()
    r.rssi = -50
    r.run(600)
    r.g.on_button(r.t)
    r.state = SC_PAIRING | ST_CONFIRMED
    r.rssi = -45
    r.run(4500)
    r.run(31500, packets=False)
    assert r.g.mode == M_SEARCHING and r.p.screen == "SEARCHING"
    assert r.p.ramp == "grey" and r.p.speed_px_s < 0 and r.p.dist_band is None
    assert r.p.word == "SEARCHING" and r.p.haptic is None and r.p.heartbeat is None
    r.run(46000, packets=False)
    assert r.p.word == "WALK ABOUT"
    r.state = SC_FAR
    r.est.fixed = 80.0
    r.packet()
    r.run(300, packets=False)
    r.t += 100
    r.packet()
    r.run(100, packets=False)
    assert r.g.mode == M_SEARCHING          # 2 packets are not enough
    r.packet()
    r.run(100, packets=False)
    assert r.p.screen == "FAR" and r.p.burst and r.p.haptic == "CLOSER"
    assert r.p.top_text == "TAP TO SCAN" or r.p.status[3]


def test_bump_confirms_pairing_on_both_sides_at_once():
    r = Rig()
    r.rssi = -50
    r.run(600)
    assert r.g.pair.sub == "seen"
    assert not r.g.on_accel_tap(r.t)           # the DOUBLE (partner seen) still blanks
    r.run(500)
    assert r.g.on_accel_tap(r.t)
    r.bump_ago = 100
    r.b.set_flags(taps=1)
    r.flags = r.b.flags
    r.run(200)
    assert r.g.pair.sub == "calibrate"


# ---- hunt, trend-free glyphs, readout --------------------------------------------

def test_zone_screens_follow_distance_with_haptics():
    r = paired_rig(d=40.0)
    r.state = SC_FAR
    r.est.fixed = 20.0
    r.run(4000)
    assert r.p.screen == "NEAR"
    r.est.fixed = 10.0
    r.run(3000)
    assert r.p.screen == "WARM" and r.p.heartbeat == "DOUBLE" and r.p.dist_band == "~10"
    r.est.fixed = 5.0
    t0 = r.t
    r.run(2500)
    assert r.p.screen == "HOT" and r.p.dist_band == "~5"
    assert "CLOSER" in r.haptics(t0)
    assert any(p.burst for p in r.params if p.t_ms > t0)
    assert any(p.top_text == "LOOK AROUND" for p in r.params if p.t_ms > t0)
    assert r.g.beacon_hz == 20
    r.est.fixed = 12.0
    t0 = r.t
    r.run(2500)
    assert r.p.screen == "WARM" and "FARTHER" in r.haptics(t0)
    for p in r.params:
        for s in (p.word, p.top_text):
            assert s not in ("FAR", "NEAR", "WARM", "HOT")


def test_bump_ready_word_and_double():
    r = hot_rig(4.0)
    assert r.p.dist_band == "~5" and r.p.word is None
    r.est.fixed = 1.5
    t0 = r.t
    r.run(2500)
    assert r.p.word == "BUMP!" and r.p.top_text == "TAP WATCHES"
    assert r.haptics(t0).count("DOUBLE") == 1


# ---- FOUND --------------------------------------------------------------------------

def test_found_needs_both_taps_within_400ms_in_hot():
    r = hot_rig()
    r.est.fixed = 1.5
    r.run(2000)
    g = r.g
    assert g.on_accel_tap(r.t)
    # partner tapped 600 ms before us: no match
    r.bump_ago = 600
    r.flags = 1 << proto.F_TAPS_SHIFT
    r.run(100)
    r.bump_ago = proto.BUMP_NONE
    r.run(500)
    assert g.mode == M_HUNT
    # a fresh pair of taps 150 ms apart -> FOUND
    r.run(1000)
    assert g.on_accel_tap(r.t)
    r.bump_ago = 50
    r.flags = 2 << proto.F_TAPS_SHIFT
    r.run(100)
    assert g.mode == M_FOUND and r.p.screen == "FOUND" and r.p.sub == "celebrate"
    assert r.p.haptic == "FOUND" and r.p.ramp == "gold" and r.p.glyph == "check"
    assert r.p.top_text.startswith("TIME ") and r.p.word == "FOUND"
    r.run(2100)
    assert r.p.sub == "result" and r.p.word == "TAP=AGAIN"


def test_rssi_alone_never_found_and_partner_must_be_hot():
    r = hot_rig()
    r.est.fixed = 0.5
    r.run(20000)
    assert r.g.mode == M_HUNT
    # our tap + theirs, but the partner reports WARM
    r.state = SC_WARM
    assert r.g.on_accel_tap(r.t)
    r.bump_ago = 20
    r.flags = 1 << proto.F_TAPS_SHIFT
    r.run(500)
    assert r.g.mode == M_HUNT
    # tap outside HOT does not count later either
    r2 = paired_rig(d=10.0)
    r2.state = SC_HOT
    assert r2.g.on_accel_tap(r2.t)
    r2.bump_ago = 20
    r2.flags = 1 << proto.F_TAPS_SHIFT
    r2.run(300)
    assert r2.g.mode == M_HUNT


def test_tap_guards_touch_and_blanking():
    r = hot_rig()
    g = r.g
    g.on_gesture(r.t, 1, 20, 20)                # outside the iris: no scan, but a touch
    assert not g.on_accel_tap(r.t + 100)        # within 300 ms of a touch
    r.run(1000)
    g._emit(r.t, "NOPE")                        # our own pulse blanks the accelerometer
    assert not g.on_accel_tap(r.t + 400)
    assert g.on_accel_tap(r.t + 950)
    g2 = Game(MAC_A, est=FakeEst(), blank_fn=lambda t: True)
    assert not g2.on_accel_tap(5000)


def test_fallback_both_short_presses_within_3s():
    r = hot_rig()
    g = r.g
    g.on_button(r.t)                            # fallback press (HOT)
    r.run(100)
    assert g.state_byte & ST_PRESS and g.mode == M_HUNT
    r.run(1500)
    r.state = SC_HOT | ST_PRESS
    r.run(200)
    assert g.mode == M_FOUND
    # too far apart: no FOUND
    r2 = hot_rig()
    r2.g.on_button(r2.t)
    r2.run(3300)
    r2.state = SC_HOT | ST_PRESS
    r2.run(500)
    assert r2.g.mode == M_HUNT


def test_new_round_from_found_tap_or_partner():
    r = hot_rig()
    g = r.g
    g.on_button(r.t)
    r.state = SC_HOT | ST_PRESS
    r.run(200)
    assert g.mode == M_FOUND
    r.state = SC_FOUND
    g.on_gesture(r.t + 50, 1)                   # celebrate: ignored
    r.run(2500)
    assert g.mode == M_FOUND
    g.on_gesture(r.t, 1)
    r.run(100)
    assert g.mode == M_PAIRING and r.p.sub == "split" and r.p.countdown == 30
    # the partner follows us into the new round
    r2 = hot_rig()
    r2.g.on_button(r2.t)
    r2.state = SC_HOT | ST_PRESS
    r2.run(200)
    r2.state = SC_FOUND
    r2.run(3000)
    r2.state = SC_PAIRING | ST_CONFIRMED
    r2.run(300)
    assert r2.g.mode == M_PAIRING and r2.g.pair.sub == "split"


# ---- LINK_LOST ----------------------------------------------------------------------

def test_link_lost_banner_and_relink():
    r = paired_rig(d=20.0)
    r.state = 3
    r.run(2000)
    assert r.p.screen == "NEAR"
    t0 = r.t
    r.run(5000, packets=False)
    assert r.g.mode == M_HUNT
    r.run(100, packets=False)
    assert r.p.screen == "LINK_LOST" and r.p.haptic == "LOST"
    assert r.p.ramp == "grey" and r.p.zone == 1 and r.p.dist_band == "~20" and r.p.dist_stale
    assert r.p.banner[0].startswith("LOST 0:0") and r.p.banner[2] is True
    assert r.p.speed_px_s < 0 and r.p.arrow_deg is None
    r.run(20000, packets=False)
    assert r.p.banner[0].endswith("GO BACK") and r.g.mode == M_LINK_LOST
    assert "LOST" not in r.haptics(t0 + 5500)
    r.run(600000, packets=False)
    assert r.p.banner[0] == "LOST 10M+ GO BACK" and r.p.screen == "LINK_LOST"
    r.est.fixed = 9.0
    t1 = r.t
    last = r.run(400)
    assert r.p.screen == "WARM" and "CLOSER" in r.haptics(t1)
    assert [p.burst for p in last].count(True) == 1
    assert r.p.banner == ("BACK IN RANGE", "info", False)


def test_partner_goodbye_and_low_battery_banners():
    r = paired_rig(d=20.0)
    r.peer_bat = 4
    r.run(500)
    assert any(p.banner and p.banner[0] == "FRIEND BATT 20%" for p in r.params[-5:])
    r.run(6000, packets=False)
    assert r.p.banner == ("FRIEND LOW BATTERY", "warn", True)
    r.state = ST_GOODBYE | 0x0F
    r.packet()
    r.run(6000, packets=False)
    assert r.p.banner == ("FRIEND IS OFF", "critical", True)


# ---- SCANNING -----------------------------------------------------------------------

def test_scan_start_cancel_and_friend_scanning():
    r = paired_rig(d=10.0)
    r.state = SC_WARM
    r.run(3000)
    g = r.g
    g.on_gesture(r.t, 1, 120, 120)
    r.run(100)
    assert g.mode == M_SCANNING and r.p.screen == "SCANNING" and r.p.sub == "ready"
    assert r.p.glyph == "countdown" and r.p.word == "TURN RIGHT" and r.p.heartbeat is None
    assert g.beacon_hz == 20
    b = proto.Beacon(1)
    g.fill_beacon(b, r.t)
    assert b.sweeping
    r.run(700)
    g.on_gesture(r.t, 1, 120, 120)              # any centre tap cancels
    r.run(100)
    assert g.mode == M_HUNT and g.arrow is None and r.p.banner is None
    # the partner is already scanning: toast instead of a scan
    r.flags = proto.F_SWEEP
    r.run(200)
    g.on_gesture(r.t, 1, 120, 120)
    r.run(100)
    assert g.mode == M_HUNT and r.p.banner == ("FRIEND SCANNING", "info", False)


def test_both_scanning_lower_mac_keeps_its_scan():
    hi = paired_rig(d=10.0, mac=MAC_B, peer=MAC_A)
    hi.state = SC_WARM
    hi.run(3000)
    hi.g.on_button(hi.t)
    hi.run(100)
    assert hi.g.mode == M_SCANNING
    hi.flags = proto.F_SWEEP
    hi.run(300)
    assert hi.g.mode == M_HUNT and any(p.banner and p.banner[0] == "FRIEND SCANNING"
                                       for p in hi.params[-3:])
    lo = paired_rig(d=10.0)
    lo.state = SC_WARM
    lo.run(3000)
    lo.g.on_button(lo.t)
    lo.run(100)
    lo.flags = proto.F_SWEEP
    lo.run(300)
    assert lo.g.mode == M_SCANNING


def test_partner_scanning_hold_chip_and_repeats_while_walking():
    r = paired_rig(d=10.0)
    r.state = SC_WARM
    r.run(3000)
    r.flags = proto.F_SWEEP
    t0 = r.t
    r.run(200)
    assert r.p.top_text == "FRIEND SCANNING" and r.p.word == "HOLD STILL"
    assert r.haptics(t0) == ["HOLD"]
    assert r.g.beacon_hz == 20
    r.activity = ACT_WALK
    r.run(12000)
    assert r.haptics(t0).count("HOLD") == 3


# ---- MENU ---------------------------------------------------------------------------

def test_menu_rows_sun_buzz_end_round():
    r = paired_rig(d=10.0)
    r.state = SC_WARM
    r.run(2000)
    g = r.g
    g.on_gesture(r.t, 3, 120, 120)              # long press
    r.run(100)
    assert r.p.screen == "MENU" and r.p.sub == "0v" and r.p.haptic is None   # more rows below
    assert r.p.heartbeat is None and r.p.word is None and r.p.top_text is None
    g.on_button(r.t)                            # short = next row
    g.on_button(r.t, long=True)                 # long = select (SUN)
    r.run(100)
    assert g.sun and g.menu_rows[1] == "SUN: ON" and r.p.backlight == 1.0
    r.run(1000)                                 # (>= 3 touches in 1 s would block touches)
    g.on_gesture(r.t, 1, 120, 140)              # tap the third row (y 120..159) = BUZZ
    r.run(1000)
    assert g.buzz == 1 and g.menu_rows[2] == "BUZZ: EVENTS"
    g.on_gesture(r.t, 1, 120, 50)               # RESUME
    r.run(1000)
    assert r.p.screen == "WARM" and r.p.heartbeat is None    # EVENTS: no heartbeat
    g.on_button(r.t, long=True)
    g.on_gesture(r.t + 10, 6, 120, 120)         # swipe up: END ROUND scrolls into view
    r.run(100)
    assert g.menu_rows[3] == "END ROUND" and r.p.sub == "0^"
    g.on_gesture(r.t, 1, 120, 170)              # END ROUND asks first
    r.run(100)
    assert g.menu_rows[3] == "SURE? PRESS" and g.mode == M_HUNT and r.p.sub == "3^"
    r.run(3100)
    assert g.menu_rows[3] == "END ROUND"
    r.run(1000)
    g.on_gesture(r.t, 1, 120, 170)
    r.run(1000)
    g.on_button(r.t)                            # second press within 3 s
    r.run(100)
    assert g.mode == M_PAIRING and g.pair.sub == "looking" and not g.menu_open
    # auto-close after 8 s
    g.on_button(r.t, long=True)
    r.run(8100)
    assert not g.menu_open


def test_menu_place_row_scrolls_and_switches_the_exponent():
    from finder import tuning as T
    r = paired_rig(d=10.0)
    r.state = SC_WARM
    r.run(2000)
    g = r.g
    g.est.set_exponent = lambda n: setattr(g.est, "exp_n", n)   # record what the game sends
    g.set_place(False)
    assert not g.indoor and g.est.exp_n == T.PATH_LOSS_N
    g.on_button(r.t, long=True)                 # open
    r.run(100)
    assert g.menu_rows == ["RESUME", "SUN: OFF", "BUZZ: FULL", "PLACE: OUT"] and r.p.sub == "0v"
    for _ in range(3):
        g.on_button(r.t)                        # step to PLACE (still in view)
    r.run(100)
    assert r.p.sub == "3v" and g.menu_top == 0
    g.on_button(r.t)                            # step to END ROUND: the list scrolls
    r.run(100)
    assert g.menu_top == 1 and r.p.sub == "3^" and g.menu_rows[3] == "END ROUND"
    g.on_button(r.t)                            # wraps to RESUME at the top
    r.run(100)
    assert g.menu_top == 0 and r.p.sub == "0v"
    g.on_gesture(r.t, 1, 120, 170)              # tap PLACE
    r.run(100)
    assert g.indoor and g.est.exp_n == T.PATH_LOSS_N_INDOOR and g.menu_rows[3] == "PLACE: IN"
    r.run(1000)
    g.on_gesture(r.t, 7, 120, 120)              # swipe down at the top: stays put
    r.run(100)
    assert g.menu_top == 0
    r.run(1000)                                 # (touch burst filter)
    g.on_gesture(r.t, 1, 120, 50)               # tap RESUME closes; reopen at the top, sel 0
    g.on_button(r.t + 10, long=True)
    r.run(1000)
    assert g.menu_sel == 0 and g.menu_top == 0
    g.on_gesture(r.t, 6, 120, 120)              # swipe up: selection follows into view
    r.run(100)
    assert g.menu_top == 1 and g.menu_sel == 1 and r.p.sub == "0^"
    g.on_button(r.t)
    g.on_button(r.t)
    g.on_button(r.t)                            # SUN -> BUZZ -> PLACE -> END ROUND
    r.run(100)
    assert g.menu_sel == 4 and g.menu_top == 1 and r.p.sub == "3^"
    g.on_gesture(r.t, 7, 120, 120)              # swipe down: END ROUND leaves, sel clamps to PLACE
    r.run(100)
    assert g.menu_top == 0 and g.menu_sel == 3 and r.p.sub == "3v"
    assert g.menu_rows == ["RESUME", "SUN: OFF", "BUZZ: FULL", "PLACE: IN"]
    r.run(1000)                                 # (touch burst filter)
    g.on_gesture(r.t, 6, 120, 120)
    r.run(1000)
    assert g.menu_top == 1
    g.on_gesture(r.t, 1, 120, 170)              # END ROUND, then confirm
    r.run(100)
    g.on_button(r.t)
    r.run(100)
    assert g.mode == M_PAIRING and g.indoor and g.est.exp_n == T.PATH_LOSS_N_INDOOR   # kept across rounds


def _menu_at_end_armed(r):
    """Open the menu, step to END ROUND (list scrolled) and arm SURE? PRESS."""
    g = r.g
    g.on_button(r.t, long=True)
    r.run(100)
    for _ in range(4):
        g.on_button(r.t)
    g.on_button(r.t, long=True)                 # select END ROUND: asks first
    r.run(100)
    assert g.menu_sel == 4 and g.menu_top == 1 and g.menu_rows[3] == "SURE? PRESS"
    assert r.p.sub == "3^" and g.mode == M_HUNT


def test_menu_end_round_confirm_cancelled_by_swipe():
    r = paired_rig(d=10.0)
    r.state = SC_WARM
    r.run(2000)
    g = r.g
    _menu_at_end_armed(r)
    g.on_gesture(r.t, 7, 120, 120)              # swipe down: SURE? PRESS scrolls away
    r.run(100)
    assert g.menu_rows == ["RESUME", "SUN: OFF", "BUZZ: FULL", "PLACE: OUT"] and r.p.sub == "3v"
    g.on_button(r.t)                            # short = next row, not a confirm
    r.run(100)
    assert g.mode == M_HUNT and g.menu_open and g.menu_sel == 4 and r.p.sub == "3^"
    assert g.menu_rows[3] == "END ROUND"        # the question is gone: it must be asked again
    g.on_button(r.t)                            # wraps to RESUME
    r.run(100)
    assert g.mode == M_HUNT and g.menu_sel == 0


def test_menu_end_round_confirm_cancelled_by_other_row():
    r = paired_rig(d=10.0)
    r.state = SC_WARM
    r.run(2000)
    g = r.g
    _menu_at_end_armed(r)
    r.run(1000)                                 # (touch burst filter)
    g.on_gesture(r.t, 1, 120, 50)               # tap SUN (visible row 0 at top 1)
    r.run(100)
    assert g.sun and g.menu_sel == 1 and g.menu_rows[3] == "END ROUND"
    g.on_button(r.t)                            # next row: BUZZ, the round goes on
    r.run(100)
    assert g.mode == M_HUNT and g.menu_sel == 2
    # armed, then the short press moves on from END ROUND itself: confirms (same row)
    for _ in range(2):
        g.on_button(r.t)
    g.on_button(r.t, long=True)
    r.run(100)
    assert g.menu_sel == 4 and g.menu_rows[3] == "SURE? PRESS"
    g.on_button(r.t)
    r.run(100)
    assert g.mode == M_PAIRING


def test_menu_rows_and_sub_change_together_at_the_tick():
    """The renderer gets menu_rows and sub from the same tick: an input between ticks
    must not shift the window under a stale highlight."""
    r = paired_rig(d=10.0)
    r.state = SC_WARM
    r.run(2000)
    g = r.g
    g.on_button(r.t, long=True)
    r.run(100)
    for _ in range(4):
        g.on_button(r.t)
    r.run(100)
    assert r.p.sub == "3^" and g.menu_rows[3] == "END ROUND"
    before = list(g.menu_rows)
    g.on_button(r.t)                            # wraps to RESUME: the window moves up ...
    assert g.menu_rows == before                # ... but not before the next tick
    g.on_gesture(r.t, 7, 120, 120)
    assert g.menu_rows == before
    r.run(100)
    assert g.menu_rows == ["RESUME", "SUN: OFF", "BUZZ: FULL", "PLACE: OUT"] and r.p.sub == "0v"


def test_place_survives_round_resets_for_every_estimator():
    from finder import tuning as T
    from finder.estimators import NAMES, make
    for name in NAMES:
        g = Game(MAC_A, est=make(name))
        for indoor, n in ((True, T.PATH_LOSS_N_INDOOR), (False, T.PATH_LOSS_N)):
            g.set_place(indoor)
            g.reset(0)                          # END ROUND / new round
            g.est.calibrate(-47.0)              # pairing calibration, then its reset
            g.est.reset()
            assert g.est.pl.n == n, (name, indoor, g.est.pl.n)


# ---- battery, screen power, beacon --------------------------------------------------

def test_low_battery_ladder_and_goodbye():
    r = paired_rig(d=20.0, battery=25)
    g = r.g
    t0 = r.t
    g.set_battery(r.t, 20)
    r.run(100)
    assert r.p.banner == ("BATTERY 20%", "warn", False) and r.p.haptic == "BATT"
    assert r.p.status[3]
    g.set_battery(r.t, 10)
    r.run(100)
    assert r.p.glyph == "battery" and r.p.word == "SAVER ON" and r.p.haptic == "BATT"
    assert r.p.fps_cap == 15 and g.beacon_hz == 5
    r.run(2600)
    assert r.p.glyph != "battery"
    r.run(30000)
    assert r.p.backlight == 0.35
    g.set_battery(r.t, 5)
    r.run(100)
    assert r.p.banner == ("BATTERY 5%", "critical", False)
    assert r.haptics(t0).count("BATT") == 3
    g.set_battery(r.t, 3)
    r.run(100)
    assert r.p.word == "BYE" and r.p.haptic == "NOPE"
    b = proto.Beacon(1)
    for _ in range(3):
        g.fill_beacon(b, r.t)
        assert b.state & ST_GOODBYE
    r.run(2100)
    assert g.power_off


def test_screen_off_on_wrist_down_and_wake_only_press():
    r = paired_rig(d=20.0)
    g = r.g
    g.set_motion(r.t, ACT_STILL, 0, 0.0, 5.0, False)
    for _ in range(25):
        r.t += 100
        g.set_motion(r.t, ACT_STILL, 0, 0.0, 5.0, False)
        p = g.tick(r.t)
        assert not validate(p)
    assert not g.screen_on and p.backlight == 0.0
    g.on_button(r.t)                           # wakes only: no scan
    assert g.screen_on and g.mode == M_HUNT
    g.on_gesture(r.t + 100, 1)                 # < 300 ms after wake: ignored
    assert g.mode == M_HUNT
    r.run(100)
    assert r.p.backlight == 1.0 and r.p.status[3]


def test_touch_burst_filter():
    r = paired_rig(d=20.0)
    g = r.g
    for k in range(3):
        g.on_gesture(r.t + 10 * k, 1, 10, 10)  # 3 touches within 1 s (outside iris)
    g.on_gesture(r.t + 500, 1, 120, 120)
    assert g.mode == M_HUNT                    # blocked for 2 s
    g.on_gesture(r.t + 2100, 1, 120, 120)
    assert g.mode == M_SCANNING


def test_fill_beacon_fields():
    r = hot_rig()
    g = r.g
    r.activity = ACT_WALK
    r.steps = 70000
    r.run(100)
    assert g.on_accel_tap(r.t)
    b = proto.Beacon(1)
    g.fill_beacon(b, r.t + 120)
    assert b.state & 0x0F == SC_HOT and b.bump_ago_ms == 120 and b.taps == 1
    assert b.walking and not b.sweeping and b.activity == ACT_WALK
    assert b.steps == 70000 & 0xFFFF and b.battery == 90
    assert b.rssi_last == -60 and b.rssi_filt == -60
    buf = bytearray(proto.SIZE)
    b.pack_into(buf)
    assert proto.valid(buf, proto.SIZE)


# ---- round-1 review fixes -------------------------------------------------------------

def test_peer_rate_hot_plus_saver_is_5hz_not_unreliable():
    r = hot_rig()
    r.peer_bat = 8                              # partner in HOT + saver beacons at 5 Hz
    r.run(8000, every=200)
    g = r.g
    assert g._peer_hz() == T.BEACON_HZ_SAVER and g.meter.expected_hz == T.BEACON_HZ_SAVER
    assert not g.px.unreliable and r.p.status[2] == T.LINK_Q_MAX
    assert not any(p.status[3] for p in r.params[-30:])   # no pinned StatusStrip


def test_peer_rate_switch_to_hot_has_no_false_delivery_drop():
    r = paired_rig(d=2.0)
    r.state = SC_WARM
    r.run(8000, drop=10)                        # 10 Hz, 10 % loss
    g = r.g
    assert g.meter.ratio(r.t) > 0.85
    r.state = SC_HOT                            # partner enters HOT: 20 Hz from now on
    n0 = len(r.params)
    r.run(8000, every=50, drop=10)
    assert g.meter.expected_hz == T.BEACON_HZ_HOT
    for p in r.params[n0:]:
        assert p.status[2] >= T.LINK_Q_MAX - 1, (p.t_ms, p.status)
    assert g._unrel_t is None and not g.px.unreliable


def test_tap_to_scan_on_entering_near_from_far_and_warm():
    r = paired_rig(d=40.0)
    r.activity = ACT_WALK                       # no stillness hint
    r.state = SC_FAR
    r.run(6000)
    assert r.p.screen == "FAR" and r.p.top_text is None
    r.est.fixed = 20.0
    t0 = r.t
    r.run(4000)
    near = [p for p in r.params if p.t_ms > t0 and p.screen == "NEAR"]
    assert near and near[0].top_text == "TAP TO SCAN"
    r.run(4100)
    assert r.p.screen == "NEAR" and r.p.top_text is None
    r.est.fixed = 10.0
    r.run(4000)
    assert r.p.screen == "WARM" and r.p.top_text is None     # WARM: only when still
    r.est.fixed = 20.0
    t0 = r.t
    r.run(4000)
    near = [p for p in r.params if p.t_ms > t0 and p.screen == "NEAR"]
    assert near and near[0].top_text == "TAP TO SCAN"


def test_menu_holds_event_haptics_and_plays_them_on_close():
    r = paired_rig(d=20.0)
    r.state = 3
    r.run(2000)
    g = r.g
    g.on_button(r.t, long=True)
    t0 = r.t
    r.run(5300, packets=False)                  # link lost under the menu
    assert g.menu_open and g.mode == M_LINK_LOST
    assert r.haptics(t0) == []
    g.on_button(r.t, long=True)                 # select RESUME
    r.run(100)
    assert not g.menu_open and r.p.haptic == "LOST"
    r.run(1000, packets=False)
    assert r.haptics(t0) == ["LOST"]
    # a lower-priority event raised later does not replace a held higher one
    g.on_button(r.t, long=True)
    g._emit(r.t, "FOUND")
    g._emit(r.t, "CLOSER")
    g._menu_close()
    r.run(100, packets=False)
    assert r.p.haptic == "FOUND"


def test_hot_button_double_press_starts_scan_single_press_is_fallback():
    r = hot_rig()
    g = r.g
    g.on_button(r.t)
    r.run(300)
    assert g.mode == M_HUNT and g.state_byte & ST_PRESS
    g.on_button(r.t)                            # second press within 1 s
    r.run(100)
    assert g.mode == M_SCANNING and not g.state_byte & ST_PRESS
    # the partner already pressed: the second press stays a fallback press
    r2 = hot_rig()
    r2.g.on_button(r2.t)
    r2.state = SC_HOT | ST_PRESS
    r2.run(100)
    assert r2.g.mode == M_FOUND


def _lose(r, walk_trend):
    """Hunt in NEAR, optionally walking closer with a warmer trend, then lose the link."""
    r.state = 3
    r.activity = ACT_WALK
    if walk_trend:
        r.est.trend = 1
        r.est.trend_conf = 1.0
        for _ in range(150):
            r.rssi += 0.2
            r.run(100)
        assert r.g.px.trend == 1, r.g.px.trend
    r.run(1000)


def test_lost_trend_keep_on_only_for_a_recent_warmer_trend():
    r = paired_rig(d=20.0)
    _lose(r, True)
    r.run(26000, packets=False)
    assert r.p.screen == "LINK_LOST" and r.p.banner[0].endswith("KEEP ON")
    assert r.g.lost_trend == 1
    # a warmer trend long before the loss does not count
    r2 = paired_rig(d=20.0)
    _lose(r2, True)
    r2.est.trend = 0
    r2.run(40000)
    r2.run(26000, packets=False)
    assert r2.p.banner[0].endswith("GO BACK") and r2.g.lost_trend == 0
    # a new round forgets the trend
    r3 = paired_rig(d=20.0)
    _lose(r3, True)
    r3.g._new_round(r3.t)
    assert r3.g._last_trend == 0 and r3.g.lost_trend == 0


def _hourly(g, t, n, face_up=True):
    out = []
    for _ in range(n):
        t += 3600000
        g.set_motion(t, ACT_STILL, 0, 0.0, 5.0, face_up)
        p = g.tick(t)
        assert not validate(p), (t, validate(p))
        out.append(p)
    return t, out


def test_long_idle_timestamps_never_wrap():
    r = paired_rig(d=20.0)
    r.state = 3
    r.run(2000)
    g = r.g
    g.on_wake(r.t)
    g._toast_set(r.t, "OLD TOAST")
    g._hint_set(r.t, "OLD HINT")
    t, ps = _hourly(g, r.t, 192, face_up=False)        # 8 days, link lost, wrist down
    for p in ps[1:]:
        assert p.screen == "LINK_LOST" and p.banner[0] == "LOST 10M+ GO BACK", p.banner
        assert p.backlight == 0.0
    g.on_wake(t)
    t, ps = _hourly(g, t, 192)                          # 8 more days face up, no input
    for p in ps[1:]:                                    # (first tick: face-up wake boost)
        assert p.banner[0] == "LOST 10M+ GO BACK" and p.top_text is None
        assert p.backlight == T.BACKLIGHT_LOW and not p.status[3]
    g.on_gesture(t + 50, 3)                             # long press still opens the menu
    assert g.menu_open
    # FOUND result stays 'result' and a tap still starts a new round
    r2 = hot_rig()
    r2.g.on_button(r2.t)
    r2.state = SC_HOT | ST_PRESS
    r2.run(200)
    assert r2.g.mode == M_FOUND
    t, ps = _hourly(r2.g, r2.t, 192)
    assert all(p.sub == "result" and p.word == "TAP=AGAIN" for p in ps)
    assert not r2.g._pressed(t) and r2.g.bump_t is None
    r2.g.on_gesture(t + 50, 1)
    r2.g.tick(t + 100)
    assert r2.g.mode == M_PAIRING


def test_peer_view_expire_caps_age_and_drops_old_tap():
    pv = PeerView()
    b = proto.Beacon(1)
    b.bump_ago_ms = 100
    for k in range(3):
        pv.on_beacon(1000 + 100 * k, b)
    assert pv.live3(1300) and pv.tap_t is not None
    pv.expire(80000, 3600000)
    assert pv.tap_t is None and pv.live3(1300)
    pv.expire(1000 + 2 * 3600000, 3600000)
    assert pv.age(1000 + 2 * 3600000) == 3600000 and pv.n_rx == 0
    assert not pv.live3(1000 + 2 * 3600000)


# ---- round-2 review: arrow vs scan, turn heartbeat, ready timeout -------------
def _arrow_rig(theta=10.0, mode=A.MODE_GUIDED):
    r = paired_rig(d=20.0)
    r.state = SC_FAR + 1
    r.run(1000)
    g = r.g
    g.arrow = A.make(theta, 20.0, r.t, mode)
    return r


def test_turn_and_face_phases_carry_no_zone_heartbeat():
    for mode in (A.MODE_GUIDED, A.MODE_STATIC):
        r = _arrow_rig(120.0, mode)
        r.run(6000)
        subs = [p for p in r.params[-60:] if p.sub == "turn"]
        assert subs, mode
        assert all(p.heartbeat is None and p.heartbeat_every == 1 for p in subs), mode
        if mode == A.MODE_GUIDED:
            assert any(p.word == "TURN RIGHT" for p in subs)
        r.run(4000)
        assert r.g.arrow.phase == A.PH_WALK and r.p.heartbeat is not None


def test_accidental_tap_keeps_the_arrow_and_sweep_spends_it():
    r = _arrow_rig(10.0)
    g = r.g
    r.tilt = 60.0                     # not flat: ready holds
    r.run(3000)
    a = g.arrow
    assert a is not None and a.phase == A.PH_WALK and r.p.glyph == "arrow"
    walked = a.steps_walked
    g.on_gesture(r.t, 1, 120, 120)
    r.activity = ACT_WALK
    for _ in range(5):
        r.steps += 2
        r.run(200)
    assert g.mode == M_SCANNING and g.arrow is None and r.p.glyph != "arrow"
    g.on_gesture(r.t, 1, 120, 120)    # second tap cancels: the arrow is back
    r.run(100)
    assert g.mode == M_HUNT and g.arrow is a and r.p.glyph == "arrow"
    assert a.steps_walked > walked    # it kept aging while hidden
    # a scan that reaches the sweep spends the old arrow
    r.activity = ACT_STILL
    r.tilt = 5.0
    g.on_gesture(r.t, 1, 120, 120)
    r.run(4500)
    assert g.mode == M_SCANNING and r.p.sub == "sweep"
    g.on_gesture(r.t, 1, 120, 120)
    r.run(200)
    assert g.mode == M_HUNT and g.arrow is None


def test_scan_ready_times_out_wrist_down_or_never_flat():
    r = _arrow_rig(10.0)
    g = r.g
    r.run(3000)
    a = g.arrow
    t0 = r.t
    g.on_gesture(r.t, 1, 120, 120)
    r.tilt = 90.0
    r.face_up = False
    r.run(1500)
    assert g.mode == M_SCANNING and g.screen_on
    r.run(1000)
    assert g.mode == M_HUNT and g.arrow is a and g.beacon_hz == T.BEACON_HZ_NORMAL
    assert not any(p.haptic == "NOPE" for p in r.params if p.t_ms > t0)
    r.run(3000)
    assert not g.screen_on and r.p.backlight == 0.0
    # face-up but tilted: the countdown holds, then cancels after 15 s
    r.face_up = True
    g.on_wake(r.t)
    r.tilt = 40.0
    r.run(500)
    g.on_gesture(r.t, 1, 120, 120)
    r.run(14000)
    assert g.mode == M_SCANNING and g.screen_on and r.p.backlight > 0.0
    r.run(1500)
    assert g.mode == M_HUNT and g.arrow is a and r.p.glyph == "arrow"
    assert g.beacon_hz != T.BEACON_HZ_SCAN


def test_set_place_switches_path_loss_exponent():
    from finder.game import Game
    from finder import tuning as T
    g = Game(b"\x01\x02\x03\x04\x05\x06")
    assert g.est.pl.n == T.PATH_LOSS_N and not g.indoor
    g.set_place(True)
    assert g.est.pl.n == T.PATH_LOSS_N_INDOOR and g.indoor
    g.set_place(False)
    assert g.est.pl.n == T.PATH_LOSS_N
