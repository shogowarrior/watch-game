from tests import fakes
from tests.fakes import machine as _fm


class _Dev(_fm.FakeI2CDevice):
    """BMA423-ish fake: clear-on-read INT_STATUS, soft reset, boot NACKs,
    INIT_CTRL -> INTERNAL_STATUS, and a separate FEATURES_IN memory."""

    def __init__(self, addr=0x19, regs=None):
        r = {0x00: 0x13, 0x40: 0xA8, 0x49: 0x10, 0x59: 0x90, 0x7C: 0x03}
        if regs:
            for k in regs:
                r[k] = regs[k]
        _fm.FakeI2CDevice.__init__(self, addr, r)
        self.boot_nacks = 0       # reads that NACK after a soft reset
        self.nacks_left = 0
        self.init_status = 1      # INTERNAL_STATUS after INIT_CTRL=1
        self.feat = bytearray(70)
        self.chunks = []          # (asic half-word address, bytes) per 0x5E write

    def read(self, reg, n):
        if self.nacks_left:
            self.nacks_left -= 1
            raise OSError(19)
        if reg == 0x5E:
            return bytes(self.feat[:n])
        out = _fm.FakeI2CDevice.read(self, reg, n)
        for i in range(n):
            if reg + i in (0x1C, 0x1D):
                self.regs[reg + i] = 0
        return out

    def write(self, reg, data):
        if reg == 0x5E:
            self.writes.append((reg, bytes(data)))
            self.chunks.append((self.regs[0x5B] | self.regs[0x5C] << 4, bytes(data)))
            if len(data) == 70:
                for i in range(70):
                    self.feat[i] = data[i]
            return
        _fm.FakeI2CDevice.write(self, reg, data)
        if reg == 0x7E and data[0] == 0xB6:
            self.regs[0x2A] = 0
            self.feat[:] = bytes(70)          # a soft reset wipes FEATURES_IN
            for r in (0x55, 0x56, 0x57, 0x58):
                self.regs[r] = 0              # and the interrupt map
            self.regs[0x7C] = 0x03
            self.nacks_left = self.boot_nacks
        if reg == 0x59 and data[0] == 1:
            self.regs[0x2A] = self.init_status


_clock = [0]


def _mod():
    import hal.bma423 as b

    def sleep_ms(ms):
        _clock[0] += ms

    def sleep_us(us):
        pass

    b.sleep_ms = sleep_ms
    b.sleep_us = sleep_us
    b._ticks_ms = lambda: _clock[0]
    b._ticks_diff = lambda a, c: a - c
    return b


def _imu(regs=None, addr=0x19, **kw):
    m = fakes.install()
    dev = _Dev(addr, regs)
    m.i2c_devices[(0, addr)] = dev
    b = _mod()
    imu = b.BMA423(m.I2C(0, scl=m.Pin(22), sda=m.Pin(21)), **kw)
    return m, dev, b, imu


def _w(dev, reg):
    return [d[0] for (r, d) in dev.writes if r == reg]


def test_chip_id_checked_before_reset():
    m = fakes.install()
    dev = _Dev(0x19, {0x00: 0x12})
    m.i2c_devices[(0, 0x19)] = dev
    b = _mod()
    try:
        b.BMA423(m.I2C(0))
    except OSError:
        assert _w(dev, 0x7E) == []  # never soft-reset a foreign chip
        return
    assert False, "wrong chip id accepted"


def test_probe_alt_address_and_missing():
    m, dev, b, imu = _imu(addr=0x18)
    assert imu.addr == 0x18
    fakes.install()
    try:
        b.BMA423(m.I2C(0))
    except OSError:
        return
    assert False, "missing device not reported"


