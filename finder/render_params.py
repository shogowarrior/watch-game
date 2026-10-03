"""RenderParams: the one object the logic hands the renderer each frame.

Contract from docs/design/ui-spec.md §3. The watch renderer and the web
simulator read only this. It is JSON-serialisable (``to_dict``/``from_dict``)
for JSONL logs and replay.

MicroPython's namedtuple has no ``_fields``/``_replace``/``_asdict``; use
``FIELDS``, ``replace`` and ``to_dict`` instead.
"""

from collections import namedtuple

from finder import tuning as T

FIELDS = (
    # screen
    "t_ms", "screen", "sub", "zone",
    # ripple field
    "ramp", "intensity", "speed_px_s", "pulse_period_ms", "wavelength_px", "glow_r_px",
    "ring_live", "burst",
    # centre glyph
    "glyph", "arrow_deg", "cone_deg", "arrow_style", "trend", "trend_strong", "countdown",
    "runes",
    # text slots
    "dist_band", "dist_stale", "word", "top_text", "banner", "status", "menu_rows",
    # scanning
    "sweep",
    # output devices
    "haptic", "heartbeat", "heartbeat_every", "backlight", "sun", "fps_cap",
)

RenderParams = namedtuple("RenderParams", FIELDS)

_FS = T.FIELD_SEARCHING
# Defaults form a valid SEARCHING frame.
DEFAULTS = {
    "t_ms": 0, "screen": "SEARCHING", "sub": None, "zone": None,
    "ramp": _FS[0], "intensity": _FS[1], "speed_px_s": _FS[2], "pulse_period_ms": _FS[3],
    "wavelength_px": None, "glow_r_px": _FS[4], "ring_live": False, "burst": False,
    "glyph": "seeker", "arrow_deg": None, "cone_deg": None, "arrow_style": None,
    "trend": 0, "trend_strong": False, "countdown": None, "runes": None,
    "dist_band": None, "dist_stale": False, "word": None, "top_text": None, "banner": None,
    "status": (100, None, 0, False, False), "menu_rows": None,
    "sweep": None,
    "haptic": None, "heartbeat": None, "heartbeat_every": 1,
    "backlight": T.BACKLIGHT_NORMAL, "sun": False, "fps_cap": T.FPS_TARGET,
}

ZONE_SCREENS = T.ZONE_NAMES                 # FAR NEAR WARM HOT, index = zone
_NO_ARROW_SCREENS = ("SEARCHING", "SCANNING", "LINK_LOST", "FOUND", "PAIRING")
_NO_BAND_SCREENS = ("PAIRING", "SEARCHING", "SCANNING", "FOUND")
_TREND_SCREENS = ("FAR", "NEAR", "WARM", "LINK_LOST")   # LINK_LOST: the LAST chip's mark
_CHEVRON_SCREENS = ("FAR", "NEAR", "WARM")
_RAMP_FOR = {"FOUND": "gold", "SEARCHING": "grey", "LINK_LOST": "grey",
             "PAIRING": "green", "SCANNING": "green",
             "FAR": "green", "NEAR": "green", "WARM": "green", "HOT": "green"}
_MENU_VISIBLE = len(T.MENU_ROWS_Y)          # rows on screen


def wavelength(speed_px_s, pulse_period_ms):
    """Ring spacing in px: |speed| * period / 1000."""
    return abs(speed_px_s) * pulse_period_ms / 1000.0


def arrow_style(cone_deg):
    """Style tier for a cone half-angle, or None when hidden (> 60 deg)."""
    if cone_deg is None:
        return None
    i = 0
    for lim in T.ARROW_TIER_MAX_DEG:
        if cone_deg <= lim:
            return T.ARROW_STYLES[i]
        i += 1
    return None


def make_params(**kw):
    """RenderParams from keyword args; missing fields take ``DEFAULTS``.

    ``wavelength_px`` defaults to ``wavelength(speed, period)``.
    """
    for k in kw:
        if k not in DEFAULTS:
            raise TypeError("unknown RenderParams field: %s" % k)
    d = dict(DEFAULTS)
    d.update(kw)
    if d["wavelength_px"] is None:
        d["wavelength_px"] = wavelength(d["speed_px_s"], d["pulse_period_ms"])
    return RenderParams(**d)


def replace(rp, **kw):
    """Copy of ``rp`` with some fields changed (namedtuple._replace substitute)."""
    d = to_dict(rp, False)
    for k in kw:
        if k not in d:
            raise TypeError("unknown RenderParams field: %s" % k)
    d.update(kw)
    return RenderParams(**d)


def _jsonable(v):
    if isinstance(v, tuple):
        return [_jsonable(x) for x in v]
    return v


