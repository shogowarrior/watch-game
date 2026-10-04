#include "hm/bus_check.h"

namespace hm {

namespace {

constexpr uint8_t AXP202 = 0x35, PCF8563 = 0x51;
constexpr uint8_t RTC_SECONDS = 0x02;
constexpr int PROBE_MS = 10, POLL_MS = 20, ODR_HZ = 800, RANGE_G = 8;

bool bcd59(uint8_t v) {
  v &= 0x7F;   // bit 7 is the low-voltage flag
  return (v & 0x0F) <= 9 && v <= 0x59;
}

}  // namespace

// Registers nothing writes during the check: AXP202 id, output enables, LDO2/4
// voltage; RTC control 1, control 2 without its alarm and timer flags, CLKOUT.
const BusCheck::Probe BusCheck::PROBES[N] = {
    {AXP202, 0x03, 0xFF}, {AXP202, 0x12, 0xFF}, {AXP202, 0x28, 0xFF},
    {PCF8563, 0x00, 0xFF}, {PCF8563, 0x01, 0x13}, {PCF8563, 0x0D, 0xFF},
};

bool BusCheck::baseline() {
  for (int i = 0; i < N; i++) {
    if (bus_.read(PROBES[i].addr, PROBES[i].reg, &base_[i], 1)) continue;
    logf("HM error what=i2c_probe addr=0x%02x reg=0x%02x", PROBES[i].addr, PROBES[i].reg);
    return false;
  }
  return true;
}

void BusCheck::probe(BusTally& t) {
  for (int i = 0; i < N; i++) {
    uint8_t v;
    t.reads++;
    if (!bus_.read(PROBES[i].addr, PROBES[i].reg, &v, 1)) t.errors++;
    else if ((v ^ base_[i]) & PROBES[i].mask) t.changed++;
  }
  uint8_t s;
  t.reads++;
  if (!bus_.read(PCF8563, RTC_SECONDS, &s, 1)) t.errors++;
  else if (!bcd59(s)) t.bad_time++;
}

bool BusCheck::run(uint32_t bma_hz, uint32_t dur_ms) {
  BusTally during = {}, after = {};
  const bool clocked = bus_.set_clock(Bma423::ADDR, bma_hz);
  const bool armed = clocked && bma_.init(ODR_HZ, RANGE_G);
  const uint32_t t0 = clock_.now_us();
  sampler_.reset();
  if (armed) {
    task_.start(sampler_, POLL_MS);
    for (ticks_t end = ticks_add(clock_.now_ms(), (int32_t)dur_ms); ticks_diff(end, clock_.now_ms()) > 0;) {
      probe(during);
      clock_.delay_ms(PROBE_MS);
    }
    task_.stop();
  }
  const uint32_t us = clock_.now_us() - t0;
  const bool restored = bus_.set_clock(Bma423::ADDR, SAFE_HZ);
  probe(after);
  const ImuStats& s = sampler_.stats;
  logf("HM i2c bma_hz=%u armed=%d rate=%u us_per_sample=%u read_ms_per_s=%u imu_errors=%u full=%u peak_mg=%u "
       "probes=%u probe_errors=%u changed=%u bad_time=%u after_errors=%u after_changed=%u after_bad_time=%u",
       (unsigned)bma_hz, armed, (unsigned)(us ? (uint64_t)s.samples * 1000000 / us : 0),
       (unsigned)(s.samples ? s.read_us / s.samples : 0), (unsigned)(us ? (uint64_t)s.read_us * 1000 / us : 0),
       (unsigned)s.errors, (unsigned)s.full, (unsigned)s.peak_mg, (unsigned)during.reads,
       (unsigned)during.errors, (unsigned)during.changed, (unsigned)during.bad_time,
       (unsigned)after.errors, (unsigned)after.changed, (unsigned)after.bad_time);
  return armed && restored && s.samples && !s.errors && !during.errors && !during.changed && !during.bad_time &&
         !after.errors && !after.changed && !after.bad_time;
}

}  // namespace hm
