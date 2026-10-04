// app/imu_feed.py against its Python trace (tests/test_app_runtime.py: the
// feed's own tests and the game loop's use of it).
#include <optional>

#include "check.h"
#include "hm/imu_feed.h"
#include "port.h"

using namespace hm;
using namespace hmt;

namespace {

// The imu the test gave the feed (its FakeIMU, or hal's BMA423 on fake
// buses): answers each call from the trace and takes on the fields recorded
// after it.
struct TraceImu final : app::Imu {
  Calls* calls = nullptr;

  int fifo_read_mg() override {
    const Json line = calls->device("imu", "fifo_read_mg", J(std::vector<Json>{}));
    return took(line) ? (int)line["r"].in() : -1;
  }
  bool set_odr(int32_t hz) override { return took(calls->device("imu", "set_odr", J(std::vector<Json>{J(hz)}))); }

  // false where Python raised OSError (a bus error)
  bool took(const Json& line) {
    const Json& s = line["s"];
    if (const Json* f = s.get("fifo_mg")) {
      if (f->a.size() != 3 * FIFO_FRAMES) throw Mismatch("fifo_mg holds " + std::to_string(f->a.size()) + " values");
      for (size_t k = 0; k < f->a.size(); k++) fifo_mg[k] = (int16_t)f->a[k].in();
    }
    if (const Json* o = s.get("odr")) odr = (int32_t)o->in();
    const Json* err = line.get("err");
    if (err && err->str() != "OSError") throw Mismatch("imu raised " + err->str());
    return !err;
  }
};

// One recorded ImuFeed with its imu and the recorded calls of its on_tap.
struct Rig {
  TraceImu imu;
  Calls* calls = nullptr;
  bool tracker_written = false;   // other code changed the tracker (the Runtime's set_chip)
  std::optional<imu_feed::ImuFeed> f;
};

void recorded_tap(void* rig, ticks_t t) { static_cast<Rig*>(rig)->calls->take("on_tap", J(std::vector<Json>{J(t)})); }

}  // namespace

TEST(trace_imu_feed) {
  Port<Rig> p;
  p.cls = "app.imu_feed.ImuFeed";
  // imu: its "dev" lines; on_tap: answered from the trace.
  p.ignore = {"imu", "on_tap"};
  p.make = [](const Json& a, Calls& calls) {
    auto r = std::make_unique<Rig>();
    r->calls = r->imu.calls = &calls;
    const Json& d = a[0];
    if (d["fifo_mg"]["n"].in() != 3 * app::Imu::FIFO_FRAMES) throw Mismatch("the imu's FIFO is not 170 frames");
    r->imu.odr = (int32_t)d["odr"].in();
    if (const Json* z = d.get("z_sign")) r->imu.z_sign = (int)z->in();
    r->f.emplace(r->imu, a[1].null() ? nullptr : recorded_tap, r.get(), (int32_t)a[2].in(),
                 a[3].null() ? std::nullopt : std::optional<int>((int)a[3].in()));
    return r;
  };
  p.call = [](Rig& r, const std::string& m, const Json& a) -> Json {
    imu_feed::ImuFeed& f = *r.f;
    if (m == "set_fast") return f.set_fast(a[0].flag()) ? J() : Jraise();
    if (m == "motor") return f.motor(tick(a[0]), a[1].num()), J();
    if (m == "blanked") return J(f.blanked(tick(a[0])));
    if (m == "poll") {
      const int n = f.poll(tick(a[0]));
      return n < 0 ? Jraise() : J(n);
    }
    unported(m);
  };
  p.state = [](const Rig& r, State& s) {
    const imu_feed::ImuFeed& f = *r.f;
    s("out_hz", J(f.out_hz));
    if (r.tracker_written)
      s.skip("tracker");
    else
      s("tracker", Jobj("MotionTracker", f.tracker, tracker_state));
    s("thr2", J(f.thr2));
    s("peak2", J(f.peak2));
    s("spike_min_ms", J(f.spike_min_ms));
    s("spike_max_ms", J(f.spike_max_ms));
    s("refractory_ms", J(f.refractory_ms));
    s("blank_ms", J(f.blank_ms));
    s("n_samples", J(f.n_samples));
    s("n_taps", J(f.n_taps));
    s("n_rejected", J(f.n_rejected));
    s("n_low", J(f.n_low));
    s("n_blanked", J(f.n_blanked));
    s("last_tap", J(f.last_tap));
    s("peak_mg", J(f.peak_mg));
    s("hz", J(f.hz));
    s("fast", J(f.fast));
    s("dec", J(f.dec));
    s("g_shift", J(f.g_shift));
    s("tap", J(f.tap));
  };
  p.set = [](Rig& r, const std::string& fld, const Json& v) {
    imu_feed::ImuFeed& f = *r.f;
    if (fld == "on_tap") {   // the Runtime's feed.on_tap = self._on_tap
      f.on_tap = v.null() ? nullptr : recorded_tap;
      f.tap_ctx = &r;
      return true;
    }
    // set_chip from the BMA423 feature engine, which the native Runtime leaves
    // out (hm/platform.h): its tracker is not checked from here on
    if (fld == "tracker") return r.tracker_written = true;
    int32_t* ints[] = {&f.thr2, &f.peak2, &f.spike_min_ms, &f.spike_max_ms, &f.refractory_ms, &f.blank_ms};
    const char* names[] = {"thr2", "peak2", "spike_min_ms", "spike_max_ms", "refractory_ms", "blank_ms"};
    for (int k = 0; k < 6; k++)
      if (fld == names[k]) return *ints[k] = (int32_t)v.in(), true;
    return false;
  };
  CHECK_REPLAY(p);
}
