#!/usr/bin/env python3
"""Debug bridge: shows the real watches in the web sim page (CPython host tool).

    python3 tools/debug_server.py --serial     # watches on USB; then open http://localhost:8765/local.html
    python3 tools/debug_server.py              # watches on Wi-Fi only
    python3 tools/debug_server.py --demo       # two simulated watches (--demo --serial: over USB)
    python3 tools/debug_server.py --no-log     # do not save the session under logs/

In debug mode each watch writes its records as lines on its USB serial port
(``tools/deploy.py --port P --debug A``), or joins the Wi-Fi and sends one
JSON object per UDP datagram to this laptop (``--debug A --wifi``); see
docs/design/debug-mode.md, "Contract between the parts". This server:

- serves ``--root`` (dist/sim, built by tools/build_sim.py) on 127.0.0.1 only,
  with ``Cache-Control: no-store`` so a rebuilt page is never stale;
- listens on UDP 0.0.0.0:``--udp-port`` (``DEBUG_PORT`` from hal/debuglink.py,
  where the watches always send; another port only suits ``--demo`` or
  ``tools/fake_watches.py --port``). A valid datagram (a UTF-8 JSON object
  with non-empty string ``dev`` and ``ev``) becomes one Server-Sent Event on
  ``GET /events``: ``data: {"src": "<sender ip>", "rx": <ms>, "rec": <the
  object>}``, ``rx`` being this laptop's wall clock in ms since the epoch (so
  the page can compare it with ``Date.now()``). Anything else is counted in
  ``bad`` and dropped;
- with ``--serial``, reads the USB serial ports given, or every one it finds
  (looking again every 2 s): one thread per port, opened raw at 115200 8N1
  and exclusively, never written to. A line that starts with 0x1E is a
  record, checked and relayed exactly like a datagram with ``src`` the
  port's name (``cu.usbserial-022152D1``); any other line becomes ``data:
  {"src": "<port>", "rx": <ms>, "line": "<text>"}``, so boot messages and
  tracebacks reach the page's raw log. A port that fails or goes away is
  closed and opened again every second, with the reason in /debug/status;
- answers ``GET /debug/status`` with the counters, the watches heard so far
  and the serial ports (the page asks it whether Real mode is available);
- appends every event line, exactly as sent on ``/events``, to
  ``logs/debug-YYYYmmdd-HHMMSS.jsonl`` (gitignored; created with the first
  record) unless ``--no-log``, so a session can be replayed later to calibrate
  the estimators on real radio data.

``--demo`` runs ``tools/fake_watches.run`` on a thread, sending to this
server's UDP port, or with ``--serial`` writing into two pseudo-terminals
that this server reads like USB ports. Standard library only (and
``DEBUG_PORT`` from hal/debuglink.py); the serial ports need macOS or Linux.
"""

import argparse
import errno
import functools
import glob
import http.server
import json
import math
import os
import queue
import select
import socket
import sys
import threading
import time

try:
    import fcntl
    import termios
except ImportError:           # Windows: no --serial; the Wi-Fi link still works
    fcntl = termios = None

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
# Where the watches send: the one value deploy.py writes into /debug and fake_watches.py uses.
from hal.debuglink import DEBUG_PORT as UDP_PORT  # noqa: E402  (after the path fix)

HTTP_PORT = 8765
RECV_MAX = 4096           # a record is at most ~1400 bytes; a longer one arrives cut and is bad
POLL_S = 0.2              # how often the UDP, HTTP and serial loops look for stop()
PING_S = 10               # SSE keep-alive comment: finds closed tabs
BACKLOG = 256             # lines queued per /events client before it misses some (~13 s)
RETRY_MS = 2000           # how soon the page's EventSource reconnects to a restarted bridge
IN_USE = (errno.EADDRINUSE, getattr(errno, "WSAEADDRINUSE", errno.EADDRINUSE))
RS = b"\x1e"              # starts a record line on a USB serial port (RFC 7464)
LINE_MAX = 8192           # a serial line with more bytes before its \n is dropped and counted in bad
READ_MAX = 4096
REOPEN_S = 1.0            # a serial port that failed or went away is opened again this often
SCAN_S = 2.0              # --serial without ports: looks for newly plugged-in ports this often
PORT_GLOBS = ("/dev/cu.usbserial-*", "/dev/cu.SLAB_USBtoUART*", "/dev/cu.wchusbserial*",
              "/dev/cu.usbmodem*", "/dev/ttyUSB*", "/dev/ttyACM*")
