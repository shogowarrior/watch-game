# AGENTS.md

Guide for coding agents working in this repo. Read it before you change anything.

## What this is

A two-player "find each other" game for two **LILYGO T-Watch 2020 V1** watches
(ESP32, ST7789 240x240, AXP202 PMU, BMA423 accelerometer with no gyro and no
compass, FT6336 touch, vibration motor). The watches broadcast 16-byte ESP-NOW
beacons. Each watch turns the partner's RSSI plus both watches' step and
activity hints into a distance estimate, and shows it as a green ripple field
(a "Sheikah sensor" homage, codename *Sheikah Finder*). A guided 360° body-turn
scan gives a direction arrow. The round ends when the players physically bump
watches. The watch runs **stock MicroPython v1.29.0 (`ESP32_GENERIC-SPIRAM`)**,
pure `.py`, no custom C modules.

Status: every layer is written and tested on CPython, on MicroPython
(WebAssembly) and in a two-watch simulator. **It has not yet run on real
watches.** Every threshold is a starting value to calibrate.

## Repo map

| Path | What |
|---|---|
| `boot.py` | Minimal: silences IDF logs. No Wi-Fi, no webrepl, no app code. |
| `main.py` | Safe-boot check (`/noapp` or side-key double press / hold), `Board().init()`, then `app.run(board, watchdog_ms=8000)`. |
| `app/runtime.py` | The watch main loop `Runtime.step(now)`: radio, imu, touch, button, logic (10 Hz), render, tx, haptic, gc. |
| `app/imu_feed.py` | BMA423 FIFO (100 Hz mg) -> `MotionTracker` at 25 Hz in g, plus the bump spike detector. |
| `app/telemetry.py` | JSONL telemetry for field tests (`session()`; main.py turns it on when `/tele` exists). |
| `hal/` | The only code that touches hardware. See `hal/README.md` (drivers, gotchas, bench tools). |
| `hal/pins.py` | GPIO map, bus ids, addresses, clock limits. V1 only. |
| `hal/st7789.py` `axp202.py` `bma423.py` `ft6336.py` `haptics.py` `radio.py` `watchdog.py` | Display, PMU + side key, accelerometer, touch, motor, ESP-NOW (`EspNowRadio` / `SimRadio`), loop watchdog. |
| `hal/board.py` | `Board` (lazy parts, one shared I2C0), `safe_boot`. |
| `finder/` | Pure game logic. No hardware imports. |
| `finder/compat.py` | Tick helpers and MicroPython portability rules. |
| `finder/tuning.py` | **Generated** from `docs/design/tokens.json`. Never hand-edit. |
| `finder/proto.py` | 16-byte `Beacon` wire format. |
| `finder/link.py` | `LinkMonitor` (partner MAC lock, dedup, loss) and `TxScheduler`. |
| `finder/estimators/` | Range estimators behind one interface (`base.py`); `DEFAULT = "kalman2"`, `make()`. |
| `finder/motion.py` | `MotionTracker`: steps, cadence, stillness, tilt, face-up. No double integration. |
| `finder/proximity.py` | Distance -> proximity, zone with hysteresis, readout band, gated trend. |
| `finder/scan.py` | `ScanSession`: guided 360° turn, circular-harmonic fit of raw RSSI vs body angle. |
| `finder/arrow.py` | DIRECTION arrow lifecycle (reveal, turn, lock, walk, expire). |
| `finder/pairing.py` | PAIRING sub-states, runes, 1 m calibration, split countdown. |
| `finder/session.py` | Beacon state byte, partner view, bump timing. |
| `finder/gestures.py` `haptic_patterns.py` | Touch gesture recognizer; the 9 haptic patterns and their player. |
| `finder/game.py` | `Game`: the state machine. Inputs in, `RenderParams` out (read its docstring). |
| `finder/menu.py` | `Menu`: the MENU list (rows, scroll, END ROUND confirm, auto-close). |
| `finder/render_params.py` | `RenderParams`, the only thing the renderer reads (ui-spec §3). |
| `ui/` | Strip renderer: `renderer.py` (10 strips of 240x24), `field.py` (ripple palette), `glyphs.py`, `text.py`, `font.py`. Colours in `ui/__init__.py` are byte-swapped RGB565. |
| `sim/` | Two-watch simulator: `world.py`, `radio.py` (RSSI profiles clean/typical/harsh/indoor, per-watch beacon period), `imu.py`, `accel_synth.py`, `scenarios.py`, `rng.py`, `link.py` (`GameLink`: beacon hand-off between two Games), `Sim`; `webhost.py` drives the browser sim. |
| `web/sim/index.html` | Browser simulator page (runs the real `finder/`, `ui/`, `sim/` in MicroPython WebAssembly). |
| `tests/` | `runner.py`, `test_*.py`, `fakes/` (fake `machine`, `network`, `espnow`), `est_helpers.py` (shared estimator fixtures), `test_deploy.py` (`tools/deploy.py`, CPython only). |
| `tools/` | Host and on-watch scripts (see Commands). `tools/mpy/run.mjs` runs Python under MicroPython WebAssembly; `tools/cli.py` is the shared `--key value` parser. |
| `docs/project/` | `handoff.md` (current state, open questions, next steps: read first); the Claude Project's `goal.md`, `instructions.md` and `setup.md` (how to create it). |
| `docs/design/` | `ui-spec.md` (behaviour), `design-system.md`, `tokens.json`, `snapshots/*.png`, `debug-mode.md` (the next feature, specified, not built). |
| `docs/estimation/` | `bakeoff.md` (why kalman2), `imu-drift.md` (why no dead reckoning). |
| `docs/research/user-research.md` | Personas, field-test plan, requirements R-01..R-15. |
| `docs/architecture.md` `docs/hardware-setup.md` | Layers and data flow; bring-up on real watches. |
| `notebooks/` | Jupyter "MicroPython - USB" notebooks. `finder_dev.ipynb` is the current one. `watch.ipynb` and `tools.ipynb` are legacy (old custom firmware) and do not run on stock v1.29. |
| `firmware/` | Old firmware images. **Do not touch.** |
| `.claude/settings.json` `.claude/hooks/cloud-setup.sh` | SessionStart hook: in cloud sessions only, installs the `tools/mpy` package. |
| `.claude/workflows/` | `review-fix-round.js` (verified review/fix round) and `debug-mode-build.js`; args in each header. |
| `typings/` | MicroPython stubs for the IDE (v1.23; the watch runs v1.29, so a few newer APIs are missing from them). |

