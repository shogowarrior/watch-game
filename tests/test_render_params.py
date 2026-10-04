import json

from finder import tuning as T
from finder.render_params import (
    FIELDS, RenderParams, DEFAULTS, make_params, replace, validate, to_dict, from_dict,
    arrow_style, wavelength, BUMP_ICONS_MAX, _W_BUMP,
)

# ui-spec §3 example: WARM, locked arrow, getting warmer.
SPEC_EXAMPLE = (
    '{"t_ms": 812340, "screen": "WARM", "sub": "walk", "zone": 2, "ramp": "green",'
    ' "intensity": 0.55, "speed_px_s": 80, "pulse_period_ms": 1000, "wavelength_px": 80,'
    ' "glow_r_px": 42, "ring_live": true, "burst": false,'
    ' "glyph": "arrow", "arrow_deg": 0, "cone_deg": 31, "arrow_style": "solid_b",'
    ' "trend": 1, "trend_strong": false, "countdown": null, "runes": null, "bump_icons": null,'
    ' "dist_band": "~10", "dist_stale": false, "word": null, "top_text": null, "banner": null,'
    ' "status": [64, 71, 4, false, false], "menu_rows": null, "sweep": null,'
    ' "haptic": null, "heartbeat": "DOUBLE", "heartbeat_every": 1, "backlight": 0.6,'
    ' "sun": false, "fps_cap": 20}'
)


def _zone_frame(z, **kw):
    d = dict(screen=T.ZONE_NAMES[z], zone=z, ramp="green", intensity=0.5,
             speed_px_s=T.ZONE_SPEED_PX_S[z], pulse_period_ms=T.ZONE_PERIOD_MS[z],
             glow_r_px=40.0, ring_live=True, glyph="glow",
             dist_band=T.BAND_LABELS[T.ZONE_BANDS[z][0]],
             heartbeat=T.ZONE_HEARTBEAT[z], heartbeat_every=T.ZONE_HB_EVERY[z])
    d.update(kw)
    return make_params(**d)


def _has(viol, field):
    for v in viol:
        if v.startswith(field + ":") or v.startswith(field + "/"):
            return True
    return False


def test_fields_and_namedtuple():
    assert len(FIELDS) == 35 and len(set(FIELDS)) == 35
    assert set(DEFAULTS) == set(FIELDS)
    rp = make_params()
    assert isinstance(rp, tuple) and len(rp) == len(FIELDS)
    for i, k in enumerate(FIELDS):
        assert getattr(rp, k) is rp[i] or getattr(rp, k) == rp[i]
    fields = getattr(RenderParams, "_fields", None)   # absent on MicroPython
    if fields is not None:
        assert tuple(fields) == FIELDS


def test_defaults_are_a_valid_searching_frame():
    rp = make_params()
    assert rp.screen == "SEARCHING"
    assert validate(rp) == [], validate(rp)
    assert abs(rp.wavelength_px - wavelength(rp.speed_px_s, rp.pulse_period_ms)) < 1e-9


def test_make_params_rejects_unknown_field():
    try:
        make_params(colour=1)
    except TypeError:
        return
    assert False, "expected TypeError"


def test_replace():
    rp = make_params()
    rp2 = replace(rp, t_ms=5, word="SEARCHING")
    assert rp2.t_ms == 5 and rp2.word == "SEARCHING" and rp.t_ms == 0
    assert rp2.screen == rp.screen
    try:
        replace(rp, nope=1)
    except TypeError:
        return
    assert False, "expected TypeError"


def test_spec_example_validates_and_roundtrips():
    src = json.loads(SPEC_EXAMPLE)
    rp = from_dict(src)
    assert validate(rp) == [], validate(rp)
    assert rp.status == (64, 71, 4, False, False)
    d = to_dict(rp)
    assert d["status"] == [64, 71, 4, False, False]
    back = json.loads(json.dumps(d))
    assert back == src
    assert from_dict(back) == rp


