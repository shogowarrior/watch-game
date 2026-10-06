#!/usr/bin/env python3
"""Two simulated watches that send real debug-mode records (CPython host tool).

    python3 tools/fake_watches.py [--host 127.0.0.1] [--port 47268] [--seconds N] [--speed 1.0]

Runs the two-watch simulator (the sim/ world, radio and motion, one
``finder.game.Game`` per watch, no renderer) in real time (``speed`` times
faster) and sends what two watches in debug mode send: per watch its events,
the 5 Hz state record and the ``rp`` record, built by app/telemetry.py and
sent through hal/debuglink.py, the same code as on the watch: UDP datagrams
(``DebugLink``), or with ``run(serial=...)`` lines into two files
(``SerialLink``, paced on the sim clock as on the watch).
``tools/debug_server.py --demo`` runs ``run()`` in a thread, so the page's
Real watches mode can be tried with no watches.

The players hold the watches together to pair (each presses the side key
once the runes show; the 30 s split is cut to 5 s). Whenever A is next to B
and both show HOT, they knock the watches together every 1.5 s: first a
knock only A feels, then one B feels 0.6 s late, then matched ones until the
round ends (``KNOCKS``), so the page's Knocks panel shows its verdicts. Each
knock's spikes go to ``Game.on_accel_tap`` and the ``tap`` event, as
app/runtime.py does. On the FOUND result A taps the screen (which only
raises the PRESS THE BUTTON toast there) and presses the side key for the
next round (BUTTON: PLAY AGAIN), then walks away to about 40 m and back
while B stands still, over and over. Beacons reach the
other game through a ``LinkMonitor``, as in app/runtime.py, so the link
counters (sequence numbers, loss) are real too.
"""

import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app.runtime import GAME_ID  # noqa: E402  (after the path fix: run as a script)
from app.telemetry import Telemetry  # noqa: E402
from finder import pairing as P  # noqa: E402
from finder import proto  # noqa: E402
from finder.compat import ticks_diff  # noqa: E402
from finder.game import Game, M_FOUND, M_HUNT, M_PAIRING  # noqa: E402
from finder.gestures import TAP, NAMES  # noqa: E402
from finder.haptic_patterns import HapticPlayer  # noqa: E402
from finder.link import LinkMonitor, R_BAD, R_DUP  # noqa: E402
from finder.proximity import HOT  # noqa: E402
from finder.tuning import FOUND_CELEBRATE_MS, LOGIC_MS  # noqa: E402
from hal.debuglink import DebugLink, SerialLink, DEBUG_PORT  # noqa: E402
from sim import Sim  # noqa: E402
from sim.world import World, Walker, PI  # noqa: E402

MACS = (b"\x24\x0a\xc4\x10\x00\x0a", b"\x24\x0a\xc4\x10\x00\x0b")
DEVS = ("A", "B")
STEP_MS = 50               # physics step; the logic runs every LOGIC_MS (100)
FRAME_PUMPS = (38, 26, 14)  # mid-frame SerialLink pumps, ms before the step's end (12 apart)
CONFIRM_MS = 800           # a player presses the side key this long after the runes show
SPLIT_S = 5
ROUTE = ((40.0, 0.0), (1.0, 0.0))   # A's walk, out and back (B stands at the origin)
PAUSE_S = 5.0              # A waits this long out there
KNOCK_MS = 1500            # next to B, both in HOT: a knock this often
KNOCKS = ((0, None), (0, 600), (0, 70))   # A's and B's spike, ms after each knock (None: missed);
                                          # the last one repeats until FOUND
AGAIN_MS = 2000            # A plays again this long after its celebration ends
TILT_FLAT = 5.0            # watch held flat, face up
BATTERY = 90
BATT_MV = 3950