## Tests: run both runtimes

```sh
python3 tests/runner.py                    # CPython
cd tools/mpy && npm install && cd -        # once: MicroPython WebAssembly port
node tools/mpy/run.mjs tests/runner.py     # real MicroPython 1.29 (WebAssembly)
python3 tests/runner.py test_game          # one module (either runner; tests/test_game.py works too)
```

Both must pass before you call anything done: read the result line,
`[impl] N passed, S skipped, K failed`. An unknown module, or a run in which
nothing passed or failed, exits 1. CPython catches logic errors fast. The
MicroPython run catches what CPython hides: missing stdlib (`dataclasses`,
`typing`, `collections` extras), `namedtuple` without `_fields`/`_replace`,
integer and float differences, `framebuf` (renderer frame tests only run under
MicroPython), and allocation behaviour. A test that cannot run on one runtime
raises `tests.Skip("reason")` (the renderer frame tests on CPython, the
`tuning.py` staleness check on MicroPython). That is expected.

Conventions: plain modules `tests/test_*.py` with `test_*` functions that use
`assert`. No pytest, no unittest. Hardware tests call `tests.fakes.install()`
**before** importing anything from `hal`, and the fakes record I2C writes, SPI
traffic and ESP-NOW frames for assertions. `tests/test_episode.py` is a headless
two-watch episode (Game + sim) that must reach FOUND on both watches, and it
shows how to wire everything. Scripts read `sys.argv`; `tools/mpy/run.mjs`
sets it as CPython would.

## Commands

