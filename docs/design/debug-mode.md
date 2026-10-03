# Debug mode: watch the real watches in the web sim page

Status: **built.** `.claude/workflows/debug-mode-build.js` built it in three
parallel tracks, then reviewed and verified it end to end. How to use it:
[docs/hardware-setup.md](../hardware-setup.md) section 7.

## What the owner asked for

A toggle at the top of the web sim page switches between **Simulator** and
**Real watches**. In Real mode the page shows what the two physical watches are
doing, live, over Wi-Fi. Wi-Fi credentials must never be committed.

## Decisions

1. **Real mode lives in the local page, not the artifact.** The claude.ai
   artifact is an https page in a sandbox. It cannot reach devices on the home
   Wi-Fi (mixed content, sandbox CSP). The rover project works the same way:
   a local page that talks to the device over Wi-Fi. The artifact build keeps
   the toggle, but Real is disabled there with one plain sentence that says why
   and gives the command to run locally.
2. **Data path: watch -> UDP -> laptop bridge -> page (Server-Sent Events).**
   The watches never run a server. In debug mode each watch joins the Wi-Fi
   access point and sends one JSON object per UDP datagram to the laptop. The
   laptop is a unicast target, because broadcast sends are unreliable on ESP32
   MicroPython; subnet broadcast is only a fallback. `tools/debug_server.py`
   (CPython, standard library only) receives the datagrams and serves the page.
   It also relays the datagrams to the page and logs them.
3. **What a watch sends** reuses `app/telemetry.py`:
   - the 5 Hz state records (`"ev": "s"`: rssi, rssi_f, d_est/d_lo/d_hi,
     zone, trend, steps, act, ui/sub, battery, link counters);
   - its events;
   - one new 5 Hz `"rp"` record with the frame's `RenderParams`, so the page
     can draw the watch's real screen with the real renderer.

   Sending happens from the 5 Hz telemetry path only, never from the render
   loop.
4. **Radio channel.** ESP-NOW and a Wi-Fi association share one radio. Once
   the watch joins the access point, the channel is the access point's. In
   debug mode:
   - The watch joins first and reads the channel.
   - The ESP-NOW radio starts on that channel without dropping the Wi-Fi
     connection. Today `EspNowRadio.begin()` always disconnects and allows
     only channels 1, 6 and 11. Debug mode needs an explicit associated mode:
     any channel from 1 to 13, no disconnect, `PM_NONE` kept.
   - Normal play is unchanged.
   - **Both watches must join the same access point**, so they share the
     channel.
   - Only `hal/` imports `network` and `socket` (AGENTS rule 12).
5. **Switching it on**, like `/tele`:
   - `python3 tools/deploy.py --port P --debug A` writes `/debug` and copies
     `secrets.py`. That file is the gitignored file with `WIFI_SSID` and
     `WIFI_PASSWORD`; its template is `secrets.example.py`.
   - `--no-debug` removes both from the watch.
   - `main.py` reads `/debug`. If `secrets.py` is missing or the join fails,
     the watch prints why and plays normally.
6. **Credentials are never committed.**
   - `secrets.py` stays gitignored.
   - It is read only on the watch and by deploy.py's copy.
   - Its values are never printed, logged or sent.
   - `tests/test_secrets_guard.py` checks that no tracked file contains them.

## Contract between the parts

### `/debug` on the watch

JSON: `{"dev": "A", "host": "192.168.1.23", "port": 47268}`

- `dev` is the label the page shows (A or B).
- `host` is the laptop's LAN IPv4 address. `deploy.py --debug` detects it with
  the UDP-connect trick; `--debug-host` overrides it. If `host` is missing, the
  watch sends to the subnet broadcast address computed from `ifconfig`.
- `port` defaults to 47268 (`DEBUG_PORT`).
- `deploy.py --debug` takes `A` or `B` only. When it cannot find the laptop's
  address and no `--debug-host` is given, it writes `/debug` without `host`
  (the watch then broadcasts) and says so.
