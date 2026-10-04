#include "wire_i2c.h"

bool WireI2c::begin(int sda, int scl, uint32_t hz) {
  struct Start {
    TwoWire* w;
    int sda, scl;
    uint32_t hz;
    volatile bool ok, done;
  } s{&w_, sda, scl, hz, false, false};
  xTaskCreatePinnedToCore(
      [](void* p) {
        auto* s = static_cast<Start*>(p);
        s->ok = s->w->begin(s->sda, s->scl, s->hz);
        s->done = true;
        vTaskDelete(nullptr);
      },
      "hm_wire", 4096, &s, 5, nullptr, 0);
  while (!s.done) delay(1);
  return s.ok;
}

bool WireI2c::write(uint8_t addr, uint8_t reg, const uint8_t* d, size_t n) {
  w_.beginTransmission(addr);
  w_.write(reg);
  w_.write(d, n);
  return w_.endTransmission() == 0;
}

bool WireI2c::read(uint8_t addr, uint8_t reg, uint8_t* d, size_t n) {
  w_.beginTransmission(addr);
  w_.write(reg);
  if (w_.endTransmission(false) != 0) return false;
  if (w_.requestFrom((uint16_t)addr, n, true) != n) return false;
  for (size_t i = 0; i < n; i++) d[i] = (uint8_t)w_.read();
  return true;
}

bool WireI2c::probe(uint8_t addr) {
  w_.beginTransmission(addr);
  return w_.endTransmission() == 0;
}
