from tests import fakes


def _setup(**kw):
    m = fakes.install()
    dev = m.add_i2c_device(1, 0x38, {0xA3: 0x64, 0xA8: 0x11})
    from hal.ft6336 import FT6336
    return m, dev, FT6336(**kw)


def _touch(dev, x, y, n=1, ev=2):
    dev.regs[0x02] = n
    dev.regs[0x03] = (ev << 6) | (x >> 8)
    dev.regs[0x04] = x & 0xFF
    dev.regs[0x05] = y >> 8
    dev.regs[0x06] = y & 0xFF


class _CountingI2C:
    def __init__(self, inner):
        self.inner = inner
        self.reads = []

    def readfrom_mem_into(self, addr, reg, buf):
        self.reads.append((addr, reg, len(buf)))
        self.inner.readfrom_mem_into(addr, reg, buf)

    def writeto_mem(self, addr, reg, data):
        self.inner.writeto_mem(addr, reg, data)


def test_default_bus_and_identity_rotation():
    # LilyGO getTouch(): display rotation 2 (our MADCTL 0xC0) -> raw x/y.
    m, dev, tp = _setup()
    assert tp.i2c.bus_id == 1 and tp.i2c.scl.id == 32 and tp.i2c.sda.id == 23
    assert tp.rotation == 0 and not tp.mirror_x and not tp.mirror_y
    _touch(dev, 10, 200)
    r = tp.read()
    assert r[0] is True and (r[1], r[2]) == (10, 200)
    for x, y in ((0, 0), (239, 0), (0, 239), (239, 239)):   # corners
        _touch(dev, x, y)
        r = tp.read()
        assert (r[1], r[2]) == (x, y)


def test_panel_v1_rotation2_opt_in():
    m, dev, tp = _setup(rotation=2)
    _touch(dev, 10, 200)
    r = tp.read()
    assert r[0] is True and (r[1], r[2]) == (229, 39)
    assert tp.read() is r          # same preallocated list every call
    assert tp.chip_id() == 0x64 and tp.vendor_id() == 0x11


def test_single_5_byte_read_from_td_status():
    m, dev, _ = _setup()
    from hal.ft6336 import FT6336
    bus = _CountingI2C(m.I2C(1))
    tp = FT6336(i2c=bus, rotation=0)
    _touch(dev, 0x0C8, 0x0AB)
    t, x, y = tp.read()
    assert bus.reads == [(0x38, 0x02, 5)]
    assert (t, x, y) == (True, 200, 171)


def test_rotations_and_mirrors():
    m, dev, tp = _setup(rotation=0)
    _touch(dev, 10, 50)
    exp = {0: (10, 50), 1: (189, 10), 2: (229, 189), 3: (50, 229)}
    for rot in range(4):
        tp.rotation = rot
        r = tp.read()
        assert (r[1], r[2]) == exp[rot], (rot, r)
    tp.rotation = 0
    tp.mirror_x = True
    r = tp.read()
    assert (r[1], r[2]) == (229, 50)
    tp.mirror_y = True
    r = tp.read()
    assert (r[1], r[2]) == (229, 189)


def test_non_square_rotation_size():
    m, dev, tp = _setup(width=240, height=320, rotation=1)
    assert tp.size == (320, 240)
    _touch(dev, 0, 0)
    r = tp.read()
    assert (r[1], r[2]) == (319, 0)
    tp.rotation = 3
    r = tp.read()
    assert (r[1], r[2]) == (0, 239)


def test_out_of_range_clamped():
    m, dev, tp = _setup(rotation=0)
    _touch(dev, 0xFFF, 300)
    r = tp.read()
    assert (r[1], r[2]) == (239, 239)


def test_not_touching_cases_keep_last_xy():
    m, dev, tp = _setup(rotation=0)
    _touch(dev, 20, 30)
    assert tp.read()[0]
    _touch(dev, 99, 99, n=0)
    r = tp.read()
    assert r[0] is False and (r[1], r[2]) == (20, 30)
    _touch(dev, 99, 99, n=0x0F)          # invalid count
    assert tp.read()[0] is False
    _touch(dev, 99, 99, n=1, ev=1)       # lift-up event
    assert tp.read()[0] is False
    _touch(dev, 40, 41, n=2, ev=0)       # two fingers: report point 1
    r = tp.read()
    assert r[0] is True and (r[1], r[2]) == (40, 41)


def test_bus_error_reads_as_release():
    m, dev, tp = _setup()
    _touch(dev, 5, 5)
    assert tp.read()[0]
    del m.i2c_devices[(1, 0x38)]
    r = tp.read()
    assert r[0] is False and tp.errors == 1


def test_int_pin_without_gate_still_polls():
    # Gating is unverified on hardware: int_pin alone must never drop touches.
    m, dev, _ = _setup()
    from hal.ft6336 import FT6336
    bus = _CountingI2C(m.I2C(1))
    tp = FT6336(i2c=bus, int_pin=38)
    pin = m.pins[38]
    pin._v = 1                                # INT high, but finger is down
    _touch(dev, 7, 8)
    assert tp.int_active() is False
    r = tp.read()
    assert r[0] is True and (r[1], r[2]) == (7, 8) and len(bus.reads) == 1
    _touch(dev, 7, 8, n=0)
    assert tp.read()[0] is False and len(bus.reads) == 2
    tp.read()
    assert len(bus.reads) == 3                # idle still polls the bus


def test_gate_without_int_pin_is_ignored():
    m, dev, tp = _setup(gate_int=True)
    _touch(dev, 3, 4)
    assert tp.read()[0] is True and tp.int_active() is None


def test_int_gate_skips_bus_when_idle():
    m, dev, _ = _setup()
    from hal.ft6336 import FT6336
    bus = _CountingI2C(m.I2C(1))
    tp = FT6336(i2c=bus, int_pin=38, gate_int=True)
    assert (0xA4, b"\x00") in dev.writes      # INT polling mode
    pin = m.pins[38]
    assert pin.mode == m.Pin.IN
    pin._v = 1                                # idle: INT high
    assert tp.int_active() is False
    assert tp.read()[0] is False and bus.reads == []
    pin._v = 0                                # touch: INT low
    _touch(dev, 7, 8)
    assert tp.int_active() is True
    assert tp.read()[0] is True and len(bus.reads) == 1
    pin._v = 1                                # INT released: one read to see lift
    _touch(dev, 7, 8, n=0)
    assert tp.read()[0] is False and len(bus.reads) == 2
    tp.read()
    assert len(bus.reads) == 2


def test_no_int_pin_and_monitor_mode():
    m, dev, tp = _setup()
    assert tp.int_active() is None
    tp.set_monitor(False)
    assert dev.writes[-1] == (0x86, b"\x00")
    tp.set_monitor(True)
    assert dev.writes[-1] == (0x86, b"\x01")
