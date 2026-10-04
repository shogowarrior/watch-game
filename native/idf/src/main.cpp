// Display and motion-sensor benchmark on ESP-IDF: the shared steps and log lines
// of hm::Bench over one ST7789 bus, chosen per PlatformIO environment
// (HM_BUS_REGS: register-level DMA, else esp_lcd). HM_BUS_CHECK adds the I2C0
// check of a faster BMA423 clock before the display steps.
#include <stdio.h>

#include "esp_system.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "hm/bench.h"
#include "hm/esp32.h"
#include "hm/idf.h"

namespace {

#ifdef HM_BUS_REGS
using Bus = hm::idf::RegDmaBus;
constexpr const char* VARIANT = "idf-regdma";
#else
using Bus = hm::idf::EspLcdBus;
constexpr const char* VARIANT = "idf-esplcd";
#endif
#ifdef HM_BUS_CHECK
constexpr bool BUS_CHECK = true;
#else
constexpr bool BUS_CHECK = false;
#endif

// BMA423 clocks for the bus check: the 400 kHz control, then faster ones.
constexpr uint32_t BUS_CHECK_HZ[] = {400000, 700000, 1000000};
constexpr uint32_t BUS_CHECK_MS = 5000;

char framework[64];
hm::esp::EspClock clock_;
hm::idf::I2c0 i2c0;
Bus lcd;
hm::esp::CoreImuTask imu;
hm::BenchHost host{VARIANT, framework, clock_, lcd, i2c0, imu, hm::esp::backlight};
hm::Bench bench(host);

void bus_check() {
  static hm::Bma423 bma(i2c0, clock_);
  static hm::ImuSampler sampler(bma, clock_);
  hm::BusCheck check(i2c0, clock_, bma, sampler, imu);
  if (!check.baseline()) return;
  for (uint32_t hz : BUS_CHECK_HZ) check.run(hz, BUS_CHECK_MS);
}

void bench_task(void*) {
  // After the run: the HOT field at 20, 30 and 60 fps in turn, 10 s each, for the eye.
  static const int targets[] = {20, 30, 60};
  if (bench.setup()) {
    if (BUS_CHECK) bus_check();
    bench.run();
    for (int i = 0;; i++) bench.show(hm::Bench::CLOCKS[1], targets[i % 3], 10);
  }
  for (;;) vTaskDelay(pdMS_TO_TICKS(1000));
}

}  // namespace

extern "C" void app_main() {
  snprintf(framework, sizeof framework, "esp-idf_%s", esp_get_idf_version());
  if (!i2c0.begin()) {
    hm::logf("HM error what=i2c_bus");
    return;
  }
  if (!hm::idf::spi_bus_begin()) {
    hm::logf("HM error what=spi_bus");
    return;
  }
  // The renderer gets core 1; the motion-sensor task and the system tasks keep core 0.
  xTaskCreatePinnedToCore(bench_task, "hm_bench", 8192, nullptr, 5, nullptr, 1);
}
