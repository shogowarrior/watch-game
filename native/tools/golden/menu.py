"""finder/menu.py: MENU sessions, one line per input or tick: next through
every row and the scroll it brings, swipes past both ends, taps on each row,
between rows and off the list, the END ROUND confirm (armed, confirmed by a
press, select or tap, cancelled by another row, a swipe or moving off, timed
out), auto-close, and the row labels for every SUN / BUZZ / PLACE setting.
Labels print with '_' for their spaces."""

from finder import menu as M
from finder import tuning as T


class _Run:
    def __init__(self):
        self.m = M.Menu()

    def state(self):
        m = self.m
        return "%d %d %d %d %s %s" % (m.is_open, m.sel, m.top, m._confirm_t is not None, m.sub,
                                      " ".join(r.replace(" ", "_") for r in m.rows))

    def _act(self, a):
        return "n" if a is None else str(a)

    def open(self, t):
        self.m.open(t)
        return "open %d -> %s" % (t, self.state())

    def close(self):
        self.m.close()
        return "close -> %s" % self.state()

    def next(self, t):
        a = self.m.next(t)
        return "next %d -> %s %s" % (t, self._act(a), self.state())

    def scroll(self, t, d):
        self.m.scroll(t, d)
        return "scroll %d %d -> %s" % (t, d, self.state())

    def tap(self, t, y):
        a = self.m.tap(t, y)
        return "tap %d %d -> %s %s" % (t, y, self._act(a), self.state())

    def select(self, t, row):
        a = self.m.select(t, row)
        return "select %d %d -> %s %s" % (t, row, self._act(a), self.state())

    def tick(self, t):
        self.m.tick(t)
        return "tick %d -> %s" % (t, self.state())

    def setsel(self, k):
        self.m.sel = k
        return "setsel %d -> %s" % (k, self.state())

    def window(self, sun, buzz, indoor):
        old = self.m.rows
        self.m.window(sun, buzz, indoor)
        return "window %d %d %d -> %d %s" % (sun, buzz, indoor, self.m.rows is not old, self.state())


def lines():
    yield ("# open <t>; close; next <t>; scroll <t> <d>; tap <t> <y>; select <t> <row>; tick <t>; setsel <row>; "
           "window <sun> <buzz> <indoor> -> <rows changed>; then the action (n: None) for next/tap/select, and: "
           "is_open sel top confirm_armed sub rows[0..3]")
    r = _Run()
    yield "menu_new -> " + r.state()
    yield r.window(False, 0, False)
    # walk down every row with short presses, then wrap to the top
    t = 1000
    yield r.open(t)
    for k in range(6):
        t += 400
        yield r.next(t)
        yield r.window(False, 0, False)
    # settings rows stay open; END ROUND asks first and a press confirms it
    t += 300
    yield r.select(t, M.SUN)
    yield r.window(True, 0, False)
    yield r.select(t + 10, M.BUZZ)
    yield r.window(True, 1, False)
    yield r.select(t + 20, M.PLACE)
    yield r.window(True, 1, True)
    for k in range(3):
        yield r.next(t + 100 + k)
    yield r.window(True, 1, True)
    yield r.select(t + 200, M.END)
    yield r.window(True, 1, True)
    yield r.next(t + 300)
    yield r.window(True, 1, True)
    # swipes past both ends; the selection follows into view; a swipe cancels the confirm
    t += 1000
    yield r.open(t)
    yield r.window(False, 2, False)
    yield r.select(t + 10, M.END)
    yield r.scroll(t + 20, 1)
    yield r.window(False, 2, False)
    for k, d in enumerate((1, 3, -1, -1, -5, 2, -2)):
        yield r.scroll(t + 100 + k, d)
        yield r.window(False, 2, False)
    # taps on every row edge, between rows, above and below the list, with the list scrolled
    t += 1000
    yield r.open(t)
    for y in (0, 31, 32, 50, 71, 72, 75, 76, 115, 116, 120, 159, 163, 164, 203, 204, 239):
        t += 10
        yield r.tap(t, y)
        if not r.m.is_open:
            yield r.open(t)
    yield r.scroll(t + 1, 1)
    yield r.tap(t + 2, T.MENU_ROWS_Y[3] + 5)      # END ROUND: arms
    yield r.window(False, 0, True)
    yield r.tap(t + 3, T.MENU_ROWS_Y[2] + 5)      # another row cancels
    yield r.window(False, 0, True)
    yield r.tap(t + 4, T.MENU_ROWS_Y[3] + 5)
    yield r.tap(t + 5, T.MENU_ROWS_Y[3] + 5)      # confirmed: END, closed
    # moving off an armed END ROUND cancels it; next on it (sel elsewhere) does not confirm
    t += 1000
    yield r.open(t)
    yield r.select(t + 1, M.END)
    yield r.setsel(M.PLACE)
    yield r.next(t + 2)
    yield r.window(True, 2, True)
    yield r.next(t + 3)
    yield r.next(t + 4)
    # the confirm times out; the menu auto-closes after the last input
    t += 1000
    yield r.open(t)
    yield r.next(t + 100)
    yield r.next(t + 200)
    yield r.next(t + 300)
    yield r.next(t + 400)
    yield r.select(t + 500, M.END)
    yield r.window(False, 0, False)
    yield r.tick(t + 500 + T.MENU_CONFIRM_MS - 1)
    yield r.window(False, 0, False)
    yield r.tick(t + 500 + T.MENU_CONFIRM_MS)
    yield r.window(False, 0, False)
    yield r.tick(t + 500 + T.MENU_AUTOCLOSE_MS - 1)
    yield r.tick(t + 500 + T.MENU_AUTOCLOSE_MS)
    # auto-close with an armed confirm; a closed menu's stamp is capped by tick
    t += 20000
    yield r.open(t)
    yield r.select(t + 10, M.END)
    yield r.tick(t + 10 + T.MENU_AUTOCLOSE_MS)
    yield r.tick(t + 60000)
    yield r.open(t + 60010)
    yield r.tick(t + 60010 + T.MENU_AUTOCLOSE_MS - 1)
    # RESUME closes; close() clears the confirm
    yield r.select(t + 60020, M.RESUME)
    yield r.open(t + 60030)
    yield r.select(t + 60040, M.END)
    yield r.close()
    # every label combination with the list at its bottom (all of them visible but RESUME)
    yield r.open(t + 70000)
    yield r.scroll(t + 70001, 1)
    for sun in (False, True):
        for buzz in (0, 1, 2):
            for indoor in (False, True):
                yield r.window(sun, buzz, indoor)
                yield r.window(sun, buzz, indoor)
