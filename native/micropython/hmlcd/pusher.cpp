// A full-width window of RGB565 rows from anywhere (MicroPython's buffers are
// in PSRAM, which the SPI DMA cannot read) goes out from a task on core 0:
// hm::push_bounced copies 20 rows at a time into two internal DMA buffers,
// one filling while the other is on the wire, so the MicroPython task on
// core 1 draws the next frame meanwhile.
#include "pusher.h"

#include "esp_heap_caps.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "freertos/task.h"
#include "hm/bounce_push.h"
#include "hm/esp32.h"
#include "hm/spi_lcd_bus.h"

namespace {

constexpr int W = hm::St7789::W, CHUNK_ROWS = 20;

hm::esp::EspClock clock_;
hm::esp::SpiLcdBus bus_;
hm::St7789 panel_(bus_, clock_);
uint16_t* bounce_[2] = {nullptr, nullptr};
TaskHandle_t task_ = nullptr;
SemaphoreHandle_t go_ = nullptr, idle_ = nullptr;   // idle_ is held while a push runs
volatile bool quit_ = false;
const uint16_t* job_px_ = nullptr;
int job_y0_ = 0, job_rows_ = 0;
uint32_t pushes_ = 0, last_us_ = 0, max_us_ = 0;
uint64_t waited_us_ = 0, total_us_ = 0;

void body(void*) {
  while (xSemaphoreTake(go_, portMAX_DELAY) == pdTRUE && !quit_) {
    const int64_t t0 = esp_timer_get_time();
    hm::push_bounced(panel_, job_px_, job_y0_, job_rows_, bounce_, CHUNK_ROWS);
    panel_.end_frame();
    last_us_ = (uint32_t)(esp_timer_get_time() - t0);
    if (last_us_ > max_us_) max_us_ = last_us_;
    total_us_ += last_us_;
    pushes_++;
    xSemaphoreGive(idle_);
  }
  xSemaphoreGive(idle_);   // stop() waits for this
  vTaskDelete(nullptr);
}

void take_idle() {
  const int64_t t0 = esp_timer_get_time();
  xSemaphoreTake(idle_, portMAX_DELAY);
  waited_us_ += (uint64_t)(esp_timer_get_time() - t0);
}

void release() {
  for (auto& b : bounce_) {
    heap_caps_free(b);
    b = nullptr;
  }
  if (go_) vSemaphoreDelete(go_);
  if (idle_) vSemaphoreDelete(idle_);
  go_ = idle_ = nullptr;
}

}  // namespace

extern "C" int hmlcd_start(int host, int sck, int mosi, int cs, int dc, uint32_t hz) {
  hmlcd_stop();
  for (auto& b : bounce_) {
    b = (uint16_t*)heap_caps_malloc(W * CHUNK_ROWS * 2, MALLOC_CAP_DMA | MALLOC_CAP_INTERNAL);
    if (!b) {
      release();
      return HMLCD_NOMEM;
    }
  }
  if (!bus_.begin((spi_host_device_t)host, sck, mosi, cs, dc, (size_t)W * CHUNK_ROWS)) {
    release();
    return HMLCD_BUS;
  }
  if (!bus_.set_clock(hz)) {
    bus_.end();
    release();
    return HMLCD_CLOCK;
  }
  go_ = xSemaphoreCreateBinary();
  idle_ = xSemaphoreCreateBinary();
  if (!go_ || !idle_) {
    bus_.end();
    release();
    return HMLCD_NOMEM;
  }
  xSemaphoreGive(idle_);
  quit_ = false;
  pushes_ = last_us_ = max_us_ = 0;
  waited_us_ = total_us_ = 0;
  if (xTaskCreatePinnedToCore(body, "hm_lcd", 4096, nullptr, 5, &task_, 0) != pdPASS) {
    task_ = nullptr;
    bus_.end();
    release();
    return HMLCD_TASK;
  }
  return HMLCD_OK;
}

extern "C" void hmlcd_stop(void) {
  if (!task_) return;
  take_idle();
  quit_ = true;
  xSemaphoreGive(go_);
  xSemaphoreTake(idle_, portMAX_DELAY);   // the task has left its loop
  task_ = nullptr;
  bus_.end();
  release();
}

extern "C" int hmlcd_started(void) { return task_ != nullptr; }

extern "C" int hmlcd_set_clock(uint32_t hz) {
  take_idle();
  const bool ok = bus_.set_clock(hz);
  xSemaphoreGive(idle_);
  return ok ? HMLCD_OK : HMLCD_CLOCK;
}

extern "C" void hmlcd_command(uint8_t cmd, const uint8_t* data, size_t n) {
  take_idle();
  bus_.command(cmd, data, n);
  xSemaphoreGive(idle_);
}

extern "C" void hmlcd_push(const uint16_t* px, int y0, int rows) {
  take_idle();
  job_px_ = px;
  job_y0_ = y0;
  job_rows_ = rows;
  xSemaphoreGive(go_);
}

extern "C" void hmlcd_wait(void) {
  take_idle();
  xSemaphoreGive(idle_);
}

extern "C" int hmlcd_busy(void) { return uxSemaphoreGetCount(idle_) == 0; }

extern "C" void hmlcd_stats(uint32_t out[5]) {
  out[0] = pushes_;
  out[1] = last_us_;
  out[2] = max_us_;
  out[3] = (uint32_t)total_us_;
  out[4] = (uint32_t)waited_us_;
}
