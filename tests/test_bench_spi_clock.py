"""tools/bench_spi_clock.py on fake hardware: it pokes the display's SPI clock
only from the 26.67 MHz value it expects, and always puts that value back."""

import sys

from tests import Skip, fakes

REG = 0x3FF64018


class Mem:
    """machine.mem32 stand-in: a dict of 32-bit words plus a write log."""

    def __init__(self, start):
        self.d = {REG: start}
        self.writes = []

    def __getitem__(self, a):
        return self.d.get(a, 0)

    def __setitem__(self, a, v):
        v &= 0xFFFFFFFF
        self.d[a] = v
        self.writes.append((a, v))


def _run(start):
    if sys.implementation.name == "cpython":
        raise Skip("draws the test card with framebuf (MicroPython)")
    m = fakes.install()
    mem = Mem(start)
    m.mem32 = mem
    from tools import bench_spi_clock as b
    from hal.board import Board
    b.main(hold_ms=0, frames=1, disp=Board().display)
    return mem, m


def test_pokes_40_and_80_then_restores():
    mem, m = _run(0x2002)
    assert mem.writes == [(REG, 0x2002), (REG, 0x1001), (REG, 0x80000000), (REG, 0x2002)]
    assert mem.d[REG] == 0x2002
    frames = sum(len(w[2]) for w in m.spi_log if w[1] == "write" and len(w[2]) == 240 * 24 * 2) // (240 * 240 * 2)
    assert frames >= 5      # first push, one timed frame per clock, the restore


def test_leaves_an_unexpected_clock_alone():
    mem, _ = _run(0x1234)
    assert mem.writes == []
