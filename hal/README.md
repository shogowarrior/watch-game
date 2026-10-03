# hal/: T-Watch 2020 V1 drivers

Pure-Python drivers for the **LILYGO T-Watch 2020 V1** on **stock MicroPython
v1.29.0** (`ESP32_GENERIC-SPIRAM`). The stock firmware has no `st7789` C module
and no frozen `axp202c`, so everything here is plain `.py`. Only `hal/` may
import `machine`, `network`, `espnow` or `micropython`. Game logic in `finder/`
stays pure and gets its data from these drivers. Every driver takes its bus
(`i2c`, `spi`, pins) as an optional argument, so tests can inject the fakes in
`tests/fakes/` and the drivers run unchanged on CPython and MicroPython.

V1 only: the V2 drives its motor through an I2C haptic chip and adds GPS/SD,
and the V3 adds a PDM microphone. Don't reuse `pins.py` on those revisions
without checking each pin.

## Drivers

**`pins.py`: GPIO map.** Every pin, bus id, I2C address and clock limit, as
`const()`. Remember three things. `TFT_MISO = None`: the ST7789 bus must never
get a MISO pin. `TFT_BAUD = 26_666_667` (80 MHz / 3) is the ceiling on stock
firmware. `TFT_BAUD_FAST = 40 MHz` is only for a custom build without the
dummy-cycle limit. GPIOs 34-39 (AXP202 IRQ 35, RTC INT 37, touch INT 38,
BMA423 INT1 39) are input-only and have no pull-ups. The I2S amplifier pins
(25/26/33) and the IR LED (13) are listed so that nothing else claims them.

**`st7789.py`: 240x240 display.** Driver class `ST7789`. It only moves bytes.
You draw into RGB565 `framebuf` strips (usually a GS8 buffer blitted through a
palette), then push them with `push_strip(y0, h, buf)` (a full-width strip),
`push_frame(fb)` (115,200 B sent as strip-sized writes in one CS-low burst; the
slice list is cached, so it allocates nothing) or `blit(x, y, w, h, buf)`. You
can also paint directly with `fill_rect` or `fill`. The SPI bus is SPI(1) with
sck 18, mosi 19, cs 5 and dc 27. There is **no reset pin**, so `init()` does a
software reset: SWRESET, SLPOUT, COLMOD 0x55, MADCTL **0xC0** with a **row
offset of 80** (the upright orientation, using GRAM rows 80..319 of 320), INVON,
NORON, DISPON. `init()` blocks for about 300 ms. Gotchas:

- The SPI is always created with `miso=None`. Otherwise HSPI's default MISO,
  GPIO12, gets claimed, and GPIO12 is the backlight.
- Pins 18/19 go through the GPIO matrix. Asking for more than 26.67 MHz on
  stock firmware hits an unhandled `ESP_ERR_NOT_SUPPORTED` and crashes, so the
  constructor raises `ValueError` unless you pass `fast=True` (custom build
  only). A full frame takes about 35 ms at 26.67 MHz.
- The panel wants RGB565 MSB-first, but `framebuf` stores it little-endian.
  Every colour you give to framebuf, a palette or `fill` must be
  **byte-swapped**. Use `rgb565(r, g, b)` or `swap16(c)`. `unswap_rgb(c)`
  converts back, for debugging.
- The backlight is PWM on GPIO12 (`brightness(0..1)`), but on the V1 the
  AXP202 **LDO2** powers both the backlight and the panel itself. LDO2 must be
  on before `init()`, or the init commands are lost. Pass
  `bl_power=pmu.set_ldo2` and `init()` switches it on first. The driver never
  switches LDO2 off: `sleep()` sets the duty to 0 and sends DISPOFF + SLPIN
  (GRAM is kept), and `wake()` sends SLPOUT + DISPON, taking about 120 ms. If
  LDO2 is cut on purpose, call `init()` again rather than `wake()`.

**`axp202.py`: power management unit (I2C0 @ 0x35, IRQ on GPIO35).** Driver
class `AXP202(i2c, irq_pin=None)`. It covers:

- power outputs: `set_ldo2` for the backlight and panel, `set_ldo3` for audio,
  `set_ldo2_mv`, and `set_output(bit, on)`
- the battery: `battery_percent()` (fuel gauge, falling back to a voltage
  curve), `battery_voltage()` in mV, and `discharge_current_ma()` /
  `charge_current_ma()`
- USB and charging: `vbus_present()`, `vbus_voltage()` and `is_charging()`
- the **side button**, which is the AXP202 PEK key. `enable_pek(edges, vbus)`
  arms the IRQs, and `poll()` returns an `EV_*` mask (`EV_SHORT`, `EV_LONG`,
  `EV_PRESS`, `EV_RELEASE`, `EV_VBUS_IN`/`OUT`).

`status()` returns a dict, for notebooks. Gotchas:

- Register 0x12 bit1 is **DCDC3, the ESP32's own supply. Never clear it.** Every
  write to 0x12 is read-modify-write and forces DCDC3 on, and
  `set_output(BIT_DCDC3, False)` raises.
- The IRQ status registers 0x48..0x4C are write-1-to-clear. `poll()` writes back
  exactly the bits it read, so nothing that arrives in between is lost. When
  `irq_pin` is set and the line is high, `poll()` costs a single GPIO read.
- Register addresses were checked against Lewis He's MIT AXP202X_Library and
  `git show HEAD:axp202c.py`. The old port read the discharge current as 12
  bits, but it is 13 bits.

**`bma423.py`: accelerometer (I2C0 @ 0x19, or 0x18; INT1 on GPIO39).**
Accelerometer only: there is no gyro and no magnetometer. `BMA423(i2c)` probes
the address, checks that CHIP_ID is 0x13, soft-resets (a few ms, not antirez's
1 s) and configures ±4 g, 100 Hz, performance mode and an **accel-only
headerless FIFO** in stream mode. For non-blocking bring-up, pass `start=False`
and then call `begin()` and poll `ready()`.

- Reading: `fifo_read_mg()` drains the FIFO with one `readfrom_mem_into` into a
  preallocated buffer, decodes it into `self.fifo_mg` (x, y, z milli-g
  interleaved, `array('h')`) and returns the sample count. Call it every frame.
  The FIFO holds 170 frames, which is 1.7 s at 100 Hz. `read_xyz_mg()` reads
  one sample from DATA_8..13.
- Interrupts are **polled, never IRQ-driven**, so no Python IRQ handler ever
  touches the shared I2C bus. `map_interrupts(int1=EV_*...)` routes events to a
  pin (feature events need latched mode). `poll_events()` reads and clears
  INT_STATUS_0/1. With `int_pin=39` it costs only a GPIO read while INT1 is
  idle.
- The **feature engine** (on-chip step counter, activity, tap, wrist-wear) is
  optional. It needs `bma423conf.bin`: the Bosch v2.14.13 BSD-3 blob, 6,144 B
  with a sha256 check. Fetch it with `tools/fetch_bma423_config.sh`. The LilyGO
  2017 blob is rejected. `load_config()` returns False if the blob is missing or
  wrong, and `feat_error` says why; the game then uses software step detection
  (`finder/motion.py`). INIT_CTRL=1 is written at most once per reset.
- Compared with the original `bma423.py` in the repo root, this fixes the data
  interrupt map bits, INT_STATUS never being cleared, and the temperature sign
  (`raw - 256`).

**`ft6336.py`: touch (I2C1: SDA 23, SCL 32, @ 0x38; INT on GPIO38).** Driver
class `FT6336(i2c, rotation=0, mirror_x, mirror_y, int_pin, gate_int)`.
`read()` does one 5-byte burst from TD_STATUS (0x02) and returns the **same**
`[touching, x, y]` list on every call, so it allocates nothing. Copy the list
if you need to keep it. Bus errors count into `errors` and read as "not
touching". The rotation is configurable (0..3 plus mirrors). The default of 0
(identity) is right for this panel at MADCTL 0xC0: LilyGO `TTGO.h`
`getTouch()` maps rotation 2 to `x = __x, y = __y` on the standard 2020 V1.
`rotation=2` (`x' = 239 - x`, `y' = 239 - y`) is only for the older
PANEL_V1 variant. Confirm on the watch by tapping the corners. INT gating
(`gate_int=True`) relies on INT staying low for the whole touch (G_MODE 0).
That hasn't been verified on hardware, so gating is off by default.

