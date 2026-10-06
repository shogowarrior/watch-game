"""finder/render_params.py: FIELDS, the enum spellings, DEFAULTS through
make_params (and its wavelength rule), wavelength, arrow_style, and validate
on valid frames and on a case for every rule the C++ types can break.

A field token is ``name=value`` (see ``tok``): None is ``n``, bools 0/1, a
space in text ``_``, tuple items joined by ``|`` (sweep bins by ``/``).

    make <field tokens given to make_params> -> <all 36 field tokens>
    validate <field tokens that differ from DEFAULTS, wavelength_px always> -> <count>
    error <message>          (one line per violation, in order, after its validate line)
"""

from finder import howto as HT
from finder import render_params as RP
from finder import tuning as T

FLOATS = ("intensity", "speed_px_s", "wavelength_px", "glow_r_px", "arrow_deg", "cone_deg", "backlight")
SUBS = (T.SUBS_PAIRING + T.SUBS_SCANNING + T.SUBS_DIRECTION + ("celebrate",)
        + tuple("%d%s" % (k, m) for k in range(4) for m in ("", "^", "v", "^v")))


def _flt(v):
    return "n" if v is None else repr(float(v))


def _int(v):
    return "n" if v is None else "%d" % v


def _txt(v):
    if v is None:
        return "n"
    assert isinstance(v, str) and "_" not in v and len(v) <= 31 and v not in ("n", ""), v
    assert all(" " <= c <= "~" for c in v), v
    return v.replace(" ", "_")


def _bool(v):
    assert isinstance(v, bool), v
    return "1" if v else "0"


def val(k, v):
    """One field as the C++ dump prints it (floats compared by value, not text)."""
    if k in FLOATS:
        return _flt(v)
    if k in ("ring_live", "burst", "trend_strong", "dist_stale", "sun"):
        return _bool(v)
    if k in ("t_ms", "zone", "pulse_period_ms", "trend", "countdown", "bump_icons", "heartbeat_every", "fps_cap"):
        assert v is None or (isinstance(v, int) and not isinstance(v, bool)), (k, v)
        return _int(v)
    if k in ("word", "top_text"):
        return _txt(v)
    if k == "runes":
        return "n" if v is None else "|".join(_int(x) for x in v)
    if k == "banner":
        return "n" if v is None else "%s|%s|%s" % (_txt(v[0]), v[1], _bool(v[2]))
    if k == "status":
        return "%d|%s|%d|%s|%s" % (v[0], _int(v[1]), v[2], _bool(v[3]), _bool(v[4]))
    if k == "menu_rows":
        return "n" if v is None else "|".join(_txt(x) for x in v)
    if k == "sweep":
        if v is None:
            return "n"
        return "%s|%s|%s|%s" % (_flt(v[0]), "/".join(_flt(x) for x in v[1]), _int(v[2]), _bool(v[3]))
    assert v is None or isinstance(v, str), (k, v)   # screen sub ramp glyph arrow_style dist_band haptic heartbeat
    return "n" if v is None else v


def representable(rp):
    """True when the C++ RenderParams can hold every field of rp as it is."""
    d = RP.to_dict(rp, False)
    ok = (d["screen"] in T.SCREENS and (d["sub"] is None or d["sub"] in SUBS) and d["ramp"] in T.RAMP_NAMES
          and (d["glyph"] is None or d["glyph"] in T.GLYPHS)
          and (d["arrow_style"] is None or d["arrow_style"] in T.ARROW_STYLES)
          and (d["dist_band"] is None or d["dist_band"] in T.BAND_LABELS)
          and (d["haptic"] is None or d["haptic"] in T.HAPTIC_NAMES)
          and (d["heartbeat"] is None or d["heartbeat"] in T.HAPTIC_NAMES)
          and d["t_ms"] >= 0 and len(d["status"]) == 5
          and (d["runes"] is None or len(d["runes"]) <= 3)
          and (d["menu_rows"] is None or len(d["menu_rows"]) <= RP._MENU_VISIBLE))
    bn, sw = d["banner"], d["sweep"]
    ok = ok and (bn is None or (len(bn) == 3 and bn[1] in T.BANNER_SEVERITIES))
    ok = ok and (sw is None or (len(sw) == 4 and len(sw[1]) <= T.SCAN_BINS))
    for k in RP.FIELDS:
        val(k, d[k])                     # asserts the types
    return ok


