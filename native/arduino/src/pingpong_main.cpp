// Radio check on Arduino-ESP32 2.0.17: pings a MicroPython watch running the pong
// side of tools/radio_pingpong.py, so the RSSI this build reads (espnow_radio.h)
// sits next to the RSSI MicroPython measures on the same link.
#include <Arduino.h>

#include "espnow_radio.h"
#include "framework.h"
#include "hm/esp32.h"
#include "pingpong.h"

namespace {

constexpr uint8_t CHANNEL = 6;   // radio_pingpong.run's default
constexpr int DBM = 20;          // hal/radio.py's txpower

hm::esp::EspClock clock_;
hm::esp::EspNowRadio radio;
pingpong::Pinger pinger;
bool running = false;

void on_frame(void* ctx, const hm::esp::EspNowRadio::Frame& f) {
  static_cast<pingpong::Pinger*>(ctx)->on_rx(f.data, f.len, f.rssi, f.t_ms);
}

}  // namespace

void setup() {
  Serial.begin(115200);
  delay(200);
  char framework[96];
  framework_name(framework, sizeof framework, "esp_now");
  hm::logf("HM hello variant=arduino-radio framework=%s", framework);
  running = radio.begin(CHANNEL, DBM);
  if (!running) return;
  const uint8_t* m = radio.mac();
  hm::logf("HM radio ch=%d dbm=%d mac=%02x:%02x:%02x:%02x:%02x:%02x", CHANNEL, DBM, m[0], m[1], m[2], m[3], m[4],
           m[5]);
}

void loop() {
  if (!running) {
    delay(1000);
    return;
  }
  const hm::ticks_t now = clock_.now_ms();
  uint8_t ping[hm::proto::SIZE];
  if (pinger.due(now, ping)) radio.send(ping, sizeof ping);
  radio.poll(on_frame, &pinger);
  if (pinger.done(now)) {
    pinger.report(CHANNEL);
    const auto s = radio.stats();
    hm::logf("HM radio_stats tx=%u tx_err=%u rx=%u rx_lost=%u rx_long=%u drain_max=%u hdr_bad=%u sig_extra=%d",
             (unsigned)s.tx, (unsigned)s.tx_err, (unsigned)s.rx, (unsigned)s.rx_lost, (unsigned)s.rx_long,
             (unsigned)s.drain_max, (unsigned)s.hdr_bad, (int)s.sig_extra);
    hm::logf("HM done");
    running = false;
  }
  delay(1);
}
