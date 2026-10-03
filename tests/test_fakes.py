from tests import fakes


def test_fake_i2c_roundtrip():
    m = fakes.install()
    dev = m.add_i2c_device(0, 0x35, {0x12: 0x5F})
    bus = m.I2C(0, scl=m.Pin(22), sda=m.Pin(21))
    assert bus.scan() == [0x35]
    assert bus.readfrom_mem(0x35, 0x12, 1)[0] == 0x5F
    bus.writeto_mem(0x35, 0x12, bytes([0x40]))
    assert dev.writes == [(0x12, b"\x40")]


def test_fake_espnow_air():
    fakes.install()
    import espnow
    a = espnow.ESPNow(); b = espnow.ESPNow()
    a.active(True); b.active(True)
    a.send(b"\xff" * 6, b"hi", False)
    data = [None, bytearray(250), 0, 0]
    assert b.recvinto(data, 0) == 2 and bytes(data[1][:2]) == b"hi" and data[2] == -50


def test_fake_wlan_is_a_new_device_and_espnow_sends_from_it():
    fakes.install()
    import network
    import espnow
    a = network.WLAN(network.STA_IF)
    ea = espnow.ESPNow()
    b = network.WLAN(network.STA_IF)
    eb = espnow.ESPNow()
    assert a.config("mac") != b.config("mac")
    assert ea.mac == a.config("mac") and eb.mac == b.config("mac")
    fakes.install()                           # reset: numbering starts again
    assert network.WLAN().config("mac") == a.config("mac")


def test_fake_axp202_irq_status_write_1_to_clear():
    m = fakes.install()
    dev = m.add_axp202({0x4A: 0x03})
    bus = m.I2C(0)
    assert bus.readfrom_mem(0x35, 0x03, 1)[0] == 0x41
    bus.writeto_mem(0x35, 0x4A, b"\x02")
    assert dev.regs[0x4A] == 0x01 and dev.writes == [(0x4A, b"\x02")]
    bus.writeto_mem(0x35, 0x12, b"\x06")      # ordinary registers store the value
    assert dev.regs[0x12] == 0x06
