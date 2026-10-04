"""MENU overlay state (ui-spec MENU): a 6-row list, 4 rows visible, that scrolls.

The Game owns one ``Menu``. It opens and closes it, passes input on
(``next`` / ``scroll`` / ``tap`` / ``select``) and applies the row action
those return (``SUN``, ``BUZZ``, ``PLACE``, ``THEME``, ``END`` once confirmed;
``RESUME`` only closes). END ROUND asks first: the first select arms ``SURE? PRESS`` for
``MENU_CONFIRM_MS``; any other row, a swipe or moving off the row cancels it.
The menu closes itself ``MENU_AUTOCLOSE_MS`` after the last input.

``window`` rebuilds the row labels and the visible ``rows`` at tick time only,
so the renderer's rows and ``sub`` (built in the same tick) always describe the
same window. Pure logic, allocation-free except a new ``rows`` tuple when a
visible label changed.
"""

from finder.compat import ticks_add, ticks_diff
from finder import tuning as T

ROWS = ("RESUME", "SUN: OFF", "BUZZ: FULL", "PLACE: OUT", "THEME: RIPPLE", "END ROUND")
RESUME, SUN, BUZZ, PLACE, THEME, END = 0, 1, 2, 3, 4, 5   # row index = the action select returns
VISIBLE = len(T.MENU_ROWS_Y)                       # rows on screen
TOP_MAX = len(ROWS) - VISIBLE
CONFIRM = "SURE? PRESS"
BUZZ_ROWS = ("BUZZ: FULL", "BUZZ: EVENTS", "BUZZ: OFF")   # index = finder.game BUZZ_*
THEME_ROWS = {n: "THEME: " + T.THEME_LABELS[n] for n in T.THEME_NAMES}   # ui-spec §4A Choosing


class Menu:
    """Open flag, selected row ``sel``, first visible row ``top``, the END ROUND
    confirm and the auto-close timer."""

    def __init__(self):
        self._labels = list(ROWS)
        self.rows = ROWS[:VISIBLE]      # visible window (rebuilt by ``window``)
        self.reset(0)

    def reset(self, t_ms):
        self.is_open = False
        self.sel = 0
        self.top = 0
        self._t = t_ms                  # last input (auto-close)
        self._confirm_t = None          # END ROUND armed at

    def open(self, t_ms):
        self.reset(t_ms)
        self.is_open = True

    def close(self):
        self.is_open = False
        self._confirm_t = None

    # ---- input --------------------------------------------------------------------
    def next(self, t_ms):
        """Short press: confirms an armed END ROUND on its row, else selects the
        next row (moving off END ROUND cancels SURE? PRESS). Returns an action."""
        if self._confirm_t is not None and self.sel == END:
            return self.select(t_ms, END)
        self._confirm_t = None
        self.sel = (self.sel + 1) % len(ROWS)
        self._t = t_ms
        if self.sel < self.top:
            self.top = self.sel
        elif self.sel >= self.top + VISIBLE:
            self.top = self.sel - VISIBLE + 1
        return None

    def scroll(self, t_ms, d):
        """Swipe: show ``d`` rows further down (negative: up); the selection
        follows into view. Cancels a pending confirm (SURE? PRESS may scroll away)."""
        self._t = t_ms
        self._confirm_t = None
        top = self.top + d
        self.top = top = 0 if top < 0 else TOP_MAX if top > TOP_MAX else top
        if self.sel < top:
            self.sel = top
        elif self.sel >= top + VISIBLE:
            self.sel = top + VISIBLE - 1

    def tap(self, t_ms, y):
        """Tap at screen ``y``: selects the visible row there; returns its action."""
        i = 0
        for y0 in T.MENU_ROWS_Y:
            if y0 <= y < y0 + T.MENU_ROW_H:
                self.sel = self.top + i
                return self.select(t_ms, self.sel)
            i += 1
        return None

    def select(self, t_ms, row):
        """Long press (or tap) on ``row``; returns the action to apply or None.
        RESUME and a confirmed END ROUND close the menu."""
        self._t = t_ms
        if row != END:
            self._confirm_t = None
        elif self._confirm_t is None:
            self._confirm_t = t_ms      # asks first: SURE? PRESS
            return None
        if row == RESUME or row == END:
            self.close()
        return row

    # ---- per tick -----------------------------------------------------------------
    def tick(self, t_ms):
        """Auto-close and the confirm timeout; caps the input stamp's age."""
        if ticks_diff(t_ms, self._t) > T.MENU_AUTOCLOSE_MS:
            self._t = ticks_add(t_ms, -T.MENU_AUTOCLOSE_MS)   # never a wrapped age
        if not self.is_open:
            return
        if ticks_diff(t_ms, self._t) >= T.MENU_AUTOCLOSE_MS:
            self.close()
        elif self._confirm_t is not None and ticks_diff(t_ms, self._confirm_t) >= T.MENU_CONFIRM_MS:
            self._confirm_t = None

    def window(self, sun, buzz, indoor, theme=T.THEME_DEFAULT):
        """Row labels for these settings (``theme``: a name in
        tuning.THEME_NAMES), and the visible ``rows`` tuple (a new tuple only
        when a visible label changed)."""
        r = self._labels
        r[SUN] = "SUN: ON" if sun else "SUN: OFF"
        r[BUZZ] = BUZZ_ROWS[buzz]
        r[PLACE] = "PLACE: IN" if indoor else "PLACE: OUT"
        r[THEME] = THEME_ROWS[theme]
        r[END] = CONFIRM if self._confirm_t is not None else ROWS[END]
        top = self.top
        v = self.rows
        for k in range(VISIBLE):
            if v[k] != r[top + k]:
                self.rows = tuple(r[top:top + VISIBLE])
                return

    @property
    def sub(self):
        """RenderParams.sub for MENU: the visible index of ``sel``, then '^'/'v'
        when rows are hidden above/below."""
        s = str(self.sel - self.top)
        if self.top > 0:
            s += "^"
        if self.top < TOP_MAX:
            s += "v"
        return s
