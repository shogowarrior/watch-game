"""finder.game / finder.pairing / finder.session unit tests (no radio sim).

A ``Rig`` drives one ``Game`` with a fake partner (beacons built by hand) and
a fake estimator whose distance the test sets; ``Two`` links two real games.
Every RenderParams produced is checked with ``validate``.
"""

from finder import proto
from finder import tuning as T
from finder.game import (Game, M_HUNT, M_SCANNING, M_FOUND, M_LINK_LOST, M_SEARCHING, M_PAIRING,
                         BUZZ_OFF)
from finder.pairing import Calibrator, Pairing, rune_ids, fnv1a32, UNSTABLE_SHOW_MS
from finder.session import (PeerView, LiveMirror, fmt_mss, fmt_found, screen_code, PEER_FRESH_MS,
                            SC_PAIRING, SC_FAR,
                            SC_NEAR, SC_WARM, SC_HOT, SC_FOUND, SC_SCANNING, SC_PAIRED, SC_BYE,
                            SC_MASK, ST_PRESS, ST_GOODBYE, ST_CONFIRMED, ST_TAP_HOT)
from finder.gestures import GestureRecognizer, TAP as G_TAP
from finder.render_params import validate
from finder.estimators import NAMES, make
from finder.estimators.base import RangeEstimator, MotionInfo, ACT_STILL, ACT_WALK
from finder import arrow as A
from finder.compat import ticks_add, ticks_diff

MAC_A = b"\x24\x0a\xc4\x10\x00\x0a"
MAC_B = b"\x24\x0a\xc4\x10\x00\x0b"
MAC_C = b"\x24\x0a\xc4\x10\x00\x0c"


class FakeEst(RangeEstimator):
    """Estimator stand-in: ``fixed`` is the distance reported after any packet."""

    name = "fake"

    def __init__(self, d=40.0):
        self.fixed = d
        self.cal = None
        self.n = 0
        RangeEstimator.__init__(self)

    def reset(self):
        RangeEstimator.reset(self)
        self.rssi_var = 4.0

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
        self.tap_at = None            # the partner's last tap, on our clock
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
        t = self.t if t is None else t
        b = self.b
        b.state = self.state
        b.flags = self.flags
        ta = self.tap_at
        b.bump_ago_ms = proto.BUMP_NONE if ta is None or t < ta else t - ta - 2   # 2 ms air
        b.battery = self.peer_bat
        b.activity = self.peer_act
        b.rssi_last = -61
        b.rssi_filt = -60
        self.g.on_packet(t, self.peer, self.rssi if rssi is None else rssi, b)

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
            tilt = self.tilt
            if not self.face_up and tilt < 30.0:
                tilt = 90.0           # lowered: the wrist hangs (unless a test tilts it)
            self.g.set_motion(self.t, self.activity, self.steps, 0.0, tilt, self.face_up)
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

    def peer_tap(self, at, n=1):
        """The partner's ``n``-th accepted tap, made at ``at`` on our clock (its
        beacons carry the tap's age from then on)."""
        self.tap_at = at
        self.flags = n << proto.F_TAPS_SHIFT


def paired_rig(d=40.0, **kw):
    """A Rig taken through pairing (button confirm) and the split into the hunt."""
    r = Rig(d=d, **kw)
    r.rssi = -50
    r.run(600)
    assert r.g.pair.sub == "seen", r.g.pair.sub
    r.g.on_button(r.t)
    r.state = SC_PAIRED | ST_CONFIRMED          # the partner confirms second: calibrate, split
    r.rssi = -45
    r.run(4500)
    assert r.g.pair.sub == "split", r.g.pair.sub
    r.rssi = -60
    r.run(31500)
    assert r.g.mode == M_HUNT, r.g.mode
    return r


def warm_rig(ms=2000, **kw):
    r = paired_rig(d=10.0, **kw)
    r.state = SC_WARM
    r.run(ms)
    return r


def hot_rig(d=2.0):
    r = paired_rig(d=d)
    r.state = SC_HOT
    r.run(2000)
    assert r.p.screen == "HOT"
    return r


def test_bump_armed_only_where_a_spike_can_count():
    """The IMU samples fast only in HOT, FOUND and PAIRING seen / confirmed (§6, §8)."""
    r = Rig()
    assert not r.g.bump_armed()                 # looking
    r.rssi = -50
    r.run(600)
    assert r.g.pair.sub == "seen" and r.g.bump_armed()
    r.g.on_button(r.t)
    r.run(100)
    assert r.g.pair.sub == "confirmed" and r.g.bump_armed()
    r.state = SC_PAIRED | ST_CONFIRMED          # the partner confirms second: calibrate
    r.rssi = -45
    r.run(200)
    assert r.g.pair.sub == "calibrate" and not r.g.bump_armed()
    r.run(4300)
    assert r.g.pair.sub == "split" and not r.g.bump_armed()
    assert not warm_rig().g.bump_armed()
    h = hot_rig()
    assert h.g.bump_armed()
    h.g.on_button(h.t)                          # second press in HOT: a scan, nothing to bump
    h.g.on_button(h.t + 300)
    assert h.g.mode == M_SCANNING and not h.g.bump_armed()
    h = hot_rig()
    _found_by_press(h)
    assert h.g.bump_armed()                     # FOUND: knocks that go on are seen (§8)
    s = Rig()                                   # split with no link: SEARCHING
    s.rssi = -50
    s.run(600)
    s.g.on_button(s.t)
    s.state = SC_PAIRED | ST_CONFIRMED
    s.rssi = -45
    s.run(4500)
    s.run(31500, packets=False)
    assert s.g.mode == M_SEARCHING and not s.g.bump_armed()


def _found_by_press(r):
    """Both fallback presses in HOT: FOUND."""
    r.g.on_button(r.t)
    r.state = SC_HOT | ST_PRESS
    r.run(200)
    assert r.g.mode == M_FOUND


class Two:
    """Two Games linked by a perfect radio at -46 dBm: beacons both ways every 50 ms."""

    def __init__(self):
        self.a = Game(MAC_A, est=FakeEst(1.0))
        self.b = Game(MAC_B, est=FakeEst(1.0))
        self.t = 0
        self.tx = proto.Beacon(1)
        self.rx = proto.Beacon(1)
        self.buf = bytearray(proto.SIZE)

    def _send(self, src, dst, mac):
        src.fill_beacon(self.tx, self.t)
        self.tx.pack_into(self.buf)
        self.rx.unpack_from(self.buf)
        dst.on_packet(self.t, mac, -46, self.rx)

    def run(self, ms, link=True):
        end = self.t + ms
        while self.t < end:
            self.t += 50
            if link:
                self._send(self.a, self.b, MAC_A)
                self._send(self.b, self.a, MAC_B)
            if self.t % 100 == 0:
                for g in (self.a, self.b):
                    g.set_motion(self.t, ACT_STILL, 0, 0.0, 5.0, True)
                    p = g.tick(self.t)
                    assert not validate(p), validate(p)


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
    assert c.digit == 3
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
    # the countdown digits follow the window length
    c = Calibrator(window_ms=5000)
    c.reset(0)
    assert c.digit == 5
    c.fill_ms = 4200
    assert c.digit == 1


def test_pairing_ignores_weak_or_non_pairing_senders():
    p = Pairing(MAC_A)
    for i in range(10):
        p.on_candidate(100 * i, MAC_B, -75)                  # too weak
        p.on_candidate(100 * i, MAC_B, -50, peer_pairing=False)
    assert p.sub == "looking"
    for i in range(3):
        assert p.sub == "looking"                           # the 3rd packet decides
        p.on_candidate(1000 + 100 * i, MAC_B, -50)
    assert p.sub == "seen" and p.peer_mac == MAC_B and p.runes == rune_ids(MAC_A, MAC_B)
    assert p.update(1300) == "DOUBLE"
    assert p.update(7000) is None and p.sub == "looking"   # partner silent 5 s


def test_pairing_tracks_several_candidates_and_takes_the_strongest():
    # two pairs starting side by side: B and C beacon interleaved at 10 Hz each
    p = Pairing(MAC_A)
    t = 0
    while p.sub == "looking":
        assert t < 1000, t
        p.on_candidate(t, MAC_B, -48)
        p.on_candidate(t + 50, MAC_C, -55)
        p.update(t + 50)
        t += 100
    assert p.peer_mac == MAC_B and p.runes == rune_ids(MAC_A, MAC_B)
    # a stronger candidate blocks a weaker one until it has been silent for 2 s
    p = Pairing(MAC_A)
    p.on_candidate(0, MAC_C, -40)
    p.on_candidate(100, MAC_C, -40)
    t = 100
    while p.sub == "looking":
        t += 100
        assert t <= 2500, t
        p.update(t)
        p.on_candidate(t, MAC_B, -50)
    assert p.peer_mac == MAC_B and t > 100 + T.RELINK_WINDOW_MS, t
    # more candidates than slots: the weakest are dropped, the strongest still wins
    p = Pairing(MAC_A)
    macs = [bytes((0, 0, 0, 0, 2, i)) for i in range(6)]
    t = 0
    while p.sub == "looking":
        assert t < 1500, t
        for i in range(6):
            p.on_candidate(t + 10 * i, macs[i], -58 + i)    # macs[5] is the strongest
        t += 100
    assert p.peer_mac == macs[5]
    # a newcomer that takes over a live slot starts with no packets of its own
    weak = [bytes((0, 0, 0, 0, 3, i)) for i in range(4)]
    p = Pairing(MAC_A)
    for t in (0, 100):
        for i in range(4):
            p.on_candidate(t + i, weak[i], -58)
    assert not p.on_candidate(300, bytes((0, 0, 0, 0, 4, 0)), -50) and p.sub == "looking"
    # a newcomer no stronger than every slot takes none: the slots keep their packets
    p = Pairing(MAC_A)
    for t in (0, 100):
        for i in range(4):
            p.on_candidate(t + i, weak[i], -55)
    assert not p.on_candidate(150, bytes((0, 0, 0, 0, 5, 0)), -60)
    assert p.on_candidate(200, weak[0], -55) and p.sub == "seen" and p.peer_mac == weak[0]


def test_session_helpers():
    assert fmt_mss(12000) == "0:12" and fmt_mss(87000) == "1:27"
    assert fmt_mss(599999) == "9:59" and fmt_mss(600000) == "10M+"
    assert screen_code("PAIRING") == SC_PAIRING and screen_code("HOT") == SC_HOT
    assert screen_code("FOUND") == SC_FOUND and screen_code("SCANNING") == SC_SCANNING
    assert screen_code("NEAR") == SC_NEAR and screen_code("WARM") == SC_WARM
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
    # the live mirror: 0 at the running min, 1 at the max, in between after a dip
    m = LiveMirror()
    m.reset(0)
    m.add(0, -70)
    assert m.value == 0.0
    for i in range(1, 40):
        m.add(50 * i, -70 + i)
    assert m.value == 1.0
    m.add(2000, -75)
    assert 0.0 < m.value < 1.0, m.value


