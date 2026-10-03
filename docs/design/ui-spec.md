# Sheikah Finder UI specification (v0.2)

This is the final screen specification for the two-watch finder game on the LILYGO T-Watch 2020: a 240×240 ST7789 panel, RGB565 colour, one side button, a vibration motor and a BMA423 accelerometer with no gyro and no compass.

It builds on these documents:

- [`design-system.md`](./design-system.md) and [`tokens.json`](./tokens.json), both v0.2.0. Their tokens were updated for this spec (see §10). If this document and `tokens.json` disagree on a value, `tokens.json` wins.
- [`../research/user-research.md`](../research/user-research.md), which defines requirements R-01 to R-15.

All pixel values are on the 240×240 physical display: origin at the top left, y pointing down, overlay centre C = (120,120). Angles are in degrees clockwise from screen-up. Every distance, RSSI and timing threshold is a **starting value to calibrate** on real watches, not a measurement.

---

## 0. Decision: base concept and grafts

**Base: RUNEWELL**, the "Sheikah-sensor homage" concept. It scored 7/10 overall, tied with BLIP HUNT and ahead of PINPOINT at 6.5.

- **Why RUNEWELL:** the tie is broken by the one constraint that cannot be designed around. RUNEWELL scored 7.5 on render feasibility, against 8 for PINPOINT and 5 for BLIP HUNT. It also scored 8 on fun, the same as BLIP HUNT, which is the other thing this user asked for.
- **Fit to the brief:** it matches the brief almost literally. Glow and ripples come from the middle, brighten and speed up as you get closer, and an arrow appears in the centre once direction is known.
- **Why not BLIP HUNT:** it makes an 8-bit numeral the hero and assumes a 25–30 fps PSRAM pipeline that the flashed firmware does not have.
- **RUNEWELL's weak spots:** glanceability (5) and consistency (6). Both are layout and discipline problems, and the design system's fixed slots and chips solve them.

**What was grafted in**

| From | Idea | Where it lands |
|---|---|---|
| PINPOINT | Fixed places: status at the top, answer in the centre, words at the bottom. Nothing moves between states | §2 grid |
| PINPOINT | Cone width = σ, and wording that gets coarser as precision drops (clock hour → 4-way word → nothing) | DIRECTION |
| PINPOINT | Live signal mirror: the halo follows instant RSSI during the scan and the turn | SCANNING, DIRECTION-TURN |
| PINPOINT | Paced turn-to-face, using the scan's own wedge and ticks | DIRECTION-TURN |
| BLIP HUNT | "Pings are real": a bright ring only when packets arrived, otherwise a grey ghost ring and no haptic | §4.6 |
| BLIP HUNT | Physical bump to win. RSSI alone never declares FOUND | HOT, FOUND |
| BLIP HUNT | Partner flags in beacons (scanning, low battery, goodbye) | SCANNING, LINK-LOST, LOW-BATTERY |
| BLIP HUNT | Real pings during the split-up countdown | PAIRING → SPLIT |
| RUNEWELL | Inward rings mean "listening / no data"; outward rings mean "live signal" | SEARCHING, LINK-LOST, PAIRING |
| RUNEWELL | Standing wave for FOUND ("arrived", not "travelling") | FOUND |
| RUNEWELL | Arrow lifecycle: earned → faced → ages → stale → gone | DIRECTION |

Dropped on purpose:

- RUNEWELL's baked rune rings, bead ring and echo ring. They were below visual acuity and sat under text.
- All on-screen zone words (FAR / NEAR / WARM / HOT). They clashed with WARMER / COLDER.
- BLIP HUNT's score, streaks and heat meter. They broke "one hero per screen" and could be farmed by noise.
- Every full-field flash.

The table in §9 maps each critical and major critique finding to its fix.

---

## 1. Principles

1. **The centre is the answer.** Everything needed to play sits inside the iris (r ≤ 64, a 128 px disc): glow, chevrons, arrow, countdown and check. Words appear only in two fixed slots (top y 12–35, bottom y 186–225), always on opaque chips. Nothing slides in from the edges, and nothing changes place between states.
2. **Tempo is proximity, felt and seen.** There are four zones, and each has one ring tempo and one haptic rhythm, locked to the ring spawn. Players learn four levels, not a continuum. Brightness and glow radius follow the estimate continuously *within* a zone, so walking the right way visibly warms the screen before the zone changes.
3. **Direction is earned, shows its doubt, and ages.** No arrow appears without a completed scan. The cone half-angle is σ. The arrow is relative to where you faced, never north. σ grows with steps and standing time until the arrow goes hollow and then disappears.
4. **Show only what the radio knows.** Distance is shown only as bands (`<3`, `~5`, `~10`, `~20`, `~40`, `60+`). Outward bright rings mean packets are arriving. Inward rings, grey rings and silence mean no data. Stale data is always grey and always shows its age.
5. **Readable in a 1–2 s glance in sun.** There is a sun floor: a bright core or a bright iris rim is always present. Ring crests are at least `prox.4` in FAR. Anything read while walking is 32 px tall. Text never sits on the live field.
6. **Radial first, calm always.** Proximity effects are palette-only. Outside SCANNING, each frame adds at most 3 polygons and 2 text chips. Motion is time-based and temporally anti-aliased at the real 15–20 fps. There are no full-field flashes. This is the direct fix for the old flickering outline-circle UI.
7. **Evoke, don't replicate.** Green glow and ripple pulses in an original geometric language: no eye emblem, no Sheikah, Hylian or Zonai script, no Nintendo fonts or chrome. "Sheikah" is the project's codename, not on-screen text.

---

## 2. Screen grid and shared elements

```
 x: 0   12  24                                   215 227 240
 y:0 +-------------------------------------------------------+
     |                                                       |
  12 |  [ TOP SLOT  x12..227, y12..35 ]  StatusStrip | hint chip
  35 |                                                       |
  56 |                    .----------.   iris top (r64)      |
     |          ripples  /  CENTRE    \  ripples              |
 120 |                  |  (120,120)   |   iris r 0/44/64/92   |
     |                   \  glyph     /                      |
 184 |                    '----------'   iris bottom (r64)   |
 186 |     [ BOTTOM SLOT x24..215, y186..225 ]  readout | word | toast | banner
 225 |                                                       |
 240 +-------------------------------------------------------+
```

| Element | Geometry | Tokens | Notes |
|---|---|---|---|
| RippleField | Whole screen, ring map centred (119.5,119.5) | `ramp.green / gold / grey`, `field.*` | Params in §4 |
| Core dot (sun floor) | Disc r 6 at C when the iris is closed | level 6 (`prox.6`) | Always on in FAR–HOT with no iris open (R-02: bright centre dot of 12 px or more) |
| Iris | Disc at C, r 44 (chevrons, seeker), 64 (arrow, scan, countdown), 92 (runes) | `bg.iris`; rim 3 px at level `max(5, floor+glow_amp)` | Opens and closes over 300 ms `out_cubic`. With the iris open, the glow becomes a halo outside it |
| Top slot | x 12–227, y 12–35 | chips `surface.chip`, `radius.md` | Holds the StatusStrip (y 12–31) **or** one hint chip (label, up to 18 chars). Never both |
| Bottom slot | x 24–215, y 186–225 | `surface.chip` / `surface.toast` | Priority: banner > toast > word > readout |
| Readout pill | h 40, centred on x 120, y 186–225 | numeral `type.numeral` in `text.secondary`, suffix `M` in `type.label` | Width = 12 + [trend mark 12 + 6] + 16·n + 2 + 8 + 12. `~20` gives 82 px (x 79–161) |
| Word | Pill h 40, centred, text y 190–221 | `type.word` (16×32, new) in `text.primary` unless noted | At most 10 chars (160 px + 24 padding). Used for anything read while moving |
| Toast / banner | Full bottom slot, `radius.lg`, 2 px border | `type.label`, up to 18 chars | Toast 2.5 s. Banner stays until resolved |

