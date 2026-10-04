// hm::I2c through an Arduino TwoWire: Wire for I2C0, Wire1 for I2C1 (board_pins.h).
#pragma once
#include <Wire.h>

#include "hm/hal.h"

class WireI2c : public hm::I2c {
 public:
  explicit WireI2c(TwoWire& w) : w_(w) {}
  // Starts the bus from a task on core 0, so its interrupt runs beside the sensor
  // task and not the renderer (as the ESP-IDF build does).
  bool begin(int sda, int scl, uint32_t hz);
  bool write(uint8_t addr, uint8_t reg, const uint8_t* d, size_t n) override;
  bool read(uint8_t addr, uint8_t reg, uint8_t* d, size_t n) override;   // n within Wire's 128-byte buffer
  bool probe(uint8_t addr);                                               // a device answers at addr

 private:
  TwoWire& w_;
};