| Command | What |
|---|---|
| `python3 tools/gen_tuning.py` / `--check` | Regenerate `finder/tuning.py` from `tokens.json` / exit 1 if stale. |
| `python3 tools/bakeoff.py --quick` | Estimator bake-off on the simulator. Full options in its docstring (`--est`, `--seeds`, `--profiles`, `--scenarios`, `--imu`, `--out`). |
| `python3 tools/render_snapshots.py [name ...]` | Render `RenderParams` fixtures through the real renderer (via the WebAssembly port) into `docs/design/snapshots/*.png`, and their frame CRCs into `tests/snapshot_crc.json` (checked by `test_renderer`). |
| `node tools/mpy/run.mjs tools/bench_est.py` | Per-packet cost and heap per estimator. |
| `node tools/mpy/run.mjs tools/bench_webhost.py` | Cost of one browser-sim step. |
| `python3 tools/drift_demo.py` | Why accelerometer double integration fails. |
| `python3 tools/build_sim.py` | Build the web simulator into `dist/sim/` (needs `tools/mpy` npm install). |
| `mpremote run tools/bench_display.py` | **On the watch**: real SPI clock, push/blit timings, fps. |
| `mpremote run tools/bench_frame.py` | **On the watch**: where each frame's ms go (step, plan, palette, field, overlays, push) with the background push off and on, push variants, IMU cost per rate, which kernels run as viper, and 10 s of the game loop with bump sensing off then on. |
| `tools/radio_pingpong.py` | **On two watches**: ESP-NOW delivery, RTT, RSSI (see `hal/README.md`). |
| `tools/flash.sh <port>` | Erase and flash stock v1.29 SPIRAM. The **user** runs this; it asks y/N. |
| `tools/fetch_bma423_config.sh` | Download and sha256-check the optional `bma423conf.bin`. |
| `python3 tools/deploy.py [--port P] [-n] [--noapp\|--app] [--tele DEV\|--no-tele]` | Hard-reset the watch, copy `app/`, `finder/`, `hal/`, `ui/` (+ `bma423conf.bin`) and last `boot.py`, `main.py` with mpremote, then hard-reset again so `main.py` starts the game. `--tele DEV` makes the game log to `/log/<n>_DEV.jsonl`. |

Agents: do not flash, erase or deploy to a watch, and do not download
firmware, unless the user explicitly asks in chat. Never write into `firmware/`.

## Hard rules and why

1. **MicroPython 1.29 compatible** for everything under `app/`, `finder/`,
   `hal/`, `ui/`, `sim/` and anything the browser loads (`finder/compat.py`):
   take tick helpers from `finder.compat` (`ticks_ms`, `ticks_diff`,
   `ticks_add`), never compare raw ticks (they wrap at 2^30 ms); no
   `dataclasses`, `typing`, `numpy`, `f"{x=}"` specifiers, or `deque` beyond
   `append`/`popleft`; use `RenderParams` helpers (`FIELDS`, `replace`,
   `to_dict`) instead of namedtuple extras. Why: this code runs unmodified on
   the watch and in the browser, and both runners are the only proof.
2. **Allocation-light hot paths; the render loop allocates nothing.** Per-packet,
   per-sample and per-frame code reuses preallocated buffers; floats are heap
   objects on MicroPython, so the renderer and ripple field work in Q8
   integers. Why: a collect on the watch's 4 MB SPIRAM heap takes about 70 ms
   and would stutter the frame and the haptic timing. `tools/bench_est.py` and
   the renderer tests measure it. The hottest integer loops (palette, field
   blit, FIFO decode, IMU feed) are one source string each, run as plain
   Python (identity `ptr` shims) on CPython and wasm, and compiled with
   `@micropython.viper` on the watch only after a self-check against the plain
   version or the framebuf path; `tools/bench_frame.py` prints which ran.
   Viper needs 32-bit words: on a 64-bit unix port the self-checks fail
   (`ptr32` loads are not sign-extended) and the plain versions run. The
   watch's display sends strips from a background thread while the next one
   is drawn: a buffer given to `push_strip` stays untouched until the next
   `push_strip` returns, so the renderer alternates two strip buffers.
3. **Byte-swapped RGB565.** `framebuf` stores RGB565 little-endian, the ST7789
   wants MSB-first. Every colour given to a framebuf, palette or `fill` goes
   through `rgb565()`/`swap16()`; `ui/__init__.py` takes the pre-swapped values
   from `finder/tuning.py` (generated from `tokens.json`).
