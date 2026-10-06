#include "hm/imu_feed.h"

#include <math.h>

namespace hm {
namespace imu_feed {

namespace {

void record(int32_t* ev, int32_t c, int32_t i, int32_t a, int32_t b, int32_t d) {
  ev[0] = c;
  ev[1] = i;
  ev[2] = a;
  ev[3] = b;
  ev[4] = d;
}

}  // namespace

// The Python's _FSRC, line for line where it can be; int16 samples need no
// sign fix (viper's ptr16 loads are unsigned), and a negative gravity scales
// by multiplying (a left shift of it is undefined in C++17).
int feed_kernel(const int16_t* mg, int n, int32_t* st, int32_t* ev) {
  int32_t gx = st[S_GX], gy = st[S_GX + 1], gz = st[S_GX + 2];
  const int s = st[S_SHIFT];
  const int32_t thr2 = st[S_THR2];
  const bool fast = st[S_FAST] != 0;
  int32_t run = st[S_RUN];
  int64_t pk = st[S_PK];
  int32_t sx = st[S_SX], sy = st[S_SX + 1], sz = st[S_SX + 2];
  int32_t sn = st[S_SN];
  const int32_t dec = st[S_DEC];
  const int32_t rmax = st[S_RMAX];
  bool seed = st[S_SEED] != 0;
  int m = 0;
  for (int i = 0; i < n; i++, mg += 3) {
    const int32_t x = mg[0], y = mg[1], z = mg[2];
    if (!seed) {
      gx = x * (1 << s);
      gy = y * (1 << s);
      gz = z * (1 << s);
      seed = true;
    }
    bool follow = true;
    if (fast) {
      const int64_t dx = x - (gx >> s), dy = y - (gy >> s), dz = z - (gz >> s);
      const int64_t m2 = dx * dx + dy * dy + dz * dz;
      if (m2 > thr2) {
        if (!run) {
          record(ev + 5 * m++, R_START, i, 0, 0, 0);
          pk = 0;
        }
        run++;
        if (m2 > pk) pk = m2;
        if (run <= rmax) follow = false;
      } else if (run) {
        record(ev + 5 * m++, R_END, i, run, (int32_t)pk, 0);
        run = 0;
      }
    }
    if (follow) {
      gx += x - (gx >> s);
      gy += y - (gy >> s);
      gz += z - (gz >> s);
    }
    sx += x;
    sy += y;
    sz += z;
    if (++sn >= dec) {
      record(ev + 5 * m++, R_BLOCK, i, sx, sy, sz);
      sx = sy = sz = sn = 0;
    }
  }
  st[S_GX] = gx;
  st[S_GX + 1] = gy;
  st[S_GX + 2] = gz;
  st[S_RUN] = run;
  st[S_PK] = (int32_t)pk;
  st[S_SX] = sx;
  st[S_SX + 1] = sy;
  st[S_SX + 2] = sz;
  st[S_SN] = sn;
  st[S_SEED] = seed;
  return m;
}

ImuFeed::ImuFeed(app::Imu& imu, TapFn on_tap, void* tap_ctx, int32_t out_hz, std::optional<int> z_sign)
    : imu(imu),
      out_hz(out_hz),
      tracker(0.7, out_hz, 1.3, z_sign ? *z_sign : imu.z_sign),   // MotionTracker's own stride defaults
      on_tap(on_tap),
      tap_ctx(tap_ctx),
      spike_min_ms(SPIKE_MIN_MS),
      spike_max_ms(SPIKE_MAX_MS),
      refractory_ms(REFRACTORY_MS),
      blank_ms(haptic_patterns::BLANKING_MS),
      fast(imu.odr != SLOW_HZ) {   // a chip left fast: set_fast(false) slows it
  const int32_t thr = (int32_t)(RUN_G * 1000);
  thr2 = thr * thr;
  const int32_t pk = (int32_t)(SPIKE_G * 1000);
  peak2 = pk * pk;
  rate_(imu.odr);
}

void ImuFeed::rate_(int32_t hz_) {
  hz = hz_;
  const int32_t d = hz / out_hz;
  dec = d > 1 ? d : 1;
  k_ = 1.0 / (1000.0 * dec);
  dq_ = 256000 / hz;
  const int32_t r = 4000 / hz;
  resync_ = r > RESYNC_MIN_MS ? r : RESYNC_MIN_MS;
  int s = G_SHIFT;
  for (int32_t q = hz / SLOW_HZ; q > 1; q >>= 1) s++;   // same 160 ms gravity tau at any rate
  const int s0 = st_[S_SHIFT];
  for (int a = S_GX; a < S_GX + 3; a++) st_[a] = (st_[a] >> s0) * (1 << s);   // gravity carries over at the new scale
  st_[S_SHIFT] = s;
  st_[S_FAST] = fast ? 1 : 0;
  st_[S_RUN] = 0;   // a run open at the switch is dropped
  st_[S_SX] = st_[S_SX + 1] = st_[S_SX + 2] = st_[S_SN] = 0;
  st_[S_DEC] = dec;
  g_shift = s;
  tap.reset();
  tb_.reset();
  f0_ = 0;
  n_ = 0;
}

bool ImuFeed::set_fast(bool on) {
  if (on == fast) return true;
  const bool ok = imu.set_odr(on ? FAST_HZ : SLOW_HZ);
  const int32_t odr = imu.odr;   // the Python's finally: whatever rate the chip is at
  if (odr != hz) {
    fast = odr != SLOW_HZ;
    rate_(odr);
  }
  return ok;
}

void ImuFeed::motor(ticks_t t, double level) {
  const bool on = level > 0;
  if (on == lvl_on_) return;
  lvl_on_ = on;
  if (on) {
    on_[pi_] = off_[pi_] = t;
    pi_ = (pi_ + 1) % NP;
    if (pn_ < NP) pn_++;
  } else if (pn_) {
    off_[(pi_ + NP - 1) % NP] = t;
  }
}

bool ImuFeed::blanked(ticks_t t) const {
  int i = pi_;
  bool last = true;
  for (int n = pn_; n; n--) {
    i = (i + NP - 1) % NP;
    if (ticks_diff(t, on_[i]) >= 0) {
      if (last && lvl_on_) return true;
      return ticks_diff(t, ticks_add(off_[i], blank_ms)) < 0;   // older pulses end before this one starts
    }
    last = false;
  }
  return false;
}

ticks_t ImuFeed::at_(int32_t i) const { return ticks_add(*tb_, (f0_ + i * dq_) >> 8); }

void ImuFeed::stamp_(ticks_t now, int n) {
  const int32_t dq = dq_;
  ticks_t tb = ticks_add(now, -(((n - 1) * dq) >> 8));
  int32_t f0 = 0;
  if (tb_) {
    int32_t q0 = f0_ + n_ * dq;   // first sample after the last batch
    const ticks_t q = ticks_add(*tb_, q0 >> 8);
    q0 &= 255;
    const int32_t e = ticks_diff(q, tb);
    if (-resync_ <= e && e <= resync_ && ticks_diff(now, ticks_add(q, (q0 + (n - 1) * dq) >> 8)) >= 0) {
      tb = q;
      f0 = q0;
    }
  }
  tb_ = tb;
  f0_ = f0;
  n_ = n;
}

int ImuFeed::poll(ticks_t now) {
  tap.reset();
  const int n = imu.fifo_read_mg();
  if (n <= 0) return n;   // empty, or -1: a bus error
  stamp_(now, n);
  st_[S_THR2] = thr2;
  st_[S_RMAX] = spike_max_ms * hz / 1000 + 1;
  const int m = feed_kernel(imu.fifo_mg, n, st_, ev_);
  for (int r = 0; r < 5 * m; r += 5) {
    const int32_t c = ev_[r];
    if (c == R_BLOCK)
      tracker.add_sample(at_(ev_[r + 1]), ev_[r + 2] * k_, ev_[r + 3] * k_, ev_[r + 4] * k_);
    else if (c == R_START)
      run_t_ = at_(ev_[r + 1]);
    else
      end_run_(at_(ev_[r + 1]), ev_[r + 2], ev_[r + 3]);
  }
  n_samples += n;
  return n;
}

void ImuFeed::end_run_(ticks_t t_end, int32_t run, int32_t pk) {
  const int64_t w = (int64_t)run * (1000000 / hz);   // us
  const ticks_t t0 = run_t_;
  if (pk > peak_mg * peak_mg) peak_mg = (int32_t)pow((double)pk, 0.5);   // int(pk ** 0.5)
  if (pk < peak2) {
    n_low++;   // a wobble, never a spike
    return;
  }
  if (w < (int64_t)spike_min_ms * 1000 || w > (int64_t)spike_max_ms * 1000) {
    n_rejected++;
    return;
  }
  if (blanked(t0) || blanked(t_end)) {
    n_blanked++;
    n_rejected++;
    return;
  }
  if (last_tap) {
    const int32_t d = ticks_diff(t0, *last_tap);
    if (0 <= d && d < refractory_ms) {
      n_rejected++;
      return;
    }
  }
  last_tap = t0;
  tap = t0;
  n_taps++;
  if (on_tap) on_tap(tap_ctx, t0);
}

}  // namespace imu_feed
}  // namespace hm
