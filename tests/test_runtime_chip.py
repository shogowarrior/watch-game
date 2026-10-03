"""app.runtime: starting the BMA423 feature engine (``_chip_start``) through bus errors.

``FlakyIMU`` is tests/test_app_runtime.py's scripted feature engine whose
first starts raise OSError, as a NACK mid-upload does.
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


def _run(ok, fails, ms=6000):
    fakes.install()
    from app.runtime import Runtime
    clock = Clock(0)
    imu = FlakyIMU(clock, ok, fails)
    rt = Runtime(Board(imu=imu), parts=("imu",), clock=clock, sleep_ms=clock.sleep,
                 renderer=_renderer(), gc_collect=lambda: None)
    rt.begin(0)
    rt.run(max_ms=ms)
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
    assert all(st[i + 1] - st[i] >= CHIP_MS for i in range(1, len(st) - 1)), st
