"""Display pipeline benchmark -- runs ON THE WATCH (T-Watch 2020 V1, stock
MicroPython 1.29, pure-Python hal/st7789.py).

    python3 tools/deploy.py --noapp      # once: hal/, finder/ and ui/ (st7789 uses finder.compat)
    mpremote run tools/bench_display.py

The ``# %%`` sections can also be pasted one per cell into a Jupyter
MicroPython kernel: run them in order once, after which any section can be
re-run on its own. Measures:
  * print(spi): the real SPI clock the IDF picked (80 MHz / integer)
  * T_push  : push_frame of a full 115,200 B frame, and 10 x push_strip(24 rows)
  * T_blit  : GS8 -> RGB565 palette blit (framebuf.blit with palette), per strip
  * palette : rebuild time of a 256- and 32-entry byte-swapped RGB565 palette
  * gc      : gc.collect() time, plus bytes allocated per pushed frame
  * a full strip-rendered frame (palette + 10 x (blit + push)) -> fps
Set FAST = True only on a custom NO_DUMMY firmware (40 MHz); stock firmware
crashes above 26.67 MHz.
"""

# %% [1] setup -----------------------------------------------------------------
import gc
import time
import machine
import framebuf
from hal.board import Board
from hal.st7789 import rgb565

FAST = False      # 40 MHz: custom build only!
N = 20            # repetitions per measurement

