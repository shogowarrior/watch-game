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
clock, as finder/game.py does (``_bump_match``): ``matched`` when a partner
spike heard lies within BUMP_WINDOW_MS of it. Each partner spike heard goes
with one spike only, the nearest (the game uses each spike once). Laptop time
(each watch's clock offset, the smallest ``rx - t`` over its last OFF_N state
records) only places a spike for the partner's side of an unmatched knock and
for the rows that pair the two watches' spikes of one knock.
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
TICKS = 1 << 30           # ticks_ms wraps here: a t outside 0..TICKS-1 is no watch's
NEAR_MS = 2000            # partner spikes within this, but outside the window: "apart"
SETTLE_MS = 2500          # an unmatched spike is judged once its watch's records reach this far
                          # past it: a partner spike up to NEAR_MS later has been heard by then
FIRM_MS = 400             # a match is judged once its watch's records reach this far past the
                          # later spike: both HOT bits are up to date by then
QUIET_MS = 4000           # ... or once the bridge has held a spike this long (its watch went silent)
SAME_MS = 150             # partner times closer than this are one spike (spikes are >= 200 ms apart)
LINK_MS = 300             # a matched or apart spike joins the row of the partner spike it names
ROW_MS = 600              # other spikes this close in laptop time are one knock
SPK_MS = 500              # the partner's accelerometer counts this long after a knock (a FIFO batch late)
ARM_MS = 1000             # the partner's state records around a knock: this far before and after it
RESTART_MS = 1000         # a state record this far behind the last one: the watch restarted
                          # (less: an out-of-order datagram, dropped)
KEEP_MS = 30000           # history kept per watch, on its clock
OFF_N = 50                # state records in the clock-offset estimate (10 s at 5 Hz)
ROWS_N = 20               # rows kept for pairing
RECENT_N = 40             # judgements kept for a page that opens mid-session (two per row)
LABELS = {"matched": "Matched", "buzz": "Set aside", "apart": "Too far apart",
          "alone": "Not matched"}      # the verdicts, as the page and the terminal name them


def _int(v):
    return v if isinstance(v, int) and not isinstance(v, bool) else None


def _tick(v):
    """A ticks_ms value from a record, or None."""
    v = _int(v)
    return v if v is not None and 0 <= v < TICKS else None


def _flag(v):
    """A record's true/false field; None when missing (older firmware) or not a bool."""
    return v if isinstance(v, bool) else None


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
        self.hot = None       # made in HOT (the latest state record's ``tap_hot``), None: unknown
        self.done = False