4. **SPI <= 26.67 MHz and `miso=None`.** Stock firmware crashes above 80/3 MHz on
   GPIO-matrix pins (`ST7789` raises unless `fast=True`, which needs a custom
   build). A MISO pin would claim GPIO12, which is the backlight.
5. **AXP202: never clear DCDC3 (reg 0x12 bit1), never cut LDO2.** DCDC3 is the
   ESP32's own supply: clearing it browns the watch out. LDO2 powers the panel
   *and* the backlight on the V1: cut it and the display loses its init. Screen
   off means `display.sleep()` and backlight duty 0, not LDO2 off. The game only
   shuts the PMU down after `game.power_off`.
6. **One I2C0.** `board.i2c0` (pins 21/22) is shared by the AXP202, BMA423 and
   RTC. Never open a second I2C on those pins, and never touch the bus from an
   IRQ handler (all interrupts are polled).
7. **`tokens.json` -> `tools/gen_tuning.py` -> `finder/tuning.py`.** Change a
   value in `docs/design/tokens.json` (or in `SPEC` in `gen_tuning.py` for
   ui-spec-only values), regenerate, and keep `--check` green. Never hand-edit
   `tuning.py`. The generator fails on any token string it parses (listed in
   the `tools/gen_tuning.py` docstring) that no longer matches its full
   template, and on tokens that repeat a value but disagree, so a changed rule
   text needs a generator change too.
8. **`docs/design/ui-spec.md` is the behaviour source of truth** (if it and
   `tokens.json` disagree on a value, `tokens.json` wins). Change the spec first,
   then code and tests. Where the spec is silent, the choice is documented in the
   module docstring (see `finder/game.py`).
9. **No accelerometer double integration.** The BMA423 bias gives metres of error
   in seconds and there is no gyro or compass, so yaw is unobservable. Use steps
   x stride, activity and stillness only (`docs/estimation/imu-drift.md`).
10. **RSSI alone never declares FOUND.** FOUND needs a matched physical bump (both
    accelerometer spikes within 400 ms, each made in HOT, while both watches are
    in HOT) or the fallback (both short presses within 3 s in HOT). RSSI at close
    range is dominated by multipath and body shadowing.
11. **No metres, dBm, degrees or zone words on screen.** Distance shows only as
    bands (`<3`, `~5`, `~10`, `~20`, `~40`, `60+`); zones are felt through tempo
    and haptics, thermal words are for the trend only (ui-spec §12). RSSI cannot
    support precise numbers, and false precision was the top usability risk.
12. **Only `hal/` imports `machine`, `network`, `espnow`.** `finder/` stays pure
    so it runs in tests and the browser; `app/` gets hardware through `Board`.
13. Other ui-spec §12 "don'ts" hold everywhere: no haptic pulse under 60 ms, no
    new patterns beyond the 9, no full-screen flashes, no Nintendo assets or
    "Sheikah" on screen, nothing mapped near the AXP202 power-off hold.

14. **GPIO12 is a boot strapping pin (flash voltage).** On the V1 it is also the
    backlight. It must be LOW at reset, so never add a pull-up to it, never claim it
    as SPI MISO, and never drive it high from `boot.py`. (Checked with the esp32
    plugin's `validate_pinmap.py`; its GPIO37/38 "not exposed" errors are false for
    this bare-chip board, where both are input-only interrupt lines.)
15. **The game runs under a watchdog.** `main.py` starts `app.run(board,
    watchdog_ms=8000)` (`hal/watchdog.py`). It starts as a timer watchdog on USB
    power (switched off when the loop exits, so Ctrl-C leaves a usable REPL) and
    switches once, for good, to the ESP32 hardware WDT (cannot be stopped; a hung
    loop reboots) at the first 10 s battery reading without VBUS. A game started
    on battery uses the hardware WDT from the start, so `tools/deploy.py`
    hard-resets the watch before copying. Notebooks call `app.run()` without it.
    Feed nothing else: the loop feeds it once per pass.
16. **Distance needs the right environment.** RSSI to metres uses the path-loss
    exponent from `tokens.json` (`calibrate.n` 2.6 outdoors, `n_indoor` 3.0 indoors
    or in crowds) via `Game.set_place(indoor)`. One exponent cannot fit both
    (docs/estimation/bakeoff.md section 6). Players switch it with the MENU row
    `PLACE: OUT/IN` (the menu is a 5-row list, 4 visible, that scrolls).

