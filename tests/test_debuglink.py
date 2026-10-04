"""hal/debuglink.py on the fake ``network``, ``socket`` and USB serial port,
and main.py's debug switch (/debug present, USB or Wi-Fi, secrets.py
missing, a failed join: the game then plays normally). No test uses real
Wi-Fi or a real serial port; the Wi-Fi name and password here are made up."""

import sys

from tests import Skip, fakes
from tests.fakes.serial_port import LINE_FIFO, LINE_RATE, Port

fakes.install()
from hal import debuglink as dl  # noqa: E402  (import failure must FAIL, not skip)

SSID = "made-up-net"
PW = "made-up-pass-123"


def _tmpdir():
    try:
        import tempfile
        return tempfile.mkdtemp()          # CPython
    except ImportError:
        return "."                         # MicroPython: in-memory filesystem


def _write(path, text):
    with open(path, "w") as f:
        f.write(text)


def _rm(*paths):
    import os
    for p in paths:
        try:
            os.remove(p)
        except OSError:
            pass


def _files(d, config=None, secrets=None):
    """``d``/_debug and ``d``/_secrets.py (None: not written) -> their paths."""
    c, s = d + "/_debug", d + "/_secrets.py"
    _rm(c, s)
    if config is not None:
        _write(c, config)
    if secrets is not None:
        _write(s, secrets)
    return c, s


def _clean(d):
    """Remove ``_files``' files, ``_run_main``'s /tele and log directory, and
    the temporary directory."""
    import os
    _rm(d + "/_debug", d + "/_secrets.py", d + "/_tele")
    log = d + "/_log"
    try:
        names = os.listdir(log)
    except OSError:
        names = None
    if names is not None:
        for n in names:
            _rm(log + "/" + n)
        os.rmdir(log)
    if d != ".":
        os.rmdir(d)


def _secrets(ssid=SSID, pw=PW):
    return "WIFI_SSID = %r\nWIFI_PASSWORD = %r\n" % (ssid, pw)


def _ap(channel=13, polls=3, **kw):
    fakes.install()
    import network
    network.set_ap(SSID, PW, channel=channel, polls=polls, **kw)
    return network


def _wlans(network):
    """Keep every WLAN made from now on (the fake makes a new device each
    time) -> (list, restore)."""
    made = []
    real = network.WLAN

    def wlan(iface=network.STA_IF):
        made.append(real(iface))
        return made[-1]
    network.WLAN = wlan

    def restore():
        network.WLAN = real
    return made, restore


# ---- files ---------------------------------------------------------------------------
def test_read_config_defaults_and_errors():
    d = _tmpdir()
    c, _ = _files(d, '{"dev": "B", "link": "wifi", "host": "192.168.1.23", "port": 5000}')
    try:
        assert dl.read_config(c) == {"dev": "B", "link": "wifi", "host": "192.168.1.23",
                                     "port": 5000}
        _write(c, '{"dev": "A"}')                  # no link: USB
        assert dl.read_config(c) == {"dev": "A", "link": "usb", "host": None,
                                     "port": dl.DEBUG_PORT}
        for bad in ("dev=A", '{"dev": "A", "link": "bluetooth"}'):
            _write(c, bad)
            try:
                dl.read_config(c)
                assert False, bad
            except ValueError:
                pass
        _rm(c)
        try:
            dl.read_config(c)
            assert False, "missing file accepted"
        except OSError:
            pass
    finally:
        _clean(d)


def test_read_secrets_never_quotes_the_file():
    d = _tmpdir()
    _, s = _files(d, secrets=_secrets())
    try:
        assert dl.read_secrets(s) == (SSID, PW)
        for bad in ("WIFI_SSID = %r\nWIFI_PASSWORD = %r +\n" % (SSID, PW),   # syntax error
                    "WIFI_PASSWORD = %r\n" % PW,                              # no name
                    "WIFI_SSID = ''\nWIFI_PASSWORD = %r\n" % PW):            # empty name
            _write(s, bad)
            try:
                dl.read_secrets(s)
                assert False, bad
            except ValueError as e:
                m = str(e)
                assert PW not in m and SSID not in m and "Wi-Fi name" in m, m
        _rm(s)
        try:
            dl.read_secrets(s)
            assert False, "missing secrets accepted"
        except ValueError as e:
            assert str(e) == "/secrets.py is not on the watch", e
    finally:
        _clean(d)


