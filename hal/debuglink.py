"""Debug mode links: send the watch's records to the laptop over USB serial or Wi-Fi.

Debug mode (docs/design/debug-mode.md) shows the real watches in the web sim
page. ``main.py`` calls ``start()`` before ``Board.init``::

    link, msg = debuglink.start()   # (None, None) when /debug is missing
    if msg:
        print(msg)                  # how the laptop reads it, or why debug mode is off
    board.debug = link              # a joined Wi-Fi link puts ESP-NOW on its channel

``start`` reads ``/debug`` (``{"dev": "A", "link": "usb"}`` or ``{"dev": "A",
"link": "wifi", "host": "192.168.1.23", "port": 47268}``, written by
``tools/deploy.py --debug A [--wifi]``; no ``link`` means ``usb``).

USB (``SerialLink``, the default) writes each record as one line on the
REPL's UART, the port ``tools/debug_server.py --serial`` reads. It reads no
``/secrets.py``, never touches the Wi-Fi, leaves the radio as in normal
play (``sta`` is None) and never reads the port, so Ctrl-C still works.

Wi-Fi (``DebugLink``): ``start`` reads ``/secrets.py`` (``WIFI_SSID``,
``WIFI_PASSWORD``: the file ``tools/wifi_setup.py`` saved on the laptop,
copied by ``tools/deploy.py --debug A --wifi``), joins the access point
(``JOIN_MS`` at most) and opens a UDP socket to ``host:port``, or to the
subnet broadcast address when ``/debug`` has no host (broadcast is
unreliable on the ESP32: a fallback only). Whatever fails, it returns
``(None, why)`` and the game plays normally; a reason about the Wi-Fi file
or the join ends with ``WIFI_FIX`` (main.py also turns any unexpected error
from ``start`` into such a message). The Wi-Fi name and password are never
printed, logged or sent, and no reason quotes them.

ESP-NOW and the Wi-Fi connection share one radio, so once joined the access
point's channel (1-13) is the ESP-NOW channel too: ``Board`` starts
``EspNowRadio`` in its associated mode on ``link.sta``. Both watches must
join the same access point, or they will not hear each other: a watch whose
join failed plays on its usual channel (6), the joined one on the access
point's, so the two cannot find each other until both have joined (restart
the missing one). A mesh or extender network with one name can also put
them on different channels.

Both links offer what app/telemetry.py and app/runtime.py use: ``dev``,
``sta`` and ``channel`` (None on USB), ``rp_ms`` (how often the screen
record goes out), ``send(data)`` (one record, from the 5 Hz path, never
from the render loop), ``pump(now)`` (once per loop pass and after every
strip of a frame), ``drain()`` (loop exit), ``stats()`` and ``close()``.
Send errors are counted in ``tx_err`` and never raised. ``network`` and
``socket`` are imported only when used, so this file also loads on CPython
(tools/fake_watches.py sends through both links) and in the tests.
"""

import json
import sys

from finder.compat import sleep_ms as _sleep_ms, ticks_diff

CONFIG = "/debug"
SECRETS = "/secrets.py"
LINKS = ("usb", "wifi")  # /debug ``link``; the first is the default
DEBUG_PORT = 47268
JOIN_MS = 10000          # give up on the Wi-Fi after this and play normally
JOIN_STEP_MS = 100
OFF_MSG = "debug mode off: %s. Playing normally."
USB_MSG = ("debug mode: watch %s sends its records on this USB port "
           "(on the laptop: python3 tools/debug_server.py --serial)")
WIFI_FIX = ("on the laptop, run python3 tools/wifi_setup.py, then "
            "python3 tools/deploy.py --debug %s --wifi again")   # %s: this watch's dev
SERIAL_FIFO = 128        # bytes: the UART's transmit FIFO, and the largest piece
SERIAL_RATE = 11         # bytes per ms the FIFO empties (115200 baud: 11.52)
SERIAL_QMAX = 4096       # bytes that may wait; a record that would pass it is dropped
SERIAL_SLOTS = 160       # pieces that may wait: records of 32+ bytes reach SERIAL_QMAX first
_EMPTY_MS = SERIAL_FIFO // SERIAL_RATE + 1   # the FIFO is empty this long after a write


