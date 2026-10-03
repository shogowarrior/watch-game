# Sheikah Finder design system (v0.1.0)

A small design system for the watch UI of the two-player hide-and-seek game on the LILYGO T-Watch 2020 (240×240 ST7789 IPS, RGB565, one side button, vibration motor, BMA423). The machine-readable source of truth is [`tokens.json`](./tokens.json). If this document and the JSON disagree, the JSON wins.

All pixel values refer to the 240×240 physical display. The panel is 1.54" diagonal, so each side is about 1.09", which works out to roughly 220 ppi: 16 px ≈ 1.8 mm, 32 px ≈ 3.7 mm, 48 px ≈ 5.5 mm.

The look borrows the feel of an ancient-tech proximity sensor: a green glow and ripples that pulse out from the centre, faster and brighter as you get closer. It reuses none of Nintendo's assets, symbols (no eye emblem, no Sheikah or Zonai script), fonts or exact palettes.

---

## 1. Principles

1. **Glanceable in 1–2 s.** Each screen answers one question: *how close?* (brightness and tempo), *which way?* (arrow) or *getting warmer?* (chevrons). Anything else is secondary and hidden by default.
2. **Radial is cheap, everything else costs.** The background is one palette-cycled ring map. Any effect that depends only on distance from the centre is almost free. Overlays are a few filled polygons and bitmap glyphs.
3. **Honest uncertainty.** RSSI ranging is off by roughly ±30–50 %. Distances only appear as coarse bands, and the arrow only appears after a scan or probe. The arrow's beam shows how uncertain the bearing is.
4. **Haptics carry proximity.** The zone tempo is felt as well as seen, and the vibration fires on the same frame as each ripple spawns, so the player doesn't need to stare at the screen.
5. **Never flicker.** Every frame is composed off-screen and pushed whole. Nothing is drawn straight to the panel. Direct-to-panel drawing (`fill` + redraw) is what makes the current prototype flicker.

---

## 2. Render model (the constraint behind every token)

```
ring_map  GS8 FrameBuffer 240x240   idx = min(255, floor(hypot(x-119.5, y-119.5)))  -> 0..168
palette   RGB565 FrameBuffer 256x1  rebuilt every frame (entries 0..168 only)
frame     RGB565 FrameBuffer 240x240

per frame:
  palette[i] = LUT[ramp][ round(v(i,t) / 7 * 63) ]      # radial function -> colour
  frame.blit(ring_map, 0, 0, -1, palette)                # background, C speed
  draw overlays (poly / fill_rect / ellipse / glyph blits)
  display.blit_buffer(frame, 0, 0, 240, 240)
```

- **Field value**, in ramp units 0–7: `v(i,t) = clamp(0,7, vignette(i) · (floor + glow_amp·e^(-(i/glow_r)²) + pulse_amp·Σ fadein(r_k)·profile(i − r_k(t))))`.
- **Rings:** a new ring spawns every `period_ms` at the iris edge (r = 0 when no iris is open) and travels outward at `speed_px_s`. The profile is asymmetric: a sharp leading edge (`lead_px`) and a soft trailing tail (`trail_px`), both smoothstepped. Rings fade in over their first 12 px so they don't pop into existence.
- **Vignette:** 1.0 out to r = 88, falling to 0.4 at r = 120 and 0.15 in the corners (r = 168). This makes the square screen read as a round sensor dish and keeps the corner text zones dim.
- **Byte order:** `framebuf` stores RGB565 little-endian, but the ST7789 expects big-endian bytes. Put the `rgb565_swapped` values from `tokens.json` into the palette.
- **Memory:** the ring map takes 57,600 B and a full RGB565 frame takes 115,200 B. The firmware in `firmware/ESP32_GENERIC-20240602-v1.23.0.bin` is the non-SPIRAM build (going by its filename). Either use the SPIRAM build or render in 240×40 strips of 19,200 B each.
- **Frame budget:** at the current 32 MHz SPI clock, sending one full frame takes about 29 ms. The 25 fps target (40 ms per frame) is therefore limited by transfer time. Keep per-frame palette maths to about 170 LUT lookups and precompute everything else.

---

## 3. Tokens

### 3.1 Colour

Every hex value survives a round trip through RGB565 unchanged: the 8-bit channel value is the bit-replicated 5- or 6-bit value, so `color565(r,g,b)` on these hex values returns exactly the listed `rgb565`. "Swapped" is the byte-swapped value for `framebuf` palettes.

**Neutrals and surfaces**