GONE = "went away (unplugged?)"


def now_ms():
    return int(time.time() * 1000)


def _reject_constant(name):
    raise ValueError(name)    # NaN / Infinity: the page's JSON.parse cannot read them


def _finite_float(s):
    v = float(s)
    if not math.isfinite(v):  # 1e400 overflows to inf: the page's JSON.parse cannot read Infinity
        raise ValueError(s)
    return v


def parse(data):
    """A datagram (bytes) -> its record (a dict with non-empty string ``dev``
    and ``ev``, every number finite), or None."""
    try:
        rec = json.loads(data.decode("utf-8"), parse_constant=_reject_constant,
                         parse_float=_finite_float)
    except (ValueError, RecursionError):      # bad UTF-8 or JSON, or absurd nesting
        return None
    if not isinstance(rec, dict):
        return None
    dev, ev = rec.get("dev"), rec.get("ev")
    if isinstance(dev, str) and dev and isinstance(ev, str) and ev:
        return rec
    return None


def log_path():
    """``ROOT/logs/debug-YYYYmmdd-HHMMSS.jsonl``, local time now."""
    return os.path.join(ROOT, "logs", time.strftime("debug-%Y%m%d-%H%M%S.jsonl"))


def _shown(path):
    """``path`` relative to the repo root when it is inside it."""
    rel = os.path.relpath(path, ROOT)
    return path if rel.startswith("..") else rel.replace(os.sep, "/")