def test_partner_restart_resets_its_step_counter_not_a_31khz_cadence():
    pv = PeerView()
    b = proto.Beacon(1)
    b.steps = 3000
    pv.on_beacon(0, b)
    b.steps = 3010
    pv.on_beacon(2000, b)
    assert pv.motion.steps == 10 and pv.motion.step_rate_hz == 5.0
    b.steps = 0                       # the partner restarted
    pv.on_beacon(2100, b)
    assert pv.motion.steps == 10 and pv.motion.step_rate_hz == 0.0
    b.steps = 4
    pv.on_beacon(4100, b)
    assert pv.motion.steps == 14 and pv.motion.step_rate_hz == 2.0


def test_partner_tap_with_jittering_age_stays_one_tap():
    pv = PeerView()
    b = proto.Beacon(1)
    b.set_flags(taps=3)
    for k in range(10):
        t = 5000 + 100 * k
        b.bump_ago_ms = t - 4000 + (20 if k % 2 else -20) - 2    # a tap at 4000 +-20 ms
        pv.on_beacon(t, b)
        assert pv.tap_t == 4020, (k, pv.tap_t)


# ---- pairing flow via Game --------------------------------------------------------

def test_pairing_flow_button_confirm_calibrate_split():
    r = Rig()
    r.rssi = -50
    r.run(600)
    g = r.g
    assert r.p.screen == "PAIRING" and r.p.sub == "seen" and r.p.glyph == "runes"
    assert "DOUBLE" in r.haptics()
    assert g.pair.runes == rune_ids(MAC_A, MAC_B) and r.p.runes == g.pair.runes
    r.g.on_button(r.t)
    r.run(300)
    assert r.p.sub == "confirmed" and r.p.word == "WAITING"
    assert g.state_byte & ST_CONFIRMED
    r.state = SC_PAIRED | ST_CONFIRMED          # the partner confirms second: calibrate
    r.rssi = -44
    r.run(200)
    assert r.p.sub == "calibrate" and r.p.glyph == "countdown" and r.p.countdown == 3
    assert g.state_byte & SC_MASK == SC_PAIRED  # paired: never a pairing candidate
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
    assert r.p.runes is None


def test_split_length_is_settable():
    pr = Pairing(MAC_A)
    pr.split_s = 5                              # the web sim's demo split
    pr.start_split(1000)
    assert pr.countdown == 5
    t = 1000
    while pr.sub == "split":
        t += 100
        pr.update(t)
        assert t < 8000
    assert pr.sub == "done" and t == 1000 + 5000 + T.PAIR_GO_MS


def _to_split(r):
    """Rig through pairing and calibration into the split countdown (30 s left)."""
    r.rssi = -50
    r.run(600)
    r.g.on_button(r.t)
    r.state = SC_PAIRED | ST_CONFIRMED
    r.rssi = -44
    r.run(200)
    while r.p.sub != "split":
        r.run(100)
        assert r.t < 10000
    assert r.p.countdown == 30


def _tap(r):
    r.g.on_gesture(r.t, G_TAP, 120, 120, r.t - 60)


def test_ready_on_both_watches_skips_the_split_to_three():
    """§6 split: a tap says READY (TICK, chip WAITING FOR FRIEND); the
    partner's READY arrives in its beacons (DOUBLE, chip FRIEND READY); once
    both are ready the countdown jumps to 3 and ends with the usual 3/2/1
    ticks and GO."""
    r = Rig()
    _to_split(r)
    r.run(2000)
    assert r.p.top_text == "NO PEEKING" and r.p.word == "SPLIT UP"
    assert not r.g.pair.ready and not r.b.ready
    r.run(T.PAIR_READY_HINT_MS - 2000)
    assert r.p.top_text == "TAP WHEN READY" and r.p.word == "SPLIT UP"
    t0 = r.t
    _tap(r)
    r.run(100)
    assert r.p.top_text == "WAITING FOR FRIEND" and r.p.word == "READY"
    assert r.haptics(t0) == ["TICK"], r.haptics(t0)
    b = proto.Beacon(1)
    r.g.fill_beacon(b, r.t)
    assert b.ready                               # the partner learns it from the beacons
    _tap(r)                                      # a second tap changes nothing
    r.run(3000)
    assert r.p.countdown > 20 and r.p.word == "READY"
    t1 = r.t
    r.flags = proto.F_READY                      # the partner taps READY
    r.run(100)
    assert r.p.top_text == "BOTH READY" and r.p.countdown == 3
    assert r.haptics(t1)[0] == "DOUBLE", r.haptics(t1)
    n = 0
    while r.p.word != "GO":
        r.run(100)
        n += 1
        assert n <= 31
    assert r.p.countdown == 0 and r.haptics(t1) == ["DOUBLE", "TICK", "TICK", "CLOSER"], \
        r.haptics(t1)
    assert 2900 <= r.t - t1 <= 3100, r.t - t1
    r.run(T.PAIR_GO_MS)
    assert r.g.mode == M_HUNT


def test_ready_on_one_watch_runs_the_full_split():
    """The partner's READY alone (or this watch's alone) never shortens it;
    a READY flag from a watch that is not in its split (it does not show
    PAIRED) is ignored."""
    r = Rig()
    _to_split(r)
    t0 = r.t
    r.flags = proto.F_READY
    r.state = SC_HOT                             # not a split: no READY
    r.run(300)
    assert not r.g.pair.peer_ready and r.p.top_text == "NO PEEKING"
    r.state = SC_PAIRED
    r.run(300)
    assert r.g.pair.peer_ready and r.p.top_text == "FRIEND READY" and r.p.word == "SPLIT UP"
    assert "DOUBLE" in r.haptics(t0)
    while r.p.word != "GO":
        r.run(100)
        assert r.t - t0 < 31000
    assert 29800 <= r.t - t0 <= 30200, r.t - t0
    r2 = Rig()
    _to_split(r2)
    t0 = r2.t
    _tap(r2)
    while r2.p.word != "GO":
        r2.run(100)
        assert r2.t - t0 < 31000
    assert 29800 <= r2.t - t0 <= 30200 and r2.p.top_text == "WAITING FOR FRIEND"


def test_ready_after_go_or_outside_the_split_does_nothing():
    pr = Pairing(MAC_A)
    pr.set_ready(0)
    pr.set_peer_ready(0, True)
    assert not pr.ready and not pr.peer_ready    # looking: no split
    pr.start_split(1000)
    t = 1000
    while pr.countdown != 0:
        t += 100
        pr.update(t)
    pr.set_ready(t)
    assert not pr.ready                          # GO: too late
    pr.start_split(t)                            # a new round forgets READY
    pr.set_ready(t)
    pr.set_peer_ready(t, True)
    assert pr.ready and pr.peer_ready
    pr.start_split(t + 100)
    assert not pr.ready and not pr.peer_ready


def test_split_without_link_goes_to_searching_then_zone_on_three_packets():
    r = Rig()
    r.rssi = -50
    r.run(600)
    r.g.on_button(r.t)
    r.state = SC_PAIRED | ST_CONFIRMED
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
    assert r.p.top_text == "TAP TO SCAN"


def test_bump_confirms_pairing_on_both_sides_at_once():
    r = Rig()
    r.rssi = -50
    r.run(600)
    assert r.g.pair.sub == "seen"
    assert not r.g.on_accel_tap(r.t)           # the DOUBLE (partner seen) still blanks
    r.run(500)
    assert r.g.on_accel_tap(r.t)
    r.peer_tap(r.t)
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
    assert r.p.word == "BUMP!" and r.p.top_text == "BUMP WRISTS"
    assert r.p.glyph == "bump" and r.p.bump_icons == 0
    assert r.haptics(t0).count("DOUBLE") == 1


def _bump_ready(r):
    """Band <3 held until bump-ready (the friend's state is the test's)."""
    r.est.fixed = 1.5
    n = 0
    while not r.g.bump_ready:
        r.run(100)
        n += 1
        assert n < 40


def test_bump_view_waits_for_a_ready_friend():
    r = hot_rig(4.0)
    r.state = SC_WARM                           # the friend's screen cannot count a bump
    t0 = r.t
    _bump_ready(r)
    r.run(1000)
    p = r.p
    assert p.glyph == "bump" and p.bump_icons == 4 and p.word is None
    assert p.dist_band == "<3" and p.top_text == "FRIEND NOT READY"
    assert "DOUBLE" not in r.haptics(t0)
    r.state = SC_HOT                            # it reaches HOT: BUMP! and the DOUBLE now
    t1 = r.t
    r.run(300)
    p = r.p
    assert p.word == "BUMP!" and p.top_text == "BUMP WRISTS" and p.bump_icons == 0
    assert r.haptics(t1).count("DOUBLE") == 1
    r.state = SC_WARM                           # gone and back in one stretch: no 2nd DOUBLE
    r.run(500)
    r.state = SC_HOT
    r.run(500)
    assert r.haptics(t1).count("DOUBLE") == 1 and r.p.word == "BUMP!"


def test_friend_not_ready_when_its_beacons_go_stale():
    r = hot_rig(4.0)
    _bump_ready(r)
    r.run(300)
    assert r.p.word == "BUMP!"
    r.run(PEER_FRESH_MS + 100, packets=False)
    p = r.p
    assert r.g.mode == M_HUNT and p.bump_icons & 4 and p.word != "BUMP!"
    assert p.top_text == "FRIEND NOT READY"


def test_bump_icons_light_for_a_second():
    r = hot_rig(4.0)
    _bump_ready(r)
    r.run(500)
    g = r.g
    assert g.on_accel_tap(r.t)
    r.run(100)
    assert r.p.bump_icons == 1
    r.run(T.BUMP_LIT_MS)
    assert r.p.bump_icons == 0
    r.state = SC_HOT | ST_TAP_HOT               # the friend's spike, made in its HOT
    r.peer_tap(r.t, 1)
    r.run(200)
    assert r.p.bump_icons == 2
    r.run(T.BUMP_LIT_MS)
    assert r.p.bump_icons == 0
    r.state = SC_HOT                            # a friend spike made outside HOT never lights
    r.peer_tap(r.t, 2)
    r.run(200)
    assert r.p.bump_icons == 0


def test_one_sided_knock_says_who_felt_it():
    r = hot_rig(4.0)
    _bump_ready(r)
    r.run(500)
    g = r.g
    t0 = r.t
    assert g.on_accel_tap(t0)                   # only this watch felt it
    r.run(400)
    assert r.p.top_text == "BUMP WRISTS"        # judged KNOCK_WAIT_MS after the spike
    r.run(200)
    assert r.p.top_text == "ONLY YOU FELT IT" and r.p.word == "BUMP!"
    assert g.mode == M_HUNT and "DOUBLE" not in r.haptics(t0 + 100)
    r.run(T.TOAST_MS)
    assert r.p.top_text == "BUMP WRISTS"
    r.state = SC_HOT | ST_TAP_HOT               # only the friend felt one
    r.peer_tap(r.t, 1)
    r.run(700)
    assert r.p.top_text == "FRIEND FELT IT" and g.mode == M_HUNT
    r.run(T.TOAST_MS)
    # a matched knock (60 ms apart) gives FOUND and no felt-it message
    assert g.on_accel_tap(r.t)
    r.peer_tap(r.t + 60, 2)
    r.run(200)
    assert g.mode == M_FOUND
    r.run(1000)
    assert all(p.top_text not in ("ONLY YOU FELT IT", "FRIEND FELT IT")
               for p in r.params[-10:])


