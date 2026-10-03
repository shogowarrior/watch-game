"""Partner link bookkeeping: MAC lock, sample ring, loss, state, bump pairing.

Feed every received ESP-NOW frame to ``LinkMonitor.on_packet`` (hal/radio.py
does this from ``poll``) and call ``tick(now)`` once per frame. Estimators
read the per-packet sample ring (``s_t``/``s_rssi``/``s_seq``/``s_peer``)
with a cursor::

    k = link.oldest(cursor)
    while k < link.total:
        i = k % link.n
        est.update(link.s_t[i], link.s_rssi[i], link.s_peer[i])
        k += 1
    cursor = k

All per-packet work is integer-only and allocation-free.
"""

from array import array
from finder.compat import const, ticks_add, ticks_diff
from finder import proto

ST_IDLE = const(0)        # no partner locked
ST_CONNECTED = const(1)
ST_SEARCHING = const(2)   # partner silent > searching_ms
ST_LOST = const(3)        # partner silent > lost_ms
STATE_NAMES = ("IDLE", "CONNECTED", "SEARCHING", "LOST")

# on_packet results
R_BAD = const(0)          # not our magic/version/game
R_PARTNER = const(1)      # accepted into the sample ring
R_OTHER = const(2)        # valid beacon from a non-partner while locked
R_CANDIDATE = const(3)    # valid beacon while unlocked (pairing table)
R_DUP = const(4)          # partner frame with repeated seq

PAIR_MIN_RSSI = const(-35)
PAIR_TOL_MS = const(150)


class Xorshift16:
    """Tiny PRNG (period 65535); small-int math only, no allocation."""

    def __init__(self, seed=0xACE1):
        self.x = (seed & 0xFFFF) or 0xACE1

    def next(self):
        x = self.x
        x ^= (x << 7) & 0xFFFF
        x ^= x >> 9
        x ^= (x << 8) & 0xFFFF
        self.x = x
        return x


class TxScheduler:
    """Beacon schedule: every ``period_ms`` +/- ``jitter_ms`` (default 45-55 ms).

    Targets advance from the previous target so the mean rate stays 20 Hz even
    when the main loop is coarse; after a stall it restarts from ``now``.
    """

    def __init__(self, period_ms=50, jitter_ms=5, seed=0xACE1):
        self.period = period_ms
        self.jitter = jitter_ms
        self.rng = Xorshift16(seed)
        self.next_t = None

    def interval(self):
        j = self.jitter
        return self.period - j + self.rng.next() % (2 * j + 1)

    def due(self, now):
        return self.next_t is None or ticks_diff(now, self.next_t) >= 0

    def mark_sent(self, now):
        base = self.next_t
        if base is None or ticks_diff(now, base) >= self.period:
            base = now
        self.next_t = ticks_add(base, self.interval())


