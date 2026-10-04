"""finder/howto: the how-to card table and the flip rules (ui-spec §6 PAIRING)."""

from finder import tuning as T
from finder import howto as HT
from finder.game import W_BUMP


def test_closed_until_a_swipe_and_either_direction_opens_card_1():
    h = HT.HowTo()
    assert h.card == 0 and not h.open
    assert h.flip(1) is True and h.card == 1 and h.open
    h.reset()
    assert h.flip(-1) is True and h.card == 1


def test_left_goes_forward_right_goes_back_and_the_ends_close():
    h = HT.HowTo()
    h.flip(1)
    seen = [h.card]
    for _ in range(3):
        assert h.flip(1) is False
        seen.append(h.card)
    assert seen == [1, 2, 3, 4]
    h.flip(1)
    assert h.card == 0                          # past card 4: closed, no wrap
    h.flip(-1)
    h.flip(1)
    assert h.card == 2
    h.flip(-1)
    h.flip(-1)
    assert h.card == 0                          # back past card 1: closed


def test_card_table_fits_the_fonts():
    assert len(HT.CARDS) == HT.N == 4
    for k, (top, word, glyph, runes, cd, trend, bump) in enumerate(HT.CARDS):
        assert top == "HOW TO PLAY %d/4" % (k + 1)
        assert len(top) <= T.LABEL_MAX_CHARS and not set(top) - set(T.LABEL_CHARS), top
        assert len(word) <= T.WORD_MAX_CHARS and not set(word) - set(T.WORD_CHARS), word
        assert glyph in T.GLYPHS
    h = HT.HINT
    assert len(h) <= T.LABEL_MAX_CHARS and not set(h) - set(T.LABEL_CHARS), h
    assert all(0 <= r <= 7 for r in HT.DEMO_RUNES)
    assert HT.CARDS[1][4] == T.PAIR_SPLIT_S
    assert HT.CARDS[2][5] == 1                  # one warmer chevron
    assert HT.CARDS[3][1] == W_BUMP and HT.CARDS[3][6] == 0