def test_only_you_felt_it_needs_a_ready_friend():
    r = hot_rig(4.0)
    r.state = SC_WARM
    _bump_ready(r)
    r.run(500)
    assert r.g.on_accel_tap(r.t)
    ps = r.run(T.BUMP_LIT_MS - 100)
    assert all(p.bump_icons == 5 for p in ps)
    ps += r.run(1000)
    assert all(p.top_text == "FRIEND NOT READY" for p in ps)


def test_spikes_from_before_hot_are_never_judged():
    r = paired_rig(d=8.0)
    r.state = SC_HOT | ST_TAP_HOT
    r.run(1000)
    assert r.p.screen == "WARM"
    assert r.g.on_accel_tap(r.t)                # made in WARM
    r.peer_tap(r.t - 900, 1)                    # and an old friend spike
    r.est.fixed = 2.0
    ps = r.run(4000)
    assert r.p.screen == "HOT"
    assert all(p.top_text not in ("ONLY YOU FELT IT", "FRIEND FELT IT") for p in ps)


def test_walk_arrow_hides_under_the_bump_view():
    r = hot_rig(4.0)
    g = r.g
    g.arrow = A.make(10.0, 20.0, r.t)
    r.run(5000)
    assert g.arrow.phase == A.PH_WALK and r.p.glyph == "arrow"
    _bump_ready(r)
    r.run(100)
    p = r.p
    assert p.glyph == "bump" and p.arrow_deg is None and p.sub is None and g.arrow is not None
    r.est.fixed = 12.0                          # bump-ready ends: the arrow is back
    r.run(300)
    assert r.p.screen == "HOT" and not g.bump_ready
    assert r.p.glyph == "arrow" and r.p.sub == "walk"


def test_pairing_one_sided_bump_toasts_and_match_calibrates():
    r = Rig()
    r.rssi = -50
    r.run(1500)                                 # seen, past the DOUBLE's blanking
    g = r.g
    assert g.pair.sub == "seen"
    assert g.on_accel_tap(r.t)                  # only this watch
    r.run(600)
    assert r.p.banner == ("ONLY YOU FELT IT", "info", False) and g.pair.sub == "seen"
    assert r.p.word == "BUMP = YES" and r.p.top_text == "SAME RUNES?"
    r.run(T.TOAST_MS)
    r.peer_tap(r.t, 1)                          # only the friend (its watch in PAIRING)
    r.run(700)
    assert r.p.banner == ("FRIEND FELT IT", "info", False)
    assert g.on_accel_tap(r.t)                  # a matched bump: both confirmed, calibrate
    r.peer_tap(r.t + 50, 2)
    r.run(200)
    assert g.pair.sub == "calibrate" and r.p.banner is None


def test_two_watches_one_sided_knock_feedback():
    w = Two()
    w.run(1000)
    w.a.on_button(w.t)
    w.b.on_button(w.t)
    w.run(5000)
    w.run(35000)                                # split, then HOT at 1 m
    while not (w.a.bump_ready and w.b.bump_ready):
        w.run(100)
    w.run(500)
    assert w.a.params.word == "BUMP!" and w.b.params.word == "BUMP!"
    assert w.a.on_accel_tap(w.t)                # A felt it, B did not
    w.run(200)                                  # B hears it (and A's ST_TAP_HOT) a beacon later
    assert w.a.params.bump_icons == 1 and w.b.params.bump_icons == 2
    w.run(500)
    assert w.a.params.top_text == "ONLY YOU FELT IT"
    assert w.b.params.top_text == "FRIEND FELT IT"
    w.run(T.TOAST_MS)
    assert w.a.on_accel_tap(w.t)                # a real knock: both spike 60 ms apart
    w.t += 60
    assert w.b.on_accel_tap(w.t)
    w.t -= 60
    w.run(300)
    assert w.a.mode == M_FOUND and w.b.mode == M_FOUND


def test_buzz_off_does_not_blank_the_bump_tap():
    r = hot_rig(4.0)
    g = r.g
    g.buzz = BUZZ_OFF
    r.est.fixed = 1.5
    n = 0
    while not g.bump_ready:                     # the (silent) DOUBLE is raised this tick
        r.run(100)
        n += 1
        assert n < 40
    assert g.on_accel_tap(r.t + 50)


# ---- FOUND --------------------------------------------------------------------------

def test_found_needs_both_taps_within_400ms_in_hot():
    r = hot_rig()
    r.est.fixed = 1.5
    r.run(2000)
    g = r.g
    r.state = SC_HOT | ST_TAP_HOT
    assert g.on_accel_tap(r.t)
    # partner tapped 500 ms before us: no match
    r.peer_tap(r.t - 500)
    r.run(600)
    assert g.mode == M_HUNT
    # a fresh pair of taps 150 ms apart -> FOUND
    r.run(1000)
    assert g.on_accel_tap(r.t)
    r.peer_tap(r.t + 150, 2)
    r.run(200)
    assert g.mode == M_FOUND and r.p.screen == "FOUND" and r.p.sub == "celebrate"
    assert r.p.haptic == "FOUND" and r.p.ramp == "gold" and r.p.glyph == "check"
    assert r.p.top_text.startswith("TIME ") and r.p.word == "FOUND"
    r.run(2100)
    assert r.p.sub == "result" and r.p.word.startswith("FOUND ")
    assert r.p.top_text == "BUTTON: PLAY AGAIN"


def test_rssi_alone_never_found_and_partner_must_be_hot():
    r = hot_rig()
    r.est.fixed = 0.5
    r.run(20000)
    assert r.g.mode == M_HUNT
    # our tap + theirs, but the partner reports WARM
    r.state = SC_WARM | ST_TAP_HOT
    assert r.g.on_accel_tap(r.t)
    r.peer_tap(r.t + 80)
    r.run(500)
    assert r.g.mode == M_HUNT
    # our tap made in WARM does not count once we reach HOT, however fresh
    r2 = paired_rig(d=8.0)
    r2.state = SC_HOT | ST_TAP_HOT
    r2.run(1000)
    assert r2.p.screen == "WARM"
    t_tap = r2.t
    assert r2.g.on_accel_tap(t_tap)
    r2.peer_tap(t_tap + 80)
    r2.est.fixed = 1.0
    while r2.g.px.zone != 3:                   # HOT
        r2.run(100)
        assert r2.t - t_tap < 2000, r2.t - t_tap   # still a fresh, matching pair of taps
    r2.run(300)
    assert r2.g.mode == M_HUNT


def test_bump_found_needs_the_partner_tap_made_in_hot():
    for bit, mode in ((0, M_HUNT), (ST_TAP_HOT, M_FOUND)):
        r = hot_rig(1.5)
        r.state = SC_WARM
        r.run(1000)
        assert r.g.on_accel_tap(r.t)            # our tap, in HOT
        r.peer_tap(r.t)                         # theirs at the same moment, still in WARM
        r.state = SC_WARM | bit
        r.run(600)
        assert r.g.mode == M_HUNT
        r.state = SC_HOT | bit                  # its zone commits HOT 600 ms later
        r.run(300)
        assert r.g.mode == mode, (bit, r.g.mode)


def test_stale_taps_and_partner_flags_never_found():
    # our tap older than BUMP_FRESH_MS when the partner's matching tap arrives
    r = hot_rig()
    r.state = SC_HOT | ST_TAP_HOT
    t_tap = r.t
    assert r.g.on_accel_tap(t_tap)
    r.run(2100)
    r.peer_tap(t_tap + 50)                      # theirs 50 ms after ours, heard only now
    r.run(300)
    assert r.g.peer.tap_t == t_tap + 50 and r.g.mode == M_HUNT
    # a partner press / FOUND last heard more than 1.5 s ago
    for state in (SC_HOT | ST_PRESS, SC_FOUND):
        r = hot_rig()
        r.state = state
        r.run(100)
        r.run(1600, packets=False)
        r.g.on_button(r.t)
        r.run(300, packets=False)
        assert r.g.mode == M_HUNT, state


def test_partner_already_found_follow_by_tap_or_press_only_while_fresh():
    for how in ("tap", "press", "stale tap"):
        r = hot_rig()
        g = r.g
        r.state = SC_FOUND
        r.run(200)
        assert g.mode == M_HUNT                 # their FOUND alone is not ours
        if how == "press":
            g.on_button(r.t)
        else:
            assert g.on_accel_tap(r.t)
        if how == "stale tap":
            r.state = SC_HOT
            r.run(2500)
            r.state = SC_FOUND
        r.run(300)
        assert g.mode == (M_HUNT if how == "stale tap" else M_FOUND), (how, g.mode)
    # a tap made before this watch reached HOT does not follow, however fresh
    r = paired_rig(d=8.0)
    r.state = SC_FOUND
    r.run(1000)
    assert r.p.screen == "WARM"
    t_tap = r.t
    assert r.g.on_accel_tap(t_tap)
    r.est.fixed = 1.0
    while r.g.px.zone != 3:                     # HOT
        r.run(100)
        assert r.t - t_tap < 2000, r.t - t_tap
    r.run(300)
    assert r.g.mode == M_HUNT


def test_a_touch_never_stops_a_spike_but_blanking_does():
    r = hot_rig()
    g = r.g
    g.on_touch_down(r.t)
    g.on_gesture(r.t + 100, 1, 20, 20)          # a touch (knocks touch the panel too)
    assert g.on_accel_tap(r.t + 50) and g._state_byte(r.t + 60) & ST_TAP_HOT
    r.run(1000)
    g._emit(r.t, "NOPE")                        # our own pulse blanks the accelerometer
    assert g.scan.blanked(r.t + 100)
    assert not g.on_accel_tap(r.t + 400)
    assert g.on_accel_tap(r.t + 950)
    g2 = Game(MAC_A, est=FakeEst(), blank_fn=lambda t: True)
    assert not g2.on_accel_tap(5000)


