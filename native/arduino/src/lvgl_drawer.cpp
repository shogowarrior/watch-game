#include "lvgl_drawer.h"

#include <lvgl.h>
#include <lvgl_private.h>   // the draw unit and draw task structs

namespace {

constexpr int W = hm::FIELD_W;

// The ring map with a colour format of our own: the field unit paints it through
// this frame's palette, nothing decodes it.
lv_image_dsc_t field_image;
const uint16_t* frame_pal = nullptr;
hm::St7789* flush_panel = nullptr;
hm::Clock* tick_clock = nullptr;

int32_t field_evaluate(lv_draw_unit_t* u, lv_draw_task_t* t) {
  if (t->type != LV_DRAW_TASK_TYPE_IMAGE || ((lv_draw_image_dsc_t*)t->draw_dsc)->src != &field_image) return 0;
  t->preference_score = 0;
  t->preferred_draw_unit_id = (uint8_t)u->idx;
  return 1;
}

void paint(lv_draw_task_t* t) {
  // The visible rows of the image, one palette lookup per pixel, into the layer's buffer.
  lv_layer_t* layer = t->target_layer;
  lv_area_t a;
  if (!lv_area_intersect(&a, &t->area, &t->clip_area)) return;
  const int32_t w = lv_area_get_width(&a);
  for (int32_t y = a.y1; y <= a.y2; y++) {
    auto* dst = (uint16_t*)lv_draw_buf_goto_xy(layer->draw_buf, a.x1 - layer->buf_area.x1, y - layer->buf_area.y1);
    hm::blit(dst, field_image.data, frame_pal, (y - t->area.y1) * W + (a.x1 - t->area.x1), w);
  }
}

int32_t field_dispatch(lv_draw_unit_t* u, lv_layer_t* layer) {
  lv_draw_task_t* t = lv_draw_get_available_task(layer, nullptr, (uint8_t)u->idx);
  if (!t || t->preferred_draw_unit_id != u->idx) return LV_DRAW_UNIT_IDLE;
  if (!lv_draw_layer_alloc_buf(layer)) {
    t->state = LV_DRAW_TASK_STATE_FAILED;
    return LV_DRAW_UNIT_IDLE;
  }
  t->state = LV_DRAW_TASK_STATE_IN_PROGRESS;
  t->draw_unit = u;
  paint(t);
  t->state = LV_DRAW_TASK_STATE_FINISHED;
  lv_draw_dispatch_request();
  return 1;
}

void flush(lv_display_t* d, const lv_area_t* a, uint8_t* px) {
  // Each buffer is its own window. The bus returns once the previous burst is out,
  // so LVGL may draw into the other buffer at once while this one is on the wire.
  flush_panel->begin_window(a->x1, a->y1, lv_area_get_width(a), lv_area_get_height(a));
  flush_panel->push_pixels((const uint16_t*)px, lv_area_get_size(a));
  lv_display_flush_ready(d);
}

}  // namespace

bool LvglDrawer::start() {
  flush_panel = &panel_;
  tick_clock = &clock_;
  lv_init();
  lv_tick_set_cb([]() { return (uint32_t)tick_clock->now_ms(); });
  disp_ = lv_display_create(W, W);
  lv_display_set_color_format(disp_, LV_COLOR_FORMAT_RGB565_SWAPPED);   // MSB first, as the panel wants
  // Two 240x24 buffers from the heap: internal RAM without PSRAM, so DMA can send them.
  const uint32_t size = W * hm::Bench::SH * sizeof(uint16_t);
  void* a = lv_malloc(size);
  void* b = lv_malloc(size);
  if (!a || !b) return false;
  lv_display_set_buffers(disp_, a, b, size, LV_DISPLAY_RENDER_MODE_PARTIAL);
  lv_display_set_flush_cb(disp_, flush);

  auto* unit = (lv_draw_unit_t*)lv_draw_create_unit(sizeof(lv_draw_unit_t));
  unit->evaluate_cb = field_evaluate;
  unit->dispatch_cb = field_dispatch;
  unit->name = "HM_FIELD";

  field_image.header.magic = LV_IMAGE_HEADER_MAGIC;
  field_image.header.cf = LV_COLOR_FORMAT_PROPRIETARY_START;
  field_image.header.w = W;
  field_image.header.h = W;
  return true;
}

void LvglDrawer::frame(const uint16_t* pal, const uint8_t* ring_map) {
  frame_pal = pal;
  if (!image_) {   // the first frame brings the ring map, the image's data
    field_image.data = ring_map;
    field_image.data_size = W * W;
    image_ = lv_image_create(lv_display_get_screen_active(disp_));
    lv_image_set_src(image_, &field_image);
  }
  lv_obj_invalidate(image_);
  lv_refr_now(disp_);
}