class _Watch:
    """What the bridge knows about one watch: its spikes, the partner spikes
    it heard, its recent state, its clock offset and its totals."""

    def __init__(self, dev):
        self.dev = dev
        self.rx = None        # the laptop ms of its last record
        self.totals = {"felt": 0, "matched": 0, "buzz": 0, "apart": 0, "alone": 0,
                       "spk": [0, 0, 0, 0]}
        self.spk = None       # the last ``spk`` counts (None: none yet, counted from the next)
        self.knows = True     # its records carry ``ptap`` (False: code from before the Knocks panel)
        self.reset()

    def reset(self):
        """Forget its history (it restarted); totals carry on."""
        self.t = None         # its latest state record's t
        self.offs = []        # rx - t of its last OFF_N state records
        self.states = []      # (t, arm, spk) of its recent state records, oldest first
        self.heard = []       # [t, hot] partner spikes heard by radio, on its clock
        self.spikes = []      # _Spike, oldest first
        self.last_tap = None  # the newest spike the game took
        if self.spk is not None:
            self.spk = [0, 0, 0, 0]       # its counts start again from 0

    def off(self):
        return min(self.offs) if self.offs else None

    def spike(self, t, ok, rx):
        self.spikes.append(_Spike(t, ok, rx))
        if ok and (self.last_tap is None or ticks_diff(t, self.last_tap) > 0):
            self.last_tap = t

    def state(self, rec, t, rx):
        self.t = t
        self.knows = "ptap" in rec
        self.offs.append(rx - t)
        if len(self.offs) > OFF_N:
            self.offs.pop(0)
        spk = _counts(rec.get("spk"))
        if spk is not None:
            last = self.spk
            if last is not None:      # counted from the first record the bridge got
                tot = self.totals["spk"]
                for k in range(4):    # a count that went down started again from 0
                    tot[k] += spk[k] - (last[k] if spk[k] >= last[k] else 0)
            self.spk = spk
        self.states.append((t, _flag(rec.get("arm")), spk))
        tap = _tick(rec.get("tap"))
        if tap is not None:
            if self.last_tap is None or ticks_diff(tap, self.last_tap) > 0:
                if ticks_diff(t, tap) <= SETTLE_MS:
                    self.spike(tap, True, rx)     # its tap event never came
                else:
                    self.last_tap = tap           # from before the bridge heard it
            for s in self.spikes:     # the latest record wins: one taken before the next
                if s.t == tap and s.ok:           # game tick still had the previous spike's bit
                    s.hot = _flag(rec.get("tap_hot"))
        p = _tick(rec.get("ptap"))
        if p is not None:
            hot = _flag(rec.get("ptap_hot"))
            for h in self.heard:
                if abs(ticks_diff(p, h[0])) < SAME_MS:
                    h[1] = hot        # the latest wins, as for its own spikes
                    break
            else:
                self.heard.append([p, hot])
        self._trim(t)

    def _trim(self, t):
        while self.states and ticks_diff(t, self.states[0][0]) > KEEP_MS:
            self.states.pop(0)
        while self.heard and ticks_diff(t, self.heard[0][0]) > KEEP_MS:
            self.heard.pop(0)
        while self.spikes and self.spikes[0].done and ticks_diff(t, self.spikes[0].t) > KEEP_MS:
            self.spikes.pop(0)

    def owner(self, h):
        """The spike the partner spike heard ``h`` goes with: its nearest own
        spike the game took (the earlier of two as near), or None."""
        o = e = None
        for s in self.spikes:
            if s.ok:
                f = abs(ticks_diff(s.t, h[0]))
                if o is None or f < e:
                    o, e = s, f
        return o

    def nearest(self, s):
        """The partner spike heard nearest ``s`` that goes with it, and how far
        ``s`` is after it, or (None, None)."""
        h = d = None
        for c in self.heard:
            e = ticks_diff(s.t, c[0])
            if (h is None or abs(e) < abs(d)) and self.owner(c) is s:
                h, d = c, e
        return h, d


