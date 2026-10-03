"""ESP-NOW broadcast radio (``EspNowRadio``) and an in-memory twin (``SimRadio``).

Both share one interface::

    r.begin(channel=6, txpower=20)
    r.maybe_send(now, beacon_buf)       # jittered 45-55 ms schedule
    r.poll(now, monitor=link)           # drain RX into a LinkMonitor ...
    r.poll(now, callback=fn)            # ... or fn(mac, buf, n, rssi, t_rx)
    r.stats()

RX drains ``recvinto(data, 0)`` into one preallocated
``[mac, bytearray(250), rssi, t_ms]`` list until it returns 0 (no irq, no
peers_table). ``buf`` handed to monitor/callback is that shared bytearray:
copy anything you need to keep. Hardware modules are imported in ``begin``
so this file (and SimRadio) also loads on CPython / the simulator.
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

    def __init__(self, seed=0xACE1, period_ms=50, jitter_ms=5):
        self.sched = TxScheduler(period_ms, jitter_ms, seed)
        self._rx = [None, bytearray(MAX_LEN), 0, 0]
        self.channel = DEFAULT_CHANNEL
        self.txpower = 20
        self.max_drain = MAX_DRAIN
        self.n_tx = 0
        self.n_tx_err = 0
        self.n_rx = 0
        self.n_rx_err = 0
        self.n_polls = 0
        self.drain_max = 0
        self.last_tx_t = None

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
        self.last_tx_t = now
        return True

    def poll(self, now, monitor=None, callback=None):
        """Drain pending frames; returns how many were handled."""
        d = self._rx
        c = 0
        self.n_polls += 1
        while c < self.max_drain:
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
            if monitor is not None:
                monitor.on_packet(d[0], d[1], n, d[2], t)
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
    """ESP-NOW broadcast on the STA interface (never associated during play)."""

    def __init__(self, channel=DEFAULT_CHANNEL, txpower=20, rxbuf=2048, seed=None, **kw):
        if seed is None:
            from finder.compat import ticks_ms
            seed = ticks_ms() & 0xFFFF
        _Radio.__init__(self, seed, **kw)
        self.channel = channel
        self.txpower = txpower
        self.rxbuf = rxbuf
        self.mac = None
        self._sta = None
        self._e = None

    def begin(self, channel=None, txpower=None):
        import network
        import espnow
        if channel is not None:
            self.channel = channel
        if txpower is not None:
            self.txpower = txpower
        if self.channel not in CHANNELS:
            raise ValueError("channel must be 1, 6 or 11")
        sta = network.WLAN(network.STA_IF)
        sta.active(True)
        try:
            sta.disconnect()      # boot.py may have joined an AP (pins the channel)
        except OSError:
            pass
        sta.config(channel=self.channel)
        sta.config(txpower=self.txpower)
        sta.config(pm=sta.PM_NONE)
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

    def close(self):
        if self._e is not None:
            self._e.active(False)

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
    """In-memory radio for tests and the simulator.

    ``connect(other, rssi, loss)`` links two SimRadios both ways; ``rssi`` may
    be an int or ``fn(t_ms) -> int``. ``inject`` queues an arbitrary frame.
    Frames are delivered on the receiver's next ``poll`` (same channel only).
    """

    def __init__(self, mac=None, seed=0xACE1, **kw):
        _Radio.__init__(self, seed, **kw)
        self.mac = bytes(mac) if mac is not None else b"\x02\x00\x00\x00\x00\x01"
        self.links = []       # [other, rssi, loss]
        self.inbox = []       # (mac, bytes, rssi, t_ms)
        self.sent = []        # bytes of every frame sent (for tests)
        self.keep_sent = True
        self.active = False
        self._loss_rng = Xorshift16(seed ^ 0x5A5A)

    def begin(self, channel=None, txpower=None):
        if channel is not None:
            self.channel = channel
        if txpower is not None:
            self.txpower = txpower
        self.active = True
        return self

    def close(self):
        self.active = False

    def connect(self, other, rssi=-50, loss=0.0):
        self.links.append([other, rssi, loss])
        other.links.append([self, rssi, loss])

    def inject(self, mac, msg, rssi=-60, t_ms=0):
        self.inbox.append((bytes(mac), bytes(msg), rssi, t_ms))

    def _send(self, buf, now):
        if not self.active:
            raise OSError("not active")
        msg = bytes(buf)
        if self.keep_sent:
            self.sent.append(msg)
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