class Bridge:
    """Records and serial lines -> /events clients, the log and the counters.
    ``handle`` runs on the UDP and serial threads, ``port_*`` on the serial
    ones, the rest on HTTP threads; one lock guards them."""

    def __init__(self, udp_port, log=None):
        self.udp_port = udp_port
        self.log = log            # path of the session log, or None
        self.done = threading.Event()
        self.packets = 0
        self.bad = 0
        self.watches = {}         # dev -> {"src", "last_rx", "n"}
        self.serial = {}          # port name -> {"open", "err", "records", "lines"}
        self._lock = threading.Lock()
        self._clients = []        # one queue of lines per open /events stream
        self._file = None

    def handle(self, data, src, rx, serial=False):
        """One datagram (None: unreadable), or the rest of a serial record
        line (``serial``: ``src`` is that port, which counts it), from
        ``src`` received at ``rx`` ms -> its event line, or None when it is
        not a valid record. Ignored once ``close`` has run (no log reopened,
        no stream fed)."""
        rec = None if data is None else parse(data)
        line = None if rec is None else json.dumps({"src": src, "rx": rx, "rec": rec},
                                                   separators=(",", ":"))
        with self._lock:
            if self.done.is_set():
                return None
            if line is None:
                self.bad += 1
                return None
            self.packets += 1
            w = self.watches.get(rec["dev"])
            if w is None:
                w = self.watches[rec["dev"]] = {"src": src, "last_rx": rx, "n": 0}
            w["src"] = src
            w["last_rx"] = rx
            w["n"] += 1
            if serial:
                self._port(src)["records"] += 1
            self._relay(line)
        return line

    def port_line(self, port, data, rx):
        """One line read from serial ``port`` at ``rx`` ms (bytes without
        its line end; None: longer than LINE_MAX, bad) -> its event line, or
        None when it is dropped. A record line goes through ``handle``."""
        if data is None:
            return self.handle(None, port, rx)
        if data[:1] == RS:
            return self.handle(data[1:], port, rx, serial=True)
        line = json.dumps({"src": port, "rx": rx, "line": data.decode("utf-8", "replace")},
                          separators=(",", ":"))
        with self._lock:
            if self.done.is_set():
                return None
            self._port(port)["lines"] += 1
            self._relay(line)
        return line

    def port_state(self, port, is_open, err):
        """Serial ``port`` is open, or closed because of ``err`` (None: not
        tried yet, or stopped)."""
        with self._lock:
            p = self._port(port)
            p["open"] = is_open
            p["err"] = err

    def _port(self, port):
        p = self.serial.get(port)
        if p is None:
            p = self.serial[port] = {"open": False, "err": None, "records": 0, "lines": 0}
        return p

    def _relay(self, line):
        """``line`` to the log and every /events stream (lock held)."""
        self._write(line)
        for q in self._clients:
            try:
                q.put_nowait(line)
            except queue.Full:
                pass          # a stalled tab misses lines; the others carry on

    def _write(self, line):
        """Appends ``line`` to the log, opened with the first record; a write
        error stops logging, never the relay."""
        if self.log is None:
            return
        try:
            if self._file is None:
                os.makedirs(os.path.dirname(self.log), exist_ok=True)
                self._file = open(self.log, "a", encoding="utf-8", buffering=1)
            self._file.write(line + "\n")
        except OSError as e:
            print("Could not write the log %s (%s): logging stopped." % (_shown(self.log), e))
            self.log = None

    def subscribe(self):
        """A new /events stream -> its queue of lines (None ends it)."""
        q = queue.Queue(BACKLOG)
        with self._lock:
            self._clients.append(q)
        return q

    def unsubscribe(self, q):
        with self._lock:
            if q in self._clients:
                self._clients.remove(q)

    def status(self):
        with self._lock:
            return {"ok": True, "udp_port": self.udp_port, "clients": len(self._clients),
                    "packets": self.packets, "bad": self.bad,
                    "log": None if self.log is None else _shown(self.log),
                    "watches": {k: dict(v) for k, v in self.watches.items()},
                    "serial": {k: dict(v) for k, v in self.serial.items()}}

    def close(self):
        """Ends every /events stream and closes the log."""
        self.done.set()
        with self._lock:
            for q in self._clients:
                try:
                    q.put_nowait(None)
                except queue.Full:
                    pass          # its stream sees ``done`` after the next line
            if self._file is not None:
                self._file.close()
                self._file = None


# ---- USB serial ------------------------------------------------------------------------

class Lines:
    """Bytes from a serial port as they arrive -> its non-empty lines, without
    the ``\\n`` and trailing ``\\r``. A line of more than LINE_MAX bytes
    comes out once, as None. The bytes before the first ``\\n`` are dropped:
    the port may have opened in the middle of a line."""

    def __init__(self):
        self._buf = bytearray()
        self._skip = True         # drop everything up to the next \n

    def feed(self, data):
        out = []
        buf = self._buf
        buf += data
        start = 0
        while True:
            k = buf.find(b"\n", start)
            if k < 0:
                break
            if self._skip:
                self._skip = False
            elif k - start > LINE_MAX:
                out.append(None)
            else:
                line = bytes(buf[start:k]).rstrip(b"\r")
                if line:
                    out.append(line)
            start = k + 1
        del buf[:start]
        if len(buf) > LINE_MAX:   # no line end yet and already too long: count it once, drop the rest
            if not self._skip:
                out.append(None)
                self._skip = True
            del buf[:]
        return out


def callout(path):
    """macOS ``/dev/tty.X`` -> ``/dev/cu.X``, which opens without waiting
    for a modem carrier; any other path as it is."""
    d, b = os.path.split(path)
    return os.path.join(d, "cu." + b[4:]) if b.startswith("tty.") else path