def test_init_writes():
    m, dev, b, imu = _imu()
    regs = [r for (r, d) in dev.writes]
    assert regs == [0x7E, 0x7C, 0x40, 0x41, 0x48, 0x49, 0x7D]
    assert _w(dev, 0x7E) == [0xB6]
    assert dev.regs[0x7C] == 0x00         # adv power save off
    assert dev.regs[0x40] == 0xA8         # perf mode, normal bwp, 100 Hz
    assert dev.regs[0x41] == 0x01         # +-4 g
    assert dev.regs[0x48] == 0x00         # stream mode, no sensortime
    assert dev.regs[0x49] == 0x40         # accel only, headerless
    assert dev.regs[0x7D] == 0x04         # acc_en
    assert imu.ready() and imu.range_mg == 4000


def test_init_options():
    m, dev, b, imu = _imu(range_g=8, odr=200, fifo=False)
    assert dev.regs[0x40] == 0xA9 and dev.regs[0x41] == 0x02 and dev.regs[0x49] == 0x00
    for kw in ({"range_g": 3}, {"odr": 99}):
        try:
            _imu(**kw)
        except ValueError:
            continue
        assert False, kw


def test_nonblocking_reset_polls_chip_id():
    m, dev, b, imu = _imu(start=False)
    dev.boot_nacks = 2
    _clock[0] = 1000
    imu.begin()
    assert dev.writes == [(0x7E, b"\xb6")]
    assert not imu.ready()                 # < RESET_MS
    _clock[0] += 2
    assert not imu.ready()                 # NACK while booting
    assert not imu.ready()
    assert imu.ready()                     # chip id back -> configured
    assert dev.regs[0x49] == 0x40 and dev.regs[0x7D] == 0x04
    assert _clock[0] == 1002               # no sleeping in the non-blocking path


def test_reset_timeout_raises():
    m, dev, b, imu = _imu(start=False)
    dev.boot_nacks = 1000
    _clock[0] = 0
    imu.begin()
    _clock[0] = 500
    try:
        imu.ready()
    except OSError:
        return
    assert False, "timeout not reported"


def _enc(count):
    return (count << 4) & 0xFFFF


def _frame(x, y, z, junk=0):
    out = bytearray()
    for c in (x, y, z):
        v = _enc(c) | junk
        out.append(v & 0xFF)
        out.append(v >> 8)
    return out


def test_fifo_decode_known_bytes():
    b = _mod()
    from array import array
    # +1 g, -1 g, +2.5 g at +-4 g (512 LSB/g); low nibble ignored
    buf = _frame(512, -512, 1280, junk=0x5)
    buf += bytes((0xF0, 0xFF, 0xF0, 0x7F, 0x00, 0x80))  # -1, +2047, -2048 counts
    buf += bytes((0x00, 0x80) * 3)                       # FIFO over-read marker
    out = array("h", [0] * 12)
    n = b.decode_frames(buf, 3, out, 4000)
    assert n == 2
    assert list(out[:6]) == [1000, -1000, 2500, -2, 3998, -4000]
    assert list(out[6:]) == [0] * 6
    # +-2 g scale and offset into the output
    n = b.decode_frames(_frame(1024, -1, 0), 1, out, 2000, 3)
    assert n == 1 and list(out[3:6]) == [1000, -1, 0]
    # output capacity caps the count
    assert b.decode_frames(buf, 3, array("h", [0] * 3), 4000) == 1


def test_fifo_read_drains_whole_frames():
    m, dev, b, imu = _imu()
    data = _frame(512, 0, -512) + _frame(0, 256, 0) + _frame(1, 2, 3)
    dev.regs[0x24] = 20          # 3 frames + 2 stray bytes
    dev.regs[0x25] = 0xC0        # reserved bits must be masked
    dev.fifo[0x26] = bytearray(data)
    buf = bytearray(60)
    assert imu.fifo_read(buf) == 3
    assert bytes(buf[:18]) == bytes(data)
    v = imu._views[3]
    dev.fifo[0x26] = bytearray(data)
    imu.fifo_read(buf)
    assert imu._views[3] is v    # view reused: no allocation per call
    dev.regs[0x24] = 0
    dev.fifo[0x26] = bytearray(b"\x11" * 6)
    assert imu.fifo_read(buf) == 0 and len(dev.fifo[0x26]) == 6
    # capped by the buffer
    dev.regs[0x24] = 18
    assert imu.fifo_read(bytearray(12)) == 2