class FakeWatch:
    """One simulated watch with what ``Telemetry.record`` reads from a
    ``Runtime``: game, link, tx, radio, params, screen and battery state.
    ``sink`` is its hal/debuglink.py link."""

    def __init__(self, i, sink):
        self.game = Game(MACS[i])
        self.link = LinkMonitor(GAME_ID)
        self.tx = proto.Beacon(GAME_ID)
        self.rxb = proto.Beacon(GAME_ID)
        self.buf = bytearray(proto.SIZE)
        self.player = HapticPlayer()
        self.radio = None              # no ESP-NOW radio, so ``rp.ch`` stays null
        self.params = None
        self.screen_is_on = True
        self.bl_level = 0.0
        self.fps = 0.0
        self.batt_mv = BATT_MV
        self.batt_chg = False
        self.feed = None               # no accelerometer, so ``spk`` stays null
        self.sink = sink
        self.tele = Telemetry(cap=64, dev=DEVS[i], sink=sink)
        self.tele.set_mac(MACS[i])

    def knock(self, t):
        """A bump spike at ``t``, as app/runtime.py's ``_on_tap`` hands it over."""
        self.tele.event(t, "tap", ("ok", self.game.on_accel_tap(t)))

    def tap(self, t):
        """A finger tap on the screen, as app/runtime.py hands a gesture over."""
        self.game.on_gesture(t, TAP)
        self.tele.event(t, "touch", ("g", NAMES[TAP]), ("x", 120), ("y", 120))

    def press(self, t):
        """A short press of the side key."""
        self.game.on_button(t)
        self.tele.event(t, "btn", ("kind", "short"))

    def hear(self, t, sender, seq, rssi):
        """A beacon from ``sender`` with sequence number ``seq``: filled as it
        leaves that watch, then received as app/runtime.py does."""
        b = sender.tx
        b.seq = seq & 0xFFFF
        sender.game.fill_beacon(b, t)
        b.pack_into(self.buf)
        g = self.game
        lk = self.link
        pm = g.pair.peer_mac
        if pm != lk.partner:
            if pm is None:
                lk.unlock()
            else:
                lk.lock(pm)
        if lk.on_packet(sender.game.my_mac, self.buf, proto.SIZE, t) in (R_BAD, R_DUP):
            return
        self.rxb.unpack_from(self.buf)
        g.on_packet(t, sender.game.my_mac, rssi, self.rxb)

    def tick(self, t, motion):
        """The 10 Hz logic, the player's confirm press, haptic events, telemetry."""
        g = self.game
        g.set_motion(t, motion.activity, motion.steps, motion.step_rate_hz, TILT_FLAT, True)
        g.set_battery(t, BATTERY)
        pr = g.pair
        pr.split_s = SPLIT_S
        if (g.mode == M_PAIRING and pr.sub == P.SEEN and not pr.confirmed
                and ticks_diff(t, pr.t_sub) >= CONFIRM_MS):
            self.press(t)
        p = g.tick(t)
        self.params = p
        on = g.screen_on
        self.screen_is_on = on
        self.bl_level = p.backlight if on else 0.0
        self.fps = float(p.fps_cap) if on else 0.0
        self.player.tick(t)
        if p.haptic and self.player.play_named(p.haptic, t):
            self.tele.event(t, "haptic", ("pattern", p.haptic))
        if self.tele.due(t):
            self.tele.record(t, self)


class Players:
    """The two players, once both games have left PAIRING: next to each other
    in HOT they knock (``KNOCKS``) until FOUND; A plays again from the FOUND
    result and walks its route. The first round starts next to each other."""

    def __init__(self, world, watches):
        self.a = world.a
        self.watches = watches
        self.walk = False          # A walks its route before the next knocks
        self.n = 0                 # knocks since A came back
        self.next_t = None         # the next knock, once both show HOT
        self.due = []              # [t, i]: a spike due on watch i

    def step(self, t):
        w = self.watches
        for d in list(self.due):
            if ticks_diff(t, d[0]) >= 0:
                self.due.remove(d)
                w[d[1]].knock(d[0])
        ga, gb = w[0].game, w[1].game
        if ga.mode == M_FOUND:
            if ticks_diff(t, ga.found_t) >= FOUND_CELEBRATE_MS + AGAIN_MS:
                w[0].tap(t)        # only a PRESS THE BUTTON toast (ui-spec §6 FOUND)
                w[0].press(t)      # BUTTON: PLAY AGAIN: B follows
                self.walk = True
                self.n = 0
                self.next_t = None
            return
        if M_PAIRING in (ga.mode, gb.mode) or self.a.plan:
            return
        if self.walk:
            self.a.walk_to(ROUTE[0]).still(PAUSE_S).walk_to(ROUTE[1])
            self.walk = False
            return
        if not (ga.mode == M_HUNT and ga.px.zone == HOT
                and (gb.mode == M_FOUND or (gb.mode == M_HUNT and gb.px.zone == HOT))):
            self.next_t = None
            return
        if self.next_t is None:
            self.next_t = t + KNOCK_MS
        elif ticks_diff(t, self.next_t) >= 0:
            da, db = KNOCKS[min(self.n, len(KNOCKS) - 1)]
            self.due.append([t + da, 0])
            if db is not None:
                self.due.append([t + db, 1])
            self.n += 1
            self.next_t = t + KNOCK_MS


