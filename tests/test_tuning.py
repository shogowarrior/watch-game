import math
import sys

from finder import tuning as T
from tests import Skip


def _gen():
    """(tools/gen_tuning module, parsed tokens.json); CPython only."""
    if sys.implementation.name != "cpython":
        raise Skip("needs CPython + docs/design/tokens.json")
    import json
    from tools import gen_tuning
    with open(gen_tuning.TOKENS) as f:
        return gen_tuning, json.load(f)


def _rejects(gen, tok, edit, errors=(ValueError,)):
    """True if ``gen.build`` fails on a copy of ``tok`` changed by ``edit``."""
    import copy
    t = copy.deepcopy(tok)
    edit(t)
    try:
        gen.build(t)
    except errors:
        return True
    return False


def test_generated_file_up_to_date():
    gen, _tok = _gen()
    with open(gen.OUT) as f:
        cur = f.read()
    assert gen.generate() == cur, "finder/tuning.py is stale: run python3 tools/gen_tuning.py"


def test_generator_rejects_changed_token_strings():
    """Token strings that carry numbers must match their whole template, and
    no token falls back to a default: a reworded token fails the generator."""
    gen, tok = _gen()
    gen.build(tok)

    def fails(edit):
        return _rejects(gen, tok, edit, (ValueError, KeyError))

    def squared(*path):
        def edit(t):
            for k in path[:-1]:
                t = t[k]
            t[path[-1]] += "^2"
        return edit

    for path in (("field", "intensity_map", "floor"), ("field", "intensity_map", "glow_r_px"),
                 ("thresholds", "proximity", "formula"), ("thresholds", "arrow", "sigma_model"),
                 ("thresholds", "found", "requires"),
                 ("field", "standing_wave"), ("field", "ghost_rings"), ("field", "iris"),
                 ("motion", "flash_limit"), ("motion", "temporal_aa"), ("haptics", "queue")):
        assert fails(squared(*path)), path
    assert fails(lambda t: t["thresholds"]["calibrate"].pop("n_indoor"))
    assert fails(lambda t: t["typography"]["word"].pop("chars"))
    assert fails(lambda t: t["thresholds"]["found"].update(
        requires=t["thresholds"]["found"]["requires"].replace("~5", "~7")))     # not a band label


def test_generator_rejects_disagreeing_copies():
    """A value tokens.json holds twice must agree, or the generator fails."""
    gen, tok = _gen()

    def fails(edit):
        return _rejects(gen, tok, edit)

    assert fails(lambda t: t["power"]["backlight"].update(low_battery=0.5))
    assert fails(lambda t: t["motion"]["use"]["sweep_rotation"].update(deg_per_s=40))
    # scan.py hard-codes the 3-2-1 countdown, 30 deg bins and one full turn
    assert fails(lambda t: t["thresholds"]["scan"].update(ready_ms=5000))
    assert fails(lambda t: t["thresholds"]["scan"].update(bins=8))
    assert fails(lambda t: t["thresholds"]["scan"].update(duration_ms=10000))
    assert fails(lambda t: t["field"]["intensity_map"].update(glow_r_px="20 + 40*I (FOUND 80)"))
    assert fails(lambda t: t["states"]["FOUND"].update(glow_r=80))
    assert fails(lambda t: t["motion"]["duration_ms"].update(breathe_found=1200))
    assert fails(lambda t: t["motion"]["duration_ms"].update(breathe_pairing=3000))
    assert fails(lambda t: t["motion"]["duration_ms"].update(hue_crossfade=1000))
    assert fails(lambda t: t["glyphs"]["link_bars"].update(count=5))
    for st in ("PAIRING", "SEARCHING", "LINK_LOST"):
        assert fails(lambda t: t["states"][st].update(wavelength_px=t["states"][st]["wavelength_px"] + 5)), st


def test_tokens_keep_no_spec_copies():
    """tokens.json wins over ui-spec (AGENTS rule 8), so it holds no stale copies:
    per-screen glyphs, copy and haptics live in ui-spec §6/§7, iris radii in
    layout, the iris rim floor comes from field.iris, and the copy charsets
    from typography.*.chars (the font prose is free text)."""
    gen, tok = _gen()
    for name, st in tok["states"].items():
        assert not set(st) & {"glyph", "text", "haptic", "iris_r"}, name
    tok["field"]["iris"] = tok["field"]["iris"].replace("max(5,", "max(6,")
    tok["typography"]["word"]["source"] = tok["typography"]["label"]["source"] = "reworded"
    built = dict((n, v) for _title, rows in gen.build(tok) for n, v, _c in rows)
    assert built["IRIS_RIM_MIN_LEVEL"] == 6.0 and T.IRIS_RIM_MIN_LEVEL == 5.0
    assert built["WORD_CHARS"] == T.WORD_CHARS and built["LABEL_CHARS"] == T.LABEL_CHARS
    # ui-spec copy: "TURN RIGHT", "SAME RUNES?", "NO FIX, TRY AGAIN", "SUN: ON/OFF"
    assert " " in T.WORD_CHARS and set(" ?,/:") <= set(T.LABEL_CHARS)
    assert not hasattr(T, "WORD_EXTRA_CHARS") and not hasattr(T, "LABEL_EXTRA_CHARS")


