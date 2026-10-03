"""finder.menu: the MENU list on its own (Game wiring: tests/test_game.py)."""

from finder import menu as M
from finder import tuning as T
from finder.compat import ticks_add


def test_select_confirm_and_close():
    m = M.Menu()
    m.open(0)
    assert m.is_open and m.sel == 0 and m.top == 0 and m.sub == "0v"
    assert m.select(10, M.SUN) == M.SUN and m.is_open           # settings rows stay open
    assert m.select(20, M.END) is None                           # asks first
    m.window(False, 0, False)
    assert m.rows == ("RESUME", "SUN: OFF", "BUZZ: FULL", "PLACE: OUT")
    m.scroll(30, 1)                                              # a swipe cancels it
    m.window(False, 0, False)
    assert m.rows[3] == "END ROUND" and m.top == 1 and m.sel == 1 and m.sub == "0^"
    assert m.select(40, M.END) is None and m.select(50, M.END) == M.END and not m.is_open
    m.open(100)
    assert m.select(110, M.RESUME) == M.RESUME and not m.is_open


def test_next_scrolls_and_confirms_on_end_only():
    m = M.Menu()
    m.open(0)
    for k in range(4):
        assert m.next(k) is None
    assert m.sel == M.END and m.top == 1
    assert m.select(10, M.END) is None
    assert m.next(20) == M.END and not m.is_open                 # short press confirms
    m.open(30)
    m.select(31, M.END)                                          # armed on row 4, but ...
    m.sel = M.PLACE
    assert m.next(32) is None and m.sel == M.END                 # ... moving cancels it
    m.window(True, 2, True)
    assert m.rows == ("SUN: ON", "BUZZ: OFF", "PLACE: IN", "END ROUND")


def test_tap_rows_and_timeouts():
    m = M.Menu()
    m.open(0)
    y = T.MENU_ROWS_Y[2] + 1
    assert m.tap(10, y) == M.BUZZ and m.sel == M.BUZZ
    assert m.tap(20, 5) is None                                  # above the rows
    m.next(30)
    m.next(30)                                                   # END ROUND, list scrolled
    m.select(30, M.END)
    m.window(False, 0, False)
    assert m.rows[3] == M.CONFIRM
    m.tick(30 + T.MENU_CONFIRM_MS)                               # the confirm times out
    m.window(False, 0, False)
    assert m.is_open and m.rows[3] == "END ROUND"
    t = ticks_add(0, -100)                                       # across the ticks wrap
    m.open(t)
    m.tick(ticks_add(t, T.MENU_AUTOCLOSE_MS - 1))
    assert m.is_open
    m.tick(ticks_add(t, T.MENU_AUTOCLOSE_MS))
    assert not m.is_open
