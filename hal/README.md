# hal/: T-Watch 2020 V1 drivers

Pure-Python drivers for the **LILYGO T-Watch 2020 V1** on **stock MicroPython
v1.29.0** (`ESP32_GENERIC-SPIRAM`). The stock firmware has no `st7789` C module
and no frozen `axp202c`, so everything here is plain `.py`. Only `hal/` may
import `machine`, `network` or `espnow`. Game logic in `finder/` stays pure and
gets its data from these drivers. The drivers take `const` and the tick helpers
from `finder/compat.py`, and the radio its send schedule from `finder/link.py`,
so `hal/` alone does not run on a watch: deploy `finder/` with it. Every driver
takes its bus (`i2c`, `spi`, pins) as an optional argument, so tests can inject
the fakes in `tests/fakes/` and the drivers run unchanged on CPython and
MicroPython.

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
palette), then push them with `push_strip(y0, h, buf)` (a full-width strip)
or `push_frame(fb)` (115,200 B sent as strip-sized writes in one CS-low burst;
the slice list is cached, so it allocates nothing). A strip that starts where
the last one ended continues its window (CS stays low, no new command), so 10
strips top to bottom cost what `push_frame` does (44.1 ms on the watch, against
52.4 ms with a window per strip and 37.3 ms as one write; the wire alone takes
34.6 ms at 26.67 MHz). Every write is copied from the PSRAM heap into an
internal DMA buffer by ESP-IDF, so a write costs about 0.7 ms on top of the
wire. A send thread was tried (4 Oct 2026) and measured no faster: the caller's
drawing holds the GIL that the thread needs to queue each DMA chunk, so strips
go out in the caller. You can also paint directly
with `fill_rect` or `fill`. The SPI bus is SPI(1) with sck 18, mosi 19, cs 5 and
dc 27. There is **no reset pin**, so `init()` does a software reset: SWRESET,
SLPOUT, COLMOD 0x55, MADCTL **0xC0** with a **row offset of 80** (the upright
orientation, using GRAM rows 80..319 of 320), INVON, NORON, a black frame (GRAM
is random after power-on and SWRESET keeps it), DISPON. `init()` blocks for
about 340 ms. Gotchas:

- The SPI is always created with `miso=None`. Otherwise HSPI's default MISO,
  GPIO12, gets claimed, and GPIO12 is the backlight.
- Pins 18/19 go through the GPIO matrix. Asking for more than 26.67 MHz on
  stock firmware hits an unhandled `ESP_ERR_NOT_SUPPORTED` and crashes, so the
  constructor raises `ValueError` unless you pass `fast=True` (custom build
  only). A full frame takes about 35 ms at 26.67 MHz.
- The panel wants RGB565 MSB-first, but `framebuf` stores it little-endian.
  Every colour you give to framebuf, a palette or `fill` must be
  **byte-swapped**. Use `rgb565(r, g, b)` or `swap16(c)`.
- The backlight is PWM on GPIO12 (`brightness(0..1)`), but on the V1 the
  AXP202 **LDO2** powers both the backlight and the panel itself. LDO2 must be
  on before `init()`, or the init commands are lost. Pass
  `bl_power=pmu.set_ldo2` and `init()` switches it on first. The driver never
  switches LDO2 off: `sleep()` sets the duty to 0 and sends DISPOFF + SLPIN
  (GRAM is kept). `wake()` first waits out the rest of 120 ms after SLPIN, then
  sends SLPOUT, waits 120 ms and sends DISPON. Pass `wait=` the main loop's idle
  so the motor keeps its timing during those waits. If LDO2 is cut on purpose,
  call `init()` again rather than `wake()`.

**`axp202.py`: power management unit (I2C0 @ 0x35, IRQ on GPIO35).** Driver
class `AXP202(i2c, irq_pin=None)`. It covers:

- power outputs: `set_ldo2` for the backlight and panel, `set_ldo3` for audio,
  `set_ldo2_mv`, and `set_output(bit, on)`
- the battery: `battery_percent()` (fuel gauge, falling back to a voltage
  curve), `battery_voltage()` in mV, and `discharge_current_ma()` /
  `charge_current_ma()`
- USB and charging: `vbus_present()`, `vbus_voltage()` and `is_charging()`
- the **side button**, which is the AXP202 PEK key. `enable_pek(edges, vbus)`
  arms the IRQs, `set_long_press_ms(ms)` sets the long-press time (REG 0x36
  bits 5:4; `Runtime.begin` sets it from tokens `input.button_long_ms`), and
  `poll()` returns an `EV_*` mask (`EV_SHORT`, `EV_LONG`, `EV_PRESS`,
  `EV_RELEASE`, `EV_VBUS_IN`/`OUT`)
- power-off: `shutdown()` sets REG 0x32 bit 7. The game calls it only after
  `game.power_off`, and the runtime retries it 3 times.

`status()` returns a dict, for notebooks. Gotchas:

- Register 0x12 bit1 is **DCDC3, the ESP32's own supply. Never clear it.** Every
  write to 0x12 is read-modify-write and forces DCDC3 on, and
  `set_output(BIT_DCDC3, False)` raises.