| Token | Hex | RGB565 | Swapped | Use |
|---|---|---|---|---|
| `bg.base` | `#000000` | `0x0000` | `0x0000` | Background, keylines, dark check mark on gold |
| `bg.iris` | `#000C08` | `0x0061` | `0x6100` | Dark lens behind the CenterGlyph (arrow, chevrons, runes, countdown) |
| `surface.chip` | `#081410` | `0x08A2` | `0xA208` | Backplate for StatusStrip items and DistanceReadout |
| `surface.toast` | `#182018` | `0x1903` | `0x0319` | Toast and Banner fill |
| `line.subtle` | `#394542` | `0x3A28` | `0x283A` | Empty battery and link segments, rune placeholders |
| `text.primary` | `#FFFFFF` | `0xFFFF` | `0xFFFF` | Numerals, labels |
| `text.secondary` | `#C6D7CE` | `0xC6B9` | `0xB9C6` | Status icons (normal state), unit suffix "m" |
| `text.tertiary` | `#7B8E84` | `0x7C70` | `0x707C` | `type.micro` only (battery %, debug) |

**Proximity ramp: green (the RippleField and glyphs)**

| Token | Name | Hex | RGB565 | Swapped | G6 | Contrast vs `bg.base` |
|---|---|---|---|---|---|---|
| `prox.0` | void | `#081810` | `0x08C2` | `0xC208` | 6 | 1.15 |
| `prox.1` | faint | `#082C18` | `0x0963` | `0x6309` | 11 | 1.38 |
| `prox.2` | dim | `#104921` | `0x1244` | `0x4412` | 18 | 2.00 |
| `prox.3` | low | `#187139` | `0x1B87` | `0x871B` | 28 | 3.46 |
| `prox.4` | mid | `#219A4A` | `0x24C9` | `0xC924` | 38 | 5.79 |
| `prox.5` | bright | `#31C363` | `0x360C` | `0x0C36` | 48 | 9.12 |
| `prox.6` | vivid | `#5AE78C` | `0x5F31` | `0x315F` | 57 | 13.24 |
| `prox.7` | blazing | `#B5FFCE` | `0xB7F9` | `0xF9B7` | 63 | 18.16 |

Adjacent steps differ in luminance by a factor of 1.2 to 1.7. The dark end (`prox.0`–`prox.2`) has only 12 green-channel codes in total, so gradients there band visibly. Keep any transition inside that range to 12 px of radius or less, and let ring crests cover it.

**Gold ramp (FOUND only)**

| `found.0` | `found.1` | `found.2` | `found.3` | `found.4` | `found.5` | `found.6` | `found.7` |
|---|---|---|---|---|---|---|---|
| `#181000` `0x1880` | `#392408` `0x3921` | `#6B4510` `0x6A22` | `#9C6918` `0x9B43` | `#D69621` `0xD4A4` | `#FFBA31` `0xFDC6` | `#FFD373` `0xFE8E` | `#FFEFC6` `0xFF78` |

**Grey ramp (SEARCHING, LINK-LOST)**

| `grey.0` | `grey.1` | `grey.2` | `grey.3` | `grey.4` | `grey.5` | `grey.6` | `grey.7` |
|---|---|---|---|---|---|---|---|
| `#080C08` `0x0861` | `#101818` `0x10C3` | `#212829` `0x2145` | `#313C39` `0x31E7` | `#4A5952` `0x4ACA` | `#6B7973` `0x6BCE` | `#94A69C` `0x9533` | `#CED7D6` `0xCEBA` |

**Semantic accents**

| Token | Hex | RGB565 | Swapped | Use |
|---|---|---|---|---|
| `accent.found` → `found.5` | `#FFBA31` | `0xFDC6` | `0xC6FD` | FOUND label, check-mark disc |
| `accent.cold` | `#6BAAC6` | `0x6D58` | `0x586D` | "Colder" chevrons. Never red, because red reads as an error |
| `status.warn` | `#FF9A21` | `0xFCC4` | `0xC4FC` | Battery ≤ 20 %, link at 1 bar, LINK LOST border |
| `status.critical` | `#FF4939` | `0xFA47` | `0x47FA` | Battery ≤ 10 % |
| `glyph.arrow` → `prox.7` | `#B5FFCE` | `0xB7F9` | `0xF9B7` | DirectionArrow fill (tier A) |
| `glyph.outline`, `text.onAccent` → `bg.base` | `#000000` | `0x0000` | `0x0000` | Keylines; glyphs on `found.5`+ or `prox.6`+ |

**Contrast** (WCAG luminance ratio, computed on the RGB565-exact values)

