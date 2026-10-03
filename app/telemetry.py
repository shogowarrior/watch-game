"""Optional field-test telemetry: a JSON Lines ring buffer (user-research §4.5).

    tele = Telemetry(dev="A", sid="s1")       # RAM ring, 5 Hz state records
    rt = Runtime(board, telemetry=tele)       # the runtime calls record()/event()
    >>> import app; app.rt.tele.dump()        # REPL: print the ring (JSONL)
    >>> app.rt.tele.save("/log/s1_A.jsonl")   # or write it to flash

State records are ``{"ev": "s", ...}`` at ``hz`` (5 by default) with the
§4.5 A fields the watch knows (rssi, rssi_f, d_est/d_lo/d_hi, zone, trend,
lost_s, steps, act, arrow_deg, ui, scr, bl, fps, batt_pct, batt_mv, p_batt)
plus link counters (``seq``, ``peer_seq``, ``rx``, ``loss``) for cross-watch
alignment. Events (``btn``, ``touch``, ``tap``, ``haptic``, ``pwr``) go in
the same ring. With ``path`` set, new lines are appended to that file every
``flush_ms`` (buffered, to limit flash wear); leave it None for RAM only.

Records allocate (a dict and a string) but only at 5 Hz plus rare events.
"""

import json

from finder.compat import ticks_add, ticks_diff

ACT_NAMES = ("unknown", "still", "walk", "run")


def _r1(v):
    return None if v is None else round(v, 1)


class Telemetry:
    """Ring of JSONL strings; newest ``cap`` records are kept."""

    def __init__(self, cap=900, hz=5, dev=None, sid=None, path=None, flush_ms=2000,
                 beacons=False):
        self.cap = cap
        self.period_ms = 1000 // hz if hz > 0 else 0
        self.dev = dev
        self.sid = sid
        self.path = path
        self.flush_ms = flush_ms
        self.beacons = beacons     # also log every received beacon (bcn_rx)
        self.enabled = True
        self.clear()

    def clear(self):
        self._ring = [None] * self.cap
        self._i = 0
        self.n = 0                 # records ever added
        self._flushed = 0          # records already written to ``path``
        self._next = None
        self._flush_t = None
        self.dropped = 0           # records overwritten before a flush

    # ---- writing ----
    def add(self, d):
        """Append one record (a dict, or an already-encoded JSON string)."""
        if not self.enabled:
            return
        s = d if isinstance(d, str) else json.dumps(d)
        i = self._i
        self._ring[i] = s
        self._i = (i + 1) % self.cap
        self.n += 1
        if self.path is not None and self.n - self._flushed > self.cap:
            self.dropped += self.n - self._flushed - self.cap
            self._flushed = self.n - self.cap

    def event(self, t, ev, a=None, b=None, c=None):
        """Event record: ``ev`` plus up to three (key, value) pairs."""
        if not self.enabled:
            return
        d = {"t": t, "ev": ev}
        for kv in (a, b, c):
            if kv is not None:
                d[kv[0]] = kv[1]
        self.add(d)

    def due(self, now):
        """True when a state record is due (``hz``)."""
        if not self.enabled or not self.period_ms:
            return False
        nx = self._next
        if nx is None or ticks_diff(now, nx) >= 0:
            self._next = ticks_add(now if nx is None or ticks_diff(now, nx) > self.period_ms
                                   else nx, self.period_ms)
            return True
        return False

    def record(self, now, rt):
        """State record from a ``Runtime`` (call when ``due``)."""
        g = rt.game
        p = g.params
        est = g.est
        link = rt.link
        age = link.age_ms(now) if link is not None else None
        d = {
            "t": now, "ev": "s", "sid": self.sid, "dev": self.dev,
            "rssi": g.rssi_last, "rssi_f": _r1(est.rssi_f),
            "d_est": _r1(est.dist_m), "d_lo": _r1(est.dist_lo_m), "d_hi": _r1(est.dist_hi_m),
            "zone": g.px.zone, "trend": est.trend, "trend_c": _r1(est.trend_conf),
            "lost_s": None if age is None else _r1(age / 1000.0),
            "steps": g.me.steps, "act": ACT_NAMES[g.me.activity & 3],
            "arrow_deg": None if p is None else _r1(p.arrow_deg),
            "cone": None if p is None else _r1(p.cone_deg),
            "ui": g.screen, "sub": None if p is None else p.sub,
            "scr": rt.screen_is_on, "bl": int((rt.bl_level or 0.0) * 100 + 0.5),
            "fps": _r1(rt.fps), "batt_pct": g.battery, "batt_mv": rt.batt_mv,
            "p_batt": g.peer.battery,
            "seq": rt.tx.seq, "peer_seq": None if link is None or link.last_seq < 0 else link.last_seq,
            "rx": None if link is None else link.n_rx,
            "loss": None if link is None else link.loss_pct(),
            "hz": g.beacon_hz, "buzz": g.buzz,
        }
        self.add(d)

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
        with open(path, "w") as f:
            for s in ls:
                f.write(s)
                f.write("\n")
        return len(ls)

    # ---- flash ----
    def flush(self, now=None, force=False):
        """Append unflushed records to ``path`` (every ``flush_ms`` unless forced)."""
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
            with open(self.path, "a") as f:
                for s in ls:
                    f.write(s)
                    f.write("\n")
        except OSError:
            self.path = None           # no flash space / no dir: RAM only from now on
            return 0
        self._flushed = self.n
        return k
