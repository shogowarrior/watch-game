#include "sf/bma423.h"

namespace sf {

namespace {

int odr_code(int hz) {
  static const int HZ[] = {25, 50, 100, 200, 400, 800, 1600};
  for (int i = 0; i < 7; i++)
    if (HZ[i] == hz) return 6 + i;
  return -1;
}

int range_code(int g) {
  switch (g) {
    case 2: return 0;
    case 4: return 1;
    case 8: return 2;
    case 16: return 3;
    default: return -1;
  }
}

}  // namespace

bool Bma423::init(int odr_hz, int range_g) {
  const int odr = odr_code(odr_hz), rng = range_code(range_g);
  if (odr < 0 || rng < 0) return false;
  i2c_.write8(ADDR, REG_CMD, 0xB6);              // soft reset (the chip NACKs while it resets)
  clock_.delay_ms(2);
  uint8_t id = 0;
  for (int i = 0; i < 50 && !(i2c_.read(ADDR, REG_CHIP_ID, &id, 1) && id == CHIP_ID); i++) clock_.delay_ms(2);
  if (id != CHIP_ID) return false;
  range_mg_ = range_g * 1000;
  bool ok = i2c_.write8(ADDR, REG_PWR_CONF, 0x00);    // adv_power_save off (on after reset)
  clock_.delay_ms(1);
  ok = ok && i2c_.write8(ADDR, REG_ACC_CONF, (uint8_t)(0x80 | 0x20 | odr));   // perf mode, normal filter
  ok = ok && i2c_.write8(ADDR, REG_ACC_RANGE, (uint8_t)rng);
  ok = ok && i2c_.write8(ADDR, REG_FIFO_CONFIG_0, 0x00);   // stream mode, no sensortime frame
  ok = ok && i2c_.write8(ADDR, REG_FIFO_CONFIG_1, 0x40);   // accel in the FIFO, no headers
  return ok && i2c_.write8(ADDR, REG_PWR_CTRL, 0x04);      // accel on
}

int Bma423::fifo_bytes() {
  uint8_t b[2];
  if (!i2c_.read(ADDR, REG_FIFO_LENGTH_0, b, 2)) return -1;
  return b[0] | (b[1] & 0x3F) << 8;
}

int Bma423::fifo_read(uint8_t* buf, int max_frames, int* level) {
  const int bytes = fifo_bytes();
  if (level) *level = bytes;
  if (bytes < 0) return -1;
  int n = bytes / FRAME_BYTES;
  if (n > max_frames) n = max_frames;
  for (int done = 0; done < n;) {
    int k = n - done;
    if (k > READ_CHUNK / FRAME_BYTES) k = READ_CHUNK / FRAME_BYTES;
    if (!i2c_.read(ADDR, REG_FIFO_DATA, buf + done * FRAME_BYTES, (size_t)k * FRAME_BYTES)) return -1;
    done += k;
  }
  return n;
}

int Bma423::decode(const uint8_t* bp, int n, int16_t* op, int rng) {
  int k = 0;
  for (; k < n; k++, bp += FRAME_BYTES) {
    const int32_t x = (int16_t)(bp[0] | bp[1] << 8), y = (int16_t)(bp[2] | bp[3] << 8),
                  z = (int16_t)(bp[4] | bp[5] << 8);
    if (x == -0x8000 && y == -0x8000 && z == -0x8000) break;
    // 12-bit count = v >> 4 (arithmetic); mg = count * range_mg / 2048, rounded
    *op++ = (int16_t)(((x >> 4) * rng + 1024) >> 11);
    *op++ = (int16_t)(((y >> 4) * rng + 1024) >> 11);
    *op++ = (int16_t)(((z >> 4) * rng + 1024) >> 11);
  }
  return k;
}

}  // namespace sf