| Foreground | on `bg.base` | on `bg.iris` | on `surface.chip` | on `surface.toast` | on `prox.4` | on `prox.5` |
|---|---|---|---|---|---|---|
| `text.primary` | 21.0 | 19.9 | 18.8 | 16.7 | 3.6 | **2.3** |
| `text.secondary` | 14.0 | 13.3 | 12.5 | 11.1 | 2.4 | 1.5 |
| `text.tertiary` | 6.0 | 5.7 | 5.4 | 4.8 | 1.0 | 1.5 |
| `accent.found` | 12.3 | 11.7 | 11.0 | 9.8 | 2.1 | 1.4 |
| `accent.cold` | 8.2 | 7.8 | 7.3 | 6.5 | 1.4 | 1.1 |
| `status.warn` | 9.9 | 9.4 | 8.9 | 7.9 | 1.7 | 1.1 |
| `status.critical` | 6.3 | 5.9 | 5.6 | 5.0 | 1.1 | 1.5 |
| `prox.7` | 18.2 | 17.2 | 16.3 | 14.4 | 3.1 | 2.0 |

**Rule:** text and numerals sit only on `bg.base`, `bg.iris`, `surface.chip` or `surface.toast`, where every pairing is at least 4.8:1. The two right-hand columns show why text never goes directly on the live field. Glyphs drawn over the field need a 2 px `glyph.outline` keyline (`bg.base` vs `prox.5` is 9.1:1).

### 3.2 Typography

Bitmap faces only, pre-rasterized to `MONO_HLSB` and blitted with a 2-entry palette and `key=0` for a transparent background. Every face is monospaced and numerals are tabular, so the readout doesn't jitter when it changes.

| Token | Size | Cell | Source | Use |
|---|---|---|---|---|
| `type.micro` | 8 px | 8×8 | `framebuf` built-in font | Battery % and debug RSSI only. Never for anything the player acts on |
| `type.label` | 16 px | 8×16, 8 px advance | 8×16 VGA-style bitmap, uppercase subset | Toasts, banners, scan prompts, state words (`FOUND`, `SCAN`). Up to 18 characters per toast |
| `type.numeral` | 32 px | 16×32 | Bold 16×32 digits (same family as `vga1_bold_16x32` in `notebooks/tools.ipynb`), subset `0-9 ~ < +` | DistanceReadout value |
| `type.display` | 48 px | 24×48 | Digits rasterized from an OFL monospace such as Share Tech Mono | Scan countdown 3-2-1 in the iris |

Numerals matter most. The only numbers a player needs are the distance band and the countdown, and each gets its own large size. The 24 px step is left out on purpose: it sits too close to 16 and 32 and would add a fourth glyph set to flash. Words are uppercase and at most two per screen.

### 3.3 Spacing and layout

A 4 px base grid, with 2 px allowed inside icons.

| `space.0` | `.1` | `.2` | `.3` | `.4` | `.5` | `.6` | `.7` | `.8` |
|---|---|---|---|---|---|---|---|---|
| 0 | 2 | 4 | 8 | 12 | 16 | 24 | 32 | 48 |

- **Insets:** bezel 6 px, giving a safe rect of 6..233. Content 12 px, giving a content rect of 12..227.
- **Centre:** overlays use (120,120). The ring map uses (119.5,119.5) so the rings are exactly symmetric on an even-sized screen.

```
  0         12                                          228      240
  +---------------------------------------------------------------+
  |  bezel 6                                                      |
  |   [own batt 12..63]      [link 98..141]    [partner 176..227] | y 12..31  StatusStrip
  |                                                               |
  |                        .-~~~~~~~~-.                           |
  |                     .'   ripples    '.                        |
  |                    /   .--------.     \                       |
  |                   |   /  iris   \      |   iris r = 44 / 64 / 92
  |                   |  |  glyph    |     |   beam r <= 60 (inside iris)
  |                   |   \ (120,120)/     |   sweep band r 70..110 (scan)
  |                    \   '--------'     /                       |
  |                     '.              .'    vignette from r 88  |
  |                        '-~~~~~~~~-'                           |
  |              [ bottom slot x 24..215, y 186..225 ]            |  readout pill or toast
  +---------------------------------------------------------------+
```

The status strip sits above the arrow iris (iris top is at y = 56) and the bottom slot sits below it (iris bottom is at y = 184). The two never overlap the arrow or its beam.

### 3.4 Radii and strokes

| Radius | px | Use |
|---|---|---|
| `radius.none` | 0 | Field, glyphs |
| `radius.sm` | 4 | Battery body, link bars |
| `radius.md` | 6 | Status chips |
| `radius.lg` | 8 | Toast and banner |
| `radius.pill` | h/2 | DistanceReadout |

