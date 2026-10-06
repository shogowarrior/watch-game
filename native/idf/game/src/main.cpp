// The game on ESP-IDF: the buses, then hm::esp::start_game (hm/esp32_game.h:
// hal/board.py's bring-up and app/runtime.py's loop, shared with the Arduino
// build). The task watchdog comes up from sdkconfig.defaults.
//
// HM_QEMU (env game-qemu): Espressif's QEMU has no Wi-Fi model and its SPI has
// no DMA, so that build leaves the radio out and sends the panel's pixels
// nowhere; the rest runs as on the watch, so qemu_run.py can watch the loop
// reach its first fps line (--until "HM fps").
#include <stdio.h>

#include "esp_system.h"
#include "hm/esp32.h"
#include "hm/esp32_game.h"
#include "hm/idf.h"

namespace {

#ifdef HM_QEMU
constexpr const char* VARIANT = "idf-game-qemu";
hm::esp::NullLcdBus lcd;
constexpr bool WITH_RADIO = false;
#else
constexpr const char* VARIANT = "idf-game";
hm::idf::EspLcdBus lcd;
constexpr bool WITH_RADIO = true;
#endif

char framework[64];
hm::idf::I2c0 i2c0;
hm::idf::I2c1 i2c1;

}  // namespace

extern "C" void app_main() {
  snprintf(framework, sizeof framework, "esp-idf_%s", esp_get_idf_version());
  hm::logf("HM hello variant=%s framework=%s", VARIANT, framework);
  hm::esp::GameBuses b;
  if (i2c0.begin()) b.i2c0 = &i2c0;
  if (i2c1.begin()) b.i2c1 = &i2c1;
  if (hm::idf::spi_bus_begin()) b.lcd = &lcd;
  b.radio = WITH_RADIO;
  hm::esp::start_game(b);
}
