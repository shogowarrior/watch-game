"""tools/debug_server.py: the UDP -> SSE relay, /debug/status, the session
log and the static files, over real localhost sockets (free ports, short
timeouts); the USB serial reader on pseudo-terminals. No Wi-Fi, no serial
port and no watches.

A CPython host tool (threads, http.server, pty): skipped under MicroPython,
and the serial tests where there is no pty."""

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


def _www(tmp):
    """``tmp``/www with a page and a module script in it."""
    www = os.path.join(tmp, "www")
    os.makedirs(os.path.join(www, "mpy"))
    with open(os.path.join(www, "local.html"), "w") as f:
        f.write("<p>page</p>")
    open(os.path.join(www, "mpy", "micropython.mjs"), "w").close()
    return www


def _start(ds, tmp, log=None, serial=None):
    """A started DebugServer on free ports, serving ``tmp``/www."""
    srv = ds.DebugServer(_www(tmp), 0, 0, log, serial)
    srv.start()
    return srv


def _serial_ds():
    """The bridge module where serial ports can be faked (pty, termios)."""
    ds = _ds()
    try:
        import pty  # noqa: F401
    except ImportError:
        raise Skip("needs pseudo-terminals (macOS or Linux)")
    if ds.termios is None:
        raise Skip("needs termios (macOS or Linux)")
    return ds


def _pty():
    """A pseudo-terminal standing in for a watch on USB -> (the watch's end:
    its master fd, the port's path). The port is closed and keeps the
    terminal settings the OS gave it (echo on), as a port nobody opened yet."""
    import pty
    m, s = pty.openpty()
    path = os.ttyname(s)
    os.close(s)
    return m, path


def _framed(rec):
    """A record as the watch's USB link writes it."""
    return b"\x1e" + json.dumps(rec, separators=(",", ":")).encode() + b"\n"


class _Fast:
    """Shorter serial retry and rescan periods for the duration of a test."""

    def __init__(self, ds):
        self.ds = ds

    def __enter__(self):
        self.saved = (self.ds.REOPEN_S, self.ds.SCAN_S)
        self.ds.REOPEN_S = self.ds.SCAN_S = 0.05

    def __exit__(self, *exc):
        self.ds.REOPEN_S, self.ds.SCAN_S = self.saved


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
            assert st["serial"] == {}, st                 # no --serial
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


