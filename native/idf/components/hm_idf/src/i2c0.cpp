#include <string.h>

#include "hm/idf.h"

namespace hm {
namespace idf {

namespace {

bool add(i2c_master_bus_handle_t bus, uint8_t addr, uint32_t hz, i2c_master_dev_handle_t* dev) {
  i2c_device_config_t d = {};
  d.dev_addr_length = I2C_ADDR_BIT_LEN_7;
  d.device_address = addr;
  d.scl_speed_hz = hz;
  return i2c_master_bus_add_device(bus, &d, dev) == ESP_OK;
}

}  // namespace

bool I2c0::begin() {
  i2c_master_bus_config_t c = {};
  c.i2c_port = I2C_NUM_0;
  c.sda_io_num = GPIO_NUM_21;
  c.scl_io_num = GPIO_NUM_22;
  c.clk_source = I2C_CLK_SRC_DEFAULT;
  c.glitch_ignore_cnt = 7;
  if (i2c_new_master_bus(&c, &bus_) != ESP_OK) return false;
  for (int i = 0; i < N; i++)
    if (!add(bus_, ADDRS[i], HZ, &dev_[i])) return false;
  return true;
}

int I2c0::index(uint8_t addr) const {
  for (int i = 0; i < N; i++)
    if (ADDRS[i] == addr) return i;
  return -1;
}

bool I2c0::set_clock(uint8_t addr, uint32_t hz) {
  const int i = index(addr);
  if (i < 0 || i2c_master_bus_rm_device(dev_[i]) != ESP_OK) return false;
  dev_[i] = nullptr;
  return add(bus_, addr, hz, &dev_[i]);
}

bool I2c0::write(uint8_t addr, uint8_t reg, const uint8_t* d, size_t n) {
  const int i = index(addr);
  uint8_t buf[17];
  if (i < 0 || !dev_[i] || n > sizeof buf - 1) return false;
  buf[0] = reg;
  memcpy(buf + 1, d, n);
  return i2c_master_transmit(dev_[i], buf, n + 1, TIMEOUT_MS) == ESP_OK;
}

bool I2c0::read(uint8_t addr, uint8_t reg, uint8_t* d, size_t n) {
  const int i = index(addr);
  return i >= 0 && dev_[i] && i2c_master_transmit_receive(dev_[i], &reg, 1, d, n, TIMEOUT_MS) == ESP_OK;
}

}  // namespace idf
}  // namespace hm
