// Display and motion-sensor benchmark on ESP-IDF with esp_lcd's SPI panel IO
// (DMA). Steps and log lines are the shared ones in native/core (sf::Bench).
#include <string.h>

#include "driver/i2c_master.h"
#include "driver/spi_master.h"
#include "esp_lcd_panel_io.h"
#include "esp_system.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "sf/bench.h"
#include "sf/esp32.h"

namespace {

// I2C0 (AXP202, BMA423) on the IDF 5 master driver, one device handle per address.
class IdfI2c : public sf::I2c {
 public:
  bool begin() {
    i2c_master_bus_config_t c = {};
    c.i2c_port = I2C_NUM_0;
    c.sda_io_num = GPIO_NUM_21;
    c.scl_io_num = GPIO_NUM_22;
    c.clk_source = I2C_CLK_SRC_DEFAULT;
    c.glitch_ignore_cnt = 7;
    if (i2c_new_master_bus(&c, &bus_) != ESP_OK) return false;
    for (int i = 0; i < N; i++) {
      i2c_device_config_t d = {};
      d.dev_addr_length = I2C_ADDR_BIT_LEN_7;
      d.device_address = ADDRS[i];
      d.scl_speed_hz = 400000;
      if (i2c_master_bus_add_device(bus_, &d, &dev_[i]) != ESP_OK) return false;
    }
    return true;
  }
  bool write(uint8_t addr, uint8_t reg, const uint8_t* d, size_t n) override {
    i2c_master_dev_handle_t h = dev(addr);
    uint8_t buf[17];
    if (!h || n > sizeof buf - 1) return false;
    buf[0] = reg;
    memcpy(buf + 1, d, n);
    return i2c_master_transmit(h, buf, n + 1, TIMEOUT_MS) == ESP_OK;
  }
  bool read(uint8_t addr, uint8_t reg, uint8_t* d, size_t n) override {
    i2c_master_dev_handle_t h = dev(addr);
    return h && i2c_master_transmit_receive(h, &reg, 1, d, n, TIMEOUT_MS) == ESP_OK;
  }

 private:
  static constexpr int N = 2, TIMEOUT_MS = 50;
  static constexpr uint8_t ADDRS[N] = {0x35, 0x19};   // AXP202, BMA423
  i2c_master_dev_handle_t dev(uint8_t addr) {
    for (int i = 0; i < N; i++)
      if (ADDRS[i] == addr) return dev_[i];
    return nullptr;
  }
  i2c_master_bus_handle_t bus_ = nullptr;
  i2c_master_dev_handle_t dev_[N] = {};
};

// esp_lcd: a command first drains the queued pixel bursts, so at most one strip
// is on the wire while the next is drawn. A new clock means a new panel IO.
class EspLcdBus : public sf::LcdBus {
 public:
  bool begin() {
    spi_bus_config_t b = {};
    b.mosi_io_num = 19;
    b.miso_io_num = -1;   // HSPI's default MISO is GPIO12, the backlight
    b.sclk_io_num = 18;
    b.quadwp_io_num = -1;
    b.quadhd_io_num = -1;
    b.max_transfer_sz = sf::FIELD_W * sf::Bench::SH * 2;
    return spi_bus_initialize(SPI2_HOST, &b, SPI_DMA_CH_AUTO) == ESP_OK;
  }
  bool set_clock(uint32_t hz) override {
    if (io_) esp_lcd_panel_io_del(io_);
    io_ = nullptr;
    esp_lcd_panel_io_spi_config_t c = {};
    c.cs_gpio_num = 5;
    c.dc_gpio_num = 27;
    c.spi_mode = 0;
    c.pclk_hz = hz;
    c.trans_queue_depth = 2;
    c.lcd_cmd_bits = 8;
    c.lcd_param_bits = 8;
    return esp_lcd_new_panel_io_spi((esp_lcd_spi_bus_handle_t)SPI2_HOST, &c, &io_) == ESP_OK;
  }
  void command(uint8_t cmd, const uint8_t* d, size_t n) override { esp_lcd_panel_io_tx_param(io_, cmd, d, n); }
  void pixels(uint8_t cmd, const uint16_t* px, size_t count) override {
    esp_lcd_panel_io_tx_color(io_, cmd, px, count * 2);
  }
  void wait() override { esp_lcd_panel_io_tx_param(io_, -1, nullptr, 0); }   // no command: just drains

 private:
  esp_lcd_panel_io_handle_t io_ = nullptr;
};

char framework[64];
sf::esp::EspClock clock_;
IdfI2c i2c0;
EspLcdBus lcd;
sf::esp::CoreImuTask imu;
sf::BenchHost host{"idf-esplcd", framework, clock_, lcd, i2c0, imu, sf::esp::backlight};
sf::Bench bench(host);

void bench_task(void*) {
  // After the run: the HOT field at 20, 30 and 60 fps in turn, 10 s each, for the eye.
  static const int targets[] = {20, 30, 60};
  if (bench.setup()) {
    bench.run();
    for (int i = 0;; i++) bench.show(sf::Bench::CLOCKS[1], targets[i % 3], 10);
  }
  for (;;) vTaskDelay(pdMS_TO_TICKS(1000));
}

}  // namespace

extern "C" void app_main() {
  snprintf(framework, sizeof framework, "esp-idf_%s_esp_lcd", esp_get_idf_version());
  if (!i2c0.begin()) {
    sf::logf("SF error what=i2c_bus");
    return;
  }
  if (!lcd.begin()) {
    sf::logf("SF error what=spi_bus");
    return;
  }
  // The renderer gets core 1; the motion-sensor task and the system tasks keep core 0.
  xTaskCreatePinnedToCore(bench_task, "sf_bench", 8192, nullptr, 5, nullptr, 1);
}
