from finder import proto
from finder.compat import ticks_add
from finder.link import (LinkMonitor, TxScheduler, Xorshift16, ST_IDLE, ST_CONNECTED,
                         ST_SEARCHING, ST_LOST, R_BAD, R_PARTNER, R_OTHER,
                         R_CANDIDATE, R_DUP)

A = b"\x02\x00\x00\x00\x00\x0a"
B = b"\x02\x00\x00\x00\x00\x0b"
C = b"\x02\x00\x00\x00\x00\x0c"


def _pkt(seq=0, game_id=1, bump_ago=proto.BUMP_NONE, rssi_last=-60):
    b = proto.Beacon(game_id)
    b.seq = seq
    b.bump_ago_ms = bump_ago
    b.rssi_last = rssi_last
    return b.pack_into(bytearray(250))


def _feed(lm, mac, seqs, t0=0, dt=50, rssi=-55):
    t = t0
    for s in seqs:
        lm.on_packet(mac, _pkt(s), 16, rssi, t)
        t += dt
    return t


def test_filtering_magic_game_partner():
    lm = LinkMonitor(game_id=1)
    lm.lock(A, 0)
    assert lm.on_packet(A, _pkt(1, game_id=2), 16, -50, 10) == R_BAD
    bad = _pkt(1)
    bad[1] = 0
    assert lm.on_packet(A, bad, 16, -50, 10) == R_BAD
    assert lm.on_packet(A, _pkt(1), 12, -50, 10) == R_BAD
    assert lm.on_packet(B, _pkt(1), 16, -50, 10) == R_OTHER
    assert lm.on_packet(A, _pkt(1, rssi_last=-66), 16, -52, 20) == R_PARTNER
    assert lm.on_packet(A, _pkt(1), 16, -52, 30) == R_DUP
    assert (lm.n_bad, lm.n_other, lm.n_dup, lm.n_rx) == (3, 1, 1, 1)
    assert lm.peer.rssi_last == -66 and lm.last_rssi == -52 and lm.last_seen == 30


def test_unlocked_goes_to_candidates():
    lm = LinkMonitor(game_id=1)
    assert lm.on_packet(A, _pkt(1), 16, -30, 0) == R_CANDIDATE
    assert lm.on_packet(B, _pkt(1), 16, -70, 0) == R_CANDIDATE
    assert lm.total == 0 and lm.nearby(100) == 2 and lm.nearby(5000) == 0
    assert lm.tick(0) == ST_IDLE


def test_sample_ring_and_cursor():
    lm = LinkMonitor(game_id=1, ring=8)
    lm.lock(A)
    for s in range(20):
        lm.on_packet(A, _pkt(s, rssi_last=-40 - s), 16, -50 - s, 1000 + 50 * s)
    assert lm.total == 20
    k = lm.oldest(0)
    assert k == 12
    got = []
    while k < lm.total:
        i = lm.idx(k)
        got.append((lm.s_t[i], lm.s_rssi[i], lm.s_seq[i], lm.s_peer[i]))
        k += 1
    assert len(got) == 8 and got[0] == (1600, -62, 12, -52) and got[-1] == (1950, -69, 19, -59)
    assert lm.oldest(15) == 15


def test_loss_no_loss_and_gaps():
    lm = LinkMonitor(game_id=1)
    lm.lock(A)
    _feed(lm, A, range(10))
    assert lm.loss() == 0.0 and lm.loss_pct() == 0
    lm2 = LinkMonitor(game_id=1)
    lm2.lock(A)
    _feed(lm2, A, range(0, 40, 2))          # every other packet lost
    assert abs(lm2.loss() - 0.5) < 1e-9 and lm2.loss_pct() == 50


def test_loss_seq_wrap():
    lm = LinkMonitor(game_id=1)
    lm.lock(A)
    seqs = [65530, 65531, 65532, 65534, 65535, 0, 1, 3, 4]   # 2 lost across the wrap
    _feed(lm, A, seqs)
    # 8 gaps summing to 10 -> 2 lost of 10
    assert abs(lm.loss() - 0.2) < 1e-9, lm.loss()
    assert lm.last_seq == 4


