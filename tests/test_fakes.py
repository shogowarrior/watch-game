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