def test_fifo_read_mg():
    m, dev, b, imu = _imu()
    dev.regs[0x24] = 12
    dev.fifo[0x26] = bytearray(_frame(512, 0, -512) + _frame(0, 1536, 0))
    assert imu.fifo_read_mg() == 2
    assert list(imu.fifo_mg[:6]) == [1000, 0, -1000, 0, 3000, 0]


def test_read_xyz_mg_and_level():
    m, dev, b, imu = _imu()
    f = _frame(-256, 512, 1024)
    for i in range(6):
        dev.regs[0x12 + i] = f[i]
    v = imu.read_xyz_mg()
    assert v is imu.xyz and list(v) == [-500, 1000, 2000]
    dev.regs[0x24] = 0x34
    dev.regs[0x25] = 0x02
    assert imu.fifo_level() == 0x234


def test_interrupt_map_bits():
    b = _mod()
    # regression: data-ready on INT1 is bit2 (0x04), not the bit index ORed in
    assert b.int_map_values(b.EV_ACC_DRDY, 0) == (0, 0, 0x04)
    assert b.int_map_values(b.EV_FIFO_FULL | b.EV_FIFO_WM, 0) == (0, 0, 0x03)
    assert b.int_map_values(0, b.EV_FIFO_FULL | b.EV_FIFO_WM | b.EV_ACC_DRDY) == (0, 0, 0x70)
    assert b.int_map_values(b.EV_STEP | b.EV_ACTIVITY, b.EV_DOUBLE_TAP) == (0x06, 0x10, 0)
    assert (b.EV_SINGLE_TAP, b.EV_STEP, b.EV_ACTIVITY, b.EV_WRIST_WEAR, b.EV_DOUBLE_TAP,
            b.EV_ANY_MOTION, b.EV_NO_MOTION, b.EV_ERROR) == (1, 2, 4, 8, 16, 32, 64, 128)
    try:
        b.int_map_values(b.EV_AUX_DRDY, 0)
    except ValueError:
        pass
    else:
        assert False, "aux drdy has no map bit"


def test_map_interrupts_writes():
    m, dev, b, imu = _imu()
    del dev.writes[:]
    imu.map_interrupts(int1=b.EV_STEP | b.EV_ACTIVITY | b.EV_FIFO_WM)
    assert dev.regs[0x55] == 1            # latched
    assert dev.regs[0x53] == 0x0A         # output_en | active high, push-pull
    assert dev.regs[0x54] == 0x00
    assert dev.regs[0x56] == 0x06 and dev.regs[0x57] == 0 and dev.regs[0x58] == 0x02
    try:
        imu.map_interrupts(int1=b.EV_STEP, latched=False)
    except ValueError:
        return
    assert False, "feature irq accepted in non-latched mode"


def test_poll_events_reads_and_clears():
    m, dev, b, imu = _imu()
    dev.regs[0x1C] = 0x02
    dev.regs[0x1D] = 0x80
    assert imu.poll_events() == b.EV_STEP | b.EV_ACC_DRDY
    assert imu.poll_events() == 0


def test_temperature():
    m, dev, b, imu = _imu()
    for raw, want in ((0xFE, 21), (0x00, 23), (0x05, 28), (0x81, -104), (0x80, None)):
        dev.regs[0x22] = raw
        assert imu.get_temperature() == want, (raw, imu.get_temperature())


def test_steps_and_activity():
    m, dev, b, imu = _imu({0x1E: 0x39, 0x1F: 0x05, 0x20: 0x01, 0x27: 0x01})
    assert imu.steps() == 0x10539
    assert imu.activity() == b.ACT_WALK
    assert not imu.enable_step_counter()  # no feature engine loaded


