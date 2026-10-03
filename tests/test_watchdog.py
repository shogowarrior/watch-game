from tests import fakes


class Clock:
    def __init__(self):
        self.t = 0

    def __call__(self):
        return self.t


def test_battery_uses_hardware_wdt_that_cannot_stop():
    m = fakes.install()
    from hal.watchdog import Watchdog, MODE_HW
    wd = Watchdog(8000, usb=False, machine_mod=m)
    assert wd.mode == MODE_HW and m.wdts[-1].timeout == 8000
    wd.feed(); wd.feed()
    assert m.wdts[-1].feeds == 2
    assert wd.stop() is False


def test_usb_soft_watchdog_resets_only_when_starved_and_can_stop():
    m = fakes.install()
    from hal.watchdog import Watchdog, MODE_SOFT
    clk = Clock()
    hits = []
    wd = Watchdog(8000, usb=True, clock=clk, machine_mod=m, reset=lambda: hits.append(clk.t))
    assert wd.mode == MODE_SOFT and not m.wdts
    clk.t = 7000; wd._check()
    assert hits == []
    wd.feed(); clk.t = 15000; wd._check()
    assert hits == []                               # exactly the timeout since the feed at 7000
    clk.t = 15001; wd._check()
    assert hits == [15001]                          # one ms past it
    assert wd.stop() is True and wd._tim is None


def test_runtime_run_without_pmu_arms_hardware_wdt():
    # The USB case (soft watchdog, off on exit) is test_app_runtime's
    # test_watchdog_turns_hardware_once_unplugged.
    m = fakes.install()
    from app.runtime import Runtime
    from hal.watchdog import MODE_HW

    class Board:
        pmu = None
        display = None
        imu = None
        touch = None
        haptics = None
        radio = None

    clk = Clock()

    def sleep(ms):
        clk.t += max(1, int(ms))

    rt = Runtime(Board(), clock=clk, sleep_ms=sleep, watchdog_ms=8000)
    rt.run(max_ms=500)
    assert m.wdts and m.wdts[-1].feeds > 5          # no PMU -> assume battery -> hardware WDT
    assert rt.wd is not None and rt.wd.mode == MODE_HW   # stays armed after the loop
