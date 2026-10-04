"""SPI clock experiment -- runs ON THE WATCH (stock MicroPython 1.29).

    python3 tools/deploy.py --noapp      # once: hal/, finder/ and ui/
    mpremote run tools/bench_spi_clock.py

Stock firmware refuses a full-duplex SPI device above 26.67 MHz on GPIO-matrix
pins (TFT SCK 18, MOSI 19), but the display only ever writes. This pokes the
display bus's clock register (HSPI, SPI_CLOCK_REG(2) at 0x3FF64018) to 40 and
then 80 MHz after the driver has set the device up: ESP-IDF writes it again
only when the bus switches to another device, so the poke holds. For each
clock it prints the time to push a full frame (10 strips) and holds a test card
for 5 s (colour bars, 1-pixel lines, a gradient) so the eye can spot errors.
It reads the register first and stops if it is not the 26.67 MHz value it
expects, and it always puts 26.67 MHz back at the end.
"""

import framebuf
import machine

from finder.compat import sleep_ms, ticks_diff, ticks_us
from hal.board import Board
from hal.st7789 import rgb565

REG = 0x3FF64018                 # SPI_CLOCK_REG for SPI2 (HSPI, machine.SPI(1))
CLOCKS = (                       # (MHz label, register value)
    ("26.67", 0x00002002),       # pre 0, N 2, H 0, L 2: 80 / 3
    ("40", 0x00001001),          # N 1, L 1: 80 / 2
    ("80", 0x80000000),          # clk_equ_sysclk: 80 / 1
)
N = 20                           # frames timed per clock
HOLD_MS = 5000


def card(strips, W, SH):
    """Test card into the strips: 8 colour bars, then 1-pixel black and white
    lines, then a red-to-blue gradient, and a white frame round the edge."""
    bars = [rgb565(r, g, b) for r, g, b in ((255, 255, 255), (255, 255, 0), (0, 255, 255), (0, 255, 0),
                                            (255, 0, 255), (255, 0, 0), (0, 0, 255), (0, 0, 0))]
    white, black = rgb565(255, 255, 255), rgb565(0, 0, 0)
    for k, buf in enumerate(strips):
        fb = framebuf.FrameBuffer(buf, W, SH, framebuf.RGB565)
        for row in range(SH):
            y = k * SH + row
            if y < 80:
                for i, c in enumerate(bars):
                    fb.hline(i * 30, row, 30, c)
            elif y < 160:
                fb.hline(0, row, W, white if y & 1 else black)
                for x in range(0, W, 2):
                    fb.pixel(x, row, black if y & 1 else white)
            else:
                for x in range(W):
                    fb.pixel(x, row, rgb565(255 - x * 255 // 239, 0, x * 255 // 239))
            fb.pixel(0, row, white)
            fb.pixel(W - 1, row, white)
        if k == 0:
            fb.hline(0, 0, W, white)
        if k == len(strips) - 1:
            fb.hline(0, SH - 1, W, white)


def push(disp, strips):
    for k, buf in enumerate(strips):
        disp.push_strip(k * disp.strip_rows, disp.strip_rows, buf)


def main(hold_ms=HOLD_MS, frames=N, disp=None):
    if disp is None:
        disp = Board().display
    disp.brightness(0.6)
    W, SH = disp.width, disp.strip_rows
    strips = [bytearray(W * SH * 2) for _ in range(disp.height // SH)]
    card(strips, W, SH)
    push(disp, strips)                        # the driver sets the device up
    now = machine.mem32[REG] & 0xFFFFFFFF
    if now != CLOCKS[0][1]:
        print("SPI_CLOCK_REG is 0x%08x, expected 0x%08x: not poking" % (now, CLOCKS[0][1]))
        return
    print("clock_mhz  reg         frame_ms  wire_ms")
    try:
        for label, value in CLOCKS:
            machine.mem32[REG] = value
            back = machine.mem32[REG] & 0xFFFFFFFF
            t0 = ticks_us()
            for _ in range(frames):
                push(disp, strips)
            ms = ticks_diff(ticks_us(), t0) / frames / 1000
            wire = disp.frame_bytes * 8 / (float(label) * 1000)
            print("%-9s  0x%08x  %7.2f  %7.2f" % (label, back, ms, wire))
            print("  look now: test card at %s MHz for %d s" % (label, hold_ms // 1000))
            sleep_ms(hold_ms)
    finally:
        machine.mem32[REG] = CLOCKS[0][1]
        push(disp, strips)
    print("back at 26.67 MHz")


if __name__ == "__main__":
    main()
