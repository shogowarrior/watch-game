#include "sf/bench.h"

#include "sf/axp202.h"

namespace sf {

namespace {

alignas(4) uint8_t ring_map[FIELD_W * FIELD_W];
alignas(4) uint16_t strips[2][FIELD_W * Bench::SH];     // internal RAM: DMA-capable

const FieldParams& HOT = BENCH_FIXTURES[1];
constexpr uint32_t FRAME_BYTES = FIELD_W * FIELD_W * 2;
constexpr uint32_t SEG_US = 3000000;                     // one timed run

// 10, 20 and 30 divide every zone period and the 200 ms scan blink
// (/mnt/project-files/themes/fps-targets.md); 40 and 60 show the ceiling.
const int LOCKED_40[] = {10, 20, 30, 40, 60};
const int LOCKED_OTHER[] = {10, 20, 30};
// Partial redraw: window sizes, from one strip down to an 8 px block.
const uint8_t WINDOWS[][2] = {{240, 24}, {120, 120}, {60, 60}, {30, 30}, {8, 8}};

}  // namespace

Bench::Bench(BenchHost& h)
    : h_(h), panel_(h.lcd, h.clock), scene_(field_), bma_(h.i2c0, h.clock), sampler_(bma_, h.clock) {}

bool Bench::setup() {
  logf("SF hello variant=%s framework=%s", h_.variant, h_.framework);
  if (!axp202::panel_power_on(h_.i2c0)) {
    logf("SF error what=axp202");
    return false;
  }
  h_.clock.delay_ms(10);
  if (!clock(CLOCKS[0])) return false;
  panel_.init(strips[0], SH);
  h_.backlight(true);
  const uint32_t t0 = h_.clock.now_us();
  build_map(ring_map, FIELD_W);
  logf("SF setup map_us=%u", (unsigned)(h_.clock.now_us() - t0));
  return true;
}

bool Bench::clock(uint32_t hz) {
  if (h_.lcd.set_clock(hz)) return true;
  logf("SF error what=spi_clock hz=%u", (unsigned)hz);
  return false;
}

void Bench::frame(const FieldParams& p, ticks_t t, bool push, bool wait_each) {
  // One frame: the field state, then each strip blitted and (optionally) pushed
  // while the previous one is still on the wire.
  scene_.step(p, t);
  if (push && h_.drawer) return h_.drawer->frame(field_.pal, ring_map);
  if (push) panel_.begin_frame();
  for (int s = 0; s < NS; s++) {
    uint16_t* buf = strips[s & 1];
    blit(buf, ring_map, field_.pal, s * FIELD_W * SH, FIELD_W * SH);
    if (!push) continue;
    panel_.push_strip(buf, SH);
    if (wait_each) h_.lcd.wait();
  }
}

void Bench::compose() {
  // CPU cost of a frame, per fixture: field state + palette, then the 10 strip blits.
  for (const FieldParams& p : BENCH_FIXTURES) {
    RippleField& f = field_;
    f.reset();
    FieldScene sc(f);
    uint32_t step = 0, blit_us = 0;
    ticks_t t = 10000;
    const int n = 60;
    for (int i = 0; i < n; i++, t += 33) {
      uint32_t t0 = h_.clock.now_us();
      sc.step(p, t);
      const uint32_t t1 = h_.clock.now_us();
      for (int s = 0; s < NS; s++) blit(strips[s & 1], ring_map, f.pal, s * FIELD_W * SH, FIELD_W * SH);
      step += t1 - t0;
      blit_us += h_.clock.now_us() - t1;
    }
    logf("SF compose fixture=\"%s\" frames=%d step_us=%u blit_us=%u", p.name, n, (unsigned)(step / n),
         (unsigned)(blit_us / n));
  }
}

void Bench::push(uint32_t hz) {
  // Per frame at this SPI clock: the wire alone, compose then push strip by strip
  // (what MicroPython does), and compose overlapped with the DMA push.
  if (!clock(hz)) return;
  const int n = 20;
  uint32_t wire = 0, serial = 0, overlap = 0;
  ticks_t t = h_.clock.now_ms();
  for (int i = 0; i < n; i++) {
    uint32_t t0 = h_.clock.now_us();
    panel_.begin_frame();
    for (int s = 0; s < NS; s++) panel_.push_strip(strips[s & 1], SH);
    panel_.end_frame();
    wire += h_.clock.now_us() - t0;
    t0 = h_.clock.now_us();
    frame(HOT, t += 33, true, true);
    panel_.end_frame();
    serial += h_.clock.now_us() - t0;
    t0 = h_.clock.now_us();
    frame(HOT, t += 33, true, false);
    panel_.end_frame();
    overlap += h_.clock.now_us() - t0;
  }
  logf("SF push hz=%u wire_us=%u serial_us=%u overlap_us=%u floor_us=%u", (unsigned)hz, (unsigned)(wire / n),
       (unsigned)(serial / n), (unsigned)(overlap / n), (unsigned)((uint64_t)FRAME_BYTES * 8 * 1000000 / hz));
  pause();
}

void Bench::windows(uint32_t hz) {
  // Partial redraw cost: a full screen sent as tiles of each size, 5 times over.
  // Each tile is its own window (CASET, RASET, RAMWR); big ones go in strip-sized chunks.
  if (!clock(hz)) return;
  const size_t chunk = (size_t)FIELD_W * SH;
  const int reps = 5;
  for (const auto& sz : WINDOWS) {
    const int w = sz[0], h = sz[1], per_row = FIELD_W / w, n = per_row * (FIELD_W / h);
    const uint32_t t0 = h_.clock.now_us();
    for (int r = 0; r < reps; r++) {
      for (int i = 0; i < n; i++) {
        panel_.begin_window(i % per_row * w, i / per_row * h, w, h);
        for (size_t left = (size_t)w * h, k = 0; left; k++) {
          const size_t c = left < chunk ? left : chunk;
          panel_.push_pixels(strips[k & 1], c);
          left -= c;
        }
      }
    }
    panel_.end_frame();
    const uint32_t us = (h_.clock.now_us() - t0) / reps;
    logf("SF window hz=%u w=%d h=%d n=%d frame_us=%u us_per_window=%u ns_per_px=%u", (unsigned)hz, w, h, n,
         (unsigned)us, (unsigned)(us / n), (unsigned)((uint64_t)us * 1000 / (FIELD_W * FIELD_W)));
    pause();
  }
}

void Bench::loop(Run& r, const FieldParams& p, int target, uint32_t dur_us, ImuSampler* inline_imu) {
  // Frames paced to target fps (0 = as fast as possible) for dur_us; a frame that
  // overruns its slot is a miss and the schedule restarts from now.
  Clock& c = h_.clock;
  r.interval.reset();
  r.work.reset();
  r.frames = r.miss = 0;
  const uint32_t period = target ? 1000000 / target : 0;
  const uint32_t start = c.now_us();
  uint32_t next = start, last = start;
  for (;;) {
    const uint32_t t0 = c.now_us();
    if (t0 - start >= dur_us) break;
    if (r.frames) r.interval.add(t0 - last);
    last = t0;
    if (inline_imu) inline_imu->poll();
    frame(p, c.now_ms(), true, false);
    r.work.add(c.now_us() - t0);
    r.frames++;
    if (!period) continue;
    next += period;
    if ((int32_t)(next - c.now_us()) < 0) {
      r.miss++;
      next = c.now_us();
    } else {
      c.sleep_until_us(next);
    }
  }
  panel_.end_frame();
  r.elapsed_us = c.now_us() - start;
}

void Bench::log_run(const char* step, uint32_t hz, int target, const Run& r) {
  const uint32_t fps10 = (uint32_t)((uint64_t)r.frames * 10000000 / r.elapsed_us);
  logf("SF %s hz=%u target=%d fps=%u.%u p50_us=%u p95_us=%u max_us=%u sd_us=%u miss=%d work_us=%u", step,
       (unsigned)hz, target, (unsigned)(fps10 / 10), (unsigned)(fps10 % 10), (unsigned)r.interval.pct(50),
       (unsigned)r.interval.pct(95), (unsigned)r.interval.pct(100), (unsigned)r.interval.sd(), r.miss,
       (unsigned)r.work.mean());
}

void Bench::locked(uint32_t hz, const int* targets, int n) {
  if (!clock(hz)) return;
  for (int i = 0; i < n; i++) {
    loop(run_, HOT, targets[i], SEG_US, nullptr);
    log_run("run", hz, targets[i], run_);
    pause();
  }
}

void Bench::imu() {
  // 800 Hz FIFO drained every 20 ms by a task on the other core, then from the
  // render loop itself (as the MicroPython game does), while frames run at 30 fps.
  if (!bma_.init(800, 8)) {
    logf("SF error what=bma423");
    return;
  }
  if (!clock(CLOCKS[1])) return;
  for (int inline_poll = 0; inline_poll < 2; inline_poll++) {
    sampler_.reset();
    if (!inline_poll) h_.imu.start(sampler_, 20);
    loop(run_, HOT, 30, 5000000, inline_poll ? &sampler_ : nullptr);
    if (!inline_poll) h_.imu.stop();
    const ImuStats& s = sampler_.stats;
    const char* where = inline_poll ? "render_loop" : "other_core";
    log_run(inline_poll ? "run_imu_inline" : "run_imu_task", CLOCKS[1], 30, run_);
    logf("SF imu where=%s odr=800 rate=%u polls=%u full=%u fifo_max=%u read_ms_per_s=%u errors=%u peak_mg=%u",
         where, (unsigned)((uint64_t)s.samples * 1000000 / run_.elapsed_us), (unsigned)s.polls, (unsigned)s.full,
         (unsigned)s.fifo_max, (unsigned)((uint64_t)s.read_us * 1000 / run_.elapsed_us), (unsigned)s.errors,
         (unsigned)s.peak_mg);
    pause();
  }
}

void Bench::run() {
  compose();
  for (uint32_t hz : CLOCKS) push(hz);
  for (uint32_t hz : CLOCKS) windows(hz);
  for (uint32_t hz : CLOCKS) {
    if (!clock(hz)) continue;
    loop(run_, HOT, 0, SEG_US, nullptr);
    log_run("run", hz, 0, run_);
    pause();
  }
  locked(CLOCKS[1], LOCKED_40, sizeof LOCKED_40 / sizeof LOCKED_40[0]);
  locked(CLOCKS[0], LOCKED_OTHER, sizeof LOCKED_OTHER / sizeof LOCKED_OTHER[0]);
  locked(CLOCKS[2], LOCKED_OTHER, sizeof LOCKED_OTHER / sizeof LOCKED_OTHER[0]);
  imu();
  logf("SF done");
}

void Bench::show(uint32_t hz, int target, int s) {
  if (!clock(hz)) return;
  logf("SF showing hz=%u target=%d s=%d", (unsigned)hz, target, s);
  loop(run_, HOT, target, (uint32_t)s * 1000000, nullptr);
  log_run("show", hz, target, run_);
  pause();
}

}  // namespace sf
