// Port of ui/renderer.py; comments name the ui-spec rule where the Python does.
#include "hm/renderer.h"

#include <string.h>

#include "hm/text.h"
#include "hm/tuning.h"
#include "hm/ui_tables.h"

namespace hm {
namespace ui {

namespace {

namespace rp = render_params;
namespace hp = haptic_patterns;
using rp::Screen;
using rp::Sub;

// ids: index in T::SCREENS / T::GLYPHS (G_DOTS is renderer-only: the PAIRING looking dots)
constexpr int S_PAIRING = 0, S_SEARCHING = 1, S_FAR = 2, S_HOT = 5, S_FOUND = 6, S_SCANNING = 7, S_LINK_LOST = 8,
              S_MENU = 9;
static_assert((int)Screen::PAIRING == S_PAIRING && (int)Screen::SEARCHING == S_SEARCHING &&
                  (int)Screen::FAR == S_FAR && (int)Screen::HOT == S_HOT && (int)Screen::FOUND == S_FOUND &&
                  (int)Screen::SCANNING == S_SCANNING && (int)Screen::LINK_LOST == S_LINK_LOST &&
                  (int)Screen::MENU == S_MENU,
              "screen ids");
constexpr int G_GLOW = 0, G_SEEKER = 1, G_CHEV = 2, G_ARROW = 3, G_COUNT = 4, G_TURN = 5, G_CHECK = 6,
              G_RUNES = 7, G_BATT = 8, G_BUMP = 9, G_DOTS = 10;
static_assert((int)rp::Glyph::GLOW == G_GLOW && (int)rp::Glyph::ARROW == G_ARROW &&
                  (int)rp::Glyph::BATTERY == G_BATT && (int)rp::Glyph::BUMP == G_BUMP && G_DOTS == rp::N_GLYPHS,
              "glyph ids");
// iris radius per glyph id (§2; countdown, turn, battery: as scan)
constexpr int32_t IRIS_R[] = {T::IRIS_R_NONE, T::IRIS_R_SEEKER, T::IRIS_R_CHEVRONS, T::IRIS_R_ARROW,
                              T::IRIS_R_SCAN, T::IRIS_R_SCAN, T::IRIS_R_NONE, T::IRIS_R_RUNES,
                              T::IRIS_R_SCAN, T::IRIS_R_BUMP, T::IRIS_R_RUNES};
// culling boxes (for drawing a strip of rows)
constexpr int G_Y0[] = {0, 90, 76, 52, 96, 94, 79, 94, 103, 63, 114};
constexpr int G_Y1[] = {0, 150, 165, 190, 145, 147, 162, 147, 137, 152, 126};

constexpr int T_NONE = 0, T_STATUS = 1, T_CHIP = 2, T_LAST = 3;
constexpr int B_NONE = 0, B_TOAST = 1, B_WORD = 2, B_READOUT = 3;

constexpr int32_t q8(double x) { return (int32_t)(x * Q8 + 0.5); }

// token values as plain ints; Q8 = 256 per ramp step
constexpr int32_t LOGIC_MS = T::LOGIC_MS;        // params arrive at 10 Hz (§3)
constexpr int32_t CAL_MS = T::CAL_WINDOW_MS;     // calibrate fill r 64 -> 168 over the calibrate window
constexpr int32_t CAL_N = (CAL_MS + 999) / 1000;   // countdown digits, as finder.pairing.Calibrator.digit
constexpr int32_t FILL_R0 = T::IRIS_R_SCAN;
constexpr int32_t R_MAX = T::FIELD_R_MAX;   // ring-map max index: inward rings spawn and the fill ends here
constexpr int32_t ARROW_IN_MS = T::ARROW_APPEAR_MS;
constexpr int32_t ARROW_IN_S0 = q8(T::ARROW_APPEAR_SCALE);   // scale-in from 0.6
constexpr int32_t ARROW_OUT_MS = T::ARROW_EXPIRE_MS;         // DIRECTION expire shrink
constexpr int32_t ARROW_TAU_MS = T::ARROW_ANGLE_TAU_MS;
constexpr int32_t ARROW_DEADBAND = (int32_t)(T::ARROW_ANGLE_DEADBAND_DEG * 16);   // Q4 deg
constexpr int32_t TOAST_PX = T::TOAST_IN_PX;   // toasts rise in from / fall out to +12 px
constexpr int32_t PU_LOOKING = q8(T::FIELD_PAIRING_LOOKING_5);
constexpr int32_t PU_SEARCHING = q8(T::FIELD_SEARCHING_5);
constexpr int32_t PU_LOST = q8(T::FIELD_LINK_LOST_5);
constexpr int32_t PU_READY = q8(T::FIELD_SCAN_READY_PULSE_SCALE);
constexpr int32_t MG_A = q8(T::FIELD_SCAN_SWEEP_GLOW_AMP[0]);   // live-mirror halo glow_amp = 1 + 5 I (§5.7)
constexpr int32_t MG_B = q8(T::FIELD_SCAN_SWEEP_GLOW_AMP[1]);
constexpr int32_t SEEN_A = q8(T::PAIRING_SEEN_BREATHE_AMP[0]);   // PAIRING seen halo breathes 1.5 <-> 3.0
constexpr int32_t SEEN_B = q8(T::PAIRING_SEEN_BREATHE_AMP[1]);
constexpr int32_t SEEN_MS = T::BREATHE_PAIRING_MS;
constexpr int32_t SEEN_FL = q8(T::PAIRING_SEEN_FLOOR);   // PAIRING seen / confirmed floor (§6)
constexpr int32_t SEARCH_GL = q8(T::SEARCHING_GLOW_AMP);   // SEARCHING glow_amp (§6)
constexpr int32_t RING_LEAD = T::FIELD_LEAD_TRAIL_PX[0], RING_TRAIL = T::FIELD_LEAD_TRAIL_PX[1];   // no zone tempo
constexpr int32_t FOUND_LEAD = T::FOUND_LEAD_TRAIL_PX[0], FOUND_TRAIL = T::FOUND_LEAD_TRAIL_PX[1];   // FOUND burst
constexpr int32_t SAVER_PU = q8(T::SAVER_PULSE_SCALE);
constexpr int32_t SAVER_VMAX = q8(T::SAVER_V_MAX);
constexpr int32_t MENU_DIM = q8(T::MENU_PALETTE_SCALE);
constexpr int32_t CORE_V = q8(T::CORE_DOT_LEVEL);
constexpr int32_t BURST_AMP = q8(T::BURST_AMP);
constexpr int32_t BURST_V_FOUND = (int32_t)T::FOUND_BURST_SPEED_PX_S;
constexpr int32_t BURST_MULT = (int32_t)T::BURST_SPEED_MULT;
constexpr int32_t SUN_FLOOR = 256;   // sun mode: floor >= 1.0 and the LUT lifted one stop (§8)
constexpr int32_t SUN_LIFT = 9;
constexpr int MENU_MORE_X = 207;     // "more rows" triangles (ui-spec MENU)
constexpr int MENU_MORE_UP_Y = 36;
constexpr int MENU_MORE_DN_Y = 195;
constexpr int16_t TRI_UP[6] = {0, 5, 3, 0, 6, 5};
constexpr int16_t TRI_DN[6] = {0, 0, 6, 0, 3, 5};

// Q8 eased progress of age ms into dur ms (ui/field.py ease).
int32_t ease(const int16_t* tab, int32_t age, int32_t dur) {
  if (age >= dur) return 256;
  if (age <= 0) return 0;
  return tab[(age << 6) / dur];
}

// Angle difference in Q4 deg (5760 = 360) -> the short way, -2880..2880.
int32_t wrap_q4(int32_t d) { return d > 2880 ? d - 5760 : (d < -2880 ? d + 5760 : d); }

bool has_text(const rp::OptText& s) { return s && s->s[0]; }
bool is(const rp::OptText& s, const char* v) { return s && *s == v; }
bool starts(const rp::OptText& s, const char* v) { return s && strncmp(s->s, v, strlen(v)) == 0; }
// Python's == on two str-or-None.
bool same(const rp::OptText& a, const rp::OptText& b) { return a ? (b && strcmp(a->s, b->s) == 0) : !b; }

}  // namespace

Renderer::Renderer() {
  build_map(map_, W);
  reset();
}

void Renderer::reset() {
  field.reset();
  t_ = 0;
  scr_ = -1;
  sub_ = Sub::NONE;
  sub_t0_ = 0;
  saver_ = false;
  dark_ = false;   // display=None frames since the last drawn one
  burst_t_.reset();
  toast_s_.reset();
  toast_sev_ = rp::Severity::INFO;
  toast_st_ = false;
  toast_out_ = false;   // exit running (from toast_t0_)
  toast_t0_ = 0;
  cal_ms_ = 0;          // calibrate fill progress (ms of 3000)
  sw_a_ = sw_b_ = 0;    // wedge glide: the angle it glides from and to (deg)
  sw_t_ = 0;
  adq_ = 0;
  // arrow: flags gate every timestamp, so a stale t0 is never compared
  a_on_ = false;
  a_grow_ = false;      // appear scale-in running (a_t0_)
  a_shrink_ = false;    // expire shrink running (a_off_t0_)
  a_scr_ = -1;          // screen the arrow was last shown on
  a_q_ = a_tgt_ = 0;
  a_t0_ = a_off_t0_ = 0;
  a_style_ = 1;
  a_cone_ = 0;
  morph_k_ = -1;        // SCANNING result: best bin seen blinking
  morphed_ = false;     // the morph drew the dart: hand over
  morph_a_ = 0;         // its angle, Q4 deg
  morph_ = -1;
  morph_e_ = 0;
  g_ = G_GLOW;
  arrow_ = sweep_ = pacer_ = false;
  arrow_buf_.beam_n = 0;
  top_ = T_NONE;
  bot_ = B_NONE;
  bot_slot_ = false;
  top_c_ = bot_c_ = 0;
  bot_dy_ = bot_mark_ = bot_band_ = 0;
}

// ---- state ---------------------------------------------------------------------
haptic_patterns::Haptic Renderer::step(const RP& p, ticks_t t) {
  RippleField& f = field;
  const bool first = !f.started;
  const ticks_t prev_t = f.tick(t, p.fps_cap);
  const int scr = (int)p.screen;
  const Sub sub = p.sub;
  const bool changed = scr != scr_ || sub != sub_;
  if (changed) {
    sub_t0_ = t;
    morph_k_ = -1;
    if (p.sweep) wedge_reset(*p.sweep);   // a new wedge starts at its angle
  }
  scr_ = scr;
  sub_ = sub;
  const bool menu = scr == S_MENU;
  saver_ = p.status.own_pct <= T::BATT_CRITICAL_PCT;
  const bool cal = scr == S_PAIRING && sub == Sub::CALIBRATE;
  if (cal) cal_fill(p, t, prev_t, changed || first);
  if (menu && !first) f.hold(ticks_diff(t, prev_t));
  f.set_ramp(p.ramp, t, first ? 0 : (p.ramp == RAMP_GOLD ? T::HUE_CROSSFADE_FOUND_MS : T::HUE_CROSSFADE_MS));
  // levels from intensity (§4 rule 1) + per-screen overrides (§6)
  int32_t iq = (int32_t)(p.intensity * 256);
  iq = iq < 0 ? 0 : (iq > 256 ? 256 : iq);
  int32_t fl = F::FL_A + ((F::FL_B * iq) >> 8);
  int32_t glw = F::GL_A + ((F::GL_B * iq) >> 8);
  int32_t pu = F::PU_A + ((F::PU_B * iq) >> 8);
  const int32_t gq = (int32_t)(p.glow_r_px * 256);
  if (scr == S_PAIRING) {
    if (sub == Sub::LOOKING || sub == Sub::HOWTO) {   // a how-to card keeps the looking field
      pu = PU_LOOKING;
    } else if (sub == Sub::SEEN || sub == Sub::CONFIRMED) {
      fl = SEEN_FL;
      const int32_t deg = (int32_t)(t % SEEN_MS) * 360 / SEEN_MS;
      glw = SEEN_A + (((SEEN_B - SEEN_A) * (16384 - F::COS[deg])) >> 15);
    }
  } else if (scr == S_SEARCHING) {
    glw = SEARCH_GL;
    pu = PU_SEARCHING;
  } else if (scr == S_LINK_LOST) {
    pu = PU_LOST;
  } else if (scr == S_FOUND) {
    // the standing wave hides these: target its mean, so the level crossfade
    // out of FOUND starts from the field that was shown
    fl = F::STAND_MEAN;
    glw = 0;
  } else if (scr == S_SCANNING) {
    if (sub == Sub::READY || sub == Sub::NONE) pu = (pu * PU_READY) >> 8;
    else glw = MG_A + ((MG_B * iq) >> 8);
  } else if (sub == Sub::TURN && p.sweep && S_FAR <= scr && scr <= S_HOT) {
    // only the guided turn (pacer wedge) has the live mirror; the static
    // FACE IT frames (also sub turn) keep the zone levels
    glw = MG_A + ((MG_B * iq) >> 8);
  }
  if (saver_) pu = (pu * SAVER_PU) >> 8;
  if (p.sun && fl < SUN_FLOOR) fl = SUN_FLOOR;
  int g = p.glyph == rp::Glyph::NONE ? G_GLOW : (int)p.glyph;
  if (scr == S_PAIRING && sub == Sub::LOOKING) g = G_DOTS;
  g_ = g;
  if (!menu) {   // MENU: the field is frozen
    f.set_levels(t, fl, glw, pu, gq, changed && !first);
    f.set_iris(t, IRIS_R[g]);
    f.set_fill(cal, FILL_R0 + ((R_MAX - FILL_R0) * cal_ms_) / CAL_MS);
    f.set_stand(scr == S_FOUND);
  }
  f.set_dim(menu ? MENU_DIM : 256);
  // rings
  bool hb = false;
  const int32_t v = (int32_t)p.speed_px_s;
  const int32_t period = p.pulse_period_ms;
  int32_t lead, trail;
  if (p.zone && 0 <= *p.zone && *p.zone <= 3 && v > 0) {
    // outward rings at a zone tempo (FAR..HOT, PAIRING split, SCANNING
    // ready): the zone's lead / trail too (§5.3)
    lead = F::ZONE_LEAD[*p.zone];
    trail = F::ZONE_TRAIL[*p.zone];
  } else if (scr == S_FOUND) {
    lead = FOUND_LEAD;
    trail = FOUND_TRAIL;
  } else {
    lead = RING_LEAD;
    trail = RING_TRAIL;
  }
  if (v != 0 && period > 0 && !menu && scr != S_FOUND &&
      !(scr == S_SCANNING && sub != Sub::READY && sub != Sub::NONE)) {
    const int32_t r0 = v > 0 ? f.iris_to << 8 : R_MAX << 8;
    // inward rings are listening rings (§4 rule 4): never ghosts
    hb = f.schedule(t, period, r0, v, lead, trail, p.ring_live || v < 0, first) && p.heartbeat != hp::NONE &&
         f.spawns % (p.heartbeat_every ? p.heartbeat_every : 1) == 0;
  } else if (!menu) {
    f.idle(t);
  }
  if (p.burst && (!burst_t_ || p.t_ms != *burst_t_)) {
    burst_t_ = p.t_ms;
    const int32_t bv = (scr == S_FOUND || v == 0) ? BURST_V_FOUND : BURST_MULT * (v > 0 ? v : -v);
    f.spawn(t, f.iris_to << 8, bv, BURST_AMP, lead, trail, false);
  }
  f.cull(t);
  f.started = true;
  return hb ? p.heartbeat : hp::NONE;
}

void Renderer::cal_fill(const RP& p, ticks_t t, ticks_t prev_t, bool restart) {
  // PAIRING calibrate fill clock: advances with frame time except while the
  // logic shows the pause chip, and is held inside the second the countdown
  // digit names (digit d of n: (n-d) s .. (n-d+1) s).
  int32_t c = 0;
  if (!restart) {
    c = cal_ms_;
    if (!is(p.top_text, U::CAL_PAUSE)) {
      const int32_t d = ticks_diff(t, prev_t);
      c += d < 0 ? 0 : (d > 250 ? 250 : d);
    }
  }
  if (p.countdown && 1 <= *p.countdown && *p.countdown <= CAL_N) {
    const int32_t lo = (CAL_N - *p.countdown) * 1000;
    if (c < lo) c = lo;
    else if (c > lo + 1000) c = lo + 1000;
  }
  cal_ms_ = c > CAL_MS ? CAL_MS : c;
}

// ---- overlay plan ------------------------------------------------------------------
void Renderer::snap(const RP& p, ticks_t t) {
  // First drawn frame after screen-off frames: the current state with no
  // intro (§8). The arrow sits at its angle (or is gone: one that expired
  // while dark never shrinks on wake), a banner is at rest.
  a_grow_ = a_shrink_ = morphed_ = false;
  a_on_ = p.arrow_deg && scr_ != S_LINK_LOST;
  if (a_on_) {
    set_ad(*p.arrow_deg);
    a_q_ = a_tgt_ = adq_;
    a_scr_ = scr_;
  }
  toast_out_ = false;
  toast_s_.reset();
  if (p.banner) {
    toast_s_ = p.banner->text;
    toast_sev_ = p.banner->severity;
    toast_st_ = p.banner->sticky;
    toast_t0_ = ticks_add(t, -T::TOAST_IN_MS);
  }
  if (p.sweep) wedge_reset(*p.sweep);
}

void Renderer::set_ad(double ad) { adq_ = (int32_t)floormod((int64_t)(ad * 16), 5760); }   // Q4 deg

void Renderer::wedge_reset(const rp::Sweep& sw) { sw_a_ = sw_b_ = (int)sw.wedge_deg; }

int Renderer::wedge_deg(const rp::Sweep& sw, ticks_t t) {
  // Sweep / pacer wedge angle on the render clock: each new 10 Hz angle is
  // reached by gliding linearly from the last one over one logic tick (§3
  // interpolation; tokens sweep_rotation is linear), integer-only, the short
  // way across 0/360, held while paused. (The Python checks a new angle only
  // when the sweep object changes; the same object always has the same angle.)
  const int a = (int)sw.wedge_deg;
  if (a != sw_b_) {
    sw_a_ = sw_b_;
    sw_b_ = a;
    sw_t_ = t;
  }
  const int b = sw_b_;
  if (sw_a_ == b) return b;
  const int32_t age = ticks_diff(t, sw_t_);
  if (sw.paused || age >= LOGIC_MS) {   // paused, or the glide is done:
    sw_a_ = b;                          // a stale sw_t_ is never read again
    return b;
  }
  int d = b - sw_a_;
  if (d > 180) d -= 360;
  else if (d < -180) d += 360;
  return sw_a_ + (int)floordiv((int64_t)d * age, LOGIC_MS);
}

void Renderer::plan(const RP& p, ticks_t t) {
  const int scr = scr_;
  const Sub sub = sub_;
  const int g = g_;
  // arrow: smoothing, appear/expire scale
  arrow_ = false;
  morph_ = -1;
  int32_t scale = 256;
  if (p.arrow_deg && scr != S_LINK_LOST) {
    set_ad(*p.arrow_deg);
    const int32_t tgt = adq_;
    if (!a_on_) {
      a_on_ = true;
      a_shrink_ = false;
      a_tgt_ = tgt;
      if (morphed_) {   // the scan morph drew the dart: glide on from its angle
        morphed_ = false;
        a_q_ = morph_a_;
        a_grow_ = false;
      } else {
        a_q_ = tgt;
        a_t0_ = t;
        a_grow_ = true;
      }
    } else {
      const int32_t d = wrap_q4(tgt - a_tgt_);
      if (d >= ARROW_DEADBAND || d <= -ARROW_DEADBAND || tgt == 0 || sub == Sub::TURN) a_tgt_ = tgt;
    }
    const int32_t d = wrap_q4(a_tgt_ - a_q_);
    if (d) {
      const int32_t dt = field.dt();
      int32_t step = (d * ((dt << 8) / (dt + ARROW_TAU_MS))) >> 8;
      if (step == 0 || (-8 < d && d < 8)) step = d;
      a_q_ = (int32_t)floormod(a_q_ + step, 5760);
    }
    a_style_ = p.arrow_style == rp::ArrowStyle::NONE ? 1 : (int)p.arrow_style + 1;   // STYLES.get(.., 1)
    a_cone_ = p.cone_deg ? (int)*p.cone_deg : 0;
    a_scr_ = scr;
    if (a_grow_) {
      const int32_t age = ticks_diff(t, a_t0_);
      if (age >= ARROW_IN_MS) {
        a_grow_ = false;
      } else {
        const int32_t e = ease(F::EASE_OC, age, ARROW_IN_MS);
        scale = ARROW_IN_S0 + (((256 - ARROW_IN_S0) * e) >> 8);
      }
    }
    arrow_ = true;
  } else {
    if (scr != S_SCANNING) morphed_ = false;
    const bool zone_ctx = S_FAR <= scr && scr <= S_HOT && scr == a_scr_ && (g == G_GLOW || g == G_CHEV || g == G_ARROW);
    if (a_on_) {
      // §6 DIRECTION expire: the dart shrinks into C only while the same zone
      // screen stays up; any other screen hides it at once
      a_on_ = false;
      a_shrink_ = zone_ctx;
      a_off_t0_ = t;
    }
    if (a_shrink_) {
      const int32_t age = ticks_diff(t, a_off_t0_);
      if (!zone_ctx || age >= ARROW_OUT_MS) {
        a_shrink_ = false;
      } else {
        scale = 256 - ease(F::EASE_OC, age, ARROW_OUT_MS);
        arrow_ = scale > 8;
      }
    }
  }
  if (arrow_) prep_arrow(arrow_buf_, (a_q_ + 8) >> 4, a_cone_, scale);
  else if (scr == S_SCANNING && sub == Sub::RESULT) plan_morph(p, t);
  // sweep / pacer
  sweep_ = scr == S_SCANNING && p.sweep && sub != Sub::READY;
  pacer_ = sub == Sub::TURN && p.sweep && scr != S_SCANNING;
  int wd = 0;
  if (sweep_ || pacer_) {
    wd = wedge_deg(*p.sweep, t);
    prep_wedge(wedge_, wd);
  }
  // top slot
  top_ = T_NONE;
  const bool sup_top = scr == S_MENU || (scr == S_SCANNING && sub == Sub::SWEEP) || pacer_;
  if (!sup_top) {
    // transient action hints outrank a pinned StatusStrip (low battery or
    // unreliable pins it for minutes); the strip outranks LAST (§6)
    const char* band = rp::band_name(p.dist_band);
    if (has_text(p.top_text)) {
      top_ = T_CHIP;
      top_s_ = *p.top_text;
      top_c_ = is(p.top_text, U::HINT_FLAT) ? WARN : TEXT_PRI;   // WARN_TOP
    } else if (p.status.visible) {
      top_ = T_STATUS;
    } else if (p.dist_stale && band) {
      top_ = T_LAST;
      char s[rp::TEXT_MAX + 1] = "LAST ";
      strncat(s, band, sizeof s - strlen(s) - 2);
      strcat(s, "M");
      top_s_.set(s);
      top_c_ = GREY[6];
    }
  }
  // bottom slot: banner > toast > word > readout. Suppressed wherever a wedge
  // covers it: the sweep, and the turn pacer in its lower sector (otherwise a
  // turn toward something behind hides the pacer under the TURN word for its
  // last 60 deg or more)
  bot_ = B_NONE;
  if (scr == S_MENU || (scr == S_SCANNING && sub == Sub::SWEEP) || (pacer_ && wedge_hits_bottom(wd))) {
    toast_s_.reset();   // hidden: one still up rises again
    toast_out_ = false;
    return;
  }
  if (p.banner) {
    const rp::Banner& bn = *p.banner;
    // rise when a banner appears, or a new toast replaces one; a sticky
    // banner's text updates (LOST 0:12 -> 0:13) stay put
    if (!toast_s_ || toast_out_ || bn.severity != toast_sev_ || bn.sticky != toast_st_ ||
        (!bn.sticky && !same(bn.text, toast_s_)))
      toast_t0_ = t;
    toast_out_ = false;
    toast_s_ = bn.text;
    toast_sev_ = bn.severity;
    toast_st_ = bn.sticky;
    bot_ = B_TOAST;
    bot_s_ = bn.text ? *bn.text : rp::Text();
    bot_c_ = sev_col(bn.severity);
    const int32_t e = ease(F::EASE_OC, ticks_diff(t, toast_t0_), T::TOAST_IN_MS);
    bot_dy_ = TOAST_PX - ((TOAST_PX * e) >> 8);
    return;
  }
  if (toast_s_) {
    // toast_out: the last toast (bot_s_ / bot_c_) falls 12 px, 150 ms in_cubic
    if (!toast_out_) {
      toast_out_ = true;
      toast_t0_ = t;
    }
    const int32_t age = ticks_diff(t, toast_t0_);
    if (age < T::TOAST_OUT_MS) {
      bot_ = B_TOAST;
      bot_dy_ = (TOAST_PX * ease(F::EASE_IC, age, T::TOAST_OUT_MS)) >> 8;
      return;
    }
    toast_s_.reset();
    toast_out_ = false;
  }
  if (has_text(p.word)) {
    bot_ = B_WORD;
    bot_s_ = *p.word;
    bot_c_ = word_col(p);
  } else if (rp::band_name(p.dist_band) && S_FAR <= scr && scr <= S_HOT && !p.dist_stale) {
    bot_ = B_READOUT;
    bot_band_ = *p.dist_band;
    // with the arrow up the readout carries a trend mark: its slot is kept
    // while the trend is 0 so the numerals do not shift 9 px each time the
    // mark comes and goes (§1)
    bot_slot_ = g == G_ARROW && arrow_;
    bot_mark_ = bot_slot_ ? p.trend : 0;
  }
}

void Renderer::plan_morph(const RP& p, ticks_t t) {
  // SCANNING result: after the best bin's blink, the bar retracts while the
  // dart grows at theta (sweep slot 0; §6, 400 ms out_cubic).
  if (!p.sweep) return;
  const rp::Sweep& sw = *p.sweep;
  if (sw.active_bin) morph_k_ = *sw.active_bin;
  const int k = morph_k_;
  if (k < 0) return;
  const int32_t e_ms = ticks_diff(t, sub_t0_) - U::SCAN_BLINK_PHASE_MS;
  if (e_ms < 0) return;
  const int32_t e = ease(F::EASE_OC, e_ms, T::SCAN_MORPH_MS);
  morph_ = k;
  morph_e_ = e;
  const int a = (int)floormod((int)sw.wedge_deg, 360);
  morph_a_ = a * 16;   // Q4
  morphed_ = true;
  a_style_ = 1;
  a_cone_ = 0;
  arrow_ = true;
  prep_arrow(arrow_buf_, a, 0, 26 + ((230 * e) >> 8));
}

uint16_t Renderer::word_col(const RP& p) const {
  if (scr_ == S_SEARCHING) return GREY[7];
  if (starts(p.word, U::W_FOUND)) return ACC_FOUND;   // FOUND and FOUND 1:48 (§6 FOUND)
  if (is(p.word, U::W_BUMP)) return PROX[7];
  if (scr_ == S_PAIRING && sub_ == Sub::LOOKING) return TEXT_SEC;
  return TEXT_PRI;
}

void Renderer::palette(const RP& p, ticks_t t) {
  int32_t rim = -1;
  if (scr_ == S_SCANNING && (sub_ == Sub::READY || sub_ == Sub::NONE)) {
    const bool flat = !is(p.top_text, U::HINT_FLAT) && !(p.sweep && p.sweep->paused);
    rim = flat ? PROX[6] : WARN;
  }
  const int32_t core = (g_ == G_GLOW && S_FAR <= scr_ && scr_ <= S_HOT) ? CORE_V : 0;
  field.build(t, core, rim, saver_ ? SAVER_VMAX : V7, p.sun ? SUN_LIFT : 0);
}

// ---- public ------------------------------------------------------------------------
haptic_patterns::Haptic Renderer::frame(const RP& p, ticks_t now, bool draw) {
  // now is the render clock, not p.t_ms: params arrive at 10 Hz and the
  // renderer interpolates between them (§3, §4 rule 3), so a params object
  // is normally drawn for several frames.
  const hp::Haptic ev = step(p, now);
  if (!draw) {
    dark_ = true;
    return ev;
  }
  if (dark_) {
    dark_ = false;
    snap(p, now);
  }
  plan(p, now);
  palette(p, now);
  t_ = now;
  return ev;
}

// ---- drawing -------------------------------------------------------------------------
void Renderer::draw_strip(const RP& p, int y0, int h, uint16_t* px) {
  blit(px, map_, field.pal, y0 * W, h * W);
  FrameBuffer fb(px, y0, h);
  const int y1 = y0 + h;
  const int g = g_;
  const int scr = scr_;
  // beam / sweep wedge layer: the keyline (r 112) spans rows 8..232 (the
  // Python's y0 < 232 misses row 232, which only drawing in strips can show)
  if ((sweep_ || pacer_) && y0 <= 232 && y1 > 8) {
    const bool wedge = pacer_ || sub_ == Sub::SWEEP;
    if (sweep_) draw_bins(p, fb, wedge ? 0 : 2);
    if (wedge) {
      draw_wedge(fb, wedge_, p.sweep->paused ? WARN : (uint16_t)PROX[6]);
      if (sweep_) draw_bins(p, fb, 1);
    }
  }
  // glyph layer
  if (arrow_) {
    if (y0 < G_Y1[G_ARROW] && y1 > G_Y0[G_ARROW]) draw_arrow(fb, a_style_, arrow_buf_);
  } else if (g != G_GLOW && y0 < G_Y1[g] && y1 > G_Y0[g]) {
    switch (g) {
      case G_CHEV:
        draw_chevrons(fb, p.trend, p.trend_strong, chevron_nudge(t_));
        break;
      case G_SEEKER:
        draw_seeker(fb);
        break;
      case G_COUNT:
        if (p.countdown) draw_countdown(fb, Num(*p.countdown).s);
        break;
      case G_TURN:
        draw_turn(fb);
        break;
      case G_CHECK:
        draw_check(fb);
        break;
      case G_RUNES:
        draw_runes(p, fb);
        break;
      case G_BATT:
        draw_battery(fb, p.status.own_pct);
        break;
      case G_BUMP:
        if (p.bump_icons) draw_bump(fb, *p.bump_icons);
        break;
      case G_DOTS:
        draw_dots(fb);
        break;
    }
  }
  // top slot (y 12..35)
  if (top_ != T_NONE && y0 < 36) {
    if (top_ == T_STATUS) {
      const rp::Status& st = p.status;
      draw_status(fb, st.own_pct, st.partner_pct, st.link_q, st.unreliable);
    } else {
      draw_top_chip(fb, top_s_.s, top_c_, top_ == T_LAST ? p.trend : 0);
    }
  }
  // bottom slot (y 186..225, toasts rise from +12)
  if (bot_ != B_NONE && y1 > 186) {
    if (bot_ == B_TOAST) draw_toast(fb, bot_s_.s, bot_c_, bot_dy_);
    else if (bot_ == B_WORD) draw_word(fb, bot_s_.s, bot_c_);
    else draw_readout(fb, T::BAND_LABELS[bot_band_], bot_mark_, bot_slot_);
  }
  if (scr == S_MENU && y1 > 32 && y0 < 204) {
    // opaque backplate: no frozen ring arcs show in the 4 px gaps
    rrect(fb, 24, 32, 192, 172, 8, BG_BASE);
    // sub = visible index + optional "^"/"v"
    const char* sel = rp::name(p.sub);
    const int k_sel = sel && '0' <= sel[0] && sel[0] <= '3' ? sel[0] - '0' : -1;
    for (int k = 0; k < 4; k++) {
      const int y = T::MENU_ROWS_Y[k];
      if (!(y < y1 && y + T::MENU_ROW_H > y0)) continue;
      const rp::OptText* row = p.menu_rows && k < p.menu_rows->n ? &p.menu_rows->rows[k] : nullptr;
      draw_menu_row(fb, y, row && *row ? (*row)->s : "", k_sel == k);
    }
    if (sel) {
      if (strchr(sel, '^') && MENU_MORE_UP_Y < y1 && MENU_MORE_UP_Y + 6 > y0)
        fb.poly(MENU_MORE_X, MENU_MORE_UP_Y, TRI_UP, 3, TEXT_SEC, true);
      if (strchr(sel, 'v') && MENU_MORE_DN_Y < y1 && MENU_MORE_DN_Y + 6 > y0)
        fb.poly(MENU_MORE_X, MENU_MORE_DN_Y, TRI_DN, 3, TEXT_SEC, true);
    }
  }
}

void Renderer::draw_bins(const RP& p, FrameBuffer& fb, int which) {
  // Scan bins; the best bin blinks in result as the Game toggles active_bin.
  // which: 0 = all but the active bin (under the wedge), 1 = only the active
  // bin (drawn over the wedge: the wedge always covers the bin being
  // sampled, which would hide its highlight), 2 = all.
  const rp::Sweep& sw = *p.sweep;
  const int act = sw.active_bin ? *sw.active_bin : -1;
  const uint16_t act_c = sub_ == Sub::SWEEP ? PROX[5] : PROX[7];
  for (int k = 0; k < 12; k++) {
    if (which == 1) {
      if (k != act) continue;
    } else if (which == 0 && k == act) {
      continue;
    }
    if (U::BIN_Y0[k] >= fb.y1() || U::BIN_Y1[k] <= fb.y0()) continue;
    int32_t q = 255;   // Q8, 255 = no data
    if (k < sw.n_bins && sw.bins[k]) {
      const double b = *sw.bins[k] * 254;   // min(254, max(0, int(b * 254)))
      q = !(b > 0) ? 0 : (b >= 254 ? 254 : (int32_t)b);
    }
    if (k == morph_) {   // retracting into the dart
      if (morph_e_ < 230 && q != 255) draw_bin(fb, k, (q * (256 - morph_e_)) >> 8, act_c);
    } else if (q == 255) {
      draw_bin_hollow(fb, k, k == act ? act_c : (uint16_t)PROX[3]);
    } else if (which == 1) {   // over the wedge
      draw_bin(fb, k, q, act_c, BG_BASE, false);
    } else {
      draw_bin(fb, k, q, k == act ? act_c : (uint16_t)PROX[3]);
    }
  }
}

void Renderer::draw_runes(const RP& p, FrameBuffer& fb) {
  if (!p.runes) return;
  const int32_t age = ticks_diff(t_, sub_t0_);
  for (int k = 0; k < 3 && k < p.runes->n; k++) {
    uint16_t c = RUNE_COL;
    if (sub_ == Sub::CONFIRMED) c = RUNE_OK;
    else if (sub_ == Sub::SEEN && age < 150 * (k + 1)) c = RUNE_WAIT;
    draw_rune(fb, p.runes->ids[k], T::RUNE_CENTERS_X[k], c);
  }
}

}  // namespace ui
}  // namespace hm
