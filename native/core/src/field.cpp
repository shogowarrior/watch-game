// Port of ui/field.py; comments name the ui-spec rule where the Python does.
#include "sf/field.h"

#include <string.h>

#include "sf/tuning.h"

namespace sf {

namespace {

const LutSrc LUTS[RAMP_N] = {
    {F::LUTS_GREEN_C, F::LUTS_GREEN_R, F::LUTS_GREEN_G, F::LUTS_GREEN_B},
    {F::LUTS_GOLD_C, F::LUTS_GOLD_R, F::LUTS_GOLD_G, F::LUTS_GOLD_B},
    {F::LUTS_GREY_C, F::LUTS_GREY_R, F::LUTS_GREY_G, F::LUTS_GREY_B},
};

inline uint16_t swap16(uint32_t c) { return (uint16_t)(((c & 0xFF) << 8) | ((c >> 8) & 0xFF)); }

// Q8 eased progress of age ms into dur ms.
inline int32_t ease(const int16_t* tab, int32_t age, int32_t dur) {
  if (age >= dur) return 256;
  if (age <= 0) return 0;
  return tab[(age << 6) / dur];
}

}  // namespace

const LutSrc& ramp_lut(int ramp) { return LUTS[(ramp >= 0 && ramp < RAMP_N) ? ramp : RAMP_GREEN]; }

void Lut::copy_from(const LutSrc& o) {
  for (int j = 0; j < LUT_N; j++) {
    r[j] = o.r[j];
    g[j] = o.g[j];
    b[j] = o.b[j];
    c[j] = o.c[j];
  }
}

void Lut::copy_from(const Lut& o) {
  memcpy(r, o.r, LUT_N);
  memcpy(g, o.g, LUT_N);
  memcpy(b, o.b, LUT_N);
  memcpy(c, o.c, LUT_N * sizeof(uint16_t));
}

// ---- kernels -----------------------------------------------------------------
void pal_kernel(uint16_t* pp, int32_t* ap, const uint16_t* tp, const int32_t* qp) {
  const int32_t iris = qp[F::P_IRIS], rim_hi = qp[F::P_RIM_HI];
  const int32_t fill_r = qp[F::P_FILL_R], fill_hi = qp[F::P_FILL_HI];
  const int32_t core = qp[F::P_CORE], fl = qp[F::P_FL], gl = qp[F::P_GL], ginv = qp[F::P_GINV];
  const int32_t vmax = qp[F::P_VMAX], dim = qp[F::P_DIM], lift = qp[F::P_LIFT], rim = qp[F::P_RIM];
  const int32_t sw = qp[F::P_STAND_W], gh = qp[F::P_GHOST], irisc = qp[F::P_IRISC];
  const int32_t rim_q = qp[F::P_RIMQ], sb = qp[F::P_SB], fill_v = qp[F::P_FILL_V];
  const int32_t fill_e = fill_v + (fill_v >> 1);
  for (int32_t i = 0; i < N_IDX; i++) {
    const int32_t a = ap[i];
    ap[i] = 0;
    int32_t g = ap[N_IDX + i];
    ap[N_IDX + i] = 0;
    if (i < iris) {
      pp[i] = (uint16_t)irisc;
      continue;
    }
    int32_t v;
    if (i < rim_hi) {
      if (rim >= 0) {
        pp[i] = (uint16_t)rim;
        continue;
      }
      v = rim_q;
    } else {
      int32_t n = ((i - iris) * ginv) >> 8;
      if (n > 255) n = 255;
      int32_t base = fl + ((gl * tp[F::T_EXP + n]) >> 8);
      if (sw) {
        const int32_t st = 512 + ((((640 * tp[F::T_COS + (i & 31)]) >> 8) * sb) >> 8);
        base += ((st - base) * sw) >> 8;
      }
      v = ((base + a) * tp[F::T_VIG + i]) >> 8;
      if (i <= 6 && v < core) v = core;
      if (i < fill_hi) {
        if (i < fill_r) {
          if (v < fill_v) v = fill_v;
        } else if (v < fill_e) {
          v = fill_e;
        }
      }
    }
    if (v > vmax) v = vmax;
    const int32_t vd = dim != 256 ? (v * dim) >> 8 : v;
    int32_t j = ((vd * 9 + 128) >> 8) + lift;
    j = j > 63 ? 63 : (j < 0 ? 0 : j);
    uint16_t c = tp[F::T_LUT + j];
    if (gh) {
      g = (g * tp[F::T_VIG + i]) >> 8;
      if (g > v) {
        if (dim != 256) g = (g * dim) >> 8;
        j = ((g * 9 + 128) >> 8) + lift;
        if (j > 63) j = 63;
        c = tp[F::T_GLUT + j];
      }
    }
    pp[i] = c;
  }
}

void build_map(uint8_t* buf, int rows) {
  // Integer-only: idx = floor(sqrt(D)/2), D = (2x-239)^2 + (2y-239)^2.
  for (int y = 0; y < rows && y < 120; y++) {
    const int32_t dy = 2 * y - 239, dy2 = dy * dy;
    uint8_t* row = buf + y * FIELD_W;
    int32_t k = 0;
    for (int x = 120; x < FIELD_W; x++) {
      const int32_t dx = 2 * x - 239, d = dx * dx + dy2;
      while ((2 * k + 2) * (2 * k + 2) <= d) k++;
      row[x] = (uint8_t)k;           // k <= 168 (the corner)
      row[239 - x] = (uint8_t)k;
    }
  }
  for (int y = 120; y < rows; y++) memcpy(buf + y * FIELD_W, buf + (239 - y) * FIELD_W, FIELD_W);
}

void blit(uint16_t* dst, const uint8_t* idx, const uint16_t* pal, int off, int n) {
  const uint8_t* s = idx + off;
  for (int i = 0; i < n; i++) dst[i] = pal[s[i]];
}

// ---- RippleField -------------------------------------------------------------
RippleField::RippleField() {
  memset(pal, 0, sizeof pal);
  memset(acc_, 0, sizeof acc_);
  memset(tab_, 0, sizeof tab_);
  memset(prm_, 0, sizeof prm_);
  memset(r_t0_, 0, sizeof r_t0_);
  memset(r_r0_, 0, sizeof r_r0_);
  memset(r_v_, 0, sizeof r_v_);
  memset(r_amp_, 0, sizeof r_amp_);
  memset(r_lead_, 0, sizeof r_lead_);
  memset(r_trail_, 0, sizeof r_trail_);
  memset(r_ghost_, 0, sizeof r_ghost_);
  for (int i = 0; i < N_IDX; i++) tab_[F::T_VIG + i] = (uint16_t)F::VIG[i];
  for (int i = 0; i < 256; i++) tab_[F::T_EXP + i] = (uint16_t)F::EXPT[i];
  for (int i = 0; i < 32; i++) tab_[F::T_COS + i] = (uint16_t)F::COS32[i];
  for (int j = 0; j < LUT_N; j++) tab_[F::T_GLUT + j] = F::LUTS_GREY_C[j];
  mix_.c = tab_ + F::T_LUT;
  frm_.c = frm_c_;
  reset();
}

void RippleField::reset() {
  mix_.copy_from(LUTS[RAMP_GREEN]);
  ramp_ = RAMP_GREEN;
  ramp_to_ = &LUTS[RAMP_GREEN];
  ramp_t0_ = 0;
  ramp_dur_ = 0;
  memset(r_on_, 0, sizeof r_on_);
  started = false;
  next_spawn_ = last_spawn_ = 0;
  period_ = 0;
  spawns = 0;
  dt_ = 50;                 // smoothed frame interval, ms
  flash_ = 0;               // flash-limit level slew per frame, Q8
  t_ = 0;
  fl_ = F::FL_A;
  gl_ = F::GL_A;
  pu_ = F::PU_A;
  gr_ = 20 * Q8;
  memset(xf_, 0, sizeof xf_);
  xf_on_ = false;
  xf_t0_ = 0;
  dim_ = 256;
  fill_r_ = fill_v_ = stand_w_ = 0;
  iris_ = iris_from_ = iris_to = 0;
  iris_on_ = false;
  iris_t0_ = 0;
  ghosts_ = 0;
  stand_t0_ = 0;
}

ticks_t RippleField::tick(ticks_t t, int fps_cap) {
  if (started) {
    int32_t dt = ticks_diff(t, t_);
    dt = dt < 1 ? 1 : (dt > 250 ? 250 : dt);
    dt_ = (dt_ * 3 + dt) >> 2;
  } else {
    dt_ = 1000 / (fps_cap ? fps_cap : T::FPS_TARGET);
  }
  flash_ = (F::FLASH_Q8 * dt_) / T::FLASH_LIMIT_MS + 1;
  const ticks_t prev = t_;
  t_ = t;
  return prev;
}

void RippleField::hold(int32_t dt) {
  // MENU: freeze rings, schedule, crossfades, iris and breathing (wrap-safe).
  for (int k = 0; k < MAXR; k++) r_t0_[k] = ticks_add(r_t0_[k], dt);
  next_spawn_ = ticks_add(next_spawn_, dt);
  last_spawn_ = ticks_add(last_spawn_, dt);
  xf_t0_ = ticks_add(xf_t0_, dt);
  iris_t0_ = ticks_add(iris_t0_, dt);
  ramp_t0_ = ticks_add(ramp_t0_, dt);
  stand_t0_ = ticks_add(stand_t0_, dt);
}

int RippleField::spawn(ticks_t t0, int32_t r0q, int32_t v, int32_t amp, int32_t lead, int32_t trail,
                       bool ghost) {
  int k = 0, oldest = 0;
  int32_t best = -0x3FFFFFFF;
  for (; k < MAXR; k++) {
    if (!r_on_[k]) break;
    const int32_t age = ticks_diff(t0, r_t0_[k]);
    if (age > best) {            // reuse the oldest slot when full
      best = age;
      oldest = k;
    }
  }
  if (k == MAXR) k = oldest;
  r_on_[k] = 1;
  r_ghost_[k] = ghost ? 1 : 0;
  r_t0_[k] = t0;
  r_r0_[k] = r0q;
  r_v_[k] = v;
  r_amp_[k] = amp;
  r_lead_[k] = lead;
  r_trail_[k] = trail;
  return k;
}

int RippleField::schedule(ticks_t t, int32_t period, int32_t r0q, int32_t v, int32_t lead, int32_t trail,
                          bool live, bool first) {
  if (period != period_) {
    if (period_ && !first) {
      const int32_t ago = ticks_diff(t, last_spawn_);
      if (ago >= 0 && ago < period_) {           // recent spawn: re-time the next
        const ticks_t nxt = ticks_add(last_spawn_, period);
        if (ticks_diff(nxt, next_spawn_) < 0) next_spawn_ = nxt;
      }
    }
    period_ = period;
  }
  const int32_t amp = live ? pu_ : (pu_ * F::GHOST_AMP) >> 8;
  if (first) {
    for (int k = 1; k < 8; k++) {
      const int32_t age = k * period;
      const int64_t r = r0q + floordiv((int64_t)v * age * 256, 1000);
      if ((v > 0 && r > (int64_t)(170 + trail) * Q8) || (v < 0 && r < (int64_t)iris_to * Q8)) break;
      last_spawn_ = ticks_add(t, -age);
      spawn(last_spawn_, r0q, v, amp, lead, trail, !live);
    }
    next_spawn_ = t;
  }
  const int32_t lag = ticks_diff(t, next_spawn_);
  if (lag < 0) return 0;
  if (lag >= period) next_spawn_ = ticks_add(t, -(lag % period));   // frames were skipped
  const ticks_t t0 = next_spawn_;
  spawn(t0, r0q, v, amp, lead, trail, !live);
  last_spawn_ = t0;
  next_spawn_ = ticks_add(t0, period);
  if (!live) return 0;
  spawns++;
  return 1;
}

int32_t RippleField::ring_r(int k, ticks_t t) const {
  // Ring k radius at t, Q8 px (fractional: temporal AA needs it).
  const int32_t va = r_v_[k] * ticks_diff(t, r_t0_[k]);
  const int32_t q = (int32_t)floordiv(va, 1000);
  return r_r0_[k] + q * 256 + ((va - q * 1000) * 256) / 1000;
}

void RippleField::cull(ticks_t t) {
  const int32_t ir = iris_ * Q8;
  int32_t g = 0;
  for (int k = 0; k < MAXR; k++) {
    if (!r_on_[k]) continue;
    const int32_t rq = ring_r(k, t), v = r_v_[k], age = ticks_diff(t, r_t0_[k]);
    if ((v > 0 && rq - r_trail_[k] * Q8 > N_IDX * Q8) || (v < 0 && rq + 64 * Q8 < ir) || age > 20000 ||
        (v < 0 && rq < -r_trail_[k] * Q8)) {
      r_on_[k] = 0;
    } else if (r_ghost_[k]) {
      g = 1;
    }
  }
  ghosts_ = g;
}

void RippleField::set_ramp(int ramp, ticks_t t, int32_t dur) {
  if (ramp == ramp_) return;
  frm_.copy_from(mix_);
  ramp_ = ramp;
  ramp_to_ = &ramp_lut(ramp);
  ramp_t0_ = t;
  ramp_dur_ = dur;
  if (dur <= 0) mix_.copy_from(*ramp_to_);
}

void RippleField::mix_ramp(ticks_t t) {
  if (ramp_dur_ <= 0) return;
  const int32_t e = ease(F::EASE_IOC, ticks_diff(t, ramp_t0_), ramp_dur_);
  const LutSrc& b = *ramp_to_;
  if (e >= 256) {
    mix_.copy_from(b);
    ramp_dur_ = 0;
    return;
  }
  const Lut& a = frm_;
  for (int j = 0; j < LUT_N; j++) {
    const int32_t r = a.r[j] + (((b.r[j] - a.r[j]) * e) >> 8);
    const int32_t g = a.g[j] + (((b.g[j] - a.g[j]) * e) >> 8);
    const int32_t bb = a.b[j] + (((b.b[j] - a.b[j]) * e) >> 8);
    mix_.r[j] = (uint8_t)r;
    mix_.g[j] = (uint8_t)g;
    mix_.b[j] = (uint8_t)bb;
    mix_.c[j] = swap16(((r >> 3) << 11) | ((g >> 2) << 5) | (bb >> 3));
  }
}

void RippleField::set_levels(ticks_t t, int32_t fl, int32_t gl, int32_t pu, int32_t gr, bool restart) {
  // Crossfade (600 ms in_out_cubic) the displayed levels toward the targets.
  if (restart) {
    xf_[0] = fl_;
    xf_[1] = gl_;
    xf_[2] = pu_;
    xf_[3] = gr_;
    xf_t0_ = t;
    xf_on_ = true;
  }
  int32_t e = 256;
  if (xf_on_) {
    e = ease(F::EASE_IOC, ticks_diff(t, xf_t0_), T::ZONE_CROSSFADE_MS);
    if (e >= 256) xf_on_ = false;               // never compare a stale t0 again
  }
  if (e < 256) {
    fl = xf_[0] + (((fl - xf_[0]) * e) >> 8);
    gl = xf_[1] + (((gl - xf_[1]) * e) >> 8);
    pu = xf_[2] + (((pu - xf_[2]) * e) >> 8);
    gr = xf_[3] + (((gr - xf_[3]) * e) >> 8);
  }
  // flash limit: the full-field floor moves <= 2 steps / 333 ms
  const int32_t d = fl - fl_;
  if (started) {
    if (d > flash_) fl = fl_ + flash_;
    else if (d < -flash_) fl = fl_ - flash_;
  }
  fl_ = fl;
  gl_ = gl;
  pu_ = pu;
  gr_ = gr > 2 * Q8 ? gr : 2 * Q8;
}

void RippleField::set_fill(bool on, int32_t r) {
  // PAIRING calibrate fill (§6); fades out at its last radius at the flash-limit rate.
  if (on) {
    fill_r_ = r;
    fill_v_ = F::FILL_V;
  } else if (fill_v_) {
    const int32_t v = fill_v_ - flash_;
    fill_v_ = v > 0 ? v : 0;
  }
}

void RippleField::set_stand(bool on) {
  // FOUND standing-wave weight: crossfades with the 600 ms state crossfade (§4).
  if (!started) {
    stand_w_ = on ? Q8 : 0;
    return;
  }
  const int32_t step = (Q8 * dt_) / T::ZONE_CROSSFADE_MS + 1;
  const int32_t w = stand_w_ + (on ? step : -step);
  stand_w_ = w > Q8 ? Q8 : (w > 0 ? w : 0);
}

void RippleField::set_iris(ticks_t t, int32_t r) {
  if (r != iris_to) {
    iris_from_ = iris_;
    iris_to = r;
    iris_t0_ = t;
    iris_on_ = true;
  }
  if (!iris_on_) {
    iris_ = iris_to;
    return;
  }
  const int32_t e = ease(F::EASE_OC, ticks_diff(t, iris_t0_), T::IRIS_ANIM_MS);
  if (e >= 256) iris_on_ = false;
  iris_ = iris_from_ + (((iris_to - iris_from_) * e) >> 8);
}

void RippleField::set_dim(int32_t target) {
  // Menu dim, slewed at 128 per 600 ms crossfade (under the flash limit).
  const int32_t step = (128 * dt_) / T::ZONE_CROSSFADE_MS + 1;
  const int32_t d = target - dim_;
  dim_ = (-step <= d && d <= step) ? target : dim_ + (d > 0 ? step : -step);
}

void RippleField::build(ticks_t t, int32_t core, int32_t rim, int32_t vmax, int32_t lift) {
  mix_ramp(t);
  for (int k = 0; k < MAXR; k++) {
    if (!r_on_[k]) continue;
    const int32_t rq = ring_r(k, t), v = r_v_[k], av = v > 0 ? v : -v;
    int32_t lead = r_lead_[k] * Q8;
    const int32_t aa = (F::AA_K * av * dt_) / 1000;     // 1.5*|v|/fps, Q8 px
    if (aa > lead) lead = aa;
    const int32_t trail = r_trail_[k] * Q8;
    const int32_t outer = v > 0 ? lead : trail, inner = v > 0 ? trail : lead;
    int32_t dist = rq - r_r0_[k];
    if (dist < 0) dist = -dist;
    int32_t fade = dist / T::FADEIN_PX;                    // 12 px fade-in, Q8
    if (fade > 256) fade = 256;
    const int32_t w = (r_amp_[k] * fade) >> 8;
    if (w <= 0) continue;
    int32_t i0 = (rq - inner + 255) >> 8, i1 = (rq + outer) >> 8;
    if (i0 < 0) i0 = 0;
    if (i1 > N_IDX - 1) i1 = N_IDX - 1;
    int32_t* acc = acc_ + (r_ghost_[k] ? N_IDX : 0);
    for (int32_t i = i0; i <= i1; i++) {
      const int32_t dq = i * 256 - rq;
      const int32_t kk = dq >= 0 ? (dq << 6) / outer : ((-dq) << 6) / inner;
      if (kk < 64) acc[i] += (w * F::PROF[kk]) >> 8;
    }
  }
  if (core && iris_ == 0) {
    // the core dot is a floor (§4 rule 2): hold the ring term non-increasing
    // outward over r 0..12 so a new crest fills the disc, then detaches.
    int32_t m = 0;
    for (int i = 12; i >= 0; i--) {
      if (acc_[i] > m) m = acc_[i];
      else acc_[i] = m;
    }
  }
  int32_t rim_q = fl_ + gl_;
  if (rim_q > V7) rim_q = V7;
  if (rim_q < F::RIM_MIN) rim_q = F::RIM_MIN;
  const int32_t fr = fill_v_ ? fill_r_ : 0;
  int32_t* q = prm_;
  q[F::P_IRIS] = iris_;
  q[F::P_RIM_HI] = iris_ > 0 ? iris_ + T::IRIS_RIM_PX : 0;
  q[F::P_FILL_R] = fr;
  q[F::P_FILL_HI] = fr > 0 ? fr + 3 : 0;
  q[F::P_CORE] = core;
  q[F::P_FL] = fl_;
  q[F::P_GL] = gl_;
  q[F::P_GINV] = (64 * Q8 * Q8) / gr_;
  q[F::P_VMAX] = vmax;
  q[F::P_DIM] = dim_;
  q[F::P_LIFT] = lift;
  q[F::P_RIM] = rim;
  q[F::P_STAND_W] = stand_w_;
  q[F::P_GHOST] = ghosts_;
  q[F::P_IRISC] = F::BG_IRIS;
  q[F::P_RIMQ] = rim_q;
  const int32_t ph = (int32_t)floormod(ticks_diff(t, stand_t0_), T::STANDING_PERIOD_MS);
  q[F::P_SB] = F::SB_A + ((F::SB_B * F::SIN[ph * 360 / T::STANDING_PERIOD_MS]) >> 14);
  q[F::P_FILL_V] = fill_v_;
  pal_kernel(pal, acc_, tab_, q);
}

}  // namespace sf