def read_config(path):
    """``/debug`` -> {dev, link (one of ``LINKS``), host (None: broadcast),
    port}. OSError when the file is missing, ValueError or TypeError when
    it is not valid."""
    with open(path) as f:
        d = json.loads(f.read())
    link = d.get("link") or LINKS[0]
    if link not in LINKS:
        raise ValueError("link")
    return {"dev": str(d.get("dev") or "A"), "link": link, "host": d.get("host") or None,
            "port": int(d.get("port") or DEBUG_PORT)}


def read_secrets(path):
    """(ssid, password) from ``/secrets.py``; ValueError with a plain reason
    when it is missing or unusable (``start`` adds ``WIFI_FIX``). The reason
    never quotes the file (a syntax error message can show one of its lines)."""
    try:
        with open(path) as f:
            src = f.read()
    except OSError:
        raise ValueError("/secrets.py is not on the watch")
    g = {}
    ok = True
    try:
        exec(src, g)
    except Exception:  # noqa: BLE001 - any error: same plain answer, no details
        ok = False
    ssid = g.get("WIFI_SSID")
    pw = g.get("WIFI_PASSWORD")
    if not ok or ssid is None or ssid == "":
        raise ValueError("/secrets.py has no usable Wi-Fi name")
    if not isinstance(ssid, str) or not (pw is None or isinstance(pw, str)):
        # WLAN.connect raises TypeError for anything but text: an all-digit
        # password typed without quotes in a hand-written file is the likely case
        raise ValueError("the Wi-Fi name or password in /secrets.py is not in quotes")
    return ssid, pw or ""


def broadcast_addr(ip, mask):
    """Subnet broadcast address: ("192.168.1.40", "255.255.255.0") -> "192.168.1.255"."""
    a = ip.split(".")
    m = mask.split(".")
    return ".".join([str(int(a[i]) | (255 ^ int(m[i]))) for i in range(4)])


class SerialLink:
    """USB link: each record is one line on ``out`` (default the REPL's UART,
    ``sys.stdout.buffer``): the byte 0x1E, the compact JSON, ``\\n``.

    ``send`` (5 Hz) frames a record and queues it, cut once into pieces of at
    most ``SERIAL_FIFO`` bytes; a record that would leave more than
    ``SERIAL_QMAX`` bytes waiting is dropped whole (``drop``). ``pump(now)``
    writes whole pieces only while the modelled FIFO has room: it refills at
    ``SERIAL_RATE`` bytes per ms since the last write, up to ``SERIAL_FIFO``.
    So a write never waits (``print`` would, while the FIFO is full), and
    ``pump`` allocates nothing. The cuts fall every ``SERIAL_FIFO`` bytes of
    the queued stream, not of each record, so a pump that finds the FIFO
    empty fills all of it. The runtime pumps once per loop pass and after
    each ~4 ms strip of a frame, so the FIFO refills about 3 times per ~40 ms
    frame: well over the ~2.7 KB/s the records need, and the queue fills only
    in a burst."""

    sta = None               # no Wi-Fi: the radio stays as in normal play
    channel = None
    rp_ms = 1000             # the screen once a second (and at once when it changes)

    def __init__(self, dev="A", out=None):
        self.dev = dev
        self.out = sys.stdout.buffer if out is None else out
        self.n_tx = 0            # records written out
        self.drop = 0
        self.tx_err = 0
        self.err = None          # the last write error
        self.queued = 0          # bytes waiting
        self._q = [None] * SERIAL_SLOTS   # the pieces, a ring from _h
        self._h = 0
        self._n = 0
        self._end = 0                     # bytes queued since it was last empty, mod SERIAL_FIFO
        self._room = SERIAL_FIFO          # FIFO room right after the last write ...
        self._t = None                    # ... at this time (None: none yet)

    def send(self, data):
        """Queue one record (str or UTF-8 bytes of compact JSON); False when it
        was dropped."""
        b = data.encode() if isinstance(data, str) else data
        n = len(b) + 2
        a = SERIAL_FIFO - self._end           # the first piece fills up the last FIFO load
        k = 1 if n <= a else 1 + (n - a + SERIAL_FIFO - 1) // SERIAL_FIFO
        if self.queued + n > SERIAL_QMAX or self._n + k > SERIAL_SLOTS:
            self.drop += 1
            return False
        line = memoryview(b"\x1e" + b + b"\n")
        q = self._q
        i = (self._h + self._n) % SERIAL_SLOTS
        q[i] = line[:a]
        for c in range(a, n, SERIAL_FIFO):
            i = (i + 1) % SERIAL_SLOTS
            q[i] = line[c:c + SERIAL_FIFO]
        self._n += k
        self.queued += n
        self._end = (self._end + n) % SERIAL_FIFO
        return True

    def pump(self, now):
        """Write the queued pieces that fit in the FIFO's room at ``now``."""
        n = self._n
        if not n:
            return
        room = SERIAL_FIFO
        t = self._t
        if t is not None:
            dt = ticks_diff(now, t)
            if dt < _EMPTY_MS:
                room = self._room + (SERIAL_RATE * dt if dt > 0 else 0)
                if room > SERIAL_FIFO:
                    room = SERIAL_FIFO
        while self._n:
            k = len(self._q[self._h])
            if k > room or not self._write_head():
                break
            room -= k
        if self._n != n:
            self._room = room
            self._t = now

    def drain(self):
        """Write out everything queued, waiting on the port as ``print`` does
        (loop exit, power off: the last records, such as ``crash``, get out)."""
        while self._n and self._write_head():
            pass

    def _write_head(self):
        """Write the oldest piece and take it off the queue; False (counted in
        ``tx_err``) when ``out`` refused it, as a closed file on the fake
        watches can (the watch's UART never does): tried again next pass."""
        h = self._h
        p = self._q[h]
        try:
            self.out.write(p)
        except Exception as e:  # noqa: BLE001 - a debug aid never stops the game
            self.tx_err += 1
            self.err = e
            return False
        self._q[h] = None
        self._h = (h + 1) % SERIAL_SLOTS
        self._n -= 1
        if not self._n:
            self._end = 0
        self.queued -= len(p)
        if p[-1] == 10:          # the line's last piece: the record is out
            self.n_tx += 1
        return True

    def stats(self):
        """Counters for ``Runtime.stats()``, like ``DebugLink.stats``."""
        return {"dev": self.dev, "link": "usb", "tx": self.n_tx, "drop": self.drop,
                "queued": self.queued, "tx_err": self.tx_err,
                "err": None if self.err is None else repr(self.err)}

    def close(self):
        """Nothing to close: ``out`` is the REPL's port (or the caller's file)."""


