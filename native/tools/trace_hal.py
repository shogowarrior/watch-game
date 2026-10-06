"""Record the hal/ drivers' bus traffic for the C++ drivers to replay (CPython only).

    python3 native/tools/trace_hal.py OUTDIR

Runs the drivers the native game loop ports (hal/axp202.py, st7789.py,
bma423.py and ft6336.py, made as hal/board.py makes them, and Board.init
itself) on the fakes of tests/fakes through scripted scenarios: every method
the game uses, bus errors (a NACK on the n-th transaction of a call), side-key
and USB events, the battery ladder, touch points, FIFO fills, rate changes,
sleep and wake. Writes one JSON-lines file per driver, OUTDIR/hal.<name>.jsonl:

    {"scenario": "poll", "t": 1073741424}          # a fresh watch; the clock at t ms
    {"op": "poll", "a": [], "r": 1, "s": {}, "io": [...]}
    {"op": "advance", "a": [30]}                    # the clock moves on (no call)

"a" holds the call's arguments, "r" its result ({"raise": "OSError"} if it
raised), "s" the driver's state afterwards (absent while there is no driver),
"io" what it did, in order:

    {"r": [bus, addr, reg], "d": "hex"}             # an I2C register read and the bytes it got
    {"r": [bus, addr, reg], "n": 5, "ok": false}    # ... NACKed (nothing read)
    {"w": [bus, addr, reg], "d": "hex"}             # a write ("ok": false: NACKed, nothing written)
    {"scan": [bus, [addr, ...]]}
    {"cmd": c, "d": "hex"}                          # a panel command and its parameters
    {"cmd": 44}                                     # RAMWR; its pixels follow as
    {"px": n, "crc": crc}                           # n bytes with this CRC-32 (one event per call)
    {"pwm": duty_u16}                               # the backlight
    {"line": 0}                                     # the AXP202 IRQ line read (0: low, pending)
    {"sleep": ms} {"sleep_us": us} {"wait": ms}     # the driver's sleeps; the wait it was given

The panel stream is decoded from CS, DC and the SPI bytes: a byte sent with
DC low is a command, the bytes after it with DC high its parameters, until
the next command or CS high. A strip that continues an open window (CS still
low) adds pixels to that window's RAMWR. Pixels in a push_strip call are a
pattern both sides compute from a seed (pattern()).

native/test/run.py runs this with tools/trace_game.py into HM_TRACES, and
native/test/test_hal_*.cpp replay it on the C++ drivers (hal_replay.h).
"""

import json
import os
import struct
import sys
import zlib

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

from tests import fakes  # noqa: E402

machine = fakes.install()

from hal import axp202, bma423, board, ft6336, pins, st7789  # noqa: E402

PERIOD = 1 << 30
RAMWR = 0x2C
W = None   # the World being recorded


def pattern(seed, n):
    """The pixel bytes of a pushed strip (native/test/hal_replay.cpp has the same)."""
    return bytes((i * 131 + seed * 7 + (i >> 9)) & 0xFF for i in range(n))