def test_read_secrets_wants_text_in_quotes():
    """A name or password that is not text (an all-digit password typed
    without quotes) would make WLAN.connect raise TypeError on the watch:
    it is a plain reason instead, which never shows the value."""
    d = _tmpdir()
    _, s = _files(d, secrets=_secrets())
    try:
        for bad in ("WIFI_SSID = %r\nWIFI_PASSWORD = 12345678\n" % SSID,
                    "WIFI_SSID = 24680\nWIFI_PASSWORD = %r\n" % PW,
                    "WIFI_SSID = %r\nWIFI_PASSWORD = 0\n" % SSID):     # falsy, still not text
            _write(s, bad)
            try:
                dl.read_secrets(s)
                assert False, bad
            except ValueError as e:
                m = str(e)
                assert "quotes" in m and "/secrets.py" in m, m
                for v in ("12345678", "24680", SSID, PW):
                    assert v not in m, m
        _write(s, "WIFI_SSID = %r\n" % SSID)                          # an open network
        assert dl.read_secrets(s) == (SSID, "")
        _write(s, "WIFI_SSID = %r\nWIFI_PASSWORD = None\n" % SSID)
        assert dl.read_secrets(s) == (SSID, "")
    finally:
        _clean(d)


def test_broadcast_addr():
    assert dl.broadcast_addr("192.168.1.40", "255.255.255.0") == "192.168.1.255"
    assert dl.broadcast_addr("10.1.2.40", "255.255.252.0") == "10.1.3.255"


# ---- join ----------------------------------------------------------------------------
def test_join_reads_channel_address_and_broadcast():
    _ap(channel=13, ip="10.1.2.40", mask="255.255.252.0")
    slept = []
    link = dl.DebugLink("B", None)
    assert link.join(SSID, PW, sleep=slept.append)
    assert link.sta.isconnected() and link.sta.active()
    assert (link.channel, link.ip, link.bcast) == (13, "10.1.2.40", "10.1.3.255")
    assert slept == [dl.JOIN_STEP_MS] * 2        # connected on the 3rd poll
    assert link.why is None


def test_join_wrong_password_disconnects_and_says_why():
    """The ESP32 keeps retrying a wrong password (``STAT_CONNECTING``), so the
    join times out with the message that names both the name and password."""
    network = _ap()
    made, restore = _wlans(network)
    slept = []
    statuses = []
    try:
        link = dl.DebugLink("A", "192.168.1.23")

        def nap(ms):
            slept.append(ms)
            statuses.append(made[0].status())
        assert not link.join(SSID, "wrong-pass", timeout_ms=1000, sleep=nap)
    finally:
        restore()
    assert link.sta is None and link.channel is None
    assert set(statuses) == {network.STAT_CONNECTING}   # what a real watch reports
    assert "could not join the Wi-Fi in 1 s" in link.why and "password" in link.why
    assert link.why.endswith(dl.WIFI_FIX % "A" + ")"), link.why     # what to run on the laptop
    assert "wrong-pass" not in link.why and SSID not in link.why
    assert len(slept) == 1000 // dl.JOIN_STEP_MS
    assert made[0].status() == network.STAT_IDLE   # disconnected: it stops trying


def test_join_unknown_network_times_out_and_stops_trying():
    network = _ap()
    made, restore = _wlans(network)
    try:
        link = dl.DebugLink("A", "192.168.1.23")
        assert not link.join("other-net", PW, timeout_ms=2000, sleep=lambda ms: None)
    finally:
        restore()
    assert "could not join the Wi-Fi in 2 s" in link.why and "2.4 GHz" in link.why
    assert "other-net" not in link.why
    assert made[0].status() == network.STAT_IDLE   # no channel hopping under ESP-NOW