- On boot the watch tries to join for at most 10 s (`JOIN_MS`), and the screen
  stays dark meanwhile. A failed join leaves the Wi-Fi disconnected, so it
  cannot pull ESP-NOW off its channel.
- A watch that cannot join plays on its usual channel (6), while a joined
  watch uses the Wi-Fi's channel, so the two cannot find each other until both
  have joined. Restart the missing one (or turn debug mode off on both with
  `--no-debug`).

### A datagram (watch -> laptop)

One UTF-8 JSON object per UDP packet, at most about 1400 bytes (one Wi-Fi
frame; `DGRAM_MAX` in `app/telemetry.py`, counted in UTF-8 bytes, not
characters). If a record would be larger, the watch drops optional fields
rather than fragmenting it.

Every datagram carries:

- `dev`
- `mac`: the last 3 MAC bytes as 6 lowercase hex characters
- `t`: the watch's `ticks_ms`
- `ev`

The kinds are:

- `"s"`: the existing state record, with unchanged fields.
- `"rp"`: `p` holds `finder.render_params.to_dict(params, json_ready=True)`;
  `on` is whether the screen is on; `bl` is the backlight from 0 to 100; `ch`
  is the watch's Wi-Fi channel (the access point's), or null on the fake
  watches or when unknown. The page compares the two watches' `ch` to explain
  a channel split.
- The other telemetry events (`btn`, `tap`, `haptic`, `pwr`, `crash`, ...), as
  they happen.

Send errors are counted (`tx_err`) and never raised into the game loop.

- A datagram that is still too long after dropping every optional field is
  not sent.
- `rp` records are sent but never stored in the watch's telemetry ring or its
  `/log` file.
- `t` is the sender's own clock: each real watch counts from its boot, and the
  fake watches share one clock that starts at 0. Never compare it across
  watches or with the laptop's time.

### `tools/debug_server.py` (CPython, standard library only)

```
python3 tools/debug_server.py [--http-port 8765] [--udp-port 47268] [--root dist/sim] [--no-log] [--demo]
```

- It serves `--root` over HTTP on 127.0.0.1, with no-store cache headers.
- It listens for UDP on 0.0.0.0:udp-port. `--udp-port` defaults to
  `DEBUG_PORT` (47268), where the watches always send, so another value only
  works with `--demo` or `tools/fake_watches.py --port`; the bridge warns when
  it differs.
- Each valid datagram (a UTF-8 JSON object with non-empty string `dev` and
  `ev`) becomes one SSE message on `GET /events`:
  `data: {"src": "<sender ip>", "rx": <server ms>, "rec": <the object>}`.
  Invalid datagrams are counted in `bad` and dropped. A datagram holding NaN,
  Infinity or a number too large to store (such as `1e400`) counts as bad, so
  every `/events` payload and log line is strict JSON.
- `GET /debug/status` returns
  `{"ok": true, "udp_port": N, "clients": N, "packets": N, "bad": N, "log": path|null, "watches": {"<dev>": {"src": ip, "last_rx": ms, "n": N}}}`.
  The page checks this to decide whether Real mode is available.
- Every valid datagram is appended to `logs/debug-YYYYmmdd-HHMMSS.jsonl`
  (gitignored) unless `--no-log` is set. A recorded session can be replayed
  later to calibrate the estimators on real radio data.
- `--demo` runs `tools/fake_watches.py` in a thread, so Real mode can be tried
  with no watches.
- `.claude/launch.json` `web-sim` runs this server instead of `http.server`.

Details the parts rely on:

- `rx` is the laptop's wall clock in ms since 1970 (`Date.now()` on the page),
  never the watch's ticks.
- `/debug/status` `log` is a path relative to the repo, such as
  `logs/debug-20261003-210553.jsonl`, or null. The log is created with the first
  record. Each of its lines is exactly one `/events` payload
  (`{"src", "rx", "rec"}`), ready to replay.
