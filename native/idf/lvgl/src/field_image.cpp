#include "field_image.h"

#include "hm/field.h"
#include "src/draw/lv_image_decoder_private.h"   // a decoder fills lv_image_decoder_dsc_t

namespace hm {
namespace lvgl {

namespace {

constexpr int W = FIELD_W;

lv_image_dsc_t src;            // 240x240 RGB565 whose data is the ring map: only this decoder reads it
lv_draw_buf_t rows;
alignas(4) uint16_t rows_px[W * DECODE_ROWS];
const uint8_t* map_;
const uint16_t* pal_;

lv_result_t info(lv_image_decoder_t*, lv_image_decoder_dsc_t* dsc, lv_image_header_t* header) {
  if (dsc->src_type != LV_IMAGE_SRC_VARIABLE || dsc->src != &src) return LV_RESULT_INVALID;
  *header = src.header;
  return LV_RESULT_OK;
}

lv_result_t open(lv_image_decoder_t*, lv_image_decoder_dsc_t* dsc) {
  dsc->decoded = nullptr;      // no whole image: LVGL asks for it a few rows at a time
  return LV_RESULT_OK;
}

// The rows of full after the ones in out (at most DECODE_ROWS).
lv_result_t area(lv_image_decoder_t*, lv_image_decoder_dsc_t* dsc, const lv_area_t* full, lv_area_t* out) {
  const int32_t y = out->y1 == LV_COORD_MIN ? full->y1 : out->y2 + 1;
  if (y > full->y2) return LV_RESULT_INVALID;
  const int32_t w = lv_area_get_width(full), h = LV_MIN(DECODE_ROWS, full->y2 - y + 1);
  lv_draw_buf_t* b = lv_draw_buf_reshape(&rows, LV_COLOR_FORMAT_RGB565, w, h, LV_STRIDE_AUTO);
  if (!b) return LV_RESULT_INVALID;
  const int32_t stride = b->header.stride / 2;
  for (int32_t r = 0; r < h; r++) blit((uint16_t*)b->data + r * stride, map_, pal_, (y + r) * W + full->x1, w);
  *out = {full->x1, y, full->x2, y + h - 1};
  dsc->decoded = b;
  return LV_RESULT_OK;
}

}  // namespace

const void* field_image(const uint8_t* map, const uint16_t* pal) {
  map_ = map;
  pal_ = pal;
  src.data = map;              // LVGL skips an image with no data
  src.data_size = W * W;
  if (src.header.magic) return &src;
  src.header.magic = LV_IMAGE_HEADER_MAGIC;
  src.header.cf = LV_COLOR_FORMAT_RGB565;
  src.header.w = W;
  src.header.h = W;
  src.header.stride = W * 2;
  lv_draw_buf_init(&rows, W, DECODE_ROWS, LV_COLOR_FORMAT_RGB565, LV_STRIDE_AUTO, rows_px, sizeof rows_px);
  lv_image_decoder_t* d = lv_image_decoder_create();
  lv_image_decoder_set_info_cb(d, info);
  lv_image_decoder_set_open_cb(d, open);
  lv_image_decoder_set_get_area_cb(d, area);
  return &src;
}

}  // namespace lvgl
}  // namespace hm