def test_json_roundtrip_with_nested_tuples():
    bins = (0.1, None, 0.5, 1.0, None, 0.0, 0.2, 0.3, None, 0.9, 0.4, 0.6)
    rp = make_params(t_ms=10, screen="SCANNING", sub="sweep", zone=2, ramp="green",
                     intensity=0.3, speed_px_s=0.0, pulse_period_ms=1000, glow_r_px=12.0,
                     glyph="turn", sweep=(90.0, bins, 3, False),
                     banner=None, status=(50, None, 3, False, False))
    assert validate(rp) == [], validate(rp)
    line = json.dumps(to_dict(rp))
    rp2 = from_dict(json.loads(line))
    assert rp2 == rp
    assert isinstance(rp2.sweep, tuple) and isinstance(rp2.sweep[1], tuple)


def test_from_dict_strict_and_lenient():
    d = to_dict(make_params())
    d["extra"] = 1
    try:
        from_dict(d)
    except ValueError:
        pass
    else:
        assert False, "expected ValueError"
    assert from_dict(d, strict=False) == make_params()
    assert from_dict({"t_ms": 7}).t_ms == 7


def test_arrow_style_tiers():
    assert arrow_style(12) == "solid_a" and arrow_style(25) == "solid_a"
    assert arrow_style(25.1) == "solid_b" and arrow_style(45) == "solid_b"
    assert arrow_style(45.5) == "outline" and arrow_style(60) == "outline"
    assert arrow_style(60.5) is None and arrow_style(None) is None


def test_valid_zone_frames():
    for z in range(4):
        rp = _zone_frame(z)
        assert validate(rp) == [], (z, validate(rp))
        rp = _zone_frame(z, glyph="arrow", arrow_deg=350.0, cone_deg=20.0,
                         arrow_style="solid_a", sub="walk")
        assert validate(rp) == [], (z, validate(rp))
    # chevrons with a strong trend in FAR..WARM
    for z in range(3):
        rp = _zone_frame(z, glyph="chevrons", trend=-1, trend_strong=True)
        assert validate(rp) == [], validate(rp)


def test_valid_other_screens():
    ll = T.FIELD_LINK_LOST
    rp = make_params(screen="LINK_LOST", zone=1, ramp=ll[0], intensity=0.3,
                     speed_px_s=ll[2], pulse_period_ms=ll[3], glow_r_px=ll[4],
                     glyph="seeker", dist_band="~20", dist_stale=True,
                     top_text="LAST ~20M", banner=("LOST 0:12", "warn", True))
    assert validate(rp) == [], validate(rp)
    pl = T.FIELD_PAIRING_LOOKING
    rp = make_params(screen="PAIRING", sub="looking", ramp=pl[0], intensity=pl[1],
                     speed_px_s=pl[2], pulse_period_ms=pl[3], glow_r_px=pl[4],
                     glyph="glow", top_text="PAIR", word="LOOKING")
    assert validate(rp) == [], validate(rp)
    rp = make_params(screen="PAIRING", sub="seen", ramp="green", speed_px_s=0.0,
                     glyph="runes", runes=(0, 7, 3), top_text="SAME RUNES?", word="BUMP = YES")
    assert validate(rp) == [], validate(rp)
    rp = make_params(screen="PAIRING", sub="split", ramp="green", glyph="countdown",
                     countdown=30, speed_px_s=40.0, pulse_period_ms=2400,
                     top_text="NO PEEKING", word="SPLIT UP")
    assert validate(rp) == [], validate(rp)
    rp = make_params(screen="FOUND", sub="celebrate", ramp="gold", intensity=1.0,
                     speed_px_s=0.0, pulse_period_ms=1200, glow_r_px=90.0, burst=True,
                     glyph="check", word="FOUND", top_text="TIME 12:48", haptic="FOUND")
    assert validate(rp) == [], validate(rp)
    rp = make_params(screen="MENU", sub="2", ramp="grey",
                     menu_rows=("RESUME", "SUN: ON", "BUZZ: EVENTS", "PLACE: IN"))
    assert validate(rp) == [], validate(rp)
    rp = _zone_frame(1, status=(80, 80, 3, True, True), sun=True)   # unreliable: pinned
    assert validate(rp) == [], validate(rp)