def test_a_knock_drops_its_touch_and_a_finger_keeps_it():
    """Screen-to-screen knocks (§8): the spike counts at once. A gesture whose
    touch-down lands from KNOCK_TOUCH_AFTER_MS before to KNOCK_TOUCH_BEFORE_MS
    after its own counted spike waits for the partner: a knock (the partner
    spiked within BUMP_WINDOW_MS) does nothing; a finger's own spike (no
    partner spike by KNOCK_WAIT_MS) lets the gesture run then."""
    # knock: the finger lands, the spike, the partner's spike, the TAP (WARM: a tap scans)
    r = warm_rig()
    g = r.g
    t = r.t + 10
    g.on_touch_down(t)
    assert g.on_accel_tap(t + T.KNOCK_TOUCH_AFTER_MS)
    assert g._tap(t + 120) == t + T.KNOCK_TOUCH_AFTER_MS   # no wait for a touch
    r.peer_tap(t + 90)
    g.on_gesture(t + 150, 1, 120, 120, t)
    r.run(T.KNOCK_WAIT_MS + 300)
    assert g.mode == M_HUNT and g._held_g is None
    # the spike drained before its touch-down; the partner's reported after the gesture
    r = warm_rig()
    g = r.g
    t = r.t + 10
    assert g.on_accel_tap(t)
    g.on_touch_down(t + T.KNOCK_TOUCH_BEFORE_MS)
    g.on_gesture(t + T.KNOCK_TOUCH_BEFORE_MS + 150, 1, 120, 120, t + T.KNOCK_TOUCH_BEFORE_MS)
    r.run(100)
    assert g._held_g is not None and g.mode == M_HUNT
    r.peer_tap(t - 50)
    r.run(T.KNOCK_WAIT_MS)
    assert g.mode == M_HUNT and g._held_g is None
    # a finger's own spike: no partner spike near it, so the tap runs after the wait
    r = warm_rig()
    g = r.g
    r.peer_tap(r.t - 700)                       # an old partner spike: not this one
    r.run(100)
    t = r.t + 10
    g.on_touch_down(t)
    assert g.on_accel_tap(t + 5)
    g.on_gesture(t + 150, 1, 120, 120, t)
    r.run(T.KNOCK_WAIT_MS - 100)
    assert g.mode == M_HUNT and g._held_g is not None
    r.run(200)
    assert g.mode == M_SCANNING and g._held_g is None
    # a newer gesture runs the held one first, in order
    r = warm_rig()
    g = r.g
    t = r.t + 10
    assert g.on_accel_tap(t)
    g.on_gesture(t + 150, 1, 120, 120, t)       # held (a scan once run)
    g.on_gesture(t + 450, 1, 120, 120, t + 420) # outside the window: runs at once
    assert g._held_g is None and g.mode == M_SCANNING
    r.run(100)
    assert g.mode == M_HUNT                     # the scan started, then the tap cancelled it
    # just outside the window on either side: a tap
    for d in (-T.KNOCK_TOUCH_AFTER_MS - 1, T.KNOCK_TOUCH_BEFORE_MS + 1):
        r = warm_rig()
        g = r.g
        t = r.t + 400
        assert g.on_accel_tap(t)
        g.on_gesture(t + d + 150, 1, 120, 120, t + d)
        assert g.mode == M_SCANNING, d
    # a spike older than KNOCK_KEEP_MS is forgotten
    r = warm_rig()
    g = r.g
    assert g.on_accel_tap(r.t + 10)
    r.run(2500)
    assert g._spike_t is None
    # a blanked spike makes no knock (and does not count)
    r = warm_rig()
    g = r.g
    g._emit(r.t, "NOPE")
    t = r.t + 100
    assert not g.on_accel_tap(t)
    g.on_gesture(t + 150, 1, 120, 120, t)
    assert g.mode == M_SCANNING


def test_a_touch_in_hot_never_starts_a_scan():
    """HOT is where watches knock: a missed or blanked knock's touch must not
    start a scan and disarm the bump (§8); the second button press scans."""
    r = hot_rig(4.0)
    r.run(1000)
    g = r.g
    g.on_gesture(r.t, 1, 120, 120)
    r.run(100)
    assert g.mode == M_HUNT and g.bump_armed()
    g.on_button(r.t)
    g.on_button(r.t + 300)
    assert g.mode == M_SCANNING
    g.on_gesture(r.t + 400, 1, 120, 120)        # a tap still cancels the scan
    r.run(500)
    assert g.mode == M_HUNT


def test_hot_heartbeat_is_muted_so_knocks_are_never_blanked():
    """§6 HOT: a TICK every 0.5 s plus 150 ms blanking hid ~40 % of knocks."""
    r = hot_rig(4.0)
    t0 = r.t
    r.run(3000)
    assert not any(p.haptic == "TICK" for p in r.params if p.t_ms > t0)
    assert not any(r.g.blanked(t) for t in range(t0 + 100, r.t, 25))
    assert r.p.heartbeat is None and T.ZONE_HEARTBEAT[3] is None
    assert warm_rig().p.heartbeat == "DOUBLE"


def test_knocks_on_the_found_screen_never_start_a_new_round():
    r = hot_rig()
    g = r.g
    _found_by_press(r)
    r.state = SC_FOUND
    r.run(T.FOUND_CELEBRATE_MS + 100)
    assert g.bump_armed() and r.p.top_text == "BUTTON: PLAY AGAIN"
    for k in range(3):                          # knocks go on: spikes + touch each
        t = r.t + 10
        g.on_touch_down(t)
        assert g.on_accel_tap(t + 4)
        r.peer_tap(t + 20, k + 1)
        g.on_gesture(t + 120, 1, 120, 120, t)
        r.run(T.KNOCK_WAIT_MS + 100)
        assert g.mode == M_FOUND, k
    r.run(2500)
    t = r.t + 10                                # a finger tap spikes only its own watch:
    g.on_touch_down(t)                          # it runs, and does nothing on FOUND either
    assert g.on_accel_tap(t + 4)
    g.on_gesture(t + 120, 1, 120, 120, t)
    r.run(T.KNOCK_WAIT_MS + 200)
    assert g.mode == M_FOUND
    g.on_button(r.t)                            # only the button starts the next round
    r.run(100)
    assert g.mode == M_PAIRING and r.p.sub == "split"


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


def test_new_round_from_found_press_or_partner():
    r = hot_rig()
    g = r.g
    _found_by_press(r)
    r.state = SC_FOUND
    g.on_button(r.t + 50)                       # celebrate: ignored
    r.run(2500)
    assert g.mode == M_FOUND
    g.on_gesture(r.t, 1)                        # result: a tap does nothing
    r.run(100)
    assert g.mode == M_FOUND
    g.on_button(r.t)
    r.run(100)
    assert g.mode == M_PAIRING and r.p.sub == "split" and r.p.countdown == 30
    # the partner starts a new round: followed, but not in the first 500 ms
    r2 = hot_rig()
    g2 = r2.g
    _found_by_press(r2)
    r2.state = SC_PAIRED | ST_CONFIRMED
    while r2.t - g2.found_t < 400:
        r2.run(100)
        assert g2.mode == M_FOUND
    r2.run(200)
    assert g2.mode == M_PAIRING and g2.pair.sub == "split"


def test_found_does_not_follow_a_partner_that_ended_the_round():
    r = hot_rig()
    g = r.g
    _found_by_press(r)
    r.state = SC_PAIRING                        # END ROUND over there ...
    t0 = r.t
    r.run(1500)
    assert g.mode == M_FOUND
    r.state = SC_PAIRING | ST_CONFIRMED         # ... and it confirmed a third watch's runes
    r.run(1000)
    assert g.mode == M_PAIRING and g.pair.sub != "split"
    left = [p for p in r.params if p.t_ms > t0 and p.screen == "PAIRING"]
    assert left[0].sub == "looking" and left[0].banner == ("FRIEND LEFT", "warn", False)


# ---- the partner leaves the round ----------------------------------------------------

def test_partner_back_in_pairing_ends_the_round_here_too():
    r = warm_rig()
    r.state = SC_PAIRED | ST_CONFIRMED          # a new round's split over there: still ours
    r.run(30000)
    assert r.g.mode == M_HUNT
    r.state = SC_PAIRING                        # END ROUND or a restart over there
    t0 = r.t
    r.run(1900)
    assert r.g.mode == M_HUNT
    r.run(600)
    left = [p for p in r.params if p.t_ms > t0 and p.screen == "PAIRING"]
    p = left[0]
    assert p.t_ms - t0 >= 2000 and p.sub == "looking" and p.haptic == "NOPE"
    assert p.banner == ("FRIEND LEFT", "warn", False)
    assert r.g.pair.sub == "seen"               # and the two can pair again at once


def test_a_restarted_partner_pulls_the_other_watch_back_to_pairing():
    w = Two()
    w.run(1000)
    w.a.on_button(w.t)
    w.b.on_button(w.t)
    w.run(5000)                                 # both confirm and calibrate
    w.a.est.fixed = w.b.est.fixed = 20.0
    w.run(32000)
    assert w.a.mode == M_HUNT and w.b.mode == M_HUNT
    w.run(3000, link=False)                     # B's watchdog reboots it: a fresh Game
    w.b = Game(MAC_B, est=FakeEst(20.0), t_ms=w.t)
    w.run(2500)
    assert w.a.mode == M_PAIRING and w.a.params.banner == ("FRIEND LEFT", "warn", False)
    assert w.a.pair.sub == "seen" and w.b.pair.sub == "seen" and w.a.pair.runes == w.b.pair.runes


# ---- LINK_LOST ----------------------------------------------------------------------

def test_link_lost_banner_and_relink():
    r = paired_rig(d=20.0)
    r.state = SC_NEAR
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
    r.state = SC_BYE | ST_GOODBYE
    r.packet()
    r.run(6000, packets=False)
    assert r.p.banner == ("FRIEND IS OFF", "critical", True)


# ---- SCANNING -----------------------------------------------------------------------

def test_scan_start_cancel_and_friend_scanning():
    r = warm_rig(3000)
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
    hi = warm_rig(3000, mac=MAC_B, peer=MAC_A)
    hi.g.on_button(hi.t)
    hi.run(100)
    assert hi.g.mode == M_SCANNING
    hi.flags = proto.F_SWEEP
    hi.run(300)
    assert hi.g.mode == M_HUNT and any(p.banner and p.banner[0] == "FRIEND SCANNING"
                                       for p in hi.params[-3:])
    lo = warm_rig(3000)
    lo.g.on_button(lo.t)
    lo.run(100)
    lo.flags = proto.F_SWEEP
    lo.run(300)
    assert lo.g.mode == M_SCANNING


def test_partner_scanning_hold_chip_and_repeats_while_walking():
    r = warm_rig(3000)
    r.flags = proto.F_SWEEP
    t0 = r.t
    r.run(200)
    assert r.p.top_text == "FRIEND SCANNING" and r.p.word == "HOLD STILL"
    assert r.haptics(t0) == ["HOLD"]
    assert r.g.beacon_hz == 20
    r.run(12000)
    assert r.haptics(t0).count("HOLD") == 1      # a still wearer: no repeat
    r.activity = ACT_WALK
    r.run(12000)
    assert r.haptics(t0).count("HOLD") == 3


def test_link_loss_during_a_scan_brings_the_hidden_arrow_to_link_lost():
    r = warm_rig()
    g = r.g
    a = A.make(10.0, 20.0, r.t, A.MODE_GUIDED)
    g.arrow = a
    r.tilt = 60.0                               # not flat: ready holds
    r.run(3000)
    g.on_gesture(r.t, 1, 120, 120)
    r.run(100)
    assert g.mode == M_SCANNING and g.arrow is None
    t0 = r.t
    r.run(5200, packets=False)
    assert g.mode == M_LINK_LOST and g.arrow is a and "LOST" in r.haptics(t0)
    r.run(17000, packets=False)                 # the 20 s window starts at LINK_LOST
    r.run(3000)
    assert g.mode == M_HUNT and g.arrow is a and r.p.glyph == "arrow"


def test_opening_the_menu_cancels_a_scan():
    r = warm_rig()
    g = r.g
    g.on_gesture(r.t, 1, 120, 120)
    r.run(100)
    assert g.mode == M_SCANNING
    g.on_button(r.t, long=True)
    r.run(100)
    assert g.mode == M_HUNT and g.menu_open and not g.scan.active


# ---- MENU ---------------------------------------------------------------------------