def test_join_wifi_error_is_a_reason_not_a_crash():
    """OSError, the port's RuntimeError('Wifi Unknown Error 0x..') and any
    other error all end in ``why`` with the STA disconnected. Only the port's
    fixed texts are quoted; anything else shows just its type."""
    network = _ap()
    real = network.WLAN.connect
    for err, want in ((OSError("Wifi Internal Error"), "Wi-Fi error (Wifi Internal Error)"),
                      (RuntimeError("Wifi Unknown Error 0x0102"),
                       "Wi-Fi error (Wifi Unknown Error 0x0102)"),
                      (TypeError("not " + PW), "Wi-Fi error (TypeError)")):
        def boom(self, ssid=None, key=None, err=err):
            real(self, ssid, key)                  # it had started trying
            raise err
        network.WLAN.connect = boom
        made, restore = _wlans(network)
        try:
            link = dl.DebugLink()
            assert not link.join(SSID, PW, sleep=lambda ms: None)
        finally:
            restore()
            network.WLAN.connect = real
        assert link.why == want and link.sta is None, link.why
        assert made[0].status() == network.STAT_IDLE, err    # not left connecting
    made, restore = _wlans(network)
    try:                                           # the watch's own TypeError: a number
        link = dl.DebugLink()
        assert not link.join(SSID, 12345678, sleep=lambda ms: None)
    finally:
        restore()
    assert link.why == "Wi-Fi error (TypeError)" and made[0].status() == network.STAT_IDLE


def test_join_ctrl_c_stops_trying_and_goes_on_up():
    """Ctrl-C (mpremote, deploy.py) during the join disconnects the STA, so it
    does not keep scanning channels, and still reaches main.py."""
    network = _ap()
    made, restore = _wlans(network)

    def ctrl_c(ms):
        raise KeyboardInterrupt
    try:
        link = dl.DebugLink()
        try:
            link.join("other-net", PW, sleep=ctrl_c)
            assert False, "Ctrl-C swallowed"
        except KeyboardInterrupt:
            pass
    finally:
        restore()
    assert link.sta is None and made[0].status() == network.STAT_IDLE


# ---- UDP -----------------------------------------------------------------------------
def test_send_to_the_laptop_counts_errors_and_never_raises():
    restore = fakes.install_socket()
    try:
        import socket
        link = dl.DebugLink("A", "192.168.1.23", 47000)
        assert not link.send("early")              # no socket yet: counted
        assert link.tx_err == 1
        assert link.open()
        s = socket.sockets[-1]
        assert link.dest == ("192.168.1.23", 47000) and not s.blocking
        assert (socket.SOL_SOCKET, socket.SO_BROADCAST) not in s.opts
        assert link.send('{"ev": "s"}') and link.send(b"raw")
        to = ("192.168.1.23", 47000)
        assert s.sent == [(b'{"ev": "s"}', to), (b"raw", to)]
        socket.fail[0] = 12                        # ENOMEM: no buffer for the frame
        assert link.send("x") is False
        assert link.n_tx == 2 and link.tx_err == 2 and isinstance(link.err, OSError)
        st = link.stats()
        assert st["tx"] == 2 and st["tx_err"] == 2 and st["dest"] == "192.168.1.23:47000"
        link.close()
        assert s.closed and link.send("y") is False and link.tx_err == 3
    finally:
        restore()


def test_no_host_broadcasts_on_the_subnet():
    _ap(channel=6)
    restore = fakes.install_socket()
    try:
        import socket
        link = dl.DebugLink("A", None, 47268)
        assert not link.open()                     # not joined: nowhere to send
        assert "no laptop address" in link.why
        assert link.join(SSID, PW, sleep=lambda ms: None) and link.open()
        assert link.dest == ("192.168.1.255", 47268)
        assert socket.sockets[-1].opts[(socket.SOL_SOCKET, socket.SO_BROADCAST)] == 1
    finally:
        restore()


def test_close_leaves_the_wifi_even_when_the_socket_will_not_close():
    _ap(channel=6)
    restore = fakes.install_socket()
    try:
        link = dl.DebugLink("A", "192.168.1.23")
        assert link.join(SSID, PW, sleep=lambda ms: None) and link.open()
        sta = link.sta

        def stuck():
            raise OSError(9)                       # EBADF
        link._s.close = stuck
        link.close()
        assert not sta.isconnected() and link.sta is None and link._s is None
    finally:
        restore()


def _hide_socket():
    """``import socket`` fails until restore() (CPython: a None entry; the
    MicroPython test port has no socket module) -> restore."""
    old = sys.modules.get("socket")
    if sys.implementation.name == "micropython":
        sys.modules.pop("socket", None)
    else:
        sys.modules["socket"] = None

    def restore():
        if old is None:
            sys.modules.pop("socket", None)
        else:
            sys.modules["socket"] = old
    return restore


