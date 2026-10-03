# BMA423 accelerometer driver for the LILYGO T-Watch 2020 V1 (stock MicroPython).
#
# Derived from antirez's pure-MicroPython BMA423 driver (formerly bma423.py in
# the repo root, removed in 44880ab: git show 44880ab^:bma423.py;
# https://github.com/antirez/bma423-pure-mp):
#   Copyright (C) 2024 Salvatore Sanfilippo -- All Rights Reserved
#   This code is released under the MIT license
#   https://opensource.org/license/mit/
# Changes in this version are under the same MIT license.
"""BMA423 driver: accel-only FIFO streaming, milli-g ints, polled interrupts.

Fixes relative to the original ``bma423.py``:

* data-interrupt mapping ORed the bit *index* into INT_MAP_DATA instead of
  ``1 << bit`` (so "data" on INT1 set fifo_wm+fifo_full, not drdy);
* ``irq()`` never read INT_STATUS_0/1 (clear-on-read; in latched mode the pin
  stayed high so only one IRQ ever fired), called undefined ``printf`` and
  passed an empty dict. Replaced by ``poll_events()`` from the main loop: no
  Python IRQ handler ever touches the shared I2C bus;
* temperature two's complement was wrong (0xFE gave 23+254); now raw-256;
* ``reset()`` slept 1 s. The datasheet gives only ts_up = 1 ms power-up time
  and "following a delay" for softreset, during which the interface NACKs or
  returns STATUS. We wait RESET_MS then poll CHIP_ID (bounded), optionally
  non-blocking via ``begin()`` + ``ready()``;
* the soft reset is only sent after CHIP_ID proved the device is a BMA423.

Registers checked against BST-BMA423-DS004 rev 2.0 (Aug 2019) and Bosch
BMA423 Sensor API v2.14.13 (BSD-3, bma4_defs.h / bma423.h). Feature engine
blob: Bosch v2.14.13 ``bma423_config_file[]`` (6144 bytes); get it with
``tools/fetch_bma423_config.sh``. The LilyGO 2017 blob has a different
FEATURES_IN layout and is rejected by the sha256 check.

Data format: 12-bit two's complement left-aligned in 16 bits (LSB first),
identical in DATA_8..13 and in headerless FIFO frames (6 bytes: x, y, z).
Axes are the sensor's own frame (no remap); the feature engine also runs with
Bosch's default (identity) axes remap, so the wrist-wear gesture assumes that
frame (unverified on the T-Watch: docs/hardware-setup.md §6).
"""

from array import array

# module-level names so tests can patch them
from finder.compat import const, sleep_ms, ticks_diff as _ticks_diff, ticks_ms as _ticks_ms

try:
    from time import sleep_us
except ImportError:  # CPython tests
    def sleep_us(us):
        import time
        time.sleep(us / 1000000)

CHIP_ID = const(0x13)
ADDRS = (0x19, 0x18)          # SDO high (T-Watch 2020) / low

# Registers
REG_CHIP_ID = const(0x00)
REG_ERR = const(0x02)
REG_DATA_8 = const(0x12)      # ACC_X LSB .. ACC_Z MSB (0x12..0x17)
REG_INT_STATUS_0 = const(0x1C)  # feature interrupts, clear-on-read
REG_INT_STATUS_1 = const(0x1D)  # data/FIFO interrupts, clear-on-read
REG_STEP_COUNTER_0 = const(0x1E)  # 4 bytes, little-endian
REG_TEMPERATURE = const(0x22)
REG_FIFO_LENGTH_0 = const(0x24)   # + 0x25 bits 5:0 = byte count
REG_FIFO_DATA = const(0x26)
REG_ACTIVITY_TYPE = const(0x27)
REG_INTERNAL_STATUS = const(0x2A)  # bits 4:0 message, 1 = init_ok
REG_ACC_CONF = const(0x40)
REG_ACC_RANGE = const(0x41)
REG_FIFO_CONFIG_0 = const(0x48)
REG_FIFO_CONFIG_1 = const(0x49)
REG_INT1_IO_CTRL = const(0x53)
REG_INT2_IO_CTRL = const(0x54)
REG_INT_LATCH = const(0x55)
REG_INT1_MAP = const(0x56)
REG_INT2_MAP = const(0x57)
REG_INT_MAP_DATA = const(0x58)
REG_INIT_CTRL = const(0x59)
REG_ASIC_LSB = const(0x5B)    # feature memory address (half-words), bits 3:0
REG_ASIC_MSB = const(0x5C)    # ... bits 11:4
REG_FEATURES_IN = const(0x5E)
REG_PWR_CONF = const(0x7C)
REG_PWR_CTRL = const(0x7D)
REG_CMD = const(0x7E)