class World:
    """One scenario's watch: the fakes' buses, pins and clock log what the
    driver does into ``io`` during a call (``call``)."""

    def __init__(self, out, name, t0_ms=PERIOD - 1000):
        global W
        W = self
        machine.reset_fakes()
        self.out = out
        self.t_us = t0_ms * 1000
        self.io = None          # None between calls: setup, not recorded
        self.n = 0              # I2C transactions so far in this call
        self.nack = ()          # ... which of them (1-based) are NACKed
        self.obj = None         # the driver
        self.state = None       # obj -> its state dict
        self.cs = 1
        self.dc = 0
        self.cmd = None         # the open panel command: [code, parameter bytes]
        self.px = None          # the open RAMWR's pixels in this call: [count, crc]
        self._write({"scenario": name, "t": t0_ms})

    def _write(self, rec):
        self.out.write(json.dumps(rec, separators=(",", ":")) + "\n")

    # -- recording -----------------------------------------------------------
    def call(self, op, args, fn, nack=()):
        """Run fn() as the call ``op(args)``: its result, state and io."""
        self.io = []
        self.n = 0
        self.nack = nack
        try:
            r = fn()
            r = list(r) if isinstance(r, list) else r
        except (OSError, ValueError) as e:
            r = {"raise": type(e).__name__}
        self._flush_px()
        rec = {"op": op, "a": args, "r": r, "io": self.io}
        if self.obj is not None:
            rec["s"] = self.state(self.obj)
        self.last_io = self.io
        self.io = None
        self._write(rec)
        return r

    def advance(self, ms):
        self.t_us += ms * 1000
        self._write({"op": "advance", "a": [ms]})

    def log(self, ev):
        if self.io is not None:
            self.io.append(ev)

    def fails(self):
        """Count one I2C transaction; True if it is to be NACKed."""
        if self.io is None:
            return False
        self.n += 1
        return self.n in self.nack

    # -- clock ---------------------------------------------------------------
    def ticks_ms(self):
        return (self.t_us // 1000) & (PERIOD - 1)

    def sleep_ms(self, ms):
        self.log({"sleep": ms})
        self.t_us += ms * 1000

    def sleep_us(self, us):
        self.log({"sleep_us": us})
        self.t_us += us

    def wait(self, ms):
        self.log({"wait": ms})
        self.t_us += ms * 1000

    # -- panel ---------------------------------------------------------------
    def pin_write(self, pid, v):
        if pid == pins.TFT_CS:
            if v and not self.cs:
                self._close()
            self.cs = v
        elif pid == pins.TFT_DC:
            self.dc = v
        else:
            raise RuntimeError("trace_hal: unexpected write to pin %d" % pid)

    def spi_write(self, data):
        if self.cs:
            raise RuntimeError("trace_hal: SPI bytes with CS high")
        if self.dc:
            if self.cmd is None:
                raise RuntimeError("trace_hal: parameters before any command")
            if self.cmd[0] == RAMWR:
                self.px[0] += len(data)
                self.px[1] = zlib.crc32(data, self.px[1])
            else:
                self.cmd[1] += data
            return
        for c in data:
            self._close()
            self.cmd = [c, bytearray()]
            if c == RAMWR:
                self.log({"cmd": c})
                self.px = [0, 0]

    def _flush_px(self):
        if self.px is not None and self.px[0]:
            self.log({"px": self.px[0], "crc": self.px[1]})
            self.px = [0, 0]

    def _close(self):
        if self.cmd is None:
            return
        if self.cmd[0] == RAMWR:
            self._flush_px()
        else:
            self.log({"cmd": self.cmd[0], "d": bytes(self.cmd[1]).hex()})
        self.cmd = None
        self.px = None


# -- the fakes, logging into W ------------------------------------------------
_I2C, _Pin, _SPI, _PWM = machine.I2C, machine.Pin, machine.SPI, machine.PWM


class LogI2C(_I2C):
    def _do(self, ev, n, fn):
        """fn() as one transaction: NACKed (OSError) when the scenario says so
        or no chip answers at that address."""
        try:
            if W.fails():
                raise OSError(19)
            d = fn()
        except OSError:
            if n is not None:
                ev["n"] = n
            ev["ok"] = False
            W.log(ev)
            raise
        if d is not None:
            ev["d"] = bytes(d).hex()
        W.log(ev)
        return d

    def readfrom_mem_into(self, addr, reg, buf, addrsize=8):
        def read():
            _I2C.readfrom_mem_into(self, addr, reg, buf)
            return buf
        self._do({"r": [self.bus_id, addr, reg]}, len(buf), read)

    def readfrom_mem(self, addr, reg, n, addrsize=8):
        return self._do({"r": [self.bus_id, addr, reg]}, n, lambda: _I2C.readfrom_mem(self, addr, reg, n))

    def writeto_mem(self, addr, reg, data, addrsize=8):
        self._do({"w": [self.bus_id, addr, reg], "d": bytes(data).hex()}, None,
                 lambda: _I2C.writeto_mem(self, addr, reg, data))

    def scan(self):
        found = _I2C.scan(self)
        W.log({"scan": [self.bus_id, found]})
        return found

    def writeto(self, *a, **k):
        raise RuntimeError("trace_hal: I2C writeto is not modelled")

    def readfrom(self, *a, **k):
        raise RuntimeError("trace_hal: I2C readfrom is not modelled")


class LogPin(_Pin):
    def value(self, v=None):
        if v is None:
            if self.id != pins.AXP202_IRQ:
                raise RuntimeError("trace_hal: unexpected read of pin %d" % self.id)
            W.log({"line": self._v})
            return self._v
        _Pin.value(self, v)
        W.pin_write(self.id, self._v)


class LogSPI(_SPI):
    def write(self, buf):
        W.spi_write(bytes(buf))


class LogPWM(_PWM):
    def duty_u16(self, d=None):
        if d is not None:
            W.log({"pwm": d})
        return _PWM.duty_u16(self, d)


class FakeBMA423(machine.FakeI2CDevice):
    """BMA423 registers: CHIP_ID reads 0 for ``boot`` reads after a soft
    reset, a FIFO flush empties the FIFO, FIFO_DATA streams (``fill``)."""

    def __init__(self, boot=0):
        machine.FakeI2CDevice.__init__(self, 0x19, {0x00: 0x13})
        self.boot_reads = boot
        self.boot = 0

    def read(self, reg, n):
        if reg == 0x00 and self.boot:
            self.boot -= 1
            return bytes(n)
        return machine.FakeI2CDevice.read(self, reg, n)

    def write(self, reg, data):
        machine.FakeI2CDevice.write(self, reg, data)
        if reg == 0x7E and data[0] == 0xB6:
            self.boot = self.boot_reads
        elif reg == 0x7E and data[0] == 0xB0:
            self.fill([])

    def fill(self, frames, level=None):
        """FIFO frames (x, y, z raw 16-bit) and FIFO_LENGTH (default: their bytes)."""
        data = b"".join(struct.pack("<HHH", *(v & 0xFFFF for v in f)) for f in frames)
        self.fifo[0x26] = bytearray(data)
        lv = len(data) if level is None else level
        self.regs[0x24] = lv & 0xFF
        self.regs[0x25] = lv >> 8


machine.I2C = LogI2C
machine.Pin = LogPin
machine.SPI = LogSPI
machine.PWM = LogPWM
st7789._sleep_ms = lambda ms: W.sleep_ms(ms)
st7789.ticks_ms = lambda: W.ticks_ms()
bma423.sleep_ms = lambda ms: W.sleep_ms(ms)
bma423.sleep_us = lambda us: W.sleep_us(us)
bma423._ticks_ms = lambda: W.ticks_ms()


# -- AXP202 -------------------------------------------------------------------
def axp_new(w, line=True, nack=()):
    def make():
        p = board.Board(cpu_hz=None)._make_pmu()
        if not line:
            p.irq_pin = None    # as AXP202(i2c) without irq_pin: the constructor never reads it
        w.obj = p
    w.state = lambda p: {}
    return w.call("new", {"line": line}, make, nack)


def axp_world(out, name, regs=None):
    w = World(out, name)
    dev = machine.add_axp202(regs)
    return w, dev


def axp(out):
    w, dev = axp_world(out, "bring-up")
    axp_new(w)
    clean = w.n
    dev.regs[0x36] = 0x5F
    p = w.obj
    for ms in (0, 999, 1000, 1249, 1250, 1500, 1749, 1750, 2000, 2249, 2250, 2500, 4000, -5, 1500):
        w.call("set_long_press_ms", [ms], lambda: p.set_long_press_ms(ms))
    w.call("set_long_press_ms", [2500], lambda: p.set_long_press_ms(2500), nack=(1,))
    w.call("set_long_press_ms", [2500], lambda: p.set_long_press_ms(2500), nack=(2,))
    w.call("clear_irqs", [], p.clear_irqs)
    w.call("clear_irqs", [], p.clear_irqs, nack=(3,))
    for mv in (1700, 1800, 1850, 2500, 3299, 3300, 3400):
        w.call("set_ldo2_mv", [mv], lambda: p.set_ldo2_mv(mv))
    w.call("set_ldo2", [True], lambda: p.set_ldo2(True))
    w.call("enable_pek", [], p.enable_pek)
    w.call("shutdown", [], p.shutdown, nack=(1,))
    w.call("shutdown", [], p.shutdown, nack=(2,))
    w.call("shutdown", [], p.shutdown)
    w.call("shutdown", [], p.shutdown)

    for name, regs in (("set already", {0x82: 0xCA, 0x28: 0xF5, 0x12: 0x5E, 0x42: 0x03}),
                       ("DCDC3 clear", {0x12: 0x00, 0x28: 0x0F, 0x82: 0x35, 0x42: 0xFC}),
                       ("another chip", {0x03: 0x42})):
        w, dev = axp_world(out, name, regs)
        axp_new(w)
    for k in range(1, clean + 1):
        w, dev = axp_world(out, "bring-up, NACK %d" % k)
        axp_new(w, nack=(k,))

    for line in (True, False):
        w, dev = axp_world(out, "poll" if line else "poll without the IRQ line")
        axp_new(w, line=line)
        p = w.obj
        irq = machine.pins[pins.AXP202_IRQ]

        def poll(sts=None, high=0, nack=()):
            if sts:
                for i, v in enumerate(sts):
                    dev.regs[0x48 + i] |= v
            irq._v = high
            w.call("poll", [], p.poll, nack)
        poll()
        poll((0, 0, 0x02, 0, 0), high=1)          # pending, but the line is high
        poll()                                     # ... then low: PEK short
        poll((0, 0, 0x03, 0, 0))                   # long + short
        poll((0, 0, 0, 0, 0x20))                   # press
        poll((0, 0, 0, 0, 0x40))                   # release
        poll((0, 0, 0, 0, 0x61))                   # both in one poll, and an unrelated bit
        poll((0x08, 0, 0, 0, 0))                   # VBUS in
        poll((0x04, 0, 0, 0, 0))                   # VBUS out
        poll((0x8C, 0xFF, 0x10, 0x01, 0))          # both, and unrelated bits everywhere
        poll((0x08, 0, 0x02, 0, 0x20), nack=(3,))  # a read NACKed: nothing cleared or reported
        poll(nack=(7,))                            # 0x4A's clear NACKed: its short waits
        poll()                                     # ... for this poll
        poll((0, 0, 0x01, 0, 0), nack=(6,))        # the only clear NACKed
        poll(high=1)
        poll()

    w, dev = axp_world(out, "battery")
    axp_new(w)
    p = w.obj
    for pct in (0, 1, 50, 99, 100, 101, 0x7F):
        dev.regs[0xB9] = pct
        w.call("battery_percent", [], p.battery_percent)
    for raw in list(range(0, 4096, 23)) + [3091, 3092, 3772, 3773, 4095]:
        dev.regs[0xB9] = 0x80 | (raw & 0x7F)       # gauge not valid yet: from the voltage
        dev.regs[0x78] = raw >> 4
        dev.regs[0x79] = 0xF0 | (raw & 0x0F)       # the high nibble is not part of it
        w.call("battery_percent", [], p.battery_percent)
        w.call("battery_voltage", [], p.battery_voltage)
    for k in (1, 2, 3):
        w.call("battery_percent", [], p.battery_percent, nack=(k,))
    for k in (1, 2):
        w.call("battery_voltage", [], p.battery_voltage, nack=(k,))
    for v in (0x00, 0x40, 0xBF, 0xFF):
        dev.regs[0x01] = v
        dev.regs[0x00] = v ^ 0x20
        w.call("is_charging", [], p.is_charging)
        w.call("vbus_present", [], p.vbus_present)
    w.call("is_charging", [], p.is_charging, nack=(1,))
    w.call("vbus_present", [], p.vbus_present, nack=(1,))


# -- ST7789 -------------------------------------------------------------------
def lcd_world(out, name, t0_ms=PERIOD - 1000):
    w = World(out, name, t0_ms)
    machine.add_axp202()
    w.pmu = axp202.AXP202(machine.I2C(0))   # bl_power's chip (setup: not recorded)
    return w


def lcd_new(w, bl_power=True, init=True, nack=()):
    def make():
        w.obj = st7789.ST7789(fast=False, bl_power=w.pmu.set_ldo2 if bl_power else None, init=init)
    w.state = lambda d: {"asleep": d.asleep}
    return w.call("new", {"bl_power": bl_power, "init": init}, make, nack)


def lcd_ops(w):
    d = w.obj

    def strip(y0, h, seed):
        w.call("push_strip", [y0, h, seed], lambda: d.push_strip(y0, h, pattern(seed, 240 * h * 2)))

    def bright(level, nack=()):
        w.call("brightness", [level], lambda: d.brightness(level), nack)

    def sleep():
        w.call("sleep", [], d.sleep)

    def wake(level, wait=True):
        w.call("wake", [level, wait], lambda: d.wake(level, wait=w.wait if wait else None))
    return strip, bright, sleep, wake


def lcd(out):
    w = lcd_world(out, "board", PERIOD - 400)   # init's 320 ms end just before the clock wraps
    lcd_new(w)
    strip, bright, sleep, wake = lcd_ops(w)
    bright(0.6)
    for k in range(10):                          # the Runtime's 24-row strips
        strip(24 * k, 24, k)
    for k in range(4):                           # the renderer's 4 bands
        strip(60 * k, 60, 10 + k)
    strip(60, 60, 20)                            # a new window ...
    strip(120, 60, 21)                           # ... continued
    strip(0, 24, 22)                             # a new window again
    strip(24, 216, 23)                           # ... to the bottom row
    strip(24, 24, 24)                            # after the bottom: a new window
    strip(48, 10, 25)
    sleep()                                      # a command closes the window ...
    strip(58, 10, 26)                            # ... so this one opens a new one
    w.advance(30)
    wake(0)                                      # 35 ms after SLPIN: waits 85 more
    for level in (0.6, 1.0, 1.7, -0.3, 0.5, 1e-6, 0.5 / 65535, 7.6e-6, 0.123456789, 0.0):
        bright(level)
    sleep()
    w.advance(200)
    wake(0.8, wait=False)                        # no wait given: the driver's own sleeps
    sleep()
    wake(0)                                      # at once: 115 ms
    sleep()
    w.advance(114)
    wake(0.25)                                   # 1 ms
    sleep()
    w.advance(115)
    wake(0)                                      # none
    w.advance((PERIOD - 10 - w.ticks_ms()) % PERIOD)   # SLPIN 10 ms before the clock wraps
    sleep()
    w.advance(20)
    wake(0)                                      # 25 ms after it: 95 more
    strip(0, 240, 27)                            # a whole frame in one strip

    w = lcd_world(out, "no bl_power")
    lcd_new(w, bl_power=False)
    strip, bright, sleep, wake = lcd_ops(w)
    wake(0)                                      # never slept: no wait before SLPOUT
    bright(0.5)
    strip(100, 140, 1)

    w = lcd_world(out, "init later")
    lcd_new(w, init=False)
    strip, bright, sleep, wake = lcd_ops(w)
    bright(0.0)                                  # duty 0: LDO2 not needed yet
    bright(0.5, nack=(1,))                       # LDO2 NACKed: nothing set
    bright(0.5)                                  # LDO2 on, then the duty
    bright(0.7)
    w.call("init", [], w.obj.init)
    bright(0.6)

    for k in (1, 2):
        w = lcd_world(out, "init, LDO2 NACK %d" % k)
        lcd_new(w, nack=(k,))


# -- BMA423 -------------------------------------------------------------------
def imu_world(out, name, boot=0):
    w = World(out, name)
    dev = FakeBMA423(boot)
    machine.i2c_devices[(0, 0x19)] = dev
    machine.add_axp202()                         # I2C0's other chip: the scan finds both
    return w, dev


def imu_new(w, nack=()):
    def make():
        w.obj = board.Board(cpu_hz=None)._make_imu()
    w.state = lambda m: {"odr": m.odr, "z_sign": m.z_sign, "fifo_mg": list(m.fifo_mg)}
    return w.call("new", {}, make, nack)


def imu(out):
    w, dev = imu_world(out, "fifo")
    imu_new(w)
    clean = [e for e in w.last_io if "r" in e or "w" in e]   # the init's I2C transactions
    m = w.obj

    def read(frames=None, level=None, nack=()):
        if frames is not None:
            dev.fill(frames, level)
        w.call("fifo_read_mg", [], m.fifo_read_mg, nack)

    def odr(hz, nack=()):
        w.call("set_odr", [hz], lambda: m.set_odr(hz), nack)
    read()                                                       # empty
    read([(0, 0, 0x2000)])                                       # 1 g on z
    read([(16 * k, -16 * k, 0x7FF0 - 4096 * k) for k in range(10)])
    read([(1, 2, 3)] * 11, level=65)                             # 10 whole frames
    read([(-0x8000 + 16 * k, 0x7FFF - k, -1 - k) for k in range(170)], level=1024)   # full
    read([(5, 6, 7)] * 50, level=0xC000 | 60)                    # FIFO_LENGTH_1 bits 7:6 are not the level
    read([(0x000F, 0x0010, -0x10)] * 3, level=0x3FFF)            # past the fill: zeros
    read([(1, 1, 1)] * 3 + [(-0x8000, -0x8000, -0x8000)] + [(2, 2, 2)] * 4)   # stops at an all-0x8000 frame
    read([(-0x8000, 0, 0), (0, -0x8000, -0x8000), (-0x8000, -0x8000, 0)])    # not all three: data
    read([(100, 200, 300)] * 5, nack=(1,))                       # the level NACKed
    read(nack=(2,))                                              # the data NACKed
    read()                                                       # ... still there
    odr(800)
    read([(9, 9, 9)])
    odr(25)
    odr(1600)
    odr(123)                                                     # no such rate
    odr(400, nack=(1,))                                          # ACC_CONF NACKed: still 1600
    dev.fill([(3, 4, 5)] * 2)
    odr(200, nack=(2,))                                          # the flush NACKed: 200, FIFO kept
    read()
    odr(100)
    seed = 12345
    for k in range(40):                                          # a sweep of values and fills
        frames = []
        seed = (seed * 1103515245 + 12345) & 0x7FFFFFFF
        for _ in range(seed % 171):
            seed = (seed * 1103515245 + 12345) & 0x7FFFFFFF
            frames.append(((seed >> 4) & 0xFFFF, (seed >> 9) & 0xFFFF, (seed * 7 >> 3) & 0xFFFF))
        read(frames)

    w, dev = imu_world(out, "slow reset", boot=3)
    imu_new(w)
    w, dev = imu_world(out, "reset NACKs", boot=0)
    imu_new(w, nack=(2, 3, 4, 5))                                # the reset write too: ignored
    w, dev = imu_world(out, "no answer after reset", boot=1000)
    imu_new(w)
    w, dev = imu_world(out, "another chip")
    dev.regs[0] = 0x12
    imu_new(w)
    # Each transaction of the init NACKed in turn, but INTERNAL_STATUS (the
    # feature engine's, not ported): a NACK there fails only the Python's init.
    for k, e in enumerate(clean, 1):
        if e.get("r", [0, 0, 0])[2] == bma423.REG_INTERNAL_STATUS:
            continue
        w, dev = imu_world(out, "init, NACK %d" % k)
        imu_new(w, nack=(k,))


# -- FT6336 -------------------------------------------------------------------
TOUCHES = (
    (0x00, 0x00, 0x00, 0x00, 0x00),       # nobody
    (0x01, 0x80, 10, 0x00, 20),           # one finger (event 10: contact)
    (0x01, 0x80, 239, 0x10, 0),           # corners (a touch id in YH's high nibble)
    (0x01, 0x00, 0, 0x00, 239),
    (0x01, 0x80, 240, 0x01, 0x2C),        # past the panel: clamped (300)
    (0x81, 0x8F, 0xFF, 0x0F, 0xFF),       # a high nibble in TD_STATUS; 4095, 4095
    (0x01, 0x40, 50, 0x00, 60),           # event 01: lift (x, y kept)
    (0x02, 0x80, 120, 0x00, 130),         # two fingers
    (0x03, 0x80, 1, 0x00, 2),             # three: not a touch
    (0x0F, 0x80, 3, 0x00, 4),
    (0x01, 0xC0, 77, 0x00, 88),           # event 11: no event (still a touch)
)


def touch_world(out, name, rotation=0, mirror_x=False, mirror_y=False, nack=()):
    w = World(out, name)
    dev = machine.add_i2c_device(pins.I2C1_ID, pins.TOUCH_ADDR)

    def make():
        bus = machine.I2C(pins.I2C1_ID, sda=machine.Pin(pins.TOUCH_SDA), scl=machine.Pin(pins.TOUCH_SCL))
        w.obj = ft6336.FT6336(i2c=bus, int_pin=pins.TOUCH_INT, rotation=rotation,
                              mirror_x=mirror_x, mirror_y=mirror_y)
    w.state = lambda t: {"errors": t.errors}
    w.call("new", {"rotation": rotation, "mirror_x": mirror_x, "mirror_y": mirror_y}, make, nack)
    return w, dev


def touch(out):
    for rot in range(5):                  # 4: the same as 0 (rotation & 3)
        for mx in (False, True):
            for my in (False, True):
                w, dev = touch_world(out, "rotation %d%s%s" % (rot, " mirror_x" * mx, " mirror_y" * my),
                                     rot, mx, my)
                t = w.obj
                for regs in TOUCHES:
                    dev.regs[2:7] = bytes(regs)
                    w.call("read", [], t.read)
                dev.regs[2:7] = bytes(TOUCHES[1])
                w.call("read", [], t.read, nack=(1,))   # a bus error: not touching, x and y kept
                w.call("read", [], t.read)
    w, dev = touch_world(out, "G_MODE NACK", nack=(1,))
    dev.regs[2:7] = bytes(TOUCHES[1])
    w.call("read", [], w.obj.read)


# -- Board.init -----------------------------------------------------------------
def boards(out):
    w = World(out, "init")
    axp = machine.add_axp202()
    dev = FakeBMA423(boot=2)
    machine.i2c_devices[(0, 0x19)] = dev
    tp = machine.add_i2c_device(pins.I2C1_ID, pins.TOUCH_ADDR)
    b = board.Board(cpu_hz=None)

    def init():
        b.init(parts=("pmu", "display", "backlight", "imu", "touch"))
        w.obj = b
    w.state = lambda b: {"asleep": b.display.asleep, "odr": b.imu.odr, "z_sign": b.imu.z_sign,
                         "touch_errors": b.touch.errors}
    w.call("init", [], init)
    axp.regs[0x4A] = 0x02
    w.call("pmu.poll", [], b.pmu.poll)
    w.call("display.push_strip", [0, 24, 1], lambda: b.display.push_strip(0, 24, pattern(1, 240 * 24 * 2)))
    dev.fill([(0, 0, -0x2000)] * 4)
    w.call("imu.fifo_read_mg", [], b.imu.fifo_read_mg)
    tp.regs[2:7] = bytes(TOUCHES[1])
    w.call("touch.read", [], b.touch.read)
    w.call("display.sleep", [], b.display.sleep)


DRIVERS = (("axp202", axp), ("st7789", lcd), ("bma423", imu), ("ft6336", touch), ("board", boards))


def main(argv):
    if len(argv) != 1:
        print(__doc__.strip().splitlines()[2].strip())
        return 2
    os.makedirs(argv[0], exist_ok=True)
    for name, fn in DRIVERS:
        with open(os.path.join(argv[0], "hal.%s.jsonl" % name), "w") as out:
            fn(out)
    print("trace_hal: %d drivers in %s" % (len(DRIVERS), argv[0]))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