def to_dict(rp, json_ready=True):
    """Field dict; with ``json_ready`` tuples become lists (JSON arrays)."""
    d = {}
    i = 0
    for k in FIELDS:
        v = rp[i]
        d[k] = _jsonable(v) if json_ready else v
        i += 1
    return d


def _tup(v):
    if isinstance(v, list):
        return tuple(_tup(x) for x in v)
    return v


def from_dict(d, strict=True):
    """Inverse of ``to_dict``: lists -> tuples, missing keys -> defaults.

    Unknown keys raise ``ValueError`` when ``strict``, else are ignored.
    """
    kw = {}
    for k in d:
        if k in DEFAULTS:
            kw[k] = _tup(d[k])
        elif strict:
            raise ValueError("unknown RenderParams field: %s" % k)
    return make_params(**kw)


# ---- validation (ui-spec §3 table + field comments) -------------------------

def _num(v):
    return (isinstance(v, (int, float))) and not isinstance(v, bool)


def _int(v):
    return isinstance(v, int) and not isinstance(v, bool)


def _in(v, lo, hi):
    return _num(v) and lo <= v <= hi


def _text(out, name, s, maxlen, chars):
    if s is None:
        return
    if not isinstance(s, str):
        out.append("%s: not a str" % name)
        return
    if len(s) > maxlen:
        out.append("%s: %d chars > %d" % (name, len(s), maxlen))
    if s != s.upper():
        out.append("%s: not uppercase" % name)
    for ch in s:
        if ch not in chars:
            out.append("%s: glyph %r not in font subset" % (name, ch))
            break


def _valid_sub(screen, sub):
    if screen == "PAIRING":
        return sub in T.SUBS_PAIRING
    if screen == "SCANNING":
        return sub in T.SUBS_SCANNING
    if screen in ZONE_SCREENS:
        return sub is None or sub in T.SUBS_DIRECTION
    if screen == "FOUND":
        return sub in T.SUBS_FOUND
    if screen == "MENU":
        # visible row index, then optional scroll marks: "^" rows above, "v" rows below
        return (isinstance(sub, str) and 1 <= len(sub) <= 3 and "0" <= sub[0] <= "3"
                and sub[1:] in ("", "^", "v", "^v"))
    return sub is None  # SEARCHING, LINK_LOST