def _tmp(name):
    try:
        import tempfile
        import os
        return os.path.join(tempfile.gettempdir(), name)
    except ImportError:
        return name  # MicroPython: cwd is an in-memory FS


def _blob(n=6144):
    return bytes((i * 7 + 3) & 0xFF for i in range(n))


def _write(path, data):
    f = open(path, "wb")
    f.write(data)
    f.close()


def _rm(path):
    try:
        import os
        os.remove(path)
    except (ImportError, OSError):
        pass


def _sha(data):
    import hashlib
    import binascii
    return binascii.hexlify(hashlib.sha256(data).digest()).decode()


def test_load_config_missing_file():
    m, dev, b, imu = _imu()
    assert imu.load_config(_tmp("no_such_bma423conf.bin")) is False
    assert imu.feat_error == "missing" and _w(dev, 0x59) == []


def test_load_config_rejects_wrong_blob():
    m, dev, b, imu = _imu()
    p = _tmp("t_bma423_short.bin")
    _write(p, _blob(6000))
    try:
        assert imu.load_config(p, expect_sha256=None) is False
        assert imu.feat_error.startswith("size")
        blob = _blob()
        _write(p, blob)
        assert imu.load_config(p) is False            # not the Bosch v2.14.13 hash
        assert imu.feat_error == "sha256"
        assert _w(dev, 0x59) == [] and dev.chunks == []
    finally:
        _rm(p)


