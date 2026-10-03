"""tools/debug_server.py: the UDP -> SSE relay, /debug/status, the session
log and the static files, over real localhost sockets (free ports, short
timeouts). No Wi-Fi and no watches.

A CPython host tool (threads, http.server): skipped under MicroPython."""

import json
import os

from tests import Skip

T = 2.0                    # s: the longest any wait here may take
REC_A = {"dev": "A", "mac": "a1b2c3", "t": 123, "ev": "s", "rssi": -61, "d_est": 4.2}
REC_B = {"dev": "B", "mac": "0d0e0f", "t": 77, "ev": "btn", "k": "short"}


def _ds():
    try:
        import tools.debug_server as ds         # CPython host tool only
    except ImportError:
        raise Skip("tools/debug_server.py is a CPython host tool")
    return ds


def _start(ds, tmp, log=None):
    """A started DebugServer on free ports, serving ``tmp``/www."""
    www = os.path.join(tmp, "www")
    os.makedirs(os.path.join(www, "mpy"))
    with open(os.path.join(www, "local.html"), "w") as f:
        f.write("<p>page</p>")
    open(os.path.join(www, "mpy", "micropython.mjs"), "w").close()
    srv = ds.DebugServer(www, 0, 0, log)
    srv.start()
    return srv


def _get(port, path):
    """-> (status, headers, body) of ``GET path``."""
    import http.client
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=T)
    try:
        c.request("GET", path)
        r = c.getresponse()
        return r.status, r, r.read()
    finally:
        c.close()


def _status(port):
    return json.loads(_get(port, "/debug/status")[2])


def _events(port):
    """An open /events stream (connection, response), already subscribed."""
    import http.client
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=T)
    c.request("GET", "/events")
    r = c.getresponse()
    assert r.status == 200 and r.getheader("Content-Type") == "text/event-stream"
    assert r.getheader("Cache-Control") == "no-store"
    assert r.readline() == b"retry: 2000\n" and r.readline() == b"\n"   # sent once subscribed
    return c, r


def _next_data(r):
    """The next ``data:`` payload on the stream, decoded."""
    while True:
        line = r.readline()
        assert line, "the stream ended"
        if line.startswith(b"data: "):
            assert r.readline() == b"\n"
            return json.loads(line[6:])


def _send(port, *datagrams):
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        for d in datagrams:
            s.sendto(d if isinstance(d, bytes) else json.dumps(d).encode(), ("127.0.0.1", port))
    finally:
        s.close()


def _wait(fn):
    """Polls ``fn`` until it is true; fails after ``T``."""
    import time
    end = time.time() + T
    while not fn():
        assert time.time() < end, "timed out"
        time.sleep(0.01)


def test_parse_accepts_records_only():
    ds = _ds()
    assert ds.parse(json.dumps(REC_A).encode()) == REC_A
    for bad in (b"", b"\xff\xfe", b"not json", b"[1, 2]", b'"A"', b'{"ev": "s"}',
                b'{"dev": "A"}', b'{"dev": "", "ev": "s"}', b'{"dev": 1, "ev": "s"}',
                b'{"dev": "A", "ev": "s", "rssi": NaN}', b"[" * 5000,
                b'{"dev": "A", "ev": "s", "d_est": 1e400}',     # overflows to inf
                b'{"dev": "A", "ev": "s", "d_est": -1e400}'):
        assert ds.parse(bad) is None, bad[:30]
    assert ds.parse(b'{"dev": "A", "ev": "s", "d_est": 1e-400}')["d_est"] == 0.0


def test_overflowing_number_is_bad_not_relayed():
    """1e400 would be relayed as Infinity, which the page's JSON.parse rejects."""
    ds = _ds()
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        log = os.path.join(tmp, "debug-x.jsonl")
        b = ds.Bridge(47268, log)
        q = b.subscribe()
        assert b.handle(b'{"dev": "A", "ev": "s", "d_est": 1e400}', "10.0.0.2", 5) is None
        line = b.handle(json.dumps(REC_A).encode(), "10.0.0.2", 6)
        b.close()
        st = b.status()
        assert (st["packets"], st["bad"]) == (1, 1), st
        assert q.get_nowait() == line and q.get_nowait() is None     # only the good one
        with open(log) as f:
            lines = f.read().splitlines()
        assert lines == [line] and "Infinity" not in line
        json.loads(line, parse_constant=ds._reject_constant)          # strict JSON


