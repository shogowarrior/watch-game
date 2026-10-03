# Project goal and instructions

Copies of the text pasted into the Claude Project's **goal** and **instructions**
fields. The Project's fields are authoritative; update this file when they change.

## Goal

Make Sheikah Finder (github.com/shogowarrior/watch-game) a game two people actually play on two LILYGO T-Watch 2020 V1 watches: the watches find each other over ESP-NOW radio, a green ripple field gets brighter and faster as they get closer, a guided body turn gives a direction arrow, and the round ends when the players bump watches. It runs on stock MicroPython 1.29. Read docs/handoff.md first and ask me its open questions before building.

Standing goals, for every change:
- **Hygiene first:** code that is easy to read, highly modular and reused: small functions and shared helpers and fixtures used by both production code and tests, wherever they apply. Review to improve, never to break a feature or cause a regression.
- **Clean and efficient:** nothing redundant, no dead code, no unnecessary bloat, no AI slop.
- **Never delete a feature.** Work on a branch.
- **When a piece of work is done:** review it thoroughly and judge it against the design (docs/design/ui-spec.md), code quality, modularity and reuse. Run adversarial reviews for defects, bugs, redundant and dead code, bloat and AI slop, and /code-review, in rounds. Fix everything a round finds before starting the next, until a round raises no flags. Check the change where it runs (/verify: the web simulator or the real watches). Then commit, merge into main and push.

Finish, in order:
1. **Debug mode.** A Simulator | Real watches toggle on the web sim page shows both real watches live over Wi-Fi through `tools/debug_server.py`. It is specified in docs/design/debug-mode.md; build it with the `debug-mode-build` workflow, then sync the Design canvas (docs/handoff.md lists what changed).
2. **Real watches.** I flash and deploy (`tools/flash.sh`, `tools/deploy.py`); you can't reach the watches from a cloud thread. Turn what I report from the bring-up checklist in docs/hardware-setup.md into fixes.
3. **Calibrate on real data.** Record sessions with debug mode, replay them through the estimator bake-off, and retune the thresholds in `docs/design/tokens.json` (never by hand in `finder/tuning.py`).
4. **Field test** with two players, following docs/research/user-research.md, and fix what it finds.

Keep:
- **Honest on screen:** distance only as bands, never metres or dBm; FOUND only after a physical bump.
- **The watch:** MicroPython 1.29 compatible, allocation-free render loop, and the AGENTS.md hard rules.
- **Secrets:** Wi-Fi credentials only in the gitignored `secrets.py`, never in a commit, a log, a notebook output or a chat.
- **Explanations** in plain language; I'm not an RF or estimation expert.

How to work:
- **Proof:** both test runners green (CPython and MicroPython in WebAssembly), then check the change in the web simulator. Keep `ui-spec.md`, snapshots and the Design canvas in step with any visual change.
- **Agents:** use workflows for independent tracks (`.claude/workflows/review-fix-round.js` runs a verified review round), and don't waste tokens: after the first full pass, review only what changed.
- **Shipping:** commit as shogowarrior and push to main when the runners are green.

## Instructions

**Setup in every new thread**
- Before the first commit, set `git config user.name "shogowarrior"` and `git config user.email "abhinabray@gmail.com"`. A fresh clone has neither, and the history uses that identity.
- Read CLAUDE.md (it imports AGENTS.md) and docs/handoff.md. Ask me the handoff's open questions in one message before building anything they affect.
- Once per clone: `cd tools/mpy && npm install` (the MicroPython test runner).

**Shipping**
- `main` is what goes on the watches. When the work is verified, merge your branch into `main` and push without asking.
- Before pushing, run both test runners in full once: `python3 tests/runner.py` and `node tools/mpy/run.mjs tests/runner.py`. Also run `python3 tools/gen_tuning.py --check` if tokens changed, `python3 tools/render_snapshots.py` if a screen changed, and `python3 tools/build_sim.py` if finder/, ui/ or sim/ changed.

**Hardware and secrets**
- Never flash, erase or deploy to a watch, and never download firmware: I do that. Give me the exact command and what to look for.
- Never open, print or commit `secrets.py` (Wi-Fi name and password). The game joins Wi-Fi only in debug mode.

**Proving things**
- Prove every check works by planting a failure first; a pass on a healthy tree proves nothing.
- Use the simulator, the bake-off (`tools/bakeoff.py`) and real recorded sessions rather than eyeballing. Judge estimator changes only on the held-out seeds 100-129.
- Don't reason about MicroPython from CPython: run it with `node tools/mpy/run.mjs`.

**Code**
- Nothing new on the watch beyond stock MicroPython 1.29; host tools use the Python standard library only.
- Comments are one or two lines stating the fact; the story belongs in the commit message. Commit messages: imperative and specific.

**Agents and tools**
- Use workflows for independent tracks. Review your own work in rounds: find, verify each finding with an independent skeptic, fix, re-run both runners, then the next round. After the first full pass, review only the files that changed.
- Tell every agent, in its first prompt, to run commands in the foreground and stop any server it starts. Keep batches small and have agents report partial results early.
- Browsers: the built-in browser pane on my Mac; headless scripts in cloud threads, never visible windows.
- Don't waste tokens.

**Collaboration**
- Only one thread at a time edits finder/game.py, app/runtime.py, ui/renderer.py or web/sim/index.html.
- Show me mockups (the Design canvas) before changing how a screen looks.
- Explain in plain language; define any RF, estimation or hardware term the first time you use it.
