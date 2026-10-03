"""Debug mode link: join the Wi-Fi and send the watch's records to the laptop over UDP.

Debug mode (docs/design/debug-mode.md) shows the real watches in the web sim
page. ``main.py`` calls ``start()`` before ``Board.init``::

    link, msg = debuglink.start()   # (None, None) when /debug is missing
    if msg:
        print(msg)                  # where it sends, or why debug mode is off
    board.debug = link              # the radio then runs ESP-NOW on the Wi-Fi's channel

``start`` reads ``/debug`` (``{"dev": "A", "host": "192.168.1.23", "port":
47268}``, written by ``tools/deploy.py --debug A``) and ``/secrets.py``
(``WIFI_SSID``, ``WIFI_PASSWORD``), joins the access point (``JOIN_MS`` at
most) and opens a UDP socket to ``host:port``, or to the subnet broadcast
address when ``/debug`` has no host (broadcast is unreliable on the ESP32:
a fallback only). Whatever fails, it returns ``(None, why)`` and the game
plays normally (main.py also turns any unexpected error from ``start`` into
such a message). The Wi-Fi name and password are never printed, logged or
sent, and no reason quotes them.

ESP-NOW and the Wi-Fi connection share one radio, so once joined the access
point's channel (1-13) is the ESP-NOW channel too: ``Board`` starts
``EspNowRadio`` in its associated mode on ``link.sta``. Both watches must
join the same access point, or they will not hear each other: a watch whose
join failed plays on its usual channel (6), the joined one on the access
point's, so the two cannot find each other until both have joined (restart
the missing one). A mesh or extender network with one name can also put
them on different channels.

``send(data)`` sends one datagram (app/telemetry.py calls it from its 5 Hz
path, never from the render loop). Errors are counted in ``tx_err`` and
never raised. ``network`` and ``socket`` are imported only when used, so
this file also loads on CPython (tools/fake_watches.py sends through it)
and in the tests.
"""

import json

from finder.compat import sleep_ms as _sleep_ms

CONFIG = "/debug"
SECRETS = "/secrets.py"
DEBUG_PORT = 47268
JOIN_MS = 10000          # give up on the Wi-Fi after this and play normally
JOIN_STEP_MS = 100
OFF_MSG = "debug mode off: %s. Playing normally."


def read_config(path):
    """``/debug`` -> {dev, host (None: broadcast), port}. OSError when the
    file is missing, ValueError or TypeError when it is not valid."""
    with open(path) as f:
        d = json.loads(f.read())
    return {"dev": str(d.get("dev") or "A"), "host": d.get("host") or None,
            "port": int(d.get("port") or DEBUG_PORT)}


def read_secrets(path):
    """(ssid, password) from ``secrets.py``; ValueError with a plain reason
    when it is missing or unusable. The reason never quotes the file (a
    syntax error message can show one of its lines)."""
    try:
        with open(path) as f:
            src = f.read()
    except OSError:
        raise ValueError("secrets.py is not on the watch (tools/deploy.py --debug copies it)")
    g = {}
    ok = True
    try:
        exec(src, g)
    except Exception:  # noqa: BLE001 - any error: same plain answer, no details
        ok = False
    ssid = g.get("WIFI_SSID")
    pw = g.get("WIFI_PASSWORD")
    if not ok or ssid is None or ssid == "":
        raise ValueError("secrets.py has no usable WIFI_SSID (compare it with secrets.example.py)")
    if not isinstance(ssid, str) or not (pw is None or isinstance(pw, str)):
        # WLAN.connect raises TypeError for anything but text: an all-digit
        # password typed without quotes is the likely case
        raise ValueError("put the Wi-Fi name and password in quotes in secrets.py "
                         "(compare it with secrets.example.py)")
    return ssid, pw or ""


def broadcast_addr(ip, mask):
    """Subnet broadcast address: ("192.168.1.40", "255.255.255.0") -> "192.168.1.255"."""
    a = ip.split(".")
    m = mask.split(".")
    return ".".join([str(int(a[i]) | (255 ^ int(m[i]))) for i in range(4)])


class DebugLink:
    """UDP sender to the laptop (``tools/debug_server.py``) with counters.
    ``join`` first connects the watch to the access point; the fake watches
    only ``open`` and ``send``."""

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
            self.why = _join_failed(timeout_ms)
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
        return {"dev": self.dev, "dest": None if self.dest is None else "%s:%d" % self.dest,
                "channel": self.channel, "ip": self.ip, "tx": self.n_tx, "tx_err": self.tx_err,
                "err": None if self.err is None else repr(self.err)}


def _join_failed(timeout_ms):
    """Why the join timed out. The ESP32 keeps retrying a wrong password or an
    unknown name (``status()`` stays ``STAT_CONNECTING``), so the watch cannot
    tell them apart: one message names both."""
    return ("could not join the Wi-Fi in %d s (check the name and password in secrets.py, "
            "and that it is a 2.4 GHz network in range)" % (timeout_ms // 1000))


def _wifi_error(e):
    """Plain reason for an exception from the Wi-Fi calls. OSError and
    RuntimeError carry the port's fixed texts ("Wifi Internal Error", "Wifi
    Unknown Error 0x0102"); anything else shows only its type, so no value
    from secrets.py can reach the message."""
    if isinstance(e, (OSError, RuntimeError)):
        return "Wi-Fi error (%s)" % (str(e) or type(e).__name__)
    return "Wi-Fi error (%s)" % type(e).__name__


def start(config=None, secrets=None, timeout_ms=None, sleep=None):
    """main.py's debug switch -> ``(link, message)``: ``(None, None)``
    without ``/debug``; ``(None, why)`` when debug mode cannot start (the
    game plays normally); ``(link, where)`` once joined and ready to send.
    Paths and the timeout default to the module's ``CONFIG``, ``SECRETS``
    and ``JOIN_MS``."""
    try:
        cfg = read_config(config or CONFIG)
    except OSError:
        return None, None
    except (TypeError, ValueError, AttributeError):
        return None, OFF_MSG % "/debug is not valid (run tools/deploy.py --debug A again)"
    try:
        ssid, password = read_secrets(secrets or SECRETS)
    except ValueError as e:
        return None, OFF_MSG % e
    link = DebugLink(cfg["dev"], cfg["host"], cfg["port"])
    if not link.join(ssid, password, timeout_ms or JOIN_MS, sleep) or not link.open():
        link.close()
        return None, OFF_MSG % link.why
    return link, ("debug mode: watch %s sends to %s:%d on Wi-Fi channel %s "
                  "(both watches must join the same access point; a mesh or extender "
                  "network can put them on different channels)"
                  % (link.dev, link.dest[0], link.dest[1], link.channel))