def test_header_and_version():
    assert T.TOKENS_VERSION == "0.2.0"
    assert len(T.TOKENS_HASH) == 16


def test_zones_align_with_bands():
    assert len(T.ZONE_ENTER_M) == len(T.ZONE_EXIT_M) == len(T.ZONE_DWELL_MS) == 3
    for k in range(3):
        assert T.ZONE_EXIT_M[k] > T.ZONE_ENTER_M[k]
        assert T.ZONE_ENTER_M[k] == T.BAND_EDGES_M[3 - k]
        # the closer zone's lowest band edge is the enter distance
        assert T.BAND_EDGES_M[T.ZONE_BANDS[k][0] - 1] == T.ZONE_ENTER_M[k]
    assert T.ZONE_BANDS[0][1] == len(T.BAND_LABELS) - 1 and T.ZONE_BANDS[3][0] == 0
    for z in range(3):
        assert T.ZONE_BANDS[z + 1][1] + 1 == T.ZONE_BANDS[z][0]
    assert len(T.BAND_LABELS) == len(T.BAND_EDGES_M) + 1
    assert list(T.BAND_EDGES_M) == sorted(T.BAND_EDGES_M)
    assert abs(T.BAND_HYST - 1.15) < 1e-9


def test_zone_tempo_matches_spec_table():
    spec_wl = (96, 90, 80, 60)
    for z in range(4):
        wl = T.ZONE_SPEED_PX_S[z] * T.ZONE_PERIOD_MS[z] / 1000.0
        assert abs(wl - spec_wl[z]) <= 1.0
    assert T.ZONE_HB_EVERY == (2, 1, 1, 1)
    assert T.ZONE_HEARTBEAT == ("TICK", "TICK", "DOUBLE", None)   # HOT: knocks (§6)
    assert T.ZONE_PERIOD_MS == (2400, 1600, 1000, 500)
    assert T.ZONE_SPEED_PX_S == (40.0, 56.0, 80.0, 120.0)
    assert T.ZONE_LEAD_PX == (3, 3, 3, 3) and T.ZONE_TRAIL_PX == (22, 20, 18, 14)


def test_intensity_map_sun_floor():
    far_crest = T.FLOOR_A + T.PULSE_AMP_A          # I = 0
    hot_crest = T.FLOOR_A + T.FLOOR_B + T.PULSE_AMP_A + T.PULSE_AMP_B
    assert far_crest >= 4.3 - 1e-9
    assert hot_crest >= 7.0
    assert T.GLOW_R_A == 20.0 and T.GLOW_R_B == 40.0 and T.GLOW_R_FOUND == 90.0


def test_calibration_matches_path_loss_defaults():
    from finder.estimators import make
    pl = make().pl                      # the estimator the game runs
    assert T.P1M_NOMINAL_DBM == pl.p0
    assert T.PATH_LOSS_N == pl.n
    assert T.PATH_LOSS_N_INDOOR > T.PATH_LOSS_N
    assert T.CAL_CLAMP_DB == 6.0


def test_sigma_model_and_tiers():
    s = math.sqrt(30.0 ** 2 + (T.SIGMA_TURN_K * 120) ** 2 + (T.SIGMA_STEP_K * 40) ** 2)
    assert 40.0 < s <= T.ARROW_TIER_MAX_DEG[1]           # spec example: solid_b
    assert T.ARROW_STYLES == ("solid_a", "solid_b", "outline")
    assert T.ARROW_TIER_MAX_DEG == (25.0, 45.0, 60.0)
    assert T.ARROW_BORN_MAX_SIGMA_DEG == T.ARROW_TIER_MAX_DEG[1]
    assert T.ARROW_HIDE_SIGMA_DEG == T.ARROW_TIER_MAX_DEG[-1]
    assert T.SIGMA_COLDER_HIT_DEG == 20.0 and T.SIGMA_UNRELIABLE_DEG == 15.0
    assert T.ARROW_MAX_AGE_MS == 120000


