// Pixels from memory the SPI DMA cannot read (the ESP32's PSRAM, where
// MicroPython keeps every buffer) go out through two DMA-capable buffers:
// one chunk is copied in while the previous one is on the wire.
#pragma once
#include <stdint.h>

#include "hm/st7789.h"

namespace hm {

// Opens a full-width window over rows y0..y0+rows-1 and sends ``src`` (rows
// of St7789::W pixels) in chunks of ``chunk_rows``. Returns with the last
// chunk on the wire: panel.end_frame() waits for it.
void push_bounced(St7789& panel, const uint16_t* src, int y0, int rows, uint16_t* const bounce[2], int chunk_rows);

}  // namespace hm