def test_no_socket_module_is_a_reason():
    restore = _hide_socket()
    try:
        link = dl.DebugLink("A", "192.168.1.23")
        assert not link.open() and link.why.startswith("no UDP socket")
    finally:
        restore()


# ---- USB serial ----------------------------------------------------------------------
def _rec(n, tag="x"):
    """A compact JSON record of exactly ``n`` bytes (from 18 + len(tag))."""
    return '{"ev":"%s","pad":"%s"}' % (tag, "y" * (n - 18 - len(tag)))


def _port(clock):
    """A fake USB port whose writes are stamped with ``clock[0]``."""
    return Port(lambda: clock[0])


def test_serial_frames_each_record_as_one_line():
    """0x1E, the JSON as UTF-8, then \\n (RFC 7464), for str and bytes alike;
    nothing is written before a pump."""
    port = Port()
    link = dl.SerialLink("B", port)
    assert link.send('{"ev":"s","t":1}') and link.send(b'{"ev":"btn"}')
    assert link.send('{"ev":"crash","e":"\u00e9"}')
    want = b'\x1e{"ev":"s","t":1}\n\x1e{"ev":"btn"}\n\x1e{"ev":"crash","e":"\xc3\xa9"}\n'
    assert port.writes == [] and link.queued == len(want) and link.n_tx == 0
    link.pump(0)
    assert port.data() == want
    assert link.stats() == {"dev": "B", "link": "usb", "tx": 3, "drop": 0, "queued": 0,
                            "tx_err": 0, "err": None}
    assert (link.sta, link.channel, link.rp_ms) == (None, None, 1000)
    assert dl.SerialLink().out is sys.stdout.buffer       # the REPL's UART on the watch


def test_serial_log_line_goes_between_records():
    """``log`` queues a plain text line (no 0x1E) behind the records already
    waiting, so it never lands inside one; the bridge shows it as text."""
    port = Port()
    link = dl.SerialLink("A", port)
    assert link.send('{"ev":"s","x":"%s"}' % ("y" * 200))     # two pieces
    assert link.log("fps 9.9 lock 10")
    assert link.send('{"ev":"btn"}')
    link.drain()
    lines = port.data().split(b"\n")
    assert lines[1] == b"fps 9.9 lock 10" and lines[0][:1] == lines[2][:1] == b"\x1e"
    assert lines[3] == b"" and link.n_tx == 3


def test_wifi_log_line_is_printed():
    """On Wi-Fi the records never use the USB port, so the fps line is a print."""
    link = dl.DebugLink("A")
    out = []
    dl.print = out.append                  # the module's print, before the builtin
    try:
        link.log("fps 9.9 lock 10")
    finally:
        del dl.print
    assert out == ["fps 9.9 lock 10"] and link.n_tx == 0


def test_serial_pieces_fill_whole_fifo_loads():
    """Pieces of at most 128 bytes, cut once in ``send`` every 128 bytes of
    the queued stream: a pass that finds the FIFO empty writes all 128 bytes,
    across records too, and each record arrives as one intact line."""
    clock = [0]
    port = _port(clock)
    link = dl.SerialLink("A", port)
    a, b = _rec(200, "a"), _rec(300, "b")          # 202 and 302 bytes framed
    assert link.send(a) and link.send(b)
    loads = []
    while link.queued:
        clock[0] += 20                             # a loop pass every 20 ms
        k = len(port.writes)
        link.pump(clock[0])
        loads.append(sum([len(w[0]) for w in port.writes[k:]]))
    assert loads == [128, 128, 128, 120], loads
    assert max([len(w[0]) for w in port.writes]) <= LINE_FIFO
    assert port.data().split(b"\n") == [b"\x1e" + a.encode(), b"\x1e" + b.encode(), b""]
    assert link.n_tx == 2