def test_load_config_upload_and_step_counter():
    m, dev, b, imu = _imu()
    p = _tmp("t_bma423_ok.bin")
    blob = _blob()
    _write(p, blob)
    try:
        del dev.writes[:]
        assert imu.load_config(p, expect_sha256=_sha(blob)) is True
    finally:
        _rm(p)
    assert imu.features_ok()
    regs = [r for (r, d) in dev.writes]
    assert regs[:2] == [0x7C, 0x59] and dev.regs[0x7C] == 0
    assert _w(dev, 0x59) == [0x00, 0x01]              # INIT_CTRL=1 exactly once
    assert b"".join(c for (a, c) in dev.chunks) == blob
    assert [a for (a, c) in dev.chunks] == [i // 2 for i in range(0, 6144, 64)]
    assert dev.regs[0x5B] == (6080 // 2) & 0x0F and dev.regs[0x5C] == (6080 // 2) >> 4
    # second call is a no-op (never INIT_CTRL twice)
    assert imu.load_config(p) is True and _w(dev, 0x59) == [0x00, 0x01]

    dev.feat[0x3B] = 0x01                             # watermark msb bit kept
    assert imu.enable_step_counter(activity=True, detector=True)
    assert dev.feat[0x3B] == 0x01 | 0x10 | 0x20 | 0x08
    assert imu.enable_step_counter(on=True, activity=False)
    assert dev.feat[0x3B] == 0x01 | 0x10
    assert imu.reset_step_counter() and dev.feat[0x3B] == 0x01 | 0x10 | 0x04
    assert imu.enable_feature(b.FEAT_DOUBLE_TAP) and dev.feat[0x3E] & 1


def test_start_and_poll_features():
    m, dev, b, imu = _imu()
    p = _tmp("t_bma423_sp.bin")
    _write(p, _blob())
    try:
        dev.init_status = 0
        _clock[0] = 0
        assert imu.start_features(path=p, expect_sha256=None) is True
        assert imu.poll_features() == b.FEAT_PENDING and dev.feat[0x3B] == 0
        dev.regs[0x2A] = 1                            # engine up
        assert imu.poll_features() == b.FEAT_OK
        assert dev.feat[0x3B] & 0x30 == 0x30          # step counter + activity
        assert dev.feat[0x40] & 0x01                  # wrist wear
        assert dev.regs[0x56] & 0x08 and dev.regs[0x55] == 1   # INT1, latched
        n = len(dev.writes)
        assert imu.poll_features() == b.FEAT_OK and len(dev.writes) == n   # once
        dev.init_status = 1
        imu.reset()                                   # REPL recovery: features again
        assert imu.load_config(p, expect_sha256=None) and imu.poll_features() == b.FEAT_OK
        assert dev.feat[0x3B] & 0x30 == 0x30 and dev.regs[0x56] & 0x08
    finally:
        _rm(p)
    m, dev, b, imu = _imu()
    assert imu.start_features(path=_tmp("no_such_bma423conf.bin")) is False
    assert imu.poll_features() == b.FEAT_NONE


def test_features_retry_after_bus_error():
    # A NACK while uploading the blob or switching the features on must not
    # leave them off for good: both calls may simply be made again.
    m, dev, b, imu = _imu()
    p = _tmp("t_bma423_retry.bin")
    _write(p, _blob())
    real = dev.write
    fail = [True]

    def write(reg, data):
        if reg == 0x5E and fail[0]:
            fail[0] = False
            raise OSError(116)
        return real(reg, data)

    dev.write = write
    try:
        try:
            imu.start_features(path=p, expect_sha256=None)
            assert False, "NACK swallowed"
        except OSError:
            pass
        assert imu.start_features(path=p, expect_sha256=None) is True
    finally:
        _rm(p)
    assert _w(dev, 0x59) == [0x00, 0x00, 0x01]        # INIT_CTRL=1 once, after a whole upload
    dev.regs[0x2A] = 1                                # engine up
    fail[0] = True
    try:
        imu.poll_features()
        assert False, "NACK swallowed"
    except OSError:
        pass
    assert imu.poll_features() == b.FEAT_OK
    assert dev.feat[0x3B] & 0x30 == 0x30              # step counter + activity
    assert dev.feat[0x40] & 0x01                      # wrist wear
    assert dev.regs[0x56] & 0x08                      # on INT1


def test_load_config_nonblocking_and_error():
    m, dev, b, imu = _imu()
    p = _tmp("t_bma423_nb.bin")
    blob = _blob()
    _write(p, blob)
    try:
        dev.init_status = 0
        _clock[0] = 0
        assert imu.load_config(p, wait=False, expect_sha256=None) is True
        assert imu.feat_state == b.FEAT_PENDING and not imu.features_ready()
        dev.regs[0x2A] = 1
        assert imu.features_ready() and imu.features_ok()

        m, dev, b, imu = _imu()
        dev.init_status = 2                             # init_err
        _clock[0] = 0
        assert imu.load_config(p, expect_sha256=None) is False
        assert imu.feat_state == b.FEAT_ERROR
        assert imu.load_config(p, expect_sha256=None) is False
        assert _w(dev, 0x59) == [0x00, 0x01]            # no second INIT_CTRL
        # init without reset must not re-arm INIT_CTRL
        imu.init(reset=False)
        assert imu.feat_state == b.FEAT_ERROR
        assert imu.load_config(p, expect_sha256=None) is False
        assert _w(dev, 0x59) == [0x00, 0x01]
        # nor may a fresh object on a chip whose engine already failed
        imu2 = b.BMA423(imu.i2c, start=False)
        imu2.init(reset=False)
        assert imu2.feat_state == b.FEAT_ERROR
        assert imu2.load_config(p, expect_sha256=None) is False
        assert _w(dev, 0x59) == [0x00, 0x01]
        # a soft reset re-allows exactly one INIT_CTRL
        dev.init_status = 1
        imu.reset()
        assert imu.feat_state == b.FEAT_NONE
        assert imu.load_config(p, expect_sha256=None) is True
        assert _w(dev, 0x59) == [0x00, 0x01, 0x00, 0x01]
        # init without reset keeps a working engine
        imu.init(reset=False)
        assert imu.features_ok()
    finally:
        _rm(p)
