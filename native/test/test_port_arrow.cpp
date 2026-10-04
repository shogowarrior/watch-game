// finder/arrow.py against its Python trace, and its module functions against
// the values tests/test_arrow.py asserts.
#include <math.h>
#include <string.h>

#include "check.h"
#include "hm/arrow.h"
#include "port.h"

using namespace hm;
using namespace hmt;
namespace A = hm::arrow;

namespace {


bool word_is(const char* got, const char* want) { return strcmp(got, want) == 0; }

bool near(double a, double b) { return fabs(a - b) <= 1e-6; }

A::Mode mode_of(const Json& v) {
  if (v.str() == A::name(A::MODE_GUIDED)) return A::MODE_GUIDED;
  if (v.str() == A::name(A::MODE_STATIC)) return A::MODE_STATIC;
  throw Mismatch("no such mode: " + dump(v));
}

void arrow_state(const A::Arrow& a, State& s) {
  s("theta", J(a.theta));
  s("s0", J(a.s0));
  s("mode", J(A::name(a.mode)));
  s("probe", J(a.probe));
  s("t0", J(a.t0));
  s("turn_deg", J(a.turn_deg));
  s("steps_walked", J(a.steps_walked));
  s("still_s", J(a.still_s));
  s("colder_hits", J(a.colder_hits));
  s("unreliable", J(a.unreliable));
  s("link_ok", J(a.link_ok));
  s("phase", J(A::name(a.phase)));
  s("pacer", J(a.pacer));
  s("haptic", J(haptic_patterns::name(a.haptic)));
  s("toast", J(a.toast));
  s("sigma", J(a.sigma));
  s("sub", J(render_params::name(a.sub)));
  s("glyph", J(render_params::name(a.glyph)));
  s("arrow_deg", J(a.arrow_deg));
  s("cone_deg", J(a.cone_deg));
  s("arrow_style", J(render_params::name(a.arrow_style)));
  if (a.sweep) {
    const render_params::Sweep& w = *a.sweep;
    s("sweep", J(std::vector<Json>{J(w.wedge_deg), Jarr(w.bins, w.n_bins), J(w.active_bin), J(w.paused)}));
  } else {
    s("sweep", J());
  }
  s("word", J(a.word));
  s("top_text", J(a.top_text));
  s("done", J(a.done()));
}

}  // namespace

TEST(trace_arrow) {
  Port<A::Arrow> p;
  p.cls = "finder.arrow.Arrow";
  p.make = [](const Json& a, Calls&) {
    return std::make_unique<A::Arrow>(a[0].num(), a[1].num(), tick(a[2]), mode_of(a[3]), a[4].flag());
  };
  p.call = [](A::Arrow& o, const std::string& m, const Json& a) -> Json {
    if (m == "sigma_true") return J(o.sigma_true());
    if (m == "tap") return J(o.tap());
    if (m == "update") {
      const std::optional<int64_t> steps = a[2].opt_in();
      return o.update(tick(a[0]), (int32_t)a[1].in(), steps ? std::optional<uint32_t>((uint32_t)*steps) : std::nullopt,
                      (int32_t)a[3].in(), a[4].flag(), a[5].flag(), a[6].flag(), a[7].flag()),
             J();
    }
    unported(m);
  };
  p.state = arrow_state;
  p.set = [](A::Arrow& o, const std::string& f, const Json& v) {
    if (f == "s0") return o.s0 = v.num(), true;
    if (f == "turn_deg") return o.turn_deg = v.num(), true;
    if (f == "steps_walked") return o.steps_walked = (int32_t)v.in(), true;
    if (f == "still_s") return o.still_s = v.num(), true;
    if (f == "colder_hits") return o.colder_hits = (int32_t)v.in(), true;
    return false;
  };
  CHECK_REPLAY(p);
}

// test_words, test_wrap_helpers, the sigma half of test_sigma_model_and_tiers
// and test_birth_gate_and_probe_floor.
TEST(test_arrow_module_functions) {
  CHECK(word_is(A::clock_word(120), "4 O'CLOCK"));
  CHECK(word_is(A::clock_word(0), "12 O'CLOCK"));
  CHECK(word_is(A::clock_word(350), "12 O'CLOCK"));
  CHECK(word_is(A::clock_word(-30), "11 O'CLOCK"));
  CHECK(word_is(A::clock_word(180), "6 O'CLOCK"));
  CHECK(word_is(A::clock_word(44), "1 O'CLOCK"));
  CHECK(word_is(A::clock_word(46), "2 O'CLOCK"));
  CHECK(word_is(A::quad_word(30), "AHEAD"));
  CHECK(word_is(A::quad_word(90), "RIGHT"));
  CHECK(word_is(A::quad_word(-100), "LEFT"));
  CHECK(word_is(A::quad_word(170), "BEHIND"));
  CHECK(word_is(A::quad_word(-170), "BEHIND"));
  CHECK(word_is(A::reveal_word(-15, 25), "AHEAD"));
  CHECK(word_is(A::reveal_word(345, 40), "AHEAD"));
  CHECK(word_is(A::reveal_word(120, 30), "4 O'CLOCK"));
  CHECK(word_is(A::reveal_word(120, 31), "RIGHT"));
  for (int th = -180; th <= 180; th += 5) CHECK(strlen(A::reveal_word(th, 20)) <= 10);

  CHECK(A::wrap180(190) == -170);
  CHECK(A::wrap180(-190) == 170);
  CHECK(A::wrap180(180) == 180);
  CHECK(A::wrap180(-180) == 180);
  CHECK(A::wrap360(-10) == 350);
  CHECK(A::wrap360(720) == 0);

  CHECK(near(A::sigma(30, 0, 0, 0, 0), 30));
  CHECK(fabs(A::sigma(30, 120, 40, 0, 0) - sqrt(2008.0)) < 1e-6);
  CHECK(near(A::sigma(30, 0, 0, 0, 1), 50));
  CHECK(near(A::sigma(30, 0, 0, 40, 0), 50));

  CHECK(!A::make(90, 45.5, 0));
  CHECK(A::make(90, 45.0, 0));
  const std::optional<A::Arrow> p = A::make(90, 10, 0, A::MODE_GUIDED, true);
  CHECK(p && p->s0 == 35.0 && p->arrow_style == render_params::ArrowStyle::SOLID_B);
  CHECK(!A::make(90, 46, 0, A::MODE_GUIDED, true));
}