def test_serial_pump_never_writes_more_than_the_fifo_has_room_for():
    """A 4 KB backlog pumped at even and uneven passes: no write ever
    overfills the 115200-baud line's FIFO (128 bytes, 11.52 bytes per ms),
    and pumped every ms the backlog drains at about that rate."""
    sizes = (411, 664, 70, 411, 63, 411)
    for steps in ((1,), (1, 5, 3, 17, 2, 9, 30, 4, 12)):
        clock = [1000]
        port = _port(clock)
        link = dl.SerialLink("A", port)
        n = 0
        while link.send(_rec(sizes[n % len(sizes)], "r%d" % n)):
            n += 1
        total = link.queued
        assert link.drop == 1 and total > dl.SERIAL_QMAX - 664
        j = 0
        while link.queued:
            link.pump(clock[0])
            clock[0] += steps[j % len(steps)]
            j += 1
        assert port.overfill() is None, steps
        assert len(port.data()) == total and link.n_tx == n
        if steps == (1,):
            t0 = port.writes[0][1]
            first = sum([len(w[0]) for w in port.writes if w[1] == t0])
            rate = (total - first) / (port.writes[-1][1] - t0)
            assert 10.0 <= rate <= LINE_RATE, rate


def test_serial_pump_runs_across_the_tick_wrap():
    """``ticks_ms`` wraps at 2**30: the FIFO's room keeps refilling across it."""
    port = Port()
    link = dl.SerialLink("A", port)
    for i in range(4):
        link.send(_rec(411, "w%d" % i))
    t = (1 << 30) - 50
    for _ in range(20):                            # 13 passes of 128 bytes are enough
        link.pump(t)
        t = (t + 20) & ((1 << 30) - 1)
    assert link.queued == 0 and link.n_tx == 4


def test_serial_drops_whole_records_past_the_queue_limit():
    port = Port()
    link = dl.SerialLink("A", port)
    n = 0
    while link.queued + 413 <= dl.SERIAL_QMAX:
        assert link.send(_rec(411, "k%d" % n))
        n += 1
    q = link.queued
    assert not link.send(_rec(411, "lost"))        # would leave more than SERIAL_QMAX waiting
    assert link.drop == 1 and link.queued == q
    assert link.send(_rec(dl.SERIAL_QMAX - q - 2, "fits"))
    assert link.queued == dl.SERIAL_QMAX
    link.drain()
    data = port.data()
    assert b"lost" not in data and b"fits" in data and data.count(b"\n") == n + 1
    assert not link.send(_rec(dl.SERIAL_QMAX, "huge")) and link.drop == 2   # never fits
    for _ in range(dl.SERIAL_SLOTS):               # tiny records run out of piece slots
        assert link.send("{}")
    assert not link.send("{}") and link.drop == 3 and link.stats()["drop"] == 3


def test_serial_drain_writes_out_everything_at_once():
    """Loop exit and power off: what waits goes out now (waiting on the port
    is fine then), so the last records, such as ``crash``, reach the laptop."""
    port = Port()
    link = dl.SerialLink("A", port)
    for i in range(3):
        link.send(_rec(411, "r%d" % i))
    link.pump(0)
    assert len(port.data()) == dl.SERIAL_FIFO and link.n_tx == 0
    link.drain()
    assert len(port.data()) == 3 * 413 and link.n_tx == 3 and link.queued == 0


def test_serial_write_errors_are_counted_and_tried_again():
    """A file that refuses a write (the fake watches' closed pty) never stops
    the game: counted in ``tx_err``, the piece goes on the next pass."""
    class Flaky(Port):
        fails = 1

        def write(self, b):
            if self.fails:
                self.fails -= 1
                raise OSError(5)                   # EIO
            return Port.write(self, b)
    port = Flaky()
    link = dl.SerialLink("A", port)
    link.send('{"ev":"s"}')
    link.pump(0)
    assert link.tx_err == 1 and isinstance(link.err, OSError) and link.queued == 12
    link.pump(20)
    assert port.data() == b'\x1e{"ev":"s"}\n' and link.n_tx == 1 and link.queued == 0
    assert link.stats()["tx_err"] == 1


def test_serial_pump_allocates_nothing():
    """``pump`` runs every loop pass: on MicroPython it must not allocate."""
    import gc
    if not hasattr(gc, "mem_alloc"):
        raise Skip("needs MicroPython gc.mem_alloc")

    class Port:
        """Counts what it is given; allocates nothing."""

        def __init__(self):
            self.n = 0

        def write(self, b):
            self.n += len(b)
            return len(b)
    port = Port()
    link = dl.SerialLink("A", port)
    while link.send(_rec(411)):
        pass
    total = link.queued
    gc.collect()
    gc.disable()
    try:
        a0 = gc.mem_alloc()
        t = 0
        while t < 1000:
            link.pump(t)
            t += 3
        used = gc.mem_alloc() - a0
    finally:
        gc.enable()
    assert port.n == total and link.queued == 0, (port.n, total)
    assert used <= 64, used


