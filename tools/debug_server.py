#!/usr/bin/env python3
"""Debug bridge: shows the real watches in the web sim page (CPython host tool).

    python3 tools/debug_server.py              # then open http://localhost:8765/local.html
    python3 tools/debug_server.py --demo       # two simulated watches, no hardware needed
    python3 tools/debug_server.py --no-log     # do not save the session under logs/

In debug mode (``tools/deploy.py --debug A``) each watch joins the Wi-Fi and
sends one JSON object per UDP datagram to this laptop
(docs/design/debug-mode.md, "Contract between the parts"). This server:

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
- answers ``GET /debug/status`` with the counters and the watches heard so far
  (the page asks it whether Real mode is available);
- appends every event line, exactly as sent on ``/events``, to
  ``logs/debug-YYYYmmdd-HHMMSS.jsonl`` (gitignored; created with the first
  record) unless ``--no-log``, so a session can be replayed later to calibrate
  the estimators on real radio data.

``--demo`` runs ``tools/fake_watches.run`` on a thread, sending to this
server's UDP port. Standard library only (and ``DEBUG_PORT`` from hal/debuglink.py).
"""

import argparse
import errno
import functools
import http.server
import json
import math
import os
import queue
import socket
import sys
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
# Where the watches send: the one value deploy.py writes into /debug and fake_watches.py uses.
from hal.debuglink import DEBUG_PORT as UDP_PORT  # noqa: E402  (after the path fix)

HTTP_PORT = 8765
RECV_MAX = 4096           # a record is at most ~1400 bytes; a longer one arrives cut and is bad
POLL_S = 0.2              # how often the UDP and HTTP loops look for stop()
PING_S = 10               # SSE keep-alive comment: finds closed tabs
BACKLOG = 256             # lines queued per /events client before it misses some (~13 s)
RETRY_MS = 2000           # how soon the page's EventSource reconnects to a restarted bridge
IN_USE = (errno.EADDRINUSE, getattr(errno, "WSAEADDRINUSE", errno.EADDRINUSE))


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
    """Records -> /events clients, the log and the counters. ``handle`` runs
    on the UDP thread, the rest on HTTP threads; one lock guards them."""

    def __init__(self, udp_port, log=None):
        self.udp_port = udp_port
        self.log = log            # path of the session log, or None
        self.done = threading.Event()
        self.packets = 0
        self.bad = 0
        self.watches = {}         # dev -> {"src", "last_rx", "n"}
        self._lock = threading.Lock()
        self._clients = []        # one queue of lines per open /events stream
        self._file = None

    def handle(self, data, src, rx):
        """One datagram (None: unreadable) from ``src`` received at ``rx`` ms
        -> its event line, or None when it is not a valid record. Ignored
        once ``close`` has run (no log reopened, no stream fed)."""
        rec = None if data is None else parse(data)
        line = None
        if rec is not None:
            try:              # strict JSON for the page and the log; parse lets no NaN through
                line = json.dumps({"src": src, "rx": rx, "rec": rec}, separators=(",", ":"),
                                  allow_nan=False)
            except ValueError:
                pass
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
            self._write(line)
            for q in self._clients:
                try:
                    q.put_nowait(line)
                except queue.Full:
                    pass          # a stalled tab misses lines; the others carry on
        return line

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
                    "watches": {k: dict(v) for k, v in self.watches.items()}}

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
    """The HTTP server (127.0.0.1) and the UDP listener (0.0.0.0) on daemon
    threads. Port 0 picks a free port; ``http_port``/``udp_port`` are the
    bound ones. Raises PortError (an OSError) naming the port it could not
    open."""

    def __init__(self, root, http_port=HTTP_PORT, udp_port=UDP_PORT, log=None):
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
        self._threads = []

    def start(self):
        for fn in (self._listen, functools.partial(self.http.serve_forever, POLL_S)):
            t = threading.Thread(target=fn, daemon=True)
            t.start()
            self._threads.append(t)

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
        """Ends the streams, both loops and the log; waits for the threads."""
        self.bridge.close()
        if self._threads:
            self.http.shutdown()
        for t in self._threads:
            t.join(2)
        self.http.server_close()
        self.udp.close()


def start_demo(port, stop):
    """Runs ``tools.fake_watches.run`` (two simulated watches sending to UDP
    ``port`` on this laptop) on a daemon thread until ``stop`` is set."""
    from tools.fake_watches import run        # lazy: loads the simulator and the game
    t = threading.Thread(target=run, kwargs={"host": "127.0.0.1", "port": port, "stop": stop},
                         daemon=True)
    t.start()
    return t


def parse_args(argv):
    p = argparse.ArgumentParser(description="Show the real watches in the web sim page.")
    p.add_argument("--http-port", type=int, default=HTTP_PORT, help="page port (default %(default)s)")
    p.add_argument("--udp-port", type=int, default=UDP_PORT,
                   help="port to hear the watches on (default %(default)s, where they always "
                        "send: change it only with --demo)")
    p.add_argument("--root", default=os.path.join(ROOT, "dist", "sim"),
                   help="folder to serve (default dist/sim)")
    p.add_argument("--no-log", action="store_true", help="do not save the session under logs/")
    p.add_argument("--demo", action="store_true", help="also run two simulated watches")
    return p.parse_args(argv)


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
    lines = ["Debug bridge running. Open http://localhost:%d/local.html and pick "
             "\"Real watches\"." % srv.http_port,
             "Listening for the watches on UDP port %d%s." % (
                 srv.udp_port, " (two simulated watches are sending)" if demo else "")]
    if srv.udp_port != UDP_PORT:
        lines.append("Real watches will not reach this port: they always send to UDP port %d. "
                     "Leave out --udp-port to hear them." % UDP_PORT)
    lines.append("If your laptop asks whether Python may accept incoming connections, allow it: "
                 "the watches reach it over Wi-Fi.")
    if srv.bridge.log is not None:
        lines.append("Saving what the watches send to %s" % _shown(srv.bridge.log))
    lines.append("Press Ctrl-C to stop.")
    return lines


def main(argv=None):
    a = parse_args(argv)
    if not os.path.isdir(a.root):
        sys.exit("Nothing to serve: %s does not exist. Build the page first: "
                 "python3 tools/build_sim.py" % a.root)
    try:
        srv = DebugServer(a.root, a.http_port, a.udp_port, None if a.no_log else log_path())
    except PortError as e:
        sys.exit(port_message(e))
    srv.start()
    for line in banner(srv, a.demo):
        print(line)
    if a.demo:
        try:
            start_demo(srv.udp_port, srv.bridge.done)
        except ImportError as e:
            srv.stop()
            sys.exit("--demo needs tools/fake_watches.py: %s" % e)
    heard = set()
    try:
        while True:
            time.sleep(1)
            for dev, w in sorted(srv.bridge.status()["watches"].items()):
                if dev not in heard:
                    heard.add(dev)
                    print("Heard watch %s (from %s)." % (dev, w["src"]))
    except KeyboardInterrupt:
        pass
    srv.stop()
    st = srv.bridge.status()
    print("Stopped. Heard %d record(s) from %d watch(es); %d could not be read." % (
        st["packets"], len(st["watches"]), st["bad"]))
    if st["log"] is not None and st["packets"]:
        print("Session saved to %s" % st["log"])


if __name__ == "__main__":
    main()