def port_name(path):
    """The ``src`` of a serial port's records: its path under /dev
    (``cu.usbserial-022152D1``, ``ttyUSB0``, ``pts/3`` for --demo), else its
    base name."""
    return path[5:] if path.startswith("/dev/") else os.path.basename(path)


def find_ports(patterns=PORT_GLOBS):
    """The USB serial ports plugged in now, sorted."""
    return sorted(set(p for pat in patterns for p in glob.glob(pat)))


def set_raw(fd):
    """Raw 115200 8N1 on the tty ``fd``: no echo (it would write to the
    port), line editing, translation or flow control; CLOCAL (no modem
    carrier) and CREAD. HUPCL stays as the OS set it, like DTR and RTS."""
    a = termios.tcgetattr(fd)
    a[0] = a[1] = a[3] = 0                    # iflag, oflag, lflag
    a[2] = (a[2] & ~(termios.CSIZE | termios.PARENB | termios.CSTOPB
                     | getattr(termios, "CRTSCTS", 0))) | termios.CS8 | termios.CLOCAL | termios.CREAD
    a[4] = a[5] = termios.B115200
    a[6][termios.VMIN] = a[6][termios.VTIME] = 0
    termios.tcsetattr(fd, termios.TCSANOW, a)


def open_port(path):
    """Serial port ``path`` -> its fd: non-blocking, never the controlling
    terminal, exclusive (the flock that pyserial, so mpremote, takes, and
    TIOCEXCL, so others get "busy" instead of losing bytes), then raw."""
    fd = os.open(path, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)    # first: never retune a port in use
        if hasattr(termios, "TIOCEXCL"):
            fcntl.ioctl(fd, termios.TIOCEXCL)
        set_raw(fd)
    except OSError:
        os.close(fd)
        raise
    except termios.error as e:    # not an OSError: the port hung up while being set up (EIO)
        os.close(fd)
        raise OSError(*e.args) from None
    return fd


def port_reason(e):
    """An OSError from opening or reading a serial port -> the plain reason
    /debug/status gives."""
    if e.errno == errno.ENOENT:
        return "not found (unplugged?)"
    if e.errno in (errno.EBUSY, errno.EAGAIN):
        return "busy: another program has it open (mpremote, deploy.py or a serial monitor?)"
    if e.errno == errno.EACCES:
        return "permission denied (on Linux, join the dialout group)"
    return e.strerror or str(e)


def _read_lines(bridge, port, fd):
    """Reads the open port ``fd`` into ``bridge`` until ``bridge.done``
    (-> None) or the port fails or goes away (-> why)."""
    lines = Lines()
    while not bridge.done.is_set():
        if not select.select([fd], [], [], POLL_S)[0]:
            continue
        try:
            data = os.read(fd, READ_MAX)
        except BlockingIOError:
            continue
        except OSError as e:
            return port_reason(e)
        if not data:
            return GONE
        rx = now_ms()
        for line in lines.feed(data):
            bridge.port_line(port, line, rx)
    return None


def read_port(bridge, path):
    """Reads serial port ``path`` into ``bridge`` until ``bridge.done``,
    keeping its state in ``bridge.serial``. A port that cannot be opened,
    fails or goes away is closed and opened again every REOPEN_S."""
    port = port_name(path)
    done = bridge.done
    bridge.port_state(port, False, None)
    while not done.is_set():
        try:
            fd = open_port(path)
        except OSError as e:
            why = port_reason(e)
        else:
            bridge.port_state(port, True, None)
            try:
                why = _read_lines(bridge, port, fd)
            finally:
                os.close(fd)
        bridge.port_state(port, False, why)
        if why is not None:
            done.wait(REOPEN_S)