# 240 MHz, the one shared I2C0, AXP202 LDO2 at 3.3 V before the panel init
# (DCDC3 kept). In a notebook that already has a Board ``b``: board = b
board = Board(fast_spi=FAST)
disp = board.display
disp.stop_background()    # times the plain driver (tools/bench_frame.py: the background push)
STRIP = disp.strip_rows   # 24 rows per strip -> 10 strips, 11,520 B each
disp.fill(0)
disp.brightness(0.6)
print("cpu", machine.freq() // 1_000_000, "MHz;", disp.spi)   # real SPI clock
BAUD = disp.baudrate

_t = [0] * N  # preallocated timing slots (small ints: no heap churn)


def report(name, n_bytes=0):
    """Print min/avg/max ms of _t[:N]; with n_bytes also MB/s vs. wire limit."""
    lo = min(_t)
    hi = max(_t)
    avg = max(1, sum(_t) / N)   # us; guard against coarse timers
    s = "%-22s min %6.2f  avg %6.2f  max %6.2f ms" % (name, lo / 1000, avg / 1000, hi / 1000)
    if n_bytes:
        ideal = n_bytes * 8 * 1000 / BAUD      # ms at the requested clock
        s += "  | %.2f MB/s, wire-ideal %.2f ms (%.0f%%)" % (
            n_bytes / avg, ideal, 100 * ideal * 1000 / avg)
    print(s)
    return avg


# %% [2] T_push: full frame (one window, strip-sized writes, CS held low) ------
frame = bytearray(disp.frame_bytes)
ffb = framebuf.FrameBuffer(frame, 240, 240, framebuf.RGB565)
ffb.fill(rgb565(0, 0, 80))
ffb.text("bench_display", 64, 116, rgb565(255, 255, 255))
disp.push_frame(frame)  # warm-up builds the cached slices
gc.collect()
a0 = gc.mem_alloc()
for i in range(N):
    t0 = time.ticks_us()
    disp.push_frame(frame)
    _t[i] = time.ticks_diff(time.ticks_us(), t0)
a1 = gc.mem_alloc()
T_push = report("push_frame", len(frame))
print("  heap alloc per push_frame: %d B" % ((a1 - a0) // N))

# %% [3] T_push: 10 x push_strip (top to bottom: one window) -------------------
strip = bytearray(240 * STRIP * 2)
sfb = framebuf.FrameBuffer(strip, 240, STRIP, framebuf.RGB565)
sfb.fill(rgb565(80, 0, 0))
gc.collect()
a0 = gc.mem_alloc()
for i in range(N):
    t0 = time.ticks_us()
    for y in range(0, 240, STRIP):
        disp.push_strip(y, STRIP, strip)
    _t[i] = time.ticks_diff(time.ticks_us(), t0)
a1 = gc.mem_alloc()
T_strips = report("10x push_strip", 240 * 240 * 2)
print("  heap alloc per frame: %d B" % ((a1 - a0) // N))

# %% [4] T_blit: GS8 intensity map -> RGB565 strip through a palette ----------
gs = bytearray(240 * 240)
gfb = framebuf.FrameBuffer(gs, 240, 240, framebuf.GS8)
for r in range(120, 0, -4):   # concentric squares = cheap stand-in for rings
    gfb.fill_rect(120 - r, 120 - r, 2 * r, 2 * r, (r * 2) & 0xFF)
pal = bytearray(256 * 2)
pfb = framebuf.FrameBuffer(pal, 256, 1, framebuf.RGB565)
for i in range(256):
    pfb.pixel(i, 0, rgb565(i, i // 2, 255 - i))
sfb.blit(gfb, 0, 0, -1, pfb)  # warm-up
for i in range(N):
    t0 = time.ticks_us()
    for k in range(240 // STRIP):
        sfb.blit(gfb, 0, -k * STRIP, -1, pfb)   # clipped: only this strip's rows
    _t[i] = time.ticks_diff(time.ticks_us(), t0)
T_blit = report("10x GS8 palette blit")
print("  per strip %.2f ms" % (T_blit / 1000 / (240 // STRIP)))

# %% [5] palette rebuild (256 and 32 entries, byte-swapped RGB565) ------------
def build_palette(buf, n, gain):
    """Write n byte-swapped colours straight into buf (framebuf byte order)."""
    for i in range(n):
        v = (i * 255) // (n - 1)
        c = rgb565((v * gain) >> 8, v, 255 - v)
        buf[2 * i] = c & 0xFF
        buf[2 * i + 1] = c >> 8


for n in (256, 32):
    for i in range(N):
        t0 = time.ticks_us()
        build_palette(pal, n, 128 + i)
        _t[i] = time.ticks_diff(time.ticks_us(), t0)
    report("palette rebuild %d" % n)
for i in range(N):
    t0 = time.ticks_us()
    for j in range(256):
        pfb.pixel(j, 0, j * 257)
    _t[i] = time.ticks_diff(time.ticks_us(), t0)
report("palette via pixel() 256")

# %% [6] gc time ---------------------------------------------------------------
for i in range(N):
    junk = [bytearray(64) for _ in range(50)]  # some garbage to sweep
    junk = None
    t0 = time.ticks_us()
    gc.collect()
    _t[i] = time.ticks_diff(time.ticks_us(), t0)
T_gc = report("gc.collect()")
print("  heap free %d B, used %d B" % (gc.mem_free(), gc.mem_alloc()))

# %% [7] full strip-rendered frame: 32-entry palette + 10 x (blit + push) -----
gc.collect()
a0 = gc.mem_alloc()
for i in range(N):
    t0 = time.ticks_us()
    build_palette(pal, 32, 128 + i)
    for k in range(240 // STRIP):
        sfb.blit(gfb, 0, -k * STRIP, -1, pfb)
        disp.push_strip(k * STRIP, STRIP, strip)
    _t[i] = time.ticks_diff(time.ticks_us(), t0)
a1 = gc.mem_alloc()
T_frame = report("frame (pal+blit+push)", 240 * 240 * 2)
print("  => %.1f fps; heap alloc per frame %d B" % (1e6 / T_frame, (a1 - a0) // N))

# %% [8] summary ---------------------------------------------------------------
import ui.field  # noqa: E402  (needs ui/ deployed: tools/deploy.py copies it)
print("palette kernel:", ui.field.KERNEL)   # 'viper', 'python' or 'self-check failed'
print("SUMMARY clk=%d fast=%s T_push=%.1fms T_strips=%.1fms T_blit=%.1fms T_gc=%.1fms frame=%.1fms"
      % (BAUD, FAST, T_push / 1000, T_strips / 1000, T_blit / 1000, T_gc / 1000, T_frame / 1000))
disp.fill(0)
