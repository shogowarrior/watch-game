// The bench through TFT_eSPI: commands by register writes, pixels by its DMA
// device (pushPixelsDMA), which overlaps the next strip's drawing.
#include <TFT_eSPI.h>

#include "bench_env.h"

uint32_t hm_tft_spi_hz;   // SPI_FREQUENCY (include/tft_setup.h)

namespace {

// One write transaction stays open (startWrite); dmaWait() before every register
// write, since those share the SPI peripheral with the DMA device.
class TftEspiBus : public ArduinoLcd {
 public:
  bool begin(uint32_t hz) override {
    hm_tft_spi_hz = hz;
    digitalWrite(5, LOW);       // CS: TFT_CS is -1, so the library leaves it alone
    pinMode(5, OUTPUT);
    tft_.init();                // its own ST7789 init; the bench's SWRESET and init follow
    if (!tft_.initDMA()) return false;
    tft_.startWrite();
    return true;
  }
  bool set_clock(uint32_t hz) override {
    // The DMA device takes its clock when added: re-add it.
    tft_.dmaWait();
    tft_.endWrite();
    tft_.deInitDMA();
    hm_tft_spi_hz = hz;
    const bool ok = tft_.initDMA();
    tft_.startWrite();
    return ok;
  }
  void command(uint8_t cmd, const uint8_t* d, size_t n) override {
    tft_.dmaWait();
    tft_.writecommand(cmd);
    for (size_t i = 0; i < n; i++) tft_.writedata(d[i]);
  }
  void pixels(uint8_t cmd, const uint16_t* px, size_t count) override {
    tft_.dmaWait();
    tft_.writecommand(cmd);
    tft_.pushPixelsDMA(const_cast<uint16_t*>(px), count);   // reads only (byte swap is off)
  }
  void wait() override { tft_.dmaWait(); }

 private:
  TFT_eSPI tft_;
};

}  // namespace

BenchEnv& bench_env() {
  static TftEspiBus lcd;
  static BenchEnv env{"arduino-tft_espi", "tft_espi_" TFT_ESPI_VERSION, lcd, nullptr};
  return env;
}