class SerialPorts:
    """``--serial``: one ``read_port`` thread per port, for the ``paths``
    given or, with none, every port matching ``patterns``, looked for again
    every SCAN_S until ``bridge.done``."""

    def __init__(self, bridge, paths, patterns=PORT_GLOBS):
        self.bridge = bridge
        self.paths = [callout(p) for p in paths]
        self.patterns = patterns
        self._readers = {}        # path -> its read_port thread
        self._scan = None

    def start(self):
        self._scan = threading.Thread(target=self._run, daemon=True)
        self._scan.start()

    def _run(self):
        while True:
            for path in self.paths or find_ports(self.patterns):
                if path not in self._readers:
                    t = threading.Thread(target=read_port, args=(self.bridge, path), daemon=True)
                    self._readers[path] = t
                    t.start()
            if self.paths or self.bridge.done.wait(SCAN_S):
                return

    def join(self, timeout):
        """Waits for the threads (once ``bridge.done`` is set)."""
        if self._scan is None:
            return
        self._scan.join(timeout)
        for t in list(self._readers.values()):
            t.join(timeout)


# ---- HTTP and UDP ----------------------------------------------------------------------

class Handler(http.server.SimpleHTTPRequestHandler):
    """Static files from ``directory``, plus /events and /debug/status."""

    extensions_map = dict(http.server.SimpleHTTPRequestHandler.extensions_map,
                          **{".mjs": "text/javascript", ".wasm": "application/wasm"})

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path == "/events":
            self._events()
        elif path == "/debug/status":
            self._status()
        else:
            super().do_GET()

    def log_request(self, code="-", size="-"):
        pass                      # quiet: the page polls /debug/status

    def _status(self):
        body = json.dumps(self.server.bridge.status()).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _events(self):
        bridge = self.server.bridge
        q = bridge.subscribe()
        self.close_connection = True
        try:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            self.wfile.write(b"retry: %d\n\n" % RETRY_MS)
            while not bridge.done.is_set():
                try:
                    line = q.get(timeout=PING_S)
                except queue.Empty:
                    self.wfile.write(b": ping\n\n")
                    continue
                if line is None:
                    break
                self.wfile.write(b"data: " + line.encode() + b"\n\n")
        except OSError:
            pass                  # the tab closed or reloaded
        finally:
            bridge.unsubscribe(q)


class HTTPServer(http.server.ThreadingHTTPServer):
    allow_reuse_port = False      # a second bridge on the same page port fails, never shares it


class PortError(OSError):
    """A port DebugServer could not open. ``strerror`` names it in plain
    words; ``udp`` is True for the watches' UDP port, False for the page's;
    ``in_use`` is True when another program holds it."""

    def __init__(self, e, what, udp):
        self.in_use = e.errno in IN_USE
        why = "is in use" if self.in_use else "could not be opened (%s)" % (e.strerror or e)
        super().__init__(e.errno, "%s %s" % (what, why))
        self.udp = udp


class DebugServer:
    """The HTTP server (127.0.0.1), the UDP listener (0.0.0.0) and, with
    ``serial`` (a list of port paths; empty: find them), the serial readers,
    on daemon threads. Port 0 picks a free port; ``http_port``/``udp_port``
    are the bound ones. Raises PortError (an OSError) naming the port it
    could not open."""

    def __init__(self, root, http_port=HTTP_PORT, udp_port=UDP_PORT, log=None, serial=None):
        self.udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            self.udp.bind(("0.0.0.0", udp_port))
        except OSError as e:
            self.udp.close()
            raise PortError(e, "UDP port %d, where the watches send," % udp_port, udp=True)
        self.udp.settimeout(POLL_S)
        self.udp_port = self.udp.getsockname()[1]
        self.bridge = Bridge(self.udp_port, log)
        try:
            self.http = HTTPServer(
                ("127.0.0.1", http_port), functools.partial(Handler, directory=root))
        except OSError as e:
            self.udp.close()
            raise PortError(e, "page port %d" % http_port, udp=False)
        self.http.block_on_close = False      # a stalled tab must not hold up stop()
        self.http.bridge = self.bridge
        self.http_port = self.http.server_address[1]
        self.ports = None if serial is None else SerialPorts(self.bridge, serial)
        self._threads = []

    def start(self):
        for fn in (self._listen, functools.partial(self.http.serve_forever, POLL_S)):
            t = threading.Thread(target=fn, daemon=True)
            t.start()
            self._threads.append(t)
        if self.ports is not None:
            self.ports.start()

    def _listen(self):
        while not self.bridge.done.is_set():
            try:
                data, addr = self.udp.recvfrom(RECV_MAX)
            except socket.timeout:
                continue
            except OSError:           # closed by stop(), or (Windows) longer than RECV_MAX
                data, addr = None, ("", 0)
            self.bridge.handle(data, addr[0], now_ms())

    def stop(self):
        """Ends the streams, the loops, the serial ports and the log; waits
        for the threads."""
        self.bridge.close()
        if self._threads:
            self.http.shutdown()
        for t in self._threads:
            t.join(2)
        if self.ports is not None:
            self.ports.join(2)
        self.http.server_close()
        self.udp.close()