CMD_SOFTRESET = const(0xB6)
CMD_FIFO_FLUSH = const(0xB0)

ACC_PERF = const(0x80)        # ACC_CONF.acc_perf_mode (continuous sampling)
ACC_BWP_NORMAL = const(0x20)  # acc_bwp = 2: normal filter (perf mode)
PWR_ACC_EN = const(0x04)
FIFO_ACC_EN = const(0x40)     # FIFO_CONFIG_1.fifo_acc_en; header bit 0x10 off
IO_LVL_HIGH = const(0x02)
IO_OUTPUT_EN = const(0x08)

ODR_CODES = {25: 6, 50: 7, 100: 8, 200: 9, 400: 10, 800: 11, 1600: 12}
RANGE_CODES = {2: 0, 4: 1, 8: 2, 16: 3}

FRAME_BYTES = const(6)
FIFO_FRAMES = const(170)      # 1024-byte FIFO // 6

# poll_events() bits: INT_STATUS_0 | INT_STATUS_1 << 8 (Bosch int_map layout)
EV_SINGLE_TAP = const(0x0001)
EV_STEP = const(0x0002)       # step detector / step counter watermark
EV_ACTIVITY = const(0x0004)
EV_WRIST_WEAR = const(0x0008)
EV_DOUBLE_TAP = const(0x0010)
EV_ANY_MOTION = const(0x0020)
EV_NO_MOTION = const(0x0040)
EV_ERROR = const(0x0080)
EV_FIFO_FULL = const(0x0100)
EV_FIFO_WM = const(0x0200)
EV_AUX_DRDY = const(0x2000)   # status only, cannot be mapped to a pin
EV_ACC_DRDY = const(0x8000)
EV_FEATURES = const(0x00FF)
EV_MAPPABLE = const(0x83FF)

# INT_MAP_DATA bits for INT1 (INT2 = << 4): ffull 0, fwm 1, drdy 2
MAP_FFULL = const(0x01)
MAP_FWM = const(0x02)
MAP_DRDY = const(0x04)

# FEATURES_IN (70 bytes) layout, config v2.14.13
FEATURES_IN_SIZE = const(70)
FEAT_STEP_CTRL = const(0x3B)  # STEP_CNTR_OFFSET 0x3A + 1
STEP_RESET = const(0x04)
STEP_DETECTOR_EN = const(0x08)
STEP_COUNTER_EN = const(0x10)
STEP_ACTIVITY_EN = const(0x20)
FEAT_SINGLE_TAP = const(0x3C)
FEAT_DOUBLE_TAP = const(0x3E)
FEAT_WRIST_WEAR = const(0x40)
FEAT_EN = const(0x01)

# ACTIVITY_TYPE values
ACT_STILL = const(0)
ACT_WALK = const(1)
ACT_RUN = const(2)
ACT_UNKNOWN = const(3)

# Feature engine state
FEAT_NONE = const(0)
FEAT_PENDING = const(1)
FEAT_OK = const(2)
FEAT_ERROR = const(-1)

CONFIG_PATH = "bma423conf.bin"
CONFIG_SIZE = const(6144)
# sha256 of bma423_config_file[] in Bosch BMA423 Sensor API v2.14.13 bma423.c
CONFIG_SHA256 = "112f81c8baba6d8abbf000c01e24fa56abd9f0c55e0415600d49109c189a2d3e"
CONFIG_CHUNK = const(64)      # even, <= FEATURES_IN_SIZE like the Bosch API