def test_knocks_are_judged_relayed_logged_and_counted():
    """Each spike's judgement (tools/knocks.py) goes to the streams and the log
    as a ``knock`` line from ``bridge``, to the terminal (``news``) and into
    /debug/status; ``tick`` judges a silent watch's spikes and ``close`` the
    ones still waiting."""
    ds = _ds()
    import tempfile
    from tools.knocks import QUIET_MS, clock
    with tempfile.TemporaryDirectory() as tmp:
        log = os.path.join(tmp, "debug-x.jsonl")
        b = ds.Bridge(47268, log)
        q = b.subscribe()

        def rec(dev, t, ev="s", **kw):
            d = dict({"dev": dev, "mac": "a1b2c3", "t": t, "ev": ev}, **kw)
            if ev == "s":
                d["ptap"] = None      # B's spikes, as A hears them: none
            return json.dumps(d).encode()
        rx = 1_790_000_000_000
        b.handle(rec("A", 1000), "ttyUSB0", rx)
        b.handle(rec("A", 1050, "tap", ok=True), "ttyUSB0", rx + 200)
        for i in range(1, 14):        # A's records reach 2.5 s past its spike, never hearing B
            b.handle(rec("A", 1000 + 200 * i), "ttyUSB0", rx + 200 * i)
        assert b.news() == ["%s  knock, watch A  Not matched    B has sent nothing yet." % (
            clock(rx + 50))] and b.news() == []
        b.handle(rec("A", 3700, "tap", ok=False), "ttyUSB0", rx + 2700)     # judged at once
        assert [x.split("  ")[2] for x in b.news()] == ["Set aside"]
        b.handle(rec("A", 3800, "tap", ok=True), "ttyUSB0", rx + 2800)      # then A goes silent
        b.tick(rx + 2800 + QUIET_MS - 1)
        assert b.news() == []
        b.tick(rx + 2800 + QUIET_MS)
        assert len(b.news()) == 1
        b.handle(rec("A", 4000, "tap", ok=True), "ttyUSB0", rx + 3000)
        st = b.status()
        assert st["packets"] == 18 and st["knocks"]["totals"]["A"]["alone"] == 2, st
        assert [v["v"] for v in st["knocks"]["recent"]] == ["alone", "buzz", "alone"], st
        b.close()                     # the one still waiting
        b.tick(rx + 99999)            # nothing once closed
        lines = []
        while True:
            line = q.get_nowait()
            if line is None:
                break
            lines.append(json.loads(line))
        knocks = [m for m in lines if "knock" in m]
        assert [m["src"] for m in knocks] == ["bridge"] * 4, knocks
        assert [(m["knock"]["dev"], m["knock"]["v"]) for m in knocks] == [
            ("A", "alone"), ("A", "buzz"), ("A", "alone"), ("A", "alone")]
        assert knocks[0]["rx"] == rx + 2600 and knocks[0]["knock"]["at"] == rx + 50
        assert knocks[3]["knock"]["n"]["felt"] == 4
        assert [m["src"] for m in lines if "rec" in m] == ["ttyUSB0"] * 18
        with open(log) as f:          # the log holds the same lines
            assert [json.loads(x) for x in f.read().splitlines()] == lines
        assert b.status()["knocks"]["totals"]["A"]["buzz"] == 1


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

    def srv(udp_port, log=None, ports=None):
        return types.SimpleNamespace(http_port=8765, udp_port=udp_port, ports=ports,
                                     bridge=types.SimpleNamespace(log=log))
    lines = ds.banner(srv(ds.UDP_PORT), False)
    assert "http://localhost:8765/local.html" in lines[0], lines
    assert not any("will not reach" in x for x in lines), lines
    assert not any("Saving" in x for x in lines), lines           # --no-log
    assert not any("USB" in x for x in lines), lines              # no --serial
    lines = ds.banner(srv(47299, os.path.join(ds.ROOT, "logs", "debug-x.jsonl")), True)
    warn = [x for x in lines if "will not reach" in x]
    assert len(warn) == 1 and "UDP port %d" % ds.UDP_PORT in warn[0], lines
    assert "Saving what the watches send to logs/debug-x.jsonl" in lines, lines
    assert "Listening for the watches on UDP port 47299 (two simulated watches are sending)." \
        in lines, lines
    assert lines[-1] == "Press Ctrl-C to stop.", lines
    # --serial: which ports, and that they are held
    lines = ds.banner(srv(ds.UDP_PORT, ports=ds.SerialPorts(None, [])), False)
    assert "Reading every USB serial port plugged in, and looking for new ones every 2 s." \
        in lines, lines
    assert any("stop it (Ctrl-C) before deploy.py" in x for x in lines), lines
    lines = ds.banner(srv(ds.UDP_PORT, ports=ds.SerialPorts(
        None, ["/dev/tty.usbserial-1", "/dev/ttyUSB0"])), False)
    assert "Reading the watches on USB serial ports cu.usbserial-1, ttyUSB0." in lines, lines
    lines = ds.banner(srv(ds.UDP_PORT, ports=ds.SerialPorts(None, ["/dev/pts/3", "/dev/pts/4"])),
                      True)                                       # --demo --serial
    assert ("Reading the watches on USB serial ports pts/3, pts/4 (two simulated watches are "
            "writing).") in lines, lines
    assert "Listening for the watches on UDP port %d." % ds.UDP_PORT in lines, lines


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
    assert a.root == os.path.join(ds.ROOT, "dist", "sim") and a.serial is None
    a = ds.parse_args(["--http-port", "8799", "--udp-port", "47299", "--no-log", "--demo"])
    assert (a.http_port, a.udp_port, a.no_log, a.demo) == (8799, 47299, True, True)
    assert ds.parse_args(["--serial"]).serial == []                  # find the ports
    a = ds.parse_args(["--serial", "/dev/ttyUSB0", "/dev/tty.usbserial-1", "--no-log"])
    assert a.serial == ["/dev/ttyUSB0", "/dev/tty.usbserial-1"] and a.no_log, a
    a = ds.parse_args(["--serial", "--demo"])
    assert (a.serial, a.demo) == ([], True)
    import contextlib
    import io
    try:
        with contextlib.redirect_stderr(io.StringIO()) as err:
            ds.parse_args(["--demo", "--serial", "/dev/ttyUSB0"])
        assert False, "--demo makes its own ports"
    except SystemExit:
        assert "give it no PORT" in err.getvalue(), err.getvalue()
    shown = ds._shown(ds.log_path())             # as /debug/status shows it
    assert re.match(r"logs/debug-\d{8}-\d{6}\.jsonl$", shown), shown


