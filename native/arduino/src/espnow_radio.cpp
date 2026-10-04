#include "espnow_radio.h"

#include <string.h>

#include "esp_event.h"
#include "esp_netif.h"
#include "esp_now.h"
#include "esp_timer.h"
#include "esp_wifi.h"
#include "hm/hal.h"

namespace hm {
namespace esp {

namespace {

const uint8_t BCAST[6] = {0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF};
// ESP-NOW's frame before the payload: the 802.11 header (24 bytes), then the
// vendor action header (category, OUI, random, element id, length, OUI, type, version).
constexpr int ESPNOW_HEADER = 24 + 15;

bool ok(esp_err_t e, const char* what) {
  if (e == ESP_OK) return true;
  logf("HM error what=%s err=0x%x", what, (unsigned)e);
  return false;
}

// Already done by someone else is fine.
bool ok_or_done(esp_err_t e, const char* what) { return e == ESP_ERR_INVALID_STATE || ok(e, what); }

}  // namespace

EspNowRadio* EspNowRadio::self_ = nullptr;

bool EspNowRadio::begin(uint8_t channel, int dbm) {
  if (self_ || (channel != 1 && channel != 6 && channel != 11)) return false;
  wifi_init_config_t cfg = WIFI_INIT_CONFIG_DEFAULT();
  cfg.nvs_enable = 0;   // nothing to remember: no access point
  if (!ok_or_done(esp_netif_init(), "netif") || !ok_or_done(esp_event_loop_create_default(), "event_loop") ||
      !ok(esp_wifi_init(&cfg), "wifi_init") || !ok(esp_wifi_set_storage(WIFI_STORAGE_RAM), "wifi_storage") ||
      !ok(esp_wifi_set_mode(WIFI_MODE_STA), "wifi_mode") || !ok(esp_wifi_start(), "wifi_start") ||
      !ok(esp_wifi_set_ps(WIFI_PS_NONE), "wifi_ps") ||   // modem sleep would miss frames
      !ok(esp_wifi_set_channel(channel, WIFI_SECOND_CHAN_NONE), "wifi_channel") ||
      !ok(esp_wifi_set_max_tx_power((int8_t)(dbm * 4)), "wifi_txpower") ||   // in 0.25 dBm
      !ok(esp_wifi_get_mac(WIFI_IF_STA, mac_), "wifi_mac") || !ok(esp_now_init(), "espnow_init"))
    return false;
  esp_now_peer_info_t peer = {};
  memcpy(peer.peer_addr, BCAST, 6);
  peer.channel = 0;   // the current one
  peer.ifidx = WIFI_IF_STA;
  if (!ok(esp_now_add_peer(&peer), "espnow_peer")) return false;
  channel_ = channel;
  self_ = this;
  return ok(esp_now_register_recv_cb(on_recv), "espnow_recv");
}

void EspNowRadio::on_recv(const uint8_t* mac, const uint8_t* data, int len) {
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
  // The received packet's radio header sits in front of the frame, as Espressif's
  // esp-now component reads it for IDF < 5; its channel shows it is really there.
  const auto* pkt = (const wifi_promiscuous_pkt_t*)(data - ESPNOW_HEADER - sizeof(wifi_pkt_rx_ctrl_t));
  const bool hdr = pkt->rx_ctrl.channel == r->channel_;
  if (!hdr) r->hdr_bad_.fetch_add(1, std::memory_order_relaxed);
  r->sig_extra_.store((int32_t)pkt->rx_ctrl.sig_len - len, std::memory_order_relaxed);
  Frame& f = r->ring_[h % SLOTS];
  memcpy(f.mac, mac, 6);
  f.len = (uint8_t)len;
  f.rssi = hdr ? (int8_t)pkt->rx_ctrl.rssi : RSSI_NONE;
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
  for (; t != h && c < MAX_DRAIN; t++, c++) fn(ctx, ring_[t % SLOTS]);
  tail_.store(t, std::memory_order_release);
  rx_ += c;
  if ((uint32_t)c > drain_max_) drain_max_ = c;
  return c;
}

EspNowRadio::Stats EspNowRadio::stats() const {
  return {tx_, tx_err_, rx_, rx_lost_.load(std::memory_order_relaxed), rx_long_.load(std::memory_order_relaxed),
          drain_max_, hdr_bad_.load(std::memory_order_relaxed), sig_extra_.load(std::memory_order_relaxed)};
}

}  // namespace esp
}  // namespace hm
