// The bench through LovyanGFX's SPI bus: DMA bursts that overlap the next strip's drawing.
#include "lgfx_bus.h"

BenchEnv& bench_env() {
  static LgfxBus lcd;
  static BenchEnv env{"arduino-lovyangfx", HM_LGFX_LIBRARY, lcd, nullptr};
  return env;
}