**Layers, bottom to top:** field → iris → beam or sweep wedge → glyph keyline → glyph → top chip → bottom chip → debug.

**Text rule:** nothing is printed on the field. Every chip is opaque, so contrast is at least 4.8:1 (tokens `contrast_wcag`).

---

## 3. Render params contract

The game logic outputs one `RenderParams` per frame. The watch renderer (`MicroPython`) and the web simulator (`JS` canvas, or the same Python run through `tools/mpy`) consume *only* this object. They never read estimator internals. The logic runs at 10 Hz and the renderer interpolates. The object must be JSON-serialisable, so it can be logged to JSONL (R-08) and replayed in the simulator.

```python
from collections import namedtuple

RenderParams = namedtuple("RenderParams", (
    # --- screen ---
    "t_ms",            # int, monotonic ms
    "screen",          # str: PAIRING SEARCHING FAR NEAR WARM HOT FOUND SCANNING LINK_LOST MENU
    "sub",             # str|None: phase within the screen (see table)
    "zone",            # int 0..3 (FAR..HOT) = current, or last known in LINK_LOST; None before first fix
    # --- ripple field ---
    "ramp",            # "green" | "gold" | "grey"
    "intensity",       # float 0..1, continuous proximity (smoothed); frozen in LINK_LOST
    "speed_px_s",      # float -60..240; negative = inward ("listening") rings; 0 = no travelling rings
    "pulse_period_ms", # int 400..4000; ring spawn period = haptic heartbeat base period
    "wavelength_px",   # float 0..140; == abs(speed_px_s) * pulse_period_ms / 1000 (±1 px)
    "glow_r_px",       # float 0..96; centre glow (or halo outside the iris) radius
    "ring_live",       # bool: >= 1 packet in the last 1000 ms. False -> next ring is a grey ghost with no haptic
    "burst",           # bool: spawn one extra bright ring this frame (zone closer, FOUND, relink)
    # --- centre glyph ---
    "glyph",           # "glow"|"seeker"|"chevrons"|"arrow"|"countdown"|"turn"|"check"|"runes"|"battery"
    "arrow_deg",       # float 0..360 or None; clockwise from screen-up
    "cone_deg",        # float 12..60 or None; beam half-angle = sigma (clamped for drawing)
    "arrow_style",     # "solid_a"|"solid_b"|"outline"|None  (tiers from cone_deg)
    "trend",           # int -1|0|+1 (+1 = warmer)
    "trend_strong",    # bool: draw 2 chevrons instead of 1
    "countdown",       # int|None: digit for the countdown glyph
    # --- text slots ---
    "dist_band",       # "<3"|"~5"|"~10"|"~20"|"~40"|"60+"|None
    "dist_stale",      # bool: draw the band grey with a LAST prefix (LINK_LOST)
    "word",            # str|None: bottom-slot word, type.word, <= 10 chars
    "top_text",        # str|None: top-slot hint chip, type.label, <= 18 chars
    "banner",          # (text, severity, sticky) | None; severity "info"|"warn"|"critical"
    "status",          # (own_pct, partner_pct|None, link_q 0..4, visible bool)
    # --- scanning ---
    "sweep",           # None | (wedge_deg, bins[12] of float 0..1 or None, active_bin, paused bool)
    # --- output devices ---
    "haptic",          # str|None: event pattern to START this frame (heartbeats are scheduled by the renderer)
    "heartbeat",       # str|None: "TICK"|"DOUBLE" played on each live ring spawn
    "heartbeat_every", # int 1..2: play the heartbeat on every Nth ring spawn (FAR = 2)
    "backlight",       # float 0..1
    "fps_cap",         # int 12..20
))
```

| Field | Range and validity rules |
|---|---|
| `screen` / `sub` | PAIRING: `looking`, `seen`, `confirmed`, `calibrate`, `split`. SCANNING: `ready`, `sweep`, `result`. FAR–HOT: `None` or DIRECTION phases `reveal`, `turn`, `walk`. FOUND: `celebrate`, `result`. LINK_LOST: `None`. MENU: the visible index of the selected row as a string, plus `^`/`v` scroll marks (see MENU) |
| `intensity` | `p(d)` smoothed (§5.1). In SCANNING and DIRECTION-TURN it is the *live mirror* value (§5.7) |
| `speed_px_s` / `pulse_period_ms` / `wavelength_px` | From the zone table (§5.3). Only new rings take changed values |
| `arrow_deg`, `cone_deg`, `arrow_style` | All three are None together. `arrow_deg` is never set while `cone_deg` > 60, while `screen` is SEARCHING, SCANNING, LINK_LOST, FOUND or PAIRING, or while `sub` is `ready`/`sweep` |
| `trend` | Non-zero only in FAR/NEAR/WARM, only while own activity is walk or run, only while not `unreliable` (§5.5). Always 0 in HOT |
| `dist_band` | Always one of the six labels. Never a raw number. None in PAIRING, SEARCHING, SCANNING and FOUND |
| `word` / `top_text` | Uppercase. Glyph subsets are in `tokens.typography`. Length is checked in the logic, not truncated in the renderer |
| `haptic` | One of the 9 named patterns (§7). The renderer's haptic queue applies priority and blanking |

Example: WARM, locked arrow, getting warmer.

```json
{"t_ms": 812340, "screen": "WARM", "sub": "walk", "zone": 2, "ramp": "green",
 "intensity": 0.55, "speed_px_s": 80, "pulse_period_ms": 1000, "wavelength_px": 80,
 "glow_r_px": 42, "ring_live": true, "burst": false,
 "glyph": "arrow", "arrow_deg": 0, "cone_deg": 31, "arrow_style": "solid_b",
 "trend": 1, "trend_strong": false, "countdown": null,
 "dist_band": "~10", "dist_stale": false, "word": null, "top_text": null, "banner": null,
 "status": [64, 71, 4, false], "sweep": null,
 "haptic": null, "heartbeat": "DOUBLE", "heartbeat_every": 1, "backlight": 0.6, "fps_cap": 20}
```

**Simulator obligations.** The web simulator shows two top-view watches you can drag and rotate. To be valid for research it must:

- Run the same state machine and constants (`tokens.json`, `finder/`).
- Generate RSSI with `sim/radio.py` (profiles clean, typical, harsh and indoor, with correlated shadowing and body loss on both wearers), not i.i.d. noise.
- Draw each watch face from `RenderParams` alone.
- Show haptics as a pulsing bezel.
- Offer a truth overlay: true distance and bearing next to the displayed band and cone, plus the running percentage of time the true bearing lies inside the cone. The target is about 80 %.

---

## 4. RippleField model (renderer side)

The palette formula stays as in the design system:

`v(i,t) = clamp(0, 7, vignette(i) · (floor + core(i) + glow_amp·e^(−((i−iris_r)/glow_r)²) + pulse_amp·Σ fadein·profile(i − r_k(t))))`, then `color(i) = LUT[ramp][round(v/7·63)]`.

This version adds five rules.

1. **Continuous intensity drives levels.**
   - `floor = 0.3 + 1.3·I`
   - `glow_amp = 2.0 + 4.0·I`
   - `pulse_amp = 4.0 + 1.5·I`
   - The game sends `glow_r_px = 20 + 40·I`; FOUND sends 90.
   - FAR crests therefore reach level ≥ 4.3 (`prox.4`), and HOT crests clamp at 7.
2. **Core dot.** When `iris_r = 0`, indices 0–6 get `core = max(0, 6 − v_rest)`, so the centre is never dimmer than `prox.6`.
3. **Temporal anti-aliasing.** `lead_eff = max(lead_px, 1.5·|speed|/fps_measured)`, and profiles are evaluated at fractional `r_k`. In HOT at 20 fps that is 9 px of leading edge, so crests never jump their own width. This fixes the judder and flicker findings.
4. **Direction.** With `speed_px_s < 0`, rings spawn at r = 168 and are absorbed at the iris edge. Inward rings mean "listening, no data".
5. **Ghost rings.** A ring spawned with `ring_live = False` contributes `0.6·pulse_amp` to a separate grey channel. `color(i) = LUT.grey[v_ghost]` wherever `v_ghost > v_green`. No haptic fires for a ghost ring.

The `burst` ring has amplitude 7 and speed 2× the zone speed, with the same lead rule. It is a travelling ring, not a flash.

Full-field brightness may not change by more than 2 ramp steps in 333 ms (`motion.flash_limit`). All state crossfades take 600 ms, and hue ramp changes take 1500 ms (400 ms into FOUND).

---

## 5. Mapping functions

### 5.1 Distance → proximity `p` → intensity `I`

```python
LN30 = math.log(30.0)
def prox(d_m):                       # 60 m -> 0.0, 2 m -> 1.0, log-spaced like RSSI
    return min(1.0, max(0.0, math.log(60.0 / max(d_m, 0.1)) / LN30))

def intensity(I_prev, p, dt_ms, tau_ms=1500):
    return I_prev + (p - I_prev) * (1.0 - math.exp(-dt_ms / tau_ms))
```

`d_m` is the estimator's `dist_m` (`finder/estimators`, e.g. `median_ema`). The path-loss reference is RSSI at **1 m** (§5.8).

Reference values:

| d | 55 m | 28 m | 14 m | 7 m | 3 m |
|---|---|---|---|---|---|
| p | 0.03 | 0.22 | 0.43 | 0.63 | 0.88 |

### 5.2 Zone with hysteresis and dwell

The zone edges line up with the readout band edges, so each zone owns whole bands: HOT = {`<3`, `~5`}, WARM = {`~10`}, NEAR = {`~20`}, FAR = {`~40`, `60+`}.

| Boundary | Enter the closer zone at d ≤ | Return to the farther zone at d ≥ | Dwell (both directions) |
|---|---|---|---|
| FAR ↔ NEAR | 28 m | 36 m | 3000 ms |
| NEAR ↔ WARM | 14 m | 18 m | 2000 ms |
| WARM ↔ HOT | 7 m | 9 m | 1500 ms |

```python
ENTER = (28.0, 14.0, 7.0)       # index = boundary from zone z to z+1
EXIT  = (36.0, 18.0, 9.0)
DWELL = (3000, 2000, 1500)
def next_zone(z, d, cond_since_ms, now):
    # one step per decision; the condition must hold for DWELL ms (symmetric: no optimism bias)
    if z < 3 and d <= ENTER[z]     and now - cond_since_ms >= DWELL[z]:     return z + 1
    if z > 0 and d >= EXIT[z - 1]  and now - cond_since_ms >= DWELL[z - 1]: return z - 1
    return z
```

- From SEARCHING, or from LINK-LOST after relinking, the first fix goes straight to its zone with no dwell.
- In RSSI terms, with p₁ₘ = −45 dBm and n = 2.2, the enter edges are about −77, −70 and −64 dBm.
- Dwell is shorter close up, where the SNR is higher and walking past the partner costs the most.

### 5.3 Zone → tempo (quantised) and haptic heartbeat

Tempo is fixed per zone, so the eye and the wrist agree. It changes only for rings spawned after the zone commits. Level parameters crossfade over 600 ms.

| Zone | `pulse_period_ms` | `speed_px_s` | `wavelength_px` | lead / trail px | Rings on screen | Heartbeat |
|---|---|---|---|---|---|---|
| FAR (0) | 2400 | 40 | 96 | 3 / 22 | ~1.5 | `TICK` every 2nd ring (4.8 s) |
| NEAR (1) | 1600 | 56 | 90 | 3 / 20 | ~1.7 | `TICK` every ring (1.6 s) |
| WARM (2) | 1000 | 80 | 80 | 3 / 18 | ~1.9 | `DOUBLE` every ring (1.0 s) |
| HOT (3) | 500 | 120 | 60 | 3 (eff. 9) / 14 | ~2.5 | `TICK` every ring (0.5 s) |

`wavelength_px = speed_px_s · pulse_period_ms / 1000`. `glow_r_px = 20 + 40·I`.

The four heartbeats differ in rhythm class: sparse, slow, double and geiger. R-07 allows at most four levels.

**FAR is no longer silent.** Its sparse tick is the "still connected" heartbeat, so silence always means "no packets".

### 5.4 Distance → readout band

Bands are about 2× apart, which matches the ±30–50 % error.

| Band | `<3` | `~5` | `~10` | `~20` | `~40` | `60+` |
|---|---|---|---|---|---|---|
| d range (m) | < 3.5 | 3.5–7 | 7–14 | 14–28 | 28–55 | ≥ 55 |

```python
EDGES = (3.5, 7.0, 14.0, 28.0, 55.0)
LABELS = ("<3", "~5", "~10", "~20", "~40", "60+")
ZONE_BANDS = {0: (4, 5), 1: (3, 3), 2: (2, 2), 3: (0, 1)}
def band(prev, d, zone, trend):
    b = prev
    while b < 5 and d >= EDGES[b] * 1.15: b += 1        # 15 % hysteresis each way
    while b > 0 and d <  EDGES[b - 1] / 1.15: b -= 1
    lo, hi = ZONE_BANDS[zone]; b = min(hi, max(lo, b))  # never contradict the zone
    if trend > 0 and b > prev: b = prev                 # never contradict a shown trend
    if trend < 0 and b < prev: b = prev
    return b
```

### 5.5 Trend (warmer / colder)

The estimator already exposes `trend` and `trend_conf` (`finder/estimators/base.py`). The UI gates them as follows.

- **Shown only when all of these hold:**
  - own activity is walk or run (BMA423);
  - `trend_conf ≥ 0.6` over an 8 s window, evaluated every 1 s and held for 2 consecutive evaluations;
  - `zone ≠ HOT`;
  - the signal is not unreliable (see below).