**`haptics.py`: vibration motor (GPIO4).** Driver class `Motor`. The ERM motor
is driven by LEDC PWM at 1 kHz. `set(strength 0..1)` maps any non-zero
strength to at least `min_duty` (35%) so the motor actually spins up. It only
writes the PWM when the value changes, so calling
`motor.set(player.tick(now))` every frame is free. Patterns are ticked from the
main loop (`finder/haptic_patterns.py`) and never sleep. `deinit()` leaves the
pin driven low.

**`radio.py`: ESP-NOW broadcast.** Two classes share one interface:
`EspNowRadio` for the watch and `SimRadio` for tests and the simulator.

- `begin(channel=6, txpower=20)` activates the STA interface, disconnects from
  any AP that `boot.py` may have joined (an association pins the channel), and
  sets `channel` (1, 6 or 11 only), `txpower` and `pm=PM_NONE`. It then applies
  `rxbuf=2048, timeout_ms=0`, activates ESP-NOW and adds the broadcast peer.
- `maybe_send(now, buf)` broadcasts on a 45-55 ms jittered schedule
  (`finder.link.TxScheduler`, seeded with the MAC so two watches never
  transmit in lock-step) with `send(BCAST, buf, False)`.
- `poll(now, monitor=link)` or `poll(now, callback=fn)` drains
  `recvinto(d, 0)` into one preallocated `[mac, bytearray(250), rssi, t_ms]`
  until it returns 0, at most 32 frames per call. It doesn't use `irq()` or
  `peers_table`. The buffer passed on to the monitor or callback is shared, so
  copy anything you keep. Driver timestamps that are stale, or more than 1 s
  off, are replaced by `now`.
- `stats()` returns the tx/rx/error counters.

`network` and `espnow` are imported inside `begin()`, so the module also loads
on CPython.

**`board.py`: bring-up and ownership.** See below.

## How `Board` wires them

`Board()` only sets the CPU to 240 MHz. Every part is a lazy property, so a
notebook can touch one subsystem without bringing up the rest. `Board().init()`
creates the parts in `ORDER`:

| part | created as | bus / pins |
|---|---|---|
| `pmu` | `AXP202(i2c0, irq_pin=Pin(35))`, then LDO2 at 3.3 V on, PEK IRQs on | `i2c0` |
| `display` | `ST7789(fast=fast_spi, bl_power=pmu.set_ldo2)` | SPI(1) 18/19, cs 5, dc 27 |
| `backlight` | `display.brightness(0.6)` | GPIO12 PWM |
| `imu` | `BMA423(i2c0)` | `i2c0` |
| `touch` | `FT6336(i2c1, int_pin=38, rotation=touch_rotation)` (default 0) | `i2c1` |
| `haptics` | `Motor()` | GPIO4 PWM |
| `radio` | `EspNowRadio(channel=...)` then `.begin()` | Wi-Fi STA |

- `board.i2c0` is the **one** `machine.I2C(0)` on pins 21/22 at 400 kHz, shared
  by the AXP202, the BMA423 and the PCF8563 RTC. Never open a second I2C on
  those pins. `board.i2c1` is the touch bus on 23/32.
- If the PMU doesn't answer, the display is still built, without `bl_power`
  (this happens on USB with no battery), and the failure goes into
  `board.errors["pmu"]`. `init(strict=False)` records any failing part in
  `errors` and carries on.
- `factories={"imu": fn}` overrides any part; `fn` receives the board.
- `board.brightness(x)` and `board.button()` (which calls `pmu.poll()`) are
  shortcuts.
- `safe_boot(board)` returns `"flag"` if `/noapp` exists. It returns `"pek"`
  after a fresh double press or a roughly 1.5 s hold of the side key within
  the first second, which keeps the REPL free for mpremote. The press that
  powered the watch on never counts.