- The IRQ status registers 0x48..0x4C are write-1-to-clear. `poll()` reads all
  five before it clears any, writes back exactly the bits it read, and reports
  a register only once its clear went through, so neither a bus error nor a bit
  that arrives in between loses an event (a failed read or clear leaves the
  bits latched for the next poll). When
  `irq_pin` is set and the line is high, `poll()` costs a single GPIO read.
- Register addresses were checked against Lewis He's MIT AXP202X_Library and
  the old root driver (`git show 44880ab^:axp202c.py`, removed in 44880ab). The
  old port read the discharge current as 12 bits, but it is 13 bits.

**`bma423.py`: accelerometer (I2C0 @ 0x19, or 0x18; INT1 on GPIO39).**
Accelerometer only: there is no gyro and no magnetometer. `BMA423(i2c)` probes
the address, checks that CHIP_ID is 0x13, soft-resets (a few ms, not the 1 s
of antirez's original driver) and configures ±4 g, 100 Hz, performance mode and an **accel-only
headerless FIFO** in stream mode. For non-blocking bring-up, pass `start=False`
and then call `begin()` and poll `ready()`.

- Reading: `fifo_read_mg()` drains the FIFO with one `readfrom_mem_into` into a
  preallocated buffer, decodes it into `self.fifo_mg` (x, y, z milli-g
  interleaved, `array('h')`) and returns the sample count. Call it every frame.
  The decode is compiled with `@micropython.viper` on the watch
  (`DECODE_KERNEL`, after a self-check against the plain version): about
  45 us a sample in Python, which at 800 Hz is 36 ms of every second.
  The FIFO holds 170 frames, which is 1.7 s at 100 Hz (212 ms at 800 Hz).
  `set_odr(hz)` changes the rate while running and empties the FIFO.
  `read_xyz_mg()` reads one sample from DATA_8..13.
- `z_sign` records how the board mounts the chip (`hal/board.py` passes
  `pins.BMA423_Z_SIGN`, -1 on the V1: face-up reads z = -1 g). Samples stay in
  the chip's frame; `app/imu_feed.py` applies it.
- Interrupts are **polled, never IRQ-driven**, so no Python IRQ handler ever
  touches the shared I2C bus. `map_interrupts(int1=EV_*...)` routes events to a
  pin (feature events need latched mode). `poll_events()` reads and clears
  INT_STATUS_0/1.
- The **feature engine** (on-chip step counter, activity, tap, wrist-wear) is
  optional. It needs `bma423conf.bin`: the Bosch v2.14.13 BSD-3 blob, 6,144 B
  with a sha256 check. Fetch it with `tools/fetch_bma423_config.sh`. The LilyGO
  2017 blob is rejected. `load_config()` returns False if the blob is missing or
  wrong, and `feat_error` says why; the game then uses software step detection
  (`finder/motion.py`). INIT_CTRL=1 is written at most once per reset. The game
  loop uses `start_features()` (a non-blocking `load_config`) and then polls
  `poll_features()`: the first FEAT_OK after each reset switches on the step
  counter, activity and wrist-wear, latched on INT1 (a soft reset wipes them).
  A bus error mid-upload leaves INIT_CTRL unset, so `app/runtime.py` retries
  `start_features()` up to 3 times at boot; a bus error in `poll_features()` is
  retried by the 1 s poll. The wrist-wear gesture runs with Bosch's default axes
  remap, which is unverified on the T-Watch (`docs/hardware-setup.md` §6).
  `load_config()` alone starts the engine but switches no feature on, so
  `steps()` reads 0 until `poll_features()` runs. `Board()` soft-resets the
  chip, so nothing loads the blob until one of these runs.
- Compared with antirez's original `bma423.py` (formerly in the repo root,
  removed in 44880ab), this fixes the data interrupt map bits, INT_STATUS never
  being cleared, and the temperature sign (`raw - 256`).

**`ft6336.py`: touch (I2C1: SDA 23, SCL 32, @ 0x38; INT on GPIO38).** Driver
class `FT6336(i2c, rotation=0, mirror_x, mirror_y, int_pin, gate_int)`.
`read()` does one 5-byte burst from TD_STATUS (0x02) and returns the **same**
`[touching, x, y, contacts]` list on every call, so it allocates nothing
(`contacts` 2 is a multi-touch, which the game ignores). Copy the list
if you need to keep it. Bus errors count into `errors` (`touch_errors` in
`Runtime.stats()`) and read as "not touching". The rotation is configurable (0..3 plus mirrors). The default of 0
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
`EspNowRadio` for the watch and `SimRadio` for tests (the simulator models the
link in `sim/radio.py`).

- `begin(channel=6, txpower=20)` activates the STA interface, disconnects from
  any AP that a notebook may have joined (an association pins the channel), and
  sets `channel` (1, 6 or 11 only), `txpower` and `pm=PM_NONE`. It then applies
  `rxbuf=2048, timeout_ms=0`, activates ESP-NOW and adds the broadcast peer.
