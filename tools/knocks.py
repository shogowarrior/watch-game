#!/usr/bin/env python3
"""Knocks: which bump spikes the two watches matched, and why the rest did not count.

    python3 tools/knocks.py logs/debug-20261004-101500.jsonl   # judge a saved session again

docs/design/debug-mode.md "Knocks" is the contract. ``tools/debug_server.py``
feeds every record it relays to a ``Knocks`` (``feed(rec, rx)``, ``rx`` the
laptop's ms) and relays each judgement it returns as a ``knock`` line.

A watch's spikes come from its ``tap`` events, and from a state record's
``tap`` that no event brought (the USB queue dropped it). The partner's
spikes, as that watch heard them by radio, come from the ``ptap`` of its
state records, already on its clock. A spike is judged on its own watch's
clock, as finder/game.py does (``_peer_spiked``): ``matched`` when a partner
spike heard lies within BUMP_WINDOW_MS of it. Laptop time (each watch's
clock offset, the smallest ``rx - t`` over its last OFF_N state records)
only places a spike for the partner's side of an unmatched knock and for the
rows that pair the two watches' spikes of one knock.
"""

import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from finder.compat import ticks_add, ticks_diff  # noqa: E402  (after the path fix)
from finder.tuning import BUMP_WINDOW_MS, BUMP_SPIKE_G  # noqa: E402

DEVS = ("A", "B")
SETTLE_MS = 1000          # a spike is judged once its watch's state records reach this far past it
QUIET_MS = 3000           # ... or once the bridge has held it this long (its watch went silent)
NEAR_MS = 2000            # partner spikes within this, but outside the window: "apart"
SAME_MS = 150             # partner times closer than this are one spike (spikes are >= 200 ms apart)
ROW_MS = 600              # the two watches' spikes this close in laptop time are one knock
SPK_MS = 500              # the partner's accelerometer counts this long after a knock (a FIFO batch late)
ARM_MS = 1000             # the partner's state record at most this old tells whether it felt for knocks
KEEP_MS = 30000           # history kept per watch, on its clock
OFF_N = 50                # state records in the clock-offset estimate (10 s at 5 Hz)
ROWS_N = 20               # rows kept for pairing
LABELS = {"matched": "Matched", "buzz": "Set aside", "apart": "Too far apart",
          "alone": "Not matched"}      # the verdicts, as the page and the terminal name them


def _int(v):
    return v if isinstance(v, int) and not isinstance(v, bool) else None


def _counts(v):
    """A state record's ``spk``: four counts, or None."""
    if isinstance(v, list) and len(v) == 4 and all(_int(c) is not None for c in v):
        return v
    return None


class _Spike:
    def __init__(self, t, ok, rx):
        self.t = t            # on its watch's clock
        self.ok = ok          # the game took it (False: its own buzz blanked it)
        self.rx = rx          # when the bridge got it
        self.hot = None       # made in HOT (a later state record's ``tap_hot``), None: unknown
        self.done = False


class _Watch:
    """What the bridge knows about one watch: its spikes, the partner spikes
    it heard, its recent state, its clock offset and its totals."""

    def __init__(self, dev):
        self.dev = dev
        self.rx = None        # the laptop ms of its last record
        self.totals = {"felt": 0, "matched": 0, "buzz": 0, "apart": 0, "alone": 0,
                       "spk": [0, 0, 0, 0]}
        self.spk = None       # the last ``spk`` counts (kept across a restart: they start again from 0)
        self.reset()

    def reset(self):
        """Forget its history (it restarted); totals carry on."""
        self.t = None         # its latest state record's t
        self.offs = []        # rx - t of its last OFF_N state records
        self.states = []      # (t, arm, spk) of its recent state records, oldest first
        self.heard = []       # [t, hot] partner spikes heard by radio, on its clock
        self.spikes = []      # _Spike, oldest first
        self.last_tap = None  # the newest spike the game took

    def off(self):
        return min(self.offs) if self.offs else None

    def spike(self, t, ok, rx):
        self.spikes.append(_Spike(t, ok, rx))
        if ok and (self.last_tap is None or ticks_diff(t, self.last_tap) > 0):
            self.last_tap = t

    def state(self, rec, t, rx):
        self.t = t
        self.offs.append(rx - t)
        if len(self.offs) > OFF_N:
            self.offs.pop(0)
        spk = _counts(rec.get("spk"))
        if spk is not None:
            last = self.spk
            if last is not None:      # counted from the first record the bridge got
                tot = self.totals["spk"]
                for k in range(4):    # a count that went down started again from 0 (a restart)
                    tot[k] += spk[k] - (last[k] if spk[k] >= last[k] else 0)
            self.spk = spk
        self.states.append((t, rec.get("arm") is True, spk))
        tap = _int(rec.get("tap"))
        if tap is not None:
            if self.last_tap is None or ticks_diff(tap, self.last_tap) > 0:
                if ticks_diff(t, tap) <= SETTLE_MS:
                    self.spike(tap, True, rx)     # its tap event never came
                else:
                    self.last_tap = tap           # from before the bridge heard it
            for s in self.spikes:
                if s.t == tap and s.ok:
                    s.hot = rec.get("tap_hot") is True
        p = _int(rec.get("ptap"))
        if p is not None and not any(abs(ticks_diff(p, h[0])) < SAME_MS for h in self.heard):
            self.heard.append([p, rec.get("ptap_hot") is True])
        self._trim(t)

    def _trim(self, t):
        while self.states and ticks_diff(t, self.states[0][0]) > KEEP_MS:
            self.states.pop(0)
        while self.heard and ticks_diff(t, self.heard[0][0]) > KEEP_MS:
            self.heard.pop(0)
        while self.spikes and self.spikes[0].done and ticks_diff(t, self.spikes[0].t) > KEEP_MS:
            self.spikes.pop(0)


