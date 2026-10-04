// Hardware interfaces the portable core talks to. Each runtime (native/arduino,
// native/idf) implements them; native/test fakes them.
#pragma once
#include <stddef.h>
#include <stdint.h>

#include "hm/ticks.h"

namespace hm {

// One I2C bus. Register reads and writes on a 7-bit address; false on a NACK or timeout.
struct I2c {
  virtual ~I2c() = default;
  virtual bool write(uint8_t addr, uint8_t reg, const uint8_t* data, size_t n) = 0;
  virtual bool read(uint8_t addr, uint8_t reg, uint8_t* data, size_t n) = 0;
  bool write8(uint8_t addr, uint8_t reg, uint8_t v) { return write(addr, reg, &v, 1); }
};

// The ST7789's SPI link: D/C and CS are the implementation's business.
// pixels() sends a command then the pixels by DMA and returns once the
// previous burst is out, so at most one is on the wire: the caller may
// refill a buffer once a later pixels(), command() or wait() has returned.
struct LcdBus {
  virtual ~LcdBus() = default;
  virtual bool set_clock(uint32_t hz) = 0;        // false: this clock is not available
  virtual void command(uint8_t cmd, const uint8_t* data, size_t n) = 0;   // blocking
  virtual void pixels(uint8_t cmd, const uint16_t* px, size_t count) = 0;
  virtual void wait() = 0;                        // every pixel is on the panel
};

// Time and the serial log.
struct Clock {
  virtual ~Clock() = default;
  virtual uint32_t now_us() = 0;                  // wraps after 71 minutes: differences only
  virtual ticks_t now_ms() = 0;
  virtual void delay_ms(uint32_t ms) = 0;         // yields to other tasks
  virtual void sleep_until_us(uint32_t t) = 0;    // yields, then spins to the exact time
};

void logf(const char* fmt, ...);                  // one line to the serial console, newline added

}  // namespace hm
