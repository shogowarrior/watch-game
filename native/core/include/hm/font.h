// ui/font.py: bitmap faces from MicroPython framebuf's built-in 8x8 font,
// scaled anisotropically so strokes stay square:
//
//   face     cell   scale (x, y)  tokens
//   MICRO    8x8    1 x 1         type.micro   (battery %, debug)
//   LABEL    8x16   1 x 2         type.label   (chips, toasts, menu)
//   WORD     16x32  2 x 4         type.word / type.numeral
//   DISPLAY  24x48  3 x 6         type.display (countdown digits)
//
// The Python renders each string once into a MONO_HLSB bitmap and blits it;
// here hm::ui::draw_text (text.h) draws each font pixel as its SX x SY block,
// the same pixels with no bitmap to keep.
#pragma once
#include <stdint.h>

#include "hm/ui_tables.h"

namespace hm {
namespace ui {
namespace font {

constexpr int MICRO = 0, LABEL = 1, WORD = 2, DISPLAY = 3;
constexpr const int32_t* SX = U::FACE_SX;
constexpr const int32_t* SY = U::FACE_SY;

// 8 row bytes (MSB = left pixel) of ch: the built-in font, or ui/font.py's
// OVERRIDE (the "1" whose flag breaks up when scaled). Like framebuf.text, a
// char outside ' '..'\x7f' draws as '\x7f'.
const uint8_t* char_rows(char ch);

}  // namespace font
}  // namespace ui
}  // namespace hm
