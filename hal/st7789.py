"""Pure-Python ST7789 driver for the T-Watch 2020 V1 240x240 panel.

Stock MicroPython has no st7789 C module, so this driver only moves bytes:
compose pixels off-screen (framebuf RGB565 strips, usually a GS8 blit through
a palette) and push them with ``push_strip``/``push_frame``.

Colour format: the panel wants RGB565 MSB-first, but framebuf stores RGB565
little-endian. Every colour handed to framebuf, palettes or ``fill`` must be
BYTE-SWAPPED -- build them with ``rgb565(r, g, b)`` or ``swap16(c)``.

Clock: pins 18/19 go through the GPIO matrix, so stock firmware must stay at
or below 26.67 MHz (80 MHz / 3); asking for more raises ESP_ERR_NOT_SUPPORTED
deep in the IDF and crashes. 40 MHz is only allowed with ``fast=True`` on a
custom build. A full frame (115,200 B) takes ~35 ms at 26.67 MHz.

Backlight: GPIO12 PWM, but the panel/backlight supply is AXP202 LDO2. On the
2020 V1 LDO2 also powers the ST7789 itself (LilyGO TTGO.h: the V1 panel
supply is shared with the backlight), so LDO2 must be ON (3.3 V, AXP202 reg
0x28 bits 7:4 = 0xF) BEFORE ``init()`` -- commands sent to an unpowered
panel are lost and it comes up in its power-on-reset state (sleep, display
off). Pass ``bl_power`` (callable taking a bool, e.g. the AXP driver's LDO2
switch) and ``init()`` calls ``bl_power(True)`` and waits ~10 ms before
SWRESET; otherwise turn LDO2 on yourself before constructing the driver.
This driver only ever calls ``bl_power(True)``; backlight off is PWM duty
0. If LDO2 is ever cut on purpose, the panel loses its registers and GRAM:
call ``init()`` (full SWRESET sequence) after restoring it, not ``wake()``.

Strips: ``push_strip`` keeps the window open (CS low, no new command) after
a strip that does not reach the bottom row, so strips pushed top to bottom
cost one SPI write each (a windowed strip costs five more small writes:
52.4 vs 44.1 ms a frame on the watch). Any other SPI use closes it.
Strips go out in the caller: a send thread was tried and measured no faster
on the watch (4 Oct 2026), since drawing (viper kernels, framebuf calls)
holds the GIL the thread needs to queue each DMA chunk.
"""

import machine
from hal import pins
from finder.compat import const, sleep_ms as _sleep_ms, ticks_diff, ticks_ms

SWRESET = const(0x01)
SLPIN = const(0x10)
SLPOUT = const(0x11)
NORON = const(0x13)
INVON = const(0x21)
DISPOFF = const(0x28)
DISPON = const(0x29)
CASET = const(0x2A)
RASET = const(0x2B)
RAMWR = const(0x2C)
MADCTL = const(0x36)
COLMOD = const(0x3A)

WIDTH = const(240)
HEIGHT = const(240)
MADCTL_ROT2 = const(0xC0)   # MY|MX: this watch's upright orientation ...
ROW_OFFSET_ROT2 = const(80)  # ... shows GRAM rows 80..319 of 320
MAX_STOCK_BAUD = pins.TFT_BAUD
FAST_BAUD = pins.TFT_BAUD_FAST
SLP_MS = const(120)          # ST7789 minimum from SLPIN to SLPOUT, and after SLPOUT


def swap16(c):
    """Swap the two bytes of a 16-bit value (native RGB565 <-> framebuf)."""
    return ((c & 0xFF) << 8) | ((c >> 8) & 0xFF)


def rgb565(r, g, b):
    """8-bit r, g, b -> byte-swapped RGB565 int ready for framebuf/palettes."""
    c = ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | ((b & 0xFF) >> 3)
    return ((c & 0xFF) << 8) | (c >> 8)


