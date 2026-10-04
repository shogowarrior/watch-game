"""hal/st7789.py: exact init bytes, window offsets, DC/CS framing, SPI setup."""

from tests import Skip, fakes

fakes.install()
from hal import st7789 as st  # noqa: E402  (import failure must FAIL, not skip)

GRAM_CLEAR = (b"\x2a\x00\x00\x00\xef" b"\x2b\x00\x50\x01\x3f"
              b"\x2c" + bytes(240 * 240 * 2))   # black frame before DISPON
INIT_BYTES = (b"\x01" b"\x11" b"\x3a\x55" b"\x36\xc0" b"\x21" b"\x13"
              + GRAM_CLEAR + b"\x29")


class _Pin:
    def __init__(self, log, name, v=1):
        self.log = log
        self.name = name
        self.v = v

    def __call__(self, v=None):
        if v is None:
            return self.v
        self.v = 1 if v else 0
        self.log.append((self.name, self.v))

    value = __call__


class _SPI:
    def __init__(self, log):
        self.log = log

    def write(self, buf):
        self.log.append(("spi", bytes(buf)))

    def deinit(self):
        pass


def _rig(**kw):
    """Driver with recording SPI/DC/CS and a fake clock that sleeps advance;
    returns (driver, log, sleeps, machine, clock)."""
    m = fakes.install()
    log = []
    sleeps = []
    clk = [0]

    def sleep_ms(ms):
        sleeps.append(ms)
        clk[0] += ms

    st._sleep_ms = sleep_ms
    st.ticks_ms = lambda: clk[0]
    d = st.ST7789(spi=_SPI(log), dc=_Pin(log, "dc", 0), cs=_Pin(log, "cs", 1), **kw)
    return d, log, sleeps, m, clk


def _decode(log):
    """-> (commands [(cmd, data)], cs_frames). Asserts CS is low on every write."""
    cs = 1
    dc = 0
    cmds = []
    frames = 0
    for ev in log:
        if ev[0] == "cs":
            if ev[1] == 0 and cs == 1:
                frames += 1
            cs = ev[1]
        elif ev[0] == "dc":
            dc = ev[1]
        else:
            assert cs == 0, "SPI write with CS high"
            if dc == 0:
                assert len(ev[1]) == 1, ev
                cmds.append([ev[1][0], b""])
            else:
                assert cmds, "data before any command"
                cmds[-1][1] += ev[1]
    assert cs == 1, "CS left low"
    return [(c, bytes(dt)) for c, dt in cmds], frames


def _flat(cmds):
    out = b""
    for c, dt in cmds:
        out += bytes([c]) + dt
    return out


def test_default_spi_is_stock_safe_and_miso_none():
    m = fakes.install()
    st._sleep_ms = lambda ms: None
    d = st.ST7789(init=False)
    spi = d.spi
    assert spi.miso is None
    assert spi.baudrate == 26_666_667
    assert spi.id == 1 and spi.sck.id == 18 and spi.mosi.id == 19
    assert d._dc.id == 27 and d._cs.id == 5
    assert m.pins[5].value() == 1          # CS idles high
    assert d._pwm.pin.id == 12 and d._pwm.duty_u16() == 0  # backlight off until asked
    assert m.spi_log == []


def test_baudrate_guard():
    fakes.install()
    st._sleep_ms = lambda ms: None
    try:
        st.ST7789(baudrate=40_000_000, init=False)
        assert False, "40 MHz must need fast=True"
    except ValueError:
        pass
    d = st.ST7789(baudrate=20_000_000, init=False)
    assert d.spi.baudrate == 20_000_000
    d = st.ST7789(fast=True, init=False)
    assert d.spi.baudrate == 40_000_000


def test_init_sequence_exact():
    d, log, sleeps, _, _ = _rig()
    cmds, frames = _decode(log)
    assert cmds == [
        (0x01, b""), (0x11, b""), (0x3A, b"\x55"), (0x36, b"\xc0"),
        (0x21, b""), (0x13, b""),
        (0x2A, b"\x00\x00\x00\xef"),          # CASET 0..239
        (0x2B, b"\x00\x50\x01\x3f"),          # RASET 80..319 (row offset 80)
        (0x2C, bytes(240 * 240 * 2)),         # GRAM black: random after power-on
        (0x29, b""),
    ], [(c, len(dt)) for c, dt in cmds]
    assert _flat(cmds) == INIT_BYTES
    assert frames == 8                      # CASET+RASET+RAMWR share one CS frame
    assert sleeps[:2] == [150, 120] and sum(sleeps) >= 270
    assert not d.asleep


def test_init_through_fake_machine_pins():
    m = fakes.install()
    st._sleep_ms = lambda ms: None
    st.ST7789()
    data = b"".join(p for (_, _, p) in m.spi_log)
    assert data == INIT_BYTES
    cs = m.pins[5].writes
    assert cs[-1] == 1 and cs.count(0) == 8
    # DC: low for each command byte, high before each data payload.
    dc = m.pins[27].writes
    assert dc.count(1) == 5 and dc[-1] == 0


