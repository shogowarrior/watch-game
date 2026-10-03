from finder import proto
from finder.compat import ticks_add
from finder.link import (LinkMonitor, TxScheduler, Xorshift16, R_BAD, R_PARTNER, R_OTHER,
                         R_CANDIDATE, R_DUP)

A = b"\x02\x00\x00\x00\x00\x0a"
B = b"\x02\x00\x00\x00\x00\x0b"


def _pkt(seq=0, game_id=1):
    b = proto.Beacon(game_id)
    b.seq = seq
    return b.pack_into(bytearray(250))


def _feed(lm, mac, seqs, t0=0, dt=50):
    t = t0
    for s in seqs:
        lm.on_packet(mac, _pkt(s), 16, t)
        t += dt
    return t


def test_filtering_magic_game_partner():
    lm = LinkMonitor(game_id=1)
    lm.lock(A)
    assert lm.on_packet(A, _pkt(1, game_id=2), 16, 10) == R_BAD
    bad = _pkt(1)
    bad[1] = 0
    assert lm.on_packet(A, bad, 16, 10) == R_BAD
    assert lm.on_packet(A, _pkt(1), 12, 10) == R_BAD
    assert lm.on_packet(B, _pkt(1), 16, 10) == R_OTHER
    assert lm.on_packet(A, _pkt(1), 16, 20) == R_PARTNER
    assert lm.on_packet(A, _pkt(1), 16, 30) == R_DUP
    assert (lm.n_bad, lm.n_other, lm.n_dup, lm.n_rx) == (3, 1, 1, 1)
    assert lm.last_seq == 1 and lm.last_seen == 30


def test_unlocked_is_candidate_only():
    lm = LinkMonitor(game_id=1)
    assert lm.on_packet(A, _pkt(1), 16, 0) == R_CANDIDATE
    assert lm.on_packet(B, _pkt(1), 16, 0) == R_CANDIDATE
    assert lm.on_packet(A, _pkt(1, game_id=2), 16, 0) == R_BAD
    assert lm.n_rx == 0 and lm.last_seen is None and lm.age_ms(100) is None


def test_lock_unlock_and_age():
    lm = LinkMonitor(game_id=1)
    lm.lock(A)
    assert lm.partner == A and lm.age_ms(1250) is None and lm.n_rx == 0
    t = ticks_add(0, -20)                    # across the ticks wrap
    lm.on_packet(A, _pkt(5), 16, t)
    assert lm.age_ms(30) == 50 and lm.n_rx == 1
    lm.lock(B)                               # a new partner forgets the old stream
    assert lm.partner == B and lm.n_rx == 0 and lm.last_seq == -1 and lm.age_ms(30) is None
    lm.unlock()
    assert lm.partner is None and lm.on_packet(B, _pkt(6), 16, 40) == R_CANDIDATE


def test_loss_no_loss_and_gaps():
    lm = LinkMonitor(game_id=1)
    lm.lock(A)
    _feed(lm, A, range(10))
    assert lm.loss_pct() == 0
    lm2 = LinkMonitor(game_id=1)
    lm2.lock(A)
    _feed(lm2, A, range(0, 40, 2))          # every other packet lost
    assert lm2.loss_pct() == 50


def test_loss_seq_wrap():
    lm = LinkMonitor(game_id=1)
    lm.lock(A)
    seqs = [65530, 65531, 65532, 65534, 65535, 0, 1, 3, 4]   # 2 lost across the wrap
    _feed(lm, A, seqs)
    # 8 gaps summing to 10 -> 2 lost of 10
    assert lm.loss_pct() == 20, lm.loss_pct()
    assert lm.last_seq == 4


def test_loss_window_is_last_40():
    lm = LinkMonitor(game_id=1, loss_window=40)
    lm.lock(A)
    t = _feed(lm, A, range(0, 100, 5))       # heavy loss early
    assert lm.loss_pct() == 80
    _feed(lm, A, range(96, 96 + 41), t0=t)   # then 41 clean packets
    assert lm.loss_pct() == 0


def test_seq_restart_resets_window():
    lm = LinkMonitor(game_id=1)
    lm.lock(A)
    t = _feed(lm, A, [100, 102, 104])
    assert lm.loss_pct() == 50
    _feed(lm, A, [0, 1, 2], t0=t)            # partner rebooted
    assert lm.loss_pct() == 0 and lm.last_seq == 2 and lm.n_restart == 1


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