def toks(rp, only_changed=False):
    d = RP.to_dict(rp, False)
    out = []
    for k in RP.FIELDS:
        if only_changed and k != "wavelength_px" and d[k] == RP.DEFAULTS[k] and type(d[k]) is type(RP.DEFAULTS[k]):
            continue
        out.append("%s=%s" % (k, val(k, d[k])))
    return " ".join(out)


def kw_toks(kw):
    return " ".join("%s=%s" % (k, val(k, kw[k])) for k in RP.FIELDS if k in kw)


def zone_frame(z, **kw):
    d = dict(screen=T.ZONE_NAMES[z], zone=z, ramp="green", intensity=0.5,
             speed_px_s=T.ZONE_SPEED_PX_S[z], pulse_period_ms=T.ZONE_PERIOD_MS[z],
             glow_r_px=40.0, ring_live=True, glyph="glow",
             dist_band=T.BAND_LABELS[T.ZONE_BANDS[z][0]],
             heartbeat=T.ZONE_HEARTBEAT[z], heartbeat_every=T.ZONE_HB_EVERY[z])
    d.update(kw)
    return RP.make_params(**d)


MAKES = (
    {},
    dict(t_ms=812340, screen="WARM", sub="walk", zone=2, ramp="green", intensity=0.55, speed_px_s=80.0,
         pulse_period_ms=1000, glow_r_px=42.0, ring_live=True, glyph="arrow", arrow_deg=0.0, cone_deg=31.0,
         arrow_style="solid_b", trend=1, dist_band="~10", status=(64, 71, 4, False, False), heartbeat="DOUBLE"),
    dict(speed_px_s=56.0, pulse_period_ms=1600),
    dict(speed_px_s=-36.7, pulse_period_ms=3333),
    dict(speed_px_s=0.1, pulse_period_ms=777, wavelength_px=12.5),
    dict(screen="SCANNING", sub="sweep", zone=2, intensity=0.3, speed_px_s=0.0, pulse_period_ms=1000,
         glow_r_px=12.0, glyph="turn", banner=None, status=(50, None, 3, False, False),
         sweep=(90.0, (0.1, None, 0.5, 1.0, None, 0.0, 0.2, 0.3, None, 0.9, 0.4, 0.6), 3, False)),
    dict(screen="PAIRING", sub="seen", ramp="green", speed_px_s=0.0, glyph="runes", runes=(0, 7, 3),
         top_text="SAME RUNES?", word="TAP = YES", haptic="DOUBLE", backlight=1.0, sun=True, fps_cap=10),
    dict(screen="MENU", sub="2^v", ramp="grey", menu_rows=("RESUME", "SUN: ON", "BUZZ: EVENTS", "PLACE: IN"),
         banner=("LOST 0:12", "warn", True), countdown=7, dist_stale=True, trend=-1, trend_strong=True,
         burst=True, heartbeat_every=2, t_ms=123456789),
    dict(screen="FOUND", sub="celebrate", ramp="gold", intensity=1.0, speed_px_s=0.0, pulse_period_ms=1200,
         glow_r_px=90.0, burst=True, glyph="check", word="FOUND", top_text="TIME 12:48", haptic="FOUND",
         banner=(None, "critical", False), menu_rows=("A", None), runes=(1,)),
    dict(screen="SEARCHING", sub="turn", sweep=(330.0, (None,) * 12, None, True), glyph="arrow",
         arrow_deg=300.0, cone_deg=59.9, arrow_style="outline", status=(80, 80, 3, True, True)),
    dict(screen="HOT", zone=3, glyph="bump", bump_icons=5, word="BUMP!", dist_band="<3"),
    dict(screen="MENU", sub="3^v", ramp="grey", theme="fireflies",
         menu_rows=("SUN: OFF", "BUZZ: FULL", "PLACE: OUT", "THEME: FIREFLIES")),
)

