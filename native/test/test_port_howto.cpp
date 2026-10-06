// finder/howto.py against its Python trace (tests/test_howto.py drives it),
// and its card table as tests/test_howto.py checks it.
#include <stdio.h>
#include <string.h>

#include "check.h"
#include "hm/game.h"
#include "hm/howto.h"
#include "port.h"

using namespace hm;
using namespace hmt;

TEST(trace_howto) {
  Port<howto::HowTo> p;
  p.cls = "finder.howto.HowTo";
  p.make = [](const Json&, Calls&) { return std::make_unique<howto::HowTo>(); };
  p.call = [](howto::HowTo& h, const std::string& m, const Json& a) -> Json {
    if (m == "reset") return h.reset(), J();
    if (m == "flip") return J(h.flip((int32_t)a[0].in()));
    unported(m);
  };
  p.state = [](const howto::HowTo& h, State& s) {
    s("card", J(h.card));
    s("open", J(h.open()));
  };
  CHECK_REPLAY(p);
}

// Every char of s is in chars and it is at most n long.
static bool fits(const char* s, const char* chars, int32_t n) {
  if ((int32_t)strlen(s) > n) return false;
  for (const char* c = s; *c; c++)
    if (!strchr(chars, *c)) return false;
  return true;
}

// test_card_table_fits_the_fonts
TEST(howto_card_table_fits_the_fonts) {
  for (int k = 0; k < howto::N; k++) {
    const howto::Card& c = howto::CARDS[k];
    char top[32];
    snprintf(top, sizeof top, "HOW TO PLAY %d/4", k + 1);
    CHECK(strcmp(c.top_text, top) == 0);
    CHECK(fits(c.top_text, T::LABEL_CHARS, T::LABEL_MAX_CHARS));
    CHECK(fits(c.word, T::WORD_CHARS, T::WORD_MAX_CHARS));
    CHECK(c.glyph != render_params::Glyph::NONE);
  }
  CHECK(fits(howto::HINT, T::LABEL_CHARS, T::LABEL_MAX_CHARS));
  for (int32_t r : howto::DEMO_RUNES.ids) CHECK(0 <= r && r <= 7);
  CHECK(howto::CARDS[1].countdown == T::PAIR_SPLIT_S);
  CHECK(howto::CARDS[2].trend == 1);   // one warmer chevron
  CHECK(strcmp(howto::CARDS[3].word, game::W_BUMP) == 0 && howto::CARDS[3].bump_icons == 0);
  CHECK(howto::CARDS[0].runes && howto::CARDS[0].runes->n == 3 && !howto::CARDS[1].runes);
}
