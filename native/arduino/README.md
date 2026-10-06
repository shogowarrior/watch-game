# native/arduino: the game and graphics libraries on Arduino-ESP32

One PlatformIO env per graphics library. Each compiles `src/bench_main.cpp`
(setup, loop, I2C) and its own `src/env_<library>.cpp`, runs the shared bench
(`native/core`, `hm::Bench`) and prints the same `HM` lines, so every env lands
in one table.

All envs run on Arduino-ESP32 2.0.17 (IDF 4.4), so only the library differs.
`game` runs the game itself, and `radio-pingpong` and `platform-check` check
the ESP-NOW radio and the rest of the game's hardware (below).

| Env | Library | How a frame goes out |
|---|---|---|
| `bench-lovyangfx` | LovyanGFX 1.2.32, `lgfx::Bus_SPI` | DMA per strip; the next strip is drawn meanwhile |
| `bench-tft-espi` | TFT_eSPI 2.5.43 | `pushPixelsDMA` per strip, overlapped the same way |
| `bench-lvgl` | LVGL 9.6.0 on LovyanGFX's bus | LVGL renders into two 240x24 buffers, one window each; a draw unit paints the field image |
| `bench-arduino-gfx` | Arduino_GFX 1.5.9, `Arduino_ESP32SPIDMA` | DMA, but each write blocks: no overlap |

Every env draws the field with the same core code, so the picture is the
MicroPython one; the libraries differ in how pixels reach the panel and, for
LVGL, in what it costs to own the frame.

## Gotchas

- **MISO.** Arduino-ESP32's `SPIClass::begin` maps MISO -1 on HSPI to GPIO12,
  the backlight and a boot strapping pin. TFT_eSPI always calls it, so its
  setup (`include/tft_setup.h`) names GPIO36 (input-only, unused) instead.
  LovyanGFX and Arduino_GFX's DMA bus skip MISO properly.
- **TFT_eSPI setup.** The library reads `include/tft_setup.h` only when it can
  see `include/` (`-I $PROJECT_INCLUDE_DIR`); without it, it silently builds
  its default ILI9341 setup.
- **Clocks.** TFT_eSPI reads its clock from a variable (`SPI_FREQUENCY` in
  `tft_setup.h`) and re-adds its DMA device on a change. Arduino_GFX takes the
  clock once, in `begin()`; the env writes the SPI clock register, which the
  IDF driver leaves alone while its device is the bus's only one.
- **Arduino_GFX 1.6 needs Arduino-ESP32 3.x** (`esp32-hal-periman.h`); 1.5.9 is
  the newest that builds on 2.0.17, with the same blocking DMA bus.
- **Reset reason under QEMU.** Arduino's bootloader runs into TG0/TG1 watchdog
  resets there, so the app's first boot reads `esp_reset_reason()` 6 (task
  watchdog). `src/task_watchdog.cpp` also checks a marker in RTC memory.

- **Static RAM.** Arduino-ESP32 2.0.17's prebuilt libraries keep Bluetooth on,
  which reserves the first 56 KB of the ESP32's static data RAM, leaving about
  124 KB: too little for the game loop's 105 KB of state. `hm::esp::start_game`
  takes it from the heap instead, whose largest internal block is about 110 KB
  at that point.

## Check and build

```sh
pio pkg install -d native/arduino -e bench-lvgl   # once: LVGL for the host check
python3 native/arduino/test/lvgl_host.py          # LVGL frames == the strip loop's, pixel for pixel
pio run -d native/arduino                         # every env; one: -e bench-tft-espi
```

## Flash and capture (watch A, one env at a time)

```sh
PORT=/dev/cu.usbserial-022152D1
for e in bench-lovyangfx bench-tft-espi bench-lvgl bench-arduino-gfx; do
  pio run -d native/arduino -e $e -t upload --upload-port $PORT &&
  python3 native/tools/capture.py $PORT logs/bench-$e-A.log --seconds 150
done
```

## Radio check (`radio-pingpong`)