WAVELENGTHS = ((-36.0, 3200), (40.0, 2400), (56.0, 1600), (80.0, 1000), (120.0, 500), (0.0, 1000),
               (-30.0, 3000), (-60.0, 400), (240.0, 4000), (33.3, 1234), (-0.7, 999), (17.25, 401),
               (-0.7, 3999), (56.7, 401), (0.3, 2999), (123.4, 777))   # the last 4: (s*p)/1000 != s*(p/1000)
CONES = (None, 0.0, 12.0, 24.999, 25.0, 25.000001, 25.1, 31.0, 44.9, 45.0, 45.1, 45.5, 59.99, 60.0,
         60.000001, 60.1, 60.5, 90.0, -5.0)


def validate_cases():
    base = zone_frame(2)
    ll = T.FIELD_LINK_LOST
    lost = RP.make_params(screen="LINK_LOST", zone=1, ramp=ll[0], intensity=0.3,
                          speed_px_s=ll[2], pulse_period_ms=ll[3], glow_r_px=ll[4],
                          glyph="seeker", dist_band="~20", dist_stale=True)
    pl = T.FIELD_PAIRING_LOOKING
    pair = dict(screen="PAIRING", ramp=pl[0], intensity=pl[1], speed_px_s=pl[2], pulse_period_ms=pl[3],
                glow_r_px=pl[4])
    arrow = dict(glyph="arrow", arrow_deg=0.0, cone_deg=20.0, arrow_style="solid_a")
    menu = dict(screen="MENU", sub="0", zone=None, dist_band=None, heartbeat=None)
    R = RP.replace
    out = [RP.make_params()]
    # valid frames (tests/test_render_params.py)
    for z in range(4):
        out.append(zone_frame(z))
        out.append(zone_frame(z, glyph="arrow", arrow_deg=350.0, cone_deg=20.0, arrow_style="solid_a", sub="walk"))
    for z in range(3):
        out.append(zone_frame(z, glyph="chevrons", trend=-1, trend_strong=True))
    out += [
        RP.make_params(**dict(MAKES[1], wavelength_px=80.0)),
        RP.make_params(screen="LINK_LOST", zone=1, ramp=ll[0], intensity=0.3, speed_px_s=ll[2],
                       pulse_period_ms=ll[3], glow_r_px=ll[4], glyph="seeker", dist_band="~20",
                       dist_stale=True, top_text="LAST ~20M", banner=("LOST 0:12", "warn", True)),
        RP.make_params(sub="looking", glyph="glow", top_text="PAIR", word="LOOKING", **pair),
        RP.make_params(screen="PAIRING", sub="seen", ramp="green", speed_px_s=0.0, glyph="runes",
                       runes=(0, 7, 3), top_text="SAME RUNES?", word="TAP = YES"),
        RP.make_params(screen="PAIRING", sub="split", ramp="green", glyph="countdown", countdown=30,
                       speed_px_s=40.0, pulse_period_ms=2400, top_text="NO PEEKING", word="SPLIT UP"),
        RP.make_params(screen="FOUND", sub="celebrate", ramp="gold", intensity=1.0, speed_px_s=0.0,
                       pulse_period_ms=1200, glow_r_px=90.0, burst=True, glyph="check", word="FOUND",
                       top_text="TIME 12:48", haptic="FOUND"),
        RP.make_params(screen="MENU", sub="2", ramp="grey",
                       menu_rows=("RESUME", "SUN: ON", "BUZZ: EVENTS", "PLACE: IN")),
        RP.make_params(screen="MENU", sub="2^", ramp="grey", theme="fireflies",
                       menu_rows=("BUZZ: FULL", "PLACE: OUT", "THEME: FIREFLIES", "END ROUND")),
        zone_frame(1, status=(80, 80, 3, True, True), sun=True),
        RP.make_params(**MAKES[5]),
        R(lost, trend=1),
        zone_frame(2, sub="turn", glyph="arrow", arrow_deg=30.0, cone_deg=20.0, arrow_style="solid_a",
                   sweep=(30.0, (None,) * 12, None, False)),
    ]
    for s in SUBS[-16:]:
        out.append(RP.make_params(screen="MENU", sub=s, ramp="green", menu_rows=("A", "B", "C", "D")))
    for w in ("LOOKING", "TAP = YES", "BUMP!", "TAP=AGAIN", "4 O'CLOCK", "12 O'CLOCK", "SAVER ON"):
        out.append(zone_frame(0, word=w))
    for s in ("STAND 1 STEP APART", "WRONG WAY? RESCAN", "FRIEND BATT 20%", "LAST ~20M", "A-B/C,D<E>F+G"):
        out.append(zone_frame(0, top_text=s))
    for s in ("NO FIX, TRY AGAIN", "LOST 0:27 GO BACK", "BATTERY 5%"):
        out.append(zone_frame(0, banner=(s, "info", False)))
    look = dict(LOOK, sub="looking", glyph="runes", top_text="START OTHER WATCH", word="LOOKING")
    out += [card(k) for k in (1, 2, 3, 4)]
    out.append(RP.make_params(**dict(look, banner=("SWIPE: HOW TO PLAY", "info", False))))
    hot = dict(glyph="bump", dist_band="<3")
    out += [zone_frame(3, bump_icons=v, **hot) for v in range(RP.BUMP_ICONS_MAX + 1)]
    out += [zone_frame(3, bump_icons=0, word="BUMP!", top_text="BUMP WRISTS", **hot),
            zone_frame(3, bump_icons=1, word="BUMP!", **hot)]
    # violations (tests/test_render_params.py, the representable ones, plus every other branch)
    out += [
        R(base, sub="sweep"), R(base, screen="MENU", sub="walk"), R(base, screen="SEARCHING", sub="reveal"),
        R(base, screen="PAIRING", sub=None), R(base, screen="FOUND", sub="walk"),
        R(base, screen="SCANNING", sub="turn"), R(base, screen="SCANNING", sub="1v"),
        R(base, zone=1), R(base, zone=7), R(base, zone=-1), R(base, zone=None), R(lost, zone=None),
        R(base, ramp="gold"), R(base, screen="MENU", ramp="gold"),
        R(base, intensity=1.5), R(base, intensity=-0.1),
        R(base, wavelength_px=50.0), R(base, wavelength_px=150.0), R(base, wavelength_px=None),
        R(base, wavelength_px=80.9), R(base, wavelength_px=81.25),
        R(base, speed_px_s=300.0), R(base, speed_px_s=56.0, wavelength_px=56.0),
        R(base, pulse_period_ms=100), R(base, pulse_period_ms=1600, wavelength_px=128.0),
        R(base, speed_px_s=-61.0, pulse_period_ms=5000),
        RP.make_params(screen="SEARCHING", speed_px_s=36.0), R(lost, speed_px_s=10.0, wavelength_px=30.0),
        R(base, glow_r_px=120.0), R(base, glow_r_px=-1.0),
        R(base, glyph=None),
        R(base, glyph="arrow", arrow_deg=10.0),
        R(base, glyph="arrow", cone_deg=10.0),
        R(base, glyph="arrow", arrow_style="outline"),
        R(base, glyph="arrow", arrow_deg=400.0, cone_deg=20.0, arrow_style="solid_a"),
        R(base, glyph="arrow", arrow_deg=10.0, cone_deg=70.0, arrow_style="outline"),
        R(base, glyph="arrow", arrow_deg=10.0, cone_deg=50.0, arrow_style="solid_a"),
        R(base, glyph="arrow", arrow_deg=10.0, cone_deg=30.5, arrow_style="outline"),
        R(base, arrow_deg=10.0, cone_deg=20.0, arrow_style="solid_a"),
        R(base, glyph="arrow"),
        R(base, sub="walk", **dict(arrow, arrow_deg=360.0, cone_deg=60.0, arrow_style="outline")),
    ]
    for sc in ("SEARCHING", "LINK_LOST", "FOUND", "PAIRING"):
        out.append(R(RP.make_params(), screen=sc, **arrow))
    out += [
        RP.make_params(screen="SCANNING", sub="ready", zone=2, ramp="green", **arrow),
        R(base, sub="sweep", **arrow),
        R(_hot(), glyph="chevrons"), R(lost, glyph="chevrons", trend=-1),
        R(base, trend=2), R(base, trend=-3, trend_strong=True), R(_hot(), trend=1),
        R(RP.make_params(), trend=-1), R(base, trend_strong=True), R(lost, trend=1, trend_strong=True),
        R(base, glyph="countdown", countdown=120), R(base, glyph="countdown"), R(base, countdown=3),
        R(base, countdown=-2, glyph="countdown"),
        R(base, runes=(0, 3, 6)), R(base, runes=(0, 3)), R(base, runes=()),
        R(base, screen="PAIRING", sub="seen", zone=None, glyph="runes", dist_band=None, heartbeat=None,
          runes=(0, 3, 8)),
        R(base, screen="PAIRING", sub="seen", zone=None, glyph="runes", dist_band=None, heartbeat=None),
        R(base, screen="PAIRING", sub="confirmed", zone=None, glyph="runes", dist_band=None, heartbeat=None,
          runes=(1, -1, 2)),
        R(base, screen="SEARCHING", sub=None, zone=None, ramp="grey", speed_px_s=-36.0, pulse_period_ms=3200),
        R(RP.make_params(), dist_band="~10"), R(base, dist_band="~40"), R(base, dist_band="<3"),
        R(base, dist_stale=True),
        R(base, word="SEARCHING.."), R(base, word="walk"), R(base, word="WALK?"), R(base, word="Hello, World"),
        R(base, top_text="THIS HINT IS FAR TOO LONG"), R(base, top_text="IT'S"), R(base, top_text="A\\B"),
        R(base, top_text="TAB\tX".replace("\t", "~") + "#"),
        RP.make_params(screen="SCANNING", sub="sweep", zone=1, ramp="green", glyph="turn", word="TURN RIGHT",
                       speed_px_s=0.0),
        RP.make_params(screen="SCANNING", sub="sweep", ramp="green", top_text="HOLD FLAT", speed_px_s=0.0),
        R(base, sub="turn", glyph="arrow", arrow_deg=30.0, cone_deg=20.0, arrow_style="solid_a",
          sweep=(30.0, (None,) * 12, None, False), top_text="TAP TO SCAN"),
        R(base, banner=("LOST", "warn", True)), R(base, banner=("LOST, FRIEND LOW BAT", "warn", True)),
        R(base, banner=(None, "info", False)), R(base, banner=("low", "critical", False)),
        R(base, status=(64, 71, 5, False, False)), R(base, status=(150, 71, 4, False, False)),
        R(base, status=(-1, 120, -1, False, True)), R(base, status=(64, -3, 4, True, True)),
        R(base, status=(64, 71, 4, False, True)),
        R(base, menu_rows=("RESUME", "SUN: OFF", "BUZZ: FULL", "END ROUND")),
        R(base, **menu), R(base, menu_rows=("RESUME", "SUN: OFF"), **menu),
        R(base, menu_rows=("RESUME", "sun", "BUZZ", "END"), **menu),
        R(base, menu_rows=("RESUME", None, "BUZZ", "END ROUND IS TOO LONG"), **menu),
        R(base, sweep=(0.0, (0.0,) * 12, 0, False)),
        R(base, screen="SCANNING", sub="result", sweep=(400.0, (0.0,) * 11, 12, False)),
        R(base, screen="SCANNING", sub="sweep", sweep=(-1.0, (0.5, None, 1.5, -0.5) + (0.0,) * 8, None, True)),
        R(base, screen="SCANNING", sub="sweep", sweep=(359.9, (None,) * 12, -1, True)),
        R(base, heartbeat="TICK"), R(base, heartbeat="FOUND"), R(base, heartbeat_every=3),
        R(base, heartbeat_every=2), R(base, heartbeat=None, heartbeat_every=0), R(_hot(), heartbeat="DOUBLE"),
        R(base, backlight=2.0), R(base, backlight=-0.5), R(base, fps_cap=30), R(base, fps_cap=4),
        R(base, theme="sheen"), R(base, theme=None),   # the C++ holds either as no theme
        R(base, haptic="LOST"),
        # howto cards and the bump view (tests/test_render_params.py)
        RP.make_params(**dict(look, glyph="chevrons")), RP.make_params(**dict(look, ring_live=True)),
        RP.make_params(**dict(look, speed_px_s=40.0, pulse_period_ms=2400, wavelength_px=96.0)),
        RP.make_params(**dict(LOOK, sub="split", glyph="countdown", countdown=20, trend=1)),
        card(1, ring_live=True), card(1, speed_px_s=40.0, pulse_period_ms=2400, wavelength_px=96.0),
        card(1, glyph="check", runes=None), card(3, trend_strong=True), card(3, trend=-1),
        card(1, runes=None), card(2, word=None), card(1, top_text=None), card(4, bump_icons=None),
        card(2, glyph="seeker", countdown=None), card(3, trend=1, glyph="countdown", countdown=5),
        card(4, bump_icons=4, word="BUMP!", ring_live=True),
        zone_frame(3, bump_icons=6, **hot), zone_frame(3, bump_icons=7, **hot),
        zone_frame(3, bump_icons=-1, **hot), zone_frame(3, glyph="bump", dist_band="<3"),
        zone_frame(3, bump_icons=0, dist_band="<3"), zone_frame(2, bump_icons=0, glyph="bump"),
        zone_frame(3, bump_icons=0, sub="walk", **hot), zone_frame(3, bump_icons=4, word="BUMP!", **hot),
        RP.make_params(screen="SEARCHING", glyph="bump", bump_icons=4, word="BUMP!", sub="turn"),
        # many at once
        RP.make_params(screen="FAR", sub="sweep", zone=5, ramp="gold", intensity=2.0, speed_px_s=400.0,
                       pulse_period_ms=50, glow_r_px=200.0, glyph="chevrons", arrow_deg=500.0, trend=4,
                       trend_strong=True, countdown=100, runes=(9, 9, 9), dist_band="60+", dist_stale=True,
                       word="much too long", top_text="also far far too long", banner=(None, "warn", True),
                       status=(101, 101, 9, False, True), menu_rows=("x",), sweep=(-5.0, (2.0,), 20, False),
                       heartbeat="NOPE", heartbeat_every=9, backlight=1.5, fps_cap=1),
    ]
    return out


