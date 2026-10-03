from tests import fakes


def _pmu(regs=None, irq_pin=None):
    m = fakes.install()
    dev = m.add_axp202(regs)
    from hal.axp202 import AXP202
    bus = m.I2C(0, scl=m.Pin(22), sda=m.Pin(21))
    return m, dev, AXP202(bus, irq_pin=irq_pin)


def _power_writes(dev):
    return [d[0] for (reg, d) in dev.writes if reg == 0x12]


def test_chip_id_checked():
    m = fakes.install()
    m.add_i2c_device(0, 0x35, {0x03: 0x03})
    from hal.axp202 import AXP202
    try:
        AXP202(m.I2C(0))
    except OSError:
        return
    assert False, "wrong chip id accepted"


def test_ldo2_enable_sets_bit2_keeps_dcdc3():
    m, dev, p = _pmu({0x12: 0x02})
    p.set_ldo2(True)
    assert dev.regs[0x12] == 0x06
    assert p.ldo2_on()
    p.set_ldo2(False)
    assert dev.regs[0x12] == 0x02
    assert not p.ldo2_on()


def test_ldo3_toggle_preserves_other_outputs():
    m, dev, p = _pmu({0x12: 0x02 | 0x04 | 0x10})
    p.set_ldo3(True)
    assert dev.regs[0x12] == 0x56
    p.set_ldo3(False)
    assert dev.regs[0x12] == 0x16


def test_dcdc3_preserved_on_every_write():
    # Even if the register reads back with DCDC3 clear, writes force it on.
    m, dev, p = _pmu({0x12: 0x00})
    p.set_ldo2(True)
    p.set_ldo3(True)
    p.set_ldo2(False)
    p.set_ldo3(False)
    p.set_output(0x08, True)    # LDO4
    p.set_output(0x08, False)
    p.set_output(0x5D, False)   # everything except DCDC3 (0x5D has bit1 clear)
    p.write(0x12, 0x00)         # raw write is guarded too
    ws = _power_writes(dev)
    assert len(ws) >= 6
    for v in ws:
        assert v & 0x02, "DCDC3 cleared in write 0x%02x" % v


def test_refuses_dcdc3_off():
    m, dev, p = _pmu()
    try:
        p.set_output(0x02, False)
    except ValueError:
        assert dev.regs[0x12] & 0x02
        return
    assert False


def test_adcs_enabled_on_init():
    m, dev, p = _pmu({0x82: 0x01})
    assert dev.regs[0x82] & 0xC0 == 0xC0   # battery V and I
    assert dev.regs[0x82] & 0x01           # read-modify-write


def test_battery_readings():
    # 3.960 V -> raw 3600 = 0xE10 -> H8 0xE1, L4 0x0
    m, dev, p = _pmu({0x78: 0xE1, 0x79: 0x00,
                      0x7C: 0x0C, 0x7D: 0x08,   # (12<<5|8)=392 -> 196 mA
                      0x7A: 0x10, 0x7B: 0x04,   # (16<<4|4)=260 -> 130 mA
                      0xB9: 57, 0x00: 0x20, 0x01: 0x60})
    assert p.battery_voltage() == 3960
    assert p.discharge_current_ma() == 196
    assert p.charge_current_ma() == 130
    assert p.battery_percent() == 57
    assert p.vbus_present() and p.is_charging() and p.battery_connected()
    dev.regs[0x00] = 0
    dev.regs[0x01] = 0x20
    assert not p.vbus_present() and not p.is_charging()


def test_percent_falls_back_to_voltage():
    m, dev, p = _pmu({0xB9: 0x80, 0x78: 0xE1, 0x79: 0x00})  # 3960 mV
    pct = p.battery_percent()
    assert 70 <= pct <= 80, pct
    from hal.axp202 import percent_from_mv
    assert percent_from_mv(4300) == 100 and percent_from_mv(3000) == 0
    assert percent_from_mv(3850) == 55


def test_ldo2_voltage_keeps_ldo4_bits():
    m, dev, p = _pmu({0x28: 0x0A})
    p.set_ldo2_mv(3300)
    assert dev.regs[0x28] == 0xFA


def test_enable_pek_irqs():
    m, dev, p = _pmu({0x42: 0x80})
    p.enable_pek(edges=True)
    assert dev.regs[0x42] == 0x83
    assert dev.regs[0x44] & 0x60 == 0x60


