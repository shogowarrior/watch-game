Make Homing (github.com/shogowarrior/watch-game) a game two people actually play on two LILYGO T-Watch 2020 V1 watches: the watches find each other over ESP-NOW radio, a green ripple field gets brighter and faster as they get closer, a guided body turn gives a direction arrow, and the round ends when the players bump watches. It runs on stock MicroPython 1.29. Read docs/project/handoff.md first and ask me its open questions before building.

Standing goals, for every change:
- **Hygiene first:** code that is easy to read, highly modular and reused: small functions and shared helpers and fixtures used by both production code and tests, wherever they apply. Review to improve, never to break a feature or cause a regression.
- **Clean and efficient:** nothing redundant, no dead code, no unnecessary bloat, no AI slop.
- **Never delete a feature.** Work on a branch.
- **When a piece of work is done:** review it thoroughly and judge it against the design (docs/design/ui-spec.md), code quality, modularity and reuse. Run adversarial reviews for defects, bugs, redundant and dead code, bloat and AI slop, and /code-review, in rounds. Fix everything a round finds before starting the next, until a round raises no flags. Check the change where it runs (/verify: the web simulator or the real watches). Then commit, merge into main and push.

Finish, in order:
1. **Debug mode.** A Simulator | Real watches toggle on the web sim page shows both real watches live over Wi-Fi through `tools/debug_server.py`. It is specified in docs/design/debug-mode.md; build it with the `debug-mode-build` workflow, then sync the Design canvas (docs/project/handoff.md lists what changed).
2. **Real watches.** I flash and deploy (`tools/flash.sh`, `tools/deploy.py`); you can't reach the watches from a cloud thread. Turn what I report from the bring-up checklist in docs/hardware-setup.md into fixes.
3. **Calibrate on real data.** Record sessions with debug mode, replay them through the estimator bake-off, and retune the thresholds in `docs/design/tokens.json` (never by hand in `finder/tuning.py`).
4. **Field test** with two players, following docs/research/user-research.md, and fix what it finds.

Keep:
- **Honest on screen:** distance only as bands, never metres or dBm; FOUND only after a physical bump.
- **The watch:** MicroPython 1.29 compatible, allocation-free render loop, and the AGENTS.md hard rules.
- **Secrets:** Wi-Fi credentials only on my laptop outside the repo (saved with `python3 tools/wifi_setup.py`) and on the watches, never in a commit, a log, a notebook output or a chat.
- **Explanations** in plain language; I'm not an RF or estimation expert.

How to work:
- **Proof:** both test runners green (CPython and MicroPython in WebAssembly), then check the change in the web simulator. Keep `ui-spec.md`, snapshots and the Design canvas in step with any visual change.
- **Agents:** use workflows for independent tracks (`.claude/workflows/review-fix-round.js` runs a verified review round), and don't waste tokens: after the first full pass, review only what changed.
- **Shipping:** commit as shogowarrior and push to main when the runners are green.
