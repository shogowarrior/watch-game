# Homing design system (v0.2.0)

A small design system for the watch UI of the two-player hide-and-seek game on the LILYGO T-Watch 2020 (240×240 ST7789 IPS, RGB565, one side button, vibration motor, BMA423). The machine-readable source of truth is [`tokens.json`](./tokens.json). If this document and the JSON disagree, the JSON wins.

This document holds the visual tokens and components. Behaviour (screens, zones, timing, haptics and input) is in [`ui-spec.md`](./ui-spec.md).

All pixel values refer to the 240×240 physical display. The panel is 1.54" diagonal, so each side is about 1.09", which works out to roughly 220 ppi: 16 px ≈ 1.8 mm, 32 px ≈ 3.7 mm, 48 px ≈ 5.5 mm.

The look borrows the feel of an ancient-tech proximity sensor: a green glow and ripples that pulse out from the centre, faster and brighter as you get closer. It reuses none of Nintendo's assets, symbols (no eye emblem, no in-game scripts or alphabets), fonts or exact palettes.

---

## 1. Principles

1. **Glanceable in 1–2 s.** Each screen answers one question: *how close?* (brightness and tempo), *which way?* (arrow) or *getting warmer?* (chevrons). Anything else is secondary and hidden by default.
2. **Radial is cheap, everything else costs.** The background is one palette-cycled ring map. Any effect that depends only on distance from the centre is almost free. Overlays are a few filled polygons and bitmap glyphs.
3. **Honest uncertainty.** RSSI ranging is off by roughly ±30–50 %. Distances only appear as coarse bands, and the arrow only appears after a scan or probe. The arrow's beam shows how uncertain the bearing is.
4. **Haptics carry proximity.** The zone tempo is felt as well as seen, and the vibration fires on the same frame as each ripple spawns, so the player doesn't need to stare at the screen.
5. **Never flicker.** Every frame is composed off-screen and pushed whole. Nothing is drawn straight to the panel. Direct-to-panel drawing (`fill` + redraw) is what made the first prototype flicker.

---

## 2. Render model (the constraint behind every token)

```
ring_map  GS8 index map   idx = floor(hypot(x-119.5, y-119.5))  -> 0..168
          (the viper blit reads one quadrant, 14.4 KB, mirrored both ways)
palette   RGB565 FrameBuffer 256x1  rebuilt every frame (entries 0..169; the map uses 0..168)
frame     RGB565 FrameBuffer 240x240 (115,200 B), drawn whole, sent as 4 bands of 240x60

per frame:
  palette[i] = LUT[ramp][ round(v(i,t) / 7 * 63) ]      # radial function -> colour
  for each of the 4 bands:
    band.blit(ring_map rows, 0, 0, -1, palette)          # background, C (viper) speed
  draw every overlay once over the frame (poly / fill_rect / ellipse / glyph blits)
  for each of the 4 bands: display.push_strip(y0, 60, band)   # one window, top to bottom
```