# ---- start (main.py) -----------------------------------------------------------------
def test_start_usb_needs_no_wifi_and_no_secrets():
    """USB (``link`` usb, or no ``link``): a ``SerialLink`` at once and a
    message that says what to run on the laptop; /secrets.py is never read
    and the Wi-Fi is never touched."""
    d = _tmpdir()
    network = _ap()
    made, restore = _wlans(network)
    real = dl.read_secrets
    reads = []
    dl.read_secrets = reads.append
    try:
        for cfg in ('{"dev": "B"}', '{"dev": "B", "link": "usb", "host": "192.168.1.23"}'):
            c, s = _files(d, cfg, _secrets())
            link, msg = dl.start(c, s)
            assert isinstance(link, dl.SerialLink) and link.dev == "B" and link.sta is None
            assert msg == dl.USB_MSG % "B" and "python3 tools/debug_server.py --serial" in msg
    finally:
        dl.read_secrets = real
        restore()
        _clean(d)
    assert reads == [] and made == []


def test_start_without_debug_file_is_silent():
    d = _tmpdir()
    c, s = _files(d)
    try:
        assert dl.start(c, s) == (None, None)
    finally:
        _clean(d)


def test_start_paths_say_why_and_never_show_the_secrets():
    d = _tmpdir()
    restore = fakes.install_socket()
    nap = lambda ms: None
    try:
        _ap(channel=11)
        c, s = _files(d, '{"dev": "B", "link": "wifi", "host": "192.168.1.23", "port": 47268}')
        link, msg = dl.start(c, s, sleep=nap)                     # no secrets.py
        assert link is None and "/secrets.py is not on the watch" in msg
        assert msg.endswith("(%s). Playing normally." % (dl.WIFI_FIX % "B")), msg
        assert "python3 tools/wifi_setup.py" in msg and "--debug B --wifi" in msg
        _write(s, _secrets(pw="wrong-pass-456"))
        link, msg = dl.start(c, s, 500, nap)                      # the AP refuses the password
        assert link is None and "password" in msg and "Playing normally" in msg
        assert "wrong-pass-456" not in msg and SSID not in msg and dl.WIFI_FIX % "B" in msg
        _write(s, "WIFI_SSID = %r\nWIFI_PASSWORD = 12345678\n" % SSID)   # no quotes
        link, msg = dl.start(c, s, 500, nap)
        assert link is None and "quotes" in msg and msg.endswith("Playing normally."), msg
        assert dl.WIFI_FIX % "B" in msg
        assert "12345678" not in msg and SSID not in msg
        _write(c, "[1, 2]")
        link, msg = dl.start(c, s, sleep=nap)
        assert link is None and "/debug is not valid" in msg
        _write(c, '{"dev": "B", "link": "wifi", "host": "192.168.1.23"}')
        _write(s, _secrets())
        link, msg = dl.start(c, s, sleep=nap)
        assert link is not None and link.dev == "B" and link.channel == 11
        assert link.dest == ("192.168.1.23", dl.DEBUG_PORT)
        assert msg == ("debug mode: watch B sends to 192.168.1.23:47268 on Wi-Fi channel 11 "
                       "(both watches must join the same access point; a mesh or extender "
                       "network can put them on different channels)")
        assert PW not in msg and SSID not in msg
    finally:
        restore()
        _clean(d)


def test_start_open_failure_leaves_the_wifi():
    d = _tmpdir()
    c, s = _files(d, '{"dev": "A", "link": "wifi", "host": "192.168.1.23"}', _secrets())
    _ap()
    unhide = _hide_socket()
    import network
    made, restore = _wlans(network)
    try:
        link, msg = dl.start(c, s, sleep=lambda ms: None)
        restore()
        assert link is None and "no UDP socket" in msg
        assert not made[0].isconnected()           # joined, then left again
    finally:
        restore()
        unhide()
        _clean(d)


