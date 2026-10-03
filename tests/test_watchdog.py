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
    assert hits == [] and not wd.fired
    clk.t = 15001 + 8000 - 7000 + 7000; wd._check()
    assert hits and wd.fired
    assert wd.stop() is True and wd._tim is None


def test_runtime_run_feeds_and_picks_mode_from_usb_power():
    m = fakes.install()
    from app.runtime import Runtime

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
    assert rt.wd is not None                        # hardware WDT stays armed after the loop

    class UsbPmu:
        def vbus_present(self):
            return True

        def poll(self):
            return 0

        def battery_percent(self):
            return 90

        def __getattr__(self, name):
            return lambda *a, **k: 0

    class UsbBoard(Board):
        pmu = UsbPmu()

    fakes.install()
    rt2 = Runtime(UsbBoard(), clock=clk, sleep_ms=sleep, watchdog_ms=8000)
    rt2.run(max_ms=300)
    assert not m.wdts and rt2.wd is None            # soft watchdog on USB, switched off on exit
