// The ripple field as an LVGL 9 image. LVGL owns the frame (invalidation, two
// 240x24 partial buffers, one flush window per buffer) and a small draw unit
// paints the image straight from the ring map and this frame's palette, the
// palette-cycling trick the strip loop uses. No Arduino calls, so it runs on a host too.
#pragma once
#include "hm/bench.h"
#include "hm/st7789.h"

struct _lv_display_t;
struct _lv_obj_t;

class LvglDrawer : public hm::FrameDrawer {
 public:
  LvglDrawer(hm::LcdBus& bus, hm::Clock& clock) : panel_(bus, clock), clock_(clock) {}
  // lv_init, the display and the field's draw unit; the bus must be up. False: out of memory.
  bool start();
  void frame(const uint16_t* pal, const uint8_t* ring_map) override;

 private:
  hm::St7789 panel_;            // windows only: the bench has already initialised the panel
  hm::Clock& clock_;
  _lv_display_t* disp_ = nullptr;
  _lv_obj_t* image_ = nullptr;
};
