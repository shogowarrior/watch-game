from tests import fakes
from finder import proto

fakes.install()
from hal import radio as hr  # noqa: E402  (import failure must FAIL, not skip)


def _beacon(game_id=1, seq=0):
    b = proto.Beacon(game_id)
    b.seq = seq
    return b


def _rx_log(log):
    """poll() callback: (mac, seq, rssi, t) per valid beacon, else (mac, None, rssi, t)."""
    def cb(mac, buf, n, rssi, t):
        log.append((bytes(mac), proto.seq_of(buf) if proto.valid(buf, n) else None, rssi, t))
    return cb


def test_begin_configures_sta_and_espnow():
    fakes.install()
    r = hr.EspNowRadio(channel=11, seed=3).begin()
    assert r._sta.active() and r._sta.cfg["channel"] == 11
    assert r._sta.cfg["txpower"] == 20 and r._sta.cfg["pm"] == r._sta.PM_NONE
    e = r._e
    assert e.active() and e.cfg["rxbuf"] == 2048 and e.cfg["timeout_ms"] == 0
    assert e.peers == [hr.BCAST]
    sta = r._sta
    r.begin()                                  # re-begin: peer already exists, no raise
    assert r._sta is sta                       # same STA, so radio.mac is still the sender's MAC
    assert r.channel == 11 and r.mac == r._e.mac == r._sta.config("mac")
    try:
        hr.EspNowRadio(channel=3).begin()
        assert False, "channel 3 accepted"
    except ValueError:
        pass


def test_two_radios_exchange_beacons():
    fakes.install()
    import espnow
    ra = hr.EspNowRadio(seed=1).begin()
    rb = hr.EspNowRadio(seed=2).begin()
    assert ra.mac != rb.mac                    # each watch sends from its own STA MAC
    la, lb = [], []
    cba, cbb = _rx_log(la), _rx_log(lb)
    ba, bb = _beacon(1), _beacon(1)
    bufa, bufb = bytearray(16), bytearray(16)
    for t in range(10000, 12000, 10):
        if ra.due(t):
            ba.next_seq()
            ba.pack_into(bufa)
        ra.maybe_send(t, bufa)
        if rb.due(t):
            bb.next_seq()
            bb.pack_into(bufb)
        rb.maybe_send(t, bufb)
        ra.poll(t, callback=cba)
        rb.poll(t, callback=cbb)
    assert 36 <= len(la) <= 44 and 36 <= len(lb) <= 44, (len(la), len(lb))
    assert set(m for m, _, _, _ in la) == {rb.mac} and set(m for m, _, _, _ in lb) == {ra.mac}
    assert [s for _, s, _, _ in la] == list(range(1, len(la) + 1))   # in order, none lost
    assert lb[-1][2] == -50
    # fake RX timestamps are 0: radio substitutes `now` (all samples recent)
    assert 10000 <= la[-1][3] < 12000
    assert ra.stats()["tx"] == ra.n_tx and ra.n_tx == len([s for s in espnow.sent if s[0] == ra.mac])


def test_poll_hands_every_frame_to_callback():
    # The radio filters nothing: bad and foreign frames reach the callback too.
    fakes.install()
    import espnow
    r = hr.EspNowRadio(seed=1).begin()
    P = b"\x02\x00\x00\x00\x00\x07"
    espnow.inject(r._e, P, _beacon(2).pack_into(bytearray(16)), -40, 500)
    espnow.inject(r._e, P, b"XX" + bytes(14), -40, 500)
    espnow.inject(r._e, P, _beacon(1, 5).pack_into(bytearray(16)), -41, 490)
    got = []
    assert r.poll(500, callback=_rx_log(got)) == 3
    assert got == [(P, 0, -40, 500), (P, None, -40, 500), (P, 5, -41, 490)]
    assert r.n_rx == 3 and r.poll(500) == 0


