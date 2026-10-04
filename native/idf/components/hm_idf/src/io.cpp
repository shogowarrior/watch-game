#include <string.h>

#include "driver/gpio.h"
#include "driver/ledc.h"
#include "esp_event.h"
#include "esp_timer.h"
#include "esp_wifi.h"
#include "hm/hal.h"
#include "hm/idf_io.h"

namespace hm {
namespace idf {

namespace {

const uint8_t BCAST[6] = {0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF};

bool ok(esp_err_t e, const char* what) {
  if (e == ESP_OK) return true;
  logf("HM error what=%s err=0x%x", what, (unsigned)e);
  return false;
}

}  // namespace

// ---- radio --------------------------------------------------------------------
EspNowRadio* EspNowRadio::self_ = nullptr;

bool EspNowRadio::begin(uint8_t channel, int dbm) {
  if (self_ || (channel != 1 && channel != 6 && channel != 11)) return false;
  const esp_err_t loop = esp_event_loop_create_default();   // Wi-Fi posts its events there
  if (loop != ESP_ERR_INVALID_STATE && !ok(loop, "event_loop")) return false;
  wifi_init_config_t cfg = WIFI_INIT_CONFIG_DEFAULT();
  cfg.nvs_enable = 0;                                        // nothing to remember: no access point
  if (!ok(esp_wifi_init(&cfg), "wifi_init") || !ok(esp_wifi_set_storage(WIFI_STORAGE_RAM), "wifi_storage") ||
      !ok(esp_wifi_set_mode(WIFI_MODE_STA), "wifi_mode") || !ok(esp_wifi_start(), "wifi_start") ||
      !ok(esp_wifi_set_ps(WIFI_PS_NONE), "wifi_ps") ||          // modem sleep would miss frames
      !ok(esp_wifi_set_channel(channel, WIFI_SECOND_CHAN_NONE), "wifi_channel") ||
      !ok(esp_wifi_set_max_tx_power((int8_t)(dbm * 4)), "wifi_txpower") ||   // in 0.25 dBm
      !ok(esp_wifi_get_mac(WIFI_IF_STA, mac_), "wifi_mac") || !ok(esp_now_init(), "espnow_init"))
    return false;
  esp_now_peer_info_t peer = {};
  memcpy(peer.peer_addr, BCAST, 6);
  peer.channel = 0;                                          // the current one
  peer.ifidx = WIFI_IF_STA;
  if (!ok(esp_now_add_peer(&peer), "espnow_peer")) return false;
  self_ = this;
  return ok(esp_now_register_recv_cb(on_recv), "espnow_recv");
}

void EspNowRadio::on_recv(const esp_now_recv_info_t* info, const uint8_t* data, int len) {
  EspNowRadio* r = self_;
  if (len > MAX_LEN || len < 0) {
    r->rx_long_.fetch_add(1, std::memory_order_relaxed);
    return;
  }
  const uint32_t h = r->head_.load(std::memory_order_relaxed);
  if (h - r->tail_.load(std::memory_order_acquire) >= SLOTS) {
    r->rx_lost_.fetch_add(1, std::memory_order_relaxed);
    return;
  }
  Frame& f = r->ring_[h % SLOTS];
  memcpy(f.mac, info->src_addr, 6);
  f.len = (uint8_t)len;
  f.rssi = (int8_t)info->rx_ctrl->rssi;
  f.t_ms = (ticks_t)(esp_timer_get_time() / 1000);
  memcpy(f.data, data, len);
  r->head_.store(h + 1, std::memory_order_release);
}

bool EspNowRadio::send(const uint8_t* buf, size_t n) {
  const bool sent = self_ == this && esp_now_send(BCAST, buf, n) == ESP_OK;
  (sent ? tx_ : tx_err_)++;
  return sent;
}

int EspNowRadio::poll(void (*fn)(void* ctx, const Frame& f), void* ctx) {
  uint32_t t = tail_.load(std::memory_order_relaxed);
  const uint32_t h = head_.load(std::memory_order_acquire);
  int c = 0;
  for (; t != h && c < MAX_DRAIN; t++, c++) {
    fn(ctx, ring_[t % SLOTS]);
  }
  tail_.store(t, std::memory_order_release);
  rx_ += c;
  if ((uint32_t)c > drain_max_) drain_max_ = c;
  return c;
}

EspNowRadio::Stats EspNowRadio::stats() const {
  return {tx_, tx_err_, rx_, rx_lost_.load(std::memory_order_relaxed), rx_long_.load(std::memory_order_relaxed),
          drain_max_};
}

// ---- motor --------------------------------------------------------------------
namespace {

constexpr ledc_mode_t MODE = LEDC_LOW_SPEED_MODE;
constexpr ledc_channel_t MOTOR_CH = LEDC_CHANNEL_6;     // the backlight has channel 7, timer 3
constexpr ledc_timer_t MOTOR_TIMER = LEDC_TIMER_2;
constexpr int MOTOR_PIN = 4;
constexpr uint32_t FULL = (1u << 13) - 1;               // 13-bit duty

}  // namespace

bool Motor::begin() {
  ledc_timer_config_t t = {};
  t.speed_mode = MODE;
  t.duty_resolution = LEDC_TIMER_13_BIT;
  t.timer_num = MOTOR_TIMER;
  t.freq_hz = 1000;
  t.clk_cfg = LEDC_AUTO_CLK;
  ledc_channel_config_t c = {};
  c.gpio_num = MOTOR_PIN;
  c.speed_mode = MODE;
  c.channel = MOTOR_CH;
  c.intr_type = LEDC_INTR_DISABLE;
  c.timer_sel = MOTOR_TIMER;
  c.duty = 0;
  return ok(ledc_timer_config(&t), "motor_timer") && ok(ledc_channel_config(&c), "motor_channel");
}

void Motor::set(float strength) {
  if (strength == s_) return;
  s_ = strength;
  const float lo = MIN_DUTY * FULL;
  const uint32_t d = strength <= 0 ? 0 : strength >= 1 ? FULL : (uint32_t)(lo + (FULL - lo) * strength);
  if (d == duty_) return;
  duty_ = d;
  ledc_set_duty(MODE, MOTOR_CH, d);
  ledc_update_duty(MODE, MOTOR_CH);
}

// ---- interrupt lines ------------------------------------------------------------
bool lines_begin() {
  gpio_config_t g = {};
  g.pin_bit_mask = 1ULL << (int)Line::AXP202 | 1ULL << (int)Line::TOUCH | 1ULL << (int)Line::BMA423;
  g.mode = GPIO_MODE_INPUT;                             // 34..39 have no internal pulls
  return ok(gpio_config(&g), "lines");
}

bool asserted(Line l) { return gpio_get_level((gpio_num_t)l) == 0; }

}  // namespace idf
}  // namespace hm
