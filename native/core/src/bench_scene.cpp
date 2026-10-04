#include "hm/bench_scene.h"

#include "hm/tuning.h"

namespace hm {

namespace {

constexpr int32_t q8(float x) { return (int32_t)(x * Q8 + 0.5f); }

// iris radius per glyph id (§2; countdown, turn, battery: as scan)
constexpr int32_t IRIS_R[] = {T::IRIS_R_NONE, T::IRIS_R_SEEKER, T::IRIS_R_CHEVRONS, T::IRIS_R_ARROW,
                              T::IRIS_R_SCAN, T::IRIS_R_SCAN, T::IRIS_R_NONE, T::IRIS_R_RUNES,
                              T::IRIS_R_SCAN, T::IRIS_R_RUNES};
constexpr int32_t CORE_V = q8(T::CORE_DOT_LEVEL);
constexpr int32_t PU_LOOKING = q8(T::FIELD_PAIRING_LOOKING_5);
constexpr int32_t SEEN_A = q8(T::PAIRING_SEEN_BREATHE_AMP[0]);   // PAIRING seen halo breathes
constexpr int32_t SEEN_B = q8(T::PAIRING_SEEN_BREATHE_AMP[1]);
constexpr int32_t SEEN_FL = q8(T::PAIRING_SEEN_FLOOR);
constexpr int32_t RING_LEAD = T::FIELD_LEAD_TRAIL_PX[0], RING_TRAIL = T::FIELD_LEAD_TRAIL_PX[1];
constexpr int32_t FILL_R0 = T::IRIS_R_SCAN;

}  // namespace

const FieldParams BENCH_FIXTURES[3] = {
    {"FAR", S_FAR, SUB_NONE, 0, RAMP_GREEN, 0.12f, 40, 2400, 25.0f, true, G_GLOW, T::FPS_TARGET},
    {"HOT bump-ready", S_HOT, SUB_NONE, 3, RAMP_GREEN, 0.85f, 120, 500, 54.0f, true, G_GLOW, T::FPS_TARGET},
    {"PAIRING runes", S_PAIRING, SUB_SEEN, -1, RAMP_GREEN, 0.1f, -30, 3000, 30.0f, true, G_RUNES,
     T::FPS_TARGET},
};

int FieldScene::step(const FieldParams& p, ticks_t t) {
  RippleField& f = f_;
  const bool first = !f.started;
  f.tick(t, p.fps_cap);
  const bool changed = p.screen != scr_ || p.sub != sub_;
  scr_ = p.screen;
  sub_ = p.sub;
  f.set_ramp(p.ramp, t, first ? 0 : (p.ramp == RAMP_GOLD ? T::HUE_CROSSFADE_FOUND_MS : T::HUE_CROSSFADE_MS));
  // levels from intensity (§4 rule 1) + per-screen overrides (§6)
  int32_t iq = (int32_t)(p.intensity * 256);
  iq = iq < 0 ? 0 : (iq > 256 ? 256 : iq);
  int32_t fl = F::FL_A + ((F::FL_B * iq) >> 8);
  int32_t glw = F::GL_A + ((F::GL_B * iq) >> 8);
  int32_t pu = F::PU_A + ((F::PU_B * iq) >> 8);
  const int32_t gq = (int32_t)(p.glow_r_px * 256);
  int g = p.glyph;
  if (p.screen == S_PAIRING) {
    if (p.sub == SUB_LOOKING) {
      pu = PU_LOOKING;
      g = G_DOTS;
    } else if (p.sub == SUB_SEEN) {
      fl = SEEN_FL;
      const int32_t deg = (int32_t)(t % T::BREATHE_PAIRING_MS) * 360 / T::BREATHE_PAIRING_MS;
      glw = SEEN_A + (((SEEN_B - SEEN_A) * (16384 - F::COS[deg])) >> 15);
    }
  }
  f.set_levels(t, fl, glw, pu, gq, changed && !first);
  f.set_iris(t, IRIS_R[g]);
  f.set_fill(false, FILL_R0);
  f.set_stand(p.screen == S_FOUND);
  f.set_dim(256);
  // rings: outward at a zone tempo use the zone's lead / trail (§5.3)
  int hb = 0;
  const int32_t v = p.speed_px_s;
  const bool zoned = p.zone >= 0 && p.zone <= 3 && v > 0;
  const int32_t lead = zoned ? T::ZONE_LEAD_PX[p.zone] : RING_LEAD;
  const int32_t trail = zoned ? T::ZONE_TRAIL_PX[p.zone] : RING_TRAIL;
  if (v != 0 && p.period_ms > 0) {
    const int32_t r0 = v > 0 ? f.iris_to * 256 : T::FIELD_R_MAX * 256;
    hb = f.schedule(t, p.period_ms, r0, v, lead, trail, p.ring_live || v < 0, first);   // inward: never ghosts
  } else {
    f.idle(t);
  }
  f.cull(t);
  f.started = true;
  const int32_t core = (g == G_GLOW && p.screen >= S_FAR && p.screen <= S_HOT) ? CORE_V : 0;
  f.build(t, core, -1, V7, 0);
  return hb;
}

}  // namespace hm
