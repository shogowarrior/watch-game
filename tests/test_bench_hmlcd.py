"""tools/bench_hmlcd.py on fake hardware with a fake hmlcd module: it never
draws into a frame while that frame is on the wire, and hands the bus back to
machine.SPI at the end."""

import sys

from tests import Skip, fakes


class FakeHmlcd:
    """hmlcd stand-in: a push is "on the wire" until the next push or wait,
    and the buffer must not change meanwhile."""

    def __init__(self):
        self.clocks = []
        self.pushes = []          # (id of the buffer, y0, rows)
        self.flight = None        # (buffer, snapshot)
        self.torn = 0
        self.started = False
        self.events = []          # hmlcd and machine.SPI calls, in order

    def _land(self):
        if self.flight is not None:
            buf, snap = self.flight
            if bytes(buf) != snap:
                self.torn += 1
        self.flight = None

    def init(self, hz):
        if hz > 40000000:
            raise OSError("SPI clock not available")
        self._land()
        self.clocks.append(hz)
        self.started = True
        self.events.append("init")

    def deinit(self):
        self._land()
        self.started = False
        self.events.append("deinit")

    def push(self, buf, y0=0, rows=240):
        assert self.started and len(buf) >= rows * 480
        self._land()
        self.pushes.append((id(buf), y0, rows))
        self.flight = (buf, bytes(buf))

    def wait(self):
        self._land()

    def stats(self):
        n = len(self.pushes)
        return (n, 1000, 1000, n * 1000, n * 10)


def _bench():
    if sys.implementation.name == "cpython":
        raise Skip("the renderer needs framebuf (MicroPython)")
    m = fakes.install()
    from tools import bench_hmlcd as b
    lines = []
    b.print = lambda *a: lines.append(" ".join(str(x) for x in a))
    b.N = 2
    b.HOLD_MS = 0
    return b, m, lines


def test_frames_alternate_and_none_is_drawn_while_on_the_wire():
    b, _, lines = _bench()
    fake = b.hmlcd = FakeHmlcd()
    fake.init(40000000)
    b.fast(40000000)
    frames = [p for p in fake.pushes]
    assert len(frames) == len(b.FIXTURES) * (10 + b.N)
    assert all(y0 == 0 and rows == 240 for _, y0, rows in frames)
    assert all(frames[i][0] != frames[i + 1][0] for i in range(9))   # two buffers, in turn
    assert fake.torn == 0
    assert [ln.split()[4] for ln in lines] == ["fixture=FAR", "fixture=HOT", "fixture=PAIRING"]
    assert all(ln.startswith("HM mpy mode=hmlcd hz=40000000 ") and " wait_us=" in ln for ln in lines)


def test_main_runs_each_clock_and_gives_the_bus_back():
    b, m, lines = _bench()
    fake = b.hmlcd = FakeHmlcd()
    saved = (m.SPI.deinit, m.SPI.init)
    m.SPI.deinit = lambda spi: fake.events.append("spi.deinit")
    m.SPI.init = lambda spi, **kw: fake.events.append("spi.init")
    try:
        b.main()
    finally:
        m.SPI.deinit, m.SPI.init = saved
    assert fake.events == ["spi.deinit", "init", "init", "deinit", "spi.init"]
    assert m.pins[5].mode == m.Pin.OUT and m.pins[5].value() == 1   # CS back to a GPIO, idle high
    assert fake.clocks == [26666666, 40000000]                     # 80 MHz refused
    assert any(ln.startswith("HM error what=spi_clock hz=80000000") for ln in lines)
    assert sum(ln.startswith("HM mpy mode=stock ") for ln in lines) == 3
    assert sum(ln.startswith("HM mpy mode=hmlcd ") for ln in lines) == 6
    assert lines[-1] == "HM done" and not fake.started and fake.torn == 0


def test_stops_on_stock_firmware():
    b, _, lines = _bench()
    b.hmlcd = None
    b.main()
    assert lines == ["HM error what=no_hmlcd (stock firmware: build native/micropython first)"]