def test_pek_irq_clear_writes_ones():
    m, dev, p = _pmu({0x4A: 0x02, 0x4C: 0x40, 0x48: 0x08})
    del dev.writes[:]
    ev = p.poll()
    from hal import axp202 as a
    assert ev & a.EV_SHORT and ev & a.EV_RELEASE and ev & a.EV_VBUS_IN
    assert not ev & a.EV_LONG
    assert (0x4A, b"\x02") in dev.writes
    assert (0x4C, b"\x40") in dev.writes
    assert (0x48, b"\x08") in dev.writes
    assert dev.regs[0x4A] == 0 and dev.regs[0x4C] == 0 and dev.regs[0x48] == 0
    # zero status registers are not written
    assert not [w for w in dev.writes if w[0] in (0x49, 0x4B)]
    assert p.poll() == 0


def test_poll_bus_error_keeps_press():
    # A NACK on a later status register must not lose a press already read.
    m, dev, p = _pmu({0x4A: 0x02})
    from hal import axp202 as a
    real = dev.read
    fail = [True]

    def read(reg, n):
        if reg == 0x4B and fail[0]:
            fail[0] = False
            raise OSError(116)
        return real(reg, n)

    dev.read = read
    try:
        p.poll()
        assert False, "NACK swallowed"
    except OSError:
        pass
    assert dev.regs[0x4A] == 0x02              # nothing cleared yet
    assert p.poll() == a.EV_SHORT and dev.regs[0x4A] == 0


def _nack_write_once(dev, reg):
    real = dev.write
    fail = [True]

    def write(r, data):
        if r == reg and fail[0]:
            fail[0] = False
            raise OSError(116)
        return real(r, data)

    dev.write = write


def test_poll_clear_nack_defers_not_drops():
    # A failed clear keeps that register's bits latched: reported on the next
    # poll, never dropped with the events already cleared elsewhere.
    from hal import axp202 as a
    m, dev, p = _pmu({0x4A: 0x02, 0x4C: 0x40})
    _nack_write_once(dev, 0x4C)
    assert p.poll() == a.EV_SHORT
    assert dev.regs[0x4A] == 0 and dev.regs[0x4C] == 0x40
    assert p.poll() == a.EV_RELEASE and dev.regs[0x4C] == 0
    m, dev, p = _pmu({0x4A: 0x02})
    _nack_write_once(dev, 0x4A)
    assert p.poll() == 0 and dev.regs[0x4A] == 0x02
    assert p.poll() == a.EV_SHORT and p.poll() == 0


def test_pek_press_release_edges():
    from hal import axp202 as a
    m, dev, p = _pmu({0x4C: 0x20})
    assert p.poll() == a.EV_PRESS
    m, dev, p = _pmu({0x4C: 0x40})
    assert p.poll() == a.EV_RELEASE
    m, dev, p = _pmu({0x4C: 0x60})
    assert p.poll() == a.EV_PRESS | a.EV_RELEASE
    assert (a.EV_PRESS | a.EV_RELEASE) & (a.EV_VBUS_IN | a.EV_VBUS_OUT) == 0


def test_long_press_event():
    m, dev, p = _pmu({0x4A: 0x01})
    from hal import axp202 as a
    assert p.poll() == a.EV_LONG


def test_clear_irqs_all_ones():
    m, dev, p = _pmu()
    del dev.writes[:]
    p.clear_irqs()
    assert dev.writes == [(0x48 + i, b"\xff") for i in range(5)]


def test_poll_skips_i2c_when_line_high():
    m, dev, p = _pmu({0x4A: 0x02})
    pin = m.Pin(35, m.Pin.IN, value=1)
    p.irq_pin = pin
    del dev.writes[:]
    assert p.poll() == 0 and dev.writes == []
    pin._v = 0
    assert p.poll() != 0


def test_shutdown_sets_off_bit_only():
    m, dev, p = _pmu({0x32: 0x46})
    del dev.writes[:]
    p.shutdown()
    assert dev.regs[0x32] == 0xC6
    assert dev.writes == [(0x32, b"\xc6")]    # 0x12 (DCDC3) never touched


def test_long_press_time_keeps_hold_bits():
    m, dev, p = _pmu({0x36: 0x0D})
    for ms, code in ((1000, 0x0D), (2000, 0x2D), (2500, 0x3D), (1500, 0x1D)):
        p.set_long_press_ms(ms)
        assert dev.regs[0x36] == code, (ms, dev.regs[0x36])
