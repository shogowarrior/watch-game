"""PAIRING screen logic (ui-spec §6 PAIRING, §5.8 calibration).

    looking -> seen -> confirmed -> calibrate -> split -> done

* ``looking``: a nearby watch that is also pairing (screen PAIRING; one in
  calibrate or split is already paired) is heard 3 times within 2 s at a
  strong RSSI (relative to the nominal p1m) -> ``seen``. The
  ``SEEN_SLOTS`` strongest candidates are tracked at once (two pairs starting
  side by side); one is taken only if no other candidate heard within the
  last 2 s is stronger.
* ``seen``: both watches show the same 3 runes, taken from a hash of the two
  sorted MACs (tokens ``glyphs.runes.code``). A tap / short press confirms
  this side (``confirmed``); a matched bump confirms both at once.
  ``seen``/``confirmed`` return to ``looking`` when the partner is silent for
  5 s or has not shown PAIRING for 2 s (it paired with another watch, or its
  round started without this one).
* ``calibrate``: 3 s mean RSSI at 1 m. The fill pauses while the sd over
  the last 1 s exceeds 4 dB (``unstable`` -> chip ``HOLD STILL`` once paused
  for 0.7 s, so the chip does not flicker); the result is clamped to nominal +-6 dB; after
  10 s the nominal p1m is used (toast ``CAL SKIPPED``).
* ``split``: 30 s countdown (``split_s``), TICK at 3/2/1, CLOSER and ``GO`` at 0
  for 1 s.

``update(t)`` returns the haptic started this frame (or None); ``toast`` is a
one-shot set on the update that raised it. Pure logic, no radio access.
"""

import math
from array import array

from finder.compat import TickRing, ticks_diff
from finder import tuning as T
from finder.haptic_patterns import stronger

LOOKING = "looking"
SEEN = "seen"
CONFIRMED = "confirmed"
CALIBRATE = "calibrate"
SPLIT = "split"
DONE = "done"

SEEN_MIN_DB = -15.0        # candidate RSSI >= p1m + this (about 3.8 m at n 2.6)
SEEN_PACKETS = T.RELINK_PACKETS
SEEN_WINDOW_MS = T.RELINK_WINDOW_MS
SEEN_SLOTS = 4             # pairing candidates tracked at once
SEEN_LOST_MS = T.LINK_LOST_AFTER_MS   # candidate silent this long -> looking
UNSTABLE_SHOW_MS = 700     # fill paused this long before the chip says HOLD STILL (no flicker)
HINT_HOLD_STILL = "HOLD STILL"   # calibrate chip while ``unstable`` (the renderer keys on it)
TOAST_CAL_SKIPPED = "CAL SKIPPED"

_FNV_OFF = 0x811C9DC5
_FNV_PRIME = 0x01000193


def fnv1a32(data):
    """32-bit FNV-1a hash of a bytes-like object (same on every platform)."""
    h = _FNV_OFF
    for b in data:
        h = ((h ^ b) * _FNV_PRIME) & 0xFFFFFFFF
    return h


def rune_ids(mac_a, mac_b):
    """3 rune ids (0..7), 3 bits each of hash(sorted(mac_a, mac_b))."""
    a = bytes(mac_a)
    b = bytes(mac_b)
    if b < a:
        a, b = b, a
    h = fnv1a32(a + b)
    return (h & 7, (h >> 3) & 7, (h >> 6) & 7)


