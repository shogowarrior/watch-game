// LVGL 9 through esp_lvgl_port on esp_lcd (SPI with DMA, 40 MHz). The ripple
// field is an LVGL image whose rows a custom decoder fills from the ring map
// through the field palette, so the pixels match the other builds; the HOT
// screen's chips are LVGL labels on top; and a second scene has LVGL draw a
// ring field itself from arcs. The same "HM" lines as hm::Bench, plus "HM lvgl".
#include <stdio.h>

#include "esp_heap_caps.h"
#include "esp_lcd_panel_ops.h"
#include "esp_lcd_panel_st7789.h"
#include "esp_lvgl_port.h"
#include "esp_system.h"
#include "field_image.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "hm/axp202.h"
#include "hm/bench.h"
#include "hm/bench_scene.h"
#include "hm/esp32.h"
#include "hm/idf.h"
#include "hm/st7789.h"
#include "hm/tuning.h"

namespace {

constexpr uint32_t HZ = 40000000;
constexpr int W = hm::FIELD_W, ROWS = hm::lvgl::DECODE_ROWS;   // LVGL renders 24-row strips into two buffers
constexpr uint32_t SEG_US = 3000000;            // one timed run, as hm::Bench
constexpr int TARGETS[] = {0, 10, 20, 30};      // 0 = unlocked
constexpr int ARCS_TARGETS[] = {0, 30};
const hm::FieldParams& HOT = hm::BENCH_FIXTURES[1];

enum Scene { FIELD, FIELD_CHIPS, ARCS };
const char* const STEP[] = {"run", "run_chips", "run_arcs"};

char framework[64];
hm::esp::EspClock clock_;
hm::idf::I2c0 i2c0;
hm::idf::EspLcdBus lcd;
hm::RippleField field;
hm::FieldScene scene(field);
alignas(4) uint8_t ring_map[W * W];
uint16_t pal[256];                              // field.pal in LVGL's byte order
hm::RunStats run_;

lv_display_t* disp;
lv_obj_t* field_img;
lv_obj_t* chips[2];
lv_obj_t* arcs;
uint32_t refr_us, wait_us, wait_t0;             // per run: inside lv_refr_now, and of that waiting for DMA

// ---- the HOT screen: chips (ui-spec §2) and LVGL's own rings -----------------
lv_color_t token(int32_t swapped565) {   // finder/tuning.py colours are byte-swapped RGB565
  const uint32_t c = (uint32_t)((swapped565 & 0xFF) << 8 | (swapped565 >> 8 & 0xFF));
  return lv_color_make((uint8_t)(c >> 8 & 0xF8), (uint8_t)(c >> 3 & 0xFC), (uint8_t)(c << 3 & 0xF8));
}

lv_obj_t* chip(const char* text, const lv_font_t* font, int32_t color, int32_t y) {
  lv_obj_t* l = lv_label_create(lv_screen_active());
  lv_label_set_text(l, text);
  lv_obj_set_style_text_font(l, font, 0);
  lv_obj_set_style_text_color(l, token(color), 0);
  lv_obj_set_style_bg_color(l, token(hm::T::C_SURFACE_CHIP), 0);
  lv_obj_set_style_bg_opa(l, LV_OPA_COVER, 0);
  lv_obj_set_style_radius(l, LV_RADIUS_CIRCLE, 0);
  lv_obj_set_style_pad_hor(l, 12, 0);
  lv_obj_set_style_pad_ver(l, 4, 0);
  lv_obj_align(l, LV_ALIGN_TOP_MID, 0, y);
  return l;
}

// HOT's rings as LVGL arcs: 120 px/s, 60 px apart, 18 px wide, dimmer outwards.
void arcs_draw(lv_event_t* e) {
  lv_layer_t* layer = lv_event_get_layer(e);
  lv_draw_arc_dsc_t a;
  lv_draw_arc_dsc_init(&a);
  a.center = {W / 2, W / 2};
  a.start_angle = 0;
  a.end_angle = 360;
  a.width = 18;
  const int32_t phase = (int32_t)((int64_t)clock_.now_ms() * 120 / 1000 % 60);
  for (int32_t r = 30 + phase; r < 170; r += 60) {
    a.radius = r;
    a.color = lv_color_make(0, (uint8_t)(255 - r), (uint8_t)((255 - r) / 3));
    lv_draw_arc(layer, &a);
  }
}

void show(Scene s) {
  const bool field_on = s != ARCS;
  lv_obj_set_flag(field_img, LV_OBJ_FLAG_HIDDEN, !field_on);
  lv_obj_set_flag(arcs, LV_OBJ_FLAG_HIDDEN, field_on);
  for (lv_obj_t* c : chips) lv_obj_set_flag(c, LV_OBJ_FLAG_HIDDEN, s != FIELD_CHIPS);
}

void on_wait(lv_event_t* e) {
  if (lv_event_get_code(e) == LV_EVENT_FLUSH_WAIT_START) wait_t0 = clock_.now_us();
  else wait_us += clock_.now_us() - wait_t0;
}

void frame(void* ctx) {
  const Scene s = *static_cast<const Scene*>(ctx);
  lvgl_port_lock(0);
  if (s == ARCS) {
    lv_obj_invalidate(arcs);
  } else {
    scene.step(HOT, clock_.now_ms());
    for (int i = 0; i < hm::N_IDX; i++) pal[i] = __builtin_bswap16(field.pal[i]);
    lv_obj_invalidate(field_img);
  }
  const uint32_t t0 = clock_.now_us();
  lv_refr_now(disp);
  refr_us += clock_.now_us() - t0;
  lvgl_port_unlock();
}

void run(Scene s, int target, uint32_t dur_us, bool log) {
  show(s);
  refr_us = wait_us = 0;
  hm::pace(clock_, run_, target, dur_us, frame, &s);
  if (!log) return;
  hm::log_run(STEP[s], HZ, target, run_);
  const uint32_t n = run_.frames ? run_.frames : 1;
  hm::logf("HM lvgl step=%s target=%d refr_us=%u wait_us=%u cpu_us=%u", STEP[s], target, (unsigned)(refr_us / n),
           (unsigned)(wait_us / n), (unsigned)((refr_us - wait_us) / n));
  clock_.delay_ms(20);   // let the idle task run (task watchdog)
}

// ---- setup ------------------------------------------------------------------
bool panel_and_lvgl() {
  if (!hm::axp202::panel_power_on(i2c0)) return false;
  clock_.delay_ms(10);
  if (!lcd.set_clock(HZ)) return false;
  uint16_t* black = static_cast<uint16_t*>(heap_caps_malloc(W * ROWS * 2, MALLOC_CAP_DMA));   // clears GRAM
  if (!black) return false;
  hm::St7789 st(lcd, clock_);
  st.init(black, ROWS);
  heap_caps_free(black);
  hm::esp::backlight(true);

  // esp_lcd's ST7789 driver only sends windows and pixels here: the init above
  // is ours (as hal/st7789.py), and mirror x+y below is MADCTL 0xC0 again.
  esp_lcd_panel_handle_t panel;
  esp_lcd_panel_dev_config_t pc = {};
  pc.reset_gpio_num = -1;
  pc.rgb_ele_order = LCD_RGB_ELEMENT_ORDER_RGB;
  pc.bits_per_pixel = 16;
  if (esp_lcd_new_panel_st7789(lcd.io(), &pc, &panel) != ESP_OK) return false;
  esp_lcd_panel_set_gap(panel, 0, hm::St7789::ROW_OFFSET);

  lvgl_port_cfg_t cfg = ESP_LVGL_PORT_INIT_CONFIG();
  cfg.task_affinity = 1;
  if (lvgl_port_init(&cfg) != ESP_OK) return false;
  lvgl_port_display_cfg_t dc = {};
  dc.io_handle = lcd.io();
  dc.panel_handle = panel;
  dc.buffer_size = W * ROWS;
  dc.double_buffer = true;
  dc.hres = W;
  dc.vres = W;
  dc.rotation.mirror_x = true;
  dc.rotation.mirror_y = true;
  dc.color_format = LV_COLOR_FORMAT_RGB565;
  dc.flags.buff_dma = 1;
  dc.flags.swap_bytes = 1;                      // LVGL draws native RGB565; the panel wants MSB first
  disp = lvgl_port_add_disp(&dc);
  if (!disp) return false;
  lvgl_port_stop();                             // frames come from frame(), paced like hm::Bench
  lv_display_add_event_cb(disp, on_wait, LV_EVENT_FLUSH_WAIT_START, nullptr);
  lv_display_add_event_cb(disp, on_wait, LV_EVENT_FLUSH_WAIT_FINISH, nullptr);

  lvgl_port_lock(0);
  lv_obj_t* scr = lv_screen_active();
  lv_obj_set_style_bg_color(scr, lv_color_black(), 0);
  field_img = lv_image_create(scr);
  lv_image_set_src(field_img, hm::lvgl::field_image(ring_map, pal));
  arcs = lv_obj_create(scr);
  lv_obj_remove_style_all(arcs);
  lv_obj_set_size(arcs, W, W);
  lv_obj_add_event_cb(arcs, arcs_draw, LV_EVENT_DRAW_MAIN, nullptr);
  chips[0] = chip("LOOK AROUND", &lv_font_montserrat_14, hm::T::C_TEXT_PRIMARY, 12);
  chips[1] = chip("~5 M", &lv_font_montserrat_28, hm::T::C_TEXT_SECONDARY, 186);
  lvgl_port_unlock();
  return true;
}

void bench_task(void*) {
  hm::logf("HM hello variant=idf-lvgl framework=%s", framework);
  const uint32_t t0 = clock_.now_us();
  hm::build_map(ring_map, W);
  hm::logf("HM setup map_us=%u", (unsigned)(clock_.now_us() - t0));
  if (!panel_and_lvgl()) {
    hm::logf("HM error what=lvgl_setup");
  } else {
    hm::logf("HM lvgl buf_rows=%d heap_free=%u heap_min=%u", ROWS, (unsigned)esp_get_free_heap_size(),
             (unsigned)esp_get_minimum_free_heap_size());
    for (int t : TARGETS) run(FIELD, t, SEG_US, true);
    for (int t : TARGETS) run(FIELD_CHIPS, t, SEG_US, true);
    for (int t : ARCS_TARGETS) run(ARCS, t, SEG_US, true);
    hm::logf("HM done");
    // After the run: the chips over the field, then LVGL's arcs, 10 s each at 30 fps, for the eye.
    for (int i = 0;; i++) run(i % 2 ? ARCS : FIELD_CHIPS, 30, 10000000, false);
  }
  for (;;) vTaskDelay(pdMS_TO_TICKS(1000));
}

}  // namespace

extern "C" void app_main() {
  snprintf(framework, sizeof framework, "esp-idf_%s_lvgl_%d.%d.%d", esp_get_idf_version(), LVGL_VERSION_MAJOR,
           LVGL_VERSION_MINOR, LVGL_VERSION_PATCH);
  if (!i2c0.begin() || !hm::idf::spi_bus_begin()) {
    hm::logf("HM error what=bus");
    return;
  }
  // The renderer gets core 1; the system tasks keep core 0.
  xTaskCreatePinnedToCore(bench_task, "hm_lvgl", 8192, nullptr, 5, nullptr, 1);
}
