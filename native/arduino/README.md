# native/arduino: graphics libraries compared

One PlatformIO env per graphics library. Each compiles `src/bench_main.cpp`
(setup, loop, I2C) and its own `src/env_<library>.cpp`, runs the shared bench
(`native/core`, `hm::Bench`) and prints the same `HM` lines, so every env lands
in one table.

All envs run on Arduino-ESP32 2.0.17 (IDF 4.4), so only the library differs.

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
