from tests import fakes
from finder import proto
from finder.link import LinkMonitor, R_OTHER, ST_CONNECTED

try:
    fakes.install()
    from hal import radio as hr
except ImportError:  # hal/ not deployed (e.g. WebAssembly runner copies no hal/)
    hr = None
    print("test_hal_radio: hal.radio not importable here, skipped")


def _beacon(game_id=1, seq=0):
    b = proto.Beacon(game_id)
    b.seq = seq
    return b


def test_begin_configures_sta_and_espnow():
    if hr is None:
        return
    fakes.install()
    r = hr.EspNowRadio(channel=11, seed=3).begin()
    assert r._sta.active() and r._sta.cfg["channel"] == 11
    assert r._sta.cfg["txpower"] == 20 and r._sta.cfg["pm"] == r._sta.PM_NONE
    e = r._e
    assert e.active() and e.cfg["rxbuf"] == 2048 and e.cfg["timeout_ms"] == 0
    assert e.peers == [hr.BCAST]
    r.begin()                                  # re-begin: peer already exists, no raise
    assert r.channel == 11 and r.mac == r._sta.config("mac")
    try:
        hr.EspNowRadio(channel=3).begin()
        assert False, "channel 3 accepted"
    except ValueError:
        pass


def test_two_radios_exchange_beacons():
    if hr is None:
        return
    fakes.install()
    import espnow
    ra = hr.EspNowRadio(seed=1).begin()
    rb = hr.EspNowRadio(seed=2).begin()
    la = LinkMonitor(game_id=1)
    lb = LinkMonitor(game_id=1)
    la.lock(rb._e.mac)
    lb.lock(ra._e.mac)
    ba, bb = _beacon(1), _beacon(1)
    bufa, bufb = bytearray(16), bytearray(16)
    for t in range(10000, 12000, 10):
        if ra.sched.due(t):
            ba.next_seq()
            ba.rssi_last = la.last_rssi
            ba.pack_into(bufa)
        ra.maybe_send(t, bufa)
        if rb.sched.due(t):
            bb.next_seq()
            bb.pack_into(bufb)
        rb.maybe_send(t, bufb)
        ra.poll(t, monitor=la)
        rb.poll(t, monitor=lb)
        la.tick(t)
        lb.tick(t)
    assert 36 <= la.n_rx <= 44 and 36 <= lb.n_rx <= 44, (la.n_rx, lb.n_rx)
    assert la.loss() == 0.0 and la.state == ST_CONNECTED and lb.state == ST_CONNECTED
    assert lb.last_rssi == -50 and lb.peer.rssi_last == -50
    # fake RX timestamps are 0: radio substitutes `now` (all samples recent)
    assert 10000 <= la.s_t[la.idx(la.total - 1)] < 12000
    assert ra.stats()["tx"] == ra.n_tx and ra.n_tx == len([s for s in espnow.sent if s[0] == ra._e.mac])


def test_filtering_other_game_and_other_mac():
    if hr is None:
        return
    fakes.install()
    import espnow
    r = hr.EspNowRadio(seed=1).begin()
    lm = LinkMonitor(game_id=1)
    P = b"\x02\x00\x00\x00\x00\x07"
    lm.lock(P)
    espnow.inject(r._e, P, _beacon(2).pack_into(bytearray(16)), -40, 500)
    espnow.inject(r._e, P, b"XX" + bytes(14), -40, 500)
    espnow.inject(r._e, b"\x02\x00\x00\x00\x00\x08", _beacon(1).pack_into(bytearray(16)), -40, 500)
    espnow.inject(r._e, P, _beacon(1, 5).pack_into(bytearray(16)), -41, 490)
    got = []
    assert r.poll(500, monitor=lm, callback=lambda m, b, n, rs, t: got.append((m, n, rs, t))) == 4
    assert lm.n_bad == 2 and lm.n_other == 1 and lm.n_rx == 1
    assert lm.s_rssi[0] == -41 and lm.s_t[0] == 490 and lm.s_seq[0] == 5
    assert got[-1] == (P, 16, -41, 490)


def test_poll_drain_limit_and_timestamp_sanity():
    if hr is None:
        return
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
    if hr is None:
        return
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


def test_poll_before_begin_is_noop():
    if hr is None:
        return
    r = hr.EspNowRadio(seed=1)
    assert r.poll(0) == 0


def test_send_before_begin_counts_tx_error():
    if hr is None:
        return
    r = hr.EspNowRadio(seed=1)
    buf = _beacon(1).pack_into(bytearray(16))
    assert r.send(buf, 0) is False
    assert r.maybe_send(0, buf) is False
    assert r.n_tx_err == 2 and r.n_tx == 0


def test_rx_timestamp_masked_to_ticks_period():
    if hr is None:
        return
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
    if hr is None:
        return
    a = hr.SimRadio(b"\x02\x00\x00\x00\x00\x01", seed=11).begin()
    b = hr.SimRadio(b"\x02\x00\x00\x00\x00\x02", seed=12).begin()
    a.connect(b, rssi=lambda t: -40 - t // 1000, loss=0.3)
    buf = _beacon(1).pack_into(bytearray(16))
    lb = LinkMonitor(game_id=1)
    lb.lock(a.mac)
    seq = 0
    for t in range(0, 20000, 5):
        if a.sched.due(t):
            seq += 1
            buf[4] = seq & 0xFF
            buf[5] = seq >> 8
        a.maybe_send(t, buf)
        b.poll(t, monitor=lb)
    assert 0.2 < 1 - lb.n_rx / a.n_tx < 0.4, (lb.n_rx, a.n_tx)
    assert lb.last_rssi <= -58
    b.channel = 1
    n0 = lb.n_rx
    for t in range(20000, 21000, 5):
        a.maybe_send(t, buf)
        b.poll(t, monitor=lb)
    assert lb.n_rx == n0
    buf[4] = (seq + 1) & 0xFF
    buf[5] = (seq + 1) >> 8
    b.inject(a.mac, buf, -33, 21000)
    b.inject(b"\x02" * 6, buf, -33, 21000)
    b.poll(21000, monitor=lb)
    assert lb.last_rssi == -33 and lb.n_other == 1


def test_pingpong_over_simradio():
    if hr is None:
        return
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
