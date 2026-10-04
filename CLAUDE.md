@AGENTS.md

**Work in progress: read [docs/project/handoff.md](docs/project/handoff.md) first.** It lists the
decisions to ask the owner about before starting and the next steps in order.

## Claude Code notes

- Commits are authored as `shogowarrior <abhinabray@gmail.com>`. The owner's
  checkout sets this in its git config; a fresh clone (a cloud or Project thread)
  does not, so run `git config user.name shogowarrior` and
  `git config user.email abhinabray@gmail.com` before the first commit.
- `.claude/workflows/` holds the review round (`review-fix-round`) and the
  debug-mode build (`debug-mode-build`); each file's header lists its args.

- Before saying something is done, run **both** test runners and read the result
  lines: `python3 tests/runner.py` and `node tools/mpy/run.mjs tests/runner.py`.
  If you touched `docs/design/tokens.json`, also `python3 tools/gen_tuning.py --check`.
- Work in phases with review rounds: after each phase, review your own diff
  against `docs/design/ui-spec.md` and the hard rules in AGENTS.md, fix what you
  find, re-run both runners, and only then start the next phase.
- When UI behaviour or visuals change, update in the same change: `ui-spec.md`
  first, then the code and tests, then the snapshots
  (`python3 tools/render_snapshots.py`) and the Design canvas mockups
  (linked from `web/sim/index.html`) so they still
  match the spec. Rebuild the web sim (`python3 tools/build_sim.py`) if
  `finder/`, `ui/` or `sim/` changed.
- Never flash, erase or deploy to a watch, and never download firmware or touch
  `firmware/`, unless the user asks in chat. Never read out or commit the
  contents of `secrets.py`.
- Don't run git commands that modify the index or history unless the user asks.