class ST7789:
    """240x240 ST7789 over SPI with CS held low for each window + pixel burst.

    ``spi``/``dc``/``cs`` may be injected (tests, shared bus); otherwise they
    are created from ``hal.pins`` with ``miso=None``. Pass ``init=False`` to
    skip the reset/init sequence (e.g. the panel is already running).
    """

    def __init__(self, spi=None, dc=None, cs=None, baudrate=None, fast=False,
                 madctl=MADCTL_ROT2, xoff=0, yoff=ROW_OFFSET_ROT2,
                 width=WIDTH, height=HEIGHT, strip_rows=24,
                 backlight=pins.TFT_BL, bl_power=None, bl_freq=1000, init=True):
        if baudrate is None:
            baudrate = FAST_BAUD if fast else MAX_STOCK_BAUD
        if baudrate > MAX_STOCK_BAUD and not fast:
            raise ValueError("baudrate > 26.67 MHz crashes stock firmware; pass fast=True only on a NO_DUMMY build")
        if spi is None:
            spi = machine.SPI(pins.TFT_SPI_ID, baudrate=baudrate, polarity=0, phase=0,
                              sck=machine.Pin(pins.TFT_SCK), mosi=machine.Pin(pins.TFT_MOSI),
                              miso=None)
        if dc is None:
            dc = machine.Pin(pins.TFT_DC, machine.Pin.OUT, value=0)
        if cs is None:
            cs = machine.Pin(pins.TFT_CS, machine.Pin.OUT, value=1)
        self.spi = spi
        self.baudrate = baudrate
        self._dc = dc
        self._cs = cs
        self.width = width
        self.height = height
        self.madctl = madctl
        self.xoff = xoff
        self.yoff = yoff
        self.strip_rows = strip_rows
        self.frame_bytes = width * height * 2
        self._c1 = bytearray(1)
        self._d1 = bytearray(1)
        self._win = bytearray(4)
        self._pf_src = None       # push_frame slice cache
        self._pf_slices = None
        self._fill = None         # lazy fill pattern buffer
        self._fill_c = -1
        self._bl_power = bl_power
        self._bl_on = False
        self._level = 0.0
        self._last_level = 1.0
        self._pwm = None
        if backlight is not None:
            self._pwm = machine.PWM(machine.Pin(backlight, machine.Pin.OUT), freq=bl_freq, duty_u16=0)
        self.asleep = False
        self._slpin_t = None      # ticks_ms of the last SLPIN
        self._next = -1           # row an open full-width window continues at
        if init:
            self.init()

    # --- low level -------------------------------------------------------
    def _cmd(self, c, data=None):
        """One command (+ optional data) framed by its own CS pulse."""
        self._next = -1           # a command ends any open window
        cs = self._cs
        dc = self._dc
        cs(0)
        dc(0)
        self._c1[0] = c
        self.spi.write(self._c1)
        if data is not None:
            dc(1)
            self.spi.write(data)
        cs(1)

    def _cmd1(self, c, v):
        self._d1[0] = v
        self._cmd(c, self._d1)

    def _addr(self, c, a, b):
        # CASET/RASET body, CS already low. Allocation-free.
        w = self._win
        w[0] = a >> 8
        w[1] = a & 0xFF
        w[2] = b >> 8
        w[3] = b & 0xFF
        dc = self._dc
        spi = self.spi
        dc(0)
        self._c1[0] = c
        spi.write(self._c1)
        dc(1)
        spi.write(w)

    def _begin(self, x0, y0, x1, y1):
        """CS low, CASET/RASET (+offsets), RAMWR, DC high: ready for pixels."""
        self._next = -1
        self._cs(0)
        self._addr(CASET, x0 + self.xoff, x1 + self.xoff)
        self._addr(RASET, y0 + self.yoff, y1 + self.yoff)
        self._dc(0)
        self._c1[0] = RAMWR
        self.spi.write(self._c1)
        self._dc(1)

    # --- setup -----------------------------------------------------------
    def init(self):
        """Software reset + init (no RST pin on this watch). Blocks ~340 ms.

        With ``bl_power`` set, powers LDO2 (panel supply on V1) first and
        waits for the panel's power-on reset before SWRESET. GRAM is cleared
        to black before DISPON (~35 ms): it is random after power-on and
        SWRESET keeps it, so the panel never shows noise or a stale frame.
        """
        if self._bl_power is not None:
            self._bl_power(True)
            self._bl_on = True
            _sleep_ms(10)
        self._cmd(SWRESET)
        _sleep_ms(150)
        self._cmd(SLPOUT)
        _sleep_ms(SLP_MS)
        self._cmd1(COLMOD, 0x55)  # 16-bit RGB565
        _sleep_ms(10)
        self._cmd1(MADCTL, self.madctl)
        self._cmd(INVON)          # this IPS panel needs inversion on
        _sleep_ms(10)
        self._cmd(NORON)
        _sleep_ms(10)
        self.fill(0)              # black GRAM before DISPON (see above)
        self._cmd(DISPON)
        _sleep_ms(10)
        self.asleep = False

    # --- pixels ----------------------------------------------------------
    def push_strip(self, y0, h, buf):
        """Push a full-width strip of ``h`` rows starting at row ``y0``.

        A strip that starts where the last one ended continues its window;
        otherwise a window from ``y0`` to the bottom row opens. CS stays low
        until a strip reaches the bottom row or another command is sent.
        """
        if len(buf) != self.width * h * 2:
            raise ValueError("strip buf must be width*h*2 bytes")
        n = self._next
        self._next = -1           # until this strip is out
        if y0 != n:
            self._begin(0, y0, self.width - 1, self.height - 1)
        self.spi.write(buf)
        y = y0 + h
        if y >= self.height:
            self._cs(1)
        else:
            self._next = y

    def _slice_frame(self, fb):
        # Kept out of push_frame: a comprehension there would turn its locals
        # into closure cells, which MicroPython heap-allocates on every call.
        if len(fb) != self.frame_bytes:
            raise ValueError("frame must be width*height*2 bytes")
        mv = memoryview(fb)
        n = self.width * 2 * self.strip_rows
        self._pf_slices = [mv[i:i + n] for i in range(0, self.frame_bytes, n)]
        self._pf_src = fb

    def push_frame(self, fb):
        """Push a whole frame (width*height*2 bytes) as strip-sized writes
        under one window and one CS-low burst. The slice list is cached per
        buffer, so repeated pushes of the same framebuffer allocate nothing.
        """
        if fb is not self._pf_src:
            self._slice_frame(fb)
        self._begin(0, 0, self.width - 1, self.height - 1)
        spi = self.spi       # not spi.write: a stored bound method is a heap alloc
        sl = self._pf_slices
        for i in range(len(sl)):
            spi.write(sl[i])
        self._cs(1)

    def fill_rect(self, x, y, w, h, colour):
        """Solid rectangle straight to the panel (byte-swapped colour)."""
        if w <= 0 or h <= 0:
            return
        buf = self._fill
        if buf is None:
            buf = self._fill = bytearray(self.width * 2 * 8)  # 8 rows, < one DMA chunk
            self._fill_mv = memoryview(buf)
        mv = self._fill_mv
        if colour != self._fill_c:
            buf[0] = colour & 0xFF   # framebuf byte order
            buf[1] = (colour >> 8) & 0xFF
            n = 2
            size = len(buf)
            while n < size:
                k = n if n * 2 <= size else size - n
                mv[n:n + k] = mv[0:k]
                n += k
            self._fill_c = colour
        total = w * h * 2
        size = len(buf)
        self._begin(x, y, x + w - 1, y + h - 1)
        spi = self.spi
        while total >= size:
            spi.write(buf)
            total -= size
        if total:
            spi.write(mv[0:total])
        self._cs(1)

    def fill(self, colour):
        """Fill the whole screen (byte-swapped RGB565)."""
        self.fill_rect(0, 0, self.width, self.height, colour)

    # --- power / backlight -----------------------------------------------
    def brightness(self, level=None):
        """Set backlight 0..1 (PWM duty on GPIO12); returns the current level.

        Calls ``bl_power(True)`` before the first non-zero level if
        ``init()`` has not already done so (``init=False``).
        """
        if level is None:
            return self._level
        level = 0.0 if level < 0 else 1.0 if level > 1 else float(level)
        duty = int(level * 65535 + 0.5)
        if duty and not self._bl_on:
            if self._bl_power is not None:
                self._bl_power(True)
            self._bl_on = True
        if self._pwm is not None:
            self._pwm.duty_u16(duty)
        self._level = level
        if duty:
            self._last_level = level
        return level

    def sleep(self):
        """Backlight off (PWM duty 0), DISPOFF + SLPIN (GRAM is kept). ~5 ms.

        LDO2 is left on: on V1 it also powers the panel.
        """
        if self._pwm is not None:
            self._pwm.duty_u16(0)
        self._level = 0.0
        self._cmd(DISPOFF)
        self._cmd(SLPIN)
        self._slpin_t = ticks_ms()
        _sleep_ms(5)
        self.asleep = True

    def wake(self, level=None, wait=None):
        """SLPOUT + DISPON, then restore the last non-zero brightness (or
        ``level``; pass 0 to keep the backlight off). Blocks ~120 ms, up to
        120 ms more when ``sleep()`` was recent (SLPOUT >= 120 ms after
        SLPIN). ``wait(ms)`` does these delays (default ``sleep_ms``); pass
        the main loop's idle so the motor keeps its timing.
        """
        wait = wait or _sleep_ms
        t = self._slpin_t
        if t is not None:
            e = ticks_diff(ticks_ms(), t)
            if 0 <= e < SLP_MS:
                wait(SLP_MS - e)
        self._cmd(SLPOUT)
        wait(SLP_MS)
        self._cmd(DISPON)
        self.asleep = False
        self.brightness(self._last_level if level is None else level)

    def deinit(self):
        if self._pwm is not None:
            self._pwm.deinit()
        self.spi.deinit()