# ---- main.py -------------------------------------------------------------------------
class _StandIn:
    """The ``app`` package as main.py sees it: ``run`` records its arguments."""


def _run_main(config, secrets, ap_key=PW, tele=None, start=None):
    """Run main.py on the fakes with a stand-in ``app`` -> (board, run kwargs,
    printed). ``tele``: the text of its /tele (None: none); ``start``: stands
    in for ``debuglink.start``. /tele and the /log directory are the test's."""
    try:
        import io
        with open("main.py") as f:
            src = f.read()
    except (ImportError, OSError):
        raise Skip("no main.py next to the tests")
    import app.telemetry as tm  # main.py imports it through the stand-in
    fakes.install()
    import network
    network.set_ap(SSID, ap_key, channel=13)
    d = _tmpdir()
    c, s = _files(d, config, secrets)
    t = d + "/_tele"
    _rm(t)
    if tele is not None:
        _write(t, tele)
    real_open = open
    real_session = tm.session

    def _open(path, *a, **k):                  # main.py's /tele is the test's file
        return real_open(t if path == "/tele" else path, *a, **k)
    calls = []
    fake_app = _StandIn()                      # MicroPython cannot make module objects
    fake_app.__path__ = sys.modules["app"].__path__
    fake_app.run = lambda board, **kw: calls.append((board, kw))
    out = io.StringIO()
    try:
        out.buffer = io.BytesIO()              # CPython: the USB link's port (SerialLink)
    except AttributeError:
        pass                                   # MicroPython keeps its own sys.stdout

    def _print(*a, **k):                       # main.py's own prints (MicroPython has no
        k["file"] = out                        # settable sys.stdout)
        print(*a, **k)
    stdout = getattr(sys, "stdout", None)      # CPython: also anything the modules print
    saved = (sys.modules["app"], dl.CONFIG, dl.SECRETS, dl._sleep_ms, dl.start)
    restore = fakes.install_socket()
    sys.modules["app"] = fake_app
    dl.CONFIG, dl.SECRETS, dl._sleep_ms = c, s, lambda ms: None
    dl.start = start or dl.start
    tm.session = lambda dev: real_session(dev, d + "/_log")
    try:
        try:
            sys.stdout = out
        except AttributeError:
            pass
        exec(compile(src, "main.py", "exec"),
             {"__name__": "__main__", "print": _print, "open": _open})
    finally:
        try:
            sys.stdout = stdout
        except AttributeError:
            pass
        sys.modules["app"], dl.CONFIG, dl.SECRETS, dl._sleep_ms, dl.start = saved
        tm.session = real_session
        restore()
        _clean(d)
    assert len(calls) == 1, out.getvalue()
    board, kw = calls[0]
    assert kw.pop("watchdog_ms") == 8000 and kw.pop("fps_log_ms") == 10000
    return board, kw, out.getvalue()


def test_main_usb_debug_plays_normally_and_sends_on_the_port():
    """``--debug B`` (USB): the game starts with the normal radio, its records
    go to the USB link, and /secrets.py is never read nor the Wi-Fi joined."""
    import json
    fakes.install()
    import network
    made, restore = _wlans(network)
    real = dl.read_secrets
    reads = []
    dl.read_secrets = reads.append
    try:
        board, kw, out = _run_main('{"dev": "B", "link": "usb"}', _secrets())
    finally:
        dl.read_secrets = real
        restore()
    link = board.debug
    assert isinstance(link, dl.SerialLink) and link.dev == "B"
    assert dl.USB_MSG % "B" in out, out
    r = board.radio                                # as in normal play
    assert not r.associated and r.channel == 6 and r._e.active()
    assert reads == [] and made and [w for w in made if w._ssid is not None] == []
    tl = kw["telemetry"]
    assert tl.sink is link and tl.dev == "B" and tl.path is None and tl.cap <= 64
    port = Port()
    link.out = port
    tl.event(5, "btn", ("kind", "short"))
    tl.flush(force=True)                           # loop exit: what waits goes out
    line = port.data()
    assert line[:1] == b"\x1e" and line[-1:] == b"\n" and b" " not in line, line
    assert json.loads(line[1:-1]) == {"t": 5, "ev": "btn", "kind": "short", "dev": "B",
                                      "mac": None}