def test_spec_copy_fits_font_and_length():
    words = ("LOOKING", "BUMP = YES", "WAITING", "HOLD STILL", "SPLIT UP", "GO", "SEARCHING",
             "WALK ABOUT", "BUMP!", "FOUND", "FOUND 1:48", "FOUND 9:59", "FOUND 12M",
             "FOUND 99M+", "TURN RIGHT", "TURN LEFT",
             "4 O'CLOCK", "12 O'CLOCK", "AHEAD", "BEHIND", "WALK", "SAVER ON", "BYE")
    labels = ("PAIR", "SAME RUNES?", "STAND 1 STEP APART", "NO PEEKING", "TAP TO SCAN",
              "LOOK AROUND", "BUMP WRISTS", "FRIEND NOT READY", "ONLY YOU FELT IT",
              "FRIEND FELT IT", "TIME 12:48", "TIME 99:59",
              "BUTTON: PLAY AGAIN", "HOLD AT CHEST", "HOLD FLAT",
              "FRIEND SCANNING", "TAP TO RESCAN", "WRONG WAY? RESCAN", "LAST ~20M",
              "CAL SKIPPED", "FRIEND BATT 20%")
    for w in words:
        assert validate(_zone_frame(0, word=w)) == [], w
    for s in labels:
        assert validate(_zone_frame(0, top_text=s)) == [], s
    for s in ("NO FIX, TRY AGAIN", "LOST 0:27 GO BACK", "BACK IN RANGE", "BATTERY 5%"):
        assert validate(_zone_frame(0, banner=(s, "info", False))) == [], s


def test_bump_icons_rules():
    from finder import game
    assert game.W_BUMP == _W_BUMP and BUMP_ICONS_MAX == 5
    hot = dict(glyph="bump", dist_band="<3")
    for v in range(BUMP_ICONS_MAX + 1):
        assert validate(_zone_frame(3, bump_icons=v, **hot)) == [], v
    assert validate(_zone_frame(3, bump_icons=0, word="BUMP!", top_text="BUMP WRISTS",
                                **hot)) == []
    for v in (6, 7, -1, True, 1.0, "1"):
        assert _has(validate(_zone_frame(3, bump_icons=v, **hot)), "bump_icons"), v
    assert _has(validate(_zone_frame(3, glyph="bump", dist_band="<3")), "bump_icons")
    assert _has(validate(_zone_frame(3, bump_icons=0, dist_band="<3")), "bump_icons")
    assert _has(validate(_zone_frame(2, bump_icons=0, glyph="bump")), "glyph")
    assert _has(validate(_zone_frame(3, bump_icons=0, sub="walk", **hot)), "glyph")
    assert _has(validate(_zone_frame(3, bump_icons=4, word="BUMP!", **hot)), "word")
    assert validate(_zone_frame(3, bump_icons=1, word="BUMP!", **hot)) == []