RESET_MS = const(2)           # >= ts_up (1 ms); then poll CHIP_ID
RESET_TIMEOUT_MS = const(100)
APS_WAIT_US = const(450)      # after clearing adv_power_save
INIT_MIN_MS = const(150)      # INTERNAL_STATUS settles in 140-150 ms
INIT_TIMEOUT_MS = const(400)

ST_OFF = const(0)
ST_RESET = const(1)
ST_READY = const(2)


def int_map_values(int1=0, int2=0):
    """Event masks for each pin -> (INT1_MAP, INT2_MAP, INT_MAP_DATA)."""
    for m in (int1, int2):
        if m & ~EV_MAPPABLE:
            raise ValueError("unmappable event bits 0x%04x" % (m & ~EV_MAPPABLE))
    return int1 & EV_FEATURES, int2 & EV_FEATURES, _data_bits(int1) | _data_bits(int2) << 4


def _data_bits(ev):
    m = 0
    if ev & EV_FIFO_FULL:
        m |= MAP_FFULL
    if ev & EV_FIFO_WM:
        m |= MAP_FWM
    if ev & EV_ACC_DRDY:
        m |= MAP_DRDY
    return m


def decode_frames(buf, n, out, range_mg=4000, off=0):
    """Decode ``n`` headerless 6-byte frames from ``buf`` into ``out`` as
    x, y, z milli-g (out[off], out[off+1], ...). Allocation-free.

    Stops early at an all-0x8000 frame (what the FIFO returns when read past
    its fill level). Returns the number of frames decoded.
    """
    cap = (len(out) - off) // 3
    if n > cap:
        n = cap
    j = off
    k = 0
    while k < n:
        i = k * 6
        x = buf[i] | buf[i + 1] << 8
        y = buf[i + 2] | buf[i + 3] << 8
        z = buf[i + 4] | buf[i + 5] << 8
        if x == 0x8000 and y == 0x8000 and z == 0x8000:
            break
        if x & 0x8000:
            x -= 0x10000
        if y & 0x8000:
            y -= 0x10000
        if z & 0x8000:
            z -= 0x10000
        # 12-bit count = v >> 4 (arithmetic); mg = count * range_mg / 2048, rounded
        out[j] = ((x >> 4) * range_mg + 1024) >> 11
        out[j + 1] = ((y >> 4) * range_mg + 1024) >> 11
        out[j + 2] = ((z >> 4) * range_mg + 1024) >> 11
        j += 3
        k += 1
    return k


def _acc_conf(odr):
    """ACC_CONF: performance mode, normal filter (-3 dB at about 0.4 x odr)."""
    return ACC_PERF | ACC_BWP_NORMAL | ODR_CODES[odr]


def temperature_c(raw):
    """TEMPERATURE register -> deg C (0x00 = 23 C, 1 K/LSB), None if 0x80."""
    if raw == 0x80:
        return None
    if raw & 0x80:
        raw -= 256
    return 23 + raw