class Calibrator:
    """1 m RSSI calibration: stable-time-gated mean, clamped, with a skip timeout."""

    def __init__(self, nominal=T.P1M_NOMINAL_DBM, clamp_db=T.CAL_CLAMP_DB,
                 window_ms=T.CAL_WINDOW_MS, gate_ms=T.CAL_GATE_WINDOW_MS,
                 sd_max=T.CAL_UNSTABLE_SD_DB, skip_ms=T.CAL_SKIP_AFTER_MS, ring=64):
        self.nominal = nominal
        self.clamp_db = clamp_db
        self.window_ms = window_ms
        self.gate_ms = gate_ms
        self.sd_max = sd_max
        self.skip_ms = skip_ms
        self._n = ring
        self._t = array("i", [0] * ring)
        self._v = array("f", [0.0] * ring)
        self.reset(0)

    def reset(self, t_ms):
        self.t0 = t_ms
        self._last = t_ms
        self._k = 0             # samples written to the ring
        self.fill_ms = 0
        self.sum = 0.0
        self.count = 0
        self.stable = False
        self.sd = None
        self.done = False
        self.skipped = False
        self.p1m = None

    def add(self, t_ms, rssi):
        """One per-packet RSSI measured at ~1 m."""
        if self.done:
            return
        i = self._k % self._n
        self._t[i] = t_ms
        self._v[i] = rssi
        self._k += 1
        if self.stable:
            self.sum += rssi
            self.count += 1

    def _gate(self, t_ms):
        n = self._k if self._k < self._n else self._n
        s = 0.0
        ss = 0.0
        c = 0
        for j in range(n):
            i = (self._k - 1 - j) % self._n
            if ticks_diff(t_ms, self._t[i]) > self.gate_ms:
                break
            v = self._v[i]
            s += v
            ss += v * v
            c += 1
        if c < 3:
            self.sd = None
            return False
        mu = s / c
        var = ss / c - mu * mu
        self.sd = math.sqrt(var) if var > 0.0 else 0.0
        return self.sd <= self.sd_max

    def update(self, t_ms):
        """Advance the fill; returns True on the update that finished (or skipped)."""
        if self.done:
            return False
        dt = ticks_diff(t_ms, self._last)
        self._last = t_ms
        was = self.stable
        self.stable = self._gate(t_ms)
        if was and self.stable and dt > 0:
            self.fill_ms += dt
        if self.fill_ms >= self.window_ms and self.count > 0:
            m = self.sum / self.count
            lo = self.nominal - self.clamp_db
            hi = self.nominal + self.clamp_db
            self.p1m = lo if m < lo else hi if m > hi else m
            self.done = True
            return True
        if ticks_diff(t_ms, self.t0) >= self.skip_ms:
            self.p1m = self.nominal
            self.skipped = True
            self.done = True
            return True
        return False

    @property
    def digit(self):
        """Countdown digit (3/2/1 for a 3 s window) from the fill."""
        n = (self.window_ms + 999) // 1000
        d = n - self.fill_ms // 1000
        return 1 if d < 1 else n if d > n else d


