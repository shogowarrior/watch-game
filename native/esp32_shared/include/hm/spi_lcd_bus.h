// The ST7789 on ESP-IDF's spi_master: half-duplex and write-only (no MISO:
// GPIO12 is the backlight), which lets the clock go past 26.67 MHz through
// the GPIO matrix. D/C follows each transfer; pixels go by DMA. Builds on
// IDF 4.4 (Arduino) and 5.x (ESP-IDF, MicroPython).
#pragma once
#include <stddef.h>
#include <stdint.h>

#include "driver/spi_master.h"
#include "hm/hal.h"

namespace hm {
namespace esp {

class SpiLcdBus : public LcdBus {
 public:
  // host: SPI2_HOST (HSPI) on this watch. max_px: the largest pixels() call.
  bool begin(spi_host_device_t host, int sck, int mosi, int cs, int dc, size_t max_px);
  void end();                                    // frees the bus for another driver
  bool set_clock(uint32_t hz) override;          // a new clock re-adds the device
  void command(uint8_t cmd, const uint8_t* data, size_t n) override;
  void pixels(uint8_t cmd, const uint16_t* px, size_t count) override;
  void wait() override;

 private:
  void drain();
  void send(const void* d, size_t n, bool data);
  spi_host_device_t host_ = SPI2_HOST;
  spi_device_handle_t dev_ = nullptr;
  spi_transaction_t burst_ = {};
  int cs_ = -1, dc_ = -1;
  bool bus_ = false, pending_ = false;
};

}  // namespace esp
}  // namespace hm