def test_violations_detected():
    base = _zone_frame(2)
    ll = T.FIELD_LINK_LOST
    lost = make_params(screen="LINK_LOST", zone=1, ramp=ll[0], intensity=0.3,
                       speed_px_s=ll[2], pulse_period_ms=ll[3], glow_r_px=ll[4],
                       glyph="seeker", dist_band="~20", dist_stale=True)
    assert validate(replace(lost, trend=1)) == []            # the LAST chip's mark (§3)
    cases = (
        ("screen", replace(base, screen="HIDING")),
        ("sub", replace(base, sub="sweep")),
        ("zone", replace(base, zone=1)),
        ("ramp", replace(base, ramp="gold")),
        ("intensity", replace(base, intensity=1.5)),
        ("wavelength_px", replace(base, wavelength_px=50.0)),
        ("speed_px_s", replace(base, speed_px_s=300.0)),
        ("speed_px_s", replace(base, speed_px_s=56.0, wavelength_px=56.0)),
        ("pulse_period_ms", replace(base, pulse_period_ms=100)),
        ("glow_r_px", replace(base, glow_r_px=120.0)),
        ("ring_live", replace(base, ring_live=1)),
        ("glyph", replace(base, glyph="eye")),
        ("arrow_deg", replace(base, glyph="arrow", arrow_deg=10.0)),
        ("cone_deg", replace(base, glyph="arrow", arrow_deg=10.0, cone_deg=70.0,
                             arrow_style="outline")),
        ("arrow_style", replace(base, glyph="arrow", arrow_deg=10.0, cone_deg=50.0,
                                arrow_style="solid_a")),
        ("glyph", replace(base, arrow_deg=10.0, cone_deg=20.0, arrow_style="solid_a")),
        ("glyph", replace(base, glyph="arrow")),
        ("trend", replace(_zone_frame(3), trend=1)),
        ("trend_strong", replace(base, trend_strong=True)),
        ("countdown", replace(base, glyph="countdown")),
        ("dist_band", replace(base, dist_band="12m")),
        ("dist_band", replace(base, dist_band="~40")),
        ("dist_stale", replace(base, dist_stale=True)),
        ("word", replace(base, word="SEARCHING..")),
        ("word", replace(base, word="walk")),
        ("word", replace(base, word="WALK?")),
        ("top_text", replace(base, top_text="THIS HINT IS FAR TOO LONG")),
        ("banner", replace(base, banner=("LOST", "panic", True))),
        ("banner", replace(base, banner=("LOST, FRIEND LOW BAT", "warn", True))),
        ("status", replace(base, status=(64, 71, 5, False, False))),
        ("status", replace(base, status=(64, 71))),
        ("status", replace(base, status=(64, 71, 4, False))),
        ("status", replace(base, status=(64, 71, 4, False, 1))),
        ("status", replace(base, status=(64, 71, 4, False, True))),
        ("runes", replace(base, runes=(0, 3, 6))),
        ("runes", replace(base, screen="PAIRING", sub="seen", zone=None, glyph="runes",
                          dist_band=None, heartbeat=None, runes=(0, 3, 8))),
        ("runes", replace(base, screen="PAIRING", sub="seen", zone=None, glyph="runes",
                          dist_band=None, heartbeat=None)),
        ("menu_rows", replace(base, menu_rows=("RESUME", "SUN: OFF", "BUZZ: FULL", "END ROUND"))),
        ("menu_rows", replace(base, screen="MENU", sub="0", zone=None, dist_band=None,
                              heartbeat=None)),
        ("menu_rows", replace(base, screen="MENU", sub="0", zone=None, dist_band=None,
                              heartbeat=None, menu_rows=("RESUME", "SUN: OFF"))),
        ("menu_rows", replace(base, screen="MENU", sub="0", zone=None, dist_band=None,
                              heartbeat=None, menu_rows=("RESUME", "sun", "BUZZ", "END"))),
        ("sun", replace(base, sun=1)),
        ("sweep", replace(base, sweep=(0.0, (0.0,) * 12, 0, False))),
        ("haptic", replace(base, haptic="BUZZ")),
        ("heartbeat", replace(base, heartbeat="TICK")),
        ("heartbeat_every", replace(base, heartbeat_every=3)),
        ("backlight", replace(base, backlight=2.0)),
        ("fps_cap", replace(base, fps_cap=30)),
        ("t_ms", replace(base, t_ms=-1)),
        ("top_text", replace(base, sub="turn", glyph="arrow", arrow_deg=30.0, cone_deg=20.0,
                             arrow_style="solid_a", sweep=(30.0, (None,) * 12, None, False),
                             top_text="TAP TO SCAN")),
        ("trend_strong", replace(lost, trend=1, trend_strong=True)),
        ("glyph", replace(lost, glyph="chevrons", trend=-1)),
        ("glyph", replace(_zone_frame(3), glyph="chevrons")),
    )
    for field, rp in cases:
        v = validate(rp)
        assert _has(v, field), (field, v)


def test_arrow_forbidden_screens_and_phases():
    arrow = dict(glyph="arrow", arrow_deg=0.0, cone_deg=20.0, arrow_style="solid_a")
    for sc in ("SEARCHING", "LINK_LOST", "FOUND", "PAIRING"):
        v = validate(replace(make_params(), screen=sc, **arrow))
        assert _has(v, "arrow_deg"), (sc, v)
    v = validate(make_params(screen="SCANNING", sub="ready", zone=2, ramp="green",
                             glyph="arrow", arrow_deg=0.0, cone_deg=20.0,
                             arrow_style="solid_a"))
    assert _has(v, "arrow_deg"), v
    v = validate(make_params(screen="SEARCHING", speed_px_s=36.0))
    assert _has(v, "speed_px_s"), v
    v = validate(make_params(screen="SCANNING", sub="sweep", zone=1, ramp="green",
                             glyph="turn", word="TURN RIGHT", speed_px_s=0.0))
    assert _has(v, "word/top_text"), v
