"""finder/session: small formatters shared by the game screens."""

from finder import tuning as T
from finder.session import fmt_found


def test_fmt_found_table():
    for s, want in ((-3, "FOUND 0:00"), (0, "FOUND 0:00"), (108, "FOUND 1:48"),
                    (599, "FOUND 9:59"), (600, "FOUND10:00"), (768, "FOUND12:48"),
                    (5999, "FOUND99:59"), (6000, "FOUND 1H+"), (21600, "FOUND 1H+")):
        assert fmt_found(s) == want, (s, fmt_found(s))


def test_fmt_found_fits_the_word_slot():
    # every value the game can give (it caps the round at FOUND_TIME_MAX_S) fits type.word
    for s in range(0, T.FOUND_TIME_MAX_S + 120):
        w = fmt_found(s)
        assert len(w) <= T.WORD_MAX_CHARS, (s, w)
        for ch in w:
            assert ch in T.WORD_CHARS, (s, w)
        assert "M" not in w[5:], (s, w)       # an M after FOUND reads as metres
