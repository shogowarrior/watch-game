"""app.runtime: starting the BMA423 feature engine (``_chip_start``) through bus errors.

``FlakyIMU`` subclasses tests/test_app_runtime.py's scripted feature engine
``ChipIMU``; its first starts raise OSError, as a NACK mid-upload does.
"""

from tests import fakes
from tests.test_app_runtime import Board, ChipIMU, Clock, _renderer


class FlakyIMU(ChipIMU):
    """``ChipIMU`` whose first ``fails`` starts raise OSError; ``starts`` holds
    the clock time of every start."""

    def __init__(self, clock, ok, fails):
        ChipIMU.__init__(self, clock, ok)
        self.fails = fails
        self.starts = []

    def start_features(self):
        self.starts.append(self.clock.now)
        if self.fails:
            self.fails -= 1
            raise OSError(5)
        return ChipIMU.start_features(self)


def _runtime(imu, clock):
    from app.runtime import Runtime
    return Runtime(Board(imu=imu), parts=("imu",), clock=clock, sleep_ms=clock.sleep,
                   renderer=_renderer(), gc_collect=lambda: None)


def _run(ok, fails):
    fakes.install()
    clock = Clock(0)
    imu = FlakyIMU(clock, ok, fails)
    rt = _runtime(imu, clock)
    rt.begin(0)
    rt.run(max_ms=6000)
    return rt, imu


def test_a_start_that_works_after_a_bus_error_clears_it():
    # the second start goes through but brings no engine (no blob, or its init fails)
    from app.runtime import CHIP_OFF
    for ok in (None, False):
        rt, imu = _run(ok, 1)
        assert len(imu.starts) == 2 and rt._chip == CHIP_OFF, (ok, imu.starts, rt._chip)
        assert not rt.errors, (ok, rt.errors)


def test_starts_that_keep_failing_are_retried_by_the_poll_a_few_times():
    from app.runtime import CHIP_MS, CHIP_OFF, CHIP_ON, CHIP_TRIES
    rt, imu = _run(True, 3)
    assert rt._chip == CHIP_ON and not rt.errors, (rt._chip, rt.errors)
    assert len(imu.starts) == 4 and imu.calls[-1] == "on", (imu.starts, imu.calls)
    rt, imu = _run(True, 99)                # the bus never recovers
    st = imu.starts
    assert rt._chip == CHIP_OFF and isinstance(rt.errors["imu_features"], OSError)
    assert len(st) == CHIP_TRIES, st
    assert all(st[i + 1] - st[i] >= CHIP_MS for i in range(len(st) - 1)), st


def test_a_lost_ack_on_init_ctrl_brings_the_engine_up_without_a_second_upload():
    # Real driver: INIT_CTRL=1 reaches the chip but its ACK is lost; the engine
    # reports init_ok INIT_MIN_MS later. The retry must find it up, not upload again.
    from tests.test_hal_bma423 import _imu, _tmp, _write, _blob, _rm, _w
    from app.runtime import CHIP_ON
    m, dev, bmod, imu = _imu()
    p = _tmp("t_rt_bma423_ack.bin")
    _write(p, _blob())
    dev.init_status = 0
    start = imu.start_features
    imu.start_features = lambda: start(path=p, expect_sha256=None)
    clock = Clock(0)
    up = []                                 # when INIT_CTRL=1 landed
    write = dev.write
    read = dev.read

    def lost_ack(reg, data):
        write(reg, data)
        if reg == 0x59 and data[0] == 1 and not up:
            up.append(clock.now)
            raise OSError(5)

    def engine(reg, n):
        if reg == 0x2A and up and clock.now - up[0] >= bmod.INIT_MIN_MS:
            dev.regs[0x2A] = 1              # init_ok
        return read(reg, n)
    dev.write = lost_ack
    dev.read = engine
    rt = _runtime(imu, clock)
    try:
        rt.begin(0)
        rt.run(max_ms=3000)
    finally:
        _rm(p)
    assert up and rt._chip == CHIP_ON and "imu_features" not in rt.errors, (rt._chip, rt.errors)
    assert _w(dev, 0x59) == [0, 1], _w(dev, 0x59)   # INIT_CTRL=1 once per reset