def test_relay_to_events_status_and_log():
    ds = _ds()
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        log = os.path.join(tmp, "logs", "debug-x.jsonl")
        srv = _start(ds, tmp, log)
        try:
            c, r = _events(srv.http_port)
            t0 = ds.now_ms()
            _send(srv.udp_port, b"not a record", REC_A, REC_B, REC_A)
            msgs = [_next_data(r) for _ in range(3)]
            assert [m["rec"] for m in msgs] == [REC_A, REC_B, REC_A], msgs
            assert all(m["src"] == "127.0.0.1" and t0 <= m["rx"] <= ds.now_ms() for m in msgs)
            st = _status(srv.http_port)
            assert st["ok"] is True and st["udp_port"] == srv.udp_port, st
            assert (st["clients"], st["packets"], st["bad"], st["log"]) == (1, 3, 1, log), st
            assert st["watches"] == {
                "A": {"src": "127.0.0.1", "last_rx": msgs[2]["rx"], "n": 2},
                "B": {"src": "127.0.0.1", "last_rx": msgs[1]["rx"], "n": 1}}, st
            with open(log) as f:          # the same lines the page got, one per record
                assert [json.loads(x) for x in f.read().splitlines()] == msgs
            r.close()
            c.close()
        finally:
            srv.stop()


def test_every_tab_gets_every_record():
    ds = _ds()
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        srv = _start(ds, tmp)
        try:
            tabs = [_events(srv.http_port) for _ in range(2)]
            assert _status(srv.http_port)["clients"] == 2
            _send(srv.udp_port, REC_B)
            for _, r in tabs:
                assert _next_data(r)["rec"] == REC_B
            for c, r in tabs:
                r.close()
                c.close()
        finally:
            srv.stop()


def test_static_files_are_never_cached():
    ds = _ds()
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        srv = _start(ds, tmp)
        try:
            assert srv.http.server_address[0] == "127.0.0.1"   # the page is not on the LAN
            code, r, body = _get(srv.http_port, "/local.html")
            assert (code, body, r.getheader("Cache-Control")) == (200, b"<p>page</p>", "no-store")
            code, r, _ = _get(srv.http_port, "/mpy/micropython.mjs")   # a module script
            assert code == 200 and r.getheader("Content-Type") == "text/javascript"
            code, r, _ = _get(srv.http_port, "/debug/status?t=1")
            assert code == 200 and r.getheader("Cache-Control") == "no-store"
            assert r.getheader("Content-Type") == "application/json"
        finally:
            srv.stop()


def test_log_only_when_asked_and_only_with_records():
    ds = _ds()
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        srv = _start(ds, tmp)                    # --no-log
        try:
            _send(srv.udp_port, REC_A)
            _wait(lambda: _status(srv.http_port)["packets"] == 1)
            assert _status(srv.http_port)["log"] is None
        finally:
            srv.stop()
        assert sorted(os.listdir(tmp)) == ["www"]
    with tempfile.TemporaryDirectory() as tmp:
        log = os.path.join(tmp, "logs", "debug-x.jsonl")
        srv = _start(ds, tmp, log)
        srv.stop()                               # nothing heard: no empty file
        assert not os.path.exists(os.path.dirname(log))


def test_nothing_is_handled_after_close():
    ds = _ds()
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        log = os.path.join(tmp, "debug-x.jsonl")
        b = ds.Bridge(47268, log)
        assert b.handle(json.dumps(REC_A).encode(), "10.0.0.2", 5) is not None
        b.close()                                # a datagram read just before stop()
        assert b.handle(json.dumps(REC_B).encode(), "10.0.0.3", 6) is None
        assert b.handle(None, "", 7) is None
        st = b.status()
        assert (st["packets"], st["bad"], sorted(st["watches"])) == (1, 0, ["A"]), st
        assert b._file is None                   # the log was not reopened
        with open(log) as f:
            assert len(f.read().splitlines()) == 1


def test_closed_tab_is_dropped():
    ds = _ds()
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        srv = _start(ds, tmp)
        try:
            c, r = _events(srv.http_port)
            r.close()
            c.close()

            def gone():                          # the next writes find the tab closed
                _send(srv.udp_port, REC_A)
                return _status(srv.http_port)["clients"] == 0
            _wait(gone)
        finally:
            srv.stop()


def test_stop_ends_streams_and_frees_the_ports():
    ds = _ds()
    import socket
    import tempfile
    import time
    with tempfile.TemporaryDirectory() as tmp:
        srv = _start(ds, tmp)
        c, r = _events(srv.http_port)
        t0 = time.time()
        srv.stop()
        assert r.readline() == b""               # the stream ended
        assert time.time() - t0 < T
        r.close()
        c.close()
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.bind(("0.0.0.0", srv.udp_port))        # the UDP port is free again
        s.close()


def test_busy_port_is_a_plain_message():
    """Another bridge holds a port: the message names that port and what to do."""
    ds = _ds()
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        other = _start(ds, tmp)                  # another debug_server.py, already running
        try:
            www = os.path.join(tmp, "www")
            try:                                 # the watches' UDP port is taken
                ds.main(["--root", www, "--http-port", "0", "--no-log",
                         "--udp-port", str(other.udp_port)])
                assert False, "main() should have stopped"
            except SystemExit as e:
                msg = str(e)
                assert "UDP port %d" % other.udp_port in msg and "is in use" in msg, msg
                assert "Is another debug_server.py" in msg, msg
                assert "UDP port %d" % ds.UDP_PORT in msg, msg   # where the watches send
                assert "--http-port" not in msg and "page port" not in msg, msg
            try:                                 # the page port is taken
                ds.main(["--root", www, "--http-port", str(other.http_port), "--no-log",
                         "--udp-port", "0"])
                assert False, "main() should have stopped"
            except SystemExit as e:
                msg = str(e)
                assert "page port %d is in use" % other.http_port in msg, msg
                assert "Is another" in msg and "--http-port" in msg, msg
                assert "UDP" not in msg, msg
        finally:
            other.stop()
        try:
            ds.main(["--root", os.path.join(tmp, "nope")])
            assert False, "main() should have stopped"
        except SystemExit as e:
            assert "tools/build_sim.py" in str(e), e


