"""Partner link bookkeeping (MAC lock, seq dedup, loss window) and the TX schedule.

``app/runtime.py`` feeds every received ESP-NOW frame to
``LinkMonitor.on_packet`` from its radio callback and drops ``R_BAD`` and
``R_DUP`` frames; the partner lock follows ``game.pair.peer_mac``. All
per-packet work is integer-only and allocation-free.
"""

from array import array
from finder.compat import const, ticks_add, ticks_diff
from finder import proto

# on_packet results
R_BAD = const(0)          # not our magic/version/game
R_PARTNER = const(1)      # new frame from the locked partner
R_OTHER = const(2)        # valid beacon from a non-partner while locked
R_CANDIDATE = const(3)    # valid beacon while unlocked
R_DUP = const(4)          # partner frame with repeated seq


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
    """Beacon schedule: every ``period`` ms +/- ``jitter`` ms (hal radio default
    50 +/- 5 ms; the game sets the rate with ``radio.set_rate``).

    Targets advance from the previous target so the mean rate stays
    1000/``period`` Hz even when the main loop is coarse; after a stall it
    restarts from ``now``.
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
    """Tracks one partner: seq dedup, restarts, loss over the last ``loss_window`` gaps."""

    def __init__(self, game_id=0, loss_window=40, max_gap=200):
        self.game_id = game_id
        self.max_gap = max_gap      # seq jump beyond this = partner restarted
        self.w = loss_window
        self._gaps = array("i", [0] * loss_window)
        self.n_bad = 0
        self.n_other = 0
        self.n_dup = 0
        self.n_restart = 0
        self.partner = None
        self.reset_link()

    # -- partner lock -------------------------------------------------------
    def reset_link(self):
        """Forget seq/loss/last-seen (keeps the partner lock and counters)."""
        self.n_rx = 0
        self.last_seq = -1
        self.last_seen = None
        self._gi = 0
        self._gn = 0
        self._gsum = 0

    def lock(self, mac):
        """Lock onto ``mac`` (seq/loss/last-seen start fresh)."""
        self.partner = bytes(mac)
        self.reset_link()

    def unlock(self):
        self.partner = None
        self.reset_link()

    # -- receive path -------------------------------------------------------
    def on_packet(self, mac, buf, n, t_rx):
        """Classify and record one frame; returns an R_* code."""
        if not proto.valid(buf, n, self.game_id):
            self.n_bad += 1
            return R_BAD
        if self.partner is None:
            return R_CANDIDATE
        if mac != self.partner:
            self.n_other += 1
            return R_OTHER
        seq = proto.seq_of(buf)
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
        self.n_rx += 1
        self.last_seen = t_rx
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

    # -- health -------------------------------------------------------------
    def loss_pct(self):
        """Percent of partner beacons missed over the last ``loss_window`` gaps."""
        s = self._gsum
        return 0 if s == 0 else (100 * (s - self._gn) + s // 2) // s

    def age_ms(self, now):
        return None if self.last_seen is None else ticks_diff(now, self.last_seen)
