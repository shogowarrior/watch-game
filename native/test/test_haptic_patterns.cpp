// hm::haptic_patterns against finder/haptic_patterns.py (native/test/golden/haptic_patterns.txt).
#include <stdio.h>

#include <memory>
#include <string>

#include "check.h"
#include "golden.h"
#include "hm/haptic_patterns.h"

namespace hp = hm::haptic_patterns;
using hmt::none;
using hmt::num;

namespace {

hp::OptTicks opt_t(const std::string& s) {
  if (none(s)) return std::nullopt;
  return (hm::ticks_t)num(s);
}

hp::Haptic haptic(const std::string& s) { return none(s) ? hp::NONE : hp::from_name(s.c_str()); }

// <busy> <active> <beat_due|n> <beat_playing>, as the generator prints it.
std::string state(const hp::HapticPlayer& pl) {
  const hp::OptTicks due = pl.beat_due();
  return std::string(pl.busy() ? "1" : "0") + " " + (pl.active() ? "1" : "0") + " " +
         (due ? std::to_string(*due) : std::string("n")) + " " + (pl.beat_playing() ? "1" : "0");
}

// Tokens [from, end) joined by single spaces.
std::string rest(const hmt::Tokens& t, size_t from) {
  std::string s;
  for (size_t i = from; i < t.size(); i++) s += (i > from ? " " : "") + t[i];
  return s;
}

}  // namespace

TEST(test_haptic_patterns_tables_like_python) {
  int names = 0, consts = 0, ranks = 0, strong = 0;
  for (const hmt::Tokens& t : hmt::golden("haptic_patterns")) {
    if (t[0] == "names") {
      CHECK(t.size() == 1 + hp::N_NAMES);
      for (int i = 0; i < hp::N_NAMES; i++) CHECK(t[1 + i] == hp::name((hp::Haptic)i));
      CHECK(hp::name(hp::NONE) == nullptr && hp::from_name("found") == hp::NONE && hp::from_name(nullptr) == hp::NONE);
      names++;
    } else if (t[0] == "consts") {
      const long want[] = {hp::MIN_PULSE_MS, hp::MIN_GAP_MS,   hp::MAX_DUTY_PCT, hp::EVENT_GUARD_MS,
                           hp::HB_RESUME_MS, hp::BLANKING_MS, hp::NRANK};
      for (int i = 0; i < 7; i++) CHECK(num(t[1 + i]) == want[i]);
      consts++;
    } else if (t[0] == "rank") {
      const hp::Haptic h = hp::from_name(t[1].c_str());
      CHECK(h != hp::NONE);
      const hp::Pattern& p = hp::PATTERNS[h];
      CHECK(num(t[2]) == hp::RANK[h] && num(t[3]) == hp::TOTAL_MS[h]);
      CHECK(num(t[4]) == hp::on_ms(p) && num(t[5]) == hp::min_period(p));
      CHECK((int)t.size() == 6 + 2 * p.n_steps());
      for (int i = 0; i < p.n_steps(); i++) CHECK(num(t[6 + 2 * i]) == p.on(i) && num(t[7 + 2 * i]) == p.off(i));
      ranks++;
    } else if (t[0] == "stronger") {
      CHECK(t[4] == hp::name(hp::stronger(haptic(t[1]), haptic(t[2]))));
      strong++;
    }
  }
  CHECK(names == 1 && consts == 1 && ranks == 9 && strong == 90);
}

TEST(test_haptic_patterns_blank_window_like_python) {
  hp::BlankWindow w;
  int n = 0;
  for (const hmt::Tokens& t : hmt::golden("haptic_patterns")) {
    if (t[0] != "blank") continue;
    size_t arrow = 2;
    if (t[1] == "extend") {
      w.extend((hm::ticks_t)num(t[2]), (int32_t)num(t[3]));
      arrow = 4;
    } else if (t[1] == "expire") {
      w.expire((hm::ticks_t)num(t[2]));
      arrow = 3;
    } else if (t[1] == "reset") {
      w.reset();
    }
    CHECK(t[arrow] == "->");
    std::string got;
    int last = -1;
    for (int ms = 0; ms < 1200; ms++) {
      const int a = w.active((hm::ticks_t)ms);
      if (a != last) got += (got.empty() ? "" : " ") + std::to_string(ms) + ":" + std::to_string(a);
      last = a;
    }
    got += " until=" + (w.until ? std::to_string(*w.until) : std::string("n"));
    CHECK(rest(t, arrow + 1) == got);
    n++;
  }
  CHECK(n == 18);
}