def _hot():
    return zone_frame(3)


LOOK = dict(screen="PAIRING", zone=None, ramp="green", intensity=0.1, speed_px_s=-30.0,
            pulse_period_ms=3000, wavelength_px=90.0, glow_r_px=30.0, ring_live=False)


def card(k, **kw):
    """The howto card k (finder/howto.py CARDS) over PAIRING looking."""
    top, word, glyph, runes, cd, trend, bump = HT.CARDS[k - 1]
    d = dict(LOOK, sub="howto", top_text=top, word=word, glyph=glyph, runes=runes,
             countdown=cd, trend=trend, bump_icons=bump)
    d.update(kw)
    return RP.make_params(**d)


def lines():
    yield "# fields <FIELDS>; names <enum> <spellings in C++ enum order>"
    yield "fields " + " ".join(RP.FIELDS)
    yield "names screen " + " ".join(T.SCREENS)
    yield "names sub " + " ".join(SUBS)
    yield "names glyph " + " ".join(T.GLYPHS)
    yield "names ramp " + " ".join(T.RAMP_NAMES)
    yield "names arrow_style " + " ".join(T.ARROW_STYLES)
    yield "names severity " + " ".join(T.BANNER_SEVERITIES)
    yield "names band " + " ".join(T.BAND_LABELS)
    yield "names zone_heartbeat " + " ".join(h or "n" for h in T.ZONE_HEARTBEAT)
    yield "menu_visible %d" % RP._MENU_VISIBLE
    yield "# make <tokens given> -> <every field>"
    for kw in MAKES:
        rp = RP.make_params(**kw)
        assert representable(rp)
        yield "make %s -> %s" % (kw_toks(kw), toks(rp))
    yield "# wavelength <speed_px_s> <pulse_period_ms> -> <px>"
    for spd, per in WAVELENGTHS:
        yield "wavelength %s %d -> %s" % (repr(spd), per, repr(RP.wavelength(spd, per)))
    yield "# arrow_style <cone_deg|n> -> <style|n>"
    for c in CONES:
        yield "arrow_style %s -> %s" % (_flt(c), RP.arrow_style(c) or "n")
    yield "# validate <fields that differ from DEFAULTS, wavelength_px always> -> <count>, then its errors"
    for rp in validate_cases():
        assert representable(rp), rp
        v = RP.validate(rp)
        yield "validate %s -> %d" % (toks(rp, True), len(v))
        for m in v:
            assert "  " not in m and m == m.strip(), m
            yield "error " + m