A rounded rect is two `fill_rect` calls plus four `framebuf.ellipse(..., True, m)` quadrant fills.

| Stroke | px | Use |
|---|---|---|
| `stroke.s` | 2 | Icon outlines, glyph keylines |
| `stroke.m` | 4 | Seeker glyph, tier-C arrow outline, minimum for anything glanced at |
| `stroke.l` | 6 | Runes, turn glyph |
| `stroke.xl` | 10 | Check mark (≈11 px effective) |

`framebuf.line` is 1 px wide, so anything thicker is drawn as a filled quad with `poly`. Don't use 1 px lines in the UI; they vanish in sunlight.

### 3.5 Motion

| Token | Value |
|---|---|
| Frame rate | Target 25 fps (40 ms), minimum 15 fps, 15 fps in low-battery mode |
| `duration.fast / base / slow` | 150 / 250 / 600 ms |
| Hue crossfade | 1500 ms (400 ms into FOUND) |
| Breathing | FOUND 1200 ms, PAIRING 2400 ms, `in_out_sine` |
| Easing | `linear` (ripple travel, sweep), `out_cubic` (enter), `in_cubic` (exit), `in_out_cubic` (crossfades), `in_out_sine` (loops), `smoothstep` (ring edges) |

**Ripple motion per zone.** Wavelength is speed × period, i.e. the gap between consecutive rings.

| Zone | Period | Speed | Wavelength | Lead / trail | Rings visible (to r ≈ 150) |
|---|---|---|---|---|---|
| SEARCHING | 3200 ms | 36 px/s | 115 px | 3 / 22 | ~1.3 |
| FAR | 2400 ms | 40 px/s | 96 px | 3 / 22 | ~1.6 |
| NEAR | 1600 ms | 56 px/s | 90 px | 3 / 20 | ~1.7 |
| WARM | 1000 ms | 80 px/s | 80 px | 3 / 18 | ~1.9 |
| HOT | 500 ms | 120 px/s | 60 px | 2 / 14 | ~2.5 |
| FOUND | 1200 ms | 100 px/s | 120 px | 3 / 24 | burst ring at 240 px/s, then breathing |

**Continuity rules**

- When a zone changes, `floor`, `glow` and `amp` crossfade over 600 ms. The new period and speed apply only to rings that spawn after the change, so rings already in flight never jump.
- The iris opens and closes over 300 ms `out_cubic` by animating its palette radius, which costs nothing extra.
- The arrow angle follows its target with exponential smoothing (τ = 200 ms) along the shortest path, and ignores changes under 4°.
- No full-field brightness change larger than 2 ramp steps in under 333 ms (stay below 3 flashes per second). HOT pulses at 2 Hz are travelling rings, not full-screen flashes.

### 3.6 Haptics

Patterns are lists of milliseconds, alternating on and off and starting with on. Pulses shorter than 30 ms may not spin up the ERM motor, so check on the device. Average duty stays at or below 12 %. Zone ticks fire on the frame a ring spawns, so the player feels the ripple tempo.

| Pattern | ms | When |
|---|---|---|
| `tick_near` | `[40]` every 1600 | NEAR, continuous |
| `tick_warm` | `[40]` every 1000 | WARM, continuous |
| `tick_hot` | `[30]` every 500 | HOT, continuous |
| — | none | FAR and SEARCHING: silence. The first tick itself means "within about 40 m" |
| `zone_closer` | `[30,60,30,60,60]` | Entering a closer zone (rising "da-da-DAA") |
| `zone_farther` | `[120]` | Entering a farther zone (one dull buzz) |
| `wrong_way` | `[200,150,200]` | Trend −2 held for 4 s; at most once every 15 s |
| `found` | `[80,60,80,60,80,200,400]` | Entering FOUND |
| `link_lost` / `relinked` | `[80,80,80,80,80]` / `[40,60,40]` | Link state changes |
| `low_battery` | `[300]` | Own battery crosses 20 %, 10 % or 5 % |
| `scan_countdown` | `[50]` per second | Scan countdown 3-2-1 |
| `scan_tick_30` / `scan_tick_90` | `[30]` / `[70]` | Every 30° / 90° of the sweep, so the player can pace the turn without looking |
| `scan_done` / `scan_no_fix` | `[40,60,120]` / `[120,100,120]` | Scan result |
| `pairing_partner_seen` / `pairing_confirmed` | `[40,60,40]` / `[60,60,150]` | Pairing |

User modes: `off`, `events_only` (no zone ticks), and `full` (the default).

### 3.7 Sound (optional, off by default)