def test_loss_window_is_last_40():
    lm = LinkMonitor(game_id=1, loss_window=40)
    lm.lock(A)
    t = _feed(lm, A, range(0, 100, 5))       # heavy loss early
    assert lm.loss() > 0.7
    _feed(lm, A, range(96, 96 + 41), t0=t)   # then 41 clean packets
    assert lm.loss() == 0.0


def test_seq_restart_resets_window():
    lm = LinkMonitor(game_id=1)
    lm.lock(A)
    t = _feed(lm, A, [100, 102, 104])
    assert lm.loss() > 0
    _feed(lm, A, [0, 1, 2], t0=t)            # partner rebooted
    assert lm.loss() == 0.0 and lm.last_seq == 2


def test_restart_after_seq_past_half_range_resets_window():
    lm = LinkMonitor(game_id=1)
    lm.lock(A)
    t = _feed(lm, A, range(40000, 40060))
    assert lm.loss_pct() == 0
    _feed(lm, A, range(1, 11), t0=t)         # partner rebooted; gap 25537 < 0x8000
    assert lm.loss_pct() == 0 and lm.n_restart == 1 and lm.last_seq == 10


def test_gap_up_to_max_gap_still_counts_as_loss():
    lm = LinkMonitor(game_id=1, max_gap=200)
    lm.lock(A)
    t = _feed(lm, A, [0, 1])
    _feed(lm, A, [201], t0=t)                # 199 lost: a real (long) outage
    assert lm.n_restart == 0 and lm.loss_pct() == 99
    _feed(lm, A, [402], t0=t + 50)           # jump 201 > max_gap: new stream
    assert lm.n_restart == 1 and lm.loss_pct() == 0


def test_locked_never_heard_reaches_lost():
    lm = LinkMonitor(game_id=1)
    lm.lock(A, now=1000)
    assert lm.state_t == 1000
    assert lm.tick(1500) == ST_SEARCHING       # not heard yet: never CONNECTED
    assert lm.tick(6000) == ST_SEARCHING
    assert lm.tick(6001) == ST_LOST and lm.state_t == 6001
    lm2 = LinkMonitor(game_id=1)
    lm2.lock(A)                                # no time given: clock starts at tick
    assert lm2.tick(100) == ST_SEARCHING and lm2.state_t == 100
    assert lm2.tick(5100) == ST_SEARCHING
    assert lm2.tick(5101) == ST_LOST
    assert lm2.age_ms(5101) is None
    lm2.on_packet(A, _pkt(1), 16, -50, 5200)
    assert lm2.tick(5200) == ST_CONNECTED


def test_state_transitions():
    lm = LinkMonitor(game_id=1)
    assert lm.tick(0) == ST_IDLE
    lm.lock(A)
    assert lm.tick(0) == ST_SEARCHING         # locked but never heard
    lm.on_packet(A, _pkt(1), 16, -50, 100)
    assert lm.tick(100) == ST_CONNECTED
    assert lm.tick(1100) == ST_CONNECTED       # exactly 1 s: still connected
    assert lm.tick(1101) == ST_SEARCHING
    assert lm.tick(5100) == ST_SEARCHING
    assert lm.tick(5101) == ST_LOST and lm.state_t == 5101
    lm.on_packet(A, _pkt(2), 16, -50, 6000)
    assert lm.tick(6000) == ST_CONNECTED
    assert lm.age_ms(6030) == 30
    lm.unlock()
    assert lm.tick(6100) == ST_IDLE and lm.partner is None


def test_state_across_ticks_wrap():
    lm = LinkMonitor(game_id=1)
    t0 = ticks_add(0, -500)
    lm.lock(A)
    lm.on_packet(A, _pkt(1), 16, -50, t0)
    assert lm.tick(ticks_add(t0, 900)) == ST_CONNECTED
    assert lm.tick(ticks_add(t0, 1500)) == ST_SEARCHING
    assert lm.tick(ticks_add(t0, 6000)) == ST_LOST