class DebugLink:
    """UDP sender to the laptop (``tools/debug_server.py``) with counters.
    ``join`` first connects the watch to the access point; the fake watches
    only ``open`` and ``send``."""

    rp_ms = 200              # the screen with every 5 Hz state record

    def __init__(self, dev="A", host=None, port=DEBUG_PORT):
        self.dev = dev
        self.host = host
        self.port = port
        self.sta = None          # the joined STA interface (Board: radio in associated mode)
        self.channel = None      # the access point's channel
        self.ip = None
        self.bcast = None        # subnet broadcast address
        self.dest = None         # (host, port) the datagrams go to
        self.why = None          # plain reason the last join/open failed
        self.n_tx = 0
        self.tx_err = 0
        self.err = None          # the last send error
        self._s = None

    def join(self, ssid, password, timeout_ms=JOIN_MS, sleep=None):
        """Connect to the access point; True once it gave the watch an
        address. Any error is a reason, never raised: on failure ``why``
        says what went wrong and the STA is disconnected (left trying, it
        would hop channels under ESP-NOW). Ctrl-C disconnects it too, then
        goes on up."""
        sleep = sleep or _sleep_ms
        sta = None
        joined = False
        try:
            import network
            sta = network.WLAN(network.STA_IF)
            sta.active(True)
            sta.connect(ssid, password)
            t = 0
            while not sta.isconnected() and t < timeout_ms:
                sleep(JOIN_STEP_MS)
                t += JOIN_STEP_MS
            if sta.isconnected():
                ip, mask = sta.ifconfig()[:2]
                ch = sta.config("channel")
                bcast = broadcast_addr(ip, mask)
                self.sta, self.channel, self.ip, self.bcast = sta, ch, ip, bcast
                joined = True
                return True
            self.why = _join_failed(timeout_ms, self.dev)
        except Exception as e:  # noqa: BLE001 - TypeError, RuntimeError('Wifi Unknown Error') too
            self.why = _wifi_error(e)
        finally:
            if not joined and sta is not None:
                try:
                    sta.disconnect()
                except Exception:  # noqa: BLE001 - already down
                    pass
        return False

    def open(self):
        """UDP socket to ``host:port`` (the subnet broadcast address without a
        host); False with ``why`` when there is nowhere to send or no socket."""
        host = self.host or self.bcast
        if not host:
            self.why = "no laptop address in /debug and no Wi-Fi address to broadcast on"
            return False
        try:
            import socket
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            if not self.host:
                s.setsockopt(socket.SOL_SOCKET, getattr(socket, "SO_BROADCAST", 0x20), 1)
            s.setblocking(False)        # a full TX queue fails the send, never blocks the loop
        except (ImportError, OSError) as e:
            self.why = "no UDP socket (%s)" % e
            return False
        self._s = s
        self.dest = (host, self.port)
        return True

    def send(self, data):
        """Send one datagram (str or bytes); False when it failed (counted in
        ``tx_err``, the error kept in ``err``). Never raises."""
        s = self._s
        if s is None:
            self.tx_err += 1
            return False
        try:
            s.sendto(data.encode() if isinstance(data, str) else data, self.dest)
        except Exception as e:  # noqa: BLE001 - a debug aid never stops the game
            self.tx_err += 1
            self.err = e
            return False
        self.n_tx += 1
        return True

    def pump(self, now):
        """Nothing waits: each datagram left in ``send``."""

    def drain(self):
        """Nothing waits: each datagram left in ``send``."""

    def close(self):
        """Close the socket and leave the access point."""
        s, sta = self._s, self.sta
        self._s = self.sta = None
        if s is not None:
            try:
                s.close()
            except Exception:  # noqa: BLE001 - already closed
                pass
        if sta is not None:            # even when the socket would not close
            try:
                sta.disconnect()
            except Exception:  # noqa: BLE001 - already down
                pass

    def stats(self):
        """Counters for ``Runtime.stats()`` (no Wi-Fi name or password)."""
        return {"dev": self.dev, "link": "wifi",
                "dest": None if self.dest is None else "%s:%d" % self.dest,
                "channel": self.channel, "ip": self.ip, "tx": self.n_tx, "tx_err": self.tx_err,
                "err": None if self.err is None else repr(self.err)}


