"""Centre glyphs drawn into a strip FrameBuffer (ui-spec §2, §6; tokens.glyphs).

Every ``draw_*`` takes the strip FrameBuffer ``fb`` and its top row ``y0``
and uses absolute screen coordinates (y is made strip-relative here), so
framebuf clips to the strip. Thick strokes are filled polygons
(``framebuf.poly``) or ellipse pairs, never 1 px lines. Geometry is
precomputed at import; per-frame rotation writes into preallocated
``array('h')`` buffers with the integer Q14 sin/cos tables (no allocation).
"""

import array
import math

from finder import tuning as T
from ui import (ACC_COLD, ACC_FOUND, BG_BASE, BG_IRIS, GREY, LINE_SUBTLE, PROX,
                TEXT_PRI, WARN)
from ui.field import COS, SIN

CX, CY = T.CENTER

# ---- geometry helpers (import time; floats allowed) -------------------------
DART = T.DART_PTS
CHEVRON = T.CHEVRON_UP_PTS
CHECK = T.CHECK_PTS
TURN_HEAD = T.TURN_HEAD_PTS


def _area(pts):
    a = 0.0
    n = len(pts)
    for k in range(n):
        x0, y0 = pts[k]
        x1, y1 = pts[(k + 1) % n]
        a += x0 * y1 - x1 * y0
    return a / 2


def offset_poly(pts, d, miter_limit=3.0, bevel=False):
    """Mitred offset of a closed polygon; d > 0 grows it, d < 0 insets it.

    ``bevel``: a corner whose mitre would stick out more than ``miter_limit``
    x |d| (an acute corner pushed outward) is cut square with two vertices
    instead of a clamped spike.
    """
    n = len(pts)
    s = -1.0 if _area(pts) > 0 else 1.0     # y-down: area > 0 means clockwise
    out = []
    for k in range(n):
        px, py = pts[k]
        ax, ay = pts[k - 1]
        bx, by = pts[(k + 1) % n]
        e1x, e1y = px - ax, py - ay
        e2x, e2y = bx - px, by - py
        l1 = math.sqrt(e1x * e1x + e1y * e1y) or 1.0
        l2 = math.sqrt(e2x * e2x + e2y * e2y) or 1.0
        # outward normal of a clockwise (y-down) polygon edge (ex, ey) is (ey, -ex)
        n1x, n1y = -e1y / l1 * s, e1x / l1 * s
        n2x, n2y = -e2y / l2 * s, e2x / l2 * s
        dot = n1x * n2x + n1y * n2y
        m = d / (1.0 + dot) if dot > -0.99 else d * miter_limit
        mx, my = (n1x + n2x) * m, (n1y + n2y) * m
        ml = math.sqrt(mx * mx + my * my)
        lim = abs(d) * miter_limit
        # the mitre points outward (away from the polygon) at a corner that
        # turns toward the grow direction: convex when growing, reflex when
        # insetting
        turn = (e1x * e2y - e1y * e2x) * -s
        if bevel and ml > lim and turn * d > 0:
            out.append((px + n1x * d, py + n1y * d))
            out.append((px + n2x * d, py + n2y * d))
            continue
        if ml > lim:
            mx, my = mx * lim / ml, my * lim / ml
        out.append((px + mx, py + my))
    return out


def _arr(pts, scale=1):
    a = array.array("h", [0] * (2 * len(pts)))
    for k in range(len(pts)):
        a[2 * k] = int(round(pts[k][0] * scale))
        a[2 * k + 1] = int(round(pts[k][1] * scale))
    return a


def _rot180(pts):
    return [(-x, -y) for x, y in pts]


def thick_line(x0, y0, x1, y1, w):
    """Quad for a w px stroke from (x0,y0) to (x1,y1) (butt ends)."""
    dx, dy = x1 - x0, y1 - y0
    ln = math.sqrt(dx * dx + dy * dy) or 1.0
    nx, ny = -dy / ln * w / 2, dx / ln * w / 2
    return _arr(((x0 + nx, y0 + ny), (x1 + nx, y1 + ny), (x1 - nx, y1 - ny), (x0 - nx, y0 - ny)))