class LinkMonitor:
    """Tracks one partner; also a small table of pairing candidates."""

    def __init__(self, game_id=0, ring=64, loss_window=40, searching_ms=1000,
                 lost_ms=5000, max_candidates=8, max_gap=200):
        self.game_id = game_id
        self.max_gap = max_gap      # seq jump beyond this = partner restarted
        self.n = ring
        self.w = loss_window
        self.searching_ms = searching_ms
        self.lost_ms = lost_ms
        self.s_t = array("i", [0] * ring)
        self.s_rssi = array("i", [0] * ring)
        self.s_seq = array("i", [0] * ring)
        self.s_peer = array("i", [0] * ring)   # partner's rssi_last of us
        self._gaps = array("i", [0] * loss_window)
        self.peer = proto.Beacon(game_id)       # latest partner beacon
        k = max_candidates
        self._c_mac = [None] * k
        self._c_rssi = [0] * k
        self._c_t = [0] * k
        self._c_bump = [0] * k
        self._c_hasbump = [False] * k
        self.n_bad = 0
        self.n_other = 0
        self.n_dup = 0
        self.n_restart = 0
        self.partner = None
        self.reset_link()

    # -- partner lock -------------------------------------------------------
    def reset_link(self):
        """Forget samples/loss/state (keeps the partner lock and counters)."""
        self.total = 0
        self.n_rx = 0
        self.last_seq = -1
        self.last_seen = None
        self._ref_t = None          # lock time: SEARCHING -> LOST if never heard
        self.last_rssi = proto.RSSI_NONE
        self._gi = 0
        self._gn = 0
        self._gsum = 0
        self.state = ST_IDLE
        self.state_t = 0
        self.changes = 0

    def lock(self, mac, t_seen=None, now=None):
        """Lock onto ``mac``; ``t_seen`` (ticks_ms) counts as a sighting.

        Without ``t_seen`` the silence clock starts at ``now`` (or the next
        ``tick``), so a partner that is never heard still reaches LOST.
        """
        self.partner = bytes(mac)
        self.reset_link()
        self.last_seen = t_seen
        self._ref_t = t_seen if t_seen is not None else now
        self.state = ST_CONNECTED if t_seen is not None else ST_SEARCHING
        if self._ref_t is not None:
            self.state_t = self._ref_t

    def unlock(self):
        self.partner = None
        self.clear_candidates()
        self.reset_link()

    # -- receive path -------------------------------------------------------
    def on_packet(self, mac, buf, n, rssi, t_rx):
        """Classify and record one frame; returns an R_* code."""
        if not proto.valid(buf, n, self.game_id):
            self.n_bad += 1
            return R_BAD
        if self.partner is None:
            self._candidate(mac, buf, rssi, t_rx)
            return R_CANDIDATE
        if mac != self.partner:
            self.n_other += 1
            return R_OTHER
        seq = buf[4] | (buf[5] << 8)
        if self.n_rx:
            gap = (seq - self.last_seq) & 0xFFFF
            if gap == 0:
                self.n_dup += 1
                self.last_seen = t_rx
                return R_DUP
            if gap > self.max_gap:  # backwards or implausible jump: restarted
                self.n_restart += 1
                self._gi = 0
                self._gn = 0
                self._gsum = 0
            else:
                self._push_gap(gap)
        self.last_seq = seq
        self.peer.unpack_from(buf)
        i = self.total % self.n
        self.s_t[i] = t_rx
        self.s_rssi[i] = rssi
        self.s_seq[i] = seq
        self.s_peer[i] = self.peer.rssi_last
        self.total += 1
        self.n_rx += 1
        self.last_seen = t_rx
        self.last_rssi = rssi
        return R_PARTNER

    def _push_gap(self, g):
        i = self._gi
        if self._gn == self.w:
            self._gsum -= self._gaps[i]
        else:
            self._gn += 1
        self._gaps[i] = g
        self._gsum += g
        i += 1
        self._gi = 0 if i == self.w else i

    # -- sample ring ----------------------------------------------------------
    def oldest(self, cursor=0):
        """First sample counter still in the ring at or after ``cursor``."""
        lo = self.total - self.n
        if lo < 0:
            lo = 0
        return cursor if cursor > lo else lo

    def idx(self, k):
        return k % self.n

    # -- health -------------------------------------------------------------
    def loss(self):
        """Fraction of partner beacons missed over the last ``loss_window``."""
        s = self._gsum
        if s == 0:
            return 0.0
        return (s - self._gn) / s

    def loss_pct(self):
        s = self._gsum
        return 0 if s == 0 else (100 * (s - self._gn) + s // 2) // s

    def age_ms(self, now):
        return None if self.last_seen is None else ticks_diff(now, self.last_seen)

    def tick(self, now):
        """Update and return ``state`` (ST_*)."""
        if self.partner is None:
            st = ST_IDLE
        else:
            ref = self.last_seen
            if ref is None:
                ref = self._ref_t
                if ref is None:     # locked without a time: start clock now
                    self._ref_t = ref = now
                    self.state_t = now
            a = ticks_diff(now, ref)
            if a > self.lost_ms:
                st = ST_LOST
            elif a > self.searching_ms or self.last_seen is None:
                st = ST_SEARCHING
            else:
                st = ST_CONNECTED
        if st != self.state:
            self.state = st
            self.state_t = now
            self.changes += 1
        return st

    # -- bump pairing -------------------------------------------------------
    def clear_candidates(self):
        for i in range(len(self._c_mac)):
            self._c_mac[i] = None
            self._c_hasbump[i] = False

    def _candidate(self, mac, buf, rssi, t_rx):
        cm = self._c_mac
        slot = -1
        old = -1
        old_slot = 0
        old_age = -1
        for i in range(len(cm)):
            m = cm[i]
            if m is None:
                if slot < 0:
                    slot = i
            elif m == mac:
                old = i
                break
            else:
                a = ticks_diff(t_rx, self._c_t[i])
                if a > old_age:
                    old_age = a
                    old_slot = i
        if old >= 0:
            i = old
            self._c_rssi[i] = (self._c_rssi[i] + rssi) >> 1
        else:
            i = slot if slot >= 0 else old_slot
            cm[i] = mac
            self._c_rssi[i] = rssi
        self._c_t[i] = t_rx
        ba = buf[14] | (buf[15] << 8)
        if ba == proto.BUMP_NONE:
            self._c_hasbump[i] = False
        else:
            # their bump time on my clock; air latency (~1-3 ms) ignored
            self._c_bump[i] = ticks_add(t_rx, -ba)
            self._c_hasbump[i] = True

    def nearby(self, now, fresh_ms=2000):
        """Number of valid senders heard in the last ``fresh_ms`` (unlocked)."""
        c = 0
        for i in range(len(self._c_mac)):
            if self._c_mac[i] is not None and ticks_diff(now, self._c_t[i]) <= fresh_ms:
                c += 1
        return c

    def find_partner(self, now, my_bump_t, min_rssi=PAIR_MIN_RSSI,
                     tol_ms=PAIR_TOL_MS, fresh_ms=1000):
        """Strongest fresh sender with rssi > ``min_rssi`` whose bump matches mine.

        Match test, with no clock sync: their bump age now is
        ``bump_ago_ms + (now - t_rx)``; mine is ``now - my_bump_t``; they must
        agree within ``tol_ms``. Returns the MAC or None.
        """
        if my_bump_t is None:
            return None
        best = None
        best_r = -1000
        for i in range(len(self._c_mac)):
            m = self._c_mac[i]
            if m is None or not self._c_hasbump[i]:
                continue
            if ticks_diff(now, self._c_t[i]) > fresh_ms:
                continue
            r = self._c_rssi[i]
            if r <= min_rssi:
                continue
            d = ticks_diff(self._c_bump[i], my_bump_t)
            if -tol_ms <= d <= tol_ms and r > best_r:
                best = m
                best_r = r
        return best

    def pair(self, now, my_bump_t, **kw):
        """``find_partner`` and lock onto it; returns the MAC or None."""
        m = self.find_partner(now, my_bump_t, **kw)
        if m is not None:
            t = None
            for i in range(len(self._c_mac)):
                if self._c_mac[i] == m:
                    t = self._c_t[i]
            self.lock(m, t)
            self.clear_candidates()
        return m
