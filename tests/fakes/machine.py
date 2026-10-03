"""Minimal fake of MicroPython's ``machine`` module for driver tests."""

_freq = [160000000]
pins = {}
i2c_devices = {}   # (bus_id, addr) -> FakeI2CDevice
spi_log = []       # list of (spi_id, "write"/"cs", payload)


def reset_fakes():
    del wdts[:]
    pins.clear()
    i2c_devices.clear()
    del spi_log[:]


def freq(f=None):
    if f is None:
        return _freq[0]
    _freq[0] = f


class Pin:
    IN = 1
    OUT = 3
    OPEN_DRAIN = 7
    PULL_UP = 1
    PULL_DOWN = 2
    IRQ_RISING = 1
    IRQ_FALLING = 2

    def __init__(self, pin_id, mode=-1, pull=-1, value=None):
        self.id = pin_id
        self.mode = mode
        self._v = 0 if value is None else value
        self.handler = None
        self.writes = []
        pins[pin_id] = self

    def init(self, mode=-1, pull=-1, value=None):
        self.mode = mode
        if value is not None:
            self.value(value)

    def value(self, v=None):
        if v is None:
            return self._v
        self._v = 1 if v else 0
        self.writes.append(self._v)

    def __call__(self, v=None):
        return self.value(v)

    def on(self):
        self.value(1)

    def off(self):
        self.value(0)

    def irq(self, handler=None, trigger=0):
        self.handler = handler
        self.trigger = trigger


class PWM:
    def __init__(self, pin, freq=1000, duty_u16=0):
        self.pin = pin
        self._freq = freq
        self._duty = duty_u16
        self.history = []

    def freq(self, f=None):
        if f is None:
            return self._freq
        self._freq = f

    def duty_u16(self, d=None):
        if d is None:
            return self._duty
        self._duty = d
        self.history.append(d)

    def deinit(self):
        self._duty = 0


class FakeI2CDevice:
    """256-byte register file; ``on_write(reg, data)`` hook for side effects."""

    def __init__(self, addr, regs=None):
        self.addr = addr
        self.regs = bytearray(256)
        if regs:
            for k in regs:
                self.regs[k] = regs[k]
        self.writes = []   # list of (reg, bytes)
        self.fifo = {}     # reg -> bytearray served as a stream on read

    def read(self, reg, n):
        if reg in self.fifo:
            src = self.fifo[reg]
            out = bytes(src[:n]) + bytes(max(0, n - len(src)))
            self.fifo[reg] = src[n:]
            return out
        return bytes(self.regs[(reg + i) & 0xFF] for i in range(n))

    def write(self, reg, data):
        self.writes.append((reg, bytes(data)))
        for i, b in enumerate(data):
            self.regs[(reg + i) & 0xFF] = b


def add_i2c_device(bus_id, addr, regs=None):
    dev = FakeI2CDevice(addr, regs)
    i2c_devices[(bus_id, addr)] = dev
    return dev


class I2C:
    def __init__(self, bus_id=0, scl=None, sda=None, freq=400000):
        self.bus_id = bus_id
        self.scl = scl
        self.sda = sda
        self.freq = freq

    def _dev(self, addr):
        d = i2c_devices.get((self.bus_id, addr))
        if d is None:
            raise OSError(19)  # ENODEV, like a missing ACK
        return d

    def scan(self):
        return sorted(a for (b, a) in i2c_devices if b == self.bus_id)

    def readfrom_mem(self, addr, reg, n, addrsize=8):
        return self._dev(addr).read(reg, n)

    def readfrom_mem_into(self, addr, reg, buf, addrsize=8):
        data = self._dev(addr).read(reg, len(buf))
        for i in range(len(buf)):
            buf[i] = data[i]

    def writeto_mem(self, addr, reg, data, addrsize=8):
        self._dev(addr).write(reg, data)

    def writeto(self, addr, data, stop=True):
        d = self._dev(addr)
        if len(data):
            d.write(data[0], data[1:])
        return len(data)

    def readfrom(self, addr, n, stop=True):
        return self._dev(addr).read(0, n)


SoftI2C = I2C


class SPI:
    MSB = 0

    def __init__(self, spi_id, baudrate=1000000, polarity=0, phase=0, bits=8,
                 firstbit=0, sck=None, mosi=None, miso=None):
        self.id = spi_id
        self.baudrate = baudrate
        self.sck = sck
        self.mosi = mosi
        self.miso = miso

    def init(self, baudrate=None, **kw):
        if baudrate is not None:
            self.baudrate = baudrate

    def write(self, buf):
        spi_log.append((self.id, "write", bytes(buf)))

    def deinit(self):
        pass


class WDT:
    def __init__(self, id=0, timeout=5000):
        self.timeout = timeout
        self.feeds = 0
        wdts.append(self)

    def feed(self):
        self.feeds += 1


wdts = []


class Timer:
    PERIODIC = 1
    ONE_SHOT = 0

    def __init__(self, tid=0):
        self.id = tid

    def init(self, mode=1, period=1000, callback=None, freq=None):
        self.callback = callback
        self.period = period

    def deinit(self):
        self.callback = None


def idle():
    pass


def reset():
    raise SystemExit("machine.reset()")


def unique_id():
    return b"\x24\x0a\xc4\x00\x00\x01"
