// ESP-NOW broadcast on Arduino-ESP32 2.0.17 (IDF 4.4), the same class as the
// ESP-IDF build's radio (hal/radio.py's EspNowRadio in C++) except for the
// receive path: IDF 4.4's callback carries no RSSI, so it is read from the
// radio header in front of the payload.
#pragma once
#include <stddef.h>
#include <stdint.h>

#include <atomic>

#include "hm/ticks.h"

namespace hm {
namespace esp {

// The station interface, never joined to an access point. Frames arrive on
// Wi-Fi's task and wait in a ring until poll() hands them to the main loop.
class EspNowRadio {
 public:
  static constexpr int MAX_LEN = 32;     // beacons are 16 bytes; longer frames are counted, not kept
  static constexpr int MAX_DRAIN = 32;   // frames per poll, as hal/radio.py
  static constexpr int8_t RSSI_NONE = -128;
  struct Frame {
    uint8_t mac[6];
    uint8_t len;
    int8_t rssi;     // RSSI_NONE when the radio header was not where expected
    ticks_t t_ms;    // when it arrived
    uint8_t data[MAX_LEN];
  };
  struct Stats {
    uint32_t tx, tx_err, rx, rx_lost, rx_long, drain_max;   // rx_lost: the ring was full
    uint32_t hdr_bad;                                       // frames whose header named another channel
    int32_t sig_extra;                                      // last frame's air length minus payload: 43
  };

  // Wi-Fi in station mode on channel 1, 6 or 11 at dbm (2..20), no power save.
  // One radio per watch: false if one already runs or a step fails (logged).
  bool begin(uint8_t channel, int dbm);
  bool send(const uint8_t* buf, size_t n);   // broadcast now; false if Wi-Fi's queue is full
  // Hands each waiting frame to fn, oldest first, at most MAX_DRAIN; returns how many.
  int poll(void (*fn)(void* ctx, const Frame& f), void* ctx);
  const uint8_t* mac() const { return mac_; }
  Stats stats() const;

 private:
  static constexpr uint32_t SLOTS = 32;   // a power of two
  static void on_recv(const uint8_t* mac, const uint8_t* data, int len);
  static EspNowRadio* self_;
  Frame ring_[SLOTS];
  std::atomic<uint32_t> head_{0}, tail_{0};   // head: Wi-Fi's task writes; tail: poll()
  std::atomic<uint32_t> rx_lost_{0}, rx_long_{0}, hdr_bad_{0};
  std::atomic<int32_t> sig_extra_{0};
  uint32_t tx_ = 0, tx_err_ = 0, rx_ = 0, drain_max_ = 0;
  uint8_t channel_ = 0;
  uint8_t mac_[6] = {};
};

}  // namespace esp
}  // namespace hm
