import math
import sys

from finder import tuning as T


def _cpython_with_docs():
    if sys.implementation.name != "cpython":
        return None
    import os
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if not os.path.exists(os.path.join(root, "docs", "design", "tokens.json")):
        return None
    return root


def test_generated_file_up_to_date():
    root = _cpython_with_docs()
    if root is None:
        print("SKIP test_generated_file_up_to_date: needs CPython + docs/design/tokens.json")
        return
    import os
    sys.path.insert(0, os.path.join(root, "tools"))
    try:
        import gen_tuning
    finally:
        sys.path.pop(0)
    with open(os.path.join(root, "finder", "tuning.py")) as f:
        cur = f.read()
    assert gen_tuning.generate() == cur, "finder/tuning.py is stale: run python3 tools/gen_tuning.py"
    assert gen_tuning.main(["--check"]) == 0


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
        assert abs(T.ZONE_WAVELENGTH_PX[z] - wl) < 1e-9
        assert abs(wl - spec_wl[z]) <= 1.0
        assert T.ZONE_HEARTBEAT[z] in T.HAPTIC_PATTERNS
    assert T.ZONE_HB_EVERY == (2, 1, 1, 1)
    assert T.ZONE_HEARTBEAT[2] == "DOUBLE"


def test_intensity_map_sun_floor():
    far_crest = T.FLOOR_A + T.PULSE_AMP_A          # I = 0
    hot_crest = T.FLOOR_A + T.FLOOR_B + T.PULSE_AMP_A + T.PULSE_AMP_B
    assert far_crest >= 4.3 - 1e-9
    assert hot_crest >= 7.0
    assert T.GLOW_R_A == 20.0 and T.GLOW_R_B == 40.0 and T.GLOW_R_FOUND == 90.0


def test_proximity_reference_values():
    def prox(d):
        p = math.log(T.PROX_D_FAR_M / max(d, T.PROX_D_MIN_M)) / T.PROX_LN_RATIO
        return min(1.0, max(0.0, p))
    for d, p in ((55, 0.03), (28, 0.22), (14, 0.43), (7, 0.63), (3, 0.88)):
        assert abs(prox(d) - p) < 0.01, (d, prox(d))
    assert prox(60) == 0.0 and prox(2) > 0.999


def test_calibration_matches_path_loss_defaults():
    from finder.estimators import make
    pl = make().pl                      # the estimator the game runs
    assert T.P1M_NOMINAL_DBM == pl.p0
    assert T.PATH_LOSS_N == pl.n
    assert T.PATH_LOSS_N_INDOOR > T.PATH_LOSS_N
    assert T.CAL_AT_M == 1.0 and T.CAL_CLAMP_DB == 6.0


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


def _rgb565(rgb):
    r, g, b = rgb
    return ((r >> 3) << 11) | ((g >> 2) << 5) | (b >> 3)


def test_colours_swapped_and_rgb_agree():
    for name, sw in T.COLOR_SWAPPED.items():
        assert _swap(_rgb565(T.COLOR_RGB[name])) == sw, name
    assert T.C_BG_IRIS == 0x6100 and T.C_BG_IRIS_RGB == (0, 12, 8)
    assert T.C_ACCENT_FOUND == T.COLOR_SWAPPED["found.5"]
    assert T.C_GLYPH_ARROW == T.COLOR_SWAPPED["prox.7"]


def test_ramps():
    prefix = {"green": "prox.", "gold": "found.", "grey": "grey."}
    for r in T.RAMP_NAMES:
        assert len(T.RAMP_HEX[r]) == len(T.RAMP_SWAPPED[r]) == len(T.RAMP_RGB[r]) == 8
        lut = T.RAMP_LUT[r]
        assert len(lut) == T.RAMP_LUT_SIZE == 64
        for k in range(8):
            assert T.RAMP_SWAPPED[r][k] == T.COLOR_SWAPPED[prefix[r] + str(k)]
            assert _swap(_rgb565(T.RAMP_RGB[r][k])) == T.RAMP_SWAPPED[r][k]
            assert lut[9 * k] == T.RAMP_SWAPPED[r][k], (r, k)   # j = 9k is stop k exactly
        # brightness (green channel) never decreases along the LUT
        g6 = [(_swap(c) >> 5) & 0x3F for c in lut]
        assert g6 == sorted(g6), r


def test_layout_geometry():
    assert T.CENTER == (120, 120)
    assert T.IRIS_R_CHEVRONS == 44 and T.IRIS_R_ARROW == 64 and T.IRIS_R_RUNES == 92
    assert T.BEAM_R < T.IRIS_R_ARROW < T.SWEEP_R_INNER < T.SWEEP_R_OUTER
    assert T.TOP_SLOT == (12, 12, 216, 23) and T.BOTTOM_SLOT == (24, 186, 192, 40)
    assert T.FIELD_R_MAX == 168 and T.CORE_DOT_R == 6
    # the dart fits inside the beam radius
    for x, y in T.DART_PTS:
        assert x * x + y * y <= T.BEAM_R * T.BEAM_R
    assert len(T.RUNES) == 8


def test_link_scan_beacon():
    assert T.LINK_LOST_AFTER_MS == 5000 and T.LIVE_WINDOW_MS == 1000
    assert T.RELINK_PACKETS == 3 and T.RELINK_WINDOW_MS == 2000
    assert (T.BEACON_HZ_NORMAL, T.BEACON_HZ_HOT, T.BEACON_HZ_SCAN, T.BEACON_HZ_SAVER) == (10, 20, 20, 5)
    assert T.SCAN_BINS * T.SCAN_WEDGE_DEG == 360
    assert T.SCAN_DURATION_MS * T.SCAN_DEG_PER_S / 1000 == 360
    assert T.SWEEP_DEG_PER_S == T.SCAN_DEG_PER_S


def test_field_presets():
    for p in (T.FIELD_PAIRING_LOOKING, T.FIELD_SEARCHING, T.FIELD_LINK_LOST):
        assert p[0] in T.RAMP_NAMES
        assert p[2] < 0                                   # inward "listening" rings
        assert abs(p[2]) * p[3] / 1000.0 <= T.WAVELENGTH_MAX_PX
    assert T.TEMPORAL_AA_K == 1.5
    assert (T.FLASH_LIMIT_STEPS, T.FLASH_LIMIT_MS) == (2, 333)