def validate(rp):
    """Check the §3 validity rules; returns a list of violation strings."""
    out = []
    e = out.append
    if len(rp) != len(FIELDS):
        return ["RenderParams: expected %d fields" % len(FIELDS)]
    sc = rp.screen
    sub = rp.sub

    # screen
    if not _int(rp.t_ms) or rp.t_ms < 0:
        e("t_ms: must be int >= 0")
    if sc not in T.SCREENS:
        e("screen: unknown %r" % (sc,))
    elif not _valid_sub(sc, sub):
        e("sub: %r not valid for %s" % (sub, sc))
    z = rp.zone
    if z is not None and (not _int(z) or not 0 <= z <= 3):
        e("zone: must be None or int 0..3")
        z = None
    if sc in ZONE_SCREENS:
        if z != ZONE_SCREENS.index(sc):
            e("zone: %r does not match screen %s" % (rp.zone, sc))
    elif sc == "LINK_LOST" and z is None:
        e("zone: LINK_LOST needs the last known zone")

    # ripple field
    if rp.ramp not in T.RAMP_NAMES:
        e("ramp: unknown %r" % (rp.ramp,))
    elif sc in _RAMP_FOR and rp.ramp != _RAMP_FOR[sc]:
        e("ramp: %s must be %s" % (sc, _RAMP_FOR[sc]))
    if not _in(rp.intensity, 0.0, 1.0):
        e("intensity: must be 0..1")
    spd, per, wl = rp.speed_px_s, rp.pulse_period_ms, rp.wavelength_px
    ok = True
    if not _in(spd, T.SPEED_MIN_PX_S, T.SPEED_MAX_PX_S):
        e("speed_px_s: must be %g..%g" % (T.SPEED_MIN_PX_S, T.SPEED_MAX_PX_S))
        ok = False
    if not _int(per) or not T.PERIOD_MIN_MS <= per <= T.PERIOD_MAX_MS:
        e("pulse_period_ms: must be int %d..%d" % (T.PERIOD_MIN_MS, T.PERIOD_MAX_MS))
        ok = False
    if not _in(wl, 0.0, T.WAVELENGTH_MAX_PX):
        e("wavelength_px: must be 0..%g" % T.WAVELENGTH_MAX_PX)
    elif ok and abs(wl - wavelength(spd, per)) > T.WAVELENGTH_TOL_PX:
        e("wavelength_px: %g != |speed|*period/1000 = %g" % (wl, wavelength(spd, per)))
    if ok and sc in ZONE_SCREENS and z is not None:
        if spd != T.ZONE_SPEED_PX_S[z] or per != T.ZONE_PERIOD_MS[z]:
            e("speed_px_s/pulse_period_ms: not the %s zone tempo" % sc)
    if ok and sc in ("SEARCHING", "LINK_LOST") and spd > 0:
        e("speed_px_s: %s rings must be inward (<= 0)" % sc)
    if not _in(rp.glow_r_px, 0.0, T.GLOW_R_MAX_PX):
        e("glow_r_px: must be 0..%g" % T.GLOW_R_MAX_PX)
    if not isinstance(rp.ring_live, bool):
        e("ring_live: must be bool")
    if not isinstance(rp.burst, bool):
        e("burst: must be bool")

    # centre glyph
    g = rp.glyph
    if g not in T.GLYPHS:
        e("glyph: unknown %r" % (g,))
    ad, cd, st = rp.arrow_deg, rp.cone_deg, rp.arrow_style
    if (ad is None) != (cd is None) or (ad is None) != (st is None):
        e("arrow_deg/cone_deg/arrow_style: must be None together")
    if ad is not None:
        if not _in(ad, 0.0, 360.0):
            e("arrow_deg: must be 0..360")
        if not _in(cd, T.CONE_DRAW_MIN_DEG, T.ARROW_HIDE_SIGMA_DEG):
            e("cone_deg: must be %g..%g" % (T.CONE_DRAW_MIN_DEG, T.ARROW_HIDE_SIGMA_DEG))
        elif st is not None and st != arrow_style(cd):
            e("arrow_style: %r does not match cone %g (%r)" % (st, cd, arrow_style(cd)))
        if st is not None and st not in T.ARROW_STYLES:
            e("arrow_style: unknown %r" % (st,))
        if sc in _NO_ARROW_SCREENS:
            e("arrow_deg: not allowed in %s" % sc)
        if sub in ("ready", "sweep"):
            e("arrow_deg: not allowed while sub is %s" % sub)
        if g != "arrow":
            e("glyph: must be 'arrow' while an arrow is set")
    elif g == "arrow":
        e("glyph: 'arrow' needs arrow_deg/cone_deg/arrow_style")
    if g == "chevrons" and sc not in _CHEVRON_SCREENS:
        e("glyph: 'chevrons' only in FAR/NEAR/WARM")
    tr = rp.trend
    if tr not in (-1, 0, 1) or isinstance(tr, bool):
        e("trend: must be -1, 0 or +1")
    elif tr != 0 and sc not in _TREND_SCREENS:
        e("trend: must be 0 in %s" % sc)
    if not isinstance(rp.trend_strong, bool):
        e("trend_strong: must be bool")
    elif rp.trend_strong and tr == 0:
        e("trend_strong: needs a non-zero trend")
    elif rp.trend_strong and sc == "LINK_LOST":
        e("trend_strong: must be False in LINK_LOST")
    cn = rp.countdown
    if cn is not None and (not _int(cn) or not 0 <= cn <= T.COUNTDOWN_MAX):
        e("countdown: must be None or int 0..%d" % T.COUNTDOWN_MAX)
    if (g == "countdown") != (cn is not None):
        e("countdown: set exactly when glyph is 'countdown'")
    rn = rp.runes
    if rn is not None:
        ok = isinstance(rn, tuple) and len(rn) == 3
        if ok:
            for r in rn:
                if not _int(r) or not 0 <= r <= 7:
                    ok = False
        if not ok:
            e("runes: must be None or 3 rune ids 0..7")
        elif sc != "PAIRING":
            e("runes: only in PAIRING")
    elif g == "runes" and sub in ("seen", "confirmed"):
        e("runes: glyph 'runes' needs them in %s" % sub)

    # text slots
    band = rp.dist_band
    if band is not None:
        if band not in T.BAND_LABELS:
            e("dist_band: %r is not a band label" % (band,))
        elif sc in _NO_BAND_SCREENS:
            e("dist_band: must be None in %s" % sc)
        elif sc in ZONE_SCREENS and z is not None:
            lo, hi = T.ZONE_BANDS[z]
            if not lo <= T.BAND_LABELS.index(band) <= hi:
                e("dist_band: %s contradicts zone %s" % (band, sc))
    if not isinstance(rp.dist_stale, bool):
        e("dist_stale: must be bool")
    elif rp.dist_stale and sc != "LINK_LOST":
        e("dist_stale: only in LINK_LOST")
    _text(out, "word", rp.word, T.WORD_MAX_CHARS, T.WORD_CHARS)
    _text(out, "top_text", rp.top_text, T.LABEL_MAX_CHARS, T.LABEL_CHARS)
    if sc == "SCANNING" and sub == "sweep" and (rp.word is not None or rp.top_text is not None):
        e("word/top_text: both slots are suppressed during the sweep")
    if sub == "turn" and rp.top_text is not None:
        e("top_text: suppressed during the turn pacer")
    bn = rp.banner
    if bn is not None:
        if not isinstance(bn, tuple) or len(bn) != 3:
            e("banner: must be (text, severity, sticky)")
        else:
            _text(out, "banner", bn[0], T.LABEL_MAX_CHARS, T.LABEL_CHARS)
            if bn[0] is None:
                e("banner: text missing")
            if bn[1] not in T.BANNER_SEVERITIES:
                e("banner: severity %r" % (bn[1],))
            if not isinstance(bn[2], bool):
                e("banner: sticky must be bool")
    s = rp.status
    if not isinstance(s, tuple) or len(s) != 5:
        e("status: must be (own_pct, partner_pct|None, link_q, visible, unreliable)")
    else:
        if not _int(s[0]) or not 0 <= s[0] <= 100:
            e("status: own_pct must be int 0..100")
        if s[1] is not None and (not _int(s[1]) or not 0 <= s[1] <= 100):
            e("status: partner_pct must be None or int 0..100")
        if not _int(s[2]) or not 0 <= s[2] <= T.LINK_Q_MAX:
            e("status: link_q must be int 0..%d" % T.LINK_Q_MAX)
        if not isinstance(s[3], bool):
            e("status: visible must be bool")
        if not isinstance(s[4], bool):
            e("status: unreliable must be bool")
        elif s[4] and not s[3]:
            e("status: unreliable pins the strip (visible)")
    mr = rp.menu_rows
    if (sc == "MENU") != (mr is not None):
        e("menu_rows: set exactly when screen is MENU")
    if mr is not None:
        if not isinstance(mr, tuple) or len(mr) != _MENU_VISIBLE:
            e("menu_rows: must be a tuple of %d rows" % _MENU_VISIBLE)
        else:
            for row in mr:
                if row is None:
                    e("menu_rows: row text missing")
                _text(out, "menu_rows", row, T.LABEL_MAX_CHARS, T.LABEL_CHARS)

    # scanning
    sw = rp.sweep
    if sw is not None:
        if sc != "SCANNING" and sub != "turn":
            e("sweep: only in SCANNING or the DIRECTION turn")
        if not isinstance(sw, tuple) or len(sw) != 4:
            e("sweep: must be (wedge_deg, bins, active_bin, paused)")
        else:
            if not _in(sw[0], 0.0, 360.0):
                e("sweep: wedge_deg must be 0..360")
            bins = sw[1]
            if not isinstance(bins, tuple) or len(bins) != T.SCAN_BINS:
                e("sweep: bins must be a tuple of %d" % T.SCAN_BINS)
            else:
                for x in bins:
                    if x is not None and not _in(x, 0.0, 1.0):
                        e("sweep: bin values must be None or 0..1")
                        break
            if sw[2] is not None and (not _int(sw[2]) or not 0 <= sw[2] < T.SCAN_BINS):
                e("sweep: active_bin must be None or 0..%d" % (T.SCAN_BINS - 1))
            if not isinstance(sw[3], bool):
                e("sweep: paused must be bool")

    # output devices
    if rp.haptic is not None and rp.haptic not in T.HAPTIC_PATTERNS:
        e("haptic: unknown pattern %r" % (rp.haptic,))
    hb, ev = rp.heartbeat, rp.heartbeat_every
    if hb is not None and hb not in T.HEARTBEATS:
        e("heartbeat: must be None, TICK or DOUBLE")
    if not _int(ev) or not 1 <= ev <= 2:
        e("heartbeat_every: must be int 1..2")
    elif hb is not None and sc in ZONE_SCREENS and z is not None:
        if hb != T.ZONE_HEARTBEAT[z] or ev != T.ZONE_HB_EVERY[z]:
            e("heartbeat: not the %s zone heartbeat" % sc)
    if not _in(rp.backlight, 0.0, 1.0):
        e("backlight: must be 0..1")
    if not isinstance(rp.sun, bool):
        e("sun: must be bool")
    if not _int(rp.fps_cap) or not T.FPS_CAP_MIN <= rp.fps_cap <= T.FPS_CAP_MAX:
        e("fps_cap: must be int %d..%d" % (T.FPS_CAP_MIN, T.FPS_CAP_MAX))
    return out
