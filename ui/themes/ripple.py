"""Ripple (ui-spec §4): today's field, as a theme. It draws exactly what the
plain Renderer draws (the field's own palette through the ring map), so a
ThemedRenderer on Ripple is pixel-identical to the Renderer, and nearly as
cheap: it keeps no clock of its own (the field's is the whole state).

Changed regions: while rings travel, every strip, every frame (they cross
all of them; a palette compare would cost more than it saves). In the MENU,
where the field is frozen, and on still screens (speed 0: PAIRING seen,
confirmed and calibrate, FOUND) the frame is the static ring map through the
palette, so the palette entries that changed since the last drawn frame (a
compare of at most 170 entries) name the rings that changed, and with them
the strips: strip k holds ring indices RMIN[k]..RMAX[k]. A frozen MENU
reports nothing once its dim has settled.
"""

import array

from ui.field import N_IDX
from ui.themes.base import ALL, NS, S_MENU, SH, W, Theme


class Ripple(Theme):
    name = "ripple"

    def __init__(self, r):
        Theme.__init__(self, r)
        self.everything()
        self._prev = array.array("H", [0] * N_IDX)   # the palette last drawn
        self._kept = False              # _prev holds the last drawn frame's palette
        idx = r.map.idx                 # each strip's nearest and farthest ring index
        lo = bytearray(NS)
        hi = bytearray(NS)
        for k in range(NS):
            a = 255
            b = 0
            for y in range(k * SH, k * SH + SH):
                o = y * W
                if idx[o + 120] < a:
                    a = idx[o + 120]
                if idx[o] > b:
                    b = idx[o]
            lo[k] = a
            hi[k] = b
        self.rmin = lo
        self.rmax = hi

    def build(self, p, t, core, rim, vmax, lift):
        f = self.f
        f.build(t, core, rim, vmax, lift)
        self.clock(p, t)
        if self.wake or not (self.r._scr == S_MENU or p.speed_px_s == 0):
            self._kept = False
            if self.dirty != ALL:
                self.everything()
            return
        pal = f.pal_arr
        prev = self._prev
        if not self._kept:              # no record of the last frame: everything
            for i in range(N_IDX):
                prev[i] = pal[i]
            self._kept = True
            if self.dirty != ALL:
                self.everything()
            return
        a = 0
        while a < N_IDX and pal[a] == prev[a]:
            a += 1
        if a == N_IDX:
            if self.dirty:
                self.clear_dirty()
            return
        b = N_IDX - 1
        while pal[b] == prev[b]:
            b -= 1
        for i in range(a, b + 1):
            prev[i] = pal[i]
        lo = self.rmin
        hi = self.rmax
        sp = self.spans
        d = 0
        for k in range(NS):
            if a <= hi[k] and b >= lo[k]:
                d |= 1 << k
                sp[2 * k] = 0
                sp[2 * k + 1] = W - 1
            else:
                sp[2 * k] = 255
                sp[2 * k + 1] = 0
        self.dirty = d

    def blit(self, y0, buf, fb):
        f = self.f
        self.r.map.blit(y0, f.pal, f.pal_arr, buf, fb)
