// The game on Arduino-ESP32 2.0.17: the buses, then hm::esp::start_game
// (hm/esp32_game.h: hal/board.py's bring-up and app/runtime.py's loop, shared
// with the ESP-IDF build). The panel goes over the shared spi_master bus.
//
// HM_QEMU (env game-qemu): Espressif's QEMU has no Wi-Fi model and its SPI has
// no DMA, so that build leaves the radio out and sends the panel's pixels
// nowhere; the rest runs as on the watch, so qemu_run.py can watch the loop
// reach its first fps and mem lines (--until "HM mem").
#include <Arduino.h>

#include "board_pins.h"
#include "framework.h"
#include "hm/esp32_game.h"
#include "hm/runtime.h"
#include "hm/spi_lcd_bus.h"
#include "hm/st7789.h"
#include "task_watchdog.h"
#include "wire_i2c.h"

namespace {

constexpr uint32_t WATCHDOG_S = 8;   // main.py: app.run(..., watchdog_ms=8000)

#ifdef HM_QEMU
constexpr const char* VARIANT = "arduino-game-qemu";
hm::esp::NullLcdBus lcd;
bool lcd_begin() { return true; }
constexpr bool WITH_RADIO = false;
#else
constexpr const char* VARIANT = "arduino-game";
hm::esp::SpiLcdBus lcd;
bool lcd_begin() {
  return lcd.begin(SPI2_HOST, pins::LCD_SCK, pins::LCD_MOSI, pins::LCD_CS, pins::LCD_DC,
                   (size_t)hm::St7789::W * hm::runtime::STRIP_ROWS);   // one strip per transfer
}
constexpr bool WITH_RADIO = true;
#endif

WireI2c i2c0(Wire), i2c1(Wire1);

}  // namespace

void setup() {
  Serial.begin(115200);
  delay(200);
  char framework[96];
  framework_name(framework, sizeof framework, "spi_master");
  hm::logf("HM hello variant=%s framework=%s", VARIANT, framework);
  if (!task_watchdog::init(WATCHDOG_S)) hm::logf("HM error what=watchdog");
  hm::esp::GameBuses b;
  if (i2c0.begin(pins::I2C0_SDA, pins::I2C0_SCL, pins::I2C_HZ)) b.i2c0 = &i2c0;
  if (i2c1.begin(pins::I2C1_SDA, pins::I2C1_SCL, pins::I2C_HZ)) b.i2c1 = &i2c1;
  if (lcd_begin()) b.lcd = &lcd;
  b.radio = WITH_RADIO;
  hm::esp::start_game(b);
}

// The game has its own task (core 1, watched by the task watchdog); Arduino's is not needed.
void loop() { vTaskDelete(nullptr); }