def test_menu_rows_sun_buzz_end_round():
    r = warm_rig()
    g = r.g
    g.on_gesture(r.t, 3, 120, 120)              # long press
    r.run(100)
    assert r.p.screen == "MENU" and r.p.sub == "0v" and r.p.haptic is None   # more rows below
    assert r.p.heartbeat is None and r.p.word is None and r.p.top_text is None
    assert r.p.menu_rows == ("RESUME", "SUN: OFF", "BUZZ: FULL", "PLACE: OUT")
    g.on_button(r.t)                            # short = next row
    g.on_button(r.t, long=True)                 # long = select (SUN)
    r.run(100)
    assert g.sun and g.menu.rows[1] == "SUN: ON" and r.p.backlight == 1.0 and r.p.sun
    r.run(1000)                                 # (>= 3 touches in 1 s would block touches)
    g.on_gesture(r.t, 1, 120, 140)              # tap the third row (y 120..159) = BUZZ
    r.run(1000)
    assert g.buzz == 1 and g.menu.rows[2] == "BUZZ: EVENTS"
    g.on_gesture(r.t, 1, 120, 50)               # RESUME
    r.run(1000)
    assert r.p.screen == "WARM" and r.p.heartbeat is None    # EVENTS: no heartbeat
    assert r.p.menu_rows is None
    g.on_button(r.t, long=True)
    g.on_gesture(r.t + 10, 6, 120, 120)         # swipe up: END ROUND scrolls into view
    r.run(100)
    assert g.menu.rows[3] == "END ROUND" and r.p.sub == "0^"
    g.on_gesture(r.t, 1, 120, 170)              # END ROUND asks first
    r.run(100)
    assert g.menu.rows[3] == "SURE? PRESS" and g.mode == M_HUNT and r.p.sub == "3^"
    r.run(3100)
    assert g.menu.rows[3] == "END ROUND"
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
    r = warm_rig()
    g = r.g
    assert not g.indoor and g.est.pl.n == T.PATH_LOSS_N
    g.on_button(r.t, long=True)                 # open
    r.run(100)
    assert g.menu.rows == ("RESUME", "SUN: OFF", "BUZZ: FULL", "PLACE: OUT") and r.p.sub == "0v"
    for _ in range(3):
        g.on_button(r.t)                        # step to PLACE (still in view)
    r.run(100)
    assert r.p.sub == "3v" and g.menu.top == 0
    g.on_button(r.t)                            # step to END ROUND: the list scrolls
    r.run(100)
    assert g.menu.top == 1 and r.p.sub == "3^" and g.menu.rows[3] == "END ROUND"
    g.on_button(r.t)                            # wraps to RESUME at the top
    r.run(100)
    assert g.menu.top == 0 and r.p.sub == "0v"
    g.on_gesture(r.t, 1, 120, 170)              # tap PLACE
    r.run(100)
    assert g.indoor and g.est.pl.n == T.PATH_LOSS_N_INDOOR and g.menu.rows[3] == "PLACE: IN"
    r.run(1000)
    g.on_gesture(r.t, 7, 120, 120)              # swipe down at the top: stays put
    r.run(100)
    assert g.menu.top == 0
    r.run(1000)                                 # (touch burst filter)
    g.on_gesture(r.t, 1, 120, 50)               # tap RESUME closes; reopen at the top, sel 0
    g.on_button(r.t + 10, long=True)
    r.run(1000)
    assert g.menu.sel == 0 and g.menu.top == 0
    g.on_gesture(r.t, 6, 120, 120)              # swipe up: selection follows into view
    r.run(100)
    assert g.menu.top == 1 and g.menu.sel == 1 and r.p.sub == "0^"
    g.on_button(r.t)
    g.on_button(r.t)
    g.on_button(r.t)                            # SUN -> BUZZ -> PLACE -> END ROUND
    r.run(100)
    assert g.menu.sel == 4 and g.menu.top == 1 and r.p.sub == "3^"
    g.on_gesture(r.t, 7, 120, 120)              # swipe down: END ROUND leaves, sel clamps to PLACE
    r.run(100)
    assert g.menu.top == 0 and g.menu.sel == 3 and r.p.sub == "3v"
    assert g.menu.rows == ("RESUME", "SUN: OFF", "BUZZ: FULL", "PLACE: IN")
    r.run(1000)                                 # (touch burst filter)
    g.on_gesture(r.t, 6, 120, 120)
    r.run(1000)
    assert g.menu.top == 1
    g.on_gesture(r.t, 1, 120, 170)              # END ROUND, then confirm
    r.run(100)
    g.on_button(r.t)
    r.run(100)
    assert g.mode == M_PAIRING and g.indoor and g.est.pl.n == T.PATH_LOSS_N_INDOOR   # kept


def test_menu_shows_a_place_set_from_outside():
    r = warm_rig()
    g = r.g
    g.on_button(r.t, long=True)
    r.run(100)
    g.set_place(True)                           # the simulator's radio profile
    r.run(100)
    assert g.menu.rows[3] == "PLACE: IN" and r.p.menu_rows[3] == "PLACE: IN"


def _menu_at_end_armed(r):
    """Open the menu, step to END ROUND (list scrolled) and arm SURE? PRESS."""
    g = r.g
    g.on_button(r.t, long=True)
    r.run(100)
    for _ in range(4):
        g.on_button(r.t)
    g.on_button(r.t, long=True)                 # select END ROUND: asks first
    r.run(100)
    assert g.menu.sel == 4 and g.menu.top == 1 and g.menu.rows[3] == "SURE? PRESS"
    assert r.p.sub == "3^" and g.mode == M_HUNT


def test_menu_end_round_confirm_cancelled_by_swipe():
    r = warm_rig()
    g = r.g
    _menu_at_end_armed(r)
    g.on_gesture(r.t, 7, 120, 120)              # swipe down: SURE? PRESS scrolls away
    r.run(100)
    assert g.menu.rows == ("RESUME", "SUN: OFF", "BUZZ: FULL", "PLACE: OUT") and r.p.sub == "3v"
    g.on_button(r.t)                            # short = next row, not a confirm
    r.run(100)
    assert g.mode == M_HUNT and g.menu_open and g.menu.sel == 4 and r.p.sub == "3^"
    assert g.menu.rows[3] == "END ROUND"        # the question is gone: it must be asked again
    g.on_button(r.t)                            # wraps to RESUME
    r.run(100)
    assert g.mode == M_HUNT and g.menu.sel == 0


def test_menu_end_round_confirm_cancelled_by_other_row():
    r = warm_rig()
    g = r.g
    _menu_at_end_armed(r)
    r.run(1000)                                 # (touch burst filter)
    g.on_gesture(r.t, 1, 120, 50)               # tap SUN (visible row 0 at top 1)
    r.run(100)
    assert g.sun and g.menu.sel == 1 and g.menu.rows[3] == "END ROUND"
    g.on_button(r.t)                            # next row: BUZZ, the round goes on
    r.run(100)
    assert g.mode == M_HUNT and g.menu.sel == 2
    # armed, then the short press moves on from END ROUND itself: confirms (same row)
    for _ in range(2):
        g.on_button(r.t)
    g.on_button(r.t, long=True)
    r.run(100)
    assert g.menu.sel == 4 and g.menu.rows[3] == "SURE? PRESS"
    g.on_button(r.t)
    r.run(100)
    assert g.mode == M_PAIRING


def test_menu_rows_and_sub_change_together_at_the_tick():
    """The renderer gets menu_rows and sub from the same tick: an input between ticks
    must not shift the window under a stale highlight."""
    r = warm_rig()
    g = r.g
    g.on_button(r.t, long=True)
    r.run(100)
    for _ in range(4):
        g.on_button(r.t)
    r.run(100)
    assert r.p.sub == "3^" and g.menu.rows[3] == "END ROUND"
    before = g.menu.rows
    g.on_button(r.t)                            # wraps to RESUME: the window moves up ...
    assert g.menu.rows == before                # ... but not before the next tick
    g.on_gesture(r.t, 7, 120, 120)
    assert g.menu.rows == before
    r.run(100)
    assert g.menu.rows == ("RESUME", "SUN: OFF", "BUZZ: FULL", "PLACE: OUT") and r.p.sub == "0v"


def test_place_survives_round_resets_for_every_estimator():
    g = Game(MAC_A)
    assert g.est.pl.n == T.PATH_LOSS_N and not g.indoor
    for name in NAMES:
        g = Game(MAC_A, est=make(name))
        d = {}
        for indoor, n in ((True, T.PATH_LOSS_N_INDOOR), (False, T.PATH_LOSS_N)):
            g.set_place(indoor)
            g.reset(0)                          # END ROUND / new round
            g.est.calibrate(-47.0)              # pairing calibration, then its reset
            g.est.reset()
            assert g.indoor == indoor and g.est.pl.n == n, (name, indoor, g.est.pl.n)
            still = MotionInfo(ACT_STILL, 0.0, 0)
            for i in range(100):                # the distance itself follows the place
                g.est.update(100 * i, -75.0 + (i % 3 - 1), -75.0, still, still)
            d[indoor] = g.est.dist_m
        assert d[True] < 0.9 * d[False], (name, d)


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
    assert r.p.fps_cap == T.SAVER_FPS == 10 and g.beacon_hz == 5
    r.run(2600)
    assert r.p.glyph != "battery"
    assert r.p.backlight == T.SAVER_BACKLIGHT          # before any idle dim
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


def test_boot_at_3_percent_says_bye_only_and_powers_off_without_a_radio():
    g = Game(MAC_A, est=FakeEst(), battery=100)  # the PMU is read after the Game is built
    g.set_battery(0, 3)
    ps = []
    t = 0
    while t < 2500:
        t += 100
        ps.append(g.tick(t))
    assert [p.haptic for p in ps if p.haptic] == ["NOPE"]
    assert all(p.word == "BYE" and p.banner is None for p in ps)
    assert not g.power_off                      # the goodbye beacons get a moment ...
    while not g.power_off:                      # ... but no fill_beacon ever comes
        t += 100
        g.tick(t)
        assert t <= 100 + T.BYE_WORD_MS + 1000, t


def test_toast_and_saver_interstitial_raised_under_the_menu_show_after_it():
    r = warm_rig(battery=15)
    g = r.g
    g.on_button(r.t, long=True)
    r.run(100)
    g.set_battery(r.t, 10)
    r.peer_bat = 15
    r.run(3000)                                 # longer than the interstitial or a toast
    assert g.menu_open and r.p.screen == "MENU"
    g.on_button(r.t, long=True)                 # RESUME
    t0 = r.t
    r.run(2400)
    shown = [p for p in r.params if p.t_ms > t0]
    assert all(p.glyph == "battery" and p.word == "SAVER ON" for p in shown)
    assert all(p.banner == ("FRIEND BATT 20%", "warn", False) for p in shown)
    assert shown[0].haptic == "BATT"            # the held haptic plays with it
    r.run(200)
    assert r.p.glyph != "battery" and r.p.banner is None