- **`trend_strong`:** `trend_conf ≥ 0.85` and |ΔRSSI over 8 s| ≥ 6 dB.
- **Flip limit:** the sign may not reverse within 5 s of the last change. There is no "steady" glyph: `trend = 0` shows the plain glow. That avoids overclaiming "unchanged", and avoids the pause icon.
- **Unreliable:**
  - Condition: RSSI filter sd > 6 dB, or packet delivery < 50 % over 5 s (R-14).
  - Effect: trend forced to 0, cone +15° (display only), and the StatusStrip is pinned with the link bars in `status.warn`.
- **Partner walking** (from `peer_motion` in their beacon): the trend is still shown, because the gap really is closing. It does **not** tighten or crack the arrow (§5.6).
- **Tuning target:** fewer than 5 % false verdicts on a tangential walk (constant distance) under the `typical` and `harsh` profiles, measured with `tools/bakeoff.py`. Starting thresholds are +1 at ≥ 3 dB / 8 s and +2 at ≥ 6 dB / 8 s. Raise them if the target fails.

### 5.6 Direction σ (cone) model

```python
def sigma(s0, turn_deg, steps_walked, still_s, colder_hits, unreliable):
    s = math.sqrt(s0**2 + (0.15 * abs(turn_deg))**2      # turn-to-face error
                  + (0.7 * steps_walked)**2                # heading drift while walking
                  + (1.0 * still_s)**2)                    # people turn while standing and looking
    s += 20.0 * colder_hits                               # sustained COLDER while locked
    return s + (15.0 if unreliable else 0.0)               # display-only widening
```

- **Scan:** `s0 = sqrt(σ_fit² + 20²)`. The 20° floor covers pacing error and over-rotation (§6 SCANNING). `still_s` counts only seconds with activity = still.
- **While `trend = +1`** (and the partner is not walking): `steps_walked` does not accumulate. Warmer confirms the half-plane ahead but never *narrows* the cone below its current value.
- **Colder hit:** `trend = −1` for ≥ 6 s while locked and the partner is still. Each hit adds 20° once, plays `NOPE`, and shows the top hint `WRONG WAY? RESCAN`. At most one hit per 15 s.
- **Tiers:** an arrow is born only if σ ≤ 45°.

  | Style | σ |
  |---|---|
  | `solid_a` | ≤ 25° |
  | `solid_b` | ≤ 45° |
  | `outline` | ≤ 60° |
  | hidden | > 60°, or age > 120 s |

- **Example:** s0 = 30°, a 120° turn and 40 steps give σ = 43° (`solid_b`). The arrow reaches 60° after about 60 steps (≈ 42 m), inside the 60–100-step fade R-04 proposes.
- `finder/motion.heading_confidence` (half-life 4 m) is much harsher than this model. The simulator's truth overlay must decide between the two. Until then the renderer uses this model.

### 5.7 Live signal mirror (SCANNING sweep, DIRECTION-TURN)

`I_mirror = clamp01((rssi_ema150 − rssi_min) / max(4, rssi_max − rssi_min))`. It uses raw per-packet RSSI with a 150 ms EMA, and the running min and max since the sweep started. The game sends it as `intensity`, so the halo brightens as the player faces the partner. This shows the measurement happening; it is not the estimate.

### 5.8 Calibration and radio

- Calibrate RSSI at **1 m**: `STAND 1 STEP APART` in PAIRING, a 3 s mean, watches worn and facing each other.
- Clamp the result to the nominal p₁ₘ (−45 dBm, as in `sim/radio.py` and `PathLoss`) ± 6 dB. If calibration is skipped or unstable, use the nominal value.
- Path-loss exponent: n = 2.6 outdoors (default) and 3.0 indoors or in crowds, via `Game.set_place()` (tokens `calibrate.n` / `n_indoor`; see docs/estimation/bakeoff.md §6). Players set it with the MENU row `PLACE: OUT/IN`.
- Every gate is expressed relative to p₁ₘ (for example, bump-ready is d ≤ 3 m from the model). None uses an absolute dBm value.
- Beacon rate: 10 Hz normally, 20 Hz in HOT and on both watches during a scan, 5 Hz in saver.
- Each beacon carries: own battery %, activity, a scan flag, tap timestamps and a goodbye flag.

---

## 6. Screens

The field table in each screen uses: ramp | I | speed | period | λ | glow_r | notes. Overlay coordinates are absolute.

### PAIRING

**Purpose:** pair in two actions or fewer (R-11), then calibrate at 1 m.

| Sub | Field | Centre (iris r 92, y 28–212) | Top slot | Bottom slot | Haptic |
|---|---|---|---|---|---|
| `looking` | green, I = 0.1, speed **−30** (inward), 3000 ms, λ 90, glow 30, pulse_amp 2.0 | 3 `line.subtle` dots r 5 at (64,120), (120,120), (176,120) | chip `PAIR` | word `LOOKING` `text.secondary` | — |
| `seen` | green, rings stop; halo breathes glow_amp 1.5↔3.0 over 2400 ms `in_out_sine` | 3 runes (48 px boxes at x 40–88, 96–144, 152–200, y 96–144), drawn in one every 150 ms, `text.primary`, `stroke.l` | chip `SAME RUNES?` | word `TAP = YES` | `DOUBLE` when the partner is seen |
| `confirmed` | as `seen` | runes turn `prox.7` | chip `WAITING` | word `WAITING` | — |
| `calibrate` (3 s) | green; the fill disc levels r 64→168 go to 4 as R(t) = 64 + 104·t/3 s, with a 3 px edge at level 6 | iris r 64, countdown `3 2 1` (`type.display`, x 108–132, y 96–144) | chip `STAND 1 STEP APART` | word `HOLD STILL` | `TICK` each second; `CLOSER` when done |
| `split` (30 s) | **Live** hunt field from real packets (zone tempo), heartbeat muted | iris r 64, countdown `30…0` (2 digits `type.display`, x 96–144) | chip `NO PEEKING` | word `SPLIT UP` | `TICK` at 3, 2, 1; `CLOSER` at 0 (word `GO` for 1 s) |

- **Confirm:** the target is the whole iris (a 184 px disc) or a short press of the button (R-11 asks for 80 px or more). One action per player.
- **Calibration gate:** if the RSSI sd over 1 s is > 4 dB, the fill pauses and the top chip reads `HOLD STILL`. After 10 s the watch uses the nominal p₁ₘ and moves on (toast `CAL SKIPPED`).
- **Runes:** chips overlap the iris rim in PAIRING. That is acceptable because no rings run behind them.

### SEARCHING

**Purpose:** paired and hunting, but no valid packets yet. There is no fake data.

| Field | Centre | Top | Bottom | Haptic |
|---|---|---|---|---|
| grey, I = 0.15, speed **−36** (inward), 3200 ms, λ 115, glow 18 at the iris rim, pulse_amp 2.5 | iris r 44; `seeker` glyph (ring r 14 plus 4 ticks r 20–28, `stroke.m`, `grey.7`) | StatusStrip (3 s after wake), else empty | word `SEARCHING` in `grey.7` (x 36–204). After 45 s: `WALK ABOUT` | none. Silence = no link |

- **Exit:** 3 packets within 2 s → the estimated zone directly, with `burst`, the `CLOSER` haptic and a 600 ms ramp crossfade from grey to green.
- No readout is shown: no `--` and no `60+`.

### FAR (d ≥ 28 m on entry; stays until d ≤ 28 m for 3 s)

