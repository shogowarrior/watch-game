"""Themes (ui-spec §4A): six looks for the field, one game.

    from ui.themes import ThemedRenderer, NAMES
    r = ThemedRenderer("tide", follow=True)  # loads Tide whole (boot: before the watchdog)
    beats = r.frame(params, display, now)    # follows params.theme, in steps

    r = ThemedRenderer("warp")        # previews, tests: the theme is switched
    r.set_theme("sonar")              # here, now (the next frame shows it whole) ...
    r.queue_theme("arcade")           # ... or in steps, one per frame call

``ThemedRenderer`` is the renderer the watch (app/runtime.py) and the web
simulator (sim/webhost.py) draw with: the Renderer (ui/renderer.py) with
``_palette`` taken over (the theme builds its frame instead of the ripple
palette) and ``_field`` (the theme blits each band, and draws its own layer,
where the renderer blits the ring map). Everything else, the field's state,
ring schedule, heartbeats, the plan and every overlay, is the renderer's
own. Ripple through it is pixel-identical to the plain Renderer
(tests/test_themes.py). With ``follow`` (the game's renderers) each
``frame`` call reads ``RenderParams.theme`` (ui-spec §3; the MENU THEME row
sets it) and queues that theme when it differs from the last one asked for;
without it (previews, tests) only ``set_theme`` and ``queue_theme`` switch.

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

    def __init__(self, theme=None, overlays=True, follow=False):
        self.theme = None
        self.loading = None             # name of the theme queue_theme is loading
        self._load = None
        self._out = [None]
        self._want = None               # the theme last asked for (params, set or queue)
        self.overlays = overlays
        self.follow = follow            # frame() follows RenderParams.theme
        self.m_live = -1                # moment of the last frame drawn outside the MENU
        self.p_live = None              # and its RenderParams
        Renderer.__init__(self)
        self.set_theme(theme)

    def set_theme(self, name):
        """Switch now, loading the theme whole; the next drawn frame is the
        new theme, whole. Cancels a queued switch. Unknown: Ripple."""
        name = known(name)
        self._want = name
        self._load = None
        self.loading = None
        self._out[0] = None
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
        self._want = name
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

    def _snap(self, p, t):
        Renderer._snap(self, p, t)
        self.theme.started = False      # the first drawn frame after a dark spell: a wake

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

    def _field(self, svc):
        th = self.theme
        bands = self.bands
        fbs = self.band_fbs
        for k in range(NB):
            th.blit(k * BH, bands[k], fbs[k])
            if svc is not None:
                svc()
        if th.layer:
            th.draw(self.fb)
            if svc is not None:
                svc()

    def _strip(self, p, t, y0, fb, h=240):     # h: Renderer._strip default (W)
        if self.overlays:
            Renderer._strip(self, p, t, y0, fb, h)

    def frame(self, p, display=None, now=None):
        """Renderer.frame with the theme drawing the field (same clock,
        events and band pushes), then one step of a queued theme load. With
        ``follow``, a ``p.theme`` other than the last theme asked for is
        queued first."""
        if self.follow:
            w = p.theme
            if w != self._want:
                self.queue_theme(w)
                self._want = w
        ev = Renderer.frame(self, p, display, now)
        if self._load is not None:
            if display is not None and self._service is not None:
                self._service()
            self._load_step()
        return ev


ThemedRenderer = _ThemedRenderer if Renderer is not None else None
