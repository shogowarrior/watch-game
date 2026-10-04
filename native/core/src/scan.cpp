#include "hm/scan.h"

#include <math.h>

#include "hm/estimator.h"

namespace hm {
namespace scan {

namespace hp = haptic_patterns;

namespace {
constexpr double PI = 3.141592653589793;   // math.pi
constexpr double D2R = PI / 180.0;         // Python's _D2R
constexpr double R2D = 180.0 / PI;         // Python's _R2D
}  // namespace

const char* name(Phase p) {
  static const char* const N[] = {"ready", "sweep", "result"};
  return N[p];
}

const char* name(Fault f) {
  static const char* const N[] = {"tilt", "walk"};
  return N[f];
}

const char* name(Reason r) {
  static const char* const N[] = {"no_fix", "friend_moved", "abort", "cancel"};
  return N[r];
}

const char* toast_of(Reason r) {
  static const char* const N[] = {"NO FIX, TRY AGAIN", "FRIEND MOVED", "SCAN STOPPED", nullptr};
  return N[r];
}

std::optional<Fit> fit_harmonic(const float* phi, const float* val, const uint8_t* src, int32_t n) {
  if (n < MIN_FIT_N) return std::nullopt;
  int32_t k0 = 0, k1 = 0;
  double y0 = 0.0, y1 = 0.0, c0 = 0.0, c1 = 0.0, s0 = 0.0, s1 = 0.0;
  for (int32_t i = 0; i < n; i++) {
    const double p = (double)phi[i] * D2R;
    const double c = cos(p);
    const double s = sin(p);
    if (src[i]) {
      k1++;
      y1 += (double)val[i];
      c1 += c;
      s1 += s;
    } else {
      k0++;
      y0 += (double)val[i];
      c0 += c;
      s0 += s;
    }
  }
  if (k0) {
    y0 /= k0;
    c0 /= k0;
    s0 /= k0;
  }
  if (k1) {
    y1 /= k1;
    c1 /= k1;
    s1 /= k1;
  }
  double scc = 0.0, sss = 0.0, scs = 0.0, syc = 0.0, sys_ = 0.0, syy = 0.0;
  for (int32_t i = 0; i < n; i++) {
    const double p = (double)phi[i] * D2R;
    double c, s, y;
    if (src[i]) {
      c = cos(p) - c1;
      s = sin(p) - s1;
      y = (double)val[i] - y1;
    } else {
      c = cos(p) - c0;
      s = sin(p) - s0;
      y = (double)val[i] - y0;
    }
    scc += c * c;
    sss += s * s;
    scs += c * s;
    syc += y * c;
    sys_ += y * s;
    syy += y * y;
  }
  const double det = scc * sss - scs * scs;
  const double tr = scc + sss;
  if (tr <= 0.0 || det <= 1e-3 * tr * tr) return std::nullopt;   // angular coverage too narrow
  const double a = (syc * sss - sys_ * scs) / det;
  const double b = (sys_ * scc - syc * scs) / det;
  const double sse = syy - a * syc - b * sys_;
  const int32_t dof = n - 2 - (k0 ? 1 : 0) - (k1 ? 1 : 0);
  if (dof < 1) return std::nullopt;
  const double sres = sse > 0.0 ? sqrt(sse / dof) : 0.0;
  const double a1 = sqrt(a * a + b * b);
  double th = atan2(b, a) * R2D;
  if (th < 0.0) th += 360.0;
  return Fit{a1, th, sres, n};
}

double sigma_fit_deg(double a1, double sigma_res, int32_t n) {
  if (a1 <= 0.0 || n <= 0) return 180.0;
  return sigma_res / (a1 * sqrt(n * 0.5)) * R2D;
}

Verdict evaluate(double a1, double sigma_res, int32_t n, int32_t peer_walk_ms) {
  if (peer_walk_ms > PEER_WALK_FAIL_MS) return {false, std::nullopt, R_FRIEND_MOVED};
  const double sf = sigma_fit_deg(a1, sigma_res, n);
  double s0 = sqrt(sf * sf + FLOOR_DEG * FLOOR_DEG);
  if (peer_walk_ms > PEER_WALK_PENALTY_MS) s0 += PEER_WALK_DEG;
  if (2.0 * a1 < MIN_P2T_DB || s0 > MAX_S0_DEG) return {false, s0, R_NO_FIX};
  return {true, s0, std::nullopt};
}

ScanSession::ScanSession(ticks_t t_ms, BlankFn blank_fn, void* blank_ctx)
    : blank_fn(blank_fn), blank_ctx(blank_ctx), phi(), val(), src(), scratch_(), seg_lo_(), seg_hi_(), med_(),
      bn_(), dirty_(), raw_(), rawok_(), sm_(), sum_(), cnt_() {
  reset(t_ms);
}

void ScanSession::reset(ticks_t t_ms) {
  phase = READY;
  t_last_ = t_ms;
  n = 0;
  for (int i = 0; i < BINS + 1; i++) seg_lo_[i] = seg_hi_[i] = -1;
  for (int i = 0; i < 2 * BINS; i++) bn_[i] = 0;
  for (int i = 0; i < BINS; i++) {
    dirty_[i] = 0;
    rawok_[i] = 0;
    bins[i].reset();
  }
  sum_[0] = sum_[1] = 0.0f;
  cnt_[0] = cnt_[1] = 0;
  // motion
  activity = 0;
  flat_since_.reset();
  over_since_.reset();
  tilt_fault_ = false;
  steps_last_.reset();
  steps = 0;
  steps_t_.clear();
  // ready
  flat = false;
  cd_ms_ = 0;
  digit_ = 0;
  countdown = 3;
  // sweep
  active_ms = 0;
  pause_ms = 0;
  wedge_deg = 0.0;
  paused = false;
  fault.reset();
  next_tick_ = TICK_EVERY_DEG;
  peer_walk_ = false;
  peer_walk_ms = 0;
  lm_.reset(t_ms);
  t_sweep.reset();
  // result
  t_result.reset();
  reason.reset();
  theta_deg.reset();
  s0_deg.reset();
  sigma_fit_deg.reset();
  a1_db.reset();
  best_bin.reset();
  haptic_ = hp::NONE;
}

// ---- inputs

void ScanSession::on_motion(ticks_t t_ms, std::optional<double> tilt_deg, std::optional<int32_t> steps,
                            std::optional<int32_t> activity) {
  const bool blank = blanked(t_ms);
  if (tilt_deg && !blank) {
    const double tilt = *tilt_deg;
    if (!flat_since_) {
      if (tilt <= FLAT_DEG) flat_since_ = t_ms;
    } else if (tilt > FLAT_OFF_DEG) {
      flat_since_.reset();
    }
    if (tilt > TILT_FAULT_DEG) {
      if (!over_since_) over_since_ = t_ms;
    } else {
      over_since_.reset();
      if (tilt_fault_ && tilt <= FLAT_DEG) tilt_fault_ = false;
    }
  }
  if (activity) this->activity = *activity;
  if (steps) {
    const std::optional<int32_t> last = steps_last_;
    steps_last_ = steps;
    if (last && phase == SWEEP && !blank) {
      const int32_t d = *steps - *last;
      if (0 < d && d < 1000) {
        this->steps += d;
        for (int32_t k = d < WALK_STEPS ? d : WALK_STEPS; k; k--) steps_t_.note(t_ms);
      }
    }
  }
}

void ScanSession::on_packet(ticks_t t_ms, std::optional<double> rssi, std::optional<double> peer_rssi) {
  if (phase != SWEEP || paused) return;
  int32_t a = active_ms;
  const int32_t d = ticks_diff(t_ms, t_last_);
  if (d > 0) a += d < MAX_DT_MS ? d : MAX_DT_MS;
  if (a >= DURATION_MS) return;
  const double p = a * DEG_PER_S * 0.001;
  if (rssi) {
    add_(p, *rssi, SRC_OWN);
    lm_.add(t_ms, *rssi);
  }
  if (peer_rssi) add_(p, *peer_rssi, SRC_PEER);
}

bool ScanSession::cancel(ticks_t t_ms) {
  if (phase == RESULT) return false;
  phase = RESULT;
  t_result = t_ms;
  reason = R_CANCEL;
  paused = false;
  return true;
}

// ---- per frame

void ScanSession::update(ticks_t t_ms) {
  int32_t dt = ticks_diff(t_ms, t_last_);
  if (dt < 0)
    dt = 0;
  else if (dt > MAX_DT_MS)
    dt = MAX_DT_MS;
  if (phase == READY)
    update_ready_(t_ms, dt);
  else if (phase == SWEEP)
    update_sweep_(t_ms, dt);
  t_last_ = t_ms;
  peer_walk_ = false;   // an on_peer report lasts one frame
}

void ScanSession::update_ready_(ticks_t t, int32_t dt) {
  const bool fl = flat_since_ && ticks_diff(t, *flat_since_) >= FLAT_HOLD_MS;
  flat = fl;
  if (!fl) return;
  const int32_t held = ticks_diff(t, *flat_since_) - FLAT_HOLD_MS;
  if (digit_ == 0)
    dt = 0;   // first flat frame shows 3 and ticks
  else if (dt > held)
    dt = held;
  cd_ms_ += dt;
  if (cd_ms_ >= READY_MS) {
    start_sweep_(t);
    return;
  }
  const int32_t d = 3 - (int32_t)floordiv(cd_ms_, 1000);
  countdown = d;
  if (d != digit_) {
    digit_ = d;
    emit_(hp::TICK);
  }
}

void ScanSession::start_sweep_(ticks_t t) {
  phase = SWEEP;
  t_sweep = t;
  countdown.reset();
  active_ms = 0;
  pause_ms = 0;
  steps = 0;
  steps_t_.clear();
  peer_walk_ms = 0;
  wedge_deg = 0.0;
  next_tick_ = TICK_EVERY_DEG;
}

void ScanSession::update_sweep_(ticks_t t, int32_t dt) {
  // time since the last frame belongs to the previous paused state
  if (paused)
    pause_ms += dt;
  else
    active_ms += dt;
  if (peer_walk_) peer_walk_ms += dt;
  // faults
  if (!tilt_fault_ && over_since_ && ticks_diff(t, *over_since_) > TILT_FAULT_MS) tilt_fault_ = true;
  const bool walk = activity == ACT_WALK || activity == ACT_RUN || steps_t_.full_within(t, WALK_WINDOW_MS);
  const bool complete = active_ms >= DURATION_MS;   // the turn is complete: nothing left to pause
  const bool faulted = !complete && (tilt_fault_ || walk);
  if (faulted && !paused) emit_(hp::NOPE);          // once per pause episode
  paused = faulted;
  if (faulted)
    fault = tilt_fault_ ? FAULT_TILT : FAULT_WALK;
  else
    fault.reset();
  if (steps > ABORT_STEPS || pause_ms > ABORT_PAUSE_MS) {
    fail_(t, R_ABORT);
    return;
  }
  int32_t a = active_ms;
  if (a > DURATION_MS) a = DURATION_MS;
  const double w = a * DEG_PER_S * 0.001;
  wedge_deg = w;
  double nt = next_tick_;
  while (nt < 360.0 && w >= nt) {
    emit_(nt == 180.0 ? hp::DOUBLE : hp::TICK);
    nt += TICK_EVERY_DEG;
  }
  next_tick_ = nt;
  refresh_bins_();
  if (active_ms >= DURATION_MS) finish_(t);
}

void ScanSession::fail_(ticks_t t, Reason r) {
  phase = RESULT;
  t_result = t;
  reason = r;
  paused = false;
  emit_(hp::NOPE);
}

void ScanSession::finish_(ticks_t t) {
  wedge_deg = 360.0;
  const std::optional<Fit> fit = fit_harmonic(phi, val, src, n);
  if (!fit) {
    fail_(t, peer_walk_ms > PEER_WALK_FAIL_MS ? R_FRIEND_MOVED : R_NO_FIX);
    return;
  }
  a1_db = fit->a1_db;
  sigma_fit_deg = scan::sigma_fit_deg(fit->a1_db, fit->sigma_res_db, fit->n);
  const Verdict v = evaluate(fit->a1_db, fit->sigma_res_db, fit->n, peer_walk_ms);
  s0_deg = v.s0_deg;
  if (!v.ok) {
    fail_(t, *v.reason);
    return;
  }
  phase = RESULT;
  t_result = t;
  theta_deg = fit->theta_deg;
  best_bin = (int32_t)((fit->theta_deg + 15.0) / 30.0) % BINS;
  emit_(hp::CLOSER);
}

// ---- samples and bins

void ScanSession::add_(double phi_deg, double y, uint8_t s) {
  const int32_t i = n;
  if (i >= CAP) return;
  phi[i] = (float)phi_deg;
  val[i] = (float)y;
  src[i] = s;
  n = i + 1;
  int32_t seg = (int32_t)((phi_deg + 15.0) / 30.0);
  if (seg > BINS) seg = BINS;
  if (seg_lo_[seg] < 0) seg_lo_[seg] = i;
  seg_hi_[seg] = i + 1;
  dirty_[seg < BINS ? seg : 0] = 1;
  sum_[s] = (float)((double)sum_[s] + y);
  cnt_[s]++;
}

double ScanSession::median_(int b, uint8_t s, int32_t* k_out) {
  float* buf = scratch_;
  int32_t k = 0;
  int seg = b;
  for (;;) {
    const int32_t lo = seg_lo_[seg];
    if (lo >= 0) {
      for (int32_t i = lo; i < seg_hi_[seg]; i++) {
        if (src[i] == s) {   // insertion sort as we go
          const float v = val[i];
          int32_t j = k;
          while (j > 0 && buf[j - 1] > v) {
            buf[j] = buf[j - 1];
            j--;
          }
          buf[j] = v;
          k++;
        }
      }
    }
    if (b != 0 || seg == BINS) break;
    seg = BINS;
  }
  *k_out = k;
  if (k == 0) return 0.0;
  const int32_t m = k >> 1;
  return k & 1 ? (double)buf[m] : 0.5 * ((double)buf[m - 1] + (double)buf[m]);
}

void ScanSession::refresh_bins_() {
  // dirty bin medians
  for (int b = 0; b < BINS; b++) {
    if (!dirty_[b]) continue;
    dirty_[b] = 0;
    for (uint8_t s = 0; s < 2; s++) {
      int32_t k;
      const double m = median_(b, s, &k);
      bn_[2 * b + s] = k;
      med_[2 * b + s] = (float)m;
    }
  }
  const int32_t c0 = cnt_[0];
  const int32_t c1 = cnt_[1];
  const double m0 = c0 ? (double)sum_[0] / c0 : 0.0;
  const double m1 = c1 ? (double)sum_[1] / c1 : 0.0;
  for (int b = 0; b < BINS; b++) {
    const int32_t n0 = bn_[2 * b];
    const int32_t n1 = bn_[2 * b + 1];
    const int32_t tot = n0 + n1;
    if (tot < MIN_PACKETS) {
      rawok_[b] = 0;
      continue;
    }
    rawok_[b] = 1;
    double v = 0.0;
    if (n0) v += n0 * ((double)med_[2 * b] - m0);
    if (n1) v += n1 * ((double)med_[2 * b + 1] - m1);
    raw_[b] = (float)(v / tot);
  }
  // the +-30 deg smoothed curve, then 0..1
  bool any = false;
  double lo = 0.0, hi = 0.0;
  for (int b = 0; b < BINS; b++) {
    if (!rawok_[b]) continue;
    double w = 2.0 * (double)raw_[b];
    int32_t ws = 2;
    const int p = (b + BINS - 1) % BINS;
    const int q = (b + 1) % BINS;
    if (rawok_[p]) {
      w += (double)raw_[p];
      ws++;
    }
    if (rawok_[q]) {
      w += (double)raw_[q];
      ws++;
    }
    sm_[b] = (float)(w / ws);
    const double v = (double)sm_[b];   // the stored (float) value, so bins stay in 0..1
    if (!any || v < lo) lo = v;
    if (!any || v > hi) hi = v;
    any = true;
  }
  if (!any) {
    for (int b = 0; b < BINS; b++) bins[b].reset();
    return;
  }
  double r = hi - lo;
  if (r < 4.0) r = 4.0;
  for (int b = 0; b < BINS; b++) {
    if (rawok_[b])
      bins[b] = ((double)sm_[b] - lo) / r;
    else
      bins[b].reset();
  }
}

// ---- render outputs

std::optional<Result> ScanSession::result() const {
  if (phase == RESULT && !reason && theta_deg && s0_deg) return Result{*theta_deg, *s0_deg};
  return std::nullopt;
}

std::optional<Sweep> ScanSession::sweep(opt_ticks t_ms) const {
  if (phase == SWEEP) return Sweep{wedge_deg, bins, active_bin(), paused};
  if (phase == RESULT && result()) {
    std::optional<int32_t> ab = best_bin;
    if (t_ms) {
      const int32_t e = ticks_diff(*t_ms, *t_result);
      if (e >= BLINK_MS || (floordiv(e, T::SCAN_BLINK_MS) & 1)) ab.reset();
    }
    return Sweep{*theta_deg, bins, ab, false};
  }
  return std::nullopt;
}

bool ScanSession::done(ticks_t t_ms) const {
  if (phase != RESULT) return false;
  if (!result()) return true;
  return ticks_diff(t_ms, *t_result) >= BLINK_MS + MORPH_MS;
}

}  // namespace scan
}  // namespace hm