- `maybe_send(now, buf)` broadcasts with `send(BCAST, buf, False)` on a
  jittered schedule (`finder.link.TxScheduler`, seeded with the MAC so two
  watches never transmit in lock-step). The default is 20 Hz (50 ± 5 ms);
  `set_rate(hz)` sets the period to `1000 // hz` ms with about 10 % jitter, and
  `app.runtime` sets it from `game.beacon_hz` (5-20 Hz). `due(now)` and
  `next_due()` let the loop plan its sleep.
- `poll(now, callback=fn)` drains `recvinto(d, 0)` into one preallocated
  `[mac, bytearray(250), rssi, t_ms]` until it returns 0, at most 32 frames per
  call, and calls `fn(mac, buf, n, rssi, t_rx)` per frame. It doesn't use
  `irq()` or `peers_table`. The buffer passed on to the callback is shared, so
  copy anything you keep. Driver timestamps that are stale, or more than 1 s
  off, are replaced by `now`.
- `stats()` returns the tx/rx/error counters.

`network` and `espnow` are imported inside `begin()`, so the module also loads
on CPython.

**`watchdog.py`: loop watchdog.** `Watchdog(timeout_ms=8000, usb=False)`
reboots the watch if the game loop stops calling `feed()`. `app.run(board,
watchdog_ms=8000)` (`app.runtime.Runtime`, started by `main.py`) feeds it once
per loop pass and picks the mode:

- **USB power:** a `machine.Timer(3)` check of the last feed time. `stop()`
  switches it off when the loop exits, so the REPL stays usable at the desk.
- **Battery:** `machine.WDT`. It catches hangs inside C calls too, and it cannot
  be stopped once started, so after Ctrl-C the watch reboots within the
  timeout. A game started on USB switches to it once, for good, at the first
  10 s battery reading without VBUS. A game started on battery keeps it even
  after USB is plugged in, which is why `tools/deploy.py` hard-resets the watch
  first.

Notebooks call `app.run()` without a watchdog. The safe-boot check runs first,
so a crash loop can always be escaped with the side key at power-on.

**`board.py`: bring-up and ownership.** See below.

## How `Board` wires them

`Board()` only sets the CPU to 240 MHz. Every part is a lazy property, so a
notebook can touch one subsystem without bringing up the rest. `Board().init()`
creates the parts in `ORDER`:

| part | created as | bus / pins |
|---|---|---|
| `pmu` | `AXP202(i2c0, irq_pin=Pin(35))`, then LDO2 at 3.3 V on, PEK IRQs on, latched IRQs from before boot cleared | `i2c0` |
| `display` | `ST7789(fast=fast_spi, bl_power=pmu.set_ldo2)` | SPI(1) 18/19, cs 5, dc 27 |
| `backlight` | `display.brightness(0.6)` | GPIO12 PWM |
| `imu` | `BMA423(i2c0)` | `i2c0` |
| `touch` | `FT6336(i2c1, int_pin=38, rotation=touch_rotation)` (default 0) | `i2c1` |
| `haptics` | `Motor()` | GPIO4 PWM |
| `radio` | `EspNowRadio(channel=...)` then `.begin()` | Wi-Fi STA |

- `board.i2c0` is the **one** `machine.I2C(0)` on pins 21/22 at 400 kHz, shared
  by the AXP202, the BMA423 and the PCF8563 RTC. Never open a second I2C on
  those pins. `board.i2c1` is the touch bus on 23/32.
- If the PMU doesn't answer (an I2C0 fault: the AXP202 powers the ESP32, so it
  is always on), the display is still built, without `bl_power`, and shows
  only if LDO2 is already on; the failure goes into `board.errors["pmu"]`.
  `init(strict=False)` records any failing part in `errors` and carries on.
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

Copy the code first:

```sh
python3 tools/deploy.py --noapp       # app/, finder/, hal/, ui/ (+ bma423conf.bin), creates /noapp
```

`hal/` alone is not enough (the drivers import `finder/compat.py`). Use
`--noapp` (or safe boot) so that a `main.py` game doesn't grab the radio or the
display while a benchmark runs.

**`tools/bench_display.py`** needs `hal/`, `finder/` and `ui/` on the watch. It
measures:

- the real SPI clock, from `print(spi)`
- `push_frame` compared with 10 × `push_strip`
- the GS8→RGB565 palette blit per strip
- palette rebuilds
- `gc.collect()` time and bytes allocated per frame
- a full strip-rendered frame, reported as fps

Run it in one of two ways:

- **mpremote:** `mpremote run tools/bench_display.py`. The script is sent from
  the host, and the modules are imported from the watch.
- **Notebook** (the `MicroPython - USB` Jupyter kernel in `notebooks/`): run
  `%serialconnect --port /dev/cu.usbserial-XXXX`. Then paste the file's
  `# %%` sections one per cell and run them in order once; after that any
  section can be re-run on its own.

Leave `FAST = False` on stock firmware. The script uses `Board`, so LDO2 and
DCDC3 are handled by `hal/axp202.py`.

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
