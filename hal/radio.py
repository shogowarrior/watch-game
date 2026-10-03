"""ESP-NOW broadcast radio (``EspNowRadio``) and an in-memory twin for tests (``SimRadio``).

Both share one interface::

    r.begin(channel=6, txpower=20)
    r.set_rate(hz)                      # beacon rate (default 20 Hz), ~10 % jitter
    r.maybe_send(now, beacon_buf)       # broadcast if the jittered schedule is due
    r.poll(now, callback=fn)            # fn(mac, buf, n, rssi, t_rx) per frame
    r.stats()

RX drains ``recvinto(data, 0)`` into one preallocated
``[mac, bytearray(250), rssi, t_ms]`` list until it returns 0 (no irq, no
peers_table). ``buf`` handed to the callback is that shared bytearray: copy
anything you need to keep. Hardware modules are imported in ``begin`` so this
file (and SimRadio) also loads on CPython and in the tests.

Normal play never joins an access point: ``begin`` drops any connection and
uses channel 1, 6 or 11. Debug mode (hal/debuglink.py) joins one first, and
ESP-NOW shares the radio with that connection, so ``begin(sta=link.sta)``
puts the radio in associated mode for good: the connection is kept and
ESP-NOW runs on the access point's channel (any of 1-13).
"""

from finder.compat import ticks_diff
from finder.link import TxScheduler, Xorshift16

BCAST = b"\xff" * 6
CHANNELS = (1, 6, 11)
DEFAULT_CHANNEL = 6
MAX_LEN = 250
MAX_DRAIN = 32          # frames per poll; keeps a flood from stalling a frame
RX_TS_MAX_AGE = 1000    # older/newer driver timestamps are replaced by `now`
TICKS_MASK = 0x3FFFFFFF  # espnow stamps raw u32 mp_hal_ticks_ms; ticks_ms() is mod 2**30


class _Radio:
    """Shared TX schedule, RX drain loop and counters."""

    def __init__(self, seed=0xACE1):
        self.sched = TxScheduler(seed=seed)
        self._rx = [None, bytearray(MAX_LEN), 0, 0]
        self.channel = DEFAULT_CHANNEL
        self.txpower = 20
        self.n_tx = 0
        self.n_tx_err = 0
        self.n_rx = 0
        self.n_rx_err = 0
        self.n_polls = 0
        self.drain_max = 0

    def set_rate(self, hz):
        """Beacon rate in Hz; jitter ~10 % of the period, >= 1 ms."""
        s = self.sched
        per = 1000 // hz
        if per != s.period:
            s.period = per
            s.jitter = per // 10 or 1

    def due(self, now):
        """True if ``maybe_send(now, ...)`` would send."""
        return self.sched.due(now)

    def next_due(self):
        """ticks_ms of the next scheduled beacon (None before the first)."""
        return self.sched.next_t

    def maybe_send(self, now, buf):
        """Broadcast ``buf`` if the jittered schedule is due; True if sent."""
        s = self.sched
        if not s.due(now):
            return False
        s.mark_sent(now)
        return self.send(buf, now)

    def send(self, buf, now=None):
        """Broadcast immediately (no schedule). False if the TX queue refused."""
        try:
            self._send(buf, now)
        except OSError:
            self.n_tx_err += 1
            return False
        self.n_tx += 1
        return True

    def poll(self, now, callback=None):
        """Drain pending frames into ``callback``; returns how many were handled."""
        d = self._rx
        c = 0
        self.n_polls += 1
        while c < MAX_DRAIN:
            try:
                n = self._recvinto(d)
            except OSError:
                self.n_rx_err += 1
                break
            if not n:
                break
            c += 1
            t = d[3] & TICKS_MASK
            a = ticks_diff(now, t)
            if a < 0 or a > RX_TS_MAX_AGE:
                t = now
            if callback is not None:
                callback(d[0], d[1], n, d[2], t)
        self.n_rx += c
        if c > self.drain_max:
            self.drain_max = c
        return c

    def stats(self):
        return {"channel": self.channel, "tx": self.n_tx, "tx_err": self.n_tx_err,
                "rx": self.n_rx, "rx_err": self.n_rx_err, "polls": self.n_polls,
                "drain_max": self.drain_max}