- **Field value**, in ramp units 0–7: `v(i,t) = clamp(0,7, vignette(i) · (floor + glow_amp·e^(-(i/glow_r)²) + pulse_amp·Σ fadein(r_k)·profile(i − r_k(t))))`. The full form, with the core dot, the halo outside an open iris, ghost rings and temporal anti-aliasing, is in ui-spec §4.
- **Rings:** a new ring spawns every `period_ms` at the iris edge (r = 0 when no iris is open) and travels outward at `speed_px_s` (a negative speed means inward "listening" rings that spawn at r = 168 and end at the iris edge). The profile is asymmetric: a sharp leading edge (`lead_px`) and a soft trailing tail (`trail_px`), both smoothstepped. Rings fade in over their first 12 px so they don't pop into existence.
- **Vignette:** 1.0 out to r = 88, falling to 0.4 at r = 120 and 0.15 in the corners (r = 168). This makes the square screen read as a round sensor dish and keeps the corner text zones dim.
- **Byte order:** `framebuf` stores RGB565 little-endian, but the ST7789 expects big-endian bytes. Put the `rgb565_swapped` values from `tokens.json` into the palette (`finder/tuning.py` carries them pre-swapped, and `ui/__init__.py` takes them from there).
- **Memory:** a full ring map would take 57,600 B and a full RGB565 frame 115,200 B. The watch runs stock MicroPython v1.29.0 `ESP32_GENERIC-SPIRAM` (`tools/flash.sh`), so RAM is not the limit: the renderer draws the whole frame in one 115,200 B buffer, allocated once (a collect costs the same whatever is live), so each overlay is drawn once and the panel gets the frame in one ~40 ms push.
- **Frame budget:** stock firmware caps SPI at 26.67 MHz, so sending a full frame takes about 35 ms. The target is 20 fps; frames are locked to an even grid at the fastest of 20, 10, 8, 7, 6 and 5 fps the watch holds (10 fps in saver; ui-spec §4 rule 6). Keep per-frame palette maths to about 170 LUT lookups and precompute everything else; the field works in Q8 integers, so a frame allocates nothing.

---

## 3. Tokens

### 3.1 Colour

Every hex value survives a round trip through RGB565 unchanged: the 8-bit channel value is the bit-replicated 5- or 6-bit value, so the plain (big-endian) RGB565 of these hex values is exactly the listed `rgb565`. "Swapped" is the byte-swapped value for `framebuf` palettes; `hal.st7789.rgb565(r,g,b)` returns it.

**Neutrals and surfaces**

| Token | Hex | RGB565 | Swapped | Use |
|---|---|---|---|---|
| `bg.base` | `#000000` | `0x0000` | `0x0000` | Background, keylines, dark check mark on gold |
| `bg.iris` | `#000C08` | `0x0061` | `0x6100` | Dark lens behind the CenterGlyph (arrow, chevrons, runes, countdown) |
| `surface.chip` | `#081410` | `0x08A2` | `0xA208` | Backplate for StatusStrip items and DistanceReadout |
| `surface.toast` | `#182018` | `0x1903` | `0x0319` | Toast and Banner fill |
| `line.subtle` | `#394542` | `0x3A28` | `0x283A` | Empty battery and link segments, rune placeholders |
| `text.primary` | `#FFFFFF` | `0xFFFF` | `0xFFFF` | Countdown digits, words, labels |
| `text.secondary` | `#C6D7CE` | `0xC6B9` | `0xB9C6` | Status icons (normal state), readout numerals and unit suffix "M" |
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
| `status.warn` | `#FF9A21` | `0xFCC4` | `0xC4FC` | Battery ≤ 20 %, link at 1 bar or unreliable, LINK-LOST banner border |
| `status.critical` | `#FF4939` | `0xFA47` | `0x47FA` | Battery ≤ 10 % |
| `glyph.arrow` → `prox.7` | `#B5FFCE` | `0xB7F9` | `0xF9B7` | DirectionArrow fill (`solid_a`) |
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

Bitmap faces only, pre-rasterized to `MONO_HLSB` and blitted with a 2-entry palette and `key=0` for a transparent background. Every face is scaled from MicroPython `framebuf`'s built-in 8×8 font (`ui/font.py`), so no font file has to be licensed or shipped; the scale is wider vertically so strokes stay square. Every face is monospaced and numerals are tabular, so the readout doesn't jitter when it changes.

| Token | Size | Cell | Scale of the 8×8 font | Use |
|---|---|---|---|---|
| `type.micro` | 8 px | 8×8 | 1 × 1 | Battery % and debug only. Never for anything the player acts on |
| `type.label` | 16 px | 8×16, 8 px advance | 1 × 2 | Chips, toasts, banners, menu rows. Up to 18 characters |
| `type.word` | 32 px | 16×32 | 2 × 4 | Bottom-slot words read while moving (`SEARCHING`, `BUMP!`, `FOUND`). Up to 10 characters |
| `type.numeral` | 32 px | 16×32 | 2 × 4 (same face as `type.word`), subset `0-9 ~ < +` | DistanceReadout value |
| `type.display` | 48 px | 24×48 | 3 × 6 | Countdown digits in the iris |

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
| `radius.sm` | 4 | Unused (battery body and link bars are square) |
| `radius.md` | 6 | Status chips |
| `radius.lg` | 8 | Toast and banner |
| `radius.pill` | h/2 | DistanceReadout |