def test_menu_closes_at_shutdown():
    r = warm_rig()
    g = r.g
    g.on_button(r.t, long=True)
    r.run(100)
    assert g.menu_open
    g.set_battery(r.t, 3)
    r.run(100)
    assert not g.menu_open and r.p.screen == "WARM" and r.p.word == "BYE"
    assert r.p.haptic == "NOPE"
    g.on_button(r.t, long=True)                 # shutting down: no MENU (button or screen) ...
    r.run(100)
    assert not g.menu_open and r.p.word == "BYE"
    g.on_gesture(r.t, 3, 120, 120)
    r.run(100)
    assert not g.menu_open and r.p.word == "BYE"
    g.on_button(r.t)                            # ... and no scan (short press or centre tap)
    r.run(100)
    g.on_gesture(r.t + 400, 1, 120, 120)
    r.run(500)
    assert g.mode == M_HUNT and g.beacon_hz != T.BEACON_HZ_SCAN and r.p.word == "BYE"


def test_shutdown_cancels_a_running_scan():
    r = warm_rig()
    g = r.g
    g.on_gesture(r.t, 1, 120, 120)
    while r.p.sub != "sweep":
        r.run(100)
        assert r.t < 60000
    g.set_battery(r.t, 3)
    r.run(100)                                  # (validate: no word during a sweep)
    assert g.mode == M_HUNT and r.p.word == "BYE" and g.beacon_hz != T.BEACON_HZ_SCAN


def test_unreliable_signal_pins_the_status_strip_with_warn_bars():
    r = paired_rig(d=20.0)
    r.state = SC_NEAR
    r.run(2000)
    assert not r.p.status[3] and not r.p.status[4]
    r.est.noise_db = T.UNRELIABLE_SD_DB + 0.5   # packet-to-packet sd above the gate (§5.5)
    r.run(200)
    assert r.p.status[3] and r.p.status[4]
    r.est.noise_db = None
    r.run(3200)                                 # pinned 3 s after it clears
    assert not r.p.status[3] and not r.p.status[4]


def test_screen_off_on_wrist_down_and_wake_only_press():
    r = paired_rig(d=20.0)
    g = r.g
    r.face_up = False
    r.run(T.WRIST_DOWN_MS - 500)
    assert g.screen_on                         # lowered, not yet WRIST_DOWN_MS
    r.run(1000)
    assert not g.screen_on and r.p.backlight == 0.0
    r.face_up = True
    g.on_button(r.t)                           # wakes only: no scan
    assert g.screen_on and g.mode == M_HUNT
    g.on_gesture(r.t + 100, 1)                 # < 300 ms after wake: ignored
    assert g.mode == M_HUNT
    r.run(100)
    assert r.p.backlight == 1.0 and r.p.status[3]
    # a wrist raise on the lit screen is no new wake: no boost, a tap counts at once
    r.run(5000)
    r.face_up = False
    r.run(1000)                                # < WRIST_DOWN_MS: still lit
    r.face_up = True
    r.run(100)
    g.on_wake(r.t)                             # the chip's wrist-wear event too
    assert g.screen_on and r.p.backlight == T.BACKLIGHT_NORMAL and not r.p.status[3]
    g.on_gesture(r.t + 100, 1, 120, 120)
    assert g.mode == M_SCANNING
    # held at an angle (not face-up, not past WRIST_DOWN_DEG): the screen stays on
    r2 = paired_rig(d=20.0)
    r2.face_up = False
    r2.tilt = T.WRIST_DOWN_DEG - 5.0
    r2.run(T.WRIST_DOWN_MS + 2000)
    assert r2.g.screen_on and r2.p.backlight > 0.0
    r2.tilt = T.WRIST_DOWN_DEG + 5.0
    r2.run(T.WRIST_DOWN_MS + 200)
    assert not r2.g.screen_on


def test_screen_stays_on_while_on_usb():
    r = paired_rig(d=20.0)
    g = r.g
    g.set_usb(r.t, True)
    r.face_up = False
    r.run(T.WRIST_DOWN_MS * 3)
    assert g.screen_on and r.p.backlight > 0.0
    g.set_usb(r.t, False)                      # unplugged: the lowered clock starts now
    r.run(T.WRIST_DOWN_MS - 500)
    assert g.screen_on
    r.run(1000)
    assert not g.screen_on
    g.set_usb(r.t, True)                       # plugged in: wakes
    assert g.screen_on
    r.run(T.WRIST_DOWN_MS + 500)
    assert g.screen_on
    # a watch already lit when plugged in gets no new wake (no touch-ignore window)
    g.set_usb(r.t, False)
    w = g._wake_t
    g.set_usb(r.t + 10, True)
    assert g._wake_t == w


def test_screen_off_3s_after_lowering_at_5_percent():
    r = paired_rig(d=20.0, battery=5)
    g = r.g
    r.face_up = False
    r.run(2500)
    assert g.screen_on                         # BATT_SCREEN_OFF_MS (3 s), not WRIST_DOWN_MS
    r.run(1000)
    assert not g.screen_on


def test_hidden_turn_arrow_does_not_keep_the_screen_on_in_link_lost():
    r = _arrow_rig(170.0)
    g = r.g
    r.run(1600)
    assert g.arrow.phase == A.PH_TURN
    r.run(5100, packets=False)
    assert g.mode == M_LINK_LOST and g.arrow.phase == A.PH_TURN
    r.face_up = False
    r.run(T.WRIST_DOWN_MS + 500, packets=False)
    assert not g.screen_on and r.p.backlight == 0.0


def test_touch_burst_filter():
    r = paired_rig(d=20.0)
    g = r.g
    for k in range(3):
        g.on_touch_down(r.t + 10 * k)          # 3 touches within 1 s (outside iris)
        g.on_gesture(r.t + 10 * k, 1, 10, 10)
    g.on_touch_down(r.t + 500)
    g.on_gesture(r.t + 500, 1, 120, 120)
    assert g.mode == M_HUNT                    # blocked for 2 s
    g.on_touch_down(r.t + 2100)
    g.on_gesture(r.t + 2100, 1, 120, 120)
    assert g.mode == M_SCANNING


def test_rain_too_short_for_a_tap_still_trips_the_burst_filter():
    # contacts under 60 ms give no gesture, but every landing counts (§8)
    r = warm_rig()
    g = r.g
    gr = GestureRecognizer()
    t0 = r.t + 10
    drops = ((0, 30), (120, 30), (240, 30), (360, 30), (600, 150))   # (start, length) ms
    for k in range(120):                        # 10 ms polling
        t = t0 + 10 * k
        touching = False
        for a, n in drops:
            if a <= t - t0 < a + n:
                touching = True
        was = gr.down
        code = gr.update(t, touching, 120, 120)
        if gr.down and not was:
            g.on_touch_down(t)
        if code:
            assert code == 1 and t - t0 > 600   # only the last drop is a TAP
            g.on_gesture(t, code, gr.ev_x, gr.ev_y)
    assert g.mode == M_HUNT                     # its TAP is ignored: no scan
    g.on_touch_down(t0 + 2500)
    g.on_gesture(t0 + 2650, 1, 120, 120)        # 2 s later a tap works again
    assert g.mode == M_SCANNING


def test_fill_beacon_fields():
    r = hot_rig()
    g = r.g
    r.activity = ACT_WALK
    r.steps = 70000
    r.run(100)
    assert g.on_accel_tap(r.t)
    r.run(100)
    b = proto.Beacon(1)
    g.fill_beacon(b, r.t + 20)
    assert b.state & 0x0F == SC_HOT and b.state & ST_TAP_HOT
    assert b.bump_ago_ms == 120 and b.taps == 1
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


def test_stillness_hint_once_per_episode():
    r = paired_rig(d=40.0)
    r.run(20000)
    assert r.p.top_text is None


def test_menu_holds_event_haptics_and_plays_them_on_close():
    r = paired_rig(d=20.0)
    r.state = SC_NEAR
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
    g.menu.close()
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
    # the partner already pressed: a second press stays a fallback press, even
    # when it lands before the tick that would match both presses
    r3 = hot_rig()
    g3 = r3.g
    g3.on_button(r3.t)
    r3.state = SC_HOT | ST_PRESS
    r3.packet(t=r3.t + 50)
    g3.on_button(r3.t + 60)
    r3.run(100)
    assert g3.mode == M_FOUND


def _lose(r, walk_trend):
    """Hunt in NEAR, optionally walking closer with a warmer trend, then lose the link."""
    r.state = SC_NEAR
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
    assert r.g.lost_trend == 1 and r.p.trend == 1      # the LAST chip's mark (§6)
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
        g.set_motion(t, ACT_STILL, 0, 0.0, 5.0 if face_up else 90.0, face_up)
        p = g.tick(t)
        assert not validate(p), (t, validate(p))
        out.append(p)
    return t, out


def test_long_idle_timestamps_never_wrap():
    r = paired_rig(d=20.0)
    r.state = SC_NEAR
    r.run(2000)
    g = r.g
    g.on_wake(r.t)
    g._toast_set("OLD TOAST")
    g._hint_set(r.t, "OLD HINT")
    t, ps = _hourly(g, r.t, 192, face_up=False)        # 8 days, link lost, wrist down
    for p in ps[1:]:
        assert p.screen == "LINK_LOST" and p.banner[0] == "LOST 10M+ GO BACK", p.banner
        assert p.backlight == 0.0
    g.on_wake(t)
    t, ps = _hourly(g, t, 192)                          # 8 more days face up, no input
    for p in ps[1:]:                                    # (first tick: face-up wake boost)
        assert p.banner[0] == "LOST 10M+ GO BACK" and p.top_text is None
        assert p.backlight == T.IDLE_DIM_BACKLIGHT and not p.status[3]
    g.on_gesture(t + 50, 3)                             # long press still opens the menu
    assert g.menu_open
    # FOUND result stays 'result' and a press still starts a new round
    r2 = hot_rig()
    _found_by_press(r2)
    t, ps = _hourly(r2.g, r2.t, 192)
    assert all(p.sub == "result" and p.word.startswith("FOUND ") for p in ps)
    assert r2.g._press_t is None and r2.g._lit_until is None
    r2.g.on_button(t + 50)
    r2.g.tick(t + 100)
    assert r2.g.mode == M_PAIRING
    # a touch 6.7 days ago never counts toward a rain burst (its age wraps past 2^29 ms)
    r3 = paired_rig(d=20.0)
    g3 = r3.g
    g3.on_touch_down(r3.t)
    g3.on_gesture(r3.t, 1, 10, 10)                      # outside the iris: just a touch
    r3.t, _ = _hourly(g3, r3.t, 161)
    r3.state = SC_NEAR
    r3.run(1000)                                        # relink
    assert g3.mode == M_HUNT
    r3.run(6000, drop=2)                                # half the packets: the bars say so
    assert r3.p.status[2] < T.LINK_Q_MAX
    g3.on_touch_down(r3.t)
    g3.on_gesture(r3.t, 1, 10, 10)
    r3.run(5000)
    g3.on_touch_down(r3.t)
    g3.on_gesture(r3.t, 1, 120, 120)                    # a centre tap: scan
    assert g3.mode == M_SCANNING


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
    assert pv.age(1000 + 2 * 3600000) == 3600000
    assert not pv.live3(1000 + 2 * 3600000)