class BMA423:
    """BMA423 on a shared I2C bus. Defaults: +-4 g, 100 Hz, perf mode,
    accel-only headerless FIFO in stream mode, advanced power save off.

    ``start=False`` skips init; then call ``init()`` (blocking, a few ms) or
    ``begin()`` and ``ready()`` from the main loop (non-blocking).
    ``z_sign`` is how the board mounts the chip (-1: face-up reads z = -1 g);
    samples stay in the chip's frame, and app/imu_feed.py applies it.
    """

    def __init__(self, i2c, addr=None, *, range_g=4, odr=100, fifo=True,
                 start=True, fifo_frames=FIFO_FRAMES, z_sign=1):
        if range_g not in RANGE_CODES:
            raise ValueError("range_g must be 2, 4, 8 or 16")
        if odr not in ODR_CODES:
            raise ValueError("odr must be one of 25..1600 Hz")
        self.i2c = i2c
        self.addr = self._probe() if addr is None else addr
        self.range_g = range_g
        self.range_mg = range_g * 1000
        self.odr = odr
        self.fifo = fifo
        self.z_sign = z_sign
        self.feat_state = FEAT_NONE
        self.feat_error = None
        self._feat_on = False         # game features switched on (poll_features)
        self._state = ST_OFF
        self._t0 = 0
        self._tf = 0
        self._b1 = bytearray(1)
        self._b2 = bytearray(2)
        self._b4 = bytearray(4)
        self._b6 = bytearray(6)
        self._faddr = bytearray(2)
        self._feat = bytearray(FEATURES_IN_SIZE)
        self.xyz = array("h", (0, 0, 0))
        self.fifo_buf = bytearray(fifo_frames * FRAME_BYTES)
        self.fifo_mg = array("h", [0] * (fifo_frames * 3))
        self._vbuf = None
        self._vmv = None
        self._views = None
        if start:
            self.init()

    # -- low level --------------------------------------------------------

    def _probe(self):
        found = self.i2c.scan()
        for a in ADDRS:
            if a in found:
                return a
        raise OSError(19, "BMA423 not found")

    def _r8(self, reg):
        self.i2c.readfrom_mem_into(self.addr, reg, self._b1)
        return self._b1[0]

    def _w8(self, reg, val):
        self._b1[0] = val
        self.i2c.writeto_mem(self.addr, reg, self._b1)

    def get_reg(self, reg, count=1):
        """Raw read (REPL/debug; allocates)."""
        d = self.i2c.readfrom_mem(self.addr, reg, count)
        return d[0] if count == 1 else d

    def set_reg(self, reg, val):
        """Raw write of an int or a buffer."""
        if isinstance(val, int):
            self._w8(reg, val)
        else:
            self.i2c.writeto_mem(self.addr, reg, val)

    # -- init -------------------------------------------------------------

    def chip_id(self):
        return self._r8(REG_CHIP_ID)

    def begin(self, reset=True):
        """Verify CHIP_ID and issue a soft reset; finish with ``ready()``.

        ``reset=False`` keeps FEAT_ERROR/FEAT_PENDING (INIT_CTRL is already
        spent until the next reset) and the interrupt mapping."""
        cid = self._r8(REG_CHIP_ID)
        if cid != CHIP_ID:
            raise OSError(19, "BMA423: chip id 0x%02x != 0x13" % cid)
        self._state = ST_RESET
        self._t0 = _ticks_ms()
        if not reset:
            if self.feat_state == FEAT_OK:
                self.feat_state = FEAT_NONE  # re-derived in _configure
            self._configure()
            return
        self.feat_state = FEAT_NONE
        self._feat_on = False         # the soft reset wipes FEATURES_IN and INT1_MAP
        try:
            self._w8(REG_CMD, CMD_SOFTRESET)
        except OSError:
            pass  # may NACK while it reboots

    def ready(self):
        """Advance a pending init; True once configured. Non-blocking."""
        if self._state == ST_READY:
            return True
        if self._state != ST_RESET:
            return False
        dt = _ticks_diff(_ticks_ms(), self._t0)
        if dt < RESET_MS:
            return False
        try:
            cid = self._r8(REG_CHIP_ID)
        except OSError:
            cid = -1  # still booting: interface not operational
        if cid != CHIP_ID:
            if dt > RESET_TIMEOUT_MS:
                self._state = ST_OFF
                raise OSError(110, "BMA423: no response after soft reset")
            return False
        self._configure()
        return True

    def init(self, reset=True):
        """Blocking init (~RESET_MS + a few register writes)."""
        self.begin(reset)
        while not self.ready():
            sleep_ms(1)
        return self

    def reset(self):
        """Soft reset and reconfigure (blocking, a few ms, not 1 s)."""
        return self.init(True)

    def _configure(self):
        self._w8(REG_PWR_CONF, 0x00)  # adv_power_save off (on after reset)
        sleep_us(APS_WAIT_US)
        self._w8(REG_ACC_CONF, _acc_conf(self.odr))
        self._w8(REG_ACC_RANGE, RANGE_CODES[self.range_g])
        self._w8(REG_FIFO_CONFIG_0, 0x00)  # stream mode, no sensortime frame
        self._w8(REG_FIFO_CONFIG_1, FIFO_ACC_EN if self.fifo else 0x00)
        self._w8(REG_PWR_CTRL, PWR_ACC_EN)
        self._state = ST_READY
        msg = self._r8(REG_INTERNAL_STATUS) & 0x1F
        if msg == 1:
            self._feat_done()  # engine survived (init without reset)
        elif msg > 1 and self.feat_state == FEAT_NONE:
            # init without reset after a failed engine init: INIT_CTRL spent
            self.feat_state = FEAT_ERROR
            self.feat_error = "internal_status %d (reset to retry)" % msg

    def set_odr(self, odr):
        """Change the output data rate while running and empty the FIFO (its
        samples were taken at the old rate)."""
        if odr not in ODR_CODES:
            raise ValueError("odr must be one of 25..1600 Hz")
        self._w8(REG_ACC_CONF, _acc_conf(odr))
        self.odr = odr
        self.fifo_flush()

    def error(self):
        """ERR_REG (0 = fine)."""
        return self._r8(REG_ERR)

    # -- samples ----------------------------------------------------------

    def read_xyz_mg(self, out=None):
        """Current x, y, z in milli-g into ``out`` (default ``self.xyz``)."""
        if out is None:
            out = self.xyz
        self.i2c.readfrom_mem_into(self.addr, REG_DATA_8, self._b6)
        decode_frames(self._b6, 1, out, self.range_mg)
        return out

    def get_xyz(self):
        """(x, y, z) in g as floats (REPL convenience; allocates)."""
        v = self.read_xyz_mg()
        return (v[0] / 1000, v[1] / 1000, v[2] / 1000)

    def fifo_level(self):
        """FIFO fill level in bytes."""
        self.i2c.readfrom_mem_into(self.addr, REG_FIFO_LENGTH_0, self._b2)
        return self._b2[0] | (self._b2[1] & 0x3F) << 8

    def fifo_flush(self):
        self._w8(REG_CMD, CMD_FIFO_FLUSH)

    def _view(self, buf, n):
        # memoryview slices allocate; cache one per frame count per buffer
        if buf is not self._vbuf:
            self._vbuf = buf
            self._vmv = memoryview(buf)
            self._views = [None] * (len(buf) // FRAME_BYTES + 1)
        v = self._views[n]
        if v is None:
            v = self._views[n] = self._vmv[:n * FRAME_BYTES]
        return v

    def fifo_read(self, buf=None):
        """Drain whole frames into ``buf`` (default ``self.fifo_buf``) with
        one burst read. Returns frames read (0 if empty)."""
        if buf is None:
            buf = self.fifo_buf
        n = self.fifo_level() // FRAME_BYTES
        cap = len(buf) // FRAME_BYTES
        if n > cap:
            n = cap
        if n:
            self.i2c.readfrom_mem_into(self.addr, REG_FIFO_DATA, self._view(buf, n))
        return n

    def fifo_read_mg(self, out=None):
        """fifo_read + decode into ``out`` (default ``self.fifo_mg``,
        x, y, z interleaved). Returns samples."""
        if out is None:
            out = self.fifo_mg
        n = self.fifo_read()
        return decode_frames(self.fifo_buf, n, out, self.range_mg) if n else 0

    def get_temperature(self):
        """Die temperature in deg C (int) or None if invalid."""
        return temperature_c(self._r8(REG_TEMPERATURE))

    # -- interrupts (polled) ----------------------------------------------

    def map_interrupts(self, int1=0, int2=0, latched=True):
        """Route EV_* masks to the INT1/INT2 pins (T-Watch: INT1 -> GPIO39),
        active high, push-pull.

        Feature events only work latched. Pins with no events get their
        output disabled. Stale status is cleared.
        """
        m1, m2, md = int_map_values(int1, int2)
        if not latched and (m1 | m2):
            raise ValueError("feature interrupts need latched mode")
        io = IO_OUTPUT_EN | IO_LVL_HIGH
        self._w8(REG_INT_LATCH, 1 if latched else 0)
        self._w8(REG_INT1_IO_CTRL, io if int1 else 0)
        self._w8(REG_INT2_IO_CTRL, io if int2 else 0)
        self._w8(REG_INT1_MAP, m1)
        self._w8(REG_INT2_MAP, m2)
        self._w8(REG_INT_MAP_DATA, md)
        self.poll_events()

    def poll_events(self):
        """EV_* bits since the last poll: reads (and thereby clears)
        INT_STATUS_0/1. Call from the main loop."""
        self.i2c.readfrom_mem_into(self.addr, REG_INT_STATUS_0, self._b2)
        return self._b2[0] | self._b2[1] << 8

    # -- feature engine (optional) ----------------------------------------

    def features_ok(self):
        return self.feat_state == FEAT_OK

    def load_config(self, path=CONFIG_PATH, wait=True, expect_sha256=CONFIG_SHA256):
        """Upload the Bosch feature blob and start the engine.

        Starts the engine only; no feature (step counter, activity,
        wrist-wear) is enabled. ``poll_features()`` or
        ``enable_step_counter()``/``enable_feature()`` does that.
        Returns False if the file is missing, has the wrong size/sha256, or
        init fails (``feat_error`` says why); the game then uses software
        step detection. With ``wait=False`` returns True once uploaded and
        ``features_ready()`` must be polled (engine needs ~150 ms).
        INIT_CTRL=1 is written at most once per reset.
        """
        if self.feat_state in (FEAT_OK, FEAT_PENDING):
            return True
        if self._state != ST_READY:
            self.feat_error = "not initialised"
            return False
        if (self._r8(REG_INTERNAL_STATUS) & 0x1F) == 1:
            self._feat_done()
            return True
        if self.feat_state == FEAT_ERROR:
            return False  # INIT_CTRL already used since reset
        try:
            f = open(path, "rb")
        except OSError:
            self.feat_error = "missing"
            return False
        try:
            why = _check_blob(f, expect_sha256)
            if why:
                self.feat_error = why
                return False
            f.seek(0)
            self._w8(REG_PWR_CONF, 0x00)
            sleep_us(APS_WAIT_US)
            self._w8(REG_INIT_CTRL, 0x00)
            buf = bytearray(CONFIG_CHUNK)
            mv = memoryview(buf)
            idx = 0
            while True:
                k = f.readinto(buf)
                if not k:
                    break
                self._set_asic(idx)
                self.i2c.writeto_mem(self.addr, REG_FEATURES_IN, mv[:k])
                idx += k
        finally:
            f.close()
        self._w8(REG_INIT_CTRL, 0x01)
        self.feat_state = FEAT_PENDING
        self._tf = _ticks_ms()
        if not wait:
            return True
        while self.feat_state == FEAT_PENDING:
            if not self.features_ready():
                sleep_ms(10)
        return self.feat_state == FEAT_OK

    def start_features(self, **kw):
        """Game-loop start of the feature engine: ``load_config(wait=False,
        **kw)``, then ``poll_features`` until it stops returning FEAT_PENDING.
        False when there is no usable blob (software step detection only).
        An OSError during the upload leaves INIT_CTRL unset, so calling it
        again is safe."""
        self._feat_on = False
        return self.load_config(wait=False, **kw)

    def poll_features(self):
        """FEAT_PENDING while the engine starts; FEAT_OK once it runs (the
        first OK switches on the step counter + activity and maps the latched
        wrist-wear event to INT1); FEAT_ERROR or FEAT_NONE if it failed."""
        if not self.features_ready():
            return self.feat_state
        if not self._feat_on:
            self.enable_step_counter()
            self.enable_feature(FEAT_WRIST_WEAR)
            self.map_interrupts(int1=EV_WRIST_WEAR, latched=True)
            self._feat_on = True    # only once all three went through: a bus error retries next poll
        return FEAT_OK

    def features_ready(self):
        """Poll a pending engine init; True once INTERNAL_STATUS = init_ok."""
        if self.feat_state != FEAT_PENDING:
            return self.feat_state == FEAT_OK
        dt = _ticks_diff(_ticks_ms(), self._tf)
        msg = self._r8(REG_INTERNAL_STATUS) & 0x1F
        if msg == 1:
            self._feat_done()
            return True
        if (msg > 1 and dt >= INIT_MIN_MS) or dt > INIT_TIMEOUT_MS:
            self.feat_state = FEAT_ERROR
            self.feat_error = "internal_status %d" % msg
        return False

    def _set_asic(self, idx):
        self._w8(REG_ASIC_LSB, (idx >> 1) & 0x0F)
        self._w8(REG_ASIC_MSB, (idx >> 1) >> 4)

    def _feat_done(self):
        self._faddr[0] = self._r8(REG_ASIC_LSB) & 0x0F
        self._faddr[1] = self._r8(REG_ASIC_MSB)
        self.feat_state = FEAT_OK
        self.feat_error = None

    def read_features_in(self):
        """FEATURES_IN -> ``self._feat`` (70 bytes)."""
        self._w8(REG_ASIC_LSB, self._faddr[0])
        self._w8(REG_ASIC_MSB, self._faddr[1])
        self.i2c.readfrom_mem_into(self.addr, REG_FEATURES_IN, self._feat)
        return self._feat

    def write_features_in(self):
        self._w8(REG_ASIC_LSB, self._faddr[0])
        self._w8(REG_ASIC_MSB, self._faddr[1])
        self.i2c.writeto_mem(self.addr, REG_FEATURES_IN, self._feat)

    def _feat_update(self, offset, clear, setbits):
        if self.feat_state != FEAT_OK:
            return False
        f = self.read_features_in()
        f[offset] = (f[offset] & ~clear & 0xFF) | setbits
        self.write_features_in()
        return True

    def enable_step_counter(self, on=True, activity=True, detector=False):
        """Chip step counter (+ activity recognition, + per-step EV_STEP
        detector). False if the feature engine is not running."""
        s = (STEP_COUNTER_EN if on else 0) | (STEP_ACTIVITY_EN if activity else 0)
        if detector:
            s |= STEP_DETECTOR_EN
        return self._feat_update(FEAT_STEP_CTRL,
                                 STEP_COUNTER_EN | STEP_ACTIVITY_EN | STEP_DETECTOR_EN | STEP_RESET, s)

    def reset_step_counter(self):
        return self._feat_update(FEAT_STEP_CTRL, 0, STEP_RESET)

    def enable_feature(self, offset, on=True):
        """Enable bit (0x01) of FEAT_SINGLE_TAP / FEAT_DOUBLE_TAP / FEAT_WRIST_WEAR."""
        return self._feat_update(offset, FEAT_EN, FEAT_EN if on else 0)

    def steps(self):
        """On-chip step count (0 until the step counter is enabled: ``poll_features``)."""
        self.i2c.readfrom_mem_into(self.addr, REG_STEP_COUNTER_0, self._b4)
        b = self._b4
        return b[0] | b[1] << 8 | b[2] << 16 | b[3] << 24

    def activity(self):
        """ACT_STILL / ACT_WALK / ACT_RUN / ACT_UNKNOWN."""
        return self._r8(REG_ACTIVITY_TYPE) & 0x03


def _check_blob(f, expect_sha256):
    """None if ``f`` is the expected config blob, else a short reason."""
    h = None
    if expect_sha256:
        import hashlib
        h = hashlib.sha256()
    buf = bytearray(CONFIG_CHUNK)
    size = 0
    while True:
        k = f.readinto(buf)
        if not k:
            break
        size += k
        if h is not None:
            h.update(buf if k == CONFIG_CHUNK else buf[:k])
    if size != CONFIG_SIZE:
        return "size %d" % size
    if h is not None:
        import binascii
        if binascii.hexlify(h.digest()).decode() != expect_sha256:
            return "sha256"
    return None
