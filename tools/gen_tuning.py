"""Generate finder/tuning.py from docs/design/tokens.json (CPython only).

    python3 tools/gen_tuning.py            # (re)write finder/tuning.py
    python3 tools/gen_tuning.py --check    # exit 1 if finder/tuning.py is stale
    python3 tools/gen_tuning.py --tokens T --out O

tokens.json is the source of truth. A few values the logic and renderer need
exist only in docs/design/ui-spec.md; they live in ``SPEC`` below with their
section reference and are emitted in their own clearly marked blocks. Formula
strings in tokens.json (intensity map, sigma model, proximity, standing wave,
temporal AA, flash limit) are parsed strictly: if their wording changes this
script fails loudly instead of emitting a stale number.
"""

import hashlib
import json
import math
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOKENS = os.path.join(ROOT, "docs", "design", "tokens.json")
OUT = os.path.join(ROOT, "finder", "tuning.py")

ZONES = ("FAR", "NEAR", "WARM", "HOT")
RAMPS = ("green", "gold", "grey")


class Hex(int):
    """int emitted as a 0x%04X literal."""


# ---- values defined only in ui-spec.md (section refs in the comments) ------
# name, value, comment
SPEC = (
    ("Screens and phases (ui-spec §3)", (
        ("SCREENS", ("PAIRING", "SEARCHING", "FAR", "NEAR", "WARM", "HOT", "FOUND",
                     "SCANNING", "LINK_LOST", "MENU"), None),
        ("SUBS_PAIRING", ("looking", "seen", "confirmed", "calibrate", "split"), None),
        ("SUBS_SCANNING", ("ready", "sweep", "result"), None),
        ("SUBS_DIRECTION", ("reveal", "turn", "walk"), "FAR..HOT sub (or None)"),
        ("SUBS_FOUND", ("celebrate", "result"), None),
        ("GLYPHS", ("glow", "seeker", "chevrons", "arrow", "countdown", "turn", "check",
                    "runes", "battery"), None),
        ("BANNER_SEVERITIES", ("info", "warn", "critical"), None),
        ("HEARTBEATS", ("TICK", "DOUBLE"), None),
        ("SPEED_MIN_PX_S", -60.0, None),
        ("SPEED_MAX_PX_S", 240.0, None),
        ("PERIOD_MIN_MS", 400, None),
        ("PERIOD_MAX_MS", 4000, None),
        ("WAVELENGTH_MAX_PX", 140.0, None),
        ("WAVELENGTH_TOL_PX", 1.0, "wavelength == |speed| * period / 1000 within this"),
        ("GLOW_R_MAX_PX", 96.0, None),
        ("CONE_DRAW_MIN_DEG", 12.0, "cone half-angle is clamped to 12..60 for drawing"),
        ("FPS_CAP_MIN", 12, None),
        ("FPS_CAP_MAX", 20, None),
        ("LINK_Q_MAX", 4, "status link bars 0..4"),
        ("COUNTDOWN_MAX", 99, "2-digit type.display countdown (split 30..0)"),
    )),
    ("Per-screen field presets (ui-spec §6): (ramp, I, speed_px_s, period_ms, glow_r_px, pulse_amp)", (
        ("FIELD_PAIRING_LOOKING", ("green", 0.1, -30.0, 3000, 30.0, 2.0), "inward rings"),
        ("FIELD_SEARCHING", ("grey", 0.15, -36.0, 3200, 18.0, 2.5), "inward rings"),
        ("FIELD_LINK_LOST", ("grey", None, -30.0, 3000, 16.0, 1.5), "I frozen at last value"),
        ("FIELD_SCAN_READY_PULSE_SCALE", 0.4, "pulse_amp x0.4, glow_r 8 at the rim"),
        ("FIELD_SCAN_READY_GLOW_R", 8.0, None),
        ("FIELD_SCAN_SWEEP_GLOW_R", 12.0, "halo outside the iris, glow_amp 1 + 5*I_mirror"),
        ("FIELD_SCAN_SWEEP_GLOW_AMP", (1.0, 5.0), "glow_amp = a + b*I_mirror"),
        ("PAIRING_SEEN_BREATHE_AMP", (1.5, 3.0), None),
        ("BYE_RING_SPEED_PX_S", -120.0, "3 %% shutdown ring"),
        ("MENU_PALETTE_SCALE", 0.5, None),
        ("BURST_SPEED_MULT", 2.0, "burst ring speed = 2x zone speed (§4)"),
        ("IRIS_RIM_MIN_LEVEL", 5.0, "rim level max(5, floor+glow_amp) (§2)"),
        ("IRIS_ANIM_MS", 300, "iris open/close, out_cubic"),
    )),
    ("Mirror, trend, direction extras (ui-spec §5)", (
        ("MIRROR_EMA_MS", 150, "live mirror RSSI EMA (§5.7)"),
        ("MIRROR_MIN_SPAN_DB", 4.0, "I_mirror denominator floor"),
        ("TREND_START_DB", 3.0, "+1 at >= 3 dB / 8 s starting threshold (§5.5)"),
        ("UNRELIABLE_WINDOW_MS", 5000, "delivery window for 'unreliable' (§5.5)"),
        ("DIRECTION_REVEAL_MS", 1500, None),
        ("DIRECTION_CLOCK_MAX_SIGMA_DEG", 30.0, "clock-hour word if sigma <= 30"),
        ("DIRECTION_AHEAD_DEG", 20.0, "|theta| <= 20 -> AHEAD, turn skipped"),
        ("DIRECTION_LOCK_EASE_MS", 300, None),
        ("DIRECTION_WALK_WORD_MS", 3000, None),
        ("ARROW_EXPIRE_MS", 600, None),
        ("PACER_DEG_PER_S", 30.0, None),
        ("PACER_TICK_DEG", 45.0, None),
    )),
    ("Scan extras (ui-spec §6 SCANNING)", (
        ("SCAN_FLAT_DEG", 20.0, "face-up within 20 deg ..."),
        ("SCAN_FLAT_HOLD_MS", 500, "... held 0.5 s"),
        ("SCAN_WALK_STEPS", 3, ">= 3 steps in 2 s pauses the sweep"),
        ("SCAN_WALK_WINDOW_MS", 2000, None),
        ("SCAN_TICK_DEG", 45.0, "TICK every 45 deg, DOUBLE at 180"),
        ("SCAN_WEDGE_DEG", 30.0, None),
        ("SCAN_BIN_WIDTH_DEG", 6.0, "drawn bar width"),
        ("SCAN_SMOOTH_DEG", 30.0, "bars show a +-30 deg smoothed curve"),
        ("SCAN_BLINK_MS", 200, None),
        ("SCAN_MORPH_MS", 400, None),
        ("SCAN_PEER_WALK_WIDEN_MS", 2000, "peer walking > 2 s of sweep: +15 deg to s0"),
        ("SCAN_PEER_WALK_WIDEN_DEG", 15.0, None),
        ("SCAN_PEER_WALK_FAIL_MS", 4000, "> 4 s: no fix, FRIEND MOVED"),
        ("SCAN_HOLD_REPEAT_MS", 3000, "partner HOLD haptic repeat"),
        ("SCAN_HOLD_REPEAT_MAX", 3, None),
    )),
    ("Pairing, found, battery, power, input (ui-spec §6, §8)", (
        ("PAIR_SPLIT_S", 30, None),
        ("PAIR_RUNE_STEP_MS", 150, None),
        ("CAL_GATE_WINDOW_MS", 1000, "RSSI sd over 1 s > unstable_sd pauses the fill"),
        ("SEARCHING_WALK_ABOUT_MS", 45000, None),
        ("BUMP_WINDOW_MS", 400, "both taps within 400 ms"),
        ("BUMP_TOUCH_GUARD_MS", 300, "ignore taps 300 ms after a screen touch"),
        ("BUMP_READY_HOLD_MS", 1500, "band <3 held 1.5 s"),
        ("BUMP_READY_BAND", 0, "index of '<3'"),
        ("FALLBACK_PRESS_WINDOW_MS", 3000, None),
        ("FALLBACK_MAX_BAND", 1, "band <= '~5'"),
        ("FOUND_CELEBRATE_MS", 2000, None),
        ("BATT_SHUTDOWN_PCT", 3, None),
        ("BATT_INTERSTITIAL_MS", 2500, None),
        ("BYE_WORD_MS", 2000, None),
        ("GOODBYE_BEACONS", 3, None),
        ("LOST_TIMER_MAX_S", 599, "m:ss up to 9:59, then 10M+"),
        ("LOST_HINT_AFTER_MS", 20000, "GO BACK / KEEP ON"),
        ("WAKE_BOOST_MS", 3000, None),
        ("WRIST_DOWN_MS", 2000, None),
        ("IDLE_DIM_MS", 30000, None),
        ("STATUS_AFTER_WAKE_MS", 3000, None),
        ("HINT_CHIP_MS", 4000, "TAP TO SCAN / LOOK AROUND"),
        ("HINT_STILL_MS", 6000, "TAP TO SCAN after 6 s still with no arrow"),
        ("TAP_MIN_MS", 60, None),
        ("TAP_MAX_MS", 400, None),
        ("TAP_MOVE_PX", 12, None),
        ("TAP_R_MAX_PX", 92, None),
        ("LONG_PRESS_MS", 800, None),
        ("WAKE_TOUCH_IGNORE_MS", 300, None),
        ("TOUCH_BURST_COUNT", 3, ">= 3 touches in 1 s ..."),
        ("TOUCH_BURST_WINDOW_MS", 1000, None),
        ("TOUCH_BURST_IGNORE_MS", 2000, "... ignore touches for 2 s"),
        ("BUMP_TAP_IGNORE_MS", 400, "HOT: touch within 400 ms of an accel tap is a bump"),
        ("MENU_AUTOCLOSE_MS", 8000, None),
        ("MENU_CONFIRM_MS", 3000, None),
        ("MENU_ROWS_Y", (32, 76, 120, 164), None),
        ("MENU_ROW_H", 40, None),
        ("HAPTIC_EVENT_GUARD_MS", 1000, "drop an event if >= priority started < 1 s ago"),
        ("HAPTIC_HB_RESUME_MS", 1000, "heartbeats resume 1 s after an event"),
    )),
    ("Copy glyph extras (ui-spec copy needs chars missing from tokens subsets)", (
        ("WORD_EXTRA_CHARS", " ", "e.g. 'TURN RIGHT', \"4 O'CLOCK\""),
        ("LABEL_EXTRA_CHARS", "?,/", "e.g. 'SAME RUNES?', 'NO FIX, TRY AGAIN', 'SUN: ON/OFF'"),
    )),
)


