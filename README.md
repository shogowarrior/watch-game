# Watch game

> Original goal: display proximity to another watch using BLE/Wifi strength.

A hide-and-seek game for two **LILYGO T-Watch 2020 V1** watches. Each watch
shows how close the other one is, like the Sheikah sensor in Zelda: a green glow
with ripples pulsing out from the centre. It gets brighter and faster as you get
closer, the watch buzzes in step with it, and an arrow appears once you've
worked out which way to go. You win by finding your friend and bumping watches.

The code has been tested on the computer and in a two-watch simulator, but
**hasn't been run on real watches yet**. Expect to calibrate.

## How it works

- **Radio.** Each watch broadcasts a small ESP-NOW packet 5 to 20 times a
  second (no Wi-Fi network needed; only debug mode joins one). The receiving watch measures the signal
  strength (RSSI). A stronger signal usually means closer, but RSSI is noisy:
  bodies, walls and reflections can move it by 10 dB or more.
- **Filtering.** A two-state Kalman filter smooths the RSSI and its rate of
  change. The accelerometer doesn't give position (it drifts far too quickly),
  but its step counter says whether each player is walking or standing still.
  When both stand still, the distance can't change, so the filter averages hard.
  When they walk, it lets the estimate move, but no faster than walking speed
  allows. The partner's measurement of your signal is used as a second reading.
- **Zones and trend.** The estimate becomes one of four zones (far, near,
  warm, hot) with its own ripple tempo and buzz rhythm. Chevrons show warmer or
  colder as you walk. Distance only ever appears as a rough band (`~10`), never
  as metres.
- **Direction.** Your body blocks the radio signal. Hold the watch flat at your
  chest and turn slowly on the spot, guided by the screen. The signal is weakest
  when your back faces your friend, so fitting signal strength against turn angle
  gives a direction and an uncertainty cone. The watch has no compass, so the
  arrow is relative to where you started the turn. It fades as you walk and time
  passes.
- **Finding.** Signal strength alone never ends the round. When you're very
  close, tap your watches together. Both accelerometers feel the bump within
  400 ms and both watches celebrate.

## Hardware

- 2 × LILYGO T-Watch 2020 **V1**. The V2 and V3 have different pins: see
  [docs/hardware-setup.md](docs/hardware-setup.md).
- A USB cable and a computer with Python 3, `esptool` and `mpremote`
  (`pip install -r requirements.txt`).
- Node.js (plus a one-time `cd tools/mpy && npm install`) is only needed to run
  the MicroPython tests, render the screen snapshots and build the browser
  simulator.

## Quick start

```sh
pip install -r requirements.txt
python3 -m jupyter_micropython_kernel.install   # once: adds the "MicroPython - USB" kernel to Jupyter

# 1. Flash stock MicroPython v1.29.0 (ESP32_GENERIC-SPIRAM). This ERASES the watch.
tools/flash.sh /dev/cu.usbserial-XXXX

# 2. Optional: the accelerometer's on-chip step counter / wrist-raise blob.
tools/fetch_bma423_config.sh            # writes <repo>/bma423conf.bin (sha256-checked)

# 3. Copy the game (boot.py, main.py, app/, finder/, hal/, ui/, bma423conf.bin).
python3 tools/deploy.py --port /dev/cu.usbserial-XXXX --noapp   # first time: copy, keep the REPL free
```

Do this for both watches. Then run the first-boot checks in
[docs/hardware-setup.md](docs/hardware-setup.md) §5 on both (the display, touch,
button and radio on a real watch still need confirming), and finish with
`python3 tools/deploy.py --port /dev/cu.usbserial-XXXX --app`. Each watch then
reboots into the game. To play:

1. **Pair.** Hold the two watches close. When both show the same three runes,
   tap the screen (or press the side button) on each, or bump the watches
   together.
2. **Calibrate.** Stand one step apart and hold still for 3 seconds.
3. **Split up.** A 30 s countdown gives you time to hide.
4. **Hunt.** Follow the glow. Tap the screen to run a direction scan, then turn
   slowly on the spot as the screen guides you.
5. **Find.** When the screen says `BUMP!`, tap watches. Tap again for a new round.

## Controls