def _cand(lm, mac, rssi, t_rx, bump_ago):
    return lm.on_packet(mac, _pkt(0, bump_ago=bump_ago), 16, rssi, t_rx)


def test_pairing_match_with_packet_age():
    lm = LinkMonitor(game_id=1)
    # my bump at t=10000; their packet arrives at 10040 saying "bumped 30 ms ago"
    # -> their bump at 10010 on my clock. Checked 300 ms later: still matches.
    _cand(lm, A, -30, 10040, 30)
    assert lm.find_partner(10340, 10000) == A
    assert lm.find_partner(10340, 10000 + 170) is None      # 160 ms apart
    assert lm.find_partner(10340, 10000 - 140) == A         # 150 ms apart
    assert lm.find_partner(10340, None) is None


def test_pairing_rssi_threshold_and_strongest():
    lm = LinkMonitor(game_id=1)
    _cand(lm, A, -34, 5000, 20)
    _cand(lm, B, -40, 5000, 20)       # too weak
    _cand(lm, C, -28, 5010, 30)       # strongest, matching
    assert lm.find_partner(5050, 4980) == C
    lm2 = LinkMonitor(game_id=1)
    _cand(lm2, B, -40, 5000, 20)
    _cand(lm2, A, -35, 5000, 20)      # must be strictly > -35
    assert lm2.find_partner(5050, 4980) is None


def test_pairing_ignores_no_bump_and_stale():
    lm = LinkMonitor(game_id=1)
    _cand(lm, A, -25, 1000, proto.BUMP_NONE)
    assert lm.find_partner(1010, 1000) is None
    _cand(lm, B, -25, 1000, 0)
    assert lm.find_partner(1500, 1000) == B
    assert lm.find_partner(2500, 1000) is None       # not heard for > 1 s


def test_pair_locks_and_uses_rssi_smoothing():
    lm = LinkMonitor(game_id=1)
    _cand(lm, A, -20, 2000, 10)
    _cand(lm, A, -60, 2050, 60)       # avg rssi -40 -> no longer a candidate
    assert lm.find_partner(2060, 1990) is None
    _cand(lm, A, -20, 2100, 110)      # avg -30
    assert lm.pair(2110, 1990) == A
    assert lm.partner == A and lm.tick(2110) == ST_CONNECTED and lm.last_seen == 2100
    assert lm.on_packet(B, _pkt(1), 16, -20, 2120) == R_OTHER
    assert lm.nearby(2120) == 0


def test_candidate_table_evicts_oldest():
    lm = LinkMonitor(game_id=1, max_candidates=2)
    _cand(lm, A, -20, 0, 0)            # bumps at 0, -1000, -2000 (my clock)
    _cand(lm, B, -20, 10, 1010)
    _cand(lm, C, -20, 20, 2020)        # evicts A (heard longest ago)
    assert lm.find_partner(30, -2000) == C
    assert lm.find_partner(30, -1000) == B
    assert lm.find_partner(30, 0) is None


def test_xorshift_and_scheduler_jitter():
    r = Xorshift16(1)
    vals = set(r.next() for _ in range(1000))
    assert len(vals) == 1000 and 0 not in vals
    s = TxScheduler(seed=0x1234)
    ivs = [s.interval() for _ in range(2000)]
    assert min(ivs) == 45 and max(ivs) == 55
    assert abs(sum(ivs) / len(ivs) - 50.0) < 0.5


def test_scheduler_keeps_20hz_with_coarse_loop():
    s = TxScheduler(seed=7)
    sent = 0
    for t in range(0, 20000, 33):       # ~30 fps main loop
        if s.due(t):
            s.mark_sent(t)
            sent += 1
    assert 380 <= sent <= 420, sent
    # after a stall it restarts from now instead of bursting
    s2 = TxScheduler(seed=7)
    s2.mark_sent(0)
    s2.mark_sent(5000)
    assert 5045 <= s2.next_t <= 5055
