**Standing goals, for every change**
- **Hygiene first:** code that is easy to read, highly modular and reused: small functions and shared helpers and fixtures used by both production code and tests, wherever they apply. Review to improve, never to break a feature or cause a regression.
- **Clean and efficient:** nothing redundant, no dead code, no unnecessary bloat, no AI slop.
- **Never delete a feature.** Work on a branch.
- **When a piece of work is done:** review it thoroughly and judge it against the design (docs/design/ui-spec.md), code quality, modularity and reuse. Run adversarial reviews for defects, bugs, redundant and dead code, bloat and AI slop, and /code-review, in rounds. Fix everything a round finds before starting the next, until a round raises no flags. Check the change where it runs (/verify: the web simulator or the real watches). Then commit, merge into main and push.

**Setup in every new thread**
- Before the first commit, set `git config user.name "shogowarrior"` and `git config user.email "abhinabray@gmail.com"`. A fresh clone has neither, and the history uses that identity.
- Read CLAUDE.md (it imports AGENTS.md) and docs/project/handoff.md. Ask me the handoff's open questions in one message before building anything they affect.
- Once per clone: `cd tools/mpy && npm ci` (the MicroPython test runner; the environment script caches its package).

**Shipping**
- `main` is what goes on the watches. When the work is verified, merge your branch into `main` and push without asking.
- Before pushing, run both test runners in full once: `python3 tests/runner.py` and `node tools/mpy/run.mjs tests/runner.py`. Also run `python3 tools/gen_tuning.py --check` if tokens changed, `python3 tools/render_snapshots.py` if a screen changed, and `python3 tools/build_sim.py` if finder/, ui/ or sim/ changed.

**What a cloud thread cannot do**
- There are no watches, no USB and no home Wi-Fi: never flash, erase or deploy, and never download firmware. Anything that needs a watch goes to me as exact commands and what to look for (docs/hardware-setup.md).
- There is no desktop browser pane: check the web sim with a headless browser script, never a visible window.
- If /verify or /code-review cannot be started from the thread, ask me to run it and never claim it ran.

**Hardware and secrets**
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
- Don't waste tokens.

**Collaboration**
- Only one thread at a time edits finder/game.py, app/runtime.py, ui/renderer.py or web/sim/index.html.
- Show me mockups (the Design canvas) before changing how a screen looks.
- Explain in plain language; define any RF, estimation or hardware term the first time you use it.
