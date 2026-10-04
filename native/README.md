# native/: Arduino and ESP-IDF ports

Work in progress. Today this is a **display and motion-sensor benchmark** built
for two runtimes from one portable C++ core, to find out where MicroPython's
frame time goes and how much a native runtime gains. The game itself has not
been ported yet. MicroPython (the rest of the repo) is untouched.

| Path | What |
|---|---|
| `core/` | Portable C++17 with no hardware calls. The ripple field (a port of `ui/field.py`, checked frame by frame against the MicroPython renderer), the ST7789, AXP202 and BMA423 command sequences (as `hal/*.py`), and `hm::Bench`, the benchmark every runtime runs. `include/hm/tuning.h` and `field_tables.h` are generated. |
| `esp32_shared/` | ESP32 clock, serial log, backlight PWM and the motion-sensor task on core 0. Plain ESP-IDF calls, so both builds share it. |
| `arduino/` | PlatformIO: Arduino-ESP32 2.0.17 (IDF 4.4), one env per graphics library (LovyanGFX, TFT_eSPI, Arduino_GFX, LVGL): see `arduino/README.md`. |
| `idf/` | PlatformIO: ESP-IDF 5.5, one environment per way of driving the panel: the `esp_lcd` SPI panel IO with DMA (`bench-esplcd`, which also checks a faster BMA423 I2C clock) and SPI2's registers with DMA (`bench-regdma`). `components/hm_idf/` holds the I2C0 and SPI buses they share; its `portable/` (the I2C0 check) runs in the host tests. |
| `idf/lvgl/` | PlatformIO: LVGL 9.5 through esp_lvgl_port 2.9 on the same `esp_lcd` bus (`bench-lvgl`). The field is an LVGL image a custom decoder fills from the ring map, so its pixels are the other builds' ones; the HOT chips are LVGL labels; and a third scene has LVGL draw rings itself as arcs. |
| `micropython/` | `hmlcd`, a C user module for a custom MicroPython 1.29 build: the screen push on core 0 from internal DMA buffers, from the shared ST7789 code and `esp32_shared`'s spi_master bus. |
| `test/` | Host tests (g++ with address and UB sanitizers) on fake hardware, plus the golden palettes. `run.py` also builds the ports' portable code and tests (`idf/test/`). |
| `tools/` | `gen_tuning_h.py` (headers), `golden_field.py` (palettes from the real renderer), `capture.py` (serial log), `qemu_run.py` (boot a build in QEMU). |

Both builds use pins and settings from `hal/pins.py`: SPI on HSPI with SCK 18,
MOSI 19, CS 5, DC 27 and no MISO (GPIO12 is the backlight), MADCTL 0xC0 with
GRAM rows 80..319, I2C0 on 21/22 at 400 kHz. The renderer runs on core 1 and
the sensor task on core 0. The PSRAM workaround flags are off: this watch's
ESP32-D0WDQ6-V3 is rev3 silicon, and the benchmark needs no PSRAM. LVGL and
esp_lvgl_port come from the ESP component registry on the first build, at the
versions in `idf/lvgl/dependencies.lock`.

## Checks

```sh
python3 native/test/run.py                      # host tests (tests/test_native.py runs them too)
python3 native/tools/gen_tuning_h.py --check    # headers match finder/tuning.py and ui/field.py
node tools/mpy/run.mjs native/tools/golden_field.py > native/test/golden_field.txt   # after a ui/field.py change
```

## Build

```sh
python3 -m pip install platformio     # once; the first build downloads the toolchains
pio run -d native/arduino             # every library env -> native/arduino/.pio/build/<env>/firmware.bin
pio run -d native/idf                 # -> native/idf/.pio/build/{bench-esplcd,bench-regdma}/firmware.bin
pio run -d native/idf/lvgl            # -> native/idf/lvgl/.pio/build/bench-lvgl/firmware.bin
```

## MicroPython with a C module (`micropython/`)

Stock MicroPython keeps every buffer in PSRAM, which the SPI DMA cannot read,
and sends the frame while the drawing waits: the push is about 40 ms of a
~90 ms frame. `hmlcd` sends a finished frame from a task on core 0 instead,
copying 20 rows at a time into two internal DMA buffers (`hm::push_bounced`),
while MicroPython on core 1 draws the next frame into a second buffer. Its
bus is half duplex, which ESP-IDF lets run past 26.67 MHz on these pins (the
bench tries 40 and 80; whether the panel keeps up is for the watch to show). The panel setup stays in `hal/st7789.py`.

```sh
# once: MicroPython v1.29.0 with its esp32 submodules, mpy-cross, and ESP-IDF 5.5 in the shell
git clone --depth 1 -b v1.29.0 https://github.com/micropython/micropython && cd micropython
make -C mpy-cross && make -C ports/esp32 BOARD=ESP32_GENERIC submodules
make -C ports/esp32 BOARD=ESP32_GENERIC BOARD_VARIANT=SPIRAM BUILD=build-hm \
    USER_C_MODULES=$REPO/native/micropython/micropython.cmake    # -> build-hm/firmware.bin (at 0x1000)
```

Flashing it is the user's call, like any firmware. It replaces only the
firmware, so the files on the watch stay. Then `mpremote run
tools/bench_hmlcd.py` prints `HM mpy` lines: the game's renderer timed with the
stock push, then with `hmlcd` at 26.67, 40 and 80 MHz, plus a test card at each
clock (`tests/test_bench_hmlcd.py` checks it never draws into a frame that is
still being sent). In QEMU the firmware boots and `hmlcd.init` and `hmlcd.cmd`
work, but a push never finishes: QEMU's SPI model has no DMA.

