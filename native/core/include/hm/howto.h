// finder/howto.py: the how-to cards over PAIRING looking (ui-spec §6 PAIRING,
// sub howto).
//
// Four cards a player can flip with sideways swipes while the watch waits for
// its partner. UI state only: the beacon, the candidate logic and pairing run
// underneath unchanged. CARDS copies the real screens (the runes, the split
// countdown, a warmer chevron, the bump view), so each card frame is a row of
// constants.
//
// Python None: a card's runes, countdown and bump_icons are std::optional.
#pragma once
#include <stdint.h>

#include <optional>

#include "hm/render_params.h"
#include "hm/tuning.h"

namespace hm {
namespace howto {

namespace rp = hm::render_params;

constexpr int N = 4;
constexpr rp::Runes DEMO_RUNES = {3, {0, 3, 6}};   // sun, wave, cross: an example row, never a pair code
constexpr const char* HINT = "SWIPE: HOW TO PLAY";

// (top_text, word, glyph, runes, countdown, trend, bump_icons)
struct Card {
  const char* top_text;
  const char* word;
  rp::Glyph glyph;
  std::optional<rp::Runes> runes;
  std::optional<int32_t> countdown;
  int32_t trend;
  std::optional<int32_t> bump_icons;
};
// index = card - 1
constexpr Card CARDS[N] = {
    {"HOW TO PLAY 1/4", "PAIR UP", rp::Glyph::RUNES, DEMO_RUNES, std::nullopt, 0, std::nullopt},
    {"HOW TO PLAY 2/4", "SPLIT UP", rp::Glyph::COUNTDOWN, std::nullopt, T::PAIR_SPLIT_S, 0, std::nullopt},
    {"HOW TO PLAY 3/4", "GET CLOSER", rp::Glyph::CHEVRONS, std::nullopt, std::nullopt, 1, std::nullopt},
    {"HOW TO PLAY 4/4", "BUMP!", rp::Glyph::BUMP, std::nullopt, std::nullopt, 0, 0},   // both ready, neither lit
};

// The open card: 0 = closed, 1..N.
class HowTo {
 public:
  void reset() { card = 0; }
  bool open() const { return card != 0; }
  // A sideways swipe: d +1 = swipe left (next), -1 = right (previous).
  // Closed: either direction opens card 1 (returns true). Past either end the
  // cards close; there is no wrap.
  bool flip(int32_t d);

  int32_t card = 0;
};

}  // namespace howto
}  // namespace hm
