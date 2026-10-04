"""T-Watch 2020 V1 board bring-up: one place that owns buses and init order.

Every subsystem is a lazy attribute, so a notebook can touch just one part::

    from hal.board import Board
    b = Board()          # sets 240 MHz, nothing else
    b.touch.read()       # creates I2C bus 1 + FT6336 only

``Board().init()`` brings everything up in the safe order: CPU clock, the one
shared I2C bus 0 (AXP202, BMA423, RTC), AXP202 (DCDC3 kept on, LDO2 at
3.3 V and on for panel + backlight), display, backlight, IMU, touch on I2C
bus 1, haptics, radio.
Never create a second I2C on pins 21/22: use ``board.i2c0``.

Safe boot for ``main.py``::

    from hal.board import Board, safe_boot
    b = Board()
    if safe_boot(b):
        print("safe boot: app skipped")   # REPL stays free for mpremote
    else:
        import app; app.run(b)

``safe_boot`` returns a reason when the file ``/noapp`` exists, or when the
side (PEK) key gets a fresh double press, or a fresh press held ~1.5 s, starting
within the first second after boot. Holding the key to power on and letting go
never counts.

Pass ``factories={"imu": fn}`` (fn(board) -> driver) to override any part.

Debug mode (hal/debuglink.py): set ``board.debug`` to the link before the
radio is made. A joined Wi-Fi ``DebugLink`` (its ``sta`` set) starts the
radio in its associated mode on the access point's channel (``channel`` is
then unused); the USB ``SerialLink`` leaves the radio as in normal play.
"""

import machine
from finder.compat import sleep_ms as _sleep_ms
from hal import pins
from hal import axp202

ORDER = ("pmu", "display", "backlight", "imu", "touch", "haptics", "radio")


class Board:
    """Lazy owner of the watch hardware. ``errors`` maps part -> exception
    for parts that failed during ``init(strict=False)``."""

    # touch_rotation 0: raw FT6336 coords already match MADCTL 0xC0 (LilyGO
    # TTGO.h getTouch, TFT rotation 2 -> x = __x, y = __y on the 2020 V1).
    def __init__(self, cpu_hz=pins.CPU_HZ, fast_spi=False, touch_rotation=0,
                 brightness=0.6, channel=None, factories=None, debug=None):
        if cpu_hz:
            machine.freq(cpu_hz)
        self.fast_spi = fast_spi
        self.touch_rotation = touch_rotation
        self.default_brightness = brightness
        self.channel = channel
        self.debug = debug        # hal.debuglink link; a joined ``sta``: radio on its channel
        self.factories = factories or {}
        self.errors = {}
        self._i2c0 = None
        self._i2c1 = None
        self._parts = {}

    # -- buses ----------------------------------------------------------------
    @property
    def i2c0(self):
        """The ONE I2C for pins 21/22 (AXP202, BMA423, PCF8563)."""
        if self._i2c0 is None:
            self._i2c0 = machine.I2C(pins.I2C0_ID, sda=machine.Pin(pins.I2C0_SDA),
                                     scl=machine.Pin(pins.I2C0_SCL), freq=pins.I2C0_FREQ)
        return self._i2c0

    @property
    def i2c1(self):
        """Touch bus (pins 23/32)."""
        if self._i2c1 is None:
            self._i2c1 = machine.I2C(pins.I2C1_ID, sda=machine.Pin(pins.TOUCH_SDA),
                                     scl=machine.Pin(pins.TOUCH_SCL), freq=pins.I2C0_FREQ)
        return self._i2c1

    # -- parts ----------------------------------------------------------------
    def _get(self, name):
        p = self._parts.get(name)
        if p is None:
            f = self.factories.get(name)
            p = f(self) if f is not None else getattr(self, "_make_" + name)()
            self._parts[name] = p
        return p

    def has(self, name):
        """True if ``name`` has been created (does not create it)."""
        return name in self._parts

    @property
    def pmu(self):
        return self._get("pmu")

    @property
    def display(self):
        return self._get("display")

    @property
    def imu(self):
        return self._get("imu")

    @property
    def touch(self):
        return self._get("touch")

    @property
    def haptics(self):
        return self._get("haptics")

    @property
    def radio(self):
        return self._get("radio")

    def _make_pmu(self):
        p = axp202.AXP202(self.i2c0, irq_pin=machine.Pin(pins.AXP202_IRQ, machine.Pin.IN))
        p.set_ldo2_mv(3300)       # LDO2 feeds panel + backlight (as TTGO.h)
        p.set_ldo2(True)          # backlight supply (PWM duty still 0)
        p.enable_pek()
        p.clear_irqs()            # drop latches from before this boot (power-on hold)
        return p

    def _bl_power(self):
        try:
            return self.pmu.set_ldo2
        except OSError as e:
            # PMU not answering on I2C0 (bus fault): build the panel anyway;
            # it only shows if LDO2 is already on
            self.errors["pmu"] = e
            return None

    def _make_display(self):
        from hal.st7789 import ST7789
        return ST7789(fast=self.fast_spi, bl_power=self._bl_power())

    def _make_backlight(self):
        d = self.display
        d.brightness(self.default_brightness)
        return d

    def _make_imu(self):
        from hal.bma423 import BMA423
        return BMA423(self.i2c0)

    def _make_touch(self):
        from hal.ft6336 import FT6336
        return FT6336(i2c=self.i2c1, int_pin=pins.TOUCH_INT, rotation=self.touch_rotation)

    def _make_haptics(self):
        from hal.haptics import Motor
        return Motor()

    def _make_radio(self):
        from hal.radio import EspNowRadio, DEFAULT_CHANNEL
        # STA up, channel/txpower/pm set, ESP-NOW active
        sta = None if self.debug is None else self.debug.sta
        if sta is not None:                 # Wi-Fi link: keep the connection, use its channel
            return EspNowRadio().begin(sta=sta)
        return EspNowRadio(channel=self.channel or DEFAULT_CHANNEL).begin()

    # -- helpers ----------------------------------------------------------------
    def init(self, parts=ORDER, strict=True):
        """Create ``parts`` in ORDER. With ``strict=False`` a failing part is
        recorded in ``errors`` and skipped. Returns self."""
        for name in ORDER:
            if name not in parts:
                continue
            try:
                self._get(name)
            except Exception as e:  # noqa: BLE001 - optional hardware
                if strict:
                    raise
                self.errors[name] = e
        return self

    def brightness(self, level=None):
        """Backlight 0..1 via the display's GPIO12 PWM (LDO2 handled there)."""
        return self.display.brightness(level)

    def button(self):
        """AXP202 IRQ events since last call (``axp202.EV_*`` mask)."""
        return self.pmu.poll()


