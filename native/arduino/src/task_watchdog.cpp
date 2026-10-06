#include "task_watchdog.h"

#include "esp_attr.h"
#include "esp_system.h"
#include "esp_task_wdt.h"

namespace task_watchdog {

namespace {

constexpr uint32_t STARVED = 0x57A4ED00;
RTC_NOINIT_ATTR uint32_t marker;   // kept across a panic reboot, random after power-on

}  // namespace

// IDF 4.4: a second init (Arduino makes the first) updates the timeout and makes it panic.
bool init(uint32_t timeout_s) { return esp_task_wdt_init(timeout_s, true) == ESP_OK; }

bool start(uint32_t timeout_s) { return init(timeout_s) && esp_task_wdt_add(nullptr) == ESP_OK; }

void feed() { esp_task_wdt_reset(); }

int reset_reason() { return (int)esp_reset_reason(); }

void starve() { marker = STARVED; }

// The marker, and a reset the watchdog's panic can cause (PANIC covers an IDF
// that does not mark it TASK_WDT): neither alone, since boot-time watchdog
// resets (QEMU's, for one) leave TASK_WDT too.
bool fired() {
  const esp_reset_reason_t r = esp_reset_reason();
  const bool yes = marker == STARVED && (r == ESP_RST_TASK_WDT || r == ESP_RST_PANIC);
  marker = 0;
  return yes;
}

}  // namespace task_watchdog
