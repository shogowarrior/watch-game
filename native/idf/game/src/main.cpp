// The game on ESP-IDF: hal/board.py's bring-up, then app/runtime.py's loop
// (hm::runtime::Runtime) as main.py runs it, with an "HM fps" line every 10 s.
// A part that does not come up is left out, as Board.init(strict=False) does,
// and shows as 0 on the "HM parts" line. The loop runs on core 1 (Wi-Fi and
// the system tasks keep core 0) and feeds the task watchdog once per pass: a
// hung loop reboots the watch after 8 s (sdkconfig.defaults). Ten seconds in,
// an "HM mem" line gives the loop's unused stack and the free heap.
//
// HM_QEMU (env game-qemu): Espressif's QEMU has no Wi-Fi model and its SPI has
// no DMA, so that build leaves the radio out and sends the panel's pixels
// nowhere; the rest runs as on the watch, so qemu_run.py can watch the loop
// reach its first fps line (--until "HM fps").
#include <stdio.h>

#include <optional>

#include "esp_heap_caps.h"
#include "esp_system.h"
#include "esp_task_wdt.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "hm/axp202_pmu.h"
#include "hm/bma423_imu.h"
#include "hm/esp32.h"
#include "hm/esp32_app.h"
#include "hm/esp32_io.h"
#include "hm/ft6336.h"
#include "hm/idf.h"
#include "hm/runtime.h"
#include "hm/st7789_display.h"

namespace {

constexpr uint32_t LCD_HZ[] = {40000000, 26666667};   // the benches' display clock, else MicroPython's
constexpr int BLACK_ROWS = 8;                   // the strip that clears the panel at init (on the stack)
constexpr double BRIGHTNESS = 0.6;              // hal/board.py default_brightness
constexpr uint8_t CHANNEL = 6;                  // hal/radio.py DEFAULT_CHANNEL
constexpr int TX_DBM = 20;                      // ... and txpower
constexpr int32_t FPS_LOG_MS = 10000;           // main.py's fps_log_ms
constexpr uint32_t LOOP_STACK = 16384;
constexpr UBaseType_t LOOP_PRIO = 5;

#ifdef HM_QEMU
constexpr const char* VARIANT = "idf-game-qemu";
// QEMU's SPI has no DMA, so a real push never finishes.
struct NullBus final : hm::LcdBus {
  bool set_clock(uint32_t) override { return true; }
  void command(uint8_t, const uint8_t*, size_t) override {}
  void pixels(uint8_t, const uint16_t*, size_t) override {}
  void wait() override {}
};
NullBus lcd;
constexpr bool WITH_RADIO = false;
#else
constexpr const char* VARIANT = "idf-game";
hm::idf::EspLcdBus lcd;
constexpr bool WITH_RADIO = true;
#endif

char framework[64];
hm::esp::EspClock clock_;
hm::idf::I2c0 i2c0;
hm::idf::I2c1 i2c1;
hm::esp::IrqLine pmu_irq(hm::esp::Line::AXP202);
hm::esp::AppBacklight backlight;
std::optional<hm::Axp202Pmu> pmu;
std::optional<hm::St7789Display> display;
std::optional<hm::Bma423Imu> imu;
std::optional<hm::Ft6336> touch;
hm::esp::AppMotor motor;
hm::esp::AppRadio radio;
hm::app::Parts parts;

// hal/board.py ORDER: pmu, display, backlight, imu, touch, haptics, radio.
void bring_up() {
  const bool bus0 = i2c0.begin(), bus1 = i2c1.begin(), spi = hm::idf::spi_bus_begin();
  const bool lines = hm::esp::lines_begin();
  if (bus0) {
    pmu.emplace(i2c0, lines ? &pmu_irq : nullptr);
    if (pmu->begin()) parts.pmu = &*pmu;
  }
  uint32_t hz = 0;
  for (uint32_t c : LCD_HZ)
    if (!hz && spi && lcd.set_clock(c)) hz = c;
  if (hz) {
    uint16_t black[hm::St7789::W * BLACK_ROWS];
    display.emplace(lcd, clock_, backlight, parts.pmu ? &*pmu : nullptr);
    if (display->init(black, BLACK_ROWS)) {
      display->brightness(BRIGHTNESS);
      parts.display = &*display;
    }
  }
  if (bus0) {
    imu.emplace(i2c0, clock_, hm::BMA423_Z_SIGN);
    if (imu->init()) parts.imu = &*imu;
  }
  if (bus1) {
    touch.emplace(i2c1);
    touch->begin();
    parts.touch = &*touch;
  }
  if (motor.hw.begin()) parts.motor = &motor;
  if (WITH_RADIO && radio.hw.begin(CHANNEL, TX_DBM)) parts.radio = &radio;
  hm::logf("HM parts pmu=%d display=%d imu=%d touch=%d haptics=%d radio=%d lcd_hz=%u", parts.pmu != nullptr,
           parts.display != nullptr, parts.imu != nullptr, parts.touch != nullptr, parts.motor != nullptr,
           parts.radio != nullptr, (unsigned)(parts.display ? hz : 0));
}

void loop_task(void*) {
  bring_up();
  static hm::runtime::Runtime rt(parts, clock_, FPS_LOG_MS);   // ~110 KB: static, never on the stack
  esp_task_wdt_add(nullptr);
  rt.begin();
  const hm::ticks_t t0 = clock_.now_ms();
  bool mem_told = false;
  for (;;) {
    esp_task_wdt_reset();
    const int32_t w = rt.step();
    if (w > 0) rt.idle(w);
    if (!mem_told && hm::ticks_diff(clock_.now_ms(), t0) >= FPS_LOG_MS) {
      mem_told = true;
      hm::logf("HM mem stack_free=%u heap_free=%u heap_min=%u", (unsigned)uxTaskGetStackHighWaterMark(nullptr),
               (unsigned)heap_caps_get_free_size(MALLOC_CAP_8BIT),
               (unsigned)heap_caps_get_minimum_free_size(MALLOC_CAP_8BIT));
    }
  }
}

}  // namespace

extern "C" void app_main() {
  snprintf(framework, sizeof framework, "esp-idf_%s", esp_get_idf_version());
  hm::logf("HM hello variant=%s framework=%s", VARIANT, framework);
  xTaskCreatePinnedToCore(loop_task, "hm_game", LOOP_STACK, nullptr, LOOP_PRIO, nullptr, 1);
}
