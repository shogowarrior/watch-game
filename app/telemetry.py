"""Optional field-test telemetry: a JSON Lines ring buffer (user-research §4.5).

    tele = Telemetry(dev="A", sid="s1")       # RAM ring, 5 Hz state records
    rt = Runtime(board, telemetry=tele)       # the runtime calls record()/event()
    >>> import app; app.rt.tele.dump()        # REPL: print the ring (JSONL)
    >>> import os; os.mkdir("/log")           # once, then
    >>> app.rt.tele.save("/log/s1_A.jsonl")   # write the ring to flash

Field tests log to flash as they go: ``session("A")`` appends to
``/log/<n>_A.jsonl``; main.py builds it when ``/tele`` exists
(``tools/deploy.py --tele A``). Pull the files with ``mpremote fs cp
:/log/0_A.jsonl .``. The RAM ring alone holds ~3 min (900 records at 5 Hz),
and on battery the hardware WDT reboots the watch ~8 s after Ctrl-C, before
any REPL dump. A state record is ~400 bytes (~120 KB a minute at 5 Hz), so
check free flash (``os.statvfs("/")``) between sessions; a failed write ends
the file (``err`` says why) and the ring carries on in RAM.

State records are ``{"ev": "s", ...}`` at ``hz`` (5 by default) with a
subset of the §4.5 A fields (rssi, rssi_f, d_est/d_lo/d_hi, zone, trend,
trend_c, lost_s, steps, act, steps_since_scan, arrow_deg, ui, scr, bl, fps,
batt_pct, batt_mv, chg, p_batt) plus ``sub`` (screen sub-state), ``cone``,
``hz`` (beacon rate), ``buzz`` and link counters (``seq``, ``peer_seq``,
``rx``, ``loss``) for cross-watch alignment. Events (``btn``, ``touch``,
``tap``, ``haptic``, ``pwr``, ``crash`` (``e``: the exception that stopped
the loop; the ring is flushed then), and ``bcn_rx`` with ``beacons=True``)
go in the same ring. Not logged: ``role``, ``fw``/``uiv``, ``arrow_c`` and the
``bcn_tx``, ``scan_*``, ``probe_*``, ``found_*``, ``mark`` and ``clap``
events; scan and found timing come from the ``ui``/``sub`` changes in the
5 Hz records. With ``path`` set, new lines are appended to that file every
``flush_ms`` (buffered, to limit flash wear); leave it None for RAM only.

Debug mode (docs/design/debug-mode.md) adds a ``sink``: one of
hal/debuglink.py's links to the laptop, USB serial or Wi-Fi (main.py sets
it when ``/debug`` exists; tools/fake_watches.py sends the same records).
Then every record also carries ``dev`` and ``mac`` (the last 3 bytes of the
radio MAC, hex, set by the runtime) and goes to the sink: at each 5 Hz state
record the events since the last one, the state record, and an ``rp``
record (``{"ev": "rp", "on": screen on, "bl": backlight 0-100, "ch": the
sink's Wi-Fi channel or null, "p": the RenderParams as JSON}``) that lets
the page draw the watch's screen. ``rp`` goes once the sink's ``rp_ms`` has
passed since the last one (Wi-Fi 200: with every state record; USB 1000),
and at once when the screen, its sub-state or its power changes. ``rp``
records go only to the sink, never into the ring or the file. Nothing is
sent from the render loop; a forced ``flush`` (loop exit, power off) sends
what is left and drains the sink (USB: writes out what waits). Records are
compact JSON (no spaces after ``,`` and ``:``), as UTF-8 bytes
(MicroPython's ``json.dumps`` keeps non-ASCII text as it is); one longer
than ``DGRAM_MAX`` bytes (one Wi-Fi frame) goes without its longest
optional fields rather than in pieces, on both links. Without ``/tele``,
main.py keeps only a small ring: it just holds what waits for the next
send, and the laptop keeps the log.

Records allocate (a dict and a string) but only at 5 Hz plus rare events.
"""

import json

from finder.compat import ticks_add, ticks_diff
from finder.render_params import to_dict

ACT_NAMES = ("unknown", "still", "walk", "run")
DGRAM_MAX = 1400                          # bytes: one Wi-Fi frame, no IP fragments
DGRAM_KEEP = ("dev", "mac", "t", "ev")    # never dropped to make a datagram fit
SEP = (",", ":")                          # compact JSON: no spaces after , and :


def _r1(v):
    return None if v is None else round(v, 1)


def _pct(level):
    return int((level or 0.0) * 100 + 0.5)