def test_poll_drain_limit_and_timestamp_sanity():
    fakes.install()
    import espnow
    r = hr.EspNowRadio(seed=1).begin()
    pkt = _beacon(1).pack_into(bytearray(16))
    for i in range(40):
        espnow.inject(r._e, b"\x02" * 6, pkt, -60, 0)
    ts = []
    cb = lambda m, b, n, rs, t: ts.append(t)
    assert r.poll(50000, callback=cb) == 32
    assert r.poll(50000, callback=cb) == 8
    assert r.poll(50000, callback=cb) == 0
    assert ts[0] == 50000                       # 0 is 50 s old -> replaced by now
    espnow.inject(r._e, b"\x02" * 6, pkt, -60, 49980)
    espnow.inject(r._e, b"\x02" * 6, pkt, -60, 50100)  # from the future -> now
    r.poll(50000, callback=cb)
    assert ts[-2:] == [49980, 50000]
    assert r.stats()["drain_max"] == 32 and r.n_rx == 42


def test_tx_error_counted_and_jitter_schedule():
    fakes.install()
    r = hr.EspNowRadio(seed=9).begin()
    buf = _beacon(1).pack_into(bytearray(16))
    sent_t = [t for t in range(0, 10000) if r.maybe_send(t, buf)]
    assert 195 <= len(sent_t) <= 205
    gaps = [b - a for a, b in zip(sent_t, sent_t[1:])]
    assert min(gaps) >= 45 and max(gaps) <= 55

    def boom(*a):
        raise OSError("ESP_ERR_ESPNOW_NO_MEM")
    r._e.send = boom
    assert r.send(buf, 20000) is False and r.n_tx_err == 1


def test_set_rate_period_and_jitter():
    fakes.install()
    r = hr.EspNowRadio(seed=9).begin()
    buf = _beacon(1).pack_into(bytearray(16))
    t0 = 0
    for hz, lo, hi in ((10, 90, 110), (5, 180, 220), (20, 45, 55)):
        r.set_rate(hz)
        assert r.sched.period == 1000 // hz and r.sched.jitter == 100 // hz
        t0 += 100000                            # idle gap: the grid restarts at t0
        sent_t = [t for t in range(t0, t0 + 20000) if r.maybe_send(t, buf)]
        gaps = [b - a for a, b in zip(sent_t, sent_t[1:])]
        assert sent_t[0] == t0 and lo <= min(gaps) and max(gaps) <= hi, (hz, min(gaps), max(gaps))
        assert abs(len(sent_t) - 20 * hz) <= 20 * hz // 30 + 1, (hz, len(sent_t))
    r.set_rate(200)
    assert r.sched.jitter == 1                  # >= 1 ms even at 5 ms periods
    nx = r.next_due()
    assert not r.due(nx - 1) and r.due(nx)


def test_poll_before_begin_is_noop():
    r = hr.EspNowRadio(seed=1)
    assert r.poll(0) == 0


def test_send_before_begin_counts_tx_error():
    r = hr.EspNowRadio(seed=1)
    buf = _beacon(1).pack_into(bytearray(16))
    assert r.send(buf, 0) is False
    assert r.maybe_send(0, buf) is False
    assert r.n_tx_err == 2 and r.n_tx == 0


def test_rx_timestamp_masked_to_ticks_period():
    fakes.install()
    import espnow
    r = hr.EspNowRadio(seed=1).begin()
    pkt = _beacon(1).pack_into(bytearray(16))
    # driver stamps raw u32 mp_hal_ticks_ms; after 2**30 ms it exceeds ticks_ms()
    espnow.inject(r._e, b"\x02" * 6, pkt, -60, (1 << 30) + 49980)
    espnow.inject(r._e, b"\x02" * 6, pkt, -60, (3 << 30) + 49990)
    ts = []
    r.poll(50000, callback=lambda m, b, n, rs, t: ts.append(t))
    assert ts == [49980, 49990]