```python
from hal.board import Board, safe_boot
b = Board()
if not safe_boot(b):
    b.init()
    b.display.fill(0)
    b.radio.maybe_send(now, beacon)
```

## Running the on-watch tools

Both tools run **on the watch**. Neither ever changes firmware.

Copy the code first, using either of these:

```sh
python3 tools/deploy.py --noapp       # finder/, hal/, ui/ (+ bma423conf.bin), creates /noapp
mpremote cp -r hal :                  # hal/ only: enough for bench_display.py
```

Use `--noapp` (or safe boot) so that a `main.py` game doesn't grab the radio or
the display while a benchmark runs.

**`tools/bench_display.py`** needs `hal/__init__.py`, `hal/pins.py` and
`hal/st7789.py` on the watch. It measures:

- the real SPI clock, from `print(spi)`
- `push_frame` compared with 10 × `push_strip`
- the GS8→RGB565 palette blit per strip
- palette rebuilds
- `gc.collect()` time and bytes allocated per frame
- a full strip-rendered frame, reported as fps

Run it in one of two ways:

- **mpremote:** `mpremote run tools/bench_display.py`. The script is sent from
  the host, and `hal/` is imported from the watch.
- **Notebook** (the `MicroPython - USB` Jupyter kernel in `notebooks/`): run
  `%serialconnect --port /dev/cu.usbserial-XXXX`. Then paste the file's
  `# %%` sections one per cell, in order. Every section after `[1] setup` can
  be re-run on its own.

Leave `FAST = False` on stock firmware. The script switches LDO2 on itself,
keeping DCDC3 set.

**`tools/radio_pingpong.py`** needs two watches with `finder/` and `hal/`
deployed. `deploy.py` does not copy `tools/`, so copy the script to the root of
each watch:

```sh
mpremote cp tools/radio_pingpong.py :
# watch A (start the echo side first)
mpremote connect /dev/cu.usbserial-A exec "import radio_pingpong as pp; pp.run('pong')"
# watch B
mpremote connect /dev/cu.usbserial-B exec "import radio_pingpong as pp; pp.run('ping', n=1000, render=True)"
```

In a notebook, open one kernel per watch and run
`import radio_pingpong as pp; pp.run('pong')` (or `pp.run('ping', n=1000)`) in
a cell. `mpremote run` can't pass arguments, so it always runs the default
ping. The ping side reports:

- delivery %
- RTT
- pong inter-arrival p50/p95/max
- RSSI in both directions

`render=True` adds a full-frame SPI push per loop, with CS held high so the
display doesn't change, to measure the radio under display DMA load. `channel=`
picks 1, 6 or 11; both watches must match.

## Tests

`tests/test_hal_*.py` run the drivers against `tests/fakes/`. Call
`fakes.install()` before importing anything from `hal`. The fakes record I2C
register writes, SPI traffic and ESP-NOW frames, so tests can assert on what a
driver did.

```sh
python3 tests/runner.py                  # CPython
node tools/mpy/run.mjs tests/runner.py   # real MicroPython (WebAssembly)
python3 tests/runner.py test_hal_board   # one module
```

`test_hal_imports.py` imports every `hal/*.py` module fresh and brings up a
full `Board().init()` with the real drivers on fake devices.

## watchdog.py

`Watchdog(timeout_ms=8000, usb=False)`: reboots the watch if the game loop stops calling `feed()`.
`app.Runtime(watchdog_ms=...)` picks the mode at start-up from `pmu.vbus_present()`:

- **Battery:** `machine.WDT`. It catches hangs inside C calls too, and it cannot be stopped once started. After
  Ctrl-C on battery the watch reboots within the timeout, which is what you want in the field.
- **USB power:** a `machine.Timer(3)` check of the last feed time. `stop()` switches it off when the loop exits,
  so the REPL stays usable at the desk.

`main.py` enables it (8 s). Notebooks call `app.run()` without a watchdog. The safe-boot check runs first, so a crash
loop can always be escaped with the side key at power-on.
