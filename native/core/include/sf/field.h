// RippleField: ring-index map, ramp LUTs and the per-frame palette (ui-spec §4).
// A line-for-line port of ui/field.py: the same Q8 integer maths, so a frame
// matches the MicroPython renderer's palette exactly (native/test checks it).
#pragma once
#include <stdint.h>

#include "sf/field_tables.h"
#include "sf/ticks.h"

namespace sf {

constexpr int FIELD_W = 240;
constexpr int N_IDX = 170;      // ring indices 0..169 (the map's max is 168, the corner)
constexpr int LUT_N = 64;
constexpr int MAXR = 12;        // ring slots
constexpr int32_t Q8 = 256;
constexpr int32_t V7 = 1792;    // level 7 in Q8

enum Ramp : uint8_t { RAMP_GREEN, RAMP_GOLD, RAMP_GREY, RAMP_N };   // T::RAMP_NAMES order

// Read-only ramp LUT: c = swapped RGB565, r/g/b = 8-bit channels (for the hue crossfade).
struct LutSrc {
  const uint16_t* c;
  const uint8_t* r;
  const uint8_t* g;
  const uint8_t* b;
};
const LutSrc& ramp_lut(int ramp);   // unknown ramps fall back to green, as LUTS.get(name) or green

struct Lut {
  uint16_t* c;                      // may point into the palette table (the mixed LUT)
  uint8_t r[LUT_N], g[LUT_N], b[LUT_N];
  void copy_from(const LutSrc& o);
  void copy_from(const Lut& o);
};

// GS8 ring-index map rows (idx = floor(hypot(x-119.5, y-119.5))): W*rows bytes.
void build_map(uint8_t* buf, int rows);
// Map bytes off..off+n-1 through the palette into dst (one RGB565 pixel per byte).
void blit(uint16_t* dst, const uint8_t* idx, const uint16_t* pal, int off, int n);
// One fused pass over ring indices 0..169 (ui/field.py pal_kernel); clears acc.
void pal_kernel(uint16_t* pal, int32_t* acc, const uint16_t* tab, const int32_t* prm);

class RippleField {
 public:
  RippleField();
  RippleField(const RippleField&) = delete;
  RippleField& operator=(const RippleField&) = delete;

  void reset();
  ticks_t tick(ticks_t t, int fps_cap);   // returns the previous t
  void hold(int32_t dt);
  int spawn(ticks_t t0, int32_t r0q, int32_t v, int32_t amp, int32_t lead, int32_t trail, bool ghost);
  int schedule(ticks_t t, int32_t period, int32_t r0q, int32_t v, int32_t lead, int32_t trail,
               bool live, bool first);
  void idle(ticks_t t) { next_spawn_ = t; }
  int32_t ring_r(int k, ticks_t t) const;
  void cull(ticks_t t);
  void set_ramp(int ramp, ticks_t t, int32_t dur);
  void set_levels(ticks_t t, int32_t fl, int32_t gl, int32_t pu, int32_t gr, bool restart);
  void set_fill(bool on, int32_t r);
  void set_stand(bool on);
  void set_iris(ticks_t t, int32_t r);
  void set_dim(int32_t target);
  void build(ticks_t t, int32_t core = 0, int32_t rim = -1, int32_t vmax = V7, int32_t lift = 0);

  uint16_t pal[256];      // entries 0..169 rebuilt by build()
  bool started;
  int32_t iris_to;        // iris target radius, px
  int32_t spawns;         // live rings spawned (heartbeat cadence)

 private:
  void mix_ramp(ticks_t t);

  int32_t acc_[2 * N_IDX];   // rings | ghosts
  uint16_t tab_[F::T_N];
  int32_t prm_[F::P_N];
  uint8_t r_on_[MAXR], r_ghost_[MAXR];
  ticks_t r_t0_[MAXR];
  int32_t r_r0_[MAXR], r_v_[MAXR], r_amp_[MAXR], r_lead_[MAXR], r_trail_[MAXR];
  Lut mix_, frm_;
  uint16_t frm_c_[LUT_N];
  int ramp_;
  const LutSrc* ramp_to_;
  ticks_t ramp_t0_;
  int32_t ramp_dur_;
  ticks_t next_spawn_, last_spawn_;
  int32_t period_;
  int32_t dt_, flash_;
  ticks_t t_;
  int32_t fl_, gl_, pu_, gr_;
  int32_t xf_[4];
  bool xf_on_;
  ticks_t xf_t0_;
  int32_t dim_, fill_r_, fill_v_, stand_w_;
  int32_t iris_, iris_from_;
  bool iris_on_;
  ticks_t iris_t0_;
  int32_t ghosts_;
  ticks_t stand_t0_;
};

}  // namespace sf