# ---- the demo --------------------------------------------------------------------------

def demo_ports():
    """``--demo --serial``: two pseudo-terminals -> (their master ends as
    unbuffered binary files for the fake watches to write, the slave paths
    to read like USB ports). Each slave is made raw first, so nothing
    written is echoed back."""
    import pty
    ends, paths = [], []
    for _ in range(2):
        m, s = pty.openpty()
        set_raw(s)
        paths.append(os.ttyname(s))
        os.close(s)                           # read_port opens it by path, like a real port
        ends.append(os.fdopen(m, "wb", buffering=0))
    return tuple(ends), paths


def start_demo(port, stop, serial=None):
    """Runs ``tools.fake_watches.run`` (two simulated watches) on a daemon
    thread until ``stop`` is set: sending to UDP ``port`` on this laptop,
    or with ``serial`` (two writable binary files) writing their USB lines
    into them."""
    from tools.fake_watches import run        # lazy: loads the simulator and the game
    kw = {"host": "127.0.0.1", "port": port, "stop": stop}
    if serial is not None:
        kw["serial"] = serial
    t = threading.Thread(target=run, kwargs=kw, daemon=True)
    t.start()
    return t


def _close_all(files):
    for f in files or ():
        f.close()


# ---- the command -----------------------------------------------------------------------

def parse_args(argv):
    p = argparse.ArgumentParser(description="Show the real watches in the web sim page.")
    p.add_argument("--serial", nargs="*", metavar="PORT",
                   help="read the watches on these USB serial ports (none given: every one "
                        "plugged in, looking again every 2 s)")
    p.add_argument("--http-port", type=int, default=HTTP_PORT, help="page port (default %(default)s)")
    p.add_argument("--udp-port", type=int, default=UDP_PORT,
                   help="port to hear the watches on (default %(default)s, where they always "
                        "send: change it only with --demo)")
    p.add_argument("--root", default=os.path.join(ROOT, "dist", "sim"),
                   help="folder to serve (default dist/sim)")
    p.add_argument("--no-log", action="store_true", help="do not save the session under logs/")
    p.add_argument("--demo", action="store_true",
                   help="also run two simulated watches (with --serial: on two pretend USB ports)")
    a = p.parse_args(argv)
    if a.demo and a.serial:
        p.error("--demo --serial makes its own pretend ports: give it no PORT")
    return a


def port_message(e):
    """A PortError -> what main() tells the owner: which port, and what to do."""
    head = "Could not start the bridge: %s." % e.strerror
    if e.udp:
        keep = ("the watches always send to UDP port %d, so changing --udp-port only works "
                "with --demo." % UDP_PORT)
        if e.in_use:
            return "%s\nIs another debug_server.py running? Stop it and start again: %s" % (
                head, keep)
        return "%s\nNote: %s" % (head, keep)
    if e.in_use:
        return ("%s\nIs another debug_server.py (or python3 -m http.server) running? Stop it "
                "and start again, or serve the page on another port with --http-port N." % head)
    return "%s\nServe the page on another port with --http-port N." % head


