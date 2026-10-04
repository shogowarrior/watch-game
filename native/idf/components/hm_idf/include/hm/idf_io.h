// ESP-IDF 5 drivers for the rest of the game's hardware: the ESP-NOW radio, the
// vibration motor and the interrupt lines the game polls (hal/radio.py,
// hal/haptics.py, hal/pins.py).
#pragma once
#include <stddef.h>
#include <stdint.h>

#include <atomic>

#include "esp_now.h"
#include "hm/ticks.h"

namespace hm {
namespace idf {

// ESP-NOW broadcast on the station interface, which never joins an access
// point here. Frames arrive on Wi-Fi's task (core 0) and wait in a ring until
// poll() hands them to the game loop, so receiving never blocks a frame.
class EspNowRadio {
 public:
  static constexpr int MAX_LEN = 32;          // beacons are 16 bytes; longer frames are counted, not kept
  static constexpr int MAX_DRAIN = 32;        // frames per poll, as hal/radio.py
  struct Frame {
    uint8_t mac[6];
    uint8_t len;
    int8_t rssi;
    ticks_t t_ms;                             // when it arrived
    uint8_t data[MAX_LEN];
  };
  struct Stats {
    uint32_t tx, tx_err, rx, rx_lost, rx_long, drain_max;   // rx_lost: the ring was full
  };

  // Wi-Fi in station mode on channel 1, 6 or 11 at dbm (2..20), no power save.
  // One radio per watch: false if one already runs or a step fails (logged).
  bool begin(uint8_t channel, int dbm);
  bool send(const uint8_t* buf, size_t n);    // broadcast now; false if Wi-Fi's queue is full
  // Hands each waiting frame to fn, oldest first, at most MAX_DRAIN; returns how many.
  int poll(void (*fn)(void* ctx, const Frame& f), void* ctx);
  const uint8_t* mac() const { return mac_; }
  Stats stats() const;

 private:
  static constexpr uint32_t SLOTS = 32;       // a power of two
  static void on_recv(const esp_now_recv_info_t* info, const uint8_t* data, int len);
  static EspNowRadio* self_;
  Frame ring_[SLOTS];
  std::atomic<uint32_t> head_{0}, tail_{0};   // head: Wi-Fi's task writes; tail: poll()
  std::atomic<uint32_t> rx_lost_{0}, rx_long_{0};
  uint32_t tx_ = 0, tx_err_ = 0, rx_ = 0, drain_max_ = 0;
  uint8_t mac_[6] = {};
};

// The vibration motor on GPIO4 (an ERM behind a transistor): LEDC PWM at
// 1 kHz. Any non-zero strength gets at least MIN_DUTY so the motor spins up.
class Motor {
 public:
  static constexpr float MIN_DUTY = 0.35f;
  bool begin();
  void set(float strength);                   // 0..1; writes the duty only when it changes

 private:
  float s_ = 0;
  uint32_t duty_ = 0;
};

// The interrupt lines the game polls (input only, active low, pulled up on
// the board): the AXP202 (side button, VBUS), the touch panel, the BMA423.
enum class Line : uint8_t { AXP202 = 35, TOUCH = 38, BMA423 = 39 };
bool lines_begin();
bool asserted(Line l);

}  // namespace idf
}  // namespace hm