def test_main_debug_mode_joins_first_and_sends_telemetry():
    board, kw, out = _run_main('{"dev": "B", "link": "wifi", "host": "192.168.1.23", '
                               '"port": 47268}', _secrets())
    link = board.debug
    assert link is not None and link.dev == "B"
    assert "debug mode: watch B sends to 192.168.1.23:47268 on Wi-Fi channel 13" in out
    r = board.radio                                # ESP-NOW joined the access point's channel
    assert r.associated and r.channel == 13 and r._sta is link.sta and link.sta.isconnected()
    tl = kw["telemetry"]
    assert tl.sink is link and tl.dev == "B" and tl.path is None
    assert tl.cap <= 64            # no /tele: a small ring, not 900 records for GC to scan
    assert PW not in out and SSID not in out


def test_main_without_secrets_plays_normally():
    board, kw, out = _run_main('{"dev": "A", "link": "wifi", "host": "192.168.1.23"}', None)
    assert "debug mode off: /secrets.py is not on the watch" in out and "Playing normally." in out
    assert "python3 tools/wifi_setup.py" in out
    assert board.debug is None and kw == {}
    r = board.radio
    assert not r.associated and r.channel == 6 and r._e.active()


def test_main_failed_join_plays_normally():
    board, kw, out = _run_main('{"dev": "A", "link": "wifi", "host": "192.168.1.23"}', _secrets(),
                               ap_key="the-real-one")
    assert "debug mode off: could not join the Wi-Fi in" in out and "Playing normally." in out
    assert PW not in out and "the-real-one" not in out
    assert board.debug is None and kw == {}
    r = board.radio
    assert not r.associated and r.channel == 6 and r._e.active()


def test_main_without_debug_file_says_nothing():
    board, kw, out = _run_main(None, _secrets())
    assert "debug" not in out and board.debug is None and kw == {}


def test_main_unquoted_password_plays_normally():
    """``WIFI_PASSWORD = 12345678`` (no quotes) once made WLAN.connect raise
    TypeError past main.py's debug switch, and the game never started."""
    board, kw, out = _run_main('{"dev": "A", "link": "wifi", "host": "192.168.1.23"}',
                               "WIFI_SSID = %r\nWIFI_PASSWORD = 12345678\n" % SSID)
    assert "debug mode off: the Wi-Fi name or password in /secrets.py is not in quotes" in out, out
    assert "Playing normally." in out and "12345678" not in out and "crashed" not in out
    assert board.debug is None and kw == {}
    r = board.radio
    assert not r.associated and r.channel == 6 and r._e.active()


def test_main_unexpected_debug_error_plays_normally():
    """Whatever ``debuglink.start`` raises, the game starts (only the error's
    type is printed, never its text)."""
    def start():
        raise RuntimeError("Wifi Unknown Error 0x3001 " + PW)
    board, kw, out = _run_main('{"dev": "A", "link": "wifi", "host": "192.168.1.23"}', _secrets(),
                               start=start)
    assert "debug mode off: it could not start (RuntimeError). Playing normally." in out, out
    assert PW not in out and "crashed" not in out
    assert board.debug is None and kw == {}
    r = board.radio
    assert not r.associated and r.channel == 6 and r._e.active()


def test_main_tele_and_debug_log_under_one_name():
    """/tele (an older ``--tele A``) and /debug (``--debug B``) disagree: the
    file name and every record use the /debug name, and it says so."""
    board, kw, out = _run_main('{"dev": "B", "link": "wifi", "host": "192.168.1.23"}', _secrets(),
                               tele="A")
    tl = kw["telemetry"]
    assert tl.path.endswith("_B.jsonl") and tl.dev == "B" and tl.sink is board.debug, tl.path
    assert "telemetry: /tele says A but /debug says B; logging as B" in out, out
    board, kw, out = _run_main('{"dev": "B", "link": "wifi", "host": "192.168.1.23"}', _secrets(),
                               tele="B")
    assert kw["telemetry"].path.endswith("_B.jsonl") and "telemetry:" not in out
    board, kw, out = _run_main(None, None, tele="A")        # field test only: as before
    tl = kw["telemetry"]
    assert tl.path.endswith("0_A.jsonl") and tl.sink is None and tl.cap == 900
    assert "telemetry:" not in out and "debug" not in out