def test_launch_config_runs_the_bridge_on_usb():
    ds = _ds()
    with open(os.path.join(ds.ROOT, ".claude", "launch.json")) as f:
        web = [c for c in json.load(f)["configurations"] if c["name"] == "web-sim"][0]
    args = web["runtimeArgs"]
    assert args[0] == "tools/debug_server.py" and "--serial" in args, args
    a = ds.parse_args(args[1:])
    assert a.serial == [] and a.http_port == web["port"] == 8765, args


def test_piped_output_shows_each_line_as_it_is_printed():
    """Run as a script with stdout on a pipe (the web-sim preview, tee), the
    banner and the "Heard watch" lines arrive while the bridge runs, not when
    it stops: Python block-buffers a pipe unless the entry point says not to."""
    ds = _ds()
    import select
    import signal
    import subprocess
    import sys
    import tempfile
    import time
    if os.name != "posix":
        raise Skip("select() on a pipe: macOS or Linux")
    env = dict(os.environ)
    env.pop("PYTHONUNBUFFERED", None)
    with tempfile.TemporaryDirectory() as tmp:
        p = subprocess.Popen(
            [sys.executable, os.path.join(ds.ROOT, "tools", "debug_server.py"), "--root",
             _www(tmp), "--http-port", "0", "--udp-port", "0", "--no-log", "--demo"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
        got = []
        try:
            end = time.time() + 20
            while not any(ln.startswith("Heard watch") for ln in got):
                left = end - time.time()
                assert left > 0, got
                ready = select.select([p.stdout], [], [], left)[0]
                assert ready, got
                ln = p.stdout.readline().decode()
                assert ln and p.poll() is None, (got, p.poll())   # arrived while it runs
                got.append(ln)
        finally:
            p.send_signal(signal.SIGINT)
            try:
                out, err = p.communicate(timeout=10)
            except subprocess.TimeoutExpired:
                p.kill()
                out, err = p.communicate()
    assert got[0].startswith("Debug bridge running."), got
    assert "Press Ctrl-C to stop.\n" in got, got
    assert "Stopped. Heard" in out.decode(), (out, err)


# ---- USB serial ------------------------------------------------------------------------

def test_lines_split_trim_and_limit():
    ds = _ds()
    lines = ds.Lines()
    rec = _framed(REC_A)
    assert lines.feed(b'"t": 9, "ev": "s"}\r\n' + rec[:15]) == []    # opened mid-line: dropped
    assert lines.feed(rec[15:-1] + b"\r\nboot ok\n\r\n\n" + b"x") == [rec[:-1], b"boot ok"]
    big = b"y" * (ds.LINE_MAX + 1)
    assert lines.feed(b"\n" + big[:5000]) == [b"x"]
    assert lines.feed(big[5000:]) == [None]                         # too long: once, no \n needed
    assert lines.feed(b"the rest of it\nnext\n") == [b"next"]
    exact = b"z" * ds.LINE_MAX
    assert lines.feed(exact + b"\n" + big + b"\nafter\n") == [exact, None, b"after"]
    first = ds.Lines()                                               # a long cut first line
    assert first.feed(big) == [] and first.feed(b"tail\nok\n") == [b"ok"]


def test_port_lines_are_records_or_text():
    """A 0x1E line is checked and relayed exactly like a datagram; any other line
    is text for the page's raw log and the session log."""
    ds = _ds()
    import tempfile
    port = "cu.usbserial-022152D1"
    with tempfile.TemporaryDirectory() as tmp:
        log = os.path.join(tmp, "debug-x.jsonl")
        b = ds.Bridge(47268, log)
        q = b.subscribe()
        rec = b.port_line(port, _framed(REC_A)[:-1], 5)
        txt = b.port_line(port, b"Traceback (most recent call last): \xe9", 6)
        for bad in (b"\x1e{not json", b'\x1e{"dev": "A", "ev": "s", "d_est": 1e400}',
                    b'\x1e{"ev": "s"}', b"\x1e", None):                # None: over LINE_MAX
            assert b.port_line(port, bad, 7) is None, bad
        wifi = b.handle(json.dumps(REC_B).encode(), "10.0.0.2", 8)      # the Wi-Fi link, alongside
        b.close()
        assert b.port_line(port, b"after close", 9) is None
        assert json.loads(rec) == {"src": port, "rx": 5, "rec": REC_A}
        assert json.loads(txt) == {"src": port, "rx": 6,
                                   "line": "Traceback (most recent call last): �"}
        st = b.status()
        assert (st["packets"], st["bad"]) == (2, 5), st
        assert st["watches"] == {"A": {"src": port, "last_rx": 5, "n": 1},
                                 "B": {"src": "10.0.0.2", "last_rx": 8, "n": 1}}, st
        assert st["serial"] == {port: {"open": False, "err": None, "records": 1, "lines": 1}}, st
        sent = []
        while True:
            x = q.get_nowait()
            if x is None:
                break
            sent.append(x)
        assert sent == [rec, txt, wifi], sent
        with open(log) as f:
            assert f.read().splitlines() == [rec, txt, wifi]


def test_port_names_and_paths():
    ds = _ds()
    import tempfile
    assert ds.callout("/dev/tty.usbserial-022152D1") == "/dev/cu.usbserial-022152D1"
    for path in ("/dev/cu.usbserial-1", "/dev/ttyUSB0", "/dev/ttyACM1", "/dev/pts/3"):
        assert ds.callout(path) == path, path
    assert ds.port_name("/dev/cu.usbserial-022152D1") == "cu.usbserial-022152D1"
    assert ds.port_name("/dev/ttyUSB0") == "ttyUSB0" and ds.port_name("/dev/pts/3") == "pts/3"
    assert ds.port_name("/tmp/x/ttyFAKE") == "ttyFAKE"
    for pat in ("/dev/cu.usbserial-*", "/dev/cu.SLAB_USBtoUART*", "/dev/cu.wchusbserial*",
                "/dev/cu.usbmodem*", "/dev/ttyUSB*", "/dev/ttyACM*"):
        assert pat in ds.PORT_GLOBS, pat
    with tempfile.TemporaryDirectory() as tmp:
        for n in ("ttyUSB1", "ttyUSB0", "ttyS0", "cu.usbmodem3"):
            open(os.path.join(tmp, n), "w").close()
        pats = (os.path.join(tmp, "ttyUSB*"), os.path.join(tmp, "cu.usbmodem*"),
                os.path.join(tmp, "ttyUSB0"))                         # found twice: listed once
        assert ds.find_ports(pats) == [os.path.join(tmp, n)
                                       for n in ("cu.usbmodem3", "ttyUSB0", "ttyUSB1")]
    import errno
    for code, words in ((errno.ENOENT, "not found"), (errno.EBUSY, "busy"),
                        (errno.EAGAIN, "busy"), (errno.EACCES, "dialout")):
        assert words in ds.port_reason(OSError(code, os.strerror(code))), code
    assert ds.port_reason(OSError(errno.EIO, "Input/output error")) == "Input/output error"
    assert ds.port_news("ttyUSB0", {"open": True, "err": None}) == "USB port ttyUSB0 is open."
    assert ds.port_news("ttyUSB0", {"open": False, "err": None}) is None   # not tried yet
    assert ds.port_news("ttyUSB0", {"open": False, "err": "busy: x"}) == \
        "USB port ttyUSB0: busy: x. Trying again every second."


def test_open_port_is_raw_exclusive_and_quiet():
    ds = _serial_ds()
    import termios
    m, path = _pty()
    fd = ds.open_port(path)
    try:
        a = termios.tcgetattr(fd)
        assert a[3] & (termios.ECHO | termios.ICANON | termios.ISIG) == 0, a[3]
        assert a[0] & (termios.IXON | termios.IXOFF | termios.ICRNL | termios.INLCR) == 0, a[0]
        assert a[2] & termios.CSIZE == termios.CS8 and not a[2] & (termios.PARENB | termios.CSTOPB)
        assert a[2] & termios.CLOCAL and a[2] & termios.CREAD, a[2]
        assert a[4] == a[5] == termios.B115200
        try:                                     # mpremote, deploy.py or a second bridge
            ds.open_port(path)
            assert False, "opened twice"
        except OSError as e:
            assert ds.port_reason(e).startswith("busy"), e
        os.write(m, b"echo me?\n")               # raw: nothing goes back to the watch
        import fcntl
        import time
        time.sleep(0.1)                          # the tty echoes from a worker, not at once
        fcntl.fcntl(m, fcntl.F_SETFL, os.O_NONBLOCK)
        try:
            assert os.read(m, 64) == b"", "the bridge wrote to the port"
        except BlockingIOError:
            pass
    finally:
        os.close(fd)
        os.close(m)
    try:
        ds.open_port(path)
        assert False, "the pty is gone"
    except OSError as e:
        assert ds.port_reason(e) == "not found (unplugged?)", e


def test_open_port_shuts_out_programs_that_take_no_flock():
    """TIOCEXCL: screen, cat or an IDE serial monitor get "busy" too."""
    ds = _serial_ds()
    import errno
    import fcntl
    import struct
    import sys
    import termios
    root = os.geteuid() == 0                     # root opens a TIOCEXCL port anyway
    if root and not sys.platform.startswith("linux"):
        raise Skip("TIOCEXCL: run as a normal user on macOS")
    m, path = _pty()
    fd = ds.open_port(path)
    try:
        if root:                                 # so ask Linux
            got = fcntl.ioctl(fd, getattr(termios, "TIOCGEXCL", 0x80045440), b"\0" * 4)
            assert struct.unpack("i", got)[0] == 1, "not in TIOCEXCL mode"
        else:
            try:
                os.close(os.open(path, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK))
                assert False, "opened by a program that takes no flock"
            except OSError as e:
                assert e.errno == errno.EBUSY, e
    finally:
        os.close(fd)
        os.close(m)


def test_serial_reader_on_a_pty():
    """Records, text, CRLF, a record split across reads, an over-long line, then
    the port going away: closed, the reason in the status, tried again."""
    ds = _serial_ds()
    import fcntl
    import time
    m, path = _pty()
    port = path[len("/dev/"):]
    b = ds.Bridge(47268)
    q = b.subscribe()
    ports = ds.SerialPorts(b, [path])
    try:
        with _Fast(ds):
            ports.start()
            _wait(lambda: b.status()["serial"].get(port, {}).get("open"))
            os.write(m, b"the tail of a line sent before the bridge opened\r\n")
            rec = _framed(REC_A)
            os.write(m, rec[:20])
            time.sleep(0.05)                     # the reader takes the first part alone
            os.write(m, rec[20:-1] + b"\r\nMicroPython v1.29.0 boot\r\n\r\n")
            os.write(m, b"x" * (ds.LINE_MAX + 100) + b"\n")
            os.write(m, _framed(REC_B))
            got = [json.loads(q.get(timeout=T)) for _ in range(3)]
            assert [g["src"] for g in got] == [port] * 3, got
            assert [g.get("rec") or g.get("line") for g in got] == [
                REC_A, "MicroPython v1.29.0 boot", REC_B], got
            st = b.status()
            assert (st["packets"], st["bad"]) == (2, 1), st
            assert st["serial"] == {port: {"open": True, "err": None, "records": 2, "lines": 1}}
            fcntl.fcntl(m, fcntl.F_SETFL, os.O_NONBLOCK)
            try:
                assert os.read(m, 64) == b"", "the bridge wrote to the port"
            except BlockingIOError:
                pass
            os.close(m)                          # unplugged
            m = None
            _wait(lambda: b.status()["serial"][port]["err"] == "not found (unplugged?)")
            assert b.status()["serial"][port]["open"] is False
    finally:
        b.close()
        ports.join(T)
        if m is not None:
            os.close(m)
    assert not any(t.is_alive() for t in ports._readers.values())
    p = b.status()["serial"][port]
    assert (p["open"], p["records"], p["lines"]) == (False, 2, 1), p


def test_serial_port_comes_back():
    """A port that went away is opened again once it is back (here: a link that
    points at a new pseudo-terminal, as a watch plugged in again)."""
    ds = _serial_ds()
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        link = os.path.join(tmp, "ttyUSB0")
        m, path = _pty()
        os.symlink(path, link)
        b = ds.Bridge(47268)
        ports = ds.SerialPorts(b, [link])
        m2 = None

        def state():
            return b.status()["serial"].get("ttyUSB0", {})
        try:
            with _Fast(ds):
                ports.start()
                _wait(lambda: state().get("open"))
                os.close(m)
                m = None
                _wait(lambda: state().get("err") is not None)
                assert state()["open"] is False, state()
                m2, path2 = _pty()
                os.symlink(path2, link + ".new")
                os.replace(link + ".new", link)  # plugged in again
                _wait(lambda: state().get("open"))
                os.write(m2, b"\n" + _framed(REC_B))
                _wait(lambda: state()["records"] == 1)
                assert b.status()["watches"]["B"]["src"] == "ttyUSB0"
        finally:
            b.close()
            ports.join(T)
            for fd in (m, m2):
                if fd is not None:
                    os.close(fd)


def test_port_that_hangs_up_while_opening_is_tried_again():
    """A watch unplugged while its port is set up (termios.error EIO, not an
    OSError): the port is closed, the reason shown, and opened again."""
    ds = _serial_ds()
    import errno
    import fcntl
    import termios
    m, path = _pty()
    port = path[len("/dev/"):]
    b = ds.Bridge(47268)
    ports = ds.SerialPorts(b, [path])
    set_raw, errs = ds.set_raw, []               # the status err at each set_raw

    def hangs_up_once(fd):
        errs.append(b.status()["serial"][port]["err"])
        if len(errs) == 1:
            fcntl.ioctl(fd, termios.TIOCNXCL)    # as a USB tty's last close does (a pty's does not)
            raise termios.error(errno.EIO, "Input/output error")
        set_raw(fd)
    ds.set_raw = hangs_up_once
    try:
        with _Fast(ds):
            ports.start()
            _wait(lambda: b.status()["serial"].get(port, {}).get("open"))
            os.write(m, b"\n" + _framed(REC_A))  # read: the first fd and its flock were closed
            _wait(lambda: b.status()["packets"] == 1)
        assert errs == [None, "Input/output error"], errs
    finally:
        ds.set_raw = set_raw
        b.close()
        ports.join(T)
        os.close(m)


def test_auto_detect_finds_ports_plugged_in_later():
    ds = _serial_ds()
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        ends = []
        b = ds.Bridge(47268)
        pats = (os.path.join(tmp, "ttyUSB*"), os.path.join(tmp, "cu.usbmodem*"))
        ports = ds.SerialPorts(b, [], pats)
        try:
            with _Fast(ds):
                for name in ("ttyUSB0", "ttyS0"):                  # ttyS0 matches no pattern
                    m, path = _pty()
                    ends.append(m)
                    os.symlink(path, os.path.join(tmp, name))
                ports.start()
                _wait(lambda: b.status()["serial"].get("ttyUSB0", {}).get("open"))
                m, path = _pty()                                   # the second watch, later
                ends.append(m)
                os.symlink(path, os.path.join(tmp, "cu.usbmodem1"))
                _wait(lambda: b.status()["serial"].get("cu.usbmodem1", {}).get("open"))
                os.write(ends[0], b"\n" + _framed(REC_A))
                os.write(ends[2], b"\n" + _framed(REC_B))
                _wait(lambda: len(b.status()["watches"]) == 2)
                st = b.status()
                assert {k: w["src"] for k, w in st["watches"].items()} == {
                    "A": "ttyUSB0", "B": "cu.usbmodem1"}, st
                assert sorted(st["serial"]) == ["cu.usbmodem1", "ttyUSB0"], st
        finally:
            b.close()
            ports.join(T)
            for fd in ends:
                os.close(fd)
        assert not ports._scan.is_alive()
        assert not any(t.is_alive() for t in ports._readers.values())


def test_status_lists_the_serial_ports():
    ds = _serial_ds()
    import tempfile
    m, path = _pty()
    try:
        with tempfile.TemporaryDirectory() as tmp, _Fast(ds):
            srv = _start(ds, tmp, serial=[path, "/dev/ttyNOPE-test"])
            try:
                def ready():
                    s = _status(srv.http_port)["serial"]
                    return len(s) == 2 and s[path[5:]]["open"] and s["ttyNOPE-test"]["err"]
                _wait(ready)
                st = _status(srv.http_port)["serial"]
                assert st == {path[5:]: {"open": True, "err": None, "records": 0, "lines": 0},
                              "ttyNOPE-test": {"open": False, "err": "not found (unplugged?)",
                                               "records": 0, "lines": 0}}, st
            finally:
                srv.stop()
            assert not any(t.is_alive() for t in srv.ports._readers.values())
    finally:
        os.close(m)


def test_demo_serial_runs_the_fake_watches_on_ptys():
    """main() with --demo --serial: the fake watches write framed lines into two
    pseudo-terminals, which the bridge reads like USB ports; Ctrl-C stops it all."""
    ds = _serial_ds()
    import contextlib
    import io
    import sys
    import tempfile
    import types
    seen = []

    def run(host="127.0.0.1", port=47268, seconds=None, speed=1.0, stop=None, serial=None):
        seen.append(serial)
        n = 0
        while not stop.is_set():
            for f, rec in zip(serial, (REC_A, REC_B)):
                f.write(b"boot %d\r\n" % n + _framed(rec))
            n += 1
            stop.wait(0.02)
        seen.append("returned")

    def follow(srv):                             # main's loop, until both watches were heard
        def both():
            st = srv.bridge.status()
            return len(st["watches"]) == 2 and all(p["lines"] for p in st["serial"].values())
        _wait(both)
        seen.append(srv.bridge.status())
    fake = types.ModuleType("tools.fake_watches")
    fake.run = run
    saved = sys.modules.get("tools.fake_watches"), ds._follow
    sys.modules["tools.fake_watches"] = fake
    ds._follow = follow
    try:
        with tempfile.TemporaryDirectory() as tmp:
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                ds.main(["--root", _www(tmp), "--http-port", "0", "--udp-port", "0", "--no-log",
                         "--demo", "--serial"])
    finally:
        ds._follow = saved[1]
        if saved[0] is None:
            del sys.modules["tools.fake_watches"]
        else:
            sys.modules["tools.fake_watches"] = saved[0]
    ends, st, done = seen
    assert done == "returned"                    # stopped with the bridge
    assert all(isinstance(f, io.FileIO) and f.mode == "wb" and f.closed for f in ends), ends
    srcs = {k: w["src"] for k, w in st["watches"].items()}
    assert sorted(srcs) == ["A", "B"] and srcs["A"] != srcs["B"], srcs
    for k in ("A", "B"):
        assert srcs[k].startswith("pts/") or srcs[k].startswith("ttys"), srcs
        p = st["serial"][srcs[k]]
        assert p["open"] and p["records"] >= 1 and p["lines"] >= 1, st["serial"]
    text = out.getvalue()
    assert "(two simulated watches are writing)" in text, text
    assert "Stopped. Heard" in text, text
