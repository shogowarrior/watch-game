// The main loop's watchdog: the IDF task watchdog on the calling task. When feed()
// stops for timeout_s it panics and the watch reboots (hal/watchdog.py's hardware mode).
#pragma once
#include <stdint.h>

namespace task_watchdog {

bool start(uint32_t timeout_s);   // from the task to watch: setup() watches loop()
void feed();
int reset_reason();               // esp_reset_reason() of this boot
// For a check that starves the watchdog on purpose: starve() marks RTC memory,
// which survives the reboot; fired() is true once, on the boot after it.
void starve();
bool fired();

}  // namespace task_watchdog