Only on T-Watch revisions that have the I2S speaker amp.

- Zone ping: 2400 Hz for 25 ms, in sync with the haptic tick.
- Found: three rising notes (1568, 1976, 2349 Hz) of 90 ms each.
- Link lost: 880 Hz then 660 Hz, 120 ms each.

---

## 4. Components

### 4.1 RippleField

The palette-cycled background, and the main proximity signal.

| Prop | Type | Description |
|---|---|---|
| `ramp` | `green \| gold \| grey` | Hue ramp: an 8-stop token list expanded into a 64-entry LUT |
| `floor` | 0–7 | Level between rings (intensity at rest) |
| `glow_amp`, `glow_r` | 0–7, px | Centre glow (a halo at the rim when the iris is open) |
| `pulse_amp` | 0–7 | Ring crest intensity |
| `period_ms`, `speed_px_s` | ms, px/s | Tempo and travel speed; wavelength follows from them |
| `lead_px`, `trail_px` | px | Ring edge shape |
| `iris_r` | 0 / 44 / 64 / 92 | Dark lens radius; rings spawn from its edge |
| `vignette` | stops | Fixed at `[[0,1],[88,1],[120,0.4],[168,0.15]]` |

**Anatomy, from the centre outward:** the iris (optional) → a 3 px rim at level `min(7, floor+glow_amp)` → the glow or halo → travelling rings → the vignette.

**States** are the per-zone parameter sets in §5. Transitions are crossfades, never cuts. **Tokens:** `prox.*`, `found.*`, `grey.*`, `bg.iris`, `motion.use.zone_param_crossfade`.

### 4.2 CenterGlyph

A slot at (120,120) that holds exactly one glyph.

| Variant | Iris | Glyph | Colour | Used in |
|---|---|---|---|---|
| `glow` | 0 | none, just the field glow | — | FAR–HOT with no direction and no trend |
| `seeker` | 0 | ring r = 14 plus 4 ticks from r 20 to 28, `stroke.m` | `grey.7` | SEARCHING, LINK-LOST |
| `arrow` | 64 | see DirectionArrow | tiered | FAR–HOT with a valid bearing |
| `chevrons` | 44 | see TrendChevrons | `prox.7` / `accent.cold` | FAR–HOT while walking with a non-zero trend |
| `countdown` | 64 | `type.display` digit | `text.primary` | SCANNING, countdown phase |
| `turn` | 64 | 240° arc r = 22 `stroke.l` with head (turn right) | `prox.6` | SCANNING, sweep phase |
| `check` | 0 | `accent.found` disc r = 40 with a `bg.base` check mark | gold / black | FOUND |
| `runes` | 92 | PairingRunes | `text.primary` | PAIRING |

Priority when several apply: `check` > `arrow` > `chevrons` > `glow`. Changing variant cross-fades the iris over 300 ms, and the new glyph appears when the iris reaches 60 % of its target radius.

### 4.3 DirectionArrow

Points to the partner, relative to the direction the player faced when the scan ended. Screen-up means that direction.

- **Anatomy:** a dart polygon `[(0,-50),(30,34),(0,16),(-30,34)]` rotated about (120,120), drawn on a **beam**: a filled sector from r = 0 to 60 with half-angle σ (clamped to 12–60°), inside a `bg.iris` lens of r = 64. The beam is the uncertainty cone. A narrow beam means a sure bearing; a wide beam means "somewhere over there".

| Prop | Type | Description |
|---|---|---|
| `bearing_deg` | 0–359 | Clockwise from screen-up |
| `sigma_deg` | deg | Uncertainty half-angle. Starts from the scan result and grows 3° per step and 0.5° per second when standing |
| `age_s` | s | Seconds since the scan. The arrow hides after 90 s |

| Tier | σ | Arrow | Beam | Style |
|---|---|---|---|---|
| A (sure) | ≤ 25° | `prox.7` | `prox.4` | Solid |
| B | 25–45° | `prox.6` | `prox.3` | Solid |
| C (fading) | 45–60° | `prox.5`, `stroke.m` outline | `prox.2` | Outline only |
| Hidden | > 60°, or > 90 s | — | — | Falls back to chevrons or glow, plus a `SCAN AGAIN` toast |

Hysteresis: the arrow appears only if σ ≤ 45°, so a new arrow is never born in tier C, and it disappears only when σ > 60°. The arrow enters with a 250 ms `out_cubic` scale from 0.6. After a scan it morphs out of the winning sweep bin over 400 ms. Every arrow tier contrasts with its beam at 3.1:1 or better.

### 4.4 TrendChevrons

