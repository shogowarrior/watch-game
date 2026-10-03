"""Loop watchdog: reboots the watch if the game loop stops feeding it.

On battery the ESP32 hardware WDT is used: it also catches hangs inside C
calls, and it cannot be stopped once started, which is what we want in the
field. On USB power (at the desk, REPL in reach) a hardware timer checks the
last feed time instead, so stopping the app (Ctrl-C) can switch it off again.
"""

from finder.compat import ticks_ms, ticks_diff

MODE_HW = "hw"
MODE_SOFT = "soft"


class Watchdog:
    def __init__(self, timeout_ms=8000, usb=False, clock=ticks_ms, timer_id=3,
                 machine_mod=None, reset=None):
        if machine_mod is None:
            import machine as machine_mod
        self.timeout_ms = timeout_ms
        self.mode = MODE_SOFT if usb else MODE_HW
        self._clock = clock
        self._last = clock()
        self._wdt = None
        self._tim = None
        if self.mode == MODE_HW:
            self._wdt = machine_mod.WDT(timeout=timeout_ms)
        else:
            self._reset = reset or machine_mod.reset
            self._tim = machine_mod.Timer(timer_id)
            self._tim.init(mode=machine_mod.Timer.PERIODIC, period=1000, callback=self._check)

    def feed(self):
        if self._wdt is not None:
            self._wdt.feed()
        else:
            self._last = self._clock()

    def _check(self, _timer=None):
        if ticks_diff(self._clock(), self._last) > self.timeout_ms:
            self._reset()

    def stop(self):
        """Switch the soft watchdog off. The hardware WDT cannot be stopped: returns False."""
        if self._tim is not None:
            self._tim.deinit()
            self._tim = None
            return True
        return self._wdt is None