def _fit(s):
    """JSON text ``s`` -> the datagram: its UTF-8 bytes, or when they are
    longer than ``DGRAM_MAX``, the record re-encoded without its longest
    optional fields (a long crash message); None if even ``DGRAM_KEEP`` does
    not fit. Bytes, not characters, are measured: MicroPython's ``json.dumps``
    writes non-ASCII text as several bytes per character. The one encode is
    the one the sender would make anyway."""
    b = s.encode()
    if len(b) <= DGRAM_MAX:
        return b
    d = json.loads(s)
    while len(b) > DGRAM_MAX:
        big = None
        n = 0
        for k in d:
            m = 0 if k in DGRAM_KEEP else len(json.dumps(d[k], separators=SEP).encode())
            if m > n:
                big, n = k, m
        if big is None:
            return None
        del d[big]
        b = json.dumps(d, separators=SEP).encode()
    return b


def _write(path, mode, lines):
    with open(path, mode) as f:
        for s in lines:
            f.write(s)
            f.write("\n")


def session(dev, logdir="/log"):
    """Telemetry appending to ``<logdir>/<n>_<dev>.jsonl``, ``n`` one past
    the highest session number already there (a deleted log never makes two
    sessions share a file); creates ``logdir``."""
    import os
    try:
        os.mkdir(logdir)
    except OSError:
        pass                       # already there
    n = 0
    for f in os.listdir(logdir):
        i = f.find("_")
        if i > 0 and f[:i].isdigit() and int(f[:i]) >= n:
            n = int(f[:i]) + 1
    sid = str(n)
    return Telemetry(dev=dev, sid=sid, path="%s/%s_%s.jsonl" % (logdir, sid, dev))