# ---- round-2 review: arrow vs scan, turn heartbeat, ready timeout -------------
def _arrow_rig(theta=10.0, mode=A.MODE_GUIDED, **kw):
    r = paired_rig(d=20.0, **kw)
    r.state = SC_NEAR
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
            # the live-mirror halo: glow_r 12 (§5.7), not the zone halo
            assert all(p.glow_r_px == T.FIELD_SCAN_SWEEP_GLOW_R for p in subs)
        r.run(4000)
        assert r.g.arrow.phase == A.PH_WALK and r.p.heartbeat is not None
    # idle dim spares the turn (§8): 30 s without input, yet the normal backlight
    r = paired_rig(d=20.0)
    r.run(30000)
    assert r.p.backlight == T.IDLE_DIM_BACKLIGHT
    r.g.arrow = A.make(120.0, 20.0, r.t)
    r.run(2000)
    assert r.g.arrow.phase == A.PH_TURN and r.p.backlight == T.BACKLIGHT_NORMAL


def test_menu_and_saver_interstitial_pause_the_turn_pacer():
    r = _arrow_rig(150.0, battery=15)
    g = r.g
    r.run(1600)
    t0 = r.t
    g.on_button(r.t, long=True)
    r.run(6000)                                 # a 150 deg turn takes 5 s
    g.on_button(r.t, long=True)                 # RESUME
    r.run(100)
    a = g.arrow
    assert a.phase == A.PH_TURN and a.pacer < 150.0 and r.p.sub == "turn"
    assert "DOUBLE" not in r.haptics(t0)
    p0 = a.pacer
    g.set_battery(r.t, 10)
    r.run(2000)
    assert r.p.word == "SAVER ON" and a.phase == A.PH_TURN and a.pacer <= p0 + 3.5


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
    r.run(T.WRIST_DOWN_MS - 2000)
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


# ---- round-3 review: pairing candidates, partner leaving, waiting toasts, hints -----
def test_pairing_drops_a_candidate_that_stops_showing_pairing():
    # C (stronger) is taken, then starts a round with another watch; B is still pairing
    p = Pairing(MAC_A)
    t = 0
    while p.sub == "looking":
        t += 100
        p.on_candidate(t, MAC_C, -45)
        p.on_candidate(t + 50, MAC_B, -50)
        p.update(t + 50)
    assert p.sub == "seen" and p.peer_mac == MAC_C
    t_off = t
    while p.peer_mac != MAC_B:
        t += 100
        assert t - t_off <= 3000, t - t_off
        p.on_candidate(t, MAC_C, -45, peer_pairing=False)   # C beacons on, not as PAIRING
        p.on_candidate(t + 50, MAC_B, -50)
        p.update(t + 50)
    assert p.sub == "seen" and p.runes == rune_ids(MAC_A, MAC_B)


def test_a_watch_in_its_split_is_never_a_pairing_candidate():
    # A restarts (a fresh Game) 1 s before B's GO: A must not take the splitting B,
    # and a tap on A must not pair or calibrate one-sidedly
    w = Two()
    w.run(1000)
    w.a.on_button(w.t)
    w.b.on_button(w.t)
    w.run(5000)
    assert w.b.pair.sub == "split" and w.b.state_byte & SC_MASK == SC_PAIRED
    while w.b.pair.countdown > 1:
        w.run(100)
    w.a = Game(MAC_A, est=FakeEst(1.0), t_ms=w.t)
    while w.b.mode == M_PAIRING:
        w.run(100)
        assert w.a.pair.sub == "looking", w.a.pair.sub
    w.run(200)
    w.a.on_button(w.t)                          # A's player taps
    t0 = w.t
    while not (w.a.pair.sub == "seen" and w.b.pair.sub == "seen"):
        w.run(100)
        assert w.a.pair.sub in ("looking", "seen"), w.a.pair.sub
        assert w.t - t0 <= 5000, (w.a.pair.sub, w.b.mode, w.b.pair.sub)
    assert w.b.mode == M_PAIRING and w.b.params.banner == ("FRIEND LEFT", "warn", False)
    assert w.a.pair.runes == w.b.pair.runes


def _two_in_a_new_round():
    """Two watches paired, FOUND by the fallback presses, then A starts a new round."""
    w = Two()
    w.run(1000)
    w.a.on_button(w.t)
    w.b.on_button(w.t)
    w.run(5000)
    w.run(35000)                                # split, then HOT at 1 m
    assert w.a.mode == M_HUNT and w.b.mode == M_HUNT and w.a.params.screen == "HOT"
    w.a.on_button(w.t)
    w.b.on_button(w.t)
    w.run(300)
    assert w.a.mode == M_FOUND and w.b.mode == M_FOUND
    w.run(2500)
    w.a.on_button(w.t)                          # PLAY AGAIN: a new round on both
    w.run(1000)
    assert w.a.pair.sub == "split" and w.b.pair.sub == "split"
    return w


def test_new_round_split_is_not_taken_by_a_partner_that_left():
    for how in ("restart", "end round"):
        w = _two_in_a_new_round()
        w.run(4000)
        if how == "restart":                    # A's watchdog reboots it 5 s into the split
            w.a = Game(MAC_A, est=FakeEst(1.0), t_ms=w.t)
        else:                                   # A picks END ROUND in the MENU
            g = w.a
            g.on_button(w.t, long=True)
            w.run(100)
            for _ in range(4):
                g.on_button(w.t)
            g.on_button(w.t, long=True)
            w.run(100)
            g.on_button(w.t)
            w.run(100)
            assert g.mode == M_PAIRING and g.pair.sub == "looking", how
        w.run(1000)
        w.a.on_button(w.t)                      # one tap on A
        while w.b.pair.sub == "split":
            w.run(100)
            assert w.a.pair.sub == "looking", (how, w.a.pair.sub)
        t0 = w.t
        while w.b.mode != M_PAIRING:
            assert w.b.mode == M_SEARCHING, (how, w.b.mode)
            w.run(100)
            assert w.t - t0 <= 2600, how
        assert w.b.params.banner == ("FRIEND LEFT", "warn", False), how
        w.run(500)
        assert w.a.pair.sub == "seen" and w.b.pair.sub == "seen", how
        assert w.a.pair.runes == w.b.pair.runes, how


def test_toast_raised_during_the_sweep_shows_after_the_scan():
    r = warm_rig(battery=25)
    g = r.g
    g.on_gesture(r.t, 1, 120, 120)
    while r.p.sub != "sweep":
        r.run(100)
        assert r.t < 60000
    g.set_battery(r.t, 20)
    t0 = r.t
    r.run(3000)
    sweep = [p for p in r.params if p.t_ms > t0]
    assert all(p.sub == "sweep" and p.banner is None for p in sweep)
    g.on_gesture(r.t, 1, 120, 120)              # cancel: back to the zone
    t1 = r.t
    r.run(T.TOAST_MS + 500)
    shown = [p for p in r.params if p.t_ms > t1 and p.banner == ("BATTERY 20%", "warn", False)]
    assert shown and shown[0].t_ms - t1 <= 100 and shown[0].screen == "WARM"
    assert shown[-1].t_ms - shown[0].t_ms >= T.TOAST_MS - 100


def test_saver_interstitial_raised_in_a_scan_shows_after_it():
    r = warm_rig(battery=15)
    g = r.g
    g.on_gesture(r.t, 1, 120, 120)
    r.run(200)
    assert r.p.screen == "SCANNING" and r.p.sub == "ready"
    g.set_battery(r.t, 10)
    t0 = r.t
    while r.p.sub != "sweep" or r.t - t0 < T.BATT_INTERSTITIAL_MS + 500:
        r.run(100)
        assert r.t - t0 < 20000
    assert all(p.glyph != "battery" and p.word != "SAVER ON" for p in r.params if p.t_ms > t0)
    g.on_gesture(r.t, 1, 120, 120)              # cancel
    t1 = r.t
    r.run(T.BATT_INTERSTITIAL_MS + 500)
    shown = [p for p in r.params if p.t_ms > t1 and p.glyph == "battery"]
    assert shown and shown[0].t_ms - t1 <= 100 and all(p.word == "SAVER ON" for p in shown)
    assert shown[-1].t_ms - shown[0].t_ms >= T.BATT_INTERSTITIAL_MS - 100


def test_calibrate_hold_still_chip_waits_for_a_real_pause():
    r = Rig()
    r.rssi = -50
    r.run(600)
    g = r.g
    g.on_button(r.t)
    r.state = SC_PAIRED | ST_CONFIRMED
    cal = []
    for _ in range(30):                         # alternating -40/-52 dB: sd 6 > 4, no fill
        r.packet(t=r.t + 50, rssi=-40)
        r.packet(t=r.t + 100, rssi=-52)
        r.t += 100
        p = g.tick(r.t)
        assert not validate(p), validate(p)
        if p.sub == "calibrate":
            cal.append(p)
    assert cal and cal[0].top_text == "STAND 1 STEP APART"
    hold = [p for p in cal if p.top_text == "HOLD STILL"]
    assert hold and hold[0].t_ms - cal[0].t_ms >= UNSTABLE_SHOW_MS
    assert all(p.top_text == "STAND 1 STEP APART" for p in cal if p.t_ms < hold[0].t_ms)


def test_partner_sweep_rate_ends_with_the_hunt():
    # the partner's last HOT beacon already carries its new scan's flag (its state
    # byte is a tick old) when the fallback presses match: FOUND must not keep 20 Hz
    r = hot_rig()
    g = r.g
    r.flags = proto.F_SWEEP
    r.state = SC_HOT | ST_PRESS
    g.on_button(r.t)
    r.run(100)
    assert g.mode == M_FOUND
    r.flags = 0
    r.state = SC_FOUND
    r.run(200)
    assert g.beacon_hz == T.BEACON_HZ_NORMAL


def test_a_partner_that_left_while_out_of_range_gets_no_relink_fanfare():
    for mode in (M_LINK_LOST, M_SEARCHING):
        if mode == M_LINK_LOST:
            r = paired_rig(d=20.0)
            r.state = SC_NEAR
            r.run(2000)
            r.run(8000, packets=False)
        else:                                   # no packet in the whole split
            r = Rig(d=20.0)
            r.rssi = -50
            r.run(600)
            r.g.on_button(r.t)
            r.state = SC_PAIRED | ST_CONFIRMED
            r.rssi = -45
            r.run(4500)
            r.run(31500, packets=False)
        assert r.g.mode == mode
        r.state = SC_PAIRING                    # it ended the round (or restarted) meanwhile
        t0 = r.t
        r.run(2500)
        after = [p for p in r.params if p.t_ms > t0]
        assert not any(p.haptic == "CLOSER" or p.burst for p in after), mode
        assert not any(p.banner == ("BACK IN RANGE", "info", False) for p in after), mode
        left = [p for p in after if p.screen == "PAIRING"]
        assert left and left[0].t_ms - t0 >= 2000 and left[0].haptic == "NOPE", mode
        assert left[0].banner == ("FRIEND LEFT", "warn", False), mode