def test_simradio_loss_channel_and_rssi_fn():
    a = hr.SimRadio(b"\x02\x00\x00\x00\x00\x01", seed=11).begin()
    b = hr.SimRadio(b"\x02\x00\x00\x00\x00\x02", seed=12).begin()
    a.connect(b, rssi=lambda t: -40 - t // 1000, loss=0.3)
    buf = _beacon(1).pack_into(bytearray(16))
    got = []
    cb = _rx_log(got)
    for t in range(0, 20000, 5):
        a.maybe_send(t, buf)
        b.poll(t, callback=cb)
    assert 0.2 < 1 - len(got) / a.n_tx < 0.4, (len(got), a.n_tx)
    assert got[-1][0] == a.mac and got[-1][2] <= -58
    b.channel = 1
    n0 = len(got)
    for t in range(20000, 21000, 5):
        a.maybe_send(t, buf)
        b.poll(t, callback=cb)
    assert len(got) == n0                       # other channel: nothing heard


def test_pingpong_over_simradio():
    from tools import radio_pingpong as pp
    a = hr.SimRadio(b"\x02\x00\x00\x00\x00\x01", seed=21).begin()
    b = hr.SimRadio(b"\x02\x00\x00\x00\x00\x02", seed=22).begin()
    a.connect(b, rssi=-48, loss=0.1)
    ping = pp.Pinger(a, n=300, period_ms=50)
    pong = pp.Ponger(b, idle_ms=500)
    t = 0
    while not (ping.done(t) and pong.done(t)):
        ping.step(t)
        pong.step(t)
        t += 5
        assert t < 60000
    rep = ping.report()
    assert rep["sent"] == 300
    # round trip crosses the lossy link twice: ~0.9^2 = 81 %
    assert 70.0 < rep["delivery_pct"] < 90.0, rep
    assert rep["rtt_p95"] <= 5 and rep["rssi_rx"][1] == -48 and rep["rssi_at_peer"][3] == -48
    assert rep["gap_p50"] == 50 and rep["stray"] == 0
    rp = pong.report()
    assert rp["pings"] > 250 and rp["echo_fail"] == 0
    assert pp.percentile([5, 1, 3, 2, 4], 50) == 3 and pp.percentile([], 95) is None


def test_pinger_restarts_its_grid_after_a_stall():
    # A loop stall must not make two pings go out back to back.
    from tools import radio_pingpong as pp
    a = hr.SimRadio(seed=1).begin()
    ping = pp.Pinger(a, n=10, period_ms=50)
    sent = []
    for t in (0, 50, 100, 260, 261, 262, 300, 310):   # stalled 100 -> 260
        n = a.n_tx
        ping.step(t)
        if a.n_tx > n:
            sent.append(t)
    assert sent == [0, 50, 100, 260, 310], sent


def _joined_sta(channel):
    """A fake STA joined to an access point on ``channel`` (as hal/debuglink leaves it)."""
    fakes.install()
    import network
    network.set_ap("made-up-net", "made-up-pass", channel=channel, polls=1)
    sta = network.WLAN(network.STA_IF)
    sta.active(True)
    sta.connect("made-up-net", "made-up-pass")
    assert sta.isconnected()
    return sta


def test_associated_mode_keeps_the_connection_and_the_ap_channel():
    # Debug mode: ESP-NOW shares the radio with the Wi-Fi connection.
    sta = _joined_sta(13)                      # any of 1-13, not just 1/6/11
    r = hr.EspNowRadio(channel=6, seed=1).begin(sta=sta)
    assert r.associated and r.channel == 13 and sta.isconnected()
    assert not any("channel" in kw for kw in sta.sets)   # never moved off the AP's channel
    assert sta.cfg["pm"] == sta.PM_NONE and sta.cfg["txpower"] == 20
    assert r.mac == sta.config("mac") and r._e.active() and r._e.peers == [hr.BCAST]
    r.begin(channel=1)                         # re-begin stays associated
    assert r.associated and r.channel == 13 and sta.isconnected()
    buf = _beacon(1).pack_into(bytearray(16))
    assert r.send(buf, 0) and r.n_tx == 1


def test_associated_mode_rejects_a_channel_espnow_cannot_use():
    sta = _joined_sta(14)
    try:
        hr.EspNowRadio(seed=1).begin(sta=sta)
        assert False, "channel 14 accepted"
    except ValueError:
        pass


def test_normal_mode_still_drops_a_connection():
    # Normal play is unchanged: a joined STA (a notebook's) is disconnected
    # and moved to the game channel.
    sta = _joined_sta(13)
    r = hr.EspNowRadio(channel=11, seed=1)
    r._sta = sta
    r.begin()
    assert not r.associated and not sta.isconnected() and sta.cfg["channel"] == 11