class EspNowRadio(_Radio):
    """ESP-NOW broadcast on the STA interface (associated only in debug mode)."""

    def __init__(self, channel=DEFAULT_CHANNEL, txpower=20, rxbuf=2048, seed=None):
        if seed is None:
            from finder.compat import ticks_ms
            seed = ticks_ms() & 0xFFFF
        _Radio.__init__(self, seed)
        self.channel = channel
        self.txpower = txpower
        self.rxbuf = rxbuf
        self.mac = None
        self.associated = False     # debug mode: the STA stays joined to an access point
        self._sta = None
        self._e = None

    def begin(self, channel=None, txpower=None, sta=None):
        """Start ESP-NOW (again). ``sta``: a STA interface joined to an access
        point (debug mode): from then on the connection is kept and ESP-NOW
        uses the access point's channel, 1-13 (``channel`` is ignored).
        Otherwise any connection is dropped and ``channel`` must be 1, 6 or 11."""
        import network
        import espnow
        if sta is not None:
            self._sta = sta
            self.associated = True
        if txpower is not None:
            self.txpower = txpower
        if not self.associated:
            if channel is not None:
                self.channel = channel
            if self.channel not in CHANNELS:
                raise ValueError("channel must be 1, 6 or 11")
        sta = self._sta or network.WLAN(network.STA_IF)   # one STA (one MAC) per radio, like self._e
        sta.active(True)
        if self.associated:
            ch = sta.config("channel")    # the AP's; changing it would break the connection
            if not 1 <= ch <= 13:
                raise ValueError("access point channel %d: ESP-NOW needs 1-13" % ch)
            self.channel = ch
        else:
            try:
                sta.disconnect()      # a notebook may have joined an AP (that pins the channel)
            except OSError:
                pass
            sta.config(channel=self.channel)
        sta.config(txpower=self.txpower)
        sta.config(pm=sta.PM_NONE)      # no modem sleep (associated, it would miss ESP-NOW frames)
        self.mac = sta.config("mac")
        # mix our MAC into the jitter PRNG so two watches never beat in lock-step
        m = self.mac
        self.sched.rng = Xorshift16((self.sched.rng.x ^ (m[4] << 8) ^ m[5]) if m else self.sched.rng.x)
        e = self._e or espnow.ESPNow()
        e.config(rxbuf=self.rxbuf, timeout_ms=0)   # rxbuf applies at active(True)
        e.active(True)
        try:
            e.add_peer(BCAST)
        except OSError:           # ESP_ERR_ESPNOW_EXIST on re-begin
            pass
        self._sta = sta
        self._e = e
        return self

    def _send(self, buf, now):
        e = self._e
        if e is None:
            raise OSError("radio not started")
        e.send(BCAST, buf, False)

    def _recvinto(self, d):
        e = self._e
        if e is None:
            return 0
        return e.recvinto(d, 0)


class SimRadio(_Radio):
    """In-memory radio for tests (runtime, radio and ping-pong tests); the
    two-watch simulator models the link in ``sim/radio.py``.

    ``connect(other, rssi, loss)`` links two SimRadios both ways; ``rssi`` may
    be an int or ``fn(t_ms) -> int``.
    Frames are delivered on the receiver's next ``poll`` (same channel only).
    """

    def __init__(self, mac=None, seed=0xACE1):
        _Radio.__init__(self, seed)
        self.mac = bytes(mac) if mac is not None else b"\x02\x00\x00\x00\x00\x01"
        self.links = []       # [other, rssi, loss]
        self.inbox = []       # (mac, bytes, rssi, t_ms)
        self.active = False
        self._loss_rng = Xorshift16(seed ^ 0x5A5A)

    def begin(self, channel=None, txpower=None):
        if channel is not None:
            self.channel = channel
        if txpower is not None:
            self.txpower = txpower
        self.active = True
        return self

    def connect(self, other, rssi=-50, loss=0.0):
        self.links.append([other, rssi, loss])
        other.links.append([self, rssi, loss])

    def _send(self, buf, now):
        if not self.active:
            raise OSError("not active")
        msg = bytes(buf)
        t = 0 if now is None else now
        for o, rssi, loss in self.links:
            if not o.active or o.channel != self.channel:
                continue
            if loss > 0 and self._loss_rng.next() < loss * 65536:
                continue
            r = rssi(t) if callable(rssi) else rssi
            o.inbox.append((self.mac, msg, r, t))

    def _recvinto(self, d):
        if not self.inbox:
            return 0
        mac, msg, rssi, t = self.inbox.pop(0)
        n = len(msg)
        d[0] = mac
        d[1][:n] = msg
        d[2] = rssi
        d[3] = t
        return n