TEST(test_haptic_patterns_player_like_python) {
  std::unique_ptr<hp::HapticPlayer> pl;
  int news = 0, ops = 0, runs = 0;
  for (const hmt::Tokens& t : hmt::golden("haptic_patterns")) {
    const std::string& op = t[0];
    if (op == "new") {
      pl.reset(new hp::HapticPlayer((int)num(t[1])));
      news++;
    } else if (op == "play" || op == "hb") {
      const hp::Haptic h = hp::from_name(t[1].c_str());   // unknown names are NONE: rejected
      const bool r = op == "play" ? pl->play_named(h, opt_t(t[2])) : pl->heartbeat(h, opt_t(t[2]));
      CHECK(num(t[4]) == r);
      CHECK(rest(t, 5) == state(*pl));
      ops++;
    } else if (op == "cancel") {
      CHECK(num(t[2]) == pl->cancel_heartbeat());
      CHECK(rest(t, 3) == state(*pl));
      ops++;
    } else if (op == "allowed") {
      CHECK(num(t[3]) == pl->hb_allowed(opt_t(t[1])));
      ops++;
    } else if (op == "metro") {
      const hp::OptTicks at = opt_t(t[2]);
      if (at) pl->set_metronome((int32_t)num(t[1]), at, haptic(t[3]));
      else pl->set_metronome((int32_t)num(t[1]));   // the Python's name is None whenever t is
      CHECK(num(t[5]) == pl->period_ms());
      ops++;
    } else if (op == "mode") {
      pl->set_mode((int)num(t[1]));
      CHECK(rest(t, 3) == state(*pl));
      ops++;
    } else if (op == "run") {
      const long t0 = num(t[1]), t1 = num(t[2]), step = num(t[3]);
      std::string lv;
      double last = -1.0;
      for (long ms = t0; ms < t1; ms += step) {
        const double v = pl->tick((hm::ticks_t)ms);
        CHECK(v == 0.0 || v == 1.0);
        if (last < 0.0) lv = std::to_string((int)v);
        else if (v != last) lv += " " + std::to_string(ms) + ":" + std::to_string((int)v);
        last = v;
      }
      const std::string got = state(*pl) + " " + lv;
      if (rest(t, 5) != got) printf("  run %ld..%ld: want %s\n  got %s\n", t0, t1, rest(t, 5).c_str(), got.c_str());
      CHECK(rest(t, 5) == got);
      runs++;
    }
  }
  CHECK(news == 105 && ops == 189 && runs == 168);
}

// The Python's own wrap test, on the C++ 2^32 ticks (the golden file stays below 2^29).
TEST(test_haptic_patterns_ticks_wraparound) {
  const hm::ticks_t t0 = hm::ticks_add(0, -40);
  hp::HapticPlayer pl;
  pl.play_named(hp::FARTHER, t0);
  CHECK(pl.tick(t0) == 1.0);
  CHECK(pl.tick(hm::ticks_add(t0, 299)) == 1.0);
  CHECK(pl.tick(hm::ticks_add(t0, 300)) == 0.0);
  pl.set_metronome(500, hm::ticks_add(t0, 1300));
  bool on_1300 = false, on_1360 = false, on_1800 = false;
  for (int k = 300; k < 2400; k++) {
    const bool on = pl.tick(hm::ticks_add(t0, k)) != 0.0;
    on_1300 |= on && k == 1300;
    on_1360 |= on && k == 1360;
    on_1800 |= on && k == 1800;
  }
  CHECK(on_1300 && !on_1360 && on_1800);
  hp::BlankWindow w;
  w.extend(t0, 100);
  CHECK(w.active(hm::ticks_add(t0, 99)) && !w.active(hm::ticks_add(t0, 100)));
}