def banner(srv, demo):
    """The lines main() prints once ``srv`` is up."""
    ports = srv.ports
    lines = ["Debug bridge running. Open http://localhost:%d/local.html and pick "
             "\"Real watches\"." % srv.http_port]
    if ports is not None:
        names = [port_name(p) for p in ports.paths]
        if names:
            lines.append("Reading the watches on USB serial port%s %s%s." % (
                "s" if len(names) > 1 else "", ", ".join(names),
                " (two simulated watches are writing)" if demo else ""))
        else:
            lines.append("Reading every USB serial port plugged in, and looking for new ones "
                         "every %d s." % SCAN_S)
        lines.append("The bridge holds those ports: stop it (Ctrl-C) before deploy.py or "
                     "mpremote can use them.")
    lines.append("Listening for the watches on UDP port %d%s." % (
        srv.udp_port, " (two simulated watches are sending)" if demo and ports is None else ""))
    if srv.udp_port != UDP_PORT:
        lines.append("Real watches will not reach this port: they always send to UDP port %d. "
                     "Leave out --udp-port to hear them." % UDP_PORT)
    lines.append("If your laptop asks whether Python may accept incoming connections, allow it: "
                 "watches on Wi-Fi reach it that way.")
    if srv.bridge.log is not None:
        lines.append("Saving what the watches send to %s" % _shown(srv.bridge.log))
    lines.append("Press Ctrl-C to stop.")
    return lines


def port_news(port, p):
    """A port's /debug/status entry -> the line main() prints when it changes
    (None before the first try)."""
    if p["open"]:
        return "USB port %s is open." % port
    if p["err"]:
        return "USB port %s: %s. Trying again every second." % (port, p["err"])
    return None


def _follow(srv):
    """Prints the watches heard and the serial ports' news until Ctrl-C."""
    heard = set()
    news = {}
    try:
        while True:
            time.sleep(1)
            st = srv.bridge.status()
            for port, p in sorted(st["serial"].items()):
                n = port_news(port, p)
                if n != news.get(port):
                    news[port] = n
                    if n:
                        print(n)
            for dev, w in sorted(st["watches"].items()):
                if dev not in heard:
                    heard.add(dev)
                    print("Heard watch %s (from %s)." % (dev, w["src"]))
    except KeyboardInterrupt:
        pass


def main(argv=None):
    a = parse_args(argv)
    if not os.path.isdir(a.root):
        sys.exit("Nothing to serve: %s does not exist. Build the page first: "
                 "python3 tools/build_sim.py" % a.root)
    if a.serial is not None and termios is None:
        sys.exit("--serial needs macOS or Linux; on this computer use the Wi-Fi link.")
    ends, serial = None, a.serial
    if a.demo and serial is not None:
        ends, serial = demo_ports()
    try:
        srv = DebugServer(a.root, a.http_port, a.udp_port, None if a.no_log else log_path(),
                          serial)
    except PortError as e:
        _close_all(ends)
        sys.exit(port_message(e))
    srv.start()
    for line in banner(srv, a.demo):
        print(line)
    demo = None
    if a.demo:
        try:
            demo = start_demo(srv.udp_port, srv.bridge.done, ends)
        except ImportError as e:
            srv.stop()
            _close_all(ends)
            sys.exit("--demo needs tools/fake_watches.py: %s" % e)
    _follow(srv)
    srv.stop()
    if demo is not None:
        demo.join(2)                  # it stops within one step once the bridge is done
    _close_all(ends)
    st = srv.bridge.status()
    print("Stopped. Heard %d record(s) from %d watch(es); %d could not be read." % (
        st["packets"], len(st["watches"]), st["bad"]))
    if st["log"] is not None and st["packets"]:
        print("Session saved to %s" % st["log"])


if __name__ == "__main__":
    # a pipe (the web-sim preview, tee) shows each line as it is printed; not in main(),
    # which the tests run with stdout redirected to a StringIO (no reconfigure)
    sys.stdout.reconfigure(line_buffering=True)
    main()
