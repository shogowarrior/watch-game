"""How-to cards over PAIRING ``looking`` (ui-spec §6 PAIRING, sub ``howto``).

Four cards a player can flip with sideways swipes while the watch waits for
its partner. UI state only: the beacon, the candidate logic and pairing run
underneath unchanged. ``CARDS`` copies the real screens (the runes, the
split countdown, a warmer chevron, the bump view), so each card frame is a
tuple of constants (no allocation per frame).
"""

from finder import tuning as T

N = 4
DEMO_RUNES = (0, 3, 6)          # sun, wave, cross: an example row, never a pair code
HINT = "SWIPE: HOW TO PLAY"
# (top_text, word, glyph, runes, countdown, trend, bump_icons), index = card - 1
CARDS = (("HOW TO PLAY 1/4", "PAIR UP", "runes", DEMO_RUNES, None, 0, None),
         ("HOW TO PLAY 2/4", "SPLIT UP", "countdown", None, T.PAIR_SPLIT_S, 0, None),
         ("HOW TO PLAY 3/4", "GET CLOSER", "chevrons", None, None, 1, None),
         ("HOW TO PLAY 4/4", "BUMP!", "bump", None, None, 0, 0))   # both ready, neither lit


class HowTo:
    """The open card: 0 = closed, 1..N."""

    def __init__(self):
        self.card = 0

    def reset(self):
        self.card = 0

    @property
    def open(self):
        return self.card != 0

    def flip(self, d):
        """A sideways swipe: ``d`` +1 = swipe left (next), -1 = right (previous).
        Closed: either direction opens card 1 (returns True). Past either end the
        cards close; there is no wrap."""
        c = self.card
        if c == 0:
            self.card = 1
            return True
        c += d
        self.card = c if 1 <= c <= N else 0
        return False