## Boot in QEMU before flashing

```sh
python3 native/tools/qemu_run.py --install        # once: Espressif's QEMU, sha256-checked (Linux: apt-get install libslirp0)
python3 native/tools/qemu_run.py native/idf       # or native/arduino, or a build dir; --env picks one of several builds
```

It checks the flash layout first (bootloader below the partition table at
0x8000, app inside its partition, all inside the flash), then boots the image
in Espressif's ESP32 emulator and passes at the first `HM hello` line. It fails
on a panic, an abort, a stack overflow, a reboot after the app started, or an
`HM error` line. Run it on every build before it goes to a watch: it caught an
ESP-IDF bootloader that had grown past 0x8000 and would not have booted.

QEMU emulates the ESP32 only. It has no model of the watch's screen, PMU or
motion sensor (the bench stops at `HM error what=axp202`), and it does not keep
the chip's timing: code runs as fast as the host allows and SPI transfers take
no time. So it checks that a build boots and runs, never how fast: fps and
push times come from the watch. The Arduino 2.0.17 bootloader resets a few
times under QEMU before it starts the app; that does not happen on the watch.

## Flash and capture (one watch at a time)

Flashing replaces MicroPython on that watch. Only do it when the user has asked.

```sh
PORT=/dev/cu.usbserial-022152D1       # watch A (watch B: /dev/cu.usbserial-02215408)
pio run -d native/arduino -e bench-lovyangfx -t upload --upload-port $PORT   # other envs: arduino/README.md
python3 native/tools/capture.py $PORT logs/bench-arduino-A.log --seconds 150
pio run -d native/idf -e bench-esplcd -t upload --upload-port $PORT
python3 native/tools/capture.py $PORT logs/bench-idf-esplcd-A.log --seconds 180
pio run -d native/idf -e bench-regdma -t upload --upload-port $PORT
python3 native/tools/capture.py $PORT logs/bench-idf-regdma-A.log --seconds 150
pio run -d native/idf/lvgl -t upload --upload-port $PORT
python3 native/tools/capture.py $PORT logs/bench-idf-lvgl-A.log --seconds 90
```

`capture.py` restarts the watch through the USB serial reset line, so the log
starts at boot, and stops at `HM done` (about 80 s). Only Python's standard
library is needed. Close any serial monitor first. After `HM done` the watch
keeps showing the HOT field at 20, 30 and 60 fps in turn, 10 s each, for
judging smoothness by eye (the LVGL build: its chips over the field, then its
arcs, at 30 fps). The I2C0 check in `bench-esplcd` adds about 20 s before the
display steps; keep the watch still then. If the watch switches off during it,
the faster clock upset the power chip: press the side button to turn it on.

To go back to MicroPython: `tools/flash.sh <port>` (it erases the flash), then
`python3 tools/deploy.py --port <port>`.

## The log

One line per measurement, `HM <step> key=value ...`:

| Line | Meaning |
|---|---|
| `HM hello variant= framework=` | which build is running |
| `HM compose fixture= step_us= blit_us=` | CPU per frame: field state and palette, then the 10 strip blits |
| `HM push hz= wire_us= serial_us= overlap_us= floor_us=` | per frame at one SPI clock: pixels alone, draw-then-send (as MicroPython does), draw while the previous strip is on the wire, and the theoretical wire time |
| `HM window hz= w= h= n= frame_us= us_per_window= ns_per_px=` | partial redraw: one screen sent as `n` tiles of `w` x `h`, each its own window; the cost of a window and of a pixel |
| `HM run hz= target= fps= p50_us= p95_us= max_us= sd_us= miss= work_us=` | 3 s of HOT frames paced to `target` fps (0 = as fast as possible; the locks are 10, 20 and 30, which divide every zone period, plus 40 and 60 at 40 MHz for the ceiling): frame-interval percentiles, misses, CPU per frame |
| `HM run_imu_task` / `HM run_imu_inline` + `HM imu where= rate= full= fifo_max= read_ms_per_s=` | 30 fps for 5 s with the BMA423 at 800 Hz, drained by a task on the other core, then from the render loop as MicroPython does |
| `HM i2c bma_hz= armed= rate= us_per_sample= read_ms_per_s= imu_errors= full= peak_mg= probes= probe_errors= changed= bad_time= after_errors= after_changed= after_bad_time=` | `bench-esplcd` only, at 400 kHz, 700 kHz and 1 MHz: the BMA423 at 800 Hz drained by the sensor task at that clock for 5 s while the AXP202's and the RTC's registers are read back at 400 kHz every 10 ms (`probes`), then again with the BMA423 back at 400 kHz (`after_`). Clean: `armed=1` and the error, `changed` and `bad_time` counts all 0 |
| `HM lvgl buf_rows= heap_free= heap_min=` | LVGL's draw buffers (two of 240 x `buf_rows`) and the heap left |
| `HM run_chips` / `HM run_arcs`, each run + `HM lvgl step= target= refr_us= wait_us= cpu_us=` | LVGL only: after the `HM run` lines of the field alone, the field with the HOT chips over it, then LVGL's own arcs. The `HM lvgl` line after each run splits a frame's time inside LVGL's refresh into waiting for the DMA and the rest (drawing) |
| `HM error what=` | a step that could not run (for example `spi_clock`) |
| `HM done` | the benchmark finished |