The warmer/colder cue when no bearing is known. "Up" means "keep going the way you're facing".

| Trend | Glyph | Colour | Motion |
|---|---|---|---|
| +2 | 2 up-chevrons, stacked 16 px apart | `prox.7` | Nudge up 6 px, 800 ms loop |
| +1 | 1 up-chevron | `prox.6` | Nudge up |
| 0 or not walking | none (CenterGlyph `glow`) | — | — |
| −1 | 1 down-chevron | `accent.cold` | Nudge down |
| −2 | 2 down-chevrons, plus the `wrong_way` haptic after 4 s | `accent.cold` | Nudge down |

The chevron polygon is `[(-24,4),(0,-16),(24,4),(24,14),(0,-6),(-24,14)]`, about 8 px thick, drawn on an iris of r = 44. The trend is the change in smoothed RSSI over 8 s, evaluated every second, and must hold for 2 evaluations. It is shown only while the BMA423 reports walking or running, because standing still produces no gradient.

### 4.5 DistanceReadout

An approximate distance, never a precise one.

- **Anatomy:** a `surface.chip` pill, 40 px tall, centred in the bottom slot. It holds a `type.numeral` value in `text.primary` and a `type.label` "m" suffix in `text.secondary`, with 12 px padding.
- **Values:** `<3`, `~5`, `~10`, `~20`, `~40`, `60+`. Each band is roughly double the last, which matches the ±30–50 % error. The boundaries are 3.5, 7, 14, 28 and 55 m, and the readout changes band only once the estimate crosses a boundary by 15 %.
- **States:** visible in FAR–HOT. In FOUND it is replaced by a `FOUND` label in `accent.found`. It is hidden in PAIRING, SEARCHING, SCANNING and LINK-LOST, and whenever a toast occupies the bottom slot.

### 4.6 StatusStrip

Top row, y 12..31, with three `surface.chip` slots (`radius.md`):

| Slot | x | Content |
|---|---|---|
| Own battery | 12..63 | 22×12 battery icon (`stroke.s`, 2 px nub) with a proportional fill, plus an optional `type.micro` % |
| Link | 98..141 | 4 bars (4 px wide, 2 px gap, heights 4/7/10/13). Measures packet delivery over the last 5 s, not RSSI |
| Partner battery | 176..227 | A 6 px diamond partner mark, then a battery icon |

- **Colours:** `text.secondary` normally, `status.warn` at ≤ 20 % or 1 bar, `status.critical` at ≤ 10 %. Empty segments are `line.subtle`.
- **Visibility:** shown for 3 s after entering any state except SCANNING, and whenever a value is in a warning state. Otherwise hidden, so the ripples stay clean.

### 4.7 SweepGuide (SCANNING)

Guides the player through the maneuvers that establish direction.

**Variant `sweep` (default: a 360° turn at chest height)**

1. **Ready (3 s).** The iris shows `type.display` 3-2-1, the bottom slot reads `HOLD AT CHEST`, and each second plays `scan_countdown`.
2. **Sweep (16 s, 22.5°/s, linear).** A lit 30° sector at r 70..110 in `prox.6` rotates clockwise from 12 o'clock. The iris shows the `turn` glyph. The sweep band overlaps both the StatusStrip and the bottom slot, so neither is drawn during the sweep. Behind the sector, each of the 12 sampled 30° bins draws a radial bar at r 70..(70 + 40·norm RSSI) in `prox.3`. The bin being sampled is drawn in `prox.5`. Haptic `scan_tick_30` plays every 30° and `scan_tick_90` every 90°. The watch has no gyro, so this timing is the only way to know the angle, and the ticks let the player keep pace without looking.
3. **Result.** The best bin flashes twice (200 ms on, 200 ms off, in `prox.7`), then morphs into the DirectionArrow over 400 ms, followed by `scan_done`. If the peak is less than 4 dB above the mean, or σ > 45°, the result is a `NO FIX` toast and `scan_no_fix`, with no arrow.

**Variant `probe` (walk, turn, walk):** a `prox.6` step-progress arc on the iris rim (r 58..64) counts 10 steps from the BMA423 step counter. Then the bottom slot reads `TURN RIGHT 90` with a quarter-arc glyph, then another 10 steps. The resulting arrow starts at σ of at least 35°.

**States:** ready, sweeping, result-ok, result-no-fix, aborted (short press, returning to the previous zone with no arrow).

### 4.8 Toast and Banner

Both use the bottom slot at x 24..215, y 186..225: `surface.toast` fill, `radius.lg`, a 2 px border in the severity colour, a 16×16 icon 12 px from the left, and a `type.label` text (up to 18 characters, uppercase).