def test_haptics():
    assert len(T.HAPTIC_NAMES) == 9
    assert set(T.HAPTIC_NAMES) == set(T.HAPTIC_PATTERNS) == set(T.HAPTIC_RANK)
    for name, pat in T.HAPTIC_PATTERNS.items():
        assert len(pat) % 2 == 1, name                   # starts and ends with "on"
        for i, ms in enumerate(pat):
            assert ms >= (T.HAPTIC_MIN_PULSE_MS if i % 2 == 0 else T.HAPTIC_MIN_GAP_MS), name
    r = T.HAPTIC_RANK
    assert r["FOUND"] > r["LOST"] > r["NOPE"] > r["HOLD"] > r["BATT"] > r["CLOSER"]
    assert r["CLOSER"] == r["FARTHER"] > r["DOUBLE"] > r["TICK"]
    assert T.HAPTIC_BLANKING_MS == 150
    assert T.HAPTIC_PATTERNS["FOUND"] == (80, 60, 80, 60, 80, 200, 500)


def _swap(c):
    return ((c & 0xFF) << 8) | (c >> 8)


def test_ramps():
    for r in T.RAMP_NAMES:
        stops = T.RAMP_SWAPPED[r]
        lut = T.RAMP_LUT[r]
        assert len(stops) == 8 and len(lut) == 64
        for k in range(8):
            assert lut[9 * k] == stops[k], (r, k)   # j = 9k is stop k exactly
        # brightness (green channel) never decreases along the LUT
        g6 = [(_swap(c) >> 5) & 0x3F for c in lut]
        assert g6 == sorted(g6), r
    assert T.C_BG_IRIS == 0x6100
    assert T.C_ACCENT_FOUND == T.RAMP_SWAPPED["gold"][5]


def test_layout_geometry():
    assert T.CENTER == (120, 120)
    ir = T.IRIS_R
    assert ir["chevrons"] == ir["seeker"] == 44 and ir["arrow"] == ir["scan"] == 64 and ir["runes"] == 92
    assert T.BEAM_R < ir["arrow"] < T.SWEEP_R_INNER < T.SWEEP_R_OUTER
    assert T.TOP_SLOT == (12, 12, 216, 24) and T.BOTTOM_SLOT == (24, 186, 192, 40)
    assert T.FIELD_R_MAX == 168 and T.CORE_DOT_R == 6
    # the dart fits inside the beam radius
    for x, y in T.DART_PTS:
        assert x * x + y * y <= T.BEAM_R * T.BEAM_R
    assert len(T.RUNES) == 8


def test_link_scan_beacon():
    assert T.LINK_LOST_AFTER_MS == 5000 and T.LIVE_WINDOW_MS == 1000
    assert T.RELINK_PACKETS == 3 and T.RELINK_WINDOW_MS == 2000
    assert (T.BEACON_HZ_NORMAL, T.BEACON_HZ_HOT, T.BEACON_HZ_SCAN, T.BEACON_HZ_SAVER) == (10, 20, 20, 5)
    assert T.SCAN_DURATION_MS * T.SCAN_DEG_PER_S / 1000 == 360


def test_found_gate():
    assert (T.BUMP_WINDOW_MS, T.FALLBACK_PRESS_WINDOW_MS) == (400, 3000)
    assert T.BAND_LABELS[T.FALLBACK_MAX_BAND] == "~5"


def test_field_presets():
    for p in (T.FIELD_PAIRING_LOOKING, T.FIELD_SEARCHING, T.FIELD_LINK_LOST):
        assert p[0] in T.RAMP_NAMES
        assert p[2] < 0                                   # inward "listening" rings
        assert abs(p[2]) * p[3] / 1000.0 <= T.WAVELENGTH_MAX_PX
    # ui-spec §6 values the renderer reads from tokens.json (states)
    assert (T.PAIRING_SEEN_FLOOR, T.SEARCHING_GLOW_AMP) == (0.4, 1.5)
    assert T.FIELD_LEAD_TRAIL_PX == (3, 22) and T.FOUND_LEAD_TRAIL_PX == (3, 24)
    assert T.FIELD_SCAN_READY_PULSE_SCALE == 0.4 and T.FIELD_SCAN_READY_GLOW_R == 8.0
    assert T.FIELD_SCAN_SWEEP_GLOW_AMP == (1.0, 5.0) and T.FIELD_SCAN_SWEEP_GLOW_R == 12.0
    assert T.BREATHE_PAIRING_MS == 2400 and T.LINK_Q_MAX == len(T.LINK_BARS[2])
    assert T.IDLE_DIM_BACKLIGHT < T.BACKLIGHT_NORMAL < T.BACKLIGHT_BOOST
    assert T.TEMPORAL_AA_K == 1.5
    assert (T.FLASH_LIMIT_STEPS, T.FLASH_LIMIT_MS) == (2, 333)