# ---- helpers ---------------------------------------------------------------


def _match(pat, text, what):
    m = re.search(pat, text)
    if not m:
        raise ValueError("tokens.json %s changed format: %r" % (what, text))
    return m


def _rgb(hexstr):
    h = hexstr.lstrip("#")
    return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))


def _rgb565(r, g, b):
    return ((r >> 3) << 11) | ((g >> 2) << 5) | (b >> 3)


def _swap(c):
    return ((c & 0xFF) << 8) | (c >> 8)


def _check_color(name, tok):
    r, g, b = _rgb(tok["hex"])
    c = _rgb565(r, g, b)
    if c != int(tok["rgb565"], 16) or _swap(c) != int(tok["rgb565_swapped"], 16):
        raise ValueError("colour %s: hex/rgb565/swapped disagree" % name)
    if (r, g, b) != ((r >> 3) << 3 | r >> 5, (g >> 2) << 2 | g >> 6, (b >> 3) << 3 | b >> 5):
        raise ValueError("colour %s is not RGB565-exact" % name)
    return c


def _lut(stops_rgb, size):
    """64-entry ramp LUT: linear in 8-bit sRGB between stops, snapped to RGB565."""
    n = len(stops_rgb) - 1
    out = []
    for j in range(size):
        pos = j * n / (size - 1)
        k = min(int(pos), n - 1)
        f = pos - k
        a, b = stops_rgb[k], stops_rgb[k + 1]
        r, g, bl = (int(math.floor(a[i] + (b[i] - a[i]) * f + 0.5)) for i in range(3))
        c = (((r * 31 + 127) // 255) << 11) | (((g * 63 + 127) // 255) << 5) | ((bl * 31 + 127) // 255)
        out.append(Hex(_swap(c)))
    return tuple(out)


def _tup(v):
    """JSON lists -> tuples, recursively."""
    if isinstance(v, list):
        return tuple(_tup(x) for x in v)
    if isinstance(v, dict):
        return {k: _tup(x) for k, x in v.items()}
    return v


def _cname(token):
    return "C_" + token.replace(".", "_").upper()


def _fmt(v, ind=0):
    if isinstance(v, bool) or v is None:
        return repr(v)
    if isinstance(v, Hex):
        return "0x%04X" % v
    if isinstance(v, (int, float, str)):
        return repr(v)
    pad = " " * (ind + 4)
    if isinstance(v, tuple):
        items = [_fmt(x, ind + 4) for x in v]
        one = "(" + ", ".join(items) + ("," if len(items) == 1 else "") + ")"
        if len(one) + ind <= 88 and "\n" not in one:
            return one
        if all(not isinstance(x, (tuple, dict)) for x in v):
            lines, cur = [], ""
            for x in items:  # pack scalars greedily
                if cur and len(pad) + len(cur) + len(x) + 2 > 88:
                    lines.append(cur.rstrip())
                    cur = ""
                cur += x + ", "
            lines.append(cur.rstrip())
            return "(\n" + "".join(pad + ln + "\n" for ln in lines) + " " * ind + ")"
        return "(\n" + "".join(pad + x + ",\n" for x in items) + " " * ind + ")"
    if isinstance(v, dict):
        items = ["%s: %s" % (repr(k), _fmt(x, ind + 4)) for k, x in v.items()]
        one = "{" + ", ".join(items) + "}"
        if len(one) + ind <= 88 and "\n" not in one:
            return one
        return "{\n" + "".join(pad + x + ",\n" for x in items) + " " * ind + "}"
    raise TypeError("cannot emit %r" % (v,))


# ---- build -----------------------------------------------------------------

def build(tok):
    """Return [(section_title, [(name, value, comment), ...]), ...]."""
    th = tok["thresholds"]
    lay = tok["layout"]
    mo = tok["motion"]
    fld = tok["field"]
    hp = tok["haptics"]
    col = tok["color"]
    S = []

    def sec(title, rows):
        S.append((title, rows))

    # display
    d = tok["meta"]["display"]
    sec("Display", [("SCREEN_W", d["width_px"], None), ("SCREEN_H", d["height_px"], None)])

    # zones
    z = th["zones_m"]
    sec("Zones (thresholds.zones_m): boundary k is between zone k and k+1", [
        ("ZONE_FAR", 0, None), ("ZONE_NEAR", 1, None), ("ZONE_WARM", 2, None),
        ("ZONE_HOT", 3, None), ("ZONE_NAMES", ZONES, None),
        ("ZONE_ENTER_M", tuple(float(x) for x in z["enter"]), "enter closer zone at d <="),
        ("ZONE_EXIT_M", tuple(float(x) for x in z["exit"]), "return to farther zone at d >="),
        ("ZONE_DWELL_MS", tuple(int(x) for x in z["dwell_ms"]), "both directions"),
    ])

    # bands
    b = th["bands_m"]
    zb = b["zone_bands"]
    sec("Readout bands (thresholds.bands_m)", [
        ("BAND_EDGES_M", tuple(float(x) for x in b["edges"]), None),
        ("BAND_LABELS", tuple(b["labels"]), None),
        ("BAND_HYST", float(b["hysteresis"]), "multiplicative, each way"),
        ("ZONE_BANDS", tuple(tuple(zb[str(i)]) for i in range(4)), "allowed (lo, hi) band per zone"),
        ("READOUT_SCREENS", tuple(th["readout_visible_in"]), None),
    ])

    # tempo
    zt = th["zone_tempo"]
    per = tuple(int(zt[n]["period_ms"]) for n in ZONES)
    spd = tuple(float(zt[n]["speed_px_s"]) for n in ZONES)
    sec("Zone tempo (thresholds.zone_tempo), index = zone", [
        ("ZONE_PERIOD_MS", per, "ring spawn = heartbeat base period"),
        ("ZONE_SPEED_PX_S", spd, None),
        ("ZONE_WAVELENGTH_PX", tuple(s * p / 1000.0 for s, p in zip(spd, per)), "speed*period/1000"),
        ("ZONE_LEAD_PX", tuple(int(zt[n]["lead_px"]) for n in ZONES), None),
        ("ZONE_TRAIL_PX", tuple(int(zt[n]["trail_px"]) for n in ZONES), None),
        ("ZONE_HEARTBEAT", tuple(zt[n]["heartbeat"] for n in ZONES), None),
        ("ZONE_HB_EVERY", tuple(int(zt[n]["every"]) for n in ZONES), "play on every Nth live ring"),
    ])

    # intensity map
    im = fld["intensity_map"]
    rows = []
    for key, name in (("floor", "FLOOR"), ("glow_amp", "GLOW_AMP"), ("pulse_amp", "PULSE_AMP"),
                      ("glow_r_px", "GLOW_R")):
        m = _match(r"^\s*([-\d.]+)\s*\+\s*([-\d.]+)\s*\*\s*I", im[key], "field.intensity_map." + key)
        rows.append(("%s_A" % name, float(m.group(1)), None))
        rows.append(("%s_B" % name, float(m.group(2)), "%s = A + B*I" % key))
    m = _match(r"FOUND\s+([\d.]+)", im["glow_r_px"], "field.intensity_map.glow_r_px")
    rows.append(("GLOW_R_FOUND", float(m.group(1)), None))
    sec("Intensity map (field.intensity_map)", rows)

    # proximity
    pr = th["proximity"]
    m = _match(r"ln\(([\d.]+)/max\(d,\s*([\d.]+)\)\)/ln\(([\d.]+)\)", pr["formula"],
               "thresholds.proximity.formula")
    ratio = float(m.group(3))
    sec("Proximity p(d) and intensity smoothing (thresholds.proximity)", [
        ("PROX_D_FAR_M", float(m.group(1)), "p = clamp01(ln(D_FAR/max(d, D_MIN))/ln(RATIO))"),
        ("PROX_D_MIN_M", float(m.group(2)), None),
        ("PROX_RATIO", ratio, None),
        ("PROX_LN_RATIO", math.log(ratio), None),
        ("INTENSITY_TAU_MS", int(pr["intensity_tau_ms"]), None),
    ])

    # calibration
    c = th["calibrate"]
    sec("Calibration and path loss (thresholds.calibrate)", [
        ("CAL_AT_M", float(c["at_m"]), None),
        ("CAL_WINDOW_MS", int(c["window_ms"]), None),
        ("P1M_NOMINAL_DBM", float(c["p1m_nominal_dbm"]), None),
        ("CAL_CLAMP_DB", float(c["clamp_db"]), "clamp to nominal +- this"),
        ("PATH_LOSS_N", float(c["n"]), None),
        ("PATH_LOSS_N_INDOOR", float(c.get("n_indoor", c["n"])), None),
        ("CAL_UNSTABLE_SD_DB", float(c["unstable_sd_db"]), None),
        ("CAL_SKIP_AFTER_MS", int(c["skip_after_ms"]), None),
    ])

    # trend
    t = th["trend"]
    sec("Trend gating (thresholds.trend)", [
        ("TREND_CONF_MIN", float(t["conf_min"]), None),
        ("TREND_WINDOW_MS", int(t["window_ms"]), None),
        ("TREND_EVAL_MS", int(t["eval_every_ms"]), None),
        ("TREND_HOLD_EVALS", int(t["hold_evals"]), None),
        ("TREND_STRONG_CONF", float(t["strong_conf"]), None),
        ("TREND_STRONG_DB", float(t["strong_delta_db"]), None),
        ("TREND_FLIP_MIN_MS", int(t["flip_min_ms"]), None),
        ("UNRELIABLE_SD_DB", float(t["unreliable_sd_db"]), None),
        ("UNRELIABLE_DELIVERY", float(t["unreliable_delivery"]), None),
    ])

    # arrow / sigma
    a = th["arrow"]
    sm = a["sigma_model"]
    tiers = a["tiers_deg"]
    tier_names = ("solid_a", "solid_b", "outline")
    sec("Direction arrow and sigma model (thresholds.arrow)", [
        ("ARROW_BORN_MAX_SIGMA_DEG", float(a["born_max_sigma_deg"]), None),
        ("ARROW_STYLES", tier_names, None),
        ("ARROW_TIER_MAX_DEG", tuple(float(tiers[n]) for n in tier_names), "style = first tier >= cone"),
        ("ARROW_HIDE_SIGMA_DEG", float(tiers["outline"]), "hidden above this"),
        ("ARROW_MAX_AGE_MS", int(a["max_age_ms"]), None),
        ("SIGMA_TURN_K", float(_match(r"\(([\d.]+)\*turn\)", sm, "sigma_model").group(1)), "deg per deg turned"),
        ("SIGMA_STEP_K", float(_match(r"\(([\d.]+)\*steps\)", sm, "sigma_model").group(1)), "deg per step"),
        ("SIGMA_STILL_K", float(_match(r"\(([\d.]+)\*still_s\)", sm, "sigma_model").group(1)), "deg per still second"),
        ("SIGMA_COLDER_HIT_DEG", float(_match(r"([\d.]+)\*colder_hits", sm, "sigma_model").group(1)), None),
        ("SIGMA_UNRELIABLE_DEG", float(_match(r"\+([\d.]+) display", sm, "sigma_model").group(1)), "display only"),
        ("SCAN_FLOOR_DEG", float(a["scan_floor_deg"]), "s0 = sqrt(sigma_fit^2 + floor^2)"),
        ("COLDER_HIT_MS", int(a["colder_hit_ms"]), None),
        ("COLDER_HIT_EVERY_MS", int(a["colder_hit_every_ms"]), None),
        ("ARROW_RELINK_RESTORE_MS", int(a["relink_restore_ms"]), None),
    ])

    # scan / probe
    s = th["scan"]
    p = th["probe"]
    sec("Scan and walk probe (thresholds.scan, thresholds.probe)", [
        ("SCAN_DURATION_MS", int(s["duration_ms"]), None),
        ("SCAN_READY_MS", int(s["ready_ms"]), None),
        ("SCAN_DEG_PER_S", float(s["deg_per_s"]), None),
        ("SCAN_MIN_P2T_DB", float(s["min_peak_to_trough_db"]), "no fix if 2*a1 < this"),
        ("SCAN_MAX_S0_DEG", float(s["max_s0_deg"]), None),
        ("SCAN_TILT_FAULT_DEG", float(s["tilt_fault_deg"]), None),
        ("SCAN_TILT_FAULT_MS", int(s["tilt_fault_ms"]), None),
        ("SCAN_ABORT_STEPS", int(s["abort_steps"]), None),
        ("SCAN_ABORT_PAUSE_MS", int(s["abort_pause_ms"]), None),
        ("SCAN_BINS", int(s["bins"]), None),
        ("SCAN_MIN_PACKETS_PER_BIN", int(s["min_packets_per_bin"]), None),
        ("PROBE_STEPS_PER_LEG", int(p["steps_per_leg"]), None),
        ("PROBE_TURN_DEG", float(p["turn_deg"]), None),
        ("PROBE_SIGMA_MIN_DEG", float(p["sigma_min_deg"]), None),
        ("PROBE_ARC_R", (int(p["progress_arc"]["r_inner"]), int(p["progress_arc"]["r_outer"])), None),
    ])

    # link / beacons / battery
    lk = th["link"]
    bh = th["beacon_hz"]
    bp = th["battery_pct"]
    sec("Link, beacons, battery (thresholds.link, beacon_hz, battery_pct)", [
        ("LINK_SEARCHING_AFTER_MS", int(lk["searching_after_ms"]), None),
        ("LINK_LOST_AFTER_MS", int(lk["lost_after_ms"]), None),
        ("RELINK_PACKETS", int(lk["relink_packets"]), "also the SEARCHING exit rule"),
        ("RELINK_WINDOW_MS", int(lk["relink_window_ms"]), None),
        ("LIVE_WINDOW_MS", int(lk["live_window_ms"]), "ring_live = packet in this window"),
        ("LINK_NO_FALLBACK", bool(lk["no_fallback_to_searching"]), None),
        ("BEACON_HZ_NORMAL", int(bh["normal"]), None),
        ("BEACON_HZ_HOT", int(bh["hot"]), None),
        ("BEACON_HZ_SCAN", int(bh["scan"]), None),
        ("BEACON_HZ_SAVER", int(bh["saver"]), None),
        ("FOUND_RSSI_ALONE", bool(th["found"]["rssi_alone"]), None),
        ("BATT_WARN_PCT", int(bp["warn"]), None),
        ("BATT_CRITICAL_PCT", int(bp["critical"]), None),
        ("BATT_BANNER_PCT", int(bp["banner"]), None),
    ])

    # saver / power
    lb = tok["states"]["LOW_BATTERY"]["own_le_10pct"]
    bl = tok["power"]["backlight"]
    sec("Saver and backlight (states.LOW_BATTERY, power.backlight)", [
        ("SAVER_FPS", int(lb["fps"]), None),
        ("SAVER_PULSE_SCALE", float(lb["pulse_amp_scale"]), None),
        ("SAVER_V_MAX", float(lb["v_max"]), None),
        ("SAVER_BACKLIGHT", float(lb["backlight"]), None),
        ("BACKLIGHT_NORMAL", float(bl["normal"]), None),
        ("BACKLIGHT_BOOST", float(bl["boost"]), None),
        ("BACKLIGHT_LOW", float(bl["low_battery"]), None),
        ("BUTTON_LONG_MS", int(tok["input"]["button_long_ms"]), None),
    ])

    # haptics
    pats = hp["patterns"]
    order = tuple(hp["priority"])
    if set(order) != set(pats):
        raise ValueError("haptics.priority and haptics.patterns name different sets")
    rank = {}
    for i, n in enumerate(order):
        rank[n] = len(order) - 1 - i
    # ui-spec §7 groups CLOSER/FARTHER as one priority level
    if "CLOSER" in rank and "FARTHER" in rank:
        rank["FARTHER"] = rank["CLOSER"]
    sec("Haptics (haptics): patterns are on/off ms, starting with on", [
        ("HAPTIC_NAMES", order, "priority order, highest first"),
        ("HAPTIC_PATTERNS", {n: tuple(int(x) for x in pats[n]) for n in order}, None),
        ("HAPTIC_RANK", rank, "higher = more important; CLOSER == FARTHER (ui-spec §7)"),
        ("HAPTIC_MIN_PULSE_MS", int(hp["min_pulse_ms"]), None),
        ("HAPTIC_MIN_GAP_MS", int(hp["min_gap_ms"]), None),
        ("HAPTIC_MAX_DUTY", float(hp["max_duty"]), None),
        ("HAPTIC_BLANKING_MS", int(hp["blanking_ms_after_pulse"]), "accel ignores pulse start .. end + this"),
        ("HAPTIC_MODES", tuple(hp["modes"]), None),
    ])

    # colours
    rgb565 = {}
    rows = []
    for name, ct in col.items():
        rgb565[name] = _check_color(name, ct)
    rows.append(("COLOR_SWAPPED", {n: Hex(_swap(c)) for n, c in rgb565.items()},
                 "byte-swapped RGB565 for framebuf palettes"))
    rows.append(("COLOR_RGB", {n: _rgb(col[n]["hex"]) for n in rgb565}, None))
    key = ("bg.base", "bg.iris", "surface.chip", "surface.toast", "line.subtle", "text.primary",
           "text.secondary", "text.tertiary", "accent.cold", "accent.found", "status.warn",
           "status.critical", "glyph.arrow", "text.onAccent")
    for n in key:
        rows.append((_cname(n), Hex(_swap(rgb565[n])), col[n]["hex"]))
    for n in key:
        rows.append((_cname(n) + "_RGB", _rgb(col[n]["hex"]), None))
    sec("Colours (color): C_* are byte-swapped RGB565 ints, C_*_RGB are (r, g, b)", rows)

    # ramps
    rp = tok["ramp"]
    size = int(rp["lut_size"])
    rows = [("RAMP_NAMES", RAMPS, None), ("RAMP_LUT_SIZE", size, None)]
    rows.append(("RAMP_HEX", {r: tuple(col[t]["hex"] for t in rp[r]) for r in RAMPS}, "8 stops"))
    rows.append(("RAMP_SWAPPED", {r: tuple(Hex(_swap(rgb565[t])) for t in rp[r]) for r in RAMPS}, None))
    rows.append(("RAMP_RGB", {r: tuple(_rgb(col[t]["hex"]) for t in rp[r]) for r in RAMPS}, None))
    rows.append(("RAMP_LUT", {r: _lut([_rgb(col[t]["hex"]) for t in rp[r]], size) for r in RAMPS},
                 "LUT[j] = ramp position j/63*7, byte-swapped"))
    sec("Ramps (ramp)", rows)

    # layout
    ir = lay["iris_radius_px"]

    def rect(r):
        return (r["x"], r["y"], r["w"], r["h"])

    ss = lay["status_strip"]
    sec("Layout (layout): rects are (x, y, w, h)", [
        ("CENTER", tuple(lay["center"]["overlay"]), None),
        ("RING_CENTER", tuple(float(x) for x in lay["center"]["ring_map"]), None),
        ("SAFE_RECT", rect(lay["safe_rect"]), None),
        ("CONTENT_RECT", rect(lay["content_rect"]), None),
        ("TOP_SLOT", rect(lay["top_slot"]), None),
        ("BOTTOM_SLOT", rect(lay["bottom_slot"]), None),
        ("STATUS_STRIP", rect(ss), None),
        ("STATUS_SLOTS", {k: (v["x"], v["w"]) for k, v in ss["slots"].items()}, "(x, w)"),
        ("IRIS_R", {k: int(v) for k, v in ir.items()}, None),
        ("IRIS_R_CHEVRONS", int(ir["chevrons"]), None),
        ("IRIS_R_ARROW", int(ir["arrow"]), None),
        ("IRIS_R_SCAN", int(ir["scan"]), None),
        ("IRIS_R_RUNES", int(ir["runes"]), None),
        ("IRIS_R_SEEKER", int(ir["seeker"]), None),
        ("BEAM_R", int(lay["beam_radius_px"]), None),
        ("SWEEP_R_INNER", int(lay["sweep_band_px"]["r_inner"]), None),
        ("SWEEP_R_OUTER", int(lay["sweep_band_px"]["r_outer"]), None),
        ("RUNE_CENTERS_X", tuple(lay["rune_row"]["centers_x"]), None),
        ("RUNE_CENTER_Y", int(lay["rune_row"]["center_y"]), None),
        ("RUNE_BOX", int(lay["rune_row"]["box_px"]), None),
        ("CORE_DOT_R", int(lay["core_dot"]["r"]), None),
        ("CORE_DOT_LEVEL", float(lay["core_dot"]["level"]), None),
        ("SPACE", tuple(tok["space"][str(i)] for i in range(9)), "space.0 .. space.8"),
        ("RADIUS", {k: v for k, v in tok["radius"].items() if isinstance(v, int)}, None),
        ("STROKE", {k: v for k, v in tok["stroke"].items() if isinstance(v, int)}, None),
    ])

    # typography
    ty = tok["typography"]
    wm = _match(r"A-Z 0-9 and (\S+)", ty["word"]["source"], "typography.word.source")
    lm = _match(r"A-Z 0-9 space and (\S+)", ty["label"]["source"], "typography.label.source")
    az09 = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
    sec("Typography (typography)", [
        ("WORD_MAX_CHARS", int(ty["word"]["max_chars"]), None),
        ("LABEL_MAX_CHARS", int(ty["label"]["max_chars_toast"]), None),
        ("WORD_CHARS", az09 + wm.group(1), "tokens subset (no space)"),
        ("LABEL_CHARS", az09 + " " + lm.group(1), "tokens subset"),
        ("TYPE_CELL", {k: tuple(int(x) for x in ty[k]["cell"].split("x"))
                       for k in ("micro", "label", "numeral", "display", "word")}, "(w, h)"),
    ])

    # motion
    du = mo["duration_ms"]
    use = mo["use"]
    fl = _match(r"more than (\d+) ramp steps in under (\d+) ms", mo["flash_limit"], "motion.flash_limit")
    aa = _match(r"max\(lead_px,\s*([\d.]+)\*\|speed_px_s\|/fps", mo["temporal_aa"], "motion.temporal_aa")
    sec("Motion (motion)", [
        ("FPS_TARGET", int(mo["fps"]["target"]), None),
        ("FPS_MIN", int(mo["fps"]["minimum"]), None),
        ("FPS_LOW_BATTERY", int(mo["fps"]["low_battery"]), None),
        ("DURATION_MS", {k: int(v) for k, v in du.items()}, None),
        ("TOAST_MS", int(du["toast_dwell"]), None),
        ("ZONE_CROSSFADE_MS", int(use["zone_param_crossfade"]["ms"]), None),
        ("HUE_CROSSFADE_MS", int(use["hue_ramp_crossfade"]["ms"]), None),
        ("HUE_CROSSFADE_FOUND_MS", int(use["hue_ramp_crossfade"]["found_ms"]), None),
        ("IRIS_OPEN_MS", int(use["iris_open_close"]["ms"]), None),
        ("ARROW_APPEAR_MS", int(use["arrow_appear"]["ms"]), None),
        ("ARROW_APPEAR_SCALE", float(use["arrow_appear"]["scale_from"]), None),
        ("ARROW_ANGLE_TAU_MS", int(use["arrow_angle"]["tau_ms"]), None),
        ("ARROW_ANGLE_DEADBAND_DEG", float(use["arrow_angle"]["deadband_deg"]), None),
        ("CHEVRON_NUDGE_MS", int(use["chevron_nudge"]["ms"]), None),
        ("CHEVRON_NUDGE_PX", int(use["chevron_nudge"]["offset_px"]), None),
        ("TOAST_IN_MS", int(use["toast_in"]["ms"]), None),
        ("TOAST_IN_PX", int(use["toast_in"]["offset_px"]), None),
        ("TOAST_OUT_MS", int(use["toast_out"]["ms"]), None),
        ("SWEEP_DEG_PER_S", float(use["sweep_rotation"]["deg_per_s"]), None),
        ("FLASH_LIMIT_STEPS", int(fl.group(1)), None),
        ("FLASH_LIMIT_MS", int(fl.group(2)), None),
        ("TEMPORAL_AA_K", float(aa.group(1)), "lead_eff = max(lead, K*|speed|/fps)"),
    ])

    # field
    vs = tuple((int(x), float(y)) for x, y in fld["vignette_stops"])
    sw = fld["standing_wave"]
    m = _match(r"v = ([\d.]+) \+ ([\d.]+)\*\(0\.5\+0\.5cos\(2\*pi\*i/(\d+)\)\)\*\(([\d.]+)\+([\d.]+) "
               r"sin\(2\*pi\*t/(\d+)\)\)", sw, "field.standing_wave")
    gh = _match(r"adds ([\d.]+)\*pulse_amp", fld["ghost_rings"], "field.ghost_rings")
    fb = tok["states"]["FOUND"]["burst"]
    rim = _match(r"iris_r\.\.iris_r\+(\d+)", fld["iris"], "field.iris")
    sec("Ripple field (field)", [
        ("FIELD_R_MAX", vs[-1][0], "ring-map max index; inward rings spawn here"),
        ("VIGNETTE_STOPS", vs, "(index, gain), linear between"),
        ("FADEIN_PX", int(fld["fadein_px"]), None),
        ("GHOST_AMP_SCALE", float(gh.group(1)), None),
        ("IRIS_RIM_PX", int(rim.group(1)) + 1, None),
        ("BURST_AMP", float(fb["amp"]), None),
        ("FOUND_BURST_SPEED_PX_S", float(fb["speed_px_s"]), None),
        ("STANDING_BASE", float(m.group(1)), None),
        ("STANDING_AMP", float(m.group(2)), None),
        ("STANDING_WAVELENGTH_PX", int(m.group(3)), None),
        ("STANDING_BREATHE", (float(m.group(4)), float(m.group(5))), "(0.6 + 0.4 sin)"),
        ("STANDING_PERIOD_MS", int(m.group(6)), None),
    ])

    # glyph geometry
    g = tok["glyphs"]
    runes = tuple((r["name"], _tup(r["prims"])) for r in g["runes"]["set"])
    sec("Glyph geometry (glyphs), relative to CENTER, pointing up", [
        ("DART_PTS", _tup(g["arrow_dart"]["points"]), None),
        ("CHEVRON_UP_PTS", _tup(g["chevron_up"]["points"]), None),
        ("CHEVRON_STACK_PX", int(g["chevron_up"]["stack_offset_px"]), None),
        ("CHECK_PTS", _tup(g["check"]["points"]), None),
        ("CHECK_DISC_R", int(g["check"]["on_disc"]["r"]), None),
        ("SEEKER_RING", (g["seeker"]["ring"]["r"], g["seeker"]["ring"]["stroke"]), "(r, stroke)"),
        ("SEEKER_TICKS", (tuple(g["seeker"]["ticks"]["angles_deg"]), g["seeker"]["ticks"]["r_from"],
                          g["seeker"]["ticks"]["r_to"], g["seeker"]["ticks"]["stroke"]),
         "(angles, r_from, r_to, stroke)"),
        ("TURN_ARC", (g["turn_right"]["arc"]["r"], g["turn_right"]["arc"]["from_deg"],
                      g["turn_right"]["arc"]["to_deg"], g["turn_right"]["arc"]["stroke"]),
         "(r, from_deg, to_deg, stroke)"),
        ("TURN_HEAD_PTS", _tup(g["turn_right"]["head"]), None),
        ("RUNE_STROKE", int(g["runes"]["stroke"]), None),
        ("RUNES", runes, "(name, prims)"),
        ("BATTERY_ICON", (tuple(g["battery_icon"]["body"]), tuple(g["battery_icon"]["nub"]),
                          g["battery_icon"]["stroke"], g["battery_icon"]["inset"]),
         "(body, nub, stroke, inset)"),
        ("LINK_BARS", (g["link_bars"]["w"], g["link_bars"]["gap"], tuple(g["link_bars"]["heights"])),
         "(w, gap, heights)"),
    ])

    for title, rows in SPEC:
        S.append(("ui-spec only: " + title, list(rows)))
    return S


def canonical_hash(tok):
    blob = json.dumps(tok, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(blob).hexdigest()[:16]


def render(tok):
    """Return the text of finder/tuning.py for the parsed tokens dict."""
    out = [
        '"""Tuning constants for the finder logic and renderer.',
        "",
        "GENERATED by tools/gen_tuning.py from docs/design/tokens.json -- DO NOT EDIT.",
        "Regenerate with: python3 tools/gen_tuning.py   (check: --check)",
        "Plain constants only; blocks marked 'ui-spec only' come from ui-spec.md.",
        '"""',
        "",
        "TOKENS_VERSION = %r" % tok["version"],
        "TOKENS_HASH = %r" % canonical_hash(tok),
    ]
    seen = set()
    for title, rows in build(tok):
        out.append("")
        out.append("# ---- %s" % title)
        for name, val, cmt in rows:
            if name in seen:
                raise ValueError("duplicate constant %s" % name)
            seen.add(name)
            line = "%s = %s" % (name, _fmt(val))
            if cmt and "\n" not in line and len(line) + len(cmt) + 5 <= 110:
                line += "  # " + cmt.replace("%%", "%")
            elif cmt:
                out.append("# " + cmt.replace("%%", "%"))
            out.append(line)
    out.append("")
    return "\n".join(out)


def generate(tokens_path=TOKENS):
    with open(tokens_path) as f:
        return render(json.load(f))


def main(args):
    tokens, out, check = TOKENS, OUT, False
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--check":
            check = True
        elif a == "--tokens":
            i += 1
            tokens = args[i]
        elif a == "--out":
            i += 1
            out = args[i]
        else:
            print(__doc__)
            return 2
        i += 1
    text = generate(tokens)
    if check:
        try:
            with open(out) as f:
                cur = f.read()
        except OSError:
            cur = None
        if cur != text:
            print("%s is stale: run python3 tools/gen_tuning.py" % os.path.relpath(out, ROOT))
            return 1
        print("%s is up to date" % os.path.relpath(out, ROOT))
        return 0
    with open(out, "w") as f:
        f.write(text)
    print("wrote %s" % os.path.relpath(out, ROOT))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