## Specs and decisions

- Behaviour, screens, haptics, interaction: `docs/design/ui-spec.md`.
- Visual tokens and components: `docs/design/design-system.md`, `docs/design/tokens.json`.
- Estimator choice and how to recalibrate on real watches: `docs/estimation/bakeoff.md`.
- IMU limits: `docs/estimation/imu-drift.md`.
- Users, field-test protocol, requirements R-xx: `docs/research/user-research.md`.
- Driver facts and hardware gotchas: `hal/README.md`.
- System structure and per-tick data flow: `docs/architecture.md`.
- Debug mode (Real watches in the web page over Wi-Fi), the next feature: `docs/design/debug-mode.md`.

## Adding a range estimator

1. Add `finder/estimators/<name>.py` with a class `Estimator(RangeEstimator)`
   from `base.py`: implement `update(t_ms, rssi, peer_rssi, my_motion,
   peer_motion)` (`rssi=None` on an idle tick: the bake-off sends them, the game
   does not) and keep the public fields finite (`rssi_f`, `rssi_var`,
   `rate_db_s`, `dist_m`, `dist_lo_m`, `dist_hi_m`, `trend`, `trend_conf`), and
   call `self._note_noise(t_ms, rssi)` (`base.py`) once per own packet: it sets
   `noise_db`, the packet-to-packet RSSI noise sd that feeds the ui-spec §5.5
   unreliable gate, the same way in every estimator. Add the name to `NAMES` in
   `finder/estimators/__init__.py`.
2. The shared contract in `tests/test_est_default.py` (`test_make_every_name`,
   `test_contract_*`, `test_noise_db_*`) runs for every name in `NAMES`; add
   `tests/test_est_<name>.py` with estimator-specific tests only (fixtures in
   `tests/est_helpers.py`).
3. Tune only on seeds 0-9 (`python3 tools/bakeoff.py --est kalman2,<name>`).
4. Judge on the **held-out seeds 100-129**, never used for tuning:
   `python3 tools/bakeoff.py --est kalman2,<name> --seeds 100-129 --out results.md`
   (all profiles and scenarios by default; also try `--imu drifty`).
5. Measure cost: `node tools/mpy/run.mjs tools/bench_est.py --est kalman2,<name>`.
   The per-packet budget on the ESP32 is about 2 ms, shared with render and radio.
6. Record the results in `docs/estimation/bakeoff.md`. Change `DEFAULT` only with
   held-out evidence that it wins and fits the budget.

## Web simulator

`sim/webhost.py` (`TwoWatchSim`) runs two `Game` + `Renderer` pairs on one
simulated world, stepped by the page. `web/sim/index.html` loads MicroPython
WebAssembly and a bundle of `finder/`, `ui/`, `sim/`.

```sh
(cd tools/mpy && npm install)       # once
python3 tools/build_sim.py          # -> dist/sim/ (index.html, local.html, py/bundle.json, mpy/)
python3 -m http.server 8765 --directory dist/sim   # open http://localhost:8765/local.html
```

`.claude/launch.json` has the same server as `web-sim`; its preview opens `/`
(`index.html`, quirks mode), so navigate to `/local.html` to see the page as a
full document. Rebuild after any change to `finder/`, `ui/` or `sim/`.
`dist/sim/index.html` is the page body (artifact format) after a leading
`<meta charset="utf-8">`; `local.html` wraps it in a full document.

## Security

- `secrets.py` (Wi-Fi credentials) is gitignored; `secrets.example.py` is the
  template. The game never joins Wi-Fi, and `deploy.py` copies `secrets.py` only
  with `--secrets`. Never commit credentials, tokens or `webrepl_cfg.py`, and
  never paste them into notebook outputs, docs or tests.
- The first commit (`c79530c`) leaked the Wi-Fi password and the WebREPL
  password (in `boot.py` and `webrepl_cfg.py`). They must be **rotated**.
  Rewriting history needs the user's explicit OK; agents must not do it.
- Do not run git commands that modify the index or history unless the user asks.