def test_custom_orientation_offsets():
    d, log, _, _, _ = _rig(madctl=0x00, yoff=0)
    cmds, _ = _decode(log)
    assert (0x36, b"\x00") in cmds and (0x2B, b"\x00\x00\x00\xef") in cmds


def test_push_strip_window_and_framing():
    d, log, _, _, _ = _rig()
    del log[:]
    buf = bytearray(240 * 24 * 2)
    buf[0] = 0xAB
    b2 = bytearray(240 * 24 * 2)
    b2[1] = 0xCD
    b3 = bytearray(240 * 24 * 2)
    b3[2] = 0xEF
    d.push_strip(48, 24, buf)
    assert d._cs.v == 0                       # window left open for the next strip
    d.push_strip(72, 24, b2)                  # continues it: one write, no command
    d.push_strip(216, 24, b3)                 # not contiguous: a new window; the
    cmds, frames = _decode(log)               # bottom row closes it
    assert frames == 1
    assert cmds == [
        (0x2A, b"\x00\x00\x00\xef"),
        (0x2B, bytes([0, 128, 1, 63])),       # rows 48..239 + 80
        (0x2C, bytes(buf) + bytes(b2)),
        (0x2A, b"\x00\x00\x00\xef"),
        (0x2B, bytes([1, 40, 1, 63])),        # rows 216..239 + 80
        (0x2C, bytes(b3)),
    ]
    assert len([e for e in log if e[0] == "spi"]) == 6 + 1 + 6
    dc = [v for (n, v) in log if n == "dc"]
    assert dc == [0, 1, 0, 1, 0, 1] * 2
    try:
        d.push_strip(0, 24, bytearray(10))
        assert False
    except ValueError:
        pass


def test_strips_top_to_bottom_are_one_window():
    d, log, _, _, _ = _rig()
    strips = [bytes([k]) * (240 * 24 * 2) for k in range(10)]
    for _ in range(2):                        # the next frame opens a new window
        del log[:]
        for k in range(10):
            d.push_strip(24 * k, 24, strips[k])
        cmds, frames = _decode(log)
        assert frames == 1 and len(cmds) == 3
        assert cmds[1] == (0x2B, b"\x00\x50\x01\x3f")
        assert cmds[2] == (0x2C, b"".join(strips))
        assert len([e for e in log if e[0] == "spi"]) == 5 + 10


def test_a_command_closes_an_open_window():
    d, log, _, _, _ = _rig()
    strip = bytes(240 * 24 * 2)
    del log[:]
    d.push_strip(0, 24, strip)
    d.sleep()                                 # DISPOFF ends the RAMWR
    d.push_strip(24, 24, strip)               # so this one opens a new window
    d.push_strip(48, 216 - 24, bytes(240 * 192 * 2))
    cmds, _ = _decode(log)
    assert [c for c, _ in cmds] == [0x2A, 0x2B, 0x2C, 0x28, 0x10, 0x2A, 0x2B, 0x2C]
    assert cmds[6] == (0x2B, bytes([0, 104, 1, 63]))


def test_push_frame_strips_under_one_cs():
    d, log, _, _, _ = _rig()
    fb = bytearray(240 * 240 * 2)
    for i in range(0, len(fb), 997):
        fb[i] = i & 0xFF
    del log[:]
    d.push_frame(fb)
    cmds, frames = _decode(log)
    assert frames == 1
    assert cmds[0] == (0x2A, b"\x00\x00\x00\xef")
    assert cmds[1] == (0x2B, b"\x00\x50\x01\x3f")
    assert cmds[2][0] == 0x2C and cmds[2][1] == bytes(fb)
    writes = [p for (n, p) in log if n == "spi"]
    assert len(writes) == 2 * 2 + 1 + 10 and len(writes[-1]) == 240 * 24 * 2
    sl = d._pf_slices
    d.push_frame(fb)
    assert d._pf_slices is sl               # cached, no re-slicing


def test_fill_and_fill_rect():
    d, log, _, _, _ = _rig()
    red = st.rgb565(255, 0, 0)
    del log[:]
    d.fill(red)
    cmds, frames = _decode(log)
    assert frames == 1 and cmds[2][0] == 0x2C
    assert cmds[2][1] == b"\xf8\x00" * (240 * 240)
    del log[:]
    d.fill_rect(10, 20, 5, 3, st.rgb565(0, 0, 255))
    cmds, _ = _decode(log)
    assert cmds == [
        (0x2A, bytes([0, 10, 0, 14])),
        (0x2B, bytes([0, 100, 0, 102])),
        (0x2C, b"\x00\x1f" * 15),
    ]