| Kind | Severity border | Lifetime | Examples |
|---|---|---|---|
| Toast info | `prox.5` | 2.5 s | `RELINKED`, `SCAN AGAIN`, `NO FIX` |
| Toast warn | `status.warn` | 2.5 s | `BATTERY 20%`, `PARTNER LOW` |
| Banner warn | `status.warn` | Until resolved | `LINK LOST 12S` (counts up) |
| Banner critical | `status.critical` | Until resolved | `BATTERY 5%` |

Motion: in over 200 ms `out_cubic` with a 12 px rise, out over 150 ms `in_cubic`. There is one toast at a time; a banner outranks a toast, and a newer toast replaces an older one.

### 4.9 PairingRunes

A shape-based code comparison that doesn't rely on reading text.

- **Set:** eight original line runes in a 48 px box, drawn with `stroke.l` in `text.primary`: sun (ring and dot), peak (triangle), gate (Π), wave (zigzag), diamond, fork (Y), cross (+) and moon (crescent). Geometry is in `tokens.json → glyphs.runes`. They are deliberately generic shapes and not based on any in-game alphabet.
- **Code:** three runes taken from `hash(sorted(mac_a, mac_b))`, giving 512 combinations. Both watches show the same row, centred at x 64 / 120 / 176, y 120, on a `bg.iris` lens of r = 92.
- **States:**
  - *looking:* three `line.subtle` placeholder dots with a breathing glow and the label `PAIR`.
  - *partner seen:* the runes draw in, one every 150 ms, with `pairing_partner_seen`.
  - *confirmed here:* a short press turns the runes `prox.7` and the label reads `WAITING`.
  - *both confirmed:* `pairing_confirmed`, then SEARCHING.

---

## 5. State table

RippleField values are in ramp units 0–7. `glyph` refers to a CenterGlyph variant.

| State | Ramp | floor | glow amp / r | pulse amp | period / speed | Glyph | Text | Haptic |
|---|---|---|---|---|---|---|---|---|
| PAIRING | green | 0.4 | 1.5↔3.0 breathing 2400 ms / 30 | 0 | — | `runes` (iris 92) | `PAIR` → `WAITING` | `pairing_*` |
| SEARCHING | grey | 0.5 | 1.5 / 18 | 2.5 | 3200 / 36 | `seeker` | `SEARCHING` (toast, once) | none |
| FAR | green | 0.3 | 2.0 / 16 | 3.0 | 2400 / 40 | glow, chevrons or arrow | readout | none |
| NEAR | green | 0.6 | 3.0 / 26 | 4.0 | 1600 / 56 | glow, chevrons or arrow | readout | `tick_near` |
| WARM | green | 1.0 | 4.5 / 38 | 5.0 | 1000 / 80 | glow, chevrons or arrow | readout | `tick_warm` |
| HOT | green | 1.6 | 6.0 / 52 | 5.5 | 500 / 120 | glow, chevrons or arrow | readout | `tick_hot` |
| FOUND | gold | 2.0 | 5.5↔7.0 breathing 1200 ms / 90 | 4.0 plus one burst ring (240 px/s, amp 7) | 1200 / 100 | `check` | `FOUND` | `found` |
| SCANNING | green | 0.3 | 2.0 / 8 (rim halo) | 0 (the sweep dominates) | — | `countdown` → `turn` | `HOLD AT CHEST` (ready phase only); no StatusStrip | `scan_*` |
| LINK-LOST | grey (1.5 s crossfade) | 0.4 | 1.0 / 16 | 0 (rings in flight finish) | — | `seeker` | Banner `LINK LOST <n>S` | `link_lost` |
| LOW-BATTERY (modifier) | unchanged | — | — | ×0.7, v capped at 5 | — | unchanged | Toast at 20 %, banner at 5 % | `low_battery` |

At ≤ 10 % own battery, LOW-BATTERY also drops to 15 fps and sets the backlight to 0.35.

**Transitions.** The thresholds apply to the smoothed distance estimate. They are starting values (`calibrate: true`), not measured facts.

