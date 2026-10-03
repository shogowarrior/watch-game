from tests import fakes


def _pmu(regs=None, irq_pin=None):
    m = fakes.install()
    r = {0x03: 0x41, 0x12: 0x02}
    if regs:
        for k in regs:
            r[k] = regs[k]
    dev = m.add_i2c_device(0, 0x35, r)
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
    p.set_output(0x5D, False)   # everything except DCDC3 (and the mask includes it)
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
    assert ev & a.EV_SHORT and ev & a.EV_EDGE and ev & a.EV_VBUS_IN
    assert not ev & a.EV_LONG
    assert (0x4A, b"\x02") in dev.writes
    assert (0x4C, b"\x40") in dev.writes
    assert (0x48, b"\x08") in dev.writes
    # zero status registers are not written
    assert not [w for w in dev.writes if w[0] in (0x49, 0x4B)]


def test_pek_press_release_edges():
    from hal import axp202 as a
    m, dev, p = _pmu({0x4C: 0x20})
    assert p.poll() == a.EV_EDGE | a.EV_PRESS
    m, dev, p = _pmu({0x4C: 0x40})
    assert p.poll() == a.EV_EDGE | a.EV_RELEASE
    m, dev, p = _pmu({0x4C: 0x60})
    assert p.poll() == a.EV_EDGE | a.EV_PRESS | a.EV_RELEASE
    assert a.EV_PEK & (a.EV_PRESS | a.EV_RELEASE) == 0
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
