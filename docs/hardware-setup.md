# Hardware setup

How to get the game onto two real watches, and what to check the first time.
Driver details and gotchas are in [`../hal/README.md`](../hal/README.md).
Terms such as RSSI, dBm and the path-loss exponent are explained in the
[ui-spec glossary](design/ui-spec.md#13-glossary).

> None of this has been run on a real watch yet. Treat the checklist as the
> bring-up plan, and write down what you find (see "Assumptions to verify").

## 1. Identify the watch revision

The code supports the **T-Watch 2020 V1** only (`hal/pins.py`).

| Revision | Tell-tale signs | Why it matters |
|---|---|---|
| **V1** | Back label or box says V1. Vibration motor driven directly from a GPIO, I2S speaker, no GPS and no microSD | Supported |
| V2 | GPS module and a microSD slot; the motor goes through an I2C haptic driver chip | Motor, and possibly other pins, differ. Don't reuse `pins.py` |
| V3 | Adds a PDM microphone | Check every pin before reuse |

Software check, from the REPL on a freshly flashed watch:

```python
import machine
[hex(a) for a in machine.I2C(0, scl=machine.Pin(22), sda=machine.Pin(21)).scan()]
```

A V1 should show `0x19` (BMA423, or `0x18`), `0x35` (AXP202) and `0x51`
(PCF8563 RTC) on this bus. An extra device, such as a haptic driver at `0x5A`,
means it isn't a V1. The touch controller (`0x38`) is on the second bus
(SDA 23, SCL 32).

## 2. Flash MicroPython v1.29.0

The watch needs **stock MicroPython v1.29.0, `ESP32_GENERIC-SPIRAM`**. The images
already in `firmware/` are old (such as v1.23 `ESP32_GENERIC`, without SPIRAM).
Don't use them.

```sh
pip install -r requirements.txt           # esptool, mpremote, jupyter kernel
python3 -m jupyter_micropython_kernel.install   # once: adds the "MicroPython - USB" kernel to Jupyter
ls /dev/cu.usbserial-*                    # macOS; /dev/ttyUSB* on Linux
tools/flash.sh /dev/cu.usbserial-XXXX
```

`tools/flash.sh` downloads `ESP32_GENERIC-SPIRAM-20260824-v1.29.0.bin` into
`firmware/` if it is missing, checks that it looks like an ESP32 image (size,
`0xE9` magic, optional `FW_SHA256=...`), shows the exact esptool commands and asks
before it acts. It then **erases the whole flash** (every file on the watch) and
writes the image at `0x1000`. If writes fail, retry with `BAUD=115200`. To flash a
local image, pass it as the second argument.

Check the result:

```sh
mpremote connect /dev/cu.usbserial-XXXX exec "import sys, gc; print(sys.implementation); print(gc.mem_free())"
```

Expect version `(1, 29, 0)` and a free heap of several MB (SPIRAM).

## 3. Optional: the BMA423 feature-engine blob

Without it, steps come from the software step detector in `finder/motion.py` and
wrist-raise comes from the face-up check. With it, the chip's own step counter,
activity classification and wrist-wear interrupt are used as well.

```sh
tools/fetch_bma423_config.sh               # <repo>/bma423conf.bin, 6,144 B, sha256-checked
```

`tools/deploy.py` copies it automatically if it is in the repo root. The script
fetches Bosch's BSD-3 v2.14.13 blob. The older LilyGO blob is rejected by
`hal/bma423.py`.

## 4. Deploy

```sh
python3 tools/deploy.py -n                                    # dry run: file list and command
python3 tools/deploy.py --port /dev/cu.usbserial-XXXX --noapp # first time: copy, but don't start the game
```

This hard-resets the watch first (a separate mpremote call, then a 3 s wait),
then copies `app/`, `finder/`, `hal/`, `ui/`, `bma423conf.bin` and finally
`boot.py` and `main.py` in one mpremote session, and hard-resets again, so
`main.py` starts the game (or stops at `/noapp`). The first reset is there for
the watchdog: a game started on battery runs the ESP32 hardware watchdog,
which cannot be stopped and would reboot the watch in the middle of the copy.
After the reset the game starts on USB with the stoppable one. The entry
points go last, so an interrupted copy keeps the old ones. The new
`boot.py` replaces any old one that joined Wi-Fi or started WebREPL. The game
joins Wi-Fi only in debug mode over Wi-Fi (section 7), and the Wi-Fi name and
password are only copied with `--debug A --wifi` (or `--secrets`). Deploy the
same build to both watches: a watch drops beacons with another protocol version
(`finder/proto.py` `VERSION`).

- `--noapp` creates `/noapp`, so `main.py` skips the game and the REPL stays free.
- `--app` removes it again. After that, every boot starts the game.
- `--tele A` writes `/tele`: from the next boot the game logs telemetry to
  `/log/<n>_A.jsonl` (a new n on every boot; use `B` on the other watch).
  `--no-tele` removes it. To pull logs, plug in USB and run
  `mpremote connect <port> reset` first, then wait about 3 s, for the watchdog
  reason above: without it a game that ran on battery reboots the watch 8 s
  into the copy. The reset starts a new, small log. List the logs with
  `mpremote connect <port> fs ls :/log`, then
  `mpremote connect <port> fs cp :/log/<n>_A.jsonl .`. Check free flash with
  `import os; os.statvfs("/")`.
- `--debug A` / `--debug B` (over USB; add `--wifi` for Wi-Fi) and
  `--no-debug` switch debug mode on and off (section 7).
- Safe boot without a computer: double-press, or hold for about 1.5 s, the side
  button within the first second after power-on.

## 5. First-boot checklist

Do these on each watch with `/noapp` set. `notebooks/finder_dev.ipynb` has
steps 1-3, 6 (live view) and 7 as cells (the "MicroPython - USB" kernel, one
kernel per watch).

1. **Board bring-up.** At the REPL:
   ```python
   from hal.board import Board
   b = Board(); b.init(strict=False)
   print(b.errors)                  # expect {}
   print(b.pmu.status())            # battery %, mV, USB power present (VBUS), charging
   print(hex(b.imu.chip_id()))      # 0x13
   print(b.imu.load_config(), b.imu.feat_error)   # True None if bma423conf.bin is on the watch and accepted
   print(b.radio.mac, b.radio.channel)
   ```
   `Board()` soft-resets the BMA423, so only `load_config()` (or the game)
   loads the blob; `features_ok()` reads False until then. `load_config()`
   starts the feature engine but switches no feature on: `b.imu.poll_features()`
   (2 = FEAT_OK) does that, as the game does, and only then does `b.imu.steps()`
   count.
2. **Display.** `mpremote connect <port> run tools/bench_display.py`. Needs `hal/`,
   `finder/` and `ui/` on the watch (the `--noapp` deploy in section 4) and `FAST = False`
   in the script. Check:
   - The `print(spi)` line (`cpu ... MHz; SPI(...)`) shows a baudrate of about
     26.67 MHz (80 MHz / 3), not higher.
   - In the first second, the white `bench_display` text on blue reads left to
     right and the right way up, and the blue fills the panel with no 80-row
     offset band (a strip of black or garbage from a wrong row offset). The
     screen is cleared at the end.
   - Push and blit timings: a full frame is about 35 ms of SPI; the whole
     strip-rendered frame should reach about 20 fps.
   - Heap allocated per frame is about 0.
3. **Backlight and motor.** `b.display.brightness(1.0)`, then `0.2`. Then, on one
   line, `import time; b.haptics.set(1); time.sleep_ms(80); b.haptics.set(0)`.
   The motor should buzz once.
4. **Touch corners.** Poll and tap each corner:
   ```python
   import time
   for _ in range(300):
       t = b.touch.read()
       if t[0]: print(t)
       time.sleep_ms(30)
   ```
   Top-left should read about `(0, 0)` and bottom-right about `(239, 239)`. If both
   axes are flipped (the older PANEL_V1 variant), use `Board(touch_rotation=2)` at
   the REPL. To make it permanent, change main.py to
   `board = Board(touch_rotation=2)` and redeploy.
5. **Side button (PEK).** Poll `b.button()` in a loop. A short press should give
   `0x01` (`EV_SHORT`), and holding for 1.5 s should give `0x02` (`EV_LONG`). After
   `b.pmu.enable_pek(edges=True)` a press and a release also show `0x20` / `0x40`
   (`EV_PRESS` / `EV_RELEASE`). Safe boot can only be tested without `/noapp`
   (with it, main.py always prints `safe boot (flag)`): at the REPL run
   `import os; os.remove("/noapp")`, reset, double-press within the first second
   and check for `safe boot (pek)`. Then `open("/noapp", "w").close()` to go on
   with the checklist.
6. **IMU.** Lay the watch face-up. `b.imu.read_xyz_mg()` should show z around
   +1000. If it shows -1000, change main.py's call to
   `app.run(board, watchdog_ms=8000, z_sign=-1, **kw)` and redeploy (the notebook's IMU
   section shows face-up, steps and bump spikes live). Knock two watches
   together: the spike should pass 2.5 g and last 10-20 ms.
7. **Radio ping-pong** (two watches, same channel):
   ```sh
   mpremote connect /dev/cu.usbserial-A cp tools/radio_pingpong.py :
   mpremote connect /dev/cu.usbserial-B cp tools/radio_pingpong.py :
   mpremote connect /dev/cu.usbserial-A exec "import radio_pingpong as pp; pp.run('pong')"
   mpremote connect /dev/cu.usbserial-B exec "import radio_pingpong as pp; pp.run('ping', n=1000, render=True)"
   ```
   Expect delivery near 100 % at arm's length, even with `render=True` (the
   display transfer running at the same time). Note the RSSI at 1 m (the
   nominal value is -45 dBm) and at 5, 10 and 20 m.