def test_colour_helpers_are_byte_swapped():
    assert st.swap16(0xF800) == 0x00F8 and st.swap16(0x00F8) == 0xF800
    assert st.rgb565(255, 0, 0) == 0x00F8
    assert st.rgb565(0, 255, 0) == 0xE007
    assert st.rgb565(0, 0, 255) == 0x1F00
    assert st.rgb565(255, 255, 255) == 0xFFFF and st.rgb565(0, 0, 0) == 0
    try:
        import framebuf
    except ImportError:
        return
    b = bytearray(4)
    fb = framebuf.FrameBuffer(b, 2, 1, framebuf.RGB565)
    fb.fill(st.rgb565(255, 0, 0))
    assert bytes(b) == b"\xf8\x00\xf8\x00"  # MSB-first on the wire


def test_brightness_sleep_wake():
    power = []
    d, log, sleeps, _, clk = _rig(bl_power=power.append)
    pwm = d._pwm
    # init() already powered LDO2 (panel supply) but left the backlight dark.
    assert pwm.duty_u16() == 0 and power == [True]
    d.brightness(0.5)
    assert power == [True] and pwm.duty_u16() == 32768
    d.brightness(0)
    assert pwm.duty_u16() == 0 and power == [True]
    d.brightness(2)
    assert pwm.duty_u16() == 65535 and d.brightness() == 1.0 and power == [True]
    del log[:]
    d.sleep()
    cmds, _ = _decode(log)
    assert cmds == [(0x28, b""), (0x10, b"")] and d.asleep
    # V1: LDO2 also powers the ST7789, so sleep must never switch it off.
    assert pwm.duty_u16() == 0 and power == [True]
    del log[:]
    del sleeps[:]
    d.wake()                                # right after SLPIN (5 ms ago)
    cmds, _ = _decode(log)
    assert cmds == [(0x11, b""), (0x29, b"")]
    assert sleeps == [115, 120]             # SLPOUT >= 120 ms after SLPIN
    assert pwm.duty_u16() == 65535 and power == [True] and not d.asleep
    d.sleep()
    clk[0] += 200                           # screen off for a while
    del sleeps[:]
    waits = []
    d.wake(0, waits.append)                 # the main loop's idle does the wait
    assert pwm.duty_u16() == 0 and waits == [120] and sleeps == []
    d.sleep()                               # SLPIN, then 5 ms
    clk[0] += 114
    d.wake(0, waits.append)
    assert waits == [120, 1, 120]
    d.brightness(0.25)
    d.sleep()
    d.brightness(0)
    d.deinit()
    assert False not in power


class _Null:
    def __call__(self, v=None):
        return 0

    def write(self, buf):
        pass


def test_hot_paths_allocation_free_on_micropython():
    import gc
    if not hasattr(gc, "mem_alloc"):
        raise Skip("CPython: no gc.mem_alloc to measure")
    fakes.install()
    n = _Null()
    d = st.ST7789(spi=n, dc=n, cs=n, backlight=None, init=False)
    strip = bytearray(240 * 24 * 2)
    fb = bytearray(240 * 240 * 2)
    d.push_frame(fb)  # warm-up builds the slice cache
    d.fill(0x1234)    # warm-up builds the fill buffer
    gc.collect()
    gc.disable()
    try:
        a0 = gc.mem_alloc()
        for _ in range(5):
            for y in range(0, 240, 24):
                d.push_strip(y, 24, strip)
            d.push_frame(fb)
            d.fill(0x1234)
        used = gc.mem_alloc() - a0
    finally:
        gc.enable()
    assert used == 0, used


def test_init_powers_ldo2_before_swreset():
    # V1: LDO2 feeds the ST7789 too; init bytes sent before it is up are lost.
    log = []
    d, _, sleeps, _, _ = _rig(bl_power=lambda on: log.append(("pwr", on)))
    assert log == [("pwr", True)] and sleeps[0] >= 5 and sleeps[1] == 150
    ev = []
    st._sleep_ms = lambda ms: None
    st.ST7789(spi=_SPI(ev), dc=_Pin(ev, "dc", 0), cs=_Pin(ev, "cs", 1),
              bl_power=lambda on: ev.append(("pwr", on)))
    assert ev[0] == ("pwr", True)
    first_spi = [i for i, e in enumerate(ev) if e[0] == "spi"][0]
    assert ev[first_spi] == ("spi", b"\x01")    # SWRESET only after power
    # init=False: nothing is powered until the first non-zero brightness.
    p = []
    d = st.ST7789(spi=_SPI([]), dc=_Pin([], "dc", 0), cs=_Pin([], "cs", 1),
                  bl_power=p.append, init=False)
    assert p == []
    d.brightness(0.3)
    d.brightness(0.6)
    assert p == [True]
    # init() again (e.g. after LDO2 was cut) re-asserts the supply first.
    d.init()
    assert p == [True, True]
