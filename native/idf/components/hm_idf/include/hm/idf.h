// ESP-IDF 5 hardware shared by the native/idf builds: I2C0 with a clock per chip,
// and two ways to drive the ST7789 over SPI2 (HSPI) with DMA.
#pragma once
#include <stddef.h>
#include <stdint.h>

#include "driver/i2c_master.h"
#include "driver/spi_master.h"
#include "esp_lcd_panel_io.h"
#include "esp_rom_lldesc.h"
#include "hm/bus_check.h"

namespace hm {
namespace idf {

// I2C0 on pins 21/22: the AXP202, the BMA423 and the PCF8563 RTC, one handle each.
class I2c0 : public I2cBus {
 public:
  static constexpr uint8_t AXP202 = 0x35, BMA423 = 0x19, PCF8563 = 0x51;
  static constexpr uint32_t HZ = 400000;      // what the AXP202 and the RTC are rated for
  bool begin();                               // every chip at HZ
  // That chip's SCL clock from its next transfer. Only while nothing else uses the chip.
  bool set_clock(uint8_t addr, uint32_t hz) override;
  bool write(uint8_t addr, uint8_t reg, const uint8_t* d, size_t n) override;
  bool read(uint8_t addr, uint8_t reg, uint8_t* d, size_t n) override;

 private:
  static constexpr int N = 3, TIMEOUT_MS = 50;
  static constexpr uint8_t ADDRS[N] = {AXP202, BMA423, PCF8563};
  int index(uint8_t addr) const;
  i2c_master_bus_handle_t bus_ = nullptr;
  i2c_master_dev_handle_t dev_[N] = {};
};

// SPI2 with SCK 18, MOSI 19 and no MISO (HSPI's default MISO is GPIO12, the
// backlight and a boot strapping pin). DMA transfers up to one 240x24 strip.
bool spi_bus_begin();

// esp_lcd SPI panel IO (spi_master underneath). A command first drains the
// queued pixels, so at most one strip is on the wire while the next is drawn.
// A new clock means a new panel IO.
class EspLcdBus : public LcdBus {
 public:
  bool set_clock(uint32_t hz) override;
  void command(uint8_t cmd, const uint8_t* d, size_t n) override;
  void pixels(uint8_t cmd, const uint16_t* px, size_t count) override;
  void wait() override;
  esp_lcd_panel_io_handle_t io() const { return io_; }

 private:
  esp_lcd_panel_io_handle_t io_ = nullptr;
};

// Register level: spi_master claims the pins, the clock and the DMA channel and
// leaves this device's settings in SPI2's registers, then this bus keeps the
// bus and writes the registers itself. Every command and pixel burst is one DMA
// transfer polled to completion, with D/C (GPIO27) set between them, so a
// window costs a few microseconds instead of a driver round trip per command.
// Pixels must be in internal RAM and 4-byte aligned (ESP32 DMA).
class RegDmaBus : public LcdBus {
 public:
  bool set_clock(uint32_t hz) override;
  void command(uint8_t cmd, const uint8_t* d, size_t n) override;   // n <= 16 (the ST7789 takes 14 at most)
  void pixels(uint8_t cmd, const uint16_t* px, size_t count) override;
  void wait() override;

 private:
  static constexpr size_t DESC_BYTES = 4092;  // most one DMA descriptor holds
  static constexpr int MAX_DESC = 3;          // one 240x24 strip; longer bursts go in pieces
  void send(const void* p, size_t n);         // starts one DMA transfer of n <= MAX_DESC * DESC_BYTES and returns
  void release();
  spi_device_handle_t dev_ = nullptr;
  bool busy_ = false;
  lldesc_t desc_[MAX_DESC];
  alignas(4) uint8_t cmd_[4];
  alignas(4) uint8_t par_[16];
};

}  // namespace idf
}  // namespace hm
