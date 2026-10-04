# native/: Arduino and ESP-IDF ports

Work in progress. Today this is a **display and motion-sensor benchmark** built
for two runtimes from one portable C++ core, to find out where MicroPython's
frame time goes and how much a native runtime gains. The game itself has not
been ported yet. MicroPython (the rest of the repo) is untouched.

| Path | What |
|---|---|
| `core/` | Portable C++17 with no hardware calls. The ripple field (a port of `ui/field.py`, checked frame by frame against the MicroPython renderer), the ST7789, AXP202 and BMA423 command sequences (as `hal/*.py`), and `hm::Bench`, the benchmark every runtime runs. `include/hm/tuning.h` and `field_tables.h` are generated. |
| `esp32_shared/` | ESP32 clock, serial log, backlight PWM and the motion-sensor task on core 0. Plain ESP-IDF calls, so both builds share it. |
| `arduino/` | PlatformIO: Arduino-ESP32 2.0.17 (IDF 4.4), LovyanGFX's SPI bus with DMA. |
| `idf/` | PlatformIO: ESP-IDF 5.5, the `esp_lcd` SPI panel IO with DMA. |
| `test/` | Host tests (g++ with address and UB sanitizers) on fake hardware, plus the golden palettes. |
| `tools/` | `gen_tuning_h.py` (headers), `golden_field.py` (palettes from the real renderer), `capture.py` (serial log), `qemu_run.py` (boot a build in QEMU). |

Both builds use pins and settings from `hal/pins.py`: SPI on HSPI with SCK 18,
MOSI 19, CS 5, DC 27 and no MISO (GPIO12 is the backlight), MADCTL 0xC0 with
GRAM rows 80..319, I2C0 on 21/22 at 400 kHz. The renderer runs on core 1 and
the sensor task on core 0. The PSRAM workaround flags are off: this watch's
ESP32-D0WDQ6-V3 is rev3 silicon, and the benchmark needs no PSRAM.

## Checks

```sh
python3 native/test/run.py                      # host tests (tests/test_native.py runs them too)
python3 native/tools/gen_tuning_h.py --check    # headers match finder/tuning.py and ui/field.py
node tools/mpy/run.mjs native/tools/golden_field.py > native/test/golden_field.txt   # after a ui/field.py change
```

## Build

```sh
python3 -m pip install platformio     # once; the first build downloads the toolchains
pio run -d native/arduino             # -> native/arduino/.pio/build/bench-lovyangfx/firmware.bin
pio run -d native/idf                 # -> native/idf/.pio/build/bench-esplcd/firmware.bin
```

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
pio run -d native/arduino -t upload --upload-port $PORT
python3 native/tools/capture.py $PORT logs/bench-arduino-A.log --seconds 150
pio run -d native/idf -t upload --upload-port $PORT
python3 native/tools/capture.py $PORT logs/bench-idf-A.log --seconds 150
```

`capture.py` restarts the watch through the USB serial reset line, so the log
starts at boot, and stops at `HM done` (about 80 s). Only Python's standard
library is needed. Close any serial monitor first. After `HM done` the watch
keeps showing the HOT field at 20, 30 and 60 fps in turn, 10 s each, for
judging smoothness by eye.

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
| `HM error what=` | a step that could not run (for example `spi_clock`) |
| `HM done` | the benchmark finished |