Arduino-ESP32 2.0.17 is IDF 4.4, whose ESP-NOW receive callback carries no
RSSI. The shared radio (`native/esp32_shared`, `hm/esp32_io.h`) reads it from
the radio header just in front of the payload, as Espressif's esp-now
component does for IDF < 5, and checks that header's channel (`hdr_bad`
counts misses). `radio-pingpong` checks that
reading on the watch: it sends 1000 pings, 50 ms apart on channel 6 at 20 dBm,
to a MicroPython watch running the pong side of `tools/radio_pingpong.py`. It
reports delivery, round trip and gaps, and RSSI both ways: `where=rx` is what
this build read, `where=peer` what MicroPython measured on the same link. They
should agree within a few dB, with `hdr_bad=0` and `sig_extra=43` (the frame's
air length minus its payload). `test/pingpong_host.py` (also run by
`tests/test_native_arduino.py`) runs the pinger beside the Python one and
checks they send the same bytes and report the same numbers.

QEMU has no Wi-Fi model: the env boots to `HM hello` there, and any Wi-Fi
start crashes it, the standard `WiFi.mode(WIFI_STA)` included.

Watch A takes the env; watch B keeps MicroPython with `finder/` and `hal/`
deployed. Flash A first (it pings into the void once), then start B's pong,
which waits up to 60 s for the first ping, then capture A, which restarts it:

```sh
A=/dev/cu.usbserial-022152D1; B=/dev/cu.usbserial-02215408
pio run -d native/arduino -e radio-pingpong -t upload --upload-port $A
mpremote connect $B cp tools/radio_pingpong.py :
mpremote connect $B exec "import radio_pingpong as pp; pp.run('pong')" > logs/radio-pingpong-B.log &
python3 native/tools/capture.py $A logs/radio-pingpong-A.log --seconds 120
```

## Platform check (`platform-check`)

The game's hardware below the display: both I2C buses (`src/wire_i2c.h`; bus 0
holds the AXP202, BMA423 and RTC, bus 1 the touch panel), the interrupt lines
and the motor (shared, `hm/esp32_io.h`), and the loop watchdog
(`src/task_watchdog.h`, 8 s as in `main.py`). It feeds the watchdog for 3 s,
then stops feeding it: the watch must reboot, say so and end.

```sh
A=/dev/cu.usbserial-022152D1
pio run -d native/arduino -e platform-check -t upload --upload-port $A
python3 native/tools/capture.py $A logs/platform-check-A.log --seconds 60
```

Expected on watch A, with one short buzz:

```
HM i2c bus=0 found=0x19,0x35,0x51 missing=none   (0x18 for 0x19 is fine)
HM i2c bus=1 found=0x38 missing=none
HM lines ok=1 axp202=0 touch=0 bma423=0
HM motor ok=1 buzz_ms=100
HM watchdog starving: a reboot should follow within 8 s
HM watchdog rebooted=1 reset_reason=6            (6: task watchdog, 4: panic)
HM done
```

The lines are read once and are active low, so 0 is idle; `axp202=1` can be an
interrupt latched before boot, which the game clears and this check does not.
QEMU has none of the devices, so there the check passes at the watchdog:
`python3 native/tools/qemu_run.py native/arduino --env platform-check --until
"Task watchdog got triggered"` (its reboot also reaches `HM watchdog
rebooted=1`, but `qemu_run.py` fails any reboot).

## The game (`game`)

`src/game_main.cpp` starts the task watchdog (8 s, then a panic and a reboot),
I2C0 and I2C1 (`src/wire_i2c.h`) and the panel's SPI bus (the shared
`hm/spi_lcd_bus.h`: spi_master, DMA), then hands them to `hm::esp::start_game`
(`native/esp32_shared`). That is the ESP-IDF build's bring-up and loop
(`native/idf/game`): the parts in `hal/board.py`'s order and an `HM parts` line,
then the loop on core 1 with an `HM fps` line every 10 s and one `HM mem` line.

```sh
python3 native/tools/qemu_run.py native/arduino --env game-qemu --until "HM mem"   # no radio; pixels go nowhere
```

On watch A (watch B keeps MicroPython's game, which speaks the same beacons):

```sh
A=/dev/cu.usbserial-022152D1
pio run -d native/arduino -e game -t upload --upload-port $A
python3 native/tools/capture.py $A logs/game-arduino-A.log --until "HM mem" --seconds 40
```

Expected: `HM parts pmu=1 display=1 imu=1 touch=1 haptics=1 radio=1
lcd_hz=40000000` (26666667 if the bus refuses 40 MHz), then an `HM fps` line
and the `HM mem` line, with no `HM error` and no reboot.
