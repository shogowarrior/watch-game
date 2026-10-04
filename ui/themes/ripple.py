"""Ripple (ui-spec §4): today's field, as a theme. It draws exactly what the
plain Renderer draws (the field's own palette through the ring map), so a
ThemedRenderer on Ripple is pixel-identical to the Renderer.

Changed regions: every strip (rings cross all of them; a palette compare
per frame would cost more than it saves on the watch).
"""

from ui.themes.base import Theme


class Ripple(Theme):
    name = "ripple"

    def build(self, p, t, core, rim, vmax, lift):
        self.clock(p, t)
        self.f.build(t, core, rim, vmax, lift)
        self.everything()

    def blit(self, y0, buf, fb):
        f = self.f
        self.r.map.blit(y0, f.pal, f.pal_arr, buf, fb)
