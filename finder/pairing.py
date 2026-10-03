"""PAIRING screen logic (ui-spec §6 PAIRING, §5.8 calibration).

    looking -> seen -> confirmed -> calibrate -> split -> done

* ``looking``: a nearby watch that is also pairing is heard 3 times within
  2 s at a strong RSSI (relative to the nominal p1m) -> ``seen``.
* ``seen``: both watches show the same 3 runes, taken from a hash of the two
  sorted MACs (tokens ``glyphs.runes.code``). A tap / short press confirms
  this side (``confirmed``); a matched bump confirms both at once.
* ``calibrate``: 3 s mean RSSI at 1 m. The fill pauses while the sd over
  the last 1 s exceeds 4 dB (``unstable`` -> chip ``HOLD STILL`` once paused
  for 0.7 s, so the chip does not flicker); the result is clamped to nominal +-6 dB; after
  10 s the nominal p1m is used (toast ``CAL SKIPPED``).
* ``split``: 30 s countdown, TICK at 3/2/1, CLOSER and ``GO`` at 0 for 1 s.

``update(t)`` returns the haptic started this frame (or None); ``toast`` is a
one-shot set on the update that raised it. Pure logic, no radio access.
"""

import math
from array import array

from finder.compat import ticks_diff
from finder import tuning as T

LOOKING = "looking"
SEEN = "seen"
CONFIRMED = "confirmed"
CALIBRATE = "calibrate"
SPLIT = "split"
DONE = "done"

SEEN_MIN_DB = -15.0        # candidate RSSI >= p1m + this (about 5 m at n 2.2)
SEEN_PACKETS = T.RELINK_PACKETS
SEEN_WINDOW_MS = T.RELINK_WINDOW_MS
SEEN_LOST_MS = T.LINK_LOST_AFTER_MS   # candidate silent this long -> looking
GO_MS = 1000
UNSTABLE_SHOW_MS = 700     # fill paused this long before the chip says HOLD STILL (no flicker)
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
    def progress(self):
        f = self.fill_ms / self.window_ms
        return 1.0 if f > 1.0 else f

    @property
    def digit(self):
        """Countdown digit 3/2/1 from the fill."""
        d = 3 - self.fill_ms // 1000
        return 1 if d < 1 else 3 if d > 3 else d


class Pairing:
    """PAIRING sub-state machine for one watch."""

    def __init__(self, my_mac=None, nominal=T.P1M_NOMINAL_DBM):
        self.my_mac = bytes(my_mac) if my_mac is not None else None
        self.nominal = nominal
        self.cal = Calibrator(nominal)
        self._c_mac = None
        self._c_t = array("i", [0] * SEEN_PACKETS)
        self._c_n = 0
        self.p1m = None
        self.reset(0)

    def reset(self, t_ms):
        """Back to ``looking``: forget the partner and calibration."""
        self.sub = LOOKING
        self.t_sub = t_ms
        self.peer_mac = None
        self.runes = None
        self.p1m = None
        self.cal_skipped = False
        self.confirmed = False
        self.peer_confirmed = False
        self._c_mac = None
        self._c_n = 0
        self._last_rx = None
        self.countdown = None
        self.toast = None
        self.unstable = False
        self._unst_t = None
        self._digit = 0
        self._haptic = None

    def start_split(self, t_ms):
        """New round with the existing pairing and calibration."""
        self._set(SPLIT, t_ms)
        self.countdown = T.PAIR_SPLIT_S
        self._digit = T.PAIR_SPLIT_S

    def _set(self, sub, t_ms):
        self.sub = sub
        self.t_sub = t_ms

    def _emit(self, name):
        h = self._haptic
        if h is None or T.HAPTIC_RANK[name] >= T.HAPTIC_RANK[h]:
            self._haptic = name

    # ---- inputs ----------------------------------------------------------------
    def on_candidate(self, t_ms, mac, rssi, peer_pairing=True):
        """Any valid beacon while looking/seen. Returns True if it is the partner."""
        if self.sub == LOOKING:
            if not peer_pairing or rssi < self.nominal + SEEN_MIN_DB:
                return False
            if self._c_mac != mac:
                self._c_mac = bytes(mac)   # the radio driver may reuse its buffer
                self._c_n = 0
            i = self._c_n % SEEN_PACKETS
            self._c_t[i] = t_ms
            self._c_n += 1
            if self._c_n >= SEEN_PACKETS:
                oldest = self._c_t[self._c_n % SEEN_PACKETS]
                if ticks_diff(t_ms, oldest) <= SEEN_WINDOW_MS:
                    self.peer_mac = bytes(mac)
                    if self.my_mac is not None:
                        self.runes = rune_ids(self.my_mac, self.peer_mac)
                    else:
                        self.runes = rune_ids(self.peer_mac, self.peer_mac)
                    self._set(SEEN, t_ms)
                    self._last_rx = t_ms
                    self._emit("DOUBLE")
                    return True
            return False
        if self.peer_mac is not None and mac == self.peer_mac:
            self._last_rx = t_ms
            return True
        return False

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
        self.countdown = 3

    # ---- per frame -------------------------------------------------------------
    def update(self, t_ms):
        """Advance timers; returns the haptic started this frame (or None)."""
        self.toast = None
        sub = self.sub
        if sub == SEEN or sub == CONFIRMED:
            if self._last_rx is not None and ticks_diff(t_ms, self._last_rx) > SEEN_LOST_MS:
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
                self.cal_skipped = c.skipped
                if c.skipped:
                    self.toast = TOAST_CAL_SKIPPED
                self._emit("CLOSER")
                self.start_split(t_ms)
            elif c.stable and d != self._digit:
                self._digit = d
                self._emit("TICK")
        elif sub == SPLIT:
            el = ticks_diff(t_ms, self.t_sub)
            left = T.PAIR_SPLIT_S * 1000 - el
            if left <= -GO_MS:
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