class Knocks:
    """Judges both watches' spikes as their records arrive (docs/design/debug-mode.md "Knocks")."""

    def __init__(self):
        self.watches = dict((d, _Watch(d)) for d in DEVS)
        self._rows = []       # [row, at, devs]
        self._row = 0

    def feed(self, rec, rx):
        """One record (a dict with ``dev`` and ``ev``) the bridge got at ``rx``
        laptop ms -> the judgements it settled, oldest first per watch."""
        w = self.watches.get(rec.get("dev"))
        t = _int(rec.get("t"))
        if w is None or t is None:
            return []
        out = []
        ev = rec.get("ev")
        if ev == "s":
            if w.t is not None and ticks_diff(t, w.t) < 0:    # it restarted
                out += self._judge(w, rx, True)
                w.reset()
            w.state(rec, t, rx)
        elif ev == "tap":
            w.spike(t, rec.get("ok") is True, rx)
        w.rx = rx
        for v in self.watches.values():
            out += self._judge(v, rx)
        return out

    def flush(self, rx):
        """Judges every spike still waiting (the bridge closes, a log ends)."""
        out = []
        for w in self.watches.values():
            out += self._judge(w, rx, True)
        return out

    def totals(self):
        return dict((d, dict(w.totals, spk=list(w.totals["spk"])))
                    for d, w in self.watches.items() if w.rx is not None)

    # ---- judging ----
    def _judge(self, w, rx, force=False):
        out = []
        for s in w.spikes:
            if s.done:
                continue
            if not (force or rx - s.rx >= QUIET_MS
                    or (w.t is not None and ticks_diff(w.t, s.t) >= SETTLE_MS)):
                break
            s.done = True
            out.append(self._verdict(w, s))
        return out

    def _verdict(self, w, s):
        x, y = w.dev, self._other(w).dev
        gap = None
        if not s.ok:
            v = "buzz"
            why = "%s was buzzing, and its own motor shakes the accelerometer." % x
        else:
            h = None
            for c in w.heard:
                if h is None or abs(ticks_diff(s.t, c[0])) < abs(ticks_diff(s.t, h[0])):
                    h = c
            d = None if h is None else ticks_diff(s.t, h[0])    # > 0: the partner's came first
            if d is not None and abs(d) <= BUMP_WINDOW_MS:
                v, gap = "matched", d
                why = "%s felt it too, %s." % (y, _when(d))
                if s.hot and h[1]:
                    why += " Both were in HOT."
                elif s.hot or h[1]:
                    why += " %s was not in HOT, so it could not end the round." % (y if s.hot else x)
            elif d is not None and abs(d) <= NEAR_MS:
                v, gap = "apart", d
                why = "%s's knock came %.2f s %s; they must be within %.1f s." % (
                    y, abs(d) / 1000.0, "earlier" if d > 0 else "later", BUMP_WINDOW_MS / 1000.0)
            else:
                v = "alone"
                why = self._partner(w, s)
        tot = w.totals
        tot["felt"] += 1
        tot[v] += 1
        at = s.rx if w.off() is None else s.t + w.off()
        return {"dev": x, "t": s.t, "at": at, "row": self._place(x, at), "v": v, "gap": gap,
                "why": why, "n": dict(tot, spk=list(tot["spk"]))}

    def _partner(self, w, s):
        """Why the partner sent no spike near ``s``, from its own records."""
        p = self._other(w)
        x, y = w.dev, p.dev
        ox, oy = w.off(), p.off()
        if p.rx is None:
            return "%s has sent nothing yet." % y
        if ox is None or oy is None:
            return "No knock from %s." % y
        at = s.t + ox
        if p.rx < at:
            return "%s was not sending then." % y
        ty = ticks_add(0, at - oy)                 # the knock on the partner's clock
        for q in p.spikes:
            if abs(ticks_diff(q.t, ty)) <= ROW_MS:
                if not q.ok:
                    return "%s felt it too, but was buzzing, so it set its spike aside." % y
                return "%s felt it too, but its knock time never reached %s by radio." % (y, x)
        arm = None
        for t, a, _ in p.states:
            d = ticks_diff(ty, t)
            if 0 <= d <= ARM_MS:
                arm = a
        if arm is False:
            return "%s was not feeling for knocks (it does only in HOT, in FOUND, and in PAIRING once the runes show)." % y
        dk = [0, 0, 0, 0]
        prev = None
        for t, _, c in p.states:
            if prev is not None and c is not None and 0 <= ticks_diff(t, ty) <= SPK_MS:
                for k in range(4):
                    dk[k] += max(0, c[k] - prev[k])
            prev = c
        if dk[3]:
            return "%s's own buzz hid the bump from its accelerometer." % y
        if dk[1]:
            return "%s felt only a soft bump, under the %g g a knock needs." % (y, BUMP_SPIKE_G)
        if dk[2]:
            return "%s felt a bump too long or too short for a knock." % y
        return "%s felt nothing." % y

    def _other(self, w):
        return self.watches[DEVS[1 - DEVS.index(w.dev)]]

    def _place(self, dev, at):
        """The row of a spike of ``dev`` at ``at``: the nearest recent row
        within ROW_MS that has none of ``dev``'s spikes yet, else a new one."""
        best = None
        for r in self._rows:
            if dev not in r[2] and abs(r[1] - at) <= ROW_MS and (
                    best is None or abs(r[1] - at) < abs(best[1] - at)):
                best = r
        if best is None:
            self._row += 1
            best = [self._row, at, []]
            self._rows.append(best)
            if len(self._rows) > ROWS_N:
                self._rows.pop(0)
        best[2].append(dev)
        return best[0]