| Field | ramp green, I 0–0.22 (continuous), **40 px/s, 2400 ms, λ 96**, glow_r 20–29, floor 0.3–0.6, pulse_amp 4.0–4.3, core dot r 6 at `prox.6` |
|---|---|
| Centre | Priority: `arrow` (DIRECTION) > `chevrons` > `glow` (the core dot) |
| Chevrons | Iris r 44 (y 76–164). **Warmer:** filled `prox.7` up-chevron `[(-24,4),(0,-16),(24,4),(24,14),(0,-6),(-24,14)]` + (120,120), spanning y 104–134. With `trend_strong`, two copies centred at y 112 and 128. Nudge up 6 px, 800 ms `in_out_sine` loop. **Colder:** the same shape rotated 180°, drawn **hollow** (a 4 px `accent.cold` outline from a pre-inset polygon, interior `bg.iris`), nudging down. Shape, fill, colour and motion all differ |
| Top slot | StatusStrip for 3 s after wake. Chip `TAP TO SCAN` for 4 s on entering FAR and after 6 s of standing still with no arrow (at most once per stillness episode) |
| Bottom | Readout `60+` or `~40`. With an arrow shown, a 12 px trend mark sits left of the numeral: ▲ filled `prox.6`, or ▽ 2 px outline `accent.cold` |
| Copy | Numerals only. No zone word |
| Haptic | `TICK` (60 ms) on every 2nd live ring (4.8 s). `FARTHER` on entering from NEAR |

### NEAR (14–28 m band; enter ≤ 28 m, exit ≥ 36 m)

| Field | green, I 0.22–0.43, **56 px/s, 1600 ms, λ 90**, glow_r 29–37, floor 0.6–0.9, pulse_amp 4.3–4.6 |
|---|---|
| Centre, top | As FAR |
| Bottom | Readout `~20` |
| Haptic | `TICK` on every live ring (1.6 s). `CLOSER` + `burst` on entry from FAR; `FARTHER` on entry from WARM |

### WARM (7–14 m band; enter ≤ 14 m, exit ≥ 18 m)

