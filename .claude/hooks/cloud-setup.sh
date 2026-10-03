#!/bin/bash
# SessionStart hook. Cloud sessions only: install the MicroPython test runner's one
# package (tools/mpy). CLAUDE_CODE_REMOTE is never "true" locally, so this exits at once.
[ "$CLAUDE_CODE_REMOTE" = "true" ] || exit 0
cd "$CLAUDE_PROJECT_DIR/tools/mpy" || exit 0
[ -d node_modules/@micropython ] && exit 0
npm ci --no-audit --no-fund >/dev/null 2>&1 ||
  echo "tools/mpy: npm ci failed; run it by hand before the MicroPython tests" >&2
exit 0