def arc_band(r_in, r_out, a0, a1, step=10):
    """Filled annular sector polygon (degrees clockwise from up)."""
    n = max(1, int(math.ceil(abs(a1 - a0) / step)))
    outer = []
    inner = []
    for k in range(n + 1):
        a = math.radians(a0 + (a1 - a0) * k / n)
        outer.append((r_out * math.sin(a), -r_out * math.cos(a)))
        inner.append((r_in * math.sin(a), -r_in * math.cos(a)))
    return outer + inner[::-1]


# Precomputed polygons: Q4 (x16) for the rotating dart, px arrays otherwise.
DART_Q4 = _arr(DART, 16)
DART_KEY_Q4 = _arr(offset_poly(DART, 2.0), 16)          # 2 px mitred keyline
DART_IN_Q4 = _arr(offset_poly(DART, -4.0), 16)          # outline tier inset
CHEV_UP = _arr(CHEVRON)
# colder: 4 px hollow band (+2 out, -2 in); the outer offset bevels the acute
# arm-end corners, so no mitre spikes stick out past the band
CHEV_DN_OUT = _arr(_rot180(offset_poly(CHEVRON, 2.0, 1.5, True)))
CHEV_DN_IN = _arr(_rot180(offset_poly(CHEVRON, -2.0)))
CHEV_STRONG_DY = T.CHEVRON_STACK_PX // 2   # warmer: copies at y 112 / 128 (+-8)
CHEV_DN_STRONG_DY = 13      # colder: +-13 (107 / 133), so the bands stay apart
CHECK_A = _arr(CHECK)
_TA = T.TURN_ARC            # (r, from_deg, to_deg, stroke)
_TURN = arc_band(_TA[0] - _TA[3] // 2, _TA[0] + _TA[3] // 2, _TA[1], _TA[2], 15)
TURN_R = _arr(_TURN)
TURN_R_HEAD = _arr(TURN_HEAD)
MARK_UP = _arr(((0, -6), (6, 5), (-6, 5)))
MARK_DN_OUT = _arr(((-6, -5), (6, -5), (0, 6)))
MARK_DN_IN = _arr(offset_poly(((-6, -5), (6, -5), (0, 6)), -2.0))
DIAMOND = _arr(((0, -3), (3, 0), (0, 3), (-3, 0)))

# rotating scratch buffers
_dart = array.array("h", [0] * len(DART_Q4))
_dart_key = array.array("h", [0] * len(DART_KEY_Q4))
_dart_in = array.array("h", [0] * len(DART_IN_Q4))
# beam sector: centre + n+1 arc points, n = 3..12 segments (<= 14 vertices)
BEAM_R = T.BEAM_R
_beam = [None, None, None] + [array.array("h", [0] * (2 * (n + 2))) for n in range(3, 13)]
_wedge = array.array("h", [0] * 16)
_wedge_key = array.array("h", [0] * 16)
_bin = array.array("h", [0] * 8)
_bin_in = array.array("h", [0] * 8)


def rot_into(dst, src, deg, scale):
    """Rotate Q4 points by integer deg (clockwise) and scale (Q8) -> px."""
    c = COS[deg % 360]
    s = SIN[deg % 360]
    k = 0
    n = len(src)
    while k < n:
        x = src[k]
        y = src[k + 1]
        dst[k] = ((((x * c - y * s) >> 14) * scale) + 2048) >> 12
        dst[k + 1] = ((((x * s + y * c) >> 14) * scale) + 2048) >> 12
        k += 2


def _polar(dst, k, r, deg):
    deg %= 360
    dst[k] = (r * SIN[deg] + 8192) >> 14
    dst[k + 1] = -((r * COS[deg] + 8192) >> 14)


# ---- arrow (dart + beam + keyline) ------------------------------------------
STYLES = {name: k + 1 for k, name in enumerate(T.ARROW_STYLES)}   # solid_a 1 .. outline 3
STYLE_OUT = STYLES["outline"]
DART_COL = (0, PROX[7], PROX[6], PROX[5])
BEAM_COL = (0, PROX[4], PROX[3], PROX[2])


def prep_arrow(deg, cone, scale):
    """Fill the dart/keyline/beam buffers; returns the beam array (or None)."""
    rot_into(_dart, DART_Q4, deg, scale)
    rot_into(_dart_key, DART_KEY_Q4, deg, scale)
    rot_into(_dart_in, DART_IN_Q4, deg, scale)
    if cone <= 0:
        return None
    if cone < 12:
        cone = 12
    elif cone > 60:
        cone = 60
    span = 2 * cone
    n = (span + 9) // 10
    if n < 3:
        n = 3
    b = _beam[n]
    b[0] = 0
    b[1] = 0
    a0 = deg - cone
    r = (BEAM_R * scale) >> 8
    for k in range(n + 1):
        _polar(b, 2 + 2 * k, r, a0 + (span * k) // n)
    return b


def draw_arrow(fb, y0, style, beam):
    y = CY - y0
    if beam is not None:
        fb.poly(CX, y, beam, BEAM_COL[style], True)
    fb.poly(CX, y, _dart_key, BG_BASE, True)
    fb.poly(CX, y, _dart, DART_COL[style], True)
    if style == STYLE_OUT:
        fb.poly(CX, y, _dart_in, BG_IRIS, True)


# ---- chevrons ----------------------------------------------------------------
def chevron_nudge(t):
    """0..6 px, 800 ms in_out_sine each way (1600 ms loop)."""
    deg = (t % (2 * T.CHEVRON_NUDGE_MS)) * 180 // T.CHEVRON_NUDGE_MS
    return (T.CHEVRON_NUDGE_PX * (16384 - COS[deg]) + 16384) >> 15


def draw_chevrons(fb, y0, trend, strong, nudge):
    """Warmer: filled up-chevron(s); colder: hollow down-chevron(s).

    Strong = two copies: warmer centred at y 112 / 128, colder at 107 / 133
    (the hollow band is 4 px wider than the filled shape, so 16 px apart the
    two bands would fuse into one filled blob). Each colder copy is finished
    (band, then interior) before the next is drawn.
    """
    if trend > 0:
        dy = CHEV_STRONG_DY if strong else 0
        a = CY - dy - nudge - y0
        fb.poly(CX, a, CHEV_UP, PROX[7], True)
        if strong:
            fb.poly(CX, a + 2 * dy, CHEV_UP, PROX[7], True)
    elif trend < 0:
        dy = CHEV_DN_STRONG_DY if strong else 0
        a = CY - dy + nudge - y0
        fb.poly(CX, a, CHEV_DN_OUT, ACC_COLD, True)
        fb.poly(CX, a, CHEV_DN_IN, BG_IRIS, True)
        if strong:
            a += 2 * dy
            fb.poly(CX, a, CHEV_DN_OUT, ACC_COLD, True)
            fb.poly(CX, a, CHEV_DN_IN, BG_IRIS, True)


# ---- seeker, check, turn, battery, dots ------------------------------------
_SR, _SS = T.SEEKER_RING                    # ring r 14, stroke 4
_SK = T.SEEKER_TICKS                        # ticks at 0/90/180/270 deg, r 20..28, stroke 4
_SK_LEN = _SK[2] - _SK[1]


def draw_seeker(fb, y0):
    """Seeker: ring r 14 plus 4 ticks r 20..28, stroke.m, grey.7 (§6 SEARCHING)."""
    c = GREY[7]
    w = _SK[3]
    y = CY - y0
    fb.ellipse(CX, y, _SR + _SS // 2, _SR + _SS // 2, c, True)
    fb.ellipse(CX, y, _SR - _SS // 2, _SR - _SS // 2, BG_IRIS, True)
    fb.fill_rect(CX - w // 2, y - _SK[2], w, _SK_LEN, c)
    fb.fill_rect(CX - w // 2, y + _SK[1], w, _SK_LEN, c)
    fb.fill_rect(CX - _SK[2], y - w // 2, _SK_LEN, w, c)
    fb.fill_rect(CX + _SK[1], y - w // 2, _SK_LEN, w, c)


def draw_check(fb, y0):
    y = CY - y0
    fb.ellipse(CX, y, T.CHECK_DISC_R, T.CHECK_DISC_R, ACC_FOUND, True)
    fb.poly(CX, y, CHECK_A, BG_BASE, True)


def draw_turn(fb, y0):
    """SCANNING sweep / result: the turn-right arc (the scan always turns right)."""
    y = CY - y0
    fb.poly(CX, y, TURN_R, PROX[6], True)
    fb.poly(CX, y, TURN_R_HEAD, PROX[6], True)


def rrect(fb, x, y, w, h, r, c):
    """Filled rounded rect (2-3 fill_rects + 4 ellipse quadrants)."""
    if r <= 0:
        fb.fill_rect(x, y, w, h, c)
        return
    fb.fill_rect(x + r, y, w - 2 * r, h, c)
    if h > 2 * r:
        fb.fill_rect(x, y + r, r, h - 2 * r, c)
        fb.fill_rect(x + w - r, y + r, r, h - 2 * r, c)
    x1 = x + w - 1 - r
    y1 = y + h - 1 - r
    fb.ellipse(x + r, y + r, r, r, c, True, 2)
    fb.ellipse(x1, y + r, r, r, c, True, 1)
    fb.ellipse(x + r, y1, r, r, c, True, 4)
    fb.ellipse(x1, y1, r, r, c, True, 8)


def draw_battery(fb, y0, pct):
    """10 % interstitial glyph: 64x32 rounded body, 3 px warn outline, nub."""
    y = 104 - y0
    rrect(fb, 88, y, 64, 32, 6, WARN)
    rrect(fb, 91, y + 3, 58, 26, 3, BG_IRIS)
    fb.fill_rect(152, 114 - y0, 6, 12, WARN)
    if pct is not None and pct > 0:
        w = (54 * (pct if pct < 100 else 100)) // 100
        if w > 0:
            fb.fill_rect(93, y + 5, w, 22, WARN)


def draw_dots(fb, y0):
    """PAIRING looking: 3 line.subtle dots r 5 at the rune centres."""
    for x in RUNE_X:
        fb.ellipse(x, CY - y0, 5, 5, LINE_SUBTLE, True)


# ---- runes (tokens.glyphs.runes) -------------------------------------------
RUNE_STROKE = T.RUNE_STROKE
RUNE_X = T.RUNE_CENTERS_X
R_POLY = 0
R_DISC = 1
R_RING = 2
R_CUT = 3


def _rune_ops(prims):
    ops = []
    h = RUNE_STROKE / 2

    def seg(a, b):
        ops.append((R_POLY, thick_line(a[0], a[1], b[0], b[1], RUNE_STROKE), 0, 0))
        ops.append((R_DISC, int(round(a[0])), int(round(a[1])), int(h)))
        ops.append((R_DISC, int(round(b[0])), int(round(b[1])), int(h)))

    for p in prims:
        kind = p[0]
        if kind == "ring":
            ops.append((R_RING, p[1], p[2], p[3]))
        elif kind == "disc":
            ops.append((R_DISC, p[1], p[2], p[3]))
        elif kind == "cut_disc":
            ops.append((R_CUT, p[1], p[2], p[3]))
        elif kind == "line":
            seg(p[1], p[2])
        elif kind in ("closed", "open"):
            pts = p[1]
            n = len(pts)
            for k in range(n if kind == "closed" else n - 1):
                seg(pts[k], pts[(k + 1) % n])
    return tuple(ops)


RUNES = tuple(_rune_ops(prims) for _, prims in T.RUNES)


def draw_rune(fb, y0, rid, cx, c):
    y = CY - y0
    for op in RUNES[rid & 7]:
        k = op[0]
        if k == R_POLY:
            fb.poly(cx, y, op[1], c, True)
        elif k == R_DISC:
            fb.ellipse(cx + op[1], y + op[2], op[3], op[3], c, True)
        elif k == R_RING:
            r = op[3]
            fb.ellipse(cx + op[1], y + op[2], r + 3, r + 3, c, True)
            fb.ellipse(cx + op[1], y + op[2], r - 3, r - 3, BG_IRIS, True)
        else:
            fb.ellipse(cx + op[1], y + op[2], op[3], op[3], BG_IRIS, True)


# ---- sweep wedge, bins, pacer ----------------------------------------------
SW_R0 = T.SWEEP_R_INNER
SW_R1 = T.SWEEP_R_OUTER
# keyline arc angles: 2 px beside a radial side is +1.0 deg at r 112, +1.7 at r 68
KEY_OUT = (-16, -5, 5, 16)
KEY_IN = (-17, -6, 6, 17)


def prep_wedge(deg):
    """30 deg sector r 70..110 centred on deg (clockwise from up), plus its
    2 px ``bg.base`` keyline (r 68..112, sides pushed out 2 px)."""
    w = _wedge
    kw = _wedge_key
    for k in range(4):
        _polar(w, 2 * k, SW_R1, deg - 15 + 10 * k)
        _polar(w, 14 - 2 * k, SW_R0, deg - 15 + 10 * k)
        _polar(kw, 2 * k, SW_R1 + 2, deg + KEY_OUT[k])
        _polar(kw, 14 - 2 * k, SW_R0 - 2, deg + KEY_IN[k])


def draw_wedge(fb, y0, c):
    """Keyline first, so the wedge never merges with a halo or crest of the
    same level behind it (the DIRECTION-TURN pacer sits on the live halo)."""
    y = CY - y0
    fb.poly(CX, y, _wedge_key, BG_BASE, True)
    fb.poly(CX, y, _wedge, c, True)


def wedge_hits_bottom(deg):
    """True while the wedge (with keyline) at ``deg`` reaches the bottom slot
    (y >= 186): centre within 70 deg of 6 o'clock."""
    deg %= 360
    return 110 < deg < 250


# Bin boxes: k*30 deg, 6 deg wide, r 70..110. Each sits in a 2 px bg.iris
# track (r 68..112, +-4 deg) so a short bar reads as a bar against the live
# halo behind it, not as a dark notch in it. Strip culling rows per bin:
def _bin_rows():
    y0 = []
    y1 = []
    for k in range(12):
        ys = []
        for r in (SW_R0 - 2, SW_R1 + 2):
            for a in (k * 30 - 4, k * 30 + 4):
                ys.append(CY - r * math.cos(math.radians(a)))
        y0.append(int(math.floor(min(ys))) - 1)
        y1.append(int(math.ceil(max(ys))) + 2)
    return bytes(y0), bytes(y1)


BIN_Y0, BIN_Y1 = _bin_rows()


def _bin_box(b, a, r0, r1, h):
    _polar(b, 0, r1, a - h)
    _polar(b, 2, r1, a + h)
    _polar(b, 4, r0, a + h)
    _polar(b, 6, r0, a - h)


def draw_bin(fb, y0, k, norm_q8, c, track=BG_IRIS, full=True):
    """Radial bar k (6 deg wide at k*30 deg), r 70 -> 70 + 40*norm, in its
    dark track (``full=False``: only a 2 px keyline round the bar, for the
    active bin drawn over the wedge, so the wedge stays lit past the bar)."""
    a = k * 30
    y = CY - y0
    b = _bin
    r1 = SW_R0 + (((SW_R1 - SW_R0) * norm_q8) >> 8)
    if r1 < SW_R0 + 3:
        r1 = SW_R0 + 3
    _bin_box(b, a, SW_R0 - 2, SW_R1 + 2 if full else r1 + 2, 4)
    fb.poly(CX, y, b, track, True)
    _bin_box(b, a, SW_R0, r1, 3)
    fb.poly(CX, y, b, c, True)


def draw_bin_hollow(fb, y0, k, c):
    """No-data bin: 2 px outline of the full 70..110 box, in its track."""
    a = k * 30
    y = CY - y0
    b = _bin
    _bin_box(b, a, SW_R0 - 2, SW_R1 + 2, 4)
    fb.poly(CX, y, b, BG_IRIS, True)
    _bin_box(b, a, SW_R0, SW_R1, 3)
    fb.poly(CX, y, b, c, False)
    b = _bin_in
    _bin_box(b, a, SW_R0 + 1, SW_R1 - 1, 2)
    fb.poly(CX, y, b, c, False)


# ---- small marks (readout / LAST chip) -------------------------------------
def draw_mark(fb, y0, x, y, trend, up_col, dn_col, bg):
    """12 px trend mark centred at (x, y): filled up / 2 px hollow down."""
    if trend > 0:
        fb.poly(x, y - y0, MARK_UP, up_col, True)
    elif trend < 0:
        fb.poly(x, y - y0, MARK_DN_OUT, dn_col, True)
        fb.poly(x, y - y0, MARK_DN_IN, bg, True)


def draw_diamond(fb, y0, x, y, c):
    fb.poly(x, y - y0, DIAMOND, c, True)


# rune colour helper for PAIRING
RUNE_COL = TEXT_PRI
RUNE_OK = PROX[7]
RUNE_WAIT = LINE_SUBTLE