def test_busy_port_frees_the_other_port():
    """A failed start closes the UDP port it had already opened."""
    ds = _ds()
    import socket
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        other = _start(ds, tmp)
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.bind(("0.0.0.0", 0))
            free = s.getsockname()[1]
            s.close()
            try:
                ds.DebugServer(tmp, other.http_port, free)
                assert False, "the page port is taken"
            except OSError as e:
                assert isinstance(e, ds.PortError) and not e.udp and e.in_use, e
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.bind(("0.0.0.0", free))            # not left open by the failed start
            s.close()
        finally:
            other.stop()


def test_other_port_errors_are_plain_too():
    """A port that cannot be opened for another reason: no "in use", no
    "is another running"; the UDP advice still keeps the watches' port."""
    ds = _ds()
    import errno
    denied = OSError(errno.EACCES, "Permission denied")
    msg = ds.port_message(ds.PortError(denied, "page port 80", udp=False))
    assert msg.startswith("Could not start the bridge: page port 80 could not be opened "
                          "(Permission denied)."), msg
    assert "--http-port" in msg and "Is another" not in msg, msg
    msg = ds.port_message(ds.PortError(denied, "UDP port 9, where the watches send,", udp=True))
    assert "could not be opened" in msg and "Is another" not in msg, msg
    assert "UDP port %d" % ds.UDP_PORT in msg and "--http-port" not in msg, msg


def test_banner_warns_when_the_watches_cannot_reach_the_bridge():
    ds = _ds()
    import types

    def srv(udp_port, log=None):
        return types.SimpleNamespace(http_port=8765, udp_port=udp_port,
                                     bridge=types.SimpleNamespace(log=log))
    lines = ds.banner(srv(ds.UDP_PORT), False)
    assert "http://localhost:8765/local.html" in lines[0], lines
    assert not any("will not reach" in x for x in lines), lines
    assert not any("Saving" in x for x in lines), lines           # --no-log
    lines = ds.banner(srv(47299, os.path.join(ds.ROOT, "logs", "debug-x.jsonl")), True)
    warn = [x for x in lines if "will not reach" in x]
    assert len(warn) == 1 and "UDP port %d" % ds.UDP_PORT in warn[0], lines
    assert "Saving what the watches send to logs/debug-x.jsonl" in lines, lines
    assert lines[-1] == "Press Ctrl-C to stop.", lines


def test_demo_runs_the_fake_watches():
    ds = _ds()
    import socket
    import sys
    import tempfile
    import types
    seen = []

    def run(host="127.0.0.1", port=47268, seconds=None, speed=1.0, stop=None):
        seen.append((host, port))
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        while not stop.is_set():
            s.sendto(json.dumps(REC_B).encode(), (host, port))
            stop.wait(0.02)
        s.close()
    fake = types.ModuleType("tools.fake_watches")
    fake.run = run
    saved = sys.modules.get("tools.fake_watches")
    sys.modules["tools.fake_watches"] = fake
    try:
        with tempfile.TemporaryDirectory() as tmp:
            srv = _start(ds, tmp)
            try:
                t = ds.start_demo(srv.udp_port, srv.bridge.done)
                _wait(lambda: "B" in _status(srv.http_port)["watches"])
                assert seen == [("127.0.0.1", srv.udp_port)]
            finally:
                srv.stop()
            t.join(T)
            assert not t.is_alive()              # stop() also stops the demo
    finally:
        if saved is None:
            del sys.modules["tools.fake_watches"]
        else:
            sys.modules["tools.fake_watches"] = saved


def test_defaults_and_log_name():
    ds = _ds()
    import re
    from hal.debuglink import DEBUG_PORT        # what deploy.py writes into /debug
    assert ds.UDP_PORT == DEBUG_PORT
    a = ds.parse_args([])
    assert (a.http_port, a.udp_port, a.no_log, a.demo) == (8765, DEBUG_PORT, False, False)
    assert a.root == os.path.join(ds.ROOT, "dist", "sim")
    a = ds.parse_args(["--http-port", "8799", "--udp-port", "47299", "--no-log", "--demo"])
    assert (a.http_port, a.udp_port, a.no_log, a.demo) == (8799, 47299, True, True)
    shown = ds._shown(ds.log_path())             # as /debug/status shows it
    assert re.match(r"logs/debug-\d{8}-\d{6}\.jsonl$", shown), shown
