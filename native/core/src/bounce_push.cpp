#include "hm/bounce_push.h"

#include <string.h>

namespace hm {

void push_bounced(St7789& panel, const uint16_t* src, int y0, int rows, uint16_t* const bounce[2], int chunk_rows) {
  panel.begin_window(0, y0, St7789::W, rows);
  int k = 0;
  for (int y = 0; y < rows; y += chunk_rows) {
    const size_t n = (size_t)St7789::W * (rows - y < chunk_rows ? rows - y : chunk_rows);
    // Free to refill: pixels() returned after the burst before last was out (LcdBus).
    memcpy(bounce[k], src + (size_t)St7789::W * y, n * 2);
    panel.push_pixels(bounce[k], n);
    k ^= 1;
  }
}

}  // namespace hm
