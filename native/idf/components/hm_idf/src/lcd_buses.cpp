#include <string.h>

#include "driver/gpio.h"
#include "hal/gpio_ll.h"
#include "hal/spi_ll.h"
#include "hm/idf.h"

namespace hm {
namespace idf {

namespace {

constexpr spi_host_device_t HOST = SPI2_HOST;
constexpr int PIN_SCK = 18, PIN_MOSI = 19, PIN_CS = 5, PIN_DC = 27;
constexpr size_t MAX_TRANSFER = 240 * 24 * 2;

}  // namespace

bool spi_bus_begin() {
  spi_bus_config_t b = {};
  b.mosi_io_num = PIN_MOSI;
  b.miso_io_num = -1;
  b.sclk_io_num = PIN_SCK;
  b.quadwp_io_num = -1;
  b.quadhd_io_num = -1;
  b.max_transfer_sz = MAX_TRANSFER;
  return spi_bus_initialize(HOST, &b, SPI_DMA_CH_AUTO) == ESP_OK;
}

// ---- esp_lcd ------------------------------------------------------------------
bool EspLcdBus::set_clock(uint32_t hz) {
  if (io_) esp_lcd_panel_io_del(io_);
  io_ = nullptr;
  esp_lcd_panel_io_spi_config_t c = {};
  c.cs_gpio_num = PIN_CS;
  c.dc_gpio_num = PIN_DC;
  c.spi_mode = 0;
  c.pclk_hz = hz;
  c.trans_queue_depth = 2;
  c.lcd_cmd_bits = 8;
  c.lcd_param_bits = 8;
  return esp_lcd_new_panel_io_spi((esp_lcd_spi_bus_handle_t)HOST, &c, &io_) == ESP_OK;
}

void EspLcdBus::command(uint8_t cmd, const uint8_t* d, size_t n) { esp_lcd_panel_io_tx_param(io_, cmd, d, n); }

void EspLcdBus::pixels(uint8_t cmd, const uint16_t* px, size_t count) {
  esp_lcd_panel_io_tx_color(io_, cmd, px, count * 2);
}

void EspLcdBus::wait() { esp_lcd_panel_io_tx_param(io_, -1, nullptr, 0); }   // no command: just drains

// ---- registers ----------------------------------------------------------------
bool RegDmaBus::set_clock(uint32_t hz) {
  release();
  spi_device_interface_config_t c = {};
  c.clock_speed_hz = (int)hz;
  c.mode = 0;
  c.spics_io_num = PIN_CS;
  c.queue_size = 1;
  c.flags = SPI_DEVICE_HALFDUPLEX | SPI_DEVICE_NO_DUMMY;
  if (spi_bus_add_device(HOST, &c, &dev_) != ESP_OK) return false;
  gpio_config_t g = {};
  g.pin_bit_mask = 1ULL << PIN_DC;
  g.mode = GPIO_MODE_OUTPUT;
  gpio_config(&g);
  // One driver transfer (a NOP) leaves this device's clock, mode and CS in the
  // registers; holding the bus keeps the driver off them from here on.
  spi_device_acquire_bus(dev_, portMAX_DELAY);
  spi_transaction_t t = {};
  t.flags = SPI_TRANS_USE_TXDATA;
  t.length = 8;
  gpio_ll_set_level(&GPIO, PIN_DC, 0);
  return spi_device_polling_transmit(dev_, &t) == ESP_OK;
}

void RegDmaBus::release() {
  if (!dev_) return;
  wait();
  spi_device_release_bus(dev_);
  spi_bus_remove_device(dev_);
  dev_ = nullptr;
}

void RegDmaBus::send(const void* p, size_t n) {
  spi_dev_t* hw = SPI_LL_GET_HW(HOST);
  const uint8_t* b = static_cast<const uint8_t*>(p);
  int k = 0;
  for (size_t left = n; left; k++) {
    const size_t c = left < DESC_BYTES ? left : DESC_BYTES;
    lldesc_t& d = desc_[k];
    d.size = c;
    d.length = c;
    d.offset = 0;
    d.sosf = 0;
    left -= c;
    d.eof = left == 0;
    d.owner = 1;
    d.buf = const_cast<uint8_t*>(b);
    d.qe.stqe_next = left ? &desc_[k + 1] : nullptr;
    b += c;
  }
  // As spi_master starts a DMA transfer (spi_hal_setup_trans, s_spi_dma_prepare_data).
  spi_ll_clear_int_stat(hw);
  spi_ll_set_mosi_bitlen(hw, n * 8);
  spi_dma_ll_tx_reset(hw, 0);
  spi_ll_dma_tx_fifo_reset(hw);
  spi_dma_ll_tx_start(hw, 0, desc_);
  spi_ll_user_start(hw);
  busy_ = true;
}

void RegDmaBus::wait() {
  if (!busy_) return;
  spi_dev_t* hw = SPI_LL_GET_HW(HOST);
  while (spi_ll_get_running_cmd(hw)) {
  }
  busy_ = false;
}

void RegDmaBus::command(uint8_t cmd, const uint8_t* d, size_t n) {
  wait();
  gpio_ll_set_level(&GPIO, PIN_DC, 0);
  cmd_[0] = cmd;
  send(cmd_, 1);
  wait();
  if (!n || n > sizeof par_) return;
  memcpy(par_, d, n);
  gpio_ll_set_level(&GPIO, PIN_DC, 1);
  send(par_, n);
  wait();
}

void RegDmaBus::pixels(uint8_t cmd, const uint16_t* px, size_t count) {
  command(cmd, nullptr, 0);
  gpio_ll_set_level(&GPIO, PIN_DC, 1);
  const uint8_t* b = reinterpret_cast<const uint8_t*>(px);
  for (size_t left = count * 2; left;) {   // the last piece is still on the wire on return
    const size_t c = left < MAX_DESC * DESC_BYTES ? left : MAX_DESC * DESC_BYTES;
    wait();
    send(b, c);
    b += c;
    left -= c;
  }
}

}  // namespace idf
}  // namespace hm