def safe_boot(board=None, window_ms=1000, flag="/noapp", step_ms=20, sleep=None,
              hold_ms=3000, gap_ms=600):
    """Return why the app should be skipped ("flag" / "pek"), else None.

    The ``flag`` file (``/noapp``) is checked first and is the reliable path.
    Otherwise the side (PEK) key is watched for ``window_ms``. The PEK is also
    the power-on key and the AXP202 keeps its IRQ bits across ESP32 boots, so
    everything latched before this call is cleared and a lone release (letting
    go of the power-on press) is ignored. Only a FRESH press counts, and only as
    a double press (second press within ``gap_ms`` of the first release) or a
    press held until the PMU long-press IRQ (about 1.5 s). A press still held at
    the end of the window extends it by up to ``hold_ms``. I2C errors never
    raise: a PMU that stops answering just lets the app start.
    """
    try:
        import os
        os.stat(flag)
        return "flag"
    except (OSError, ImportError):
        pass
    if board is None:
        board = Board(cpu_hz=None)
    try:
        pmu = board.pmu
        pmu.enable_pek(edges=True)
        pmu.clear_irqs()          # drop pre-boot latches (power-on/off holds)
    except OSError:
        return None               # no PMU: never block the app on it
    sleep = sleep or _sleep_ms
    why = None
    presses = 0
    down = False
    t = 0
    end = window_ms
    try:
        while t < end or (down and t < window_ms + hold_ms):
            ev = pmu.poll()
            if presses and ev & axp202.EV_LONG:
                why = "pek"           # fresh press held to long-press
                break
            p = ev & axp202.EV_PRESS
            r = ev & axp202.EV_RELEASE
            if p:
                presses += 1
                if presses >= 2:
                    why = "pek"       # double press
                    break
            if p and not r:
                down = True
            elif r and not p:
                down = False          # both in one poll: state unchanged
            if r and presses == 1:
                e = t + gap_ms        # room for the second press
                end = e if e > end else end
            sleep(step_ms)
            t += step_ms
    except OSError:
        pass                      # bus glitch: keep what we saw, run the app
    try:
        pmu.disable_irqs(4, axp202.IRQ5_PEK_FALL | axp202.IRQ5_PEK_RISE)
        pmu.clear_irqs()
    except OSError:
        pass
    return why