8. **Play.** `python3 tools/deploy.py --port <port> --app` on both watches, then
   pair them. After Ctrl-C at the REPL (while the game has stayed on USB; once
   it has run on battery, Ctrl-C reboots the watch within 8 s),
   `import app; app.rt.print_stats()` shows fps and ms per stage (and
   `touch_errors`, FT6336 bus errors, if any).

## 6. Assumptions to verify

Each of these is a design assumption in the code that nobody has checked on a real
watch yet.

| Assumption | Where | How to check |
|---|---|---|
| SPI runs at 26.67 MHz on GPIO-matrix pins; above that stock firmware crashes | `hal/pins.py` `TFT_BAUD`, `hal/st7789.py` | `print(spi)` in `bench_display.py` |
| MADCTL (the panel's orientation register) `0xC0` with row offset 80 is upright | `hal/st7789.py` | test pattern orientation |
| LDO2 (the power-chip output that feeds the panel and backlight) powers both; the display works on USB with no battery | `hal/board.py`, `hal/axp202.py` | boot with and without a battery |
| Touch rotation 0 matches this panel | `Board(touch_rotation=...)`, `hal/ft6336.py` | corner taps |
| FT6336 INT stays low for the whole touch (INT gating, reading touch only while the INT line is low, is off until verified) | `hal/ft6336.py` `gate_int` | log INT against `read()` |
| PEK double press / hold within 1 s gives a reliable safe boot | `hal/board.py` `safe_boot` | reboot tests |
| BMA423 at `0x19`, face-up z positive, ±4 g, 100 Hz FIFO | `hal/bma423.py`, `z_sign` | step 6 |
| A bump is a spike > 2.5 g lasting 10-20 ms; haptic pulses don't trigger it | `app/imu_feed.py` | knock tests while the motor runs |
| The feature engine (steps, activity, wrist-wear) works with the Bosch blob | `hal/bma423.py` `load_config`, `poll_features` | `b.imu.load_config()` (True), then `b.imu.poll_features()` (2 = FEAT_OK); walk 20 steps: `b.imu.steps()` reads about 20 and `b.imu.activity()` reads 1 (walk) while walking |
| The chip wrist-wear gesture fires on a wrist raise only (the feature engine runs with Bosch's default axes remap; the T-Watch mounting is unmeasured) | `hal/bma423.py` `poll_features`, `app/runtime.py` `_stage_imu` | After `b.imu.poll_features()` returns 2, call `b.imu.poll_events()` to clear it. Then raise the wrist, lower it, twist it and swing the arm while walking, calling `b.imu.poll_events() & 0x08` after each move: it must be set only after a raise. If it misfires, drop `enable_feature(FEAT_WRIST_WEAR)` and the INT1 map from `poll_features` (face_up still wakes the screen, ui-spec §8), or write the FEATURES_IN axes-remap word before enabling it. |
| Motor spins up at 35 % duty; 60 ms pulses are felt | `hal/haptics.py` `min_duty` | step 3, haptic patterns |
| RSSI at 1 m is about -45 dBm. The path-loss exponent n (how fast the signal falls with distance) is about 2.6 outdoors and 3.0 indoors | `tokens.json` `thresholds.calibrate` (`p1m_nominal_dbm`, `n`, `n_indoor`) -> `tuning.P1M_NOMINAL_DBM`, `PATH_LOSS_N`, `PATH_LOSS_N_INDOOR` | ping-pong at known distances, once outdoors and once indoors |
| Body shadowing is deep enough (several dB) for the scan to fit a direction | `finder/scan.py` | scans with a partner at 10-20 m |
| ESP-NOW on channel 6 at 20 dBm keeps 10-20 Hz beacons with the display running | `hal/radio.py` | ping-pong with `render=True` |
| The renderer holds 20 fps, and an estimator update is well under 2 ms | `app/runtime.py` stats | `app.rt.print_stats()`, `tools/bench_est.py` on the watch (what to copy: [bakeoff.md section 3](estimation/bakeoff.md#when-to-switch-to-particle), step 1) |
| The battery gauge is trustworthy enough for the low-battery and shutdown thresholds | `hal/axp202.py` `battery_percent` | compare it with the voltage over a discharge |
| Debug mode over USB: opening a port does not restart the watch, and the link keeps up with the records | `tools/debug_server.py` `open_port`, `hal/debuglink.py` `SerialLink` | section 7 notes: `debug_stats` `drop` and `queued` stay near 0 |

Record the results, and move any calibrated value into `docs/design/tokens.json`
(then run `python3 tools/gen_tuning.py`), or into the driver defaults in `hal/`.
`docs/estimation/bakeoff.md` ("What to measure on real watches") lists the radio
measurements needed to recalibrate the simulator.

## 7. Debug mode: watch the real watches on your laptop

Debug mode shows what both watches are doing, live, in the web sim page on your
laptop: their screens (drawn by the same screen code), the distance each one
guesses, signal strength, steps, battery and missed signals, plus a chart and
the raw messages. Each watch sends this 5 times a second, either over its
**USB cable** (start here: no Wi-Fi and no password, and the game plays
exactly as usual) or over your **Wi-Fi**, for when the watches are off the
cable. The design is in [design/debug-mode.md](design/debug-mode.md).

You need both watches and the page built once (`python3 tools/build_sim.py`,
which needs the one-time `cd tools/mpy && npm install`).

### Over USB

1. **Plug both watches into the laptop.** `mpremote devs` lists their ports
   (on macOS `/dev/cu.usbserial-...`, on Linux `/dev/ttyUSB0` and so on). On
   Linux your user must be in the `dialout` group to open them
   (`sudo usermod -aG dialout $USER`, then log out and back in).
2. **Load each watch in debug mode**, one port each:
   ```sh
   python3 tools/deploy.py --port /dev/cu.usbserial-A --debug A
   python3 tools/deploy.py --port /dev/cu.usbserial-B --debug B
   ```
   This copies the game and writes `/debug` with the name the page shows (A or
   B). Nothing secret is copied. Use A for one watch and B for the other.
3. **Start the bridge** on the laptop:
   ```sh
   python3 tools/debug_server.py --serial
   ```
   It reads every USB serial port it finds, and picks up a watch plugged in
   later.
4. **Open http://localhost:8765/local.html** and flip the toggle at the top to
   **Real watches**. A few seconds after a watch starts, its screen shows up.

**Stop the bridge (Ctrl-C in its terminal) before you run `deploy.py` or
`mpremote`.** It holds the ports, so while it runs they report the port as
busy. Start it again afterwards.

The watch writes its messages a little at a time, so the game never waits for
the cable. Its other output (the boot message, an error) shows up in the page's
raw log too, marked with the port. If you unplug a watch, the bridge opens the
port again when it comes back.

### Over Wi-Fi

For when the watches are off the cable. You need a **2.4 GHz** Wi-Fi network
that both watches and the laptop are on.

1. **Save your Wi-Fi name and password on the laptop**, once:
   ```sh
   python3 tools/wifi_setup.py
   ```
   Type them into your own terminal; never paste the password into a chat.
   The tool saves them outside the project folder
   (`~/.config/watch-game/wifi.py`, readable only by you), so they are never
   committed. `python3 tools/wifi_setup.py --check` says whether the file is
   there, private and usable, without showing what is in it; `--forget`
   deletes it.
2. **Load each watch in Wi-Fi debug mode:**
   ```sh
   python3 tools/deploy.py --port /dev/cu.usbserial-A --debug A --wifi
   python3 tools/deploy.py --port /dev/cu.usbserial-B --debug B --wifi
   ```
   This also copies your Wi-Fi file to the watch (as `/secrets.py`), and
   writes this laptop's address into `/debug`; it finds the address by itself
   and prints it. If it cannot, it says so and the watch broadcasts to the
   whole network instead, which is less reliable: give the address with
   `--debug-host 192.168.1.23` (on macOS, `ipconfig getifaddr en0` prints it).
3. **Start the bridge** with `python3 tools/debug_server.py` (add `--serial`
   to read USB watches too) and open http://localhost:8765/local.html as
   above. If macOS (or another firewall) asks whether Python may accept
   incoming connections, allow it: that is how the watches reach the laptop.
4. **Switch the watches on** (or let them restart after the deploy). Each one
   tries to join the Wi-Fi for up to 10 s before the game starts, with the
   screen dark meanwhile.

**Both watches must join the same Wi-Fi network.** The watches talk to each
other on the Wi-Fi's channel while they are joined, so two different networks
(or a 2.4 GHz and a 5 GHz name of the same router, if your router splits them)
put them on different channels, and they will not hear each other. A mesh
system or a range extender can also put the two watches on different channels
even though the network has one name; if both watches show up here but never
find each other on the page, use a network with a single access point (when
the two watches report different channels, the page says so under both
screens).

If a Wi-Fi watch does not show up:

- The page's "Last heard" under each screen says when it last heard that watch.
  The bridge also prints "Heard watch A" the first time.
- A watch that cannot join plays normally without sending, and prints why on
  its USB port, with what to run (`python3 tools/wifi_setup.py`, then the
  `deploy.py ... --wifi` line again). To read it, keep the watch plugged in
  after `deploy.py` and start the bridge with `--serial` straight away: the
  reason shows up in the page's raw log about 10 s later. Or, with the bridge
  stopped, run `mpremote connect <port> repl`, press Ctrl-C (stops the game),
  then type `import machine; machine.reset()` and press Enter: the watch
  restarts in the same window, and its first lines say where it sends, or why
  debug mode is off (no Wi-Fi file on the watch, a wrong password, no network
  in range). A wrong password and a network out of reach give the same
  message, after 10 s: the watch cannot tell them apart.
- A watch that cannot join plays on its usual radio channel, but a watch that
  did join talks on the Wi-Fi's channel, so the two cannot find each other
  until both show up on the page. Restart the missing watch (or turn debug mode
  off on both with `--no-debug`).
- If two different watches both say A (or B), the page says so: load one of
  them again with the other letter.

### Turn it off

`python3 tools/deploy.py --port <port> --no-debug` removes `/debug` and
`/secrets.py` from the watch. From then on it plays normally and never joins
the Wi-Fi.

### Logs

The bridge saves everything the watches send to
`logs/debug-YYYYmmdd-HHMMSS.jsonl` in the project folder (ignored by git; the
bridge prints the file's name when it starts and when it stops). Each line is one
message as the page received it: `{"src": the watch's port or address, "rx":
laptop time in ms, "rec": what the watch sent}`. These sessions are real radio
data to calibrate the estimators with later (`docs/estimation/bakeoff.md`). No
tool replays them yet: a replay input for the bake-off is a planned next step
([project/handoff.md](project/handoff.md)), and the files are already in the
form it will read. Pass `--no-log` to save nothing.

### Try it without watches

`python3 tools/debug_server.py --demo` runs two pretend watches on the laptop
that send the same messages as real watches on Wi-Fi; `--demo --serial` makes
them use the USB link instead, through two pretend ports. One walks away to
about 40 m and back while the other stands still.

Notes:

- At bring-up, check that the USB link keeps up: stop the bridge, open the
  watch's REPL (`mpremote connect <port> repl`), press Ctrl-C and run
  `import app; app.rt.print_stats()`. In `debug_stats`, `drop` (messages left
  out) and `queued` (bytes waiting) should stay near 0. Note too whether
  starting the bridge restarts a watch (it should not).
- If the Wi-Fi drops in the middle of a game, the watch keeps trying to
  reconnect, and until it is back within reach of the access point the watches
  can lose each other. Use Wi-Fi debug mode at home and for field tests near
  the access point; turn it off for normal play.
- Real mode works only in the page served by `tools/debug_server.py`. The
  claude.ai artifact and a page served by a plain `http.server` show it as
  unavailable, with the command to run.
- `--debug` works together with `--tele A`: the watch then also keeps its own
  log in `/log`.