A rounded rect is two `fill_rect` calls plus four `framebuf.ellipse(..., True, m)` quadrant fills.

| Stroke | px | Use |
|---|---|---|
| `stroke.s` | 2 | Icon outlines, glyph keylines |
| `stroke.m` | 4 | Seeker glyph, `outline`-tier arrow outline, minimum for anything glanced at |
| `stroke.l` | 6 | Runes, turn glyph |
| `stroke.xl` | 10 | Check mark (≈11 px effective) |

`framebuf.line` is 1 px wide, so anything thicker is drawn as a filled quad with `poly`. Don't use 1 px lines in the UI; they vanish in sunlight.

### 3.5 Motion

| Token | Value |
|---|---|
| Frame rate | Target 20 fps (50 ms); locked to 20, 10, 8, 7, 6 or 5 fps (`motion.fps.locks`), 10 fps in saver |
| `duration.fast / base / slow` | 150 / 250 / 600 ms |
| Hue crossfade | 1500 ms (400 ms into FOUND) |
| Breathing | FOUND standing wave 2400 ms (1 ramp step per 1.2 s half-cycle), PAIRING 2400 ms, `in_out_sine` |
| Easing | `linear` (ripple travel, sweep), `out_cubic` (enter), `in_cubic` (exit), `in_out_cubic` (crossfades), `in_out_sine` (loops), `smoothstep` (ring edges) |

**Ripple motion.** Wavelength is speed × period, the gap between consecutive rings. The zone tempo table is in ui-spec §5.3, and the field values of the other screens are in the ui-spec §6 screen tables.

**Continuity rules**

- When a zone changes, `floor`, `glow` and `amp` crossfade over 600 ms. The new period and speed apply only to rings that spawn after the change, so rings already in flight never jump.
- The iris opens and closes over 300 ms `out_cubic` by animating its palette radius, which costs nothing extra.
- The arrow angle follows its target with exponential smoothing (τ = 200 ms) along the shortest path, and ignores changes under 4°.
- No full-field brightness change larger than 2 ramp steps in under 333 ms (stay below 3 flashes per second). HOT pulses at 2 Hz are travelling rings, not full-screen flashes.

### 3.6 Haptics

Patterns are lists of milliseconds, alternating on and off and starting with on. Every pulse and gap is at least 60 ms (ERM spin-up), and average duty stays at or below 12 %. Zone heartbeats fire on the frame a ring spawns, so the player feels the ripple tempo. The 9 patterns, their meaning, the priority queue and blanking are in ui-spec §7 (tokens `haptics`). Menu modes: `FULL` (the default), `EVENTS` (no heartbeats) and `OFF`.

### 3.7 Sound (optional, off by default, not built)

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

**Anatomy, from the centre outward:** the iris (optional) → a 3 px rim at level `clamp(5, 7, floor+glow_amp)` → the glow or halo → travelling rings → the vignette.

**States** are the per-screen parameter sets in ui-spec §5.3 and §6. Transitions are crossfades, never cuts. **Tokens:** `prox.*`, `found.*`, `grey.*`, `bg.iris`, `motion.use.zone_param_crossfade`.

### 4.2 CenterGlyph

A slot at (120,120) that holds exactly one glyph.

