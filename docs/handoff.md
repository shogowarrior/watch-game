# Sheikah Finder: handoff (2026-10-03)

Read this first. It covers the current state, the decisions waiting on the
owner, and the order to do things in. CLAUDE.md and AGENTS.md still govern how
to work. The Project's goal and instructions are copied in
[docs/project.md](project.md).

## Before doing anything: ask the owner

These decisions are still open. Ask them together at the start of the thread,
in one message with the current default marked. Don't build on an assumption.

1. **Shipping.** Should a thread merge its branch into `main` and push once
   both runners are green, or leave the branch for the owner? Default: merge
   and push. That is how the 2026-10 work was shipped.
2. **Commit trailer.** The history ends commits with `Co-Authored-By: Claude
   ...`. Keep it, or drop it as word-finder does? Default: keep.
3. **Leaked passwords.** The first commit (`c79530c`) contains the old Wi-Fi
   and WebREPL passwords, and the repo is public. Have they been changed? The
   other option is scrubbing them from history, which needs a force-push.
   Default: no scrub. Changing the passwords is the fix that matters.
4. **Trend target not met.** ui-spec §5.5 wants fewer than 5 % false
   WARMER/COLDER verdicts on a tangential walk. In the simulator kalman2 gets
   28 % (typical) and 8 % (harsh). Retune now (fewer hints), or wait for real
   recordings? Default: wait for real data from debug mode.
5. **Far-range reads short.** At 36 m true distance, both simulated watches
   read about 16-20 m (band "~20" instead of "~40"). Calibrate on real
   recordings before changing anything? Default: yes, wait.
6. **Simulator artifact.** Keep publishing a simulator-only claude.ai artifact
   next to the local page? Real-watch mode can only work locally (see
   docs/design/debug-mode.md). Default: keep it as a shareable demo.

## Where things stand

- **Code:** every layer is written:
  - `finder/` game logic;
  - `ui/` renderer;
  - `hal/` drivers;
  - `app/` watch loop;
  - `sim/` plus `web/sim` browser simulator;
  - `tools/`.

  It runs unchanged on CPython, on MicroPython 1.29 (WebAssembly) and in the
  web simulator.
- **Tests:** `python3 tests/runner.py` gives 544 passed; `node tools/mpy/run.mjs
  tests/runner.py` gives 582 passed. The held-out bake-off (seeds 100-129)
  still ranks kalman2 first, at 0.660.
- **Review:** four whole-repo review rounds ran on 2026-10-02/03. Every finding
  was verified by an independent skeptic before it was fixed. Confirmed
  findings per round: 282, 175, 95, 21. Round 4 covered only the files round
  3 had changed, and only medium-or-worse problems.
- **Real watches:** nothing has run on a real watch yet. On 2026-10-03 the owner
  flashed one watch with stock MicroPython 1.29 (`tools/flash.sh`). Next is
  `python3 tools/deploy.py --port <port> --noapp` and the checklist in
  docs/hardware-setup.md.
- **Beacon format is version 3.** Both watches must run the same code; a watch
  on older code ignores the other.

## Next steps, in order

1. **Debug mode** (Simulator | Real watches toggle). It is fully specified in
   [docs/design/debug-mode.md](design/debug-mode.md), including the formats
   between the parts. Run the workflow `debug-mode-build`
   (`.claude/workflows/debug-mode-build.js`). It builds the watch side, the
   laptop bridge and the page in parallel, then reviews, fixes and checks it
   end to end with `tools/debug_server.py --demo`. Afterwards, run one
   `review-fix-round` with `changed` set to the files it touched.
2. **Design canvas sync.** The "Sheikah Finder watch UI" canvas
   (https://claude.ai/artifact/EnemW5QZy7BkxYyQ4SyfFn) predates these visual
   changes. The snapshots in docs/design/snapshots/ are current:
   - the FRIEND LEFT toast with NOPE when the partner goes back to PAIRING;
   - toasts fall out 12 px over 150 ms;
   - the SEARCHING and LINK-LOST inward listening rings are visible;
   - PAIRING split and SCANNING ready rings use the zone's lead/trail widths;
   - in sun mode the ghost rings lift one stop (snapshot `far_glow_sun`);
   - the MENU over FOUND freezes the breathing;
   - no top chip while a sweep is paused.

   The scan-result dart at θ already matches.
3. **Simulator artifact.** Republish
   https://claude.ai/artifact/RHC6ou3NCzccQxnha3esXW from `dist/sim/index.html`
   (`python3 tools/build_sim.py`) after finder/, ui/ or sim/ change. It still
   shows the 2026-10-02 build. Owner-side check: it has not been confirmed to
   boot inside the claude.ai sandbox.
4. **Real-watch bring-up**, with the owner at the watches. Turn what they report
   from docs/hardware-setup.md into fixes. A cloud thread cannot reach USB or
   the watches.
5. **Calibrate on real data.** Record sessions with debug mode (logs/), add a
   replay input to `tools/bakeoff.py`, and retune thresholds in
   `docs/design/tokens.json`. That answers questions 4 and 5.
6. **Field test** with two players, following docs/research/user-research.md.

## Known small issues (verified, not fixed)

- **Arrow clock.** If the MENU or SAVER ON is open on the exact tick an arrow
  comes back (an unstash after a cancelled scan, or a relink), the arrow's
  clock runs for one 100 ms tick, about 3° of pacer. The fix is one line in
  `finder/game.py` `_update_arrow`: OR `self.menu.is_open or self._inter_until
  is not None` into `hidden`.
- **Chip features after a bad start.** In `app/runtime.py` `_chip_start`, a
  retry that succeeds while `start()` returns False leaves a stale
  `errors['imu_features']`. If all 3 tries raise, the chip features stay off
  for the session.
- **Repeated partner RSSI.** The beacon's `rssi_last` has no age or sequence,
  so the partner can count the same RSSI value twice. A protocol field would
  fix it.
- **Quick bake-off ranking.** `bakeoff.py --quick` (typical, seeds 0-2) ranks
  kalman1d slightly above kalman2. Only the held-out seeds decide `DEFAULT`.

## Tools for this work

- **`.claude/workflows/review-fix-round.js`:** a whole-repo review and fix
  round. The header comment lists its args. Use `changed` and `lenses` to keep
  later rounds cheap.
- **`.claude/workflows/debug-mode-build.js`:** builds the debug mode (step 1).
- **`window.fieldSim`** on the web sim page drives the simulator from a script:
  - `advance(ms)` runs the simulator forward;
  - `call(name, ...args)` calls a `TwoWatchSim` method;
  - `telemetry` holds the latest state.
- **Dev server:** `.claude/launch.json` `web-sim` serves `dist/sim` on port 8765.

## How this project has been run (owner's standing preferences)

- **Autonomy:** work independently with workflows. Review your own work in
  rounds and fix each round's findings before the next. Don't waste tokens.
- **Plain language:** the owner is not an RF or estimation expert. Explain terms,
  and make cause and effect visible (the simulator was rebuilt for this).
- **Hardware:** the owner flashes and deploys. Agents never do, and never
  download firmware.
- **Secrets:** Wi-Fi credentials live only in the gitignored `secrets.py`.
- **Commits:** as `shogowarrior <abhinabray@gmail.com>`. Pushing works over SSH
  with the owner's shogowarrior key on their Mac, or through the Project's
  GitHub connection in the cloud.