- The `/events` stream starts with `retry: 2000`, so the page's `EventSource`
  reconnects within 2 s after the bridge restarts. A comment line every 10 s
  finds closed tabs.

### `tools/fake_watches.py` (CPython)

`run(host="127.0.0.1", port=47268, seconds=None, speed=1.0, stop=None)` runs
the two-watch simulator: `sim/` plus `finder.game.Game`, with no renderer. It
sends the same datagrams real watches send, built by the same `app/telemetry.py`
code; there is no second copy of the record format.

- `stop` is a `threading.Event`: `run` loops while it is not set and paces
  itself with `stop.wait`, so it returns within one 50 ms step once it is set.
  `debug_server.py --demo` passes the Event it sets on shutdown and runs `run`
  on a daemon thread.
- The two watches are `A` and `B`, with different `mac` values. Their `rp`
  records hold `finder.render_params.to_dict(params)`.

### Page side (`sim/webhost.py`, `web/sim/index.html`)

`TwoWatchSim` gets two methods:

- `show_params(i, json_text)` draws the given params on watch i's screen. Frames
  keep advancing with time, but no game logic runs for that watch.
- `real_mode(on)` stops or resumes the simulated world.

The page in Real mode:

- **Controls:** the drag, pace, speed and posture controls and the guide are
  hidden.
- **Screens:** both screens are drawn from the latest `rp` per watch.
- **Under each screen:** the connection state ("last heard N s ago"), band,
  zone, trend, rssi/rssi_f, steps, activity, battery and loss, all with
  plain-language labels.
- **Distance chart:** each watch's d_est (with the d_lo..d_hi band) and rssi_f.
- **Raw log:** the last 50 lines, collapsible.
- **Waiting state:** a short how-to.
- **Unavailable state:** the artifact, or a page served by a plain http.server.

The choice is remembered in localStorage. The page must work at 375 px and in
both themes, and `window.fieldSim` must keep working.

How the page reads the records:

- Only `dev` `A` and `B` map to the two screens. Records with other labels are
  counted and kept in the raw log only.
- Two different `mac` values under one label within 3 s are flagged as two
  watches with the same name. The same MAC on both watches is fine.
- When both watches are live and their latest `rp` records carry different
  non-null `ch`, both cards say the watches joined different parts of the
  Wi-Fi (different channels) and suggest a network with one access point.
- "Last heard" counts from the bridge's `rx`, not from the record's `t`.
- An `rp` record without `p` keeps the last screen. A `mac` of null (a watch
  whose radio failed to start) is accepted.
- `window.fieldSim` also has `mode`, `setMode('sim'|'real')` (it resolves to the
  mode in effect) and `real` (a summary of what each watch sent).

## Tests (both runners where they apply)

- The Telemetry sink and the `rp` record.
- `hal/debuglink` with fakes. `tests/fakes` gets a fake `socket`, and the fake
  `network` gets connect, isconnected, `config('channel')` and `ifconfig`.
- The `EspNowRadio` associated mode.
- The `main`/runtime wiring: `/debug` present, secrets missing, and a failed
  join that falls back to normal play.
- Command building for `deploy.py --debug` and `--no-debug`.
- The `debug_server` UDP-to-SSE relay and its log, with real localhost sockets
  and short timeouts. These run on CPython only.
- `show_params` draws the same frame the renderer draws for the same params.
- The secrets guard.

No test uses real Wi-Fi.

## Docs that describe it (updated when it landed)

- `docs/hardware-setup.md` section 7: how to use it, with the exact commands.
- `AGENTS.md`:
  - the repo map;
  - the commands;
  - Security: the game joins Wi-Fi only in debug mode;
  - rule 12, which names `socket`.
- `README.md`: one paragraph.
- `docs/architecture.md`: the debug data path.
- `hal/README.md`: `debuglink` and the associated radio mode.
