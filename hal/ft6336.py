"""FocalTech FT6336 (FT6236-compatible) capacitive touch, T-Watch 2020 V1.

I2C bus 1 (SDA 23, SCL 32) at 0x38; INT on GPIO38 (input only, active low).
Each ``read()`` is ONE 5-byte burst from TD_STATUS (0x02) into a
preallocated buffer, mapped into the display frame. With the display at
MADCTL 0xC0 + row offset 80 (TFT_eSPI rotation 2, LilyGO's default) raw panel
coordinates already match the screen, so the default is rotation 0 (identity):
LilyGO TTGO.h getTouch() uses ``x = __x; y = __y`` at rotation 2 for the
standard 2020 V1 panel. rotation=2 (x' = W-1-x, y' = H-1-y) is only for the
older LILYGO_WATCH_2020_PANEL_V1 variant. Confirm by tapping the corners.

Registers (FT6x36 datasheet): 0x02 TD_STATUS (touch count, low nibble),
0x03 P1_XH (event flag bits 7:6, x bits 11:8), 0x04 P1_XL, 0x05 P1_YH
(y bits 11:8), 0x06 P1_YL, 0x86 CTRL (1 = monitor mode when idle),
0xA3 chip id, 0xA4 G_MODE (0 = INT held low while touched), 0xA8 vendor id.
"""

import machine
from hal import pins

try:
    from micropython import const
except ImportError:  # CPython tests
    def const(x):
        return x

ADDR = const(0x38)
_TD_STATUS = const(0x02)
_CTRL = const(0x86)
_CHIP_ID = const(0xA3)
_G_MODE = const(0xA4)
_VENDOR_ID = const(0xA8)
_EV_LIFT = const(1)      # event flag 01 = lift up


class FT6336:
    """Polled touch reader.

    ``rotation`` (0..3 quarter turns clockwise) maps raw panel coordinates to
    the display, then ``mirror_x``/``mirror_y`` flip the result. ``int_pin``
    (GPIO number or Pin, optional) enables ``int_active()``; ``gate_int=True``
    additionally lets ``read()`` skip the bus while INT is high. Gating relies
    on INT staying low for the whole touch (G_MODE 0), which is NOT yet
    verified on the watch, so it is off by default (plain polling).
    """

    def __init__(self, i2c=None, addr=ADDR, width=240, height=240, rotation=0,
                 mirror_x=False, mirror_y=False, int_pin=None, gate_int=False,
                 freq=400_000):
        if i2c is None:
            i2c = machine.I2C(pins.I2C1_ID, scl=machine.Pin(pins.TOUCH_SCL),
                              sda=machine.Pin(pins.TOUCH_SDA), freq=freq)
        self.i2c = i2c
        self.addr = addr
        self.width = width      # raw panel size
        self.height = height
        self.rotation = rotation & 3
        self.mirror_x = mirror_x
        self.mirror_y = mirror_y
        self.errors = 0
        self._buf = bytearray(5)
        self._res = [False, 0, 0]
        self._int = None
        self._gate = False
        if int_pin is not None:
            if isinstance(int_pin, int):
                int_pin = machine.Pin(int_pin, machine.Pin.IN)
            self._int = int_pin
            # Polling mode should keep INT low for the whole touch, so "INT
            # high and not touching" would mean idle (unverified on hardware).
            try:
                self.i2c.writeto_mem(addr, _G_MODE, b"\x00")
                self._gate = bool(gate_int)
            except OSError:
                self._int = None

    @property
    def size(self):
        """(width, height) of the mapped output frame."""
        if self.rotation & 1:
            return self.height, self.width
        return self.width, self.height

    def read(self):
        """Poll once -> ``[touching, x, y]``.

        The SAME list is returned every call (allocation-free); copy it if you
        need to keep it. x/y keep their last value while not touching. Bus
        errors count in ``errors`` and read as "not touching".
        """
        r = self._res
        if self._gate and not r[0] and self._int.value():
            return r
        b = self._buf
        try:
            self.i2c.readfrom_mem_into(self.addr, _TD_STATUS, b)
        except OSError:
            self.errors += 1
            r[0] = False
            return r
        n = b[0] & 0x0F
        if n == 0 or n > 2 or (b[1] >> 6) == _EV_LIFT:
            r[0] = False
            return r
        w = self.width
        h = self.height
        x = ((b[1] & 0x0F) << 8) | b[2]
        y = ((b[3] & 0x0F) << 8) | b[4]
        if x >= w:
            x = w - 1
        if y >= h:
            y = h - 1
        rot = self.rotation
        if rot == 1:
            x, y = h - 1 - y, x
            w, h = h, w
        elif rot == 2:
            x = w - 1 - x
            y = h - 1 - y
        elif rot == 3:
            x, y = y, w - 1 - x
            w, h = h, w
        if self.mirror_x:
            x = w - 1 - x
        if self.mirror_y:
            y = h - 1 - y
        r[0] = True
        r[1] = x
        r[2] = y
        return r

    def int_active(self):
        """True while INT is low (touch in progress); None without an INT pin."""
        if self._int is None:
            return None
        return not self._int.value()

    def chip_id(self):
        """Chip id register (0x64 on FT6336U, 0x36 on FT6236)."""
        return self.i2c.readfrom_mem(self.addr, _CHIP_ID, 1)[0]

    def vendor_id(self):
        """FocalTech vendor id register (0x11)."""
        return self.i2c.readfrom_mem(self.addr, _VENDOR_ID, 1)[0]

    def set_monitor(self, on):
        """Let the controller drop to low-rate monitor mode when idle."""
        self.i2c.writeto_mem(self.addr, _CTRL, b"\x01" if on else b"\x00")
