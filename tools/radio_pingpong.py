"""On-watch ESP-NOW benchmark: N-packet ping-pong, optionally under display load.

Needs two watches on the same channel with finder/ and hal/ deployed
(``tools/deploy.py``; it does not copy tools/). Copy this file to each watch's
root and start the echo side first::

    mpremote cp tools/radio_pingpong.py :
    # watch A
    mpremote exec "import radio_pingpong as pp; pp.run('pong')"
    # watch B
    mpremote exec "import radio_pingpong as pp; pp.run('ping', n=1000, render=True)"

(or the same ``import``/``pp.run`` at the REPL). ``mpremote run`` cannot pass
script arguments to the device, so it always runs the default ping. The ping side
reports round-trip delivery %, RTT and pong inter-arrival p50/p95/max, and RSSI
stats in both directions (pong frames carry the RSSI the echo side measured).
``render`` pushes a full 240x240 RGB565 frame per loop over the display SPI
bus with CS held high (panel ignores it): the same DMA/bus load as drawing.

Beacons are the real 16-byte ``finder.proto`` format with ``GAME_ID``;
``game_state`` marks PING/PONG and ``seq`` is the ping index.
"""

from array import array
from finder.compat import ticks_ms, ticks_add, ticks_diff, argv
from finder import proto

GAME_ID = 0xEE
KIND_PING = 1
KIND_PONG = 2


def percentile(vals, p):
    """p in 0..100 over a list/array (sorts a copy)."""
    if not vals:
        return None
    s = sorted(vals)
    k = int(p * (len(s) - 1) / 100.0 + 0.5)
    return s[k]


def summary(vals):
    """(n, min, mean, max, sd) or None."""
    n = len(vals)
    if not n:
        return None
    m = sum(vals) / n
    v = sum((x - m) * (x - m) for x in vals) / n
    return (n, min(vals), m, max(vals), v ** 0.5)


class Pinger:
    """Sends ``n`` pings every ``period_ms`` and matches the pongs."""

    def __init__(self, radio, n=1000, period_ms=50, tail_ms=1000):
        self.r = radio
        self.n = n
        self.period = period_ms
        self.tail_ms = tail_ms
        self.b = proto.Beacon(GAME_ID)
        self.b.state = KIND_PING
        self.rx_b = proto.Beacon(GAME_ID)
        self.buf = bytearray(proto.SIZE)
        self.t_sent = array("i", [0] * n)
        self.got = bytearray(n)
        self.sent = 0
        self.recv = 0
        self.next_t = None
        self.last_send_t = None
        self.last_rx_t = None
        self.rtt = []
        self.gaps = []
        self.rssi = []        # what I measured on pongs
        self.peer_rssi = []   # what the echo side measured on my pings
        self.stray = 0
        self.last_rssi = proto.RSSI_NONE
        self._cb = self.on_rx

    def step(self, now):
        if self.sent < self.n and (self.next_t is None or ticks_diff(now, self.next_t) >= 0):
            b = self.b
            b.seq = self.sent
            b.rssi_last = self.last_rssi
            b.pack_into(self.buf)
            self.t_sent[self.sent] = now
            self.r.send(self.buf, now)
            self.sent += 1
            self.last_send_t = now
            self.next_t = ticks_add(now if self.next_t is None else self.next_t, self.period)
            if ticks_diff(now, self.next_t) > self.period:
                self.next_t = now
        self.r.poll(now, callback=self._cb)

    def on_rx(self, mac, buf, n, rssi, t):
        if not proto.valid(buf, n, GAME_ID) or buf[12] != KIND_PONG:
            self.stray += 1
            return
        b = self.rx_b.unpack_from(buf)
        s = b.seq
        if s >= self.sent or self.got[s]:
            self.stray += 1
            return
        self.got[s] = 1
        self.recv += 1
        self.rtt.append(ticks_diff(t, self.t_sent[s]))
        if self.last_rx_t is not None:
            self.gaps.append(ticks_diff(t, self.last_rx_t))
        self.last_rx_t = t
        self.last_rssi = rssi
        self.rssi.append(rssi)
        if b.rssi_last != proto.RSSI_NONE:
            self.peer_rssi.append(b.rssi_last)

    def done(self, now):
        if self.sent < self.n:
            return False
        return self.recv >= self.n or ticks_diff(now, self.last_send_t) >= self.tail_ms

    def report(self):
        return {
            "role": "ping", "sent": self.sent, "pongs": self.recv,
            "delivery_pct": 100.0 * self.recv / self.sent if self.sent else 0.0,
            "rtt_p50": percentile(self.rtt, 50), "rtt_p95": percentile(self.rtt, 95),
            "rtt_max": max(self.rtt) if self.rtt else None,
            "gap_p50": percentile(self.gaps, 50), "gap_p95": percentile(self.gaps, 95),
            "gap_max": max(self.gaps) if self.gaps else None,
            "rssi_rx": summary(self.rssi), "rssi_at_peer": summary(self.peer_rssi),
            "stray": self.stray,
        }


