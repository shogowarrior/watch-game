"""Themes (ui-spec §4A): six looks for the field, one game.

    from ui.themes import ThemedRenderer, NAMES
    r = ThemedRenderer("tide")        # a Renderer whose field a theme draws
    r.set_theme("warp")               # switch now (the next frame shows it whole)
    r.queue_theme("sonar")            # switch in steps while the game runs
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

It also remembers the moment and the RenderParams of the last frame drawn
outside the MENU (``m_live``, ``p_live``), so a theme keeps, or a theme made
in the MENU starts in, the moment of the screen under it (§4A rule 2; the
MENU's own params carry the speed, ramp and band of no moment).

Loading a theme (compiling its module and kernels, running their
self-checks, building its maps) takes 10-30 ms on a 32-bit desktop
MicroPython, which is seconds on the watch, where the game loop runs under
an 8 s hardware watchdog fed once per pass (AGENTS.md hard rule 15) and
also sends beacons and services the motor. So a theme loads in steps
(``stages``): the module import, the module's optional ``load()`` generator
(one-time work: kernel compiles and self-checks), the theme's ``__init__``
(allocations) and its ``prepare()`` generator (map builds), each step
bounded (base.py "Loading"). ``set_theme`` runs every step at once (boot,
before the watchdog starts; tests; previews); ``queue_theme`` runs one step
per ``frame`` call, after the frame is pushed, while the current theme
keeps drawing, and swaps when the last step is done.

``overlays=False`` draws the field layer only (tests). On CPython (no
framebuf) ``ThemedRenderer`` is None; ``NAMES``, ``make`` and the theme
modules still import, for their maths.
"""

from finder import tuning as T
from finder.compat import ticks_ms
from ui.themes.base import moment_of

try:
    from ui.renderer import (BH, CORE_V, G_GLOW, NB, PROX, S_FAR, S_HOT, S_MENU, S_SCANNING,
                             SAVER_VMAX, SUN_LIFT, V7, WARN, WARN_TOP, Renderer)
except ImportError:  # CPython: no framebuf, so no renderer; NAMES and make() still work
    Renderer = None

NAMES = T.THEME_NAMES


def known(name):
    """``name`` if it is a theme, else the default (Ripple)."""
    return name if name in NAMES else T.THEME_DEFAULT


def _module(name):
    """The theme module and class for ``name`` (unknown: Ripple)."""
    if name == "sonar":
        import ui.themes.sonar as m
        return m, m.Sonar
    if name == "tide":
        import ui.themes.tide as m
        return m, m.Tide
    if name == "warp":
        import ui.themes.warp as m
        return m, m.Warp
    if name == "arcade":
        import ui.themes.arcade as m
        return m, m.Arcade
    if name == "fireflies":
        import ui.themes.fireflies as m
        return m, m.Fireflies
    import ui.themes.ripple as m
    return m, m.Ripple


def stages(name, r, out):
    """Load theme ``name`` for renderer ``r``, one bounded step per
    iteration (a generator); when it ends, ``out[0]`` is the theme."""
    m, cls = _module(name)
    yield
    load = getattr(m, "load", None)
    if load is not None:
        for _ in load():
            yield
    th = cls(r)
    yield
    for _ in th.prepare():
        yield
    out[0] = th


def make(name, r):
    """The theme object called ``name`` (ui-spec §4A) drawing for renderer
    ``r``, loaded whole; an unknown name gives Ripple."""
    out = [None]
    for _ in stages(known(name), r, out):
        pass
    return out[0]


class _ThemedRenderer(Renderer or object):
    """Renderer whose field is drawn by a theme (see the module docstring)."""

    def __init__(self, theme=None, overlays=True):
        self.theme = None
        self.loading = None             # name of the theme queue_theme is loading
        self._load = None
        self._out = [None]
        self.overlays = overlays
        self.m_live = -1                # moment of the last frame drawn outside the MENU
        self.p_live = None              # and its RenderParams
        Renderer.__init__(self)
        self.set_theme(theme)

    def set_theme(self, name):
        """Switch now, loading the theme whole; the next drawn frame is the
        new theme, whole. Cancels a queued switch. Unknown: Ripple."""
        name = known(name)
        self._load = None
        self.loading = None
        if self.theme is not None and self.theme.name == name:
            return
        self.theme = None               # let the old one's buffers go first
        self.theme = make(name, self)

    def queue_theme(self, name):
        """Switch in steps: each ``frame`` call (drawn or not) runs one load
        step while the current theme keeps drawing; the frame after the last
        step is the new theme, whole. A later call replaces a queued one;
        cheap to call every frame with the same name. Unknown: Ripple."""
        name = known(name)
        if name == self.loading:
            return
        self._out[0] = None
        if self.theme is not None and self.theme.name == name:
            self._load = None
            self.loading = None
            return
        self.loading = name
        self._load = stages(name, self, self._out)

    def _load_step(self):
        try:
            next(self._load)
        except StopIteration:
            self._load = None
            self.loading = None
            self.theme = self._out[0]
            self._out[0] = None

    def reset(self):
        Renderer.reset(self)
        self.m_live = -1
        self.p_live = None
        if self.theme is not None:
            self.theme.reset()

    def _palette(self, p, t):
        # Renderer._palette's arguments, handed to the theme
        scr = self._scr
        sub = self._sub
        if scr != S_MENU:
            self.m_live = moment_of(self, p)
            self.p_live = p
        rim = -1
        if scr == S_SCANNING and (sub == "ready" or sub is None):
            flat = p.top_text not in WARN_TOP and not (p.sweep is not None and p.sweep[3])
            rim = PROX[6] if flat else WARN
        core = CORE_V if (self.g == G_GLOW and S_FAR <= scr <= S_HOT) else 0
        self.theme.build(p, t, core, rim, SAVER_VMAX if self._saver else V7,
                         SUN_LIFT if p.sun else 0)

    def frame(self, p, display=None, now=None):
        """Renderer.frame with the theme drawing the field (same clock,
        events and band pushes), then one step of a queued theme load."""
        t = ticks_ms() if now is None else now
        ev = self._step(p, t)
        if display is None:
            self._dark = True
            if self.theme is not None:
                self.theme.started = False      # the next drawn frame is a wake
            if self._load is not None:
                self._load_step()
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
            if svc is not None:
                svc()
        if self.overlays:
            self._strip(p, t, 0, self.fb)
        if svc is not None:
            svc()
        for k in range(NB):
            display.push_strip(k * BH, BH, bands[k])
        if self._load is not None:
            if svc is not None:
                svc()
            self._load_step()
        return ev


ThemedRenderer = _ThemedRenderer if Renderer is not None else None