class Pairing:
    """PAIRING sub-state machine for one watch."""

    def __init__(self, my_mac=None, nominal=T.P1M_NOMINAL_DBM):
        self.my_mac = bytes(my_mac) if my_mac is not None else None
        self.nominal = nominal
        self.cal = Calibrator(nominal)
        self.split_s = T.PAIR_SPLIT_S   # split countdown length, s (the web sim's demo shortens it)
        # looking: candidate table (MAC, packet times, last RSSI and time)
        self._c_mac = [None] * SEEN_SLOTS
        self._c_rx = [TickRing(SEEN_PACKETS) for _ in range(SEEN_SLOTS)]
        self._c_rssi = [0] * SEEN_SLOTS
        self._c_last = [0] * SEEN_SLOTS
        self.p1m = None
        self.reset(0)

    def reset(self, t_ms):
        """Back to ``looking``: forget the partner and calibration."""
        self.sub = LOOKING
        self.t_sub = t_ms
        self.peer_mac = None
        self.runes = None
        self.p1m = None
        self.confirmed = False
        self.peer_confirmed = False
        for k in range(SEEN_SLOTS):
            self._c_mac[k] = None
        self._last_rx = None
        self._last_pair = None     # the partner last showed PAIRING (seen/confirmed)
        self.countdown = None
        self.toast = None
        self.unstable = False
        self._unst_t = None
        self._digit = 0
        self._haptic = None

    def start_split(self, t_ms):
        """New round with the existing pairing and calibration."""
        self._set(SPLIT, t_ms)
        self.countdown = self.split_s
        self._digit = self.split_s

    def _set(self, sub, t_ms):
        self.sub = sub
        self.t_sub = t_ms

    def _emit(self, name):
        self._haptic = stronger(self._haptic, name)

    # ---- inputs ----------------------------------------------------------------
    def on_candidate(self, t_ms, mac, rssi, peer_pairing=True):
        """Any valid beacon while looking/seen/confirmed (``peer_pairing``: the
        sender shows PAIRING). Returns True if it is the partner."""
        if self.sub == LOOKING:
            if not peer_pairing or rssi < self.nominal + SEEN_MIN_DB:
                return False
            k = self._slot(mac, rssi)
            if k < 0:
                return False
            rx = self._c_rx[k]
            rx.note(t_ms)
            self._c_rssi[k] = rssi
            self._c_last[k] = t_ms
            if not rx.full_within(t_ms, SEEN_WINDOW_MS):
                return False
            for j in range(SEEN_SLOTS):
                if j != k and self._c_mac[j] is not None and self._c_rssi[j] > rssi:
                    return False          # a stronger candidate is still around
            self.peer_mac = self._c_mac[k]
            if self.my_mac is not None:
                self.runes = rune_ids(self.my_mac, self.peer_mac)
            else:
                self.runes = rune_ids(self.peer_mac, self.peer_mac)
            self._set(SEEN, t_ms)
            self._last_rx = t_ms
            self._last_pair = t_ms
            self._emit("DOUBLE")
            return True
        if self.peer_mac is not None and mac == self.peer_mac:
            self._last_rx = t_ms
            if peer_pairing:
                self._last_pair = t_ms
            return True
        return False

    def _slot(self, mac, rssi):
        """Candidate slot of ``mac``. A new MAC takes an empty slot, else the weakest
        one if it is stronger than that; otherwise -1 (it could never be taken)."""
        cm = self._c_mac
        cr = self._c_rssi
        k = 0
        for j in range(SEEN_SLOTS):
            m = cm[j]
            if m == mac:
                return j
            if cm[k] is not None and (m is None or cr[j] < cr[k]):
                k = j
        if cm[k] is not None and cr[k] >= rssi:
            return -1
        cm[k] = bytes(mac)            # the radio driver may reuse its buffer
        self._c_rx[k].clear()
        return k

    def on_rssi(self, t_ms, rssi):
        """Partner packet RSSI (own measurement); feeds the calibration."""
        self._last_rx = t_ms
        if self.sub == CALIBRATE:
            self.cal.add(t_ms, rssi)

    def confirm(self, t_ms):
        """Tap / short press in ``seen``: this side says the runes match."""
        if self.sub != SEEN:
            return False
        self.confirmed = True
        if self.peer_confirmed:
            self._start_cal(t_ms)
        else:
            self._set(CONFIRMED, t_ms)
        return True

    def bump(self, t_ms):
        """Matched bump in ``seen``/``confirmed``: both sides confirm at once."""
        if self.sub != SEEN and self.sub != CONFIRMED:
            return False
        self.confirmed = True
        self.peer_confirmed = True
        self._start_cal(t_ms)
        return True

    def set_peer_confirmed(self, t_ms, on):
        self.peer_confirmed = bool(on)
        if on and self.sub == CONFIRMED:
            self._start_cal(t_ms)

    def _start_cal(self, t_ms):
        self._set(CALIBRATE, t_ms)
        self.unstable = False
        self._unst_t = None
        self.cal.reset(t_ms)
        self._digit = 0
        self.countdown = self.cal.digit

    # ---- per frame -------------------------------------------------------------
    def update(self, t_ms):
        """Advance timers; returns the haptic started this frame (or None)."""
        self.toast = None
        sub = self.sub
        if sub == LOOKING:
            for k in range(SEEN_SLOTS):   # forget candidates silent for the window
                if (self._c_mac[k] is not None
                        and ticks_diff(t_ms, self._c_last[k]) > SEEN_WINDOW_MS):
                    self._c_mac[k] = None
        elif sub == SEEN or sub == CONFIRMED:
            lr = self._last_rx
            lp = self._last_pair
            if ((lr is not None and ticks_diff(t_ms, lr) > SEEN_LOST_MS)
                    or (lp is not None and ticks_diff(t_ms, lp) > SEEN_WINDOW_MS)):
                self.reset(t_ms)
        elif sub == CALIBRATE:
            c = self.cal
            fin = c.update(t_ms)
            if c.stable:
                self._unst_t = None
            elif self._unst_t is None:
                self._unst_t = t_ms
            u = self._unst_t
            self.unstable = u is not None and ticks_diff(t_ms, u) >= UNSTABLE_SHOW_MS
            d = c.digit
            self.countdown = d
            if fin:
                self.p1m = c.p1m
                if c.skipped:
                    self.toast = TOAST_CAL_SKIPPED
                self._emit("CLOSER")
                self.start_split(t_ms)
            elif c.stable and d != self._digit:
                self._digit = d
                self._emit("TICK")
        elif sub == SPLIT:
            el = ticks_diff(t_ms, self.t_sub)
            left = self.split_s * 1000 - el
            if left <= -T.PAIR_GO_MS:
                self.countdown = 0
                self._set(DONE, t_ms)
            else:
                d = 0 if left <= 0 else (left + 999) // 1000
                self.countdown = d
                if d != self._digit:
                    self._digit = d
                    if d == 0:
                        self._emit("CLOSER")
                    elif d <= 3:
                        self._emit("TICK")
        h = self._haptic
        self._haptic = None
        return h

    # ---- outputs ---------------------------------------------------------------
    @property
    def go(self):
        """Split countdown reached 0 (word ``GO``)."""
        return self.sub == SPLIT and self.countdown == 0
