#include "hm/spi_lcd_bus.h"

#include <string.h>

#include "driver/gpio.h"
#include "esp_attr.h"

namespace hm {
namespace esp {

namespace {

// Before each transfer: D/C low for a command byte, high for data. ``user``
// carries the pin and level, so the callback needs no state.
void IRAM_ATTR set_dc(spi_transaction_t* t) {
  const uint32_t v = (uint32_t)(uintptr_t)t->user;
  gpio_set_level((gpio_num_t)(v >> 1), v & 1);
}

}  // namespace

bool SpiLcdBus::begin(spi_host_device_t host, int sck, int mosi, int cs, int dc, size_t max_px) {
  host_ = host;
  cs_ = cs;
  dc_ = dc;
  gpio_config_t g = {};
  g.pin_bit_mask = 1ULL << dc;
  g.mode = GPIO_MODE_OUTPUT;
  if (gpio_config(&g) != ESP_OK) return false;
  spi_bus_config_t b = {};
  b.mosi_io_num = mosi;
  b.miso_io_num = -1;
  b.sclk_io_num = sck;
  b.quadwp_io_num = -1;
  b.quadhd_io_num = -1;
  b.max_transfer_sz = (int)(max_px * 2);
  bus_ = spi_bus_initialize(host, &b, SPI_DMA_CH_AUTO) == ESP_OK;
  return bus_;
}

void SpiLcdBus::end() {
  drain();
  if (dev_) spi_bus_remove_device(dev_);
  dev_ = nullptr;
  if (bus_) spi_bus_free(host_);
  bus_ = false;
}

bool SpiLcdBus::set_clock(uint32_t hz) {
  drain();
  if (dev_) spi_bus_remove_device(dev_);
  dev_ = nullptr;
  spi_device_interface_config_t d = {};
  d.clock_speed_hz = (int)hz;
  d.mode = 0;
  d.spics_io_num = cs_;
  d.queue_size = 2;
  d.flags = SPI_DEVICE_HALFDUPLEX | SPI_DEVICE_NO_DUMMY;
  d.pre_cb = set_dc;
  return bus_ && spi_bus_add_device(host_, &d, &dev_) == ESP_OK;
}

void SpiLcdBus::send(const void* d, size_t n, bool data) {
  spi_transaction_t t = {};
  t.length = n * 8;
  t.user = (void*)(uintptr_t)(dc_ << 1 | (data ? 1 : 0));
  if (n <= 4) {
    t.flags = SPI_TRANS_USE_TXDATA;
    memcpy(t.tx_data, d, n);
  } else {
    t.tx_buffer = d;
  }
  spi_device_polling_transmit(dev_, &t);
}

void SpiLcdBus::command(uint8_t cmd, const uint8_t* data, size_t n) {
  drain();
  send(&cmd, 1, false);
  if (n) send(data, n, true);
}

void SpiLcdBus::pixels(uint8_t cmd, const uint16_t* px, size_t count) {
  command(cmd, nullptr, 0);   // returns once the previous burst is out
  burst_ = {};
  burst_.length = count * 16;
  burst_.tx_buffer = px;
  burst_.user = (void*)(uintptr_t)(dc_ << 1 | 1);
  pending_ = spi_device_queue_trans(dev_, &burst_, portMAX_DELAY) == ESP_OK;
}

void SpiLcdBus::wait() { drain(); }

void SpiLcdBus::drain() {
  if (!pending_) return;
  spi_transaction_t* done;
  spi_device_get_trans_result(dev_, &done, portMAX_DELAY);
  pending_ = false;
}

}  // namespace esp
}  // namespace hm