def test_partner_leaving_lights_a_lowered_screen_for_5s_not_at_5_percent():
    for bat in (90, 5):
        r = warm_rig(battery=bat)
        g = r.g
        r.face_up = False
        r.run(T.WRIST_DOWN_MS + 500)
        assert not g.screen_on, bat
        r.state = SC_PAIRING                    # the partner ended the round
        t0 = r.t
        while g.mode != M_PAIRING:
            r.run(100)
            assert r.t - t0 < 3000, bat
        p = r.p
        assert p.banner == ("FRIEND LEFT", "warn", False) and p.haptic == "NOPE", bat
        if bat == 5:                            # 5 %: haptics carry it, the screen stays dark
            r.run(3000)
            assert not g.screen_on and all(q.backlight == 0.0 for q in r.params if q.t_ms > t0)
            continue
        _lit_for(r, r.t, T.EVENT_LIT_MS)


def _dark(r, limit=40000):
    """Run until the screen is off (the wrist must already be down)."""
    t0 = r.t
    while r.g.screen_on:
        r.run(100)
        assert r.t - t0 < limit, "screen never went dark"


def _lit_for(r, t0, ms):
    """The screen stays lit from ``t0`` until ``t0 + ms``, then (wrist still
    down, clock long run out) is dark within one tick."""
    while r.t < t0 + ms - 100:
        r.run(100)
        assert r.g.screen_on and r.p.backlight > 0.0, (r.t - t0, ms)
    r.run(200)
    assert not r.g.screen_on and r.p.backlight == 0.0, (r.t - t0, ms)


def test_found_lights_a_dark_screen_for_10s_whatever_the_tilt():
    r = hot_rig()
    g = r.g
    r.est.fixed = 1.5
    r.face_up = False
    _dark(r)                                    # HOT entry and bump-ready holds ran out
    r.state = SC_HOT | ST_TAP_HOT
    assert g.on_accel_tap(r.t)
    r.peer_tap(r.t + 150)
    r.run(200)
    assert g.mode == M_FOUND and g.screen_on
    first = [p for p in r.params if p.screen == "FOUND"][0]
    assert first.haptic == "FOUND" and first.backlight == T.BACKLIGHT_BOOST
    _lit_for(r, g.found_t, T.FOUND_LIT_MS)
    assert g.mode == M_FOUND and r.p.word.startswith("FOUND ")


def test_found_result_word_chip_and_button_only():
    r = hot_rig()
    g = r.g
    g.round_t0 = r.t - 108000                   # a 1:48 round
    _found_by_press(r)
    assert r.p.sub == "celebrate" and r.p.top_text == "TIME 1:48" and r.p.word == "FOUND"
    g.on_button(r.t + 50)                       # celebrate: a press made with the bump is ignored
    r.run(100)
    assert g.mode == M_FOUND
    r.run(T.FOUND_CELEBRATE_MS)
    assert r.p.sub == "result" and r.p.top_text == "BUTTON: PLAY AGAIN"
    assert r.p.word == "FOUND 1:48"
    t = r.t + 10                                # a finger tap (no spike): nothing
    g.on_touch_down(t)
    g.on_gesture(t + 100, 1, 120, 120, t)
    r.run(T.KNOCK_WAIT_MS + 200)
    assert g.mode == M_FOUND and r.p.word == "FOUND 1:48" and r.p.banner is None
    g.on_button(r.t)
    r.run(100)
    assert g.mode == M_PAIRING and r.p.sub == "split"


def test_found_word_overflows_to_whole_minutes():
    assert [fmt_found(s) for s in (0, 108, 599, 600, 768, 5999, 6000, 21600)] == [
        "FOUND 0:00", "FOUND 1:48", "FOUND 9:59", "FOUND 10M", "FOUND 12M", "FOUND 99M",
        "FOUND 99M+", "FOUND 99M+"]
    for s in range(0, 6100, 7):
        w = fmt_found(s)
        assert len(w) <= T.WORD_MAX_CHARS and not set(w) - set(T.WORD_CHARS), w
    for back, top, word in ((768000, "TIME 12:48", "FOUND 12M"),
                            (7200000, "TIME 99:59", "FOUND 99M+")):
        r = hot_rig()
        r.g.round_t0 = ticks_add(r.t, -back)
        _found_by_press(r)
        assert r.p.top_text == top, r.p.top_text
        r.run(T.FOUND_CELEBRATE_MS + 100)
        assert r.p.word == word, r.p.word


def test_button_on_a_dark_found_screen_only_wakes():
    r = hot_rig()
    g = r.g
    _found_by_press(r)
    r.face_up = False
    _dark(r)
    assert g.mode == M_FOUND
    g.on_button(r.t)                            # wake only
    assert g.screen_on and g.mode == M_FOUND
    r.run(100)
    assert r.p.word.startswith("FOUND ") and r.p.backlight == T.BACKLIGHT_BOOST
    g.on_button(r.t)
    r.run(100)
    assert g.mode == M_PAIRING and g.pair.sub == "split"


def _event(r, how):
    """Raise one §8 event wake on a rig; returns when its first frame is out."""
    g = r.g
    if how == "hot":
        r.est.fixed = 4.0
        while r.p.screen != "HOT":
            r.run(100)
    elif how == "bump_ready":
        r.est.fixed = 1.5
        while not g.bump_ready:
            r.run(100)
        assert r.p.word == "BUMP!" and r.p.haptic == "DOUBLE"
    elif how == "lost":
        while r.p.screen != "LINK_LOST":
            r.run(100, packets=False)
        assert r.p.haptic == "LOST"


def test_event_wakes_light_a_dark_screen_for_5s():
    for how in ("hot", "bump_ready", "lost"):
        r = hot_rig(4.0) if how == "bump_ready" else warm_rig()
        r.face_up = False
        _dark(r)
        _event(r, how)
        t0 = r.t
        assert r.g.screen_on and r.p.backlight == T.BACKLIGHT_BOOST, how
        if how == "lost":                       # the hold ends with the link still lost
            while r.t < t0 + T.EVENT_LIT_MS - 100:
                r.run(100, packets=False)
                assert r.g.screen_on, how
            r.run(200, packets=False)
            assert not r.g.screen_on, how
        else:
            _lit_for(r, t0, T.EVENT_LIT_MS)


def test_event_wake_holds_a_lit_lowered_screen_on_the_wrist_clock():
    r = warm_rig()
    g = r.g
    r.face_up = False
    r.run(8000)                                 # lowered 8 s: 2 s left on the wrist clock
    assert g.screen_on
    _event(r, "hot")
    _lit_for(r, r.t, T.EVENT_LIT_MS)            # held past the 10 s, dark when the hold ends
    r2 = warm_rig()
    r2.face_up = False
    t_down = r2.t
    r2.run(500)
    _event(r2, "hot")                           # lowered ~2 s at the event: the clock decides
    r2.run(T.EVENT_LIT_MS + 200)
    assert r2.g.screen_on
    while r2.g.screen_on:
        r2.run(100)
    assert T.WRIST_DOWN_MS <= r2.t - t_down <= T.WRIST_DOWN_MS + 300, r2.t - t_down


def test_event_wakes_at_5_percent_only_found():
    for how in ("hot", "lost"):
        r = warm_rig(battery=5)
        r.face_up = False
        _dark(r)
        t0 = r.t
        _event(r, how)
        assert not r.g.screen_on and all(p.backlight == 0.0 for p in r.params if p.t_ms > t0), how
    r = paired_rig(d=2.0, battery=5)
    r.state = SC_HOT
    r.run(2000)
    g = r.g
    r.est.fixed = 1.5
    r.face_up = False
    _dark(r)
    r.state = SC_HOT | ST_TAP_HOT
    assert g.on_accel_tap(r.t)
    r.peer_tap(r.t + 150)
    r.run(200)
    assert g.mode == M_FOUND and r.p.backlight == T.SAVER_BACKLIGHT
    _lit_for(r, g.found_t, T.FOUND_LIT_MS)      # 10 s, not BATT_SCREEN_OFF_MS


def test_event_wake_ignores_a_press_that_began_before_it():
    r = warm_rig()
    g = r.g
    r.face_up = False
    _dark(r)
    t = r.t + 10
    g.on_touch_down(t)                          # a sleeve on the dark screen ...
    _event(r, "lost")                           # ... the link drops: the screen lights
    assert g.screen_on and ticks_diff(g._wake_t, t) > 0
    g.on_gesture(t + 900, 3, 120, 120, t)       # the press that began before it: no MENU
    assert not g.menu_open
    t2 = ticks_add(g._wake_t, T.WAKE_TOUCH_IGNORE_MS + 50)
    g.on_gesture(t2 + 900, 3, 120, 120, t2)
    assert g.menu_open


def test_no_event_wake_while_shutting_down():
    r = paired_rig(d=20.0)
    g = r.g
    r.state = SC_NEAR
    r.face_up = False
    _dark(r)
    r.run(3000, packets=False)
    g.set_battery(r.t, 3)                       # BYE ...
    t0 = r.t
    while not g.power_off:
        r.run(100, packets=False)
        assert r.t - t0 < 5000
    after = [p for p in r.params if p.t_ms > t0]
    assert any(p.screen == "LINK_LOST" for p in after)   # ... LINK-LOST arrives during it
    assert not g.screen_on and all(p.backlight == 0.0 for p in after)


def test_relink_into_hot_says_look_around():
    r = hot_rig()
    r.run(5200, packets=False)
    assert r.g.mode == M_LINK_LOST
    r.state = SC_HOT
    t1 = r.t
    r.run(400)
    hot = [p for p in r.params if p.t_ms > t1 and p.screen == "HOT"]
    assert hot and hot[0].top_text == "LOOK AROUND"


def test_warm_clears_a_running_tap_to_scan_hint():
    r = paired_rig(d=40.0)
    r.activity = ACT_WALK
    r.state = SC_FAR
    r.run(6000)
    r.est.fixed = 20.0
    r.run(4000)
    assert r.p.screen == "NEAR" and r.p.top_text == "TAP TO SCAN"
    r.est.fixed = 10.0
    t0 = r.t
    r.run(2500)
    warm = [p for p in r.params if p.t_ms > t0 and p.screen == "WARM"]
    assert warm and all(p.top_text is None for p in warm)


def test_second_loss_forgets_the_trend_from_before_the_relink():
    r = paired_rig(d=20.0)
    _lose(r, True)
    r.run(5200, packets=False)
    assert r.g.mode == M_LINK_LOST and r.g.lost_trend == 1
    r.est.trend = 0
    r.activity = ACT_STILL
    r.run(1000)
    assert r.g.mode == M_HUNT
    r.run(5200, packets=False)
    assert r.g.mode == M_LINK_LOST and r.g.lost_trend == 0


def test_battery_warnings_rearm_after_charging():
    r = paired_rig(d=20.0, battery=25)
    g = r.g
    g.set_battery(r.t, 20)
    r.run(100)
    assert r.p.banner == ("BATTERY 20%", "warn", False)
    r.run(3000)
    g.set_battery(r.t, 24)
    r.run(100)
    g.set_battery(r.t, 20)
    r.run(100)
    assert r.p.banner == ("BATTERY 20%", "warn", False) and r.p.haptic == "BATT"
    r.peer_bat = 20
    r.run(3500)
    r.peer_bat = 24
    r.run(500)
    r.peer_bat = 20
    t0 = r.t
    r.run(500)
    assert any(p.banner == ("FRIEND BATT 20%", "warn", False) for p in r.params if p.t_ms > t0)
