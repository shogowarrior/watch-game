"""Themes (ui-spec §4A): six looks for the field, one game.

    from ui.themes import ThemedRenderer, NAMES
    r = ThemedRenderer("tide")        # a Renderer whose field a theme draws
    r.set_theme("warp")               # switch at any time (the next frame shows it whole)
    beats = r.frame(params, display, now)

``ThemedRenderer`` is the Renderer (ui/renderer.py) with two methods taken
over: ``_palette`` asks the theme to build its frame instead of rebuilding
the ripple palette, and ``frame`` lets the theme blit each band (and draw
its own layer) where the renderer blits the ring map. Everything else, the
field's state, ring schedule, heartbeats, the plan and every overlay, is the
renderer's own. Ripple through it is pixel-identical to the plain Renderer
(tests/test_themes.py). Not yet used by the game: the MENU row, the saved
choice and ``RenderParams.theme`` come with the renderer hook (ui-spec §4A
Status).

``overlays=False`` draws the field layer only (tests, previews).
"""

from finder import tuning as T
from finder.compat import ticks_ms
from ui.renderer import (BH, CORE_V, G_GLOW, NB, PROX, S_FAR, S_HOT, S_SCANNING, SAVER_VMAX,
                         SUN_LIFT, V7, WARN, WARN_TOP, Renderer)

NAMES = T.THEME_NAMES


def make(name, r):
    """The theme object called ``name`` (ui-spec §4A) drawing for renderer
    ``r``; an unknown name gives Ripple. Modules load on first use."""
    if name == "sonar":
        from ui.themes.sonar import Sonar as C
    elif name == "tide":
        from ui.themes.tide import Tide as C
    elif name == "warp":
        from ui.themes.warp import Warp as C
    elif name == "arcade":
        from ui.themes.arcade import Arcade as C
    elif name == "fireflies":
        from ui.themes.fireflies import Fireflies as C
    else:
        from ui.themes.ripple import Ripple as C
    return C(r)


class ThemedRenderer(Renderer):
    """Renderer whose field is drawn by a theme (see the module docstring)."""

    def __init__(self, theme=None, overlays=True):
        self.theme = None
        self.overlays = overlays
        Renderer.__init__(self)
        self.set_theme(theme or T.THEME_DEFAULT)

    def set_theme(self, name):
        """Switch theme; the next drawn frame is the new theme, whole."""
        if self.theme is not None and self.theme.name == name:
            return
        self.theme = None               # let the old one's buffers go first
        self.theme = make(name, self)

    def reset(self):
        Renderer.reset(self)
        if self.theme is not None:
            self.theme.reset()

    def _palette(self, p, t):
        # Renderer._palette's arguments, handed to the theme
        scr = self._scr
        sub = self._sub
        rim = -1
        if scr == S_SCANNING and (sub == "ready" or sub is None):
            flat = p.top_text not in WARN_TOP and not (p.sweep is not None and p.sweep[3])
            rim = PROX[6] if flat else WARN
        core = CORE_V if (self.g == G_GLOW and S_FAR <= scr <= S_HOT) else 0
        self.theme.build(p, t, core, rim, SAVER_VMAX if self._saver else V7,
                         SUN_LIFT if p.sun else 0)

    def frame(self, p, display=None, now=None):
        """Renderer.frame with the theme drawing the field (same clock,
        events and band pushes)."""
        t = ticks_ms() if now is None else now
        ev = self._step(p, t)
        if display is None:
            self._dark = True
            return ev
        if self._dark:
            self._dark = False
            self._snap(p, t)
        self._plan(p, t)
        self._palette(p, t)
        th = self.theme
        bands = self.bands
        fbs = self.band_fbs
        if display is not self._disp:
            self._disp = display
            self._service = getattr(display, "service", None)
        svc = self._service
        for k in range(NB):
            th.blit(k * BH, bands[k], fbs[k])
            if svc is not None:
                svc()
        if th.layer:
            th.draw(self.fb)
        if self.overlays:
            self._strip(p, t, 0, self.fb)
        if svc is not None:
            svc()
        for k in range(NB):
            display.push_strip(k * BH, BH, bands[k])
        return ev
