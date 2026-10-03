"""Beacon hand-off between two ``finder.game.Game`` objects over the sim radio.

The two-watch harnesses (tests/test_episode.py, sim/webhost.py) wire the games
to ``sim.Sim`` with one ``GameLink``: each watch beacons at its own
``Game.beacon_hz``, and every packet the radio delivers is filled by the sender
with ``Game.fill_beacon``, packed and unpacked through finder.proto (as on the
air) and handed to the receiver's ``Game.on_packet`` with the packet's RSSI.
Lost packets are never filled, so they spend no goodbye beacon and leave no
``seq`` gap (the sims keep the battery at 90 %, so no goodbye is ever sent).
"""

from finder import proto


class GameLink:
    """Two games (index 0 = A, 1 = B) and their MACs; buffers are reused per packet."""

    def __init__(self, games, macs):
        self.games = games
        self.macs = macs
        self.tx = (proto.Beacon(1), proto.Beacon(1))
        self.rxb = proto.Beacon(1)
        self.buf = bytearray(proto.SIZE)

    def set_rates(self, radio):
        """Each watch transmits at its own rate (call before every radio step)."""
        radio.period_ms[0] = 1000 // self.games[0].beacon_hz
        radio.period_ms[1] = 1000 // self.games[1].beacon_hz

    def deliver(self, packets):
        """Hand ``Sim.step``'s (packets heard by A, packets heard by B) to the games."""
        for rx in (0, 1):
            tx = 1 - rx
            b = self.tx[tx]
            for pk in packets[rx]:
                b.next_seq()
                self.games[tx].fill_beacon(b, pk.t_ms)
                b.pack_into(self.buf)
                self.rxb.unpack_from(self.buf)
                self.games[rx].on_packet(pk.t_ms, self.macs[tx], pk.rssi, self.rxb)