| Input | Action |
|---|---|
| Tap the centre | Start a direction scan, or cancel one; confirm runes when pairing; lock the arrow while turning; new round after FOUND |
| Side button, short press | Same as a tap on the current screen, except in HOT: one press is your half of the fallback bump, and two presses within 1 s start a scan. When the screen is off, it only wakes it |
| Touch and hold (0.8 s) or button hold (1.5 s) | Menu: resume, sun mode, buzz mode (full / events / off), place (outdoors / indoors, changes how signal turns into distance), end round. A short press moves to the next row (it wraps) and a swipe scrolls; tap a row, or hold again, to choose it. END ROUND asks SURE? PRESS: press again within 3 s to confirm. The menu closes by itself after 8 s without input |
| Raise your wrist | Screen on. Lower it and the screen goes off, but the game keeps running and buzzing |
| Bump watches (in HOT) | Found! Fallback: both press the button within 3 s |

**Safe boot:** double-press or hold the side button within the first second after
power-on to skip the game and keep the USB REPL free. `tools/deploy.py --noapp`
does the same permanently, and `--app` undoes it.

## Developing

```sh
python3 tests/runner.py                     # all tests on CPython
(cd tools/mpy && npm install)               # once
node tools/mpy/run.mjs tests/runner.py      # the same tests on real MicroPython (WebAssembly)
```

- **Browser simulator:** `python3 tools/build_sim.py`, then
  `python3 tools/debug_server.py` and open
  `http://localhost:8765/local.html`. It runs the real game and screen code for
  two watches in your browser. You can drag the watches around a field and see
  both screens react.
- **Debug mode (the real watches in that page):** load each watch with
  `python3 tools/deploy.py --debug A` (and `--debug B`), after putting your
  Wi-Fi name and password in `secrets.py` (from `secrets.example.py`; it is
  never committed). The watches join your Wi-Fi and send their screens and
  readings to the laptop. Flip the page's toggle to **Real watches** to see
  both screens live, with a distance chart and the raw messages. `--demo` on
  the bridge tries it with two pretend watches. Steps and troubleshooting:
  [docs/hardware-setup.md](docs/hardware-setup.md) section 7.
- **Estimator experiments:** `python3 tools/bakeoff.py --quick`
  ([docs/estimation/bakeoff.md](docs/estimation/bakeoff.md)).
- **Screen snapshots:** `python3 tools/render_snapshots.py` renders every screen
  into [docs/design/snapshots/](docs/design/snapshots/).
- **On the watch:** `notebooks/finder_dev.ipynb` (Jupyter with the "MicroPython -
  USB" kernel) covers connecting, deploying, board bring-up, the display benchmark
  and the radio ping-pong. Or use `mpremote` directly; see
  [hal/README.md](hal/README.md).
- Coding agents and contributors: read [AGENTS.md](AGENTS.md) first.

## Documentation

| Doc | Contents |
|---|---|
| [docs/architecture.md](docs/architecture.md) | Layers, data flow per tick, timing |
| [docs/hardware-setup.md](docs/hardware-setup.md) | Identifying the watch, flashing, deploying, first-boot checks, debug mode |
| [hal/README.md](hal/README.md) | Drivers and hardware gotchas |
| [docs/design/ui-spec.md](docs/design/ui-spec.md) | Every screen, haptic and interaction (behaviour source of truth) |
| [docs/design/design-system.md](docs/design/design-system.md) | Colours, type, motion, components ([tokens.json](docs/design/tokens.json)) |
| [docs/estimation/bakeoff.md](docs/estimation/bakeoff.md) | Why the Kalman filter, and how to recalibrate |
| [docs/estimation/imu-drift.md](docs/estimation/imu-drift.md) | Why the accelerometer can't track position |
| [docs/research/user-research.md](docs/research/user-research.md) | Personas, field-test plan, requirements |

## Limitations

- **No compass or gyro.** The watch can't tell which way you're facing, so the
  arrow is relative to where you did the scan and it goes stale as you move.
- **RSSI is rough.** Even in simulation, the distance estimate is typically off by
  a factor of about 1.7, and it's worse indoors or through crowds. That's why the screen shows bands and
  trends, not metres. It isn't a safety or person tracker.
- **The simulator isn't reality.** The radio and motion models are educated
  guesses. Every threshold is a starting value to calibrate on real watches
  (see the "what to measure" list in the bakeoff doc).
- **V1 only.** The V2 and V3 have different pins and motor drivers.

## Status

All layers are written: drivers, game logic, renderer, watch runtime, simulator
and web simulator. The test suite passes on CPython and on MicroPython 1.29
(WebAssembly), and a simulated two-watch episode reaches FOUND. **It hasn't yet
run on real watches.** Next steps: the first-boot checklist, the radio
ping-pong, calibrating the path loss, then field tests.