class Ponger:
    """Echoes every ping; stops ``idle_ms`` after the last one."""

    def __init__(self, radio, idle_ms=3000, max_wait_ms=60000):
        self.r = radio
        self.idle_ms = idle_ms
        self.max_wait_ms = max_wait_ms
        self.b = proto.Beacon(GAME_ID)
        self.b.state = KIND_PONG
        self.buf = bytearray(proto.SIZE)
        self.recv = 0
        self.echo_fail = 0
        self.t0 = None
        self.last_rx_t = None
        self.gaps = []
        self.rssi = []
        self.stray = 0
        self._cb = self.on_rx

    def step(self, now):
        if self.t0 is None:
            self.t0 = now
        self.r.poll(now, callback=self._cb)

    def on_rx(self, mac, buf, n, rssi, t):
        if not proto.valid(buf, n, GAME_ID) or buf[12] != KIND_PING:
            self.stray += 1
            return
        b = self.b
        b.seq = proto.seq_of(buf)
        b.rssi_last = rssi
        b.pack_into(self.buf)
        if not self.r.send(self.buf, t):
            self.echo_fail += 1
        self.recv += 1
        if self.last_rx_t is not None:
            self.gaps.append(ticks_diff(t, self.last_rx_t))
        self.last_rx_t = t
        self.rssi.append(rssi)

    def done(self, now):
        if self.last_rx_t is None:
            return self.t0 is not None and ticks_diff(now, self.t0) > self.max_wait_ms
        return ticks_diff(now, self.last_rx_t) > self.idle_ms

    def report(self):
        return {
            "role": "pong", "pings": self.recv, "echo_fail": self.echo_fail,
            "gap_p50": percentile(self.gaps, 50), "gap_p95": percentile(self.gaps, 95),
            "gap_max": max(self.gaps) if self.gaps else None,
            "rssi_rx": summary(self.rssi), "stray": self.stray,
        }


class RenderLoad:
    """Full-frame SPI pushes to the display bus with CS high (no pixels change)."""

    STRIP = 240 * 24 * 2

    def __init__(self):
        from machine import Pin, SPI
        from hal import pins
        self.cs = Pin(pins.TFT_CS, Pin.OUT, value=1)
        self.spi = SPI(pins.TFT_SPI_ID, baudrate=pins.TFT_BAUD, sck=Pin(pins.TFT_SCK),
                       mosi=Pin(pins.TFT_MOSI), miso=None)
        self.strip = bytearray(self.STRIP)
        self.frames = 0

    def frame(self):
        for _ in range(10):
            self.spi.write(self.strip)
        self.frames += 1


def print_report(rep, radio=None, frames=0):
    print("--- radio_pingpong (%s) ---" % rep["role"])
    for k in sorted(rep):
        v = rep[k]
        if isinstance(v, tuple):
            v = "n=%d min=%d mean=%.1f max=%d sd=%.1f" % v
        elif isinstance(v, float):
            v = "%.1f" % v
        print("%-13s %s" % (k, v))
    if frames:
        print("%-13s %d" % ("frames", frames))
    if radio is not None:
        print("%-13s %s" % ("radio", radio.stats()))


def run(role="ping", n=1000, period_ms=50, render=False, channel=6, radio=None):
    """Run one side of the benchmark on a watch; returns the report dict."""
    import machine
    try:
        machine.freq(240_000_000)
    except Exception:  # noqa: BLE001 - benchmark still useful at other clocks
        pass
    if radio is None:
        from hal.radio import EspNowRadio
        radio = EspNowRadio(channel=channel).begin()
    load = RenderLoad() if render else None
    x = Pinger(radio, n, period_ms) if role == "ping" else Ponger(radio)
    try:
        from time import sleep_ms
    except ImportError:
        sleep_ms = None
    print("radio_pingpong: %s on ch %d, n=%d, render=%s" % (role, radio.channel, n, bool(render)))
    while True:
        now = ticks_ms()
        x.step(now)
        if x.done(now):
            break
        if load is not None:
            load.frame()
        elif sleep_ms is not None:
            sleep_ms(1)
    rep = x.report()
    print_report(rep, radio, load.frames if load else 0)
    return rep


def main(args):
    """Host/WASM runners only (they set argv); on a watch use ``run()``."""
    role = args[1] if len(args) > 1 else "ping"
    n = int(args[2]) if len(args) > 2 else 1000
    render = "render" in args[3:]
    run(role, n, render=render)


if __name__ == "__main__":
    main(argv(globals()))