def _join_failed(timeout_ms, dev):
    """Why the join timed out. The ESP32 keeps retrying a wrong password or an
    unknown name (``status()`` stays ``STAT_CONNECTING``), so the watch cannot
    tell them apart: one message names both."""
    return ("could not join the Wi-Fi in %d s (is it a 2.4 GHz network in range? "
            "A wrong name or password looks the same: %s)"
            % (timeout_ms // 1000, WIFI_FIX % dev))


def _wifi_error(e):
    """Plain reason for an exception from the Wi-Fi calls. OSError and
    RuntimeError carry the port's fixed texts ("Wifi Internal Error", "Wifi
    Unknown Error 0x0102"); anything else shows only its type, so no value
    from /secrets.py can reach the message."""
    if isinstance(e, (OSError, RuntimeError)):
        return "Wi-Fi error (%s)" % (str(e) or type(e).__name__)
    return "Wi-Fi error (%s)" % type(e).__name__


def start(config=None, secrets=None, timeout_ms=None, sleep=None):
    """main.py's debug switch -> ``(link, message)``: ``(None, None)``
    without ``/debug``; ``(None, why)`` when debug mode cannot start (the
    game plays normally); ``(SerialLink, how the laptop reads it)`` for USB;
    ``(DebugLink, where)`` once joined to the Wi-Fi and ready to send.
    Paths and the timeout default to the module's ``CONFIG``, ``SECRETS``
    and ``JOIN_MS``."""
    try:
        cfg = read_config(config or CONFIG)
    except OSError:
        return None, None
    except (TypeError, ValueError, AttributeError):
        return None, OFF_MSG % "/debug is not valid (run tools/deploy.py --debug A again)"
    if cfg["link"] == "usb":
        return SerialLink(cfg["dev"]), USB_MSG % cfg["dev"]
    try:
        ssid, password = read_secrets(secrets or SECRETS)
    except ValueError as e:
        return None, OFF_MSG % ("%s (%s)" % (e, WIFI_FIX % cfg["dev"]))
    link = DebugLink(cfg["dev"], cfg["host"], cfg["port"])
    if not link.join(ssid, password, timeout_ms or JOIN_MS, sleep) or not link.open():
        link.close()
        return None, OFF_MSG % link.why
    return link, ("debug mode: watch %s sends to %s:%d on Wi-Fi channel %s "
                  "(both watches must join the same access point; a mesh or extender "
                  "network can put them on different channels)"
                  % (link.dev, link.dest[0], link.dest[1], link.channel))
