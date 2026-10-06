#include "hm/esp32_game.h"

#include <new>
#include <optional>

#include "esp_heap_caps.h"
#include "esp_task_wdt.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "hm/axp202_pmu.h"
#include "hm/bma423_imu.h"
#include "hm/esp32.h"
#include "hm/esp32_app.h"
#include "hm/esp32_io.h"
#include "hm/ft6336.h"
#include "hm/runtime.h"
#include "hm/st7789_display.h"

namespace hm {
namespace esp {

namespace {

constexpr uint32_t LCD_HZ[] = {40000000, 26666667};   // the benches' display clock, else MicroPython's
constexpr int BLACK_ROWS = 8;                         // the strip that clears the panel at init (on the stack)
constexpr uint8_t CHANNEL = 6;                        // hal/radio.py DEFAULT_CHANNEL
constexpr int TX_DBM = 20;                            // ... and txpower
constexpr int32_t FPS_LOG_MS = 10000;                 // main.py's fps_log_ms
constexpr uint32_t STACK = 16384;
constexpr UBaseType_t PRIO = 5;

GameBuses buses;
EspClock clock_;
IrqLine pmu_irq(Line::AXP202);
AppBacklight backlight;
std::optional<Axp202Pmu> pmu;
std::optional<St7789Display> display;
std::optional<Bma423Imu> imu;
std::optional<Ft6336> touch;
AppMotor motor;
AppRadio radio;
app::Parts parts;
void* rt_mem = nullptr;   // the Runtime's block, taken before Wi-Fi splits the heap (start_game)

void bring_up() {
  const bool lines = lines_begin();
  if (buses.i2c0) {
    pmu.emplace(*buses.i2c0, lines ? &pmu_irq : nullptr);
    if (pmu->begin()) parts.pmu = &*pmu;
  }
  uint32_t hz = 0;
  for (uint32_t c : LCD_HZ)
    if (!hz && buses.lcd && buses.lcd->set_clock(c)) hz = c;
  if (hz) {
    alignas(4) uint16_t black[St7789::W * BLACK_ROWS];   // SPI DMA reads it in place
    display.emplace(*buses.lcd, clock_, backlight, parts.pmu ? &*pmu : nullptr);
    if (display->init(black, BLACK_ROWS)) {
      display->brightness(St7789Display::DEFAULT_BRIGHTNESS);
      parts.display = &*display;
    }
  }
  if (buses.i2c0) {
    imu.emplace(*buses.i2c0, clock_, BMA423_Z_SIGN);
    if (imu->init()) parts.imu = &*imu;
  }
  if (buses.i2c1) {
    touch.emplace(*buses.i2c1);
    touch->begin();
    parts.touch = &*touch;
  }
  if (motor.hw.begin()) parts.motor = &motor;
  if (buses.radio && radio.hw.begin(CHANNEL, TX_DBM)) parts.radio = &radio;
  logf("HM parts pmu=%d display=%d imu=%d touch=%d haptics=%d radio=%d lcd_hz=%u", parts.pmu != nullptr,
       parts.display != nullptr, parts.imu != nullptr, parts.touch != nullptr, parts.motor != nullptr,
       parts.radio != nullptr, (unsigned)(parts.display ? hz : 0));
}

void game_task(void*) {
  bring_up();
  runtime::Runtime& rt = *new (rt_mem) runtime::Runtime(parts, clock_, FPS_LOG_MS);   // lives as long as the task
  esp_task_wdt_add(nullptr);
  rt.begin();
  const ticks_t t0 = clock_.now_ms();
  bool mem_told = false;
  for (;;) {
    esp_task_wdt_reset();
    const int32_t w = rt.step();
    if (w > 0) rt.idle(w);
    if (!mem_told && ticks_diff(clock_.now_ms(), t0) >= FPS_LOG_MS) {
      mem_told = true;
      logf("HM mem stack_free=%u heap_free=%u heap_min=%u", (unsigned)uxTaskGetStackHighWaterMark(nullptr),
           (unsigned)heap_caps_get_free_size(MALLOC_CAP_8BIT),
           (unsigned)heap_caps_get_minimum_free_size(MALLOC_CAP_8BIT));
    }
  }
}

}  // namespace

void start_game(const GameBuses& b) {
  buses = b;
  // ~105 KB in internal RAM, as DMA reads its strips: from the heap, as Arduino's static
  // DRAM cannot hold it (its Bluetooth reserve takes the start of that region).
  rt_mem = heap_caps_aligned_alloc(alignof(runtime::Runtime), sizeof(runtime::Runtime), MALLOC_CAP_DMA);
  if (!rt_mem) {
    logf("HM error what=runtime_mem need=%u largest=%u", (unsigned)sizeof(runtime::Runtime),
         (unsigned)heap_caps_get_largest_free_block(MALLOC_CAP_DMA));
    return;
  }
  xTaskCreatePinnedToCore(game_task, "hm_game", STACK, nullptr, PRIO, nullptr, 1);
}

}  // namespace esp
}  // namespace hm
