"""tools/fake_watches.py: two simulated watches send real debug-mode datagrams
to a localhost UDP socket. CPython only (real sockets and threads)."""

import sys

from tests import Skip


def _receiver():
    """A localhost UDP socket and a thread that drains it into a list."""
    if sys.implementation.name != "cpython":
        raise Skip("real UDP sockets: CPython only")
    import socket
    import threading
    rx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    rx.bind(("127.0.0.1", 0))
    rx.settimeout(0.5)
    got = []

    def drain():
        while True:
            try:
                got.append(rx.recvfrom(4096)[0])
            except socket.timeout:
                return
    th = threading.Thread(target=drain)
    th.start()
    return rx, th, got


def test_fake_watches_send_what_real_watches_send():
    rx, th, got = _receiver()
    import json
    from tools import fake_watches as fw
    from app.telemetry import DGRAM_MAX
    from finder.render_params import from_dict, validate
    try:
        st = fw.run(port=rx.getsockname()[1], seconds=40, speed=100)
        th.join()
    finally:
        rx.close()
    assert st["A"]["tx"] > 300 and st["B"]["tx"] > 300 and st["A"]["tx_err"] == 0, st
    assert len(got) > 400, len(got)
    recs = [json.loads(d.decode()) for d in got]
    for d, r in zip(got, recs):
        assert len(d) <= DGRAM_MAX
        assert r["dev"] in ("A", "B") and isinstance(r["t"], int), r
        assert r["mac"] == ("10000a" if r["dev"] == "A" else "10000b")
    for r in recs:
        if r["ev"] == "rp":
            assert not validate(from_dict(r["p"])) and r["on"] in (True, False)
            assert "ch" in r and r["ch"] is None           # no Wi-Fi channel on the fakes
    s = [r for r in recs if r["ev"] == "s"]
    for k in ("rssi", "rssi_f", "d_est", "d_lo", "d_hi", "zone", "trend", "steps", "act",
              "ui", "sub", "batt_pct", "seq", "peer_seq", "rx", "loss"):
        assert k in s[-1], k
    # they pair (the side key at the runes) and A walks away: the hunt shows zones
    assert [r for r in recs if r["ev"] == "btn" and r["dev"] == "A"]
    ui_a = set(r["ui"] for r in s if r["dev"] == "A")
    assert "PAIRING" in ui_a and ("WARM" in ui_a or "NEAR" in ui_a), ui_a


def test_fake_watches_stop_at_once():
    rx, th, got = _receiver()
    import threading
    from tools import fake_watches as fw
    stop = threading.Event()
    stop.set()
    try:
        st = fw.run(port=rx.getsockname()[1], stop=stop)
        th.join()
    finally:
        rx.close()
    assert st["A"]["tx"] == 0 and st["B"]["tx"] == 0 and got == []


def test_fake_watches_stop_mid_run_returns_within_a_step():
    rx, th, got = _receiver()
    import threading
    import time
    from tools import fake_watches as fw
    stop = threading.Event()
    out = {}
    t = threading.Thread(target=lambda: out.update(fw.run(port=rx.getsockname()[1], stop=stop)))
    try:
        t.start()
        time.sleep(0.5)                      # real time (speed 1): a few records each
        t0 = time.monotonic()
        stop.set()
        t.join(2)
        took = time.monotonic() - t0
        th.join()
    finally:
        rx.close()
    assert not t.is_alive() and took < 0.5, took   # one 50 ms step, plus the final flush
    assert out["A"]["tx"] > 0 and out["B"]["tx"] > 0, out
