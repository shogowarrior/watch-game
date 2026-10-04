// The ripple field as an LVGL image source. A custom decoder fills the rows
// LVGL asks for, a strip at a time, from the ring map through the palette, so
// no 240x240 image exists and the pixels are the ones the other builds send.
#pragma once
#include <stdint.h>

#include "lvgl.h"

namespace hm {
namespace lvgl {

constexpr int DECODE_ROWS = 24;   // rows per decode, at most

// Registers the decoder (once) and returns the source for lv_image_set_src.
// map: the 240x240 ring-index map; pal: 256 RGB565 colours in LVGL's byte
// order, read whenever LVGL draws the image (invalidate it after a change).
const void* field_image(const uint8_t* map, const uint16_t* pal);

}  // namespace lvgl
}  // namespace hm