def _when(d):
    """A gap (ms; > 0: the partner's spike came first) in words."""
    if d == 0:
        return "at the same moment"
    return "%d ms %s" % (abs(d), "earlier" if d > 0 else "later")


def clock(at):
    """Laptop ms -> local HH:MM:SS."""
    return time.strftime("%H:%M:%S", time.localtime(at / 1000.0))


def say(v):
    """One judgement as a terminal line."""
    return "%s  knock, watch %s  %-14s %s" % (clock(v["at"]), v["dev"], LABELS[v["v"]], v["why"])


def summary(totals):
    """The totals as lines: each watch's counts, and whether the matched counts agree."""
    out = []
    for d in sorted(totals):
        n = totals[d]
        k = n["spk"]
        out.append("watch %s: %d spike%s, %d matched, %d set aside while buzzing, %d too far "
                   "apart, %d with no knock from the other watch; accelerometer: %d soft, %d wrong "
                   "length, %d hidden by its buzz" % (d, n["felt"], "" if n["felt"] == 1 else "s",
                                                      n["matched"], n["buzz"], n["apart"],
                                                      n["alone"], k[1], k[2], k[3]))
    if len(totals) == 2:
        a, b = totals["A"]["matched"], totals["B"]["matched"]
        out.append("matched: both count %d" % a if a == b else "matched: A counts %d, B %d" % (a, b))
    return out


def judge_log(path):
    """A bridge log (docs/design/debug-mode.md, its ``rec`` lines in order)
    -> (judgements, totals)."""
    k = Knocks()
    out = []
    rx = 0
    with open(path, encoding="utf-8") as f:
        for line in f:
            try:
                m = json.loads(line)
            except ValueError:
                continue
            rec = m.get("rec") if isinstance(m, dict) else None
            if isinstance(rec, dict) and _int(m.get("rx")) is not None:
                rx = m["rx"]
                out += k.feed(rec, rx)
    out += k.flush(rx)
    return out, k.totals()


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 1 or argv[0].startswith("-"):
        print("usage: python3 tools/knocks.py logs/debug-YYYYmmdd-HHMMSS.jsonl")
        return 2
    try:
        out, totals = judge_log(argv[0])
    except OSError as e:
        print("Could not read %s: %s" % (argv[0], e.strerror or e))
        return 1
    for v in out:
        print(say(v))
    for s in summary(totals) or ["no records from watch A or B"]:
        print(s)
    return 0


if __name__ == "__main__":
    sys.exit(main())
