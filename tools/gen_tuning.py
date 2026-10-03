"""Generate finder/tuning.py from docs/design/tokens.json (CPython only).

    python3 tools/gen_tuning.py            # (re)write finder/tuning.py
    python3 tools/gen_tuning.py --check    # exit 1 if finder/tuning.py is stale

tokens.json is the source of truth. A few values the logic and renderer need
exist only in docs/design/ui-spec.md; they live in ``SPEC`` below with their
section reference and are emitted in their own clearly marked blocks. Strings
in tokens.json that carry numbers (intensity map, sigma model, proximity,
FOUND gate, standing wave, ghost rings, iris rim, temporal AA, flash limit,
haptic queue) must match their whole template: if their wording changes this
script fails loudly instead of emitting a stale number.
Tokens that repeat a value (backlight, sweep rate, breathing, crossfade, link
bars, preset wavelengths, FOUND glow_r) must agree.
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
        ("LOGIC_MS", 100, "logic rate: one RenderParams per 100 ms (10 Hz)"),
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
        ("COUNTDOWN_MAX", 99, "2-digit type.display countdown (split 30..0)"),
    )),
    ("Per-screen field extras (ui-spec §6)", (
        ("MENU_PALETTE_SCALE", 0.5, None),
        ("BURST_SPEED_MULT", 2.0, "burst ring speed = 2x zone speed (§4)"),
    )),
    ("Mirror, trend, direction extras (ui-spec §5)", (
        ("MIRROR_EMA_MS", 150, "live mirror RSSI EMA (§5.7)"),
        ("MIRROR_MIN_SPAN_DB", 4.0, "I_mirror denominator floor"),
        ("TREND_START_DB", 3.0, "+1 at >= 3 dB / 8 s starting threshold (§5.5)"),
        ("UNRELIABLE_WINDOW_MS", 5000, "delivery window for 'unreliable' (§5.5)"),
        ("NOISE_EMA_ALPHA", 0.02, "noise_db: EMA of |own RSSI step| (§5.5)"),
        ("NOISE_PAIR_GAP_MS", 1000, "own packets further apart don't form a pair (§5.5)"),
        ("DIRECTION_REVEAL_MS", 1500, None),
        ("DIRECTION_CLOCK_MAX_SIGMA_DEG", 30.0, "clock-hour word if sigma <= 30"),
        ("DIRECTION_AHEAD_DEG", 20.0, "|theta| <= 20 -> AHEAD, turn skipped"),
        ("DIRECTION_LOCK_EASE_MS", 300, None),
        ("DIRECTION_WALK_WORD_MS", 3000, None),
        ("ARROW_EXPIRE_MS", 600, None),
        ("PACER_TICK_DEG", 45.0, None),
    )),
    ("Scan extras (ui-spec §6 SCANNING)", (
        ("SCAN_FLAT_DEG", 20.0, "face-up within 20 deg ..."),
        ("SCAN_FLAT_HOLD_MS", 500, "... held 0.5 s"),
        ("SCAN_WALK_STEPS", 3, ">= 3 steps in 2 s pauses the sweep"),
        ("SCAN_WALK_WINDOW_MS", 2000, None),
        ("SCAN_TICK_DEG", 45.0, "TICK every 45 deg, DOUBLE at 180"),
        ("SCAN_BLINK_MS", 200, None),
        ("SCAN_PEER_WALK_WIDEN_MS", 2000, "peer walking > 2 s of sweep: +15 deg to s0"),
        ("SCAN_PEER_WALK_WIDEN_DEG", 15.0, None),
        ("SCAN_PEER_WALK_FAIL_MS", 4000, "> 4 s: no fix, FRIEND MOVED"),
        ("SCAN_HOLD_REPEAT_MS", 3000, "partner HOLD haptic repeat"),
        ("SCAN_HOLD_REPEAT_MAX", 3, None),
    )),
    ("Pairing, found, battery, power, input (ui-spec §6, §8)", (
        ("PAIR_SPLIT_S", 30, None),
        ("PAIR_GO_MS", 1000, "split: GO shown 1 s at 0"),
        ("CAL_GATE_WINDOW_MS", 1000, "RSSI sd over 1 s > unstable_sd pauses the fill"),
        ("SEARCHING_WALK_ABOUT_MS", 45000, None),
        ("BUMP_TOUCH_GUARD_MS", 300, "ignore taps 300 ms after a screen touch"),
        ("BUMP_TOUCH_LEAD_MS", 100, "... and from 100 ms before its touch-down (§8)"),
        ("BUMP_SPIKE_G", 2.5, "gravity-removed |a| above this (§6 HOT)"),
        ("BUMP_SPIKE_MS", (10, 20), "spike run length min..max"),
        ("BUMP_REFRACTORY_MS", 200, None),
        ("BUMP_READY_HOLD_MS", 1500, "band <3 held 1.5 s"),
        ("BUMP_READY_BAND", 0, "index of '<3'"),
        ("HOT_SCAN_PRESS_MS", 1000, "HOT: 2nd short press within this starts a scan (§8)"),
        ("FOUND_CELEBRATE_MS", 2000, None),
        ("PARTNER_LEFT_MS", 2000, "partner in PAIRING this long: it left (§6 MENU)"),
        ("BATT_SHUTDOWN_PCT", 3, None),
        ("BATT_INTERSTITIAL_MS", 2500, None),
        ("BATT_SCREEN_OFF_MS", 3000, "5 %: screen off 3 s after lowering (LOW-BATTERY)"),
        ("BYE_WORD_MS", 2000, None),
        ("GOODBYE_BEACONS", 3, None),
        ("GOODBYE_GRACE_MS", 1000, "power off this long after the BYE word (§6 LOW-BATTERY)"),
        ("LOST_TIMER_MAX_S", 599, "m:ss up to 9:59, then 10M+"),
        ("LOST_HINT_AFTER_MS", 20000, "GO BACK / KEEP ON"),
        ("WAKE_BOOST_MS", 3000, None),
        ("WRIST_DOWN_MS", 2000, None),
        ("IDLE_DIM_MS", 30000, None),
        ("IDLE_DIM_BACKLIGHT", 0.35, "ui-spec §8: face-up > 30 s with no input"),
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
    )),
)


# ---- helpers ---------------------------------------------------------------


def _match(pat, text, what):
    """``pat`` must match the whole (stripped) token string."""
    m = re.fullmatch(pat, text.strip())
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
    return v


def _cname(token):
    return "C_" + token.replace(".", "_").upper()


def _fmt(v, ind=0):
    if v is None:
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
    ])

    # tempo
    zt = th["zone_tempo"]
    per = tuple(int(zt[n]["period_ms"]) for n in ZONES)
    spd = tuple(float(zt[n]["speed_px_s"]) for n in ZONES)
    sec("Zone tempo (thresholds.zone_tempo), index = zone", [
        ("ZONE_PERIOD_MS", per, "ring spawn = heartbeat base period"),
        ("ZONE_SPEED_PX_S", spd, None),
        ("ZONE_LEAD_PX", tuple(int(zt[n]["lead_px"]) for n in ZONES), None),
        ("ZONE_TRAIL_PX", tuple(int(zt[n]["trail_px"]) for n in ZONES), None),
        ("ZONE_HEARTBEAT", tuple(zt[n]["heartbeat"] for n in ZONES), None),
        ("ZONE_HB_EVERY", tuple(int(zt[n]["every"]) for n in ZONES), "play on every Nth live ring"),
    ])

    # intensity map
    im = fld["intensity_map"]
    lin = r"([-\d.]+)\s*\+\s*([-\d.]+)\s*\*\s*I"
    rows = []
    for key, name in (("floor", "FLOOR"), ("glow_amp", "GLOW_AMP"), ("pulse_amp", "PULSE_AMP"),
                      ("glow_r_px", "GLOW_R")):
        found = key == "glow_r_px"
        m = _match(lin + (r"\s*\(FOUND\s+([\d.]+)\)" if found else ""), im[key],
                   "field.intensity_map." + key)
        rows.append(("%s_A" % name, float(m.group(1)), None))
        rows.append(("%s_B" % name, float(m.group(2)), "%s = A + B*I" % key))
    glow_r_found = float(m.group(3))    # m is glow_r_px's, the last key
    rows.append(("GLOW_R_FOUND", glow_r_found, None))
    sec("Intensity map (field.intensity_map)", rows)

    # proximity
    pr = th["proximity"]
    m = _match(r"p\s*=\s*clamp01\(ln\(([\d.]+)/max\(d,\s*([\d.]+)\)\)/ln\(([\d.]+)\)\)", pr["formula"],
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
        ("CAL_WINDOW_MS", int(c["window_ms"]), None),
        ("P1M_NOMINAL_DBM", float(c["p1m_nominal_dbm"]), None),
        ("CAL_CLAMP_DB", float(c["clamp_db"]), "clamp to nominal +- this"),
        ("PATH_LOSS_N", float(c["n"]), None),
        ("PATH_LOSS_N_INDOOR", float(c["n_indoor"]), None),
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
    sm = _match(r"sqrt\(s0\^2 \+ \(([\d.]+)\*turn\)\^2 \+ \(([\d.]+)\*steps\)\^2 \+ "
                r"\(([\d.]+)\*still_s\)\^2\) \+ ([\d.]+)\*colder_hits "
                r"\(\+([\d.]+) display if unreliable\)", a["sigma_model"], "thresholds.arrow.sigma_model")
    tiers = a["tiers_deg"]
    tier_names = ("solid_a", "solid_b", "outline")
    sec("Direction arrow and sigma model (thresholds.arrow)", [
        ("ARROW_BORN_MAX_SIGMA_DEG", float(a["born_max_sigma_deg"]), None),
        ("ARROW_STYLES", tier_names, None),
        ("ARROW_TIER_MAX_DEG", tuple(float(tiers[n]) for n in tier_names), "style = first tier >= cone"),
        ("ARROW_HIDE_SIGMA_DEG", float(tiers["outline"]), "hidden above this"),
        ("ARROW_MAX_AGE_MS", int(a["max_age_ms"]), None),
        ("SIGMA_TURN_K", float(sm.group(1)), "deg per deg turned"),
        ("SIGMA_STEP_K", float(sm.group(2)), "deg per step"),
        ("SIGMA_STILL_K", float(sm.group(3)), "deg per still second"),
        ("SIGMA_COLDER_HIT_DEG", float(sm.group(4)), None),
        ("SIGMA_UNRELIABLE_DEG", float(sm.group(5)), "display only"),
        ("SCAN_FLOOR_DEG", float(a["scan_floor_deg"]), "s0 = sqrt(sigma_fit^2 + floor^2)"),
        ("COLDER_HIT_MS", int(a["colder_hit_ms"]), None),
        ("COLDER_HIT_EVERY_MS", int(a["colder_hit_every_ms"]), None),
        ("ARROW_RELINK_RESTORE_MS", int(a["relink_restore_ms"]), None),
    ])

    # scan / probe
    s = th["scan"]
    if float(mo["use"]["sweep_rotation"]["deg_per_s"]) != float(s["deg_per_s"]):
        raise ValueError("tokens.json motion.use.sweep_rotation.deg_per_s != thresholds.scan.deg_per_s")
    if (int(s["ready_ms"]) != 3000 or int(s["bins"]) != 12
            or int(s["duration_ms"]) * float(s["deg_per_s"]) != 360000):
        raise ValueError("tokens.json thresholds.scan: ui-spec §6 fixes ready_ms 3000 (countdown 3 2 1), "
                         "bins 12 (30 deg each; the renderer draws 12) and duration_ms x deg_per_s = 360 deg")
    sec("Scan and walk probe (thresholds.scan, thresholds.probe)", [
        ("SCAN_DURATION_MS", int(s["duration_ms"]), None),
        ("SCAN_READY_MS", int(s["ready_ms"]), None),
        ("SCAN_DEG_PER_S", float(s["deg_per_s"]), None),
        ("PACER_DEG_PER_S", float(s["deg_per_s"]), "DIRECTION turn pacer, the scan's rate"),
        ("SCAN_MIN_P2T_DB", float(s["min_peak_to_trough_db"]), "no fix if 2*a1 < this"),
        ("SCAN_MAX_S0_DEG", float(s["max_s0_deg"]), None),
        ("SCAN_TILT_FAULT_DEG", float(s["tilt_fault_deg"]), None),
        ("SCAN_TILT_FAULT_MS", int(s["tilt_fault_ms"]), None),
        ("SCAN_ABORT_STEPS", int(s["abort_steps"]), None),
        ("SCAN_ABORT_PAUSE_MS", int(s["abort_pause_ms"]), None),
        ("SCAN_BINS", int(s["bins"]), None),
        ("SCAN_MIN_PACKETS_PER_BIN", int(s["min_packets_per_bin"]), None),
        ("PROBE_SIGMA_MIN_DEG", float(th["probe"]["sigma_min_deg"]), None),
    ])

    # link / beacons / battery
    lk = th["link"]
    bh = th["beacon_hz"]
    bp = th["battery_pct"]
    sec("Link, beacons, battery (thresholds.link, beacon_hz, battery_pct)", [
        ("LINK_LOST_AFTER_MS", int(lk["lost_after_ms"]), None),
        ("RELINK_PACKETS", int(lk["relink_packets"]), "also the SEARCHING exit rule"),
        ("RELINK_WINDOW_MS", int(lk["relink_window_ms"]), None),
        ("LIVE_WINDOW_MS", int(lk["live_window_ms"]), "ring_live = packet in this window"),
        ("BEACON_HZ_NORMAL", int(bh["normal"]), None),
        ("BEACON_HZ_HOT", int(bh["hot"]), None),
        ("BEACON_HZ_SCAN", int(bh["scan"]), None),
        ("BEACON_HZ_SAVER", int(bh["saver"]), None),
        ("BATT_WARN_PCT", int(bp["warn"]), None),
        ("BATT_CRITICAL_PCT", int(bp["critical"]), None),
        ("BATT_BANNER_PCT", int(bp["banner"]), None),
    ])

    # FOUND gate (hard rule 10)
    fg = _match(r"bump: both accelerometer bump spikes within (\d+) ms while both in HOT; fallback both "
                r"short-press within (\d+) s with band <= (\S+)", th["found"]["requires"],
                "thresholds.found.requires")
    sec("FOUND gate (thresholds.found)", [
        ("BUMP_WINDOW_MS", int(fg.group(1)), "both bump spikes within this"),
        ("FALLBACK_PRESS_WINDOW_MS", int(fg.group(2)) * 1000, None),
        ("FALLBACK_MAX_BAND", list(b["labels"]).index(fg.group(3)), "band <= '%s'" % fg.group(3)),
    ])

    # saver / power
    lb = tok["states"]["LOW_BATTERY"]["own_le_10pct"]
    bl = tok["power"]["backlight"]
    if float(bl["low_battery"]) != float(lb["backlight"]):
        raise ValueError("tokens.json power.backlight.low_battery != "
                         "states.LOW_BATTERY.own_le_10pct.backlight")
    sec("Saver and backlight (states.LOW_BATTERY, power.backlight)", [
        ("SAVER_FPS", int(lb["fps"]), None),
        ("SAVER_PULSE_SCALE", float(lb["pulse_amp_scale"]), None),
        ("SAVER_V_MAX", float(lb["v_max"]), None),
        ("SAVER_BACKLIGHT", float(lb["backlight"]), None),
        ("BACKLIGHT_NORMAL", float(bl["normal"]), None),
        ("BACKLIGHT_BOOST", float(bl["boost"]), None),
        ("BUTTON_LONG_MS", int(tok["input"]["button_long_ms"]), None),
    ])

    # field presets of the screens without a zone (FAR..HOT: zone_tempo + intensity map)
    st = tok["states"]

    def preset(name):
        p = st[name]
        i = p["intensity"]
        if abs(float(p["wavelength_px"]) - abs(float(p["speed_px_s"])) * int(p["period_ms"]) / 1000.0) > 1.0:
            raise ValueError("tokens.json states.%s.wavelength_px != |speed_px_s| * period_ms" % name)
        return (p["ramp"], None if i is None else float(i), float(p["speed_px_s"]),
                int(p["period_ms"]), float(p["glow_r"]), float(p["pulse_amp"]))

    sc = st["SCANNING"]

    sec("Screen field presets (states): (ramp, I, speed_px_s, period_ms, glow_r_px, pulse_amp)", [
        ("FIELD_PAIRING_LOOKING", preset("PAIRING"), "inward rings"),
        ("FIELD_SEARCHING", preset("SEARCHING"), "inward rings"),
        ("FIELD_LINK_LOST", preset("LINK_LOST"), "I frozen at last value"),
        ("PAIRING_SEEN_BREATHE_AMP", tuple(float(x) for x in st["PAIRING"]["glow_amp"]), None),
        ("PAIRING_SEEN_FLOOR", float(st["PAIRING"]["seen_floor"]), None),
        ("SEARCHING_GLOW_AMP", float(st["SEARCHING"]["glow_amp"]), None),
        ("FIELD_LEAD_TRAIL_PX", (int(st["SEARCHING"]["lead_px"]), int(st["SEARCHING"]["trail_px"])),
         "rings outside FAR..HOT"),
        ("FOUND_LEAD_TRAIL_PX", (int(st["FOUND"]["lead_px"]), int(st["FOUND"]["trail_px"])),
         "FOUND burst ring"),
        ("FIELD_SCAN_READY_GLOW_R", float(sc["glow_r"]), None),
        ("FIELD_SCAN_READY_PULSE_SCALE", float(sc["ready_pulse_scale"]), "ready: pulse_amp x this"),
        ("FIELD_SCAN_SWEEP_GLOW_AMP", tuple(float(x) for x in sc["sweep_glow_amp"]),
         "glow_amp = a + b*I_mirror"),
        ("FIELD_SCAN_SWEEP_GLOW_R", float(sc["sweep_glow_r"]), "halo outside the iris"),
        ("FOUND_PERIOD_MS", int(st["FOUND"]["period_ms"]),
         "RenderParams pulse_period_ms in FOUND (no rings travel)"),
    ])

    # haptics
    pats = hp["patterns"]
    order = tuple(hp["priority"])
    if set(order) != set(pats):
        raise ValueError("haptics.priority and haptics.patterns name different sets")
    rank = {}
    for i, n in enumerate(order):
        rank[n] = len(order) - 1 - i
    rank["FARTHER"] = rank["CLOSER"]    # ui-spec §7: one priority level (KeyError if renamed)
    q = _match(r"an event replaces the next heartbeat pulse; heartbeats resume (\d+) s after the "
               r"event; drop an event only if one of same or higher priority started < (\d+) s earlier "
               r"\(a TICK only for a higher priority\); while a higher event plays one lower event "
               r"waits \(latest wins\)",
               hp["queue"], "haptics.queue")
    sec("Haptics (haptics): patterns are on/off ms, starting with on", [
        ("HAPTIC_NAMES", order, "priority order, highest first"),
        ("HAPTIC_PATTERNS", {n: tuple(int(x) for x in pats[n]) for n in order}, None),
        ("HAPTIC_RANK", rank, "higher = more important; CLOSER == FARTHER (ui-spec §7)"),
        ("HAPTIC_MIN_PULSE_MS", int(hp["min_pulse_ms"]), None),
        ("HAPTIC_MIN_GAP_MS", int(hp["min_gap_ms"]), None),
        ("HAPTIC_MAX_DUTY", float(hp["max_duty"]), None),
        ("HAPTIC_BLANKING_MS", int(hp["blanking_ms_after_pulse"]), "accel ignores pulse start .. end + this"),
        ("HAPTIC_HB_RESUME_MS", int(q.group(1)) * 1000, "heartbeats resume this long after an event"),
        ("HAPTIC_EVENT_GUARD_MS", int(q.group(2)) * 1000, "drop an event if >= priority started < this ago"),
    ])

    # colours
    rgb565 = {}
    rows = []
    for name, ct in col.items():
        rgb565[name] = _check_color(name, ct)
    key = ("bg.base", "bg.iris", "surface.chip", "surface.toast", "line.subtle", "text.primary",
           "text.secondary", "text.tertiary", "accent.cold", "accent.found", "status.warn",
           "status.critical")
    for n in key:
        rows.append((_cname(n), Hex(_swap(rgb565[n])), col[n]["hex"]))
    sec("Colours (color): byte-swapped RGB565 ints for framebuf palettes", rows)

    # ramps
    rp = tok["ramp"]
    size = int(rp["lut_size"])
    rows = [("RAMP_NAMES", RAMPS, None)]
    rows.append(("RAMP_SWAPPED", {r: tuple(Hex(_swap(rgb565[t])) for t in rp[r]) for r in RAMPS}, "8 stops"))
    rows.append(("RAMP_LUT", {r: _lut([_rgb(col[t]["hex"]) for t in rp[r]], size) for r in RAMPS},
                 "LUT[j] = ramp position j/63*7, byte-swapped"))
    sec("Ramps (ramp)", rows)

    # layout
    ir = lay["iris_radius_px"]

    def rect(r):
        return (r["x"], r["y"], r["w"], r["h"])

    sec("Layout (layout): rects are (x, y, w, h)", [
        ("CENTER", tuple(lay["center"]["overlay"]), None),
        ("TOP_SLOT", rect(lay["top_slot"]), None),
        ("BOTTOM_SLOT", rect(lay["bottom_slot"]), None),
        ("IRIS_R", {k: int(v) for k, v in ir.items()}, None),
        ("BEAM_R", int(lay["beam_radius_px"]), None),
        ("SWEEP_R_INNER", int(lay["sweep_band_px"]["r_inner"]), None),
        ("SWEEP_R_OUTER", int(lay["sweep_band_px"]["r_outer"]), None),
        ("RUNE_CENTERS_X", tuple(lay["rune_row"]["centers_x"]), None),
        ("CORE_DOT_R", int(lay["core_dot"]["r"]), None),
        ("CORE_DOT_LEVEL", float(lay["core_dot"]["level"]), None),
    ])

    # typography
    ty = tok["typography"]
    sec("Typography (typography)", [
        ("WORD_MAX_CHARS", int(ty["word"]["max_chars"]), None),
        ("LABEL_MAX_CHARS", int(ty["label"]["max_chars_toast"]), None),
        ("WORD_CHARS", ty["word"]["chars"], None),
        ("LABEL_CHARS", ty["label"]["chars"], None),
    ])

    # motion
    du = mo["duration_ms"]
    use = mo["use"]
    fl = _match(r"No full-field luminance change of more than (\d+) ramp steps in under (\d+) ms "
                r"\(stay below 3 flashes/s\)\.", mo["flash_limit"], "motion.flash_limit")
    aa = _match(r"lead_eff_px = max\(lead_px, ([\d.]+)\*\|speed_px_s\|/fps_measured\); ring profiles "
                r"evaluated at fractional r_k", mo["temporal_aa"], "motion.temporal_aa")
    if int(du["breathe_pairing"]) != int(st["PAIRING"]["glow_breathe_ms"]):
        raise ValueError("tokens.json motion.duration_ms.breathe_pairing != states.PAIRING.glow_breathe_ms")
    if int(du["hue_crossfade"]) != int(use["hue_ramp_crossfade"]["ms"]):
        raise ValueError("tokens.json motion.duration_ms.hue_crossfade != motion.use.hue_ramp_crossfade.ms")
    sec("Motion (motion)", [
        ("FPS_TARGET", int(mo["fps"]["target"]), None),
        ("BREATHE_PAIRING_MS", int(du["breathe_pairing"]), "PAIRING seen halo breathing"),
        ("TOAST_MS", int(du["toast_dwell"]), None),
        ("ZONE_CROSSFADE_MS", int(use["zone_param_crossfade"]["ms"]), None),
        ("HUE_CROSSFADE_MS", int(use["hue_ramp_crossfade"]["ms"]), None),
        ("HUE_CROSSFADE_FOUND_MS", int(use["hue_ramp_crossfade"]["found_ms"]), None),
        ("IRIS_ANIM_MS", int(use["iris_open_close"]["ms"]), None),
        ("SCAN_MORPH_MS", int(use["scan_result_morph"]["ms"]), None),
        ("ARROW_APPEAR_MS", int(use["arrow_appear"]["ms"]), None),
        ("ARROW_APPEAR_SCALE", float(use["arrow_appear"]["scale_from"]), None),
        ("ARROW_ANGLE_TAU_MS", int(use["arrow_angle"]["tau_ms"]), None),
        ("ARROW_ANGLE_DEADBAND_DEG", float(use["arrow_angle"]["deadband_deg"]), None),
        ("CHEVRON_NUDGE_MS", int(use["chevron_nudge"]["ms"]), None),
        ("CHEVRON_NUDGE_PX", int(use["chevron_nudge"]["offset_px"]), None),
        ("TOAST_IN_MS", int(use["toast_in"]["ms"]), None),
        ("TOAST_IN_PX", int(use["toast_in"]["offset_px"]), None),
        ("TOAST_OUT_MS", int(use["toast_out"]["ms"]), None),
        ("FLASH_LIMIT_STEPS", int(fl.group(1)), None),
        ("FLASH_LIMIT_MS", int(fl.group(2)), None),
        ("TEMPORAL_AA_K", float(aa.group(1)), "lead_eff = max(lead, K*|speed|/fps)"),
    ])

    # field
    vs = tuple((int(x), float(y)) for x, y in fld["vignette_stops"])
    sw = fld["standing_wave"]
    m = _match(r"FOUND: v = ([\d.]+) \+ ([\d.]+)\*\(0\.5\+0\.5cos\(2\*pi\*i/(\d+)\)\)\*\(([\d.]+)\+([\d.]+) "
               r"sin\(2\*pi\*t/(\d+)\)\), glow_r ([\d.]+)", sw, "field.standing_wave")
    if not float(m.group(7)) == float(st["FOUND"]["glow_r"]) == glow_r_found:
        raise ValueError("tokens.json field.standing_wave glow_r, states.FOUND.glow_r and "
                         "field.intensity_map glow_r_px FOUND disagree")
    if int(mo["duration_ms"]["breathe_found"]) != int(m.group(6)):
        raise ValueError("tokens.json motion.duration_ms.breathe_found != field.standing_wave period")
    gh = _match(r"outward ring \(speed_px_s > 0\) spawned with ring_live=False adds "
                r"([\d.]+)\*pulse_amp to a grey channel; "
                r"colour = LUT\.grey where v_ghost > v_green; no haptic", fld["ghost_rings"],
                "field.ghost_rings")
    fb = st["FOUND"]["burst"]
    rim = _match(r"indices < iris_r paint bg\.iris; indices iris_r\.\.iris_r\+(\d+) are the rim at level "
                 r"min\(7, max\((\d+), floor\+glow_amp\)\); when an iris is open the center glow becomes a halo: "
                 r"glow term uses \(i - iris_r\) instead of i", fld["iris"], "field.iris")
    sec("Ripple field (field)", [
        ("FIELD_R_MAX", vs[-1][0], "ring-map max index; inward rings spawn here"),
        ("VIGNETTE_STOPS", vs, "(index, gain), linear between"),
        ("FADEIN_PX", int(fld["fadein_px"]), None),
        ("GHOST_AMP_SCALE", float(gh.group(1)), None),
        ("IRIS_RIM_PX", int(rim.group(1)) + 1, None),
        ("IRIS_RIM_MIN_LEVEL", float(rim.group(2)), "rim level min(7, max(this, floor+glow_amp))"),
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
    bars = g["link_bars"]
    if int(bars["count"]) != len(bars["heights"]):
        raise ValueError("tokens.json glyphs.link_bars.count != len(heights)")
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
        ("LINK_BARS", (bars["w"], bars["gap"], tuple(bars["heights"])), "(w, gap, heights)"),
        ("LINK_Q_MAX", int(bars["count"]), "status link bars 0..count"),
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
                line += "  # " + cmt
            elif cmt:
                out.append("# " + cmt)
            out.append(line)
    out.append("")
    return "\n".join(out)


def generate():
    with open(TOKENS) as f:
        return render(json.load(f))


def main(args):
    if args not in ([], ["--check"]):
        print(__doc__)
        return 2
    text = generate()
    rel = os.path.relpath(OUT, ROOT)
    if args:
        try:
            with open(OUT) as f:
                cur = f.read()
        except OSError:
            cur = None
        if cur != text:
            print("%s is stale: run python3 tools/gen_tuning.py" % rel)
            return 1
        print("%s is up to date" % rel)
        return 0
    with open(OUT, "w") as f:
        f.write(text)
    print("wrote %s" % rel)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