class Telemetry:
    """Ring of JSONL strings; newest ``cap`` records are kept."""

    def __init__(self, cap=900, hz=5, dev=None, sid=None, path=None, flush_ms=2000,
                 beacons=False, sink=None):
        self.cap = cap
        self.period_ms = 1000 // hz if hz > 0 else 0
        self.dev = dev
        self.sid = sid
        self.path = path
        self.flush_ms = flush_ms
        self.beacons = beacons     # also log every received beacon (bcn_rx)
        self.sink = sink           # debug mode: a hal/debuglink.py link, ``send(bytes)`` per record
        self.mac = None            # datagram ``mac`` (``set_mac``)
        self.err = None            # the OSError that ended writing to ``path``
        self.clear()

    def clear(self):
        self._ring = [None] * self.cap
        self._i = 0
        self.n = 0                 # records ever added
        self._flushed = 0          # records already written to ``path``
        self._sent = 0             # records already handed to ``sink``
        self._next = None
        self._flush_t = None
        self.dropped = 0           # records overwritten before a flush
        self._rp_t = None          # the last ``rp``: when, and the screen it showed
        self._rp_scr = self._rp_sub = self._rp_on = None

    def set_mac(self, mac):
        """The radio's MAC (6 bytes) -> ``mac``: its last 3 bytes, lowercase hex."""
        self.mac = "%02x%02x%02x" % (mac[3], mac[4], mac[5])

    # ---- writing ----
    def add(self, d):
        """Append one record (a dict); with a ``sink`` it also gets ``dev`` and ``mac``."""
        if self.sink is not None:
            d["dev"] = self.dev
            d["mac"] = self.mac
        s = json.dumps(d, separators=SEP)
        i = self._i
        self._ring[i] = s
        self._i = (i + 1) % self.cap
        self.n += 1
        if self.path is not None and self.n - self._flushed > self.cap:
            self.dropped += self.n - self._flushed - self.cap
            self._flushed = self.n - self.cap

    def event(self, t, ev, a=None, b=None, c=None):
        """Event record: ``ev`` plus up to three (key, value) pairs."""
        d = {"t": t, "ev": ev}
        for kv in (a, b, c):
            if kv is not None:
                d[kv[0]] = kv[1]
        self.add(d)

    def due(self, now):
        """True when a state record is due (``hz``)."""
        if not self.period_ms:
            return False
        nx = self._next
        if nx is None or ticks_diff(now, nx) >= 0:
            self._next = ticks_add(now if nx is None or ticks_diff(now, nx) > self.period_ms
                                   else nx, self.period_ms)
            return True
        return False

    def record(self, now, rt):
        """State record from a ``Runtime`` (call when ``due``). Reads its
        ``game``, ``link``, ``tx``, ``params``, ``screen_is_on``, ``bl_level``,
        ``fps``, ``batt_mv`` and ``batt_chg`` (tools/fake_watches.py has the same)."""
        g = rt.game
        p = g.params
        a = g.arrow
        est = g.est
        link = rt.link
        age = link.age_ms(now)
        d = {
            "t": now, "ev": "s", "sid": self.sid, "dev": self.dev,
            "rssi": g.rssi_last, "rssi_f": _r1(est.rssi_f),
            "d_est": _r1(est.dist_m), "d_lo": _r1(est.dist_lo_m), "d_hi": _r1(est.dist_hi_m),
            "zone": g.px.zone, "trend": est.trend, "trend_c": _r1(est.trend_conf),
            "lost_s": None if age is None else _r1(age / 1000.0),
            "steps": g.me.steps, "act": ACT_NAMES[g.me.activity & 3],
            "steps_since_scan": None if a is None else a.steps_walked,
            "arrow_deg": _r1(p.arrow_deg), "cone": _r1(p.cone_deg),
            "ui": g.screen, "sub": p.sub,
            "scr": rt.screen_is_on, "bl": _pct(rt.bl_level),
            "fps": _r1(rt.fps), "batt_pct": g.battery, "batt_mv": rt.batt_mv,
            "chg": rt.batt_chg,
            "p_batt": g.peer.battery,
            "seq": rt.tx.seq, "peer_seq": None if link.last_seq < 0 else link.last_seq,
            "rx": link.n_rx, "loss": link.loss_pct(),
            "hz": g.beacon_hz, "buzz": g.buzz,
        }
        self.add(d)
        if self.sink is not None:
            self.send()
            self._send_rp(now, rt)

    # ---- debug sink ----
    def send(self):
        """Hand the records added since the last send to ``sink`` (a datagram
        or a line each; each state record does this, and so does a forced
        ``flush``). Returns how many were due."""
        sk = self.sink
        k = self.n - self._sent
        if sk is None or k <= 0:
            return 0
        self._sent = self.n
        for s in self.lines(k):            # at most ``cap``: older ones were overwritten
            s = _fit(s)
            if s is not None:
                sk.send(s)
        return k

    def _send_rp(self, now, rt):
        """The ``rp`` record, when due (``_rp_due``): the latest RenderParams,
        the screen's state and the Wi-Fi channel (``ch``: the page tells two
        watches on different channels apart; null on USB and the fake watches)."""
        p = rt.params
        on = rt.screen_is_on
        if p is None or not self._rp_due(now, p, on):
            return
        sk = self.sink
        s = _fit(json.dumps({"t": now, "ev": "rp", "dev": self.dev, "mac": self.mac,
                             "on": on, "bl": _pct(rt.bl_level),
                             "ch": sk.channel, "p": to_dict(p)}, separators=SEP))
        if s is not None:
            sk.send(s)

    def _rp_due(self, now, p, on):
        """True when ``rp`` goes with this state record: the sink's ``rp_ms``
        after the last one, less half a record period (so the record after
        one a few ms late keeps its ``rp``), or at once when the screen, its
        sub-state or its power changed."""
        t = self._rp_t
        if (t is not None and p.screen == self._rp_scr and p.sub == self._rp_sub
                and on == self._rp_on
                and ticks_diff(now, t) < self.sink.rp_ms - self.period_ms // 2):
            return False
        self._rp_t = now
        self._rp_scr = p.screen
        self._rp_sub = p.sub
        self._rp_on = on
        return True

    # ---- reading ----
    def lines(self, n=None):
        """The newest ``n`` (default all) records, oldest first."""
        have = self.n if self.n < self.cap else self.cap
        if n is None or n > have:
            n = have
        out = []
        i = (self._i - n) % self.cap
        for _ in range(n):
            out.append(self._ring[i])
            i = (i + 1) % self.cap
        return out

    def dump(self, n=None):
        """Print the newest ``n`` records as JSON Lines (REPL / mpremote)."""
        for s in self.lines(n):
            print(s)

    def save(self, path, n=None):
        """Write the ring (or its newest ``n``) to ``path`` as JSONL; returns lines."""
        ls = self.lines(n)
        _write(path, "w", ls)
        return len(ls)

    # ---- flash ----
    def flush(self, now=None, force=False):
        """Append unflushed records to ``path`` (every ``flush_ms`` unless forced).
        ``force`` (loop exit, power off) also sends what ``sink`` has not had
        and drains it."""
        if force and self.sink is not None:
            self.send()
            self.sink.drain()
        if self.path is None:
            return 0
        if not force and now is not None:
            ft = self._flush_t
            if ft is not None and ticks_diff(now, ft) < self.flush_ms:
                return 0
            self._flush_t = now
        k = self.n - self._flushed
        if k <= 0:
            return 0
        ls = self.lines(k)
        try:
            _write(self.path, "a", ls)
        except OSError as e:
            self.err = e
            self.path = None           # no flash space / no dir: RAM only from now on
            return 0
        self._flushed = self.n
        return k