| Field | green, I 0.43–0.63, **80 px/s, 1000 ms, λ 80**, glow_r 37–45, floor 0.9–1.1, pulse_amp 4.6–4.9 |
|---|---|
| Centre | As FAR. With the iris open, the halo sits outside r 44/64, so the glyph never sits on bright glow (this fixes RUNEWELL's camouflaged chevron) |
| Top | As FAR, but the hint chip is `TAP TO SCAN` only when still. The scan works best in this range |
| Bottom | Readout `~10` |
| Haptic | `DOUBLE` (60·80·60) on every live ring (1.0 s). `CLOSER` + `burst` on entry from NEAR |

### HOT (< 7 m band; enter ≤ 7 m, exit ≥ 9 m)

| Field | green, I 0.63–1.0, **120 px/s, 500 ms, λ 60**, effective lead 9 px at 20 fps, glow_r 45–60, floor 1.1–1.6, pulse_amp 4.9–5.5 (crests clamp at 7 = `prox.7`) |
|---|---|
| Centre | `arrow` if valid, else `glow`. There are **no trend chevrons**, because multipath dominates below about 7 m |
| Top | Chip `LOOK AROUND` for 4 s on entry. On bump-ready: `TAP WATCHES` |
| Bottom | Readout `~5` or `<3`. **Bump-ready** (band `<3` held 1.5 s): word `BUMP!` in `prox.7`, replacing the readout |
| Haptic | `TICK` every live ring (0.5 s, 12 % duty). `CLOSER` + `burst` on entry. On bump-ready: `DOUBLE` once |
| Beacon | 20 Hz, so "pings are real" still holds at a 500 ms ring period |

**Bump rule (R-10):**

- Both watches are in HOT.
- Both BMA423 single-tap IRQs fire within 400 ms of each other. Timestamps are exchanged in beacons, with the clock offset estimated from beacon round trips.
- Neither tap is within 300 ms after a screen touch or within the haptic blanking window (§7).

**Fallback:** both players short-press within 3 s while in HOT with band ≤ `~5`.

RSSI alone never enters FOUND.

### FOUND

| Sub | Field | Centre | Top | Bottom | Haptic |
|---|---|---|---|---|---|
| `celebrate` (0–2 s) | ramp **gold** (400 ms crossfade); one burst ring at 240 px/s, amp 7; then a **standing wave**: `v = 2.0 + 2.5·(0.5+0.5cos(2πi/32))·(0.6+0.4 sin(2πt/2400))`, glow_r 90. It does not travel, so it reads as "arrived" | iris 0; `check`: disc r 40 in `accent.found` (x 80–160, y 80–160) with a `bg.base` check polygon, `stroke.xl` | chip `TIME 12:48` (session m:ss, `text.primary`) | word `FOUND` in `accent.found` | `FOUND` (80·60·80·60·80·200·500) on both watches, synchronised by the bump packet |
| `result` (until a press) | as above | as above | same | word `TAP=AGAIN` | — |

- Both watches enter FOUND on the shared bump packet.
- A press or tap on either watch starts a new round on both: PAIRING `split`, keeping the existing pairing and calibration.
- The standing wave's breathing changes the field by 1 ramp step over 1.2 s, well inside the flash limit.

### SCANNING (a guided 360° body turn; short press, or a centre tap, from FAR/NEAR/WARM/HOT)

| Sub | Field | Centre (iris r 64) | Top / bottom | Haptic |
|---|---|---|---|---|
| `ready` (flat check + 3 s) | green, I frozen at the zone value, pulse_amp ×0.4, glow_r 8 at the rim | Countdown `3 2 1` (`type.display`). The iris rim turns `prox.6` when the watch is flat (face-up within 20°, held 0.5 s) and `status.warn` when it is not; the countdown holds until flat | top chip `HOLD AT CHEST`, or `HOLD FLAT` in `status.warn`; bottom word `TURN RIGHT` | `TICK` per count |
| `sweep` (12 s) | halo = **live mirror** (§5.7): glow_amp 1 + 5·I_mirror, glow_r 12 outside the iris; rings paused | `turn` glyph (a 240° arc r 22, `stroke.l`, `prox.6`, with head). **Wedge:** a 30° filled sector r 70–110 in `prox.6`, rotating clockwise from 0° at **30°/s**. **Bins:** 12 radial bars (6° wide) centred on k·30°, r 70 → 70 + 40·norm(bin median) in `prox.3`; the active bin in `prox.5`; a bin with fewer than 4 packets is drawn as a hollow 2 px outline of its full 70–110 box (honest "no data") | **Both slots suppressed**, since the wedge overlaps them | `TICK` every 45°; `DOUBLE` at 180° (halfway); no heartbeat |
| `result` | ok: the best bin blinks twice (200 ms on/off, `prox.7`, one bar only), then morphs into the dart at θ over 400 ms → DIRECTION `reveal`. No fix: back to the zone screen | — | no fix: toast `NO FIX, TRY AGAIN` (17) | ok `CLOSER`; no fix `NOPE` |

**Estimator:**

- Use **raw per-packet** RSSI, both own RSSI and the partner's reported `peer_rssi`, not the distance filter.
- Tag each sample with the wedge angle φ at arrival.
- Fit `rssi(φ) = a0 + a1·cos(φ − θ)` by least squares (first circular harmonic).
- σ_fit = deg(σ_res / (a1·√(N/2))), and `s0 = √(σ_fit² + 20²)`.
- **No fix** if 2·a1 < 4 dB (fitted peak-to-trough) or s0 > 45°.
- The bin bars show a ±30° smoothed curve, not raw bin values.

**Faults:**

| Fault | Condition | Effect |
|---|---|---|
| Tilt | > 35° from flat for > 0.5 s | Sweep pauses: wedge turns `status.warn` and holds its angle, top chip reappears as `HOLD FLAT`, `NOPE` plays once. Resumes when flat |
| Walking | Activity walk, or ≥ 3 steps in 2 s | Pauses as above, with `STAND STILL` |
| Abort | > 8 steps in total, or > 6 s of total pause | Abort, toast `SCAN STOPPED` |
| Blanking | — | Tilt and shake detection are blanked from each haptic pulse start until 150 ms after it ends, so the metronome cannot trip its own guard |

**Partner:**

- The scanning watch sets the scan flag in its beacon.
- On the partner watch, the top chip shows `FRIEND SCANNING` and the bottom word `HOLD STILL`, and `HOLD` plays. It repeats every 3 s, at most 3 times, while the partner is walking.
- If `peer_motion` reports walking for > 2 s of the sweep, add 15° to s0. For > 4 s, the result is no fix with toast `FRIEND MOVED`.
- Only one scan at a time: if both flags are raised, the lower MAC keeps its scan and the other gets toast `FRIEND SCANNING`.

**Cancel:** a short press or any centre tap cancels at any phase and returns to the zone with no arrow.

**Backup probe (R-15, P2):** menu → `WALK TEST`.

1. Iris r 64 with a `prox.6` step arc r 58–64 filling over 10 steps; bottom word `WALK`.
2. Bottom word `TURN RIGHT` with a quarter-arc glyph.
3. 10 more steps.
4. The result is a DIRECTION arrow with s0 ≥ 35°, relative to the *current* heading, so no turn phase follows.

### DIRECTION (arrow + cone): an overlay state of FAR, NEAR, WARM and HOT

**Common geometry, inside iris r 64 (y 56–184):**

- **Beam:** a filled sector from C to r 60, spanning `arrow_deg ± cone_deg` (cone drawn clamped 12–60°), with vertices every ≤ 10°. At most 14 vertices.
- **Dart:** `[(0,-50),(30,34),(0,16),(-30,34)]`, rotated by `arrow_deg` about C. Pointing up, it spans x 90–150, y 70–154.
- **Keyline:** a **precomputed 2 px mitred offset polygon** in `bg.base`, drawn first (not a scaled copy).

| Style | σ | Dart | Beam |
|---|---|---|---|
| `solid_a` | ≤ 25° | `prox.7` | `prox.4` |
| `solid_b` | ≤ 45° | `prox.6` | `prox.3` |
| `outline` | 45°–60° | 4 px `prox.5` outline | `prox.2` |

A wide beam is the uncertainty, drawn at the pointer itself (this fixes RUNEWELL's "doubt on the rim").

| Phase | What shows | Bottom slot | Top slot | Haptic |
|---|---|---|---|---|
| `reveal` (1.5 s) | Dart at θ relative to the heading at scan start, which is screen-up; beam ±σ; the field keeps the zone tempo | Word by precision: σ ≤ 30° → clock hour `4 O'CLOCK` (round(θ/30), 0 → 12). 30–45° → `AHEAD` / `RIGHT` / `BEHIND` / `LEFT`. If \|θ\| ≤ 20°: `AHEAD`, and `turn` is skipped | — | `CLOSER` (already fired at the scan result) |
| `turn` | A **pacer wedge** (the scan wedge, 30°, r 70–110, `prox.6`) rotates from 0° toward θ the short way at 30°/s. The dart counter-rotates: `arrow_deg = θ − pacer_deg`. The halo is the **live mirror** | Word `TURN RIGHT` or `TURN LEFT` | Suppressed while the wedge is drawn | `TICK` every 45° of pacer |
| lock | When the pacer reaches θ, or on a press/tap ("I'm facing it"): the dart eases to 0° over 300 ms. σ gains the turn term 0.15·\|θ turned\| | Word `WALK` for 3 s, then the readout with a trend mark | — | `DOUBLE` |
| `walk` | Dart up, beam 0 ± σ(t). The style tier follows σ (§5.6) | Readout + trend mark | `outline` tier: chip `TAP TO RESCAN`. Colder hit: `WRONG WAY? RESCAN` | Zone heartbeat; `NOPE` on a colder hit |
| expire | σ > 60° or age > 120 s: the dart scales 1 → 0 into C over 600 ms and the iris closes | Toast `SCAN AGAIN` | — | `FARTHER` |

- The TURN phase is paced dead reckoning: the pacer assumes the player follows it, just as the scan does, and its error is paid for in σ.
- `arrow_mode = "guided" | "static"` in the logic lets the simulator and field tests A/B this against a plain arrow with a `FACE IT` prompt (R-04).
- On LINK_LOST the arrow is hidden at once, but σ keeps growing. If the link returns within 20 s and σ ≤ 60°, the arrow comes back.

### LINK-LOST (no packet for 5 s after a fix)

| Field | Centre | Top slot | Bottom slot | Haptic |
|---|---|---|---|---|
| grey (1500 ms crossfade); rings in flight finish, then **inward** listening rings: speed −30, 3000 ms, λ 90, pulse_amp 1.5, glow_r 16, I frozen at the last value | iris r 44, `seeker` in `grey.7` | chip `LAST ~20M ▲` (label `grey.6`, with a 12 px last-trend mark in `grey.6`, filled up / hollow down, or none) | **Banner** (warn): `LOST 0:12`. After 20 s: `LOST 0:27 GO BACK` if the last trend was ≤ 0, or `LOST 0:27 KEEP ON` if it was +1. Partner goodbye flag: critical banner `FRIEND IS OFF`. Partner battery flag ≤ 5 %: `LOST, FRIEND LOW BAT` | `LOST` (5 × 60 ms) once at entry, then silence |

- The timer counts in m:ss up to 9:59, then shows `10M+`.
- The watch stays in LINK-LOST until relink. It does not fall back to SEARCHING, because the last known state stays useful (R-06).
- **Relink:** 3 packets within 2 s → the new zone directly, with a green crossfade, `burst`, `CLOSER`, and a 2.5 s toast `BACK IN RANGE`.
- StatusStrip battery warnings outrank the `LAST` chip in the top slot.

### LOW-BATTERY (modifier over any screen; own AXP202 %, partner % from beacons)

| Level | Visual | Behaviour | Haptic |
|---|---|---|---|
| 20 % | Toast (warn) `BATTERY 20%`. The StatusStrip own-battery icon turns `status.warn` and stays pinned. Partner gets toast `FRIEND BATT 20%` | — | `BATT` once |
| 10 % | **Once**, a 2.5 s interstitial: iris r 64 with a battery glyph (64×32 rounded rect x 88–152, y 104–136, 3 px `status.warn` outline, nub x 152–158 y 114–126, fill proportional). Bottom word `SAVER ON` | Saver: fps 15, backlight 0.35, pulse_amp ×0.7, v ≤ 5, beacons 5 Hz. Heartbeats continue | `BATT` |
| 5 % | Toast (critical) `BATTERY 5%` once; icon `status.critical`, pinned | Screen only on wrist raise, off 3 s after lowering; haptics carry the game | `BATT` |
| 3 % | Word `BYE` for 2 s; one inward ring at −120 px/s closes into C | Goodbye beacon ×3, then AXP202 power-off. Partner shows `FRIEND IS OFF` | `NOPE` |

The game never dies silently (R-12), and a modal never takes over every glance.

### MENU (screen long-press ≥ 800 ms or button long-press 1.5 s; auto-closes after 8 s)

- **Field:** frozen and dimmed (palette ×0.5, no rings). Haptics are paused.
- **Rows:** a scrolling list of 5 rows, 4 visible at a time (44 px pitch: x 24–215, y 32, 76, 120, 164; each h 40, `surface.toast`, `type.label`):
  1. `RESUME`
  2. `SUN: ON/OFF`
  3. `BUZZ: FULL/EVENTS/OFF`
  4. `PLACE: OUT/IN`: outdoors or indoors/crowded. Sets the path-loss exponent used to turn signal into distance (`calibrate.n` 2.6 / `n_indoor` 3.0, §5.8). It stays set across rounds.
  5. `END ROUND`

  The list opens at the top (rows 1–4 visible). It scrolls so the selected row is always visible. A filled 6 px triangle in `text.secondary` at the list's right edge (x 207–213) marks more rows: pointing up at y 36–41 when rows are hidden above, pointing down at y 195–200 when rows are hidden below. `WALK TEST` (P2, not built) would be a 6th row; a `DEBUG` row appears in dev builds only.
- **Controls:** tap a visible row to select it. Swipe up shows the rows below, swipe down the rows above. Short press = next row (wraps, scrolling as needed), long press = select.
- `END ROUND` asks `SURE? PRESS` and needs a second press within 3 s. Scrolling, selecting another row or moving the selection off `END ROUND` cancels the question, so the next press is an ordinary one again.
- **`sub` encoding:** the visible index of the selected row (`"0"`–`"3"`), followed by `"^"` when rows are hidden above and/or `"v"` when rows are hidden below (for example `"3v"`, `"2^"`). The renderer draws the four row strings it is given (`menu_rows`).

---

## 7. Haptic vocabulary

Every pulse is ≥ 60 ms and every gap ≥ 60 ms (ERM spin-up). Duty stays ≤ 12 % in continuous use. Patterns list on/off ms.

| Name | Pattern | Meaning |
|---|---|---|
| `TICK` | 60 | Heartbeat for FAR (every 2nd ring), NEAR and HOT; scan pacing every 45°; countdown |
| `DOUBLE` | 60·80·60 | WARM heartbeat; halfway during the scan; arrow locked; partner seen; bump-ready |
| `CLOSER` | 60·60·60·60·200 | Good news: closer zone, relinked, scan fix, pairing done, GO |
| `FARTHER` | 300 | Farther zone; arrow expired |
| `NOPE` | 300·150·300 | Wrong way, no fix, scan fault, shutdown |
| `LOST` | 60·60·60·60·60·60·60·60·60 | Link lost (a 5-pulse stutter) |
| `HOLD` | 400·200·400·200·400 | Your friend is scanning: stand still |
| `FOUND` | 80·60·80·60·80·200·500 | FOUND, on both watches |
| `BATT` | 500·200·500 | Own battery threshold |

- **Queue.** The priority order is FOUND > LOST > NOPE > HOLD > BATT > CLOSER/FARTHER > DOUBLE > TICK.
  - An event **replaces** the next heartbeat pulse, and heartbeats resume 1 s after the event ends.
  - An event is dropped only if one of the same or higher priority started less than 1 s earlier (the WARM-heartbeat collision bug in BLIP HUNT).
- **Blanking.** The accelerometer's tap, shake and tilt-fault detection ignores samples from each pulse start until 150 ms after it ends.
- **Screen off.** Heartbeats and events continue while the screen is off; the scheduler is time-based.
- **Modes (menu).** `FULL` (default), `EVENTS` (no heartbeats), `OFF`.

---

## 8. Interaction

| Input | Where | Action |
|---|---|---|
| **Tap** (touch down→up 60–400 ms, one finger, moving ≤ 12 px, inside r ≤ 92 of C) | FAR–HOT | Start SCANNING. The 3 s `ready` countdown is the confirmation window, so an accidental tap costs nothing and any second tap cancels |
| Tap | PAIRING `seen` | Confirm runes |
| Tap | DIRECTION `turn` | "I'm facing it": lock now |
| Tap | FOUND `result` | New round |
| Tap | SCANNING | Cancel |
| **Long-press** (≥ 800 ms stationary) | Any screen | MENU |
| **Button short** | Any screen | The same primary action as a tap on that screen. **When the screen is off, it only wakes the screen** |
| **Button long** (1.5 s) | Any screen | MENU. Nothing is ever mapped near the AXP202 hardware power-off hold |
| Wrist raise (BMA423 wrist-tilt IRQ, or `face_up` from `finder/motion.py`) | — | Screen on. The first frame is the current state with no intro (rings are time-based, so they are already mid-flight). Backlight 1.0 for 3 s, then 0.6 |
| Wrist down (not face-up for 2 s) | — | Backlight off and rendering stops. Radio, logic and haptics continue |
| Face-up > 30 s with no input | Not SCANNING or `turn` | Dim to 0.35. The glow stays readable. Any input or zone change → 0.6 |
| Sun mode (menu) | — | Backlight 1.0, and the ramp LUT is lifted by one stop (floor ≥ 1.0) |

**Touch filters (rain, sleeves):**

- Ignore touches for 300 ms after a wake.
- Ignore multi-touch.
- After 3 or more touches in 1 s, ignore touches for 2 s.
- In HOT, a tap within 400 ms of an accelerometer tap is treated as part of a bump and ignored.

**During a scan:**

- The screen never dims or sleeps.
- Heartbeats stop, and only pacing ticks play.
- Both watches beacon at 20 Hz.
- The partner is asked to hold still.
- Every fault pauses the sweep rather than silently corrupting it.
- The result is either an arrow with a visible σ or an honest `NO FIX`.

---

## 9. Critique traceability (critical and major findings)

| Finding (concept) | Fix in this spec |
|---|---|
| Trend has no primary home, alternates with other copy, shares the brightness channel (RUNEWELL, critical) | Trend chevrons have one fixed home in the iris (r 44). When the arrow owns the centre, a 12 px mark sits in the readout pill. No copy alternates. Brightness follows only distance |
| Text sits on animated rings or bright field (RUNEWELL major, PINPOINT critical) | Text only on opaque chips in two slots; runes and echo ring dropped |
| Chevron or arrow camouflaged by a bright core (RUNEWELL) | Dark iris lens behind every glyph; glow becomes a halo outside it |
| Uncertainty drawn far from the pointer (RUNEWELL); cues not glanceable (PINPOINT) | Beam half-angle = σ at the dart; freshness *is* σ; tiers solid → outline → gone |
| FAR too dim, fails the sun floor; FAR silent (all three) | Core dot at `prox.6`; FAR crest ≥ `prox.4`; FAR heartbeat every 4.8 s |
| Haptic pulses too short, vocabulary too big, event drop rule (all three) | 9 patterns, each pulse ≥ 60 ms, priority queue |
| Continuous tempo vs learnable levels (RUNEWELL) | Tempo quantised per zone; only brightness and glow are continuous |
| Arrow decay too fast or double-counted (RUNEWELL) | 0.7°/step, time term only while still, turn error, warmer pauses growth |
| Scan estimator smeared or fragile; pivot steps stall it; no cancel (RUNEWELL, PINPOINT) | Raw per-packet RSSI + peer_rssi, harmonic fit, 20° floor, fault only on sustained walking, cancel with any input |
| Calibration at 0–10 cm (all three) | Calibrate at 1 m, clamped ±6 dB; gates relative to p₁ₘ |
| Ring judder or aliasing at the real fps (RUNEWELL, BLIP HUNT) | `lead_eff ≥ 1.5·v/fps`, sub-pixel profiles, HOT spacing 60 px, 20 fps target |
| Zone words vs trend words (RUNEWELL) | No zone words on screen |
| Too many layers; the numeral is the hero (RUNEWELL, BLIP HUNT) | One primary element per mode; readout in `text.secondary`; StatusStrip transient |
| 8×16 text read while moving (RUNEWELL) | New `type.word` 16×32 for anything read in motion |
| Input gating, false triggers (RUNEWELL, PINPOINT) | Tap starts a cancellable countdown; touch filters; accelerometer taps blanked after touches; wake press only wakes |
| Arrow exceeds the hero area; keyline by scaling (PINPOINT) | Dart within r 50 inside iris r 64; mitred offset keyline |
| Number jitter, contradicting the trend (PINPOINT) | 2× bands, 15 % hysteresis, zone clamp, trend-monotonic rule |
| No feedback while turning to face (PINPOINT) | Paced turn wedge + live mirror + counter-rotating dart; turn error in σ |
| Modal on every glance at low battery (PINPOINT) | 10 % interstitial shown once |
| Glyph clashes: pause-like "steady", mirrored chevrons (PINPOINT) | No steady glyph; colder chevrons are hollow, blue and move down |
| Weak simulator radio model (PINPOINT, BLIP HUNT) | The simulator must use `sim/radio.py` and show a truth overlay |
| Frame budget assumes PSRAM or 80 MHz (BLIP HUNT) | 20 fps target, 15 floor; strip rendering on the non-SPIRAM build, ring-map copies by slice (memcpy) |
| Trend thresholds below correlated noise (BLIP HUNT) | Gated by `trend_conf`; tuned to < 5 % false verdicts on tangential walks |
| Scan assumes a perfect turn; partner not asked to stop (BLIP HUNT) | 20° pacing floor, turn error, `HOLD` pattern + peer_motion check |
| Motor trips the shake guard (BLIP HUNT) | 150 ms blanking |
| "Pings are real" breaks in HOT (BLIP HUNT) | 1 s window + 20 Hz beacons in HOT |
| Photosensitive flashes (BLIP HUNT) | No full-field flash; bursts are travelling rings; FOUND breathes 1 step / 1.2 s |
| Latency and optimistic asymmetric holds (BLIP HUNT) | Symmetric dwell 3 / 2 / 1.5 s; continuous intensity keeps the field live |

---

## 10. Token changes made for this spec (design system v0.1.0 → v0.2.0)

`tokens.json` was updated and still parses. `design-system.md` was updated to match.

- **Typography:** new `type.word` (16×32 uppercase).
- **Layout:** new `layout.top_slot` and `layout.core_dot`. Seeker iris set to 44.
- **Motion:**
  - fps target 20 (minimum 15).
  - Sweep 30°/s.
  - New rule `motion.temporal_aa`.
- **Field:**
  - `intensity_map` (floor, glow, pulse and glow_r as functions of I).
  - Inward rings.
  - Ghost rings.
  - Core dot.
- **States:**
  - FAR–HOT carry tempo only; levels come from I.
  - SEARCHING and LINK-LOST use inward rings.
  - FOUND uses a standing wave.
  - PAIRING looks with inward rings.
- **Thresholds:**
  - Zones realigned to band edges (28/36, 14/18, 7/9 m), with per-boundary dwell.
  - FOUND requires the bump.
  - p₁ₘ = −45 dBm at 1 m, clamped ±6 dB.
  - No LINK-LOST → SEARCHING fallback.
  - New σ-growth model, max age 120 s.
  - Scan 12 s, harmonic estimator, 4 dB peak-to-trough.
  - Trend gating.
  - Beacon rates.
- **Haptics:** replaced by the 9-pattern vocabulary with the priority and blanking rules.
- **Input:** tap = scan (via countdown), long-press = menu, button short = primary or wake. The screen tap no longer toggles the backlight; sun mode moved to the menu.

---

## 11. Accessibility and sunlight

- **Sun:**
  - The brightest elements are always meaningful: core dot or iris rim ≥ level 5, dart `prox.7`, readout chip text at 12.5:1 or better.
  - No essential element uses `prox.0`–`prox.2` or a 1 px stroke. The minimum stroke for glanced glyphs is 4 px.
  - A wake boosts the backlight to 1.0 for 3 s, and sun mode lifts the ramp.
  - FAR is told apart from NEAR by tempo and wavelength, not by being darker.
- **Colour vision:** every signal uses at least three channels.
  - Warmer: filled + up + green.
  - Colder: hollow + down + blue + no fill.
  - FOUND: gold + check disc + unique haptic + standing wave.
  - Lost: grey + inward rings + banner text + `LOST` haptic.
  - Red is only battery-critical, always with text.
- **Type size:** anything read while walking is ≥ 32 px (about 3.7 mm) and ≤ 10 characters. 16 px text is only for stationary moments: pairing, scan prep, toasts, menu. The 8 px micro type is debug and battery % only.
- **Photosensitivity:** travelling rings only. There is no full-field change of more than 2 ramp steps in 333 ms. The HOT rings at 2 Hz are narrow bands, and the scan bin blink covers one bar.
- **Eyes-free:** zone heartbeats, zone changes, wrong way, scan pacing, lost, found and battery are all distinct patterns. A player can play FAR → HOT with the wrist down and look only to scan or bump.
- **One hand:** every action is reachable with the side button alone (short = primary, long = menu), and the tap target is the whole iris.

## 12. What not to do

- Don't print metres (`12.4 m`), dBm or degree numbers outside the debug overlay. Don't show `--` or a placeholder distance before the first fix.
- Don't show an arrow without a completed scan or probe, with σ > 45° at birth, after it is 120 s old, or during LINK-LOST. Never imply north.
- Don't let the arrow track body turns it cannot sense. It moves only during the paced turn phase or when locked.
- Don't draw text on the live field, and don't alternate two messages in one slot.
- Don't use zone words (WARM, HOT) on screen. Thermal words are trend-only, and trend is a glyph.
- Don't flash the whole screen, strobe the core, or run crests narrower than their per-frame travel.
- Don't let RSSI alone declare FOUND, and don't use an absolute dBm gate for anything.
- Don't draw directly to the panel (`fill` then redraw). Compose every strip off-screen and push it whole.
- Don't make a haptic pulse under 60 ms, and don't invent new patterns beyond the 9.
- Don't copy Nintendo assets: no eye emblem, no Sheikah, Hylian or Zonai script, no fonts, no UI chrome, no "Sheikah" on screen.
- Don't present the game as a child-safety or person tracker. RSSI cannot support that claim (R-13).
- Don't map anything to a button hold anywhere near the AXP202 power-off hold.