| From → To | Condition |
|---|---|
| PAIRING → SEARCHING | Both players confirm the runes |
| SEARCHING → zone | 3 packets within 2 s. Enters the estimated zone directly, with no dwell |
| FAR ↔ NEAR | Enter NEAR at ≤ 35 m, back to FAR at ≥ 45 m |
| NEAR ↔ WARM | Enter at ≤ 15 m, back at ≥ 20 m |
| WARM ↔ HOT | Enter at ≤ 6 m, back at ≥ 9 m |
| HOT → FOUND | ≤ 2.5 m held for 3 s, **or** a mutual BMA423 tap on both watches within 1 s while HOT ("bump watches") |
| FOUND → next round | A short press on either watch, synced over radio |
| zone → SCANNING | Short press. Result → previous zone plus arrow; abort or NO FIX → previous zone, no arrow |
| any (except PAIRING) → LINK-LOST | No packet for 5 s. The arrow is hidden but its σ keeps growing |
| LINK-LOST → zone | 3 packets within 2 s, then a `RELINKED` toast and haptic |
| LINK-LOST → SEARCHING | 30 s without link |

**Hysteresis.** Every zone boundary has separate enter and exit distances (the exit distance is 30–50 % farther than the entry). A zone must also be held for 3 s before it can change again (the 3 s FOUND hold is the only exception). The estimate itself is an exponential moving average of RSSI over about 2–3 s. As a starting model, `d = 10^((−55 − RSSI) / 22)` puts the 35 / 15 / 6 / 2.5 m thresholds at roughly −89, −81, −72 and −64 dBm. Calibrate these per device pair and environment.

**Input.** A short press starts or cancels a scan, confirms the runes, or starts a new round. A long press (1.5 s) ends the round, confirmed by a second short press within 3 s. A screen tap toggles the backlight between normal (0.6) and boost (1.0). Never map anything near the AXP202's hardware power-off hold.

---

## 6. Do's and don'ts

| Do | Don't |
|---|---|
| Show distance as a band (`~10 m`, `<3 m`, `60+ m`) | Show a precise distance (`12.4 m`) or raw dBm outside debug mode |
| Show the arrow only after a scan or probe, with σ ≤ 45°, and fade it through the tiers as σ grows | Show an arrow when σ > 60°, when it is more than 90 s old, or while the link is lost |
| Draw uncertainty as beam width | Imply a precision you don't have (a thin needle, degree numbers) |
| Treat screen-up as "the way you faced at the end of the scan" | Suggest the arrow is compass-true; the watch has no compass |
| Put text on a chip, the iris or a toast (≥ 4.8:1) | Draw text directly over the ripples (only 2.3:1 on `prox.5`) |
| Add a 2 px `glyph.outline` to glyphs drawn on the field | Use `prox.0`–`prox.2` for glyphs or lines (≤ 2:1 against black, and they band) |
| Change ripple tempo at the next ring spawn and crossfade levels | Reset ring phase or cut the palette on every RSSI update |
| Sync the haptic tick with the ring spawn | Buzz in FAR; silence there makes the first tick meaningful |
| Pair every colour state with a shape, tempo and haptic: FOUND is gold **and** a check mark **and** its own pattern | Rely on green versus gold versus orange alone (hard to tell apart with deuteranopia) |
| Use `accent.cold` blue for "colder" | Use red for "colder"; red means error or critical battery |
| Compose each frame off-screen and push it whole | Draw directly to the panel (`display.fill` then redraw), which causes flicker |
| Keep full-field changes under 3 per second | Flash the whole screen for HOT or FOUND |
| Evoke the sensor feel with radial glow and rings | Copy the Sheikah eye, Hylian, Sheikah or Zonai script, Nintendo fonts or UI chrome |
| Keep words to at most 2 per screen, uppercase, `type.label` or larger | Put anything the player must act on in `type.micro` (8 px) |

---

## 7. Accessibility and field conditions

- **Sunlight:** a screen tap boosts the backlight. The key glyphs use strokes of at least 4 px and fills of `prox.5` or brighter against `bg.iris`, which is at least 8.6:1.
- **Colour vision:** every state is carried by at least three channels: hue, glyph shape, tempo or haptic pattern.
- **Photosensitivity:** travelling rings only. There are no full-field flashes at 3 Hz or above, and the 200 ms double-flash of the best scan bin affects a small sector, not the whole screen.
- **Eyes-free play:** zone tempo, zone changes, wrong-way, FOUND and the scan pacing are all distinct haptic patterns.

## 8. Open questions

- The FOUND rule needs field tests. Is the ≤ 2.5 m / 3 s condition reliable enough without the mutual tap, or should the bump be required?
- The σ growth rate (3° per step) is a guess, because the watch cannot detect body turns. Measure how quickly scan bearings go stale in real play.
- Glance mode (backlight on for 6 s after a BMA423 wrist-tilt) versus an always-on screen: sessions are short (5–30 min), so always-on is the default. Measure the drain on the 380 mAh cell before deciding.
- Should the partner see when you are scanning (a small indicator in their StatusStrip)?