def _deliver(sim, watches, pks):
    """Each received packet to its watch. The radio counts every beacon it
    sends, so the ones lost in this step take the sequence numbers before
    the delivered ones."""
    r = sim.radio
    for rx in (0, 1):
        tx = 1 - rx
        got = pks[rx]
        seq = r.sent[tx] - len(got)
        for pk in got:
            seq += 1
            watches[rx].hear(pk.t_ms, watches[tx], seq, pk.rssi)
        watches[tx].tx.seq = r.sent[tx] & 0xFFFF


def _sink(i, host, port, serial):
    """Watch ``i``'s link: ``SerialLink`` into ``serial[i]``, else UDP to ``host:port``."""
    if serial is not None:
        return SerialLink(DEVS[i], serial[i])
    s = DebugLink(DEVS[i], host, port)
    s.open()
    return s


def run(host="127.0.0.1", port=DEBUG_PORT, seconds=None, speed=1.0, stop=None, serial=None):
    """Send two watches' datagrams to ``host:port`` for ``seconds`` of watch
    time (None: until ``stop.is_set()``), ``speed`` times faster than real
    time. With ``serial``, two writable binary files (A's, B's; unbuffered,
    like the watch's UART), each watch writes its records as lines into its
    file instead, through ``SerialLink`` pumped on the sim clock as on the
    watch: during the step's frame (``FRAME_PUMPS``, as after its strips)
    and after the records. Returns each watch's link counters, by name."""
    a = Walker(1.0, 0.0, PI, 1.3, "A")       # held together: 1 m apart, face to face
    b = Walker(0.0, 0.0, 0.0, 1.3, "B")
    world = World(a, b)
    sim = Sim(world, "typical", 1)
    watches = (FakeWatch(0, _sink(0, host, port, serial)),
               FakeWatch(1, _sink(1, host, port, serial)))
    games = (watches[0].game, watches[1].game)
    players = Players(world, watches)
    t = 0
    t0 = time.monotonic()
    while (seconds is None or t < seconds * 1000) and not (stop is not None and stop.is_set()):
        players.step(t)
        sim.radio.period_ms[0] = 1000 // games[0].beacon_hz
        sim.radio.period_ms[1] = 1000 // games[1].beacon_hz
        pks = sim.step(STEP_MS / 1000.0)
        t += STEP_MS
        _deliver(sim, watches, pks)
        for dt in FRAME_PUMPS:
            for w in watches:
                w.sink.pump(t - dt)
        if t % LOGIC_MS == 0:
            for i in (0, 1):
                watches[i].tick(t, sim.motion(i))
        for w in watches:
            w.sink.pump(t)
        ahead = t / 1000.0 / speed - (time.monotonic() - t0)
        if ahead > 0:
            if stop is not None:
                stop.wait(ahead)          # wakes at once when stop is set
            else:
                time.sleep(ahead)
    for w in watches:
        w.tele.flush(force=True)
        w.sink.close()
    return dict((w.sink.dev, w.sink.stats()) for w in watches)


def main(argv=None):
    from tools.cli import parse_args
    o = parse_args(sys.argv[1:] if argv is None else argv,
                   {"host": "127.0.0.1", "port": DEBUG_PORT, "seconds": None, "speed": 1.0})
    secs = None if o["seconds"] is None else float(o["seconds"])
    print("fake watches A and B sending to %s:%s (Ctrl-C stops)" % (o["host"], o["port"]))
    try:
        st = run(o["host"], int(o["port"]), secs, float(o["speed"]))
    except KeyboardInterrupt:
        return 0
    for dev in sorted(st):
        print("%s: %d sent, %d failed" % (dev, st[dev]["tx"], st[dev]["tx_err"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