| Variant | Iris | Glyph | Colour | Used in |
|---|---|---|---|---|
| `glow` | 0 | none, just the field glow | — | FAR–HOT with no direction and no trend |
| `seeker` | 44 | ring r = 14 plus 4 ticks from r 20 to 28, `stroke.m` | `grey.7` | SEARCHING, LINK-LOST |
| `arrow` | 64 | see DirectionArrow | tiered | FAR–HOT with a valid bearing |
| `chevrons` | 44 | see TrendChevrons | `prox.7` / `accent.cold` | FAR–WARM while walking with a non-zero trend; PAIRING `howto` card 3 (an example) |
| `countdown` | 64 | `type.display` digits | `text.primary` | SCANNING `ready`, PAIRING `calibrate` and `split`, PAIRING `howto` card 2 |
| `turn` | 64 | 240° arc r = 22 `stroke.l` with head (turn right) | `prox.6` | SCANNING `sweep` |
| `check` | 0 | `accent.found` disc r = 40 with a `bg.base` check mark | gold / black | FOUND |
| `runes` | 92 | PairingRunes (3 placeholder dots in `looking`) | `text.primary` | PAIRING (`howto` card 1: an example row) |
| `battery` | 64 | 64×32 battery outline in `status.warn`, fill proportional | `status.warn` | LOW-BATTERY 10 % interstitial |
| `bump` | 64 | two watch outlines (yours left, friend's right) + 3 rays, 4 px strokes | `prox.6` / `prox.7` lit / `grey.5` friend not ready | HOT bump-ready; PAIRING `howto` card 4 (both ready, neither lit) |

Priority when several apply: `check` > `arrow` in `reveal`/`turn` > `bump` > `arrow` > `chevrons` > `glow`. Changing variant animates the iris radius over 300 ms `out_cubic`; the new glyph is drawn at once (ui-spec §2).

### 4.3 DirectionArrow

Points to the partner, relative to the way the player faced for the scan (screen-up). The watch has no compass, so it never means north.

- **Anatomy:** a dart polygon `[(0,-50),(30,34),(0,16),(-30,34)]` rotated about (120,120), drawn on a **beam**: a filled sector from r = 0 to 60 with half-angle σ (clamped to 12–60°), inside a `bg.iris` lens of r = 64. The beam is the uncertainty cone. A narrow beam means a sure bearing; a wide beam means "somewhere over there".
- **Props:** the RenderParams fields `arrow_deg` (clockwise from screen-up), `cone_deg` (σ, clamped) and `arrow_style` (the tier below). How σ grows with steps and time, and the 120 s age limit, are in ui-spec §5.6.

| Tier (`arrow_style`) | σ | Arrow | Beam | Style |
|---|---|---|---|---|
| `solid_a` (sure) | ≤ 25° | `prox.7` | `prox.4` | Solid |
| `solid_b` | 25–45° | `prox.6` | `prox.3` | Solid |
| `outline` (fading) | 45–60° | `prox.5`, `stroke.m` outline | `prox.2` | Outline only |
| Hidden | > 60°, or older than 120 s | — | — | Falls back to chevrons or glow, plus a `SCAN AGAIN` toast |

Hysteresis: the arrow appears only if σ ≤ 45°, so a new arrow is never born in the `outline` tier, and it disappears only when σ > 60°. The arrow enters with a 250 ms `out_cubic` scale from 0.6. After a scan it morphs out of the winning sweep bin over 400 ms. Every arrow tier contrasts with its beam at 3.1:1 or better.

### 4.4 TrendChevrons

The warmer/colder cue when no bearing is known. "Up" means "keep going the way you're facing".

| `trend` | Glyph | Colour | Motion |
|---|---|---|---|
| +1 (warmer) | 1 filled up-chevron; with `trend_strong`, 2 copies centred at y 112 / 128 | `prox.7` | Nudge up 6 px, 800 ms loop |
| 0 | none (CenterGlyph `glow`) | — | — |
| −1 (colder) | 1 hollow down-chevron (4 px outline, `bg.iris` inside); with `trend_strong`, 2 copies at y 107 / 133 | `accent.cold` | Nudge down |

The chevron polygon is `[(-24,4),(0,-16),(24,4),(24,14),(0,-6),(-24,14)]`, about 8 px thick, drawn on an iris of r = 44. When a trend is shown, and how it is gated against noise, is in ui-spec §5.5.

### 4.5 DistanceReadout

An approximate distance, never a precise one.

- **Anatomy:** a `surface.chip` pill, 40 px tall, centred in the bottom slot. It holds a `type.numeral` value and a `type.label` "M" suffix, both in `text.secondary`, with 12 px padding.
- **Values:** `<3`, `~5`, `~10`, `~20`, `~40`, `60+`. Each band is roughly double the last, which matches the ±30–50 % error. Band edges and hysteresis: ui-spec §5.4.
- **States:** visible in FAR–HOT, with a 12 px trend mark left of the numeral while an arrow is shown. Hidden in PAIRING, SEARCHING, SCANNING and FOUND. In LINK-LOST the last band moves to the top-slot `LAST` chip. A word, toast or banner takes the bottom slot first (ui-spec §2).

### 4.6 StatusStrip

Top row, y 12..31, with three `surface.chip` slots (`radius.md`):

| Slot | x | Content |
|---|---|---|
| Own battery | 12..63 | 22×12 battery icon (`stroke.s`, 2 px nub) with a proportional fill, plus an optional `type.micro` % |
| Link | 98..141 | 4 bars (4 px wide, 2 px gap, heights 4/7/10/13). Measures packet delivery over the last 5 s, not RSSI |
| Partner battery | 176..227 | A 6 px diamond partner mark, then a battery icon |

- **Colours:** `text.secondary` normally, `status.warn` at ≤ 20 % or 1 bar, `status.critical` at ≤ 10 %. The link bars also turn `status.warn` while the signal is unreliable (ui-spec §5.5). Empty segments are `line.subtle`.
- **Visibility:** in SEARCHING and FAR–HOT for 3 s after a wake, and pinned while own battery is ≤ 20 % or the signal is unreliable. In LINK-LOST only when pinned by the battery. Otherwise hidden, so the ripples stay clean.

### 4.7 SweepGuide (SCANNING)

Guides the 360° body turn that gives a direction. The phases, timing, haptics, faults and the fit are in ui-spec §6 SCANNING; this is what gets drawn.

1. **Ready.** The iris shows the `type.display` countdown. Its rim is `prox.6` while the watch is flat and `status.warn` while it is not.
2. **Sweep.** A lit 30° sector at r 70..110 in `prox.6` (`status.warn` while paused) rotates clockwise from 12 o'clock. The iris shows the `turn` glyph. Behind the sector, 12 radial bars (6° wide, centred on k·30°) run from r 70 to 70 + 40·norm(bin) in `prox.3`, the active bin in `prox.5`; a bin with too few packets is a hollow 2 px outline of its full box. The band overlaps the StatusStrip and the bottom slot, so neither is drawn.
3. **Result.** The best bar blinks twice (200 ms on, 200 ms off, `prox.7`, one bar only), then morphs into the DirectionArrow over 400 ms.

The DIRECTION `turn` phase reuses the sector as its pacer wedge. A `probe` variant (walk, turn, walk; R-15) is not built.

### 4.8 Toast and Banner

Both use the bottom slot at x 24..215, y 186..225: `surface.toast` fill, `radius.lg`, a 2 px border in the severity colour, and centred `type.label` text (up to 18 characters, uppercase).

| Kind | Severity border | Lifetime | Examples |
|---|---|---|---|
| Toast info | `prox.5` | 2.5 s | `BACK IN RANGE`, `SCAN AGAIN`, `NEW ROUND`, `ONLY YOU FELT IT`, `PRESS 2X TO SCAN`, `SWIPE: HOW TO PLAY` |
| Toast warn | `status.warn` | 2.5 s | `BATTERY 20%`, `FRIEND BATT 20%`, `FRIEND LEFT` |
| Toast critical | `status.critical` | 2.5 s | `BATTERY 5%` |
| Banner warn | `status.warn` | Until resolved | `SIGNAL LOST`, then `LOST: GO BACK` / `LOST: KEEP ON` (no clock), `FRIEND LOW BATTERY` |
| Banner critical | `status.critical` | Until resolved | `FRIEND IS OFF` |

Motion: in over 200 ms `out_cubic` with a 12 px rise, out with a 12 px fall over 150 ms `in_cubic`. There is one toast at a time; a banner outranks a toast, and a newer toast replaces an older one.

### 4.9 PairingRunes

A shape-based code comparison that doesn't rely on reading text.

- **Set:** eight original line runes in a 48 px box, drawn with `stroke.l` in `text.primary`: sun (ring and dot), peak (triangle), gate (Π), wave (zigzag), diamond, fork (Y), cross (+) and moon (crescent). Geometry is in `tokens.json → glyphs.runes`. They are deliberately generic shapes and not based on any in-game alphabet.
- **Code:** three runes taken from `hash(sorted(mac_a, mac_b))` (`finder/pairing.py`), giving 512 combinations. Both watches show the same row, centred at x 64 / 120 / 176, y 120, on a `bg.iris` lens of r = 92.
- **States:** three `line.subtle` placeholder dots while looking; the runes draw in one every 150 ms once the partner is seen, and turn `prox.7` once confirmed here. The sub-states and their texts are in ui-spec §6 PAIRING.

---

## 5. States

Which values each screen uses, how the screens change and what the inputs do is behaviour, so it lives in [`ui-spec.md`](./ui-spec.md): zones, hysteresis and dwell (§5.2), zone tempo (§5.3), arrow σ (§5.6), screens and transitions (§6), haptics (§7) and input (§8). `tokens.json` `states` holds only the field presets those tables use (glyphs, texts and haptics are in ui-spec §6/§7).

---

## 6. Do's and don'ts

| Do | Don't |
|---|---|
| Show distance as a band (`~10 m`, `<3 m`, `60+ m`) | Show a precise distance (`12.4 m`) or raw dBm outside debug mode |
| Show the arrow only after a scan or probe, with σ ≤ 45°, and fade it through the tiers as σ grows | Show an arrow when σ > 60°, when it is more than 120 s old, or while the link is lost |
| Draw uncertainty as beam width | Imply a precision you don't have (a thin needle, degree numbers) |
| Treat screen-up as "the way you faced for the scan" | Suggest the arrow is compass-true; the watch has no compass |
| Put text on a chip, the iris or a toast (≥ 4.8:1) | Draw text directly over the ripples (only 2.3:1 on `prox.5`) |
| Add a 2 px `glyph.outline` to glyphs drawn on the field | Use `prox.0`–`prox.2` for glyphs or lines (≤ 2:1 against black, and they band) |
| Change ripple tempo at the next ring spawn and crossfade levels | Reset ring phase or cut the palette on every RSSI update |
| Sync the haptic heartbeat with the ring spawn | Buzz without live packets; silence must always mean "no link" |
| Pair every colour state with a shape, tempo and haptic: FOUND is gold **and** a check mark **and** its own pattern | Rely on green versus gold versus orange alone (hard to tell apart with deuteranopia) |
| Use `accent.cold` blue for "colder" | Use red for "colder"; red means error or critical battery |
| Compose each frame off-screen and push it whole | Draw directly to the panel (`display.fill` then redraw), which causes flicker |
| Keep full-field changes under 3 per second | Flash the whole screen for HOT or FOUND |
| Evoke the sensor feel with radial glow and rings | Copy the games' eye emblem, in-game scripts or alphabets, Nintendo fonts or UI chrome |
| Keep words to at most 2 per screen, uppercase, `type.label` or larger | Put anything the player must act on in `type.micro` (8 px) |

---

## 7. Accessibility and field conditions

Sunlight, colour vision, photosensitivity and eyes-free play are covered in ui-spec §11.