class Knocks:
    """Judges both watches' spikes as their records arrive (docs/design/debug-mode.md "Knocks")."""

    def __init__(self):
        self.watches = dict((d, _Watch(d)) for d in DEVS)
        self._rows = []       # [row, {dev: at}, linked]
        self._row = 0
        self._recent = []     # the last RECENT_N judgements

    def feed(self, rec, rx):
        """One record (a dict with ``dev`` and ``ev``) the bridge got at ``rx``
        laptop ms -> the judgements it settled."""
        w = self.watches.get(rec.get("dev"))
        t = _tick(rec.get("t"))
        if w is None or t is None:
            return []
        out = []
        ev = rec.get("ev")
        w.rx = rx
        if ev == "s":
            d = None if w.t is None else ticks_diff(t, w.t)
            if d is not None and d < -RESTART_MS:     # it restarted: judge what it left
                out += self._judge(w, rx, True)
                w.reset()
                d = None
            if d is None or d > 0:                    # else out of order: dropped
                w.state(rec, t, rx)
        elif ev == "tap":
            w.spike(t, rec.get("ok") is True, rx)
        return out + self.tick(rx)

    def tick(self, rx):
        """Judges the spikes that are ready at ``rx`` (the bridge also calls this
        while no record arrives, so a silent watch's spikes are judged)."""
        out = []
        for w in self.watches.values():
            out += self._judge(w, rx)
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

    def recent(self):
        """The last RECENT_N judgements, oldest first."""
        return list(self._recent)

    # ---- judging ----
    def _ready(self, w, s, rx):
        """A buzz at once; a match once both HOT bits are firm; anything else
        once a partner spike up to NEAR_MS later would have been heard; any
        spike the bridge has held QUIET_MS."""
        if not s.ok or rx - s.rx >= QUIET_MS:
            return True
        if w.t is None:
            return False
        h, d = w.nearest(s)
        if h is not None and abs(d) <= BUMP_WINDOW_MS:
            return ticks_diff(w.t, s.t if d >= 0 else h[0]) >= FIRM_MS
        return ticks_diff(w.t, s.t) >= SETTLE_MS

    def _judge(self, w, rx, force=False):
        out = []
        for s in w.spikes:
            if not s.done and (force or self._ready(w, s, rx)):
                s.done = True
                v = self._verdict(w, s)
                out.append(v)
                self._recent.append(v)
                if len(self._recent) > RECENT_N:
                    self._recent.pop(0)
        return out

    def _verdict(self, w, s):
        x, y = w.dev, self._other(w).dev
        gap = link = None
        if not s.ok:
            v = "buzz"
            why = "%s was buzzing, and its own motor shakes the accelerometer." % x
        elif not w.knows:
            v = "alone"
            why = ("%s runs code from before the Knocks panel, so it does not say which knocks "
                   "it heard: deploy both watches again." % x)
        else:
            h, d = w.nearest(s)                       # d > 0: the partner's came first
            if d is not None and abs(d) <= NEAR_MS:
                gap = d
                link = None if w.off() is None else h[0] + w.off()
            if gap is not None and abs(d) <= BUMP_WINDOW_MS:
                v = "matched"
                why = "%s felt it too, %s.%s" % (y, _when(d), _hot(x, s.hot, y, h[1]))
            elif gap is not None:
                v = "apart"
                why = "%s's knock came %s; they must be within %.1f s." % (
                    y, _secs(d), BUMP_WINDOW_MS / 1000.0)
            else:
                v = "alone"
                why = self._partner(w, s)
        tot = w.totals
        tot["felt"] += 1
        tot[v] += 1
        at = s.rx if w.off() is None else s.t + w.off()
        return {"dev": x, "t": s.t, "at": at, "row": self._place(x, at, link), "v": v, "gap": gap,
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
        ty = ticks_add(s.t, ox - oy)              # the knock on the partner's clock
        before = after = False
        arm = None
        for t, a, _ in p.states:
            d = ticks_diff(t, ty)
            if -ARM_MS <= d <= 0:
                before = True
                arm = a
            elif 0 < d <= ARM_MS:
                after = True
        if not (before and after):
            return "%s was not sending then." % y
        for e, q in sorted(((abs(ticks_diff(q.t, ty)), q) for q in p.spikes
                            if abs(ticks_diff(q.t, ty)) <= NEAR_MS), key=lambda c: c[0]):
            o = self._went_with(w, q, oy - ox)
            if o is not None:                     # x heard it: another spike of x's took it
                if e <= ROW_MS and o is not s:
                    return "%s felt one knock then, but it goes with %s's other spike, %s." % (
                        y, x, _when(ticks_diff(s.t, o.t)))
                continue
            if e <= ROW_MS:
                if not q.ok:
                    return "%s felt it too, but was buzzing, so it set the knock aside." % y
                return "%s felt it too, but its knock time never reached %s by radio." % (y, x)
            if q.ok:
                return ("%s's nearest knock came %s (they must be within %.1f s), and its time "
                        "never reached %s by radio." % (y, _secs(ticks_diff(ty, q.t)),
                                                       BUMP_WINDOW_MS / 1000.0, x))
        if arm is False:
            return "%s was not feeling for knocks (it does only in HOT, in FOUND, and in PAIRING once the runes show)." % y
        dk = None                 # its accelerometer's counts over the knock (None: not sent)
        prev = None
        for t, _, c in p.states:
            if prev is not None and c is not None and 0 <= ticks_diff(t, ty) <= SPK_MS:
                dk = dk or [0, 0, 0, 0]
                for k in range(4):
                    dk[k] += max(0, c[k] - prev[k])
            prev = c
        if dk is None:
            return "No knock from %s." % y
        if dk[3]:
            return "%s's own buzz hid the bump from its accelerometer." % y
        if dk[1]:
            return "%s felt only a soft bump, under the %g g a knock needs." % (y, BUMP_SPIKE_G)
        if dk[2]:
            return "%s felt a bump, but it lasted too long or came too soon after its last knock." % y
        return "%s felt nothing." % y

    def _went_with(self, w, q, d):
        """The spike of ``w`` that the partner spike ``q`` (on its own clock,
        ``d`` ms behind ``w``'s) went with, if ``w`` heard it; else None."""
        t = ticks_add(q.t, d)
        for h in w.heard:
            if abs(ticks_diff(h[0], t)) <= LINK_MS:
                return w.owner(h)
        return None

    def _other(self, w):
        return self.watches[DEVS[1 - DEVS.index(w.dev)]]

    def _place(self, dev, at, link):
        """The row of a spike of ``dev`` at ``at`` (laptop ms). One that names a
        partner spike (``link``, a match or one too far apart) joins the row
        holding that spike, if it is judged already, else starts a row the
        partner's verdict will find. Any other joins the nearest row within
        ROW_MS with no such link and none of ``dev``'s spikes, else starts one."""
        o = DEVS[1 - DEVS.index(dev)]
        best = e = None
        for r in self._rows:
            if dev in r[1] or o not in r[1] or (link is None and r[2]):
                continue
            f = abs(r[1][o] - (at if link is None else link))
            if f <= (ROW_MS if link is None else LINK_MS) and (best is None or f < e):
                best, e = r, f
        if best is None:
            self._row += 1
            best = [self._row, {}, False]
            self._rows.append(best)
            if len(self._rows) > ROWS_N:
                self._rows.pop(0)
        best[1][dev] = at
        best[2] = best[2] or link is not None
        return best[0]


def _hot(x, hx, y, hy):
    """Whether both spikes of a match were made in HOT (None: not known), in words."""
    if hx and hy:
        return " Both were in HOT."
    no = [n for n, f in ((x, hx), (y, hy)) if f is False]
    if no:
        return " %s in HOT, so it could not end the round." % (
            "Neither was" if len(no) == 2 else no[0] + " was not")
    return " Whether %s in HOT is not known." % (
        "they were" if hx is None and hy is None else (x if hx is None else y) + " was")


def _when(d):
    """A gap (ms; > 0: the partner's spike came first) in words."""
    if d == 0:
        return "at the same moment"
    return "%d ms %s" % (abs(d), "earlier" if d > 0 else "later")


def _secs(d):
    """A gap (ms; > 0: the partner's spike came first) as seconds in words."""
    return "%.2f s %s" % (abs(d) / 1000.0, "earlier" if d > 0 else "later")


def clock(at):
    """Laptop ms -> local HH:MM:SS (?? for a time no clock can show)."""
    try:
        return time.strftime("%H:%M:%S", time.localtime(at / 1000.0))
    except (OverflowError, OSError, ValueError):
        return "??:??:??"


def say(v):
    """One judgement as a terminal line."""
    return "%s  knock, watch %s  %-14s %s" % (clock(v["at"]), v["dev"], LABELS[v["v"]], v["why"])


def summary(totals):
    """The totals as lines: each watch's counts, and whether the matched counts agree."""
    out = []
    for d in sorted(totals):
        n = totals[d]
        k = n["spk"]
        out.append("watch %s: %d knock%s felt, %d matched, %d set aside while it buzzed, %d too "
                   "far apart, %d with no knock from the other watch; its accelerometer ignored "
                   "%d bumps too soft, %d too long or too soon after a knock, %d during its buzz"
                   % (d, n["felt"], "" if n["felt"] == 1 else "s", n["matched"], n["buzz"],
                      n["apart"], n["alone"], k[1], k[2], k[3]))
    if len(totals) == 2:
        a, b = totals["A"]["matched"], totals["B"]["matched"]
        out.append("matched: %d on each watch" % a if a == b
                   else "matched: %d on watch A, %d on watch B" % (a, b))
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
