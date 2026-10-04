#include "hm/esp32.h"

#include <stdarg.h>
#include <stdio.h>

#include "driver/ledc.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "hm/tuning.h"

namespace hm {

void logf(const char* fmt, ...) {
  char buf[256];
  va_list ap;
  va_start(ap, fmt);
  vsnprintf(buf, sizeof buf, fmt, ap);
  va_end(ap);
  puts(buf);
  fflush(stdout);
}

namespace esp {

uint32_t EspClock::now_us() { return (uint32_t)esp_timer_get_time(); }

ticks_t EspClock::now_ms() { return (ticks_t)(esp_timer_get_time() / 1000); }

void EspClock::delay_ms(uint32_t ms) {
  const TickType_t n = pdMS_TO_TICKS(ms);
  vTaskDelay(n ? n : 1);
}

void EspClock::sleep_until_us(uint32_t t) {
  // Block for whole ticks while two or more are left (a tick wakes up to one
  // period late), then spin to the microsecond.
  const int32_t tick_us = 1000000 / configTICK_RATE_HZ;
  const int32_t left = (int32_t)(t - now_us());
  if (left >= 2 * tick_us) vTaskDelay((TickType_t)((left - tick_us) / tick_us));
  while ((int32_t)(t - now_us()) > 0) {
  }
}

void CoreImuTask::start(ImuSampler& s, int poll_ms) {
  s_ = &s;
  poll_ms_ = poll_ms;
  run_ = true;
  done_ = false;
  if (xTaskCreatePinnedToCore(body, "hm_imu", 4096, this, 10, nullptr, 0) != pdPASS) {
    logf("HM error what=imu_task");
    run_ = false;
    done_ = true;
  }
}

void CoreImuTask::body(void* self) {
  CoreImuTask* t = static_cast<CoreImuTask*>(self);
  const TickType_t period = pdMS_TO_TICKS(t->poll_ms_) ? pdMS_TO_TICKS(t->poll_ms_) : 1;
  TickType_t last = xTaskGetTickCount();
  while (t->run_) {
    t->s_->poll();
    vTaskDelayUntil(&last, period);
  }
  t->done_ = true;
  vTaskDelete(nullptr);
}

void CoreImuTask::stop() {
  run_ = false;
  while (!done_) vTaskDelay(1);
}

void backlight(bool on) {
  static bool ready = false;
  constexpr ledc_mode_t MODE = LEDC_LOW_SPEED_MODE;
  constexpr ledc_channel_t CH = LEDC_CHANNEL_7;
  if (!ready) {
    ledc_timer_config_t t = {};
    t.speed_mode = MODE;
    t.duty_resolution = LEDC_TIMER_13_BIT;
    t.timer_num = LEDC_TIMER_3;
    t.freq_hz = 1000;
    t.clk_cfg = LEDC_AUTO_CLK;
    ledc_timer_config(&t);
    ledc_channel_config_t c = {};
    c.gpio_num = 12;
    c.speed_mode = MODE;
    c.channel = CH;
    c.intr_type = LEDC_INTR_DISABLE;
    c.timer_sel = LEDC_TIMER_3;
    c.duty = 0;
    ledc_channel_config(&c);
    ready = true;
  }
  ledc_set_duty(MODE, CH, on ? (uint32_t)(T::BACKLIGHT_NORMAL * 8191 + 0.5) : 0);
  ledc_update_duty(MODE, CH);
}

}  // namespace esp
}  // namespace hm
