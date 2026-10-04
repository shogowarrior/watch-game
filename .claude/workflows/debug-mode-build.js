// Builds the debug mode specified in docs/design/debug-mode.md: three tracks in parallel
// (watch side, laptop bridge, page), an end-to-end check with fake watches, a verified
// review, fixes, docs and a final end-to-end check. args (optional): repo, scratch.
export const meta = {
  name: 'debug-mode-build',
  description: 'Build the Real-watches debug mode (watch Wi-Fi link, laptop bridge, page toggle), then review, fix and verify it end to end',
  phases: [
    { title: 'Build', detail: 'three tracks in parallel on disjoint files: watch side, laptop bridge, page' },
    { title: 'Integrate', detail: 'all tests, sim build, end-to-end run with fake watches' },
    { title: 'Review', detail: 'bugs, design and quality lenses on the new code, each finding verified' },
    { title: 'Fix', detail: 'one fixer per track' },
    { title: 'Docs', detail: 'hardware setup, AGENTS, README, architecture' },
    { title: 'Final', detail: 'full checks and end-to-end again' },
  ],
}

const REPO = (args && args.repo) || 'the current working directory (the repo root)'
const SCRATCH = (args && args.scratch) || 'a scratch directory outside the repo ($TMPDIR/watch-game-debug)'
const SPEC = 'docs/design/debug-mode.md'

const COMMON = `You are working on the "Homing" repo in ${REPO}: a two-watch find-each-other game for LILYGO T-Watch 2020 V1 on stock MicroPython 1.29 (finder/ = pure logic, ui/ = renderer, hal/ = drivers, app/ = watch loop, sim/ + web/sim = browser simulator, tools/ = host scripts). The AGENTS.md hard rules apply. Safety: never run git commands that change the index, working tree or history (diff/log/show/status are fine); never open, print or copy the contents of secrets.py or webrepl_cfg.py (code you write may read secrets.py ON THE WATCH at run time, and tests may use a temporary fake secrets file); never touch firmware/; never flash, erase or deploy to a watch; never download anything; no new dependencies (CPython stdlib only for host tools). Scratch files go under ${SCRATCH}. Tests: python3 tests/runner.py [module ...] and node tools/mpy/run.mjs tests/runner.py [module ...] (MicroPython 1.29 in WebAssembly). Always run commands in the foreground and never leave a server running when you finish (kill what you start). The feature you are building, including the fixed contract between the tracks (its "Contract between the parts" section), is specified in ${SPEC}. Read it first, and change its Status line to built when the Final stage passes. The owner is not an RF or networking expert: user-facing text is plain language.`

const TRACKS = [
  { key: 'watch', name: 'watch side', owned: ['hal/debuglink.py', 'hal/radio.py', 'hal/board.py', 'app/telemetry.py', 'app/runtime.py', 'main.py', 'tools/deploy.py', 'tools/fake_watches.py', 'tests/fakes/', 'tests/test_debuglink.py', 'tests/test_hal_radio.py', 'tests/test_hal_board.py', 'tests/test_app_runtime.py', 'tests/test_deploy.py', 'tests/test_fake_watches.py', 'secrets.example.py'], tests: 'test_debuglink test_hal_radio test_hal_board test_app_runtime test_deploy test_fake_watches test_hal_imports test_fakes',
    job: 'hal/debuglink.py (join the AP with a timeout, read the AP channel, UDP sender to host:port with broadcast fallback, counters, never raises into the loop; only hal/ imports network/socket), the EspNowRadio associated mode (keep the STA connection, AP channel 1-13, PM_NONE) with normal play unchanged, Board wiring, the Telemetry sink plus the "rp" record (and the dev/mac/t fields on every datagram), runtime wiring, main.py reading /debug (missing secrets or a failed join: print why and play normally), deploy.py --debug A / --debug-host / --no-debug, and tools/fake_watches.py (two simulated watches sending the exact same datagrams, built by the same app/telemetry.py code). Add fakes for network/socket as needed and tests for every path.' },
  { key: 'bridge', name: 'laptop bridge', owned: ['tools/debug_server.py', 'tests/test_debug_server.py', 'tests/test_secrets_guard.py', '.claude/launch.json', '.gitignore'], tests: 'test_debug_server test_secrets_guard',
    job: 'tools/debug_server.py exactly as the contract says (static files with no-store, UDP listener, SSE /events, /debug/status, JSONL log under logs/ unless --no-log, --demo that imports tools.fake_watches.run lazily so it works once the watch track lands), launch.json web-sim running it, logs/ in .gitignore, tests with real localhost sockets and short timeouts (CPython only: skip cleanly under MicroPython), and tests/test_secrets_guard.py (if a local secrets.py exists, load its values without printing them and assert no git-tracked file contains any of them; assert secrets.py, webrepl_cfg.py and logs/ are gitignored; skip when there is no secrets.py or no git).' },
  { key: 'page', name: 'page and web sim host', owned: ['web/sim/index.html', 'sim/webhost.py', 'tests/test_webhost.py', 'tools/build_sim.py', 'tools/bench_webhost.py'], tests: 'test_webhost',
    job: 'the Simulator | Real watches toggle at the top of web/sim/index.html and the whole Real mode view per the spec (screens drawn from the latest rp record via TwoWatchSim.show_params, per-watch readouts with plain-language labels, last-heard age, the distance chart fed from d_est/d_lo/d_hi and rssi_f, a collapsible raw log, the waiting and unavailable states), TwoWatchSim.show_params / real_mode in sim/webhost.py with tests, and the build: dist/sim/index.html (the artifact body) must show Real as unavailable with one plain sentence and the local command, while local.html served by tools/debug_server.py gets the working mode (the page can decide at run time by fetching /debug/status). Keep the page working at 375 px and in both themes, and keep window.fieldSim working.' },
]

const FIX = {
  type: 'object',
  properties: {
    applied: { type: 'array', items: { type: 'object', properties: { title: { type: 'string' }, files: { type: 'array', items: { type: 'string' } }, change: { type: 'string' } }, required: ['title', 'files', 'change'] } },
    skipped: { type: 'array', items: { type: 'object', properties: { title: { type: 'string' }, reason: { type: 'string' } }, required: ['title', 'reason'] } },
    outside_changes_needed: { type: 'array', items: { type: 'object', properties: { file: { type: 'string' }, change: { type: 'string' }, reason: { type: 'string' } }, required: ['file', 'change', 'reason'] } },
    tests: { type: 'string' },
  },
  required: ['applied', 'skipped', 'outside_changes_needed', 'tests'],
}
const FINDINGS = {
  type: 'object',
  properties: { findings: { type: 'array', items: { type: 'object', properties: {
    file: { type: 'string' }, line: { type: 'integer' }, severity: { type: 'string', enum: ['critical', 'high', 'medium', 'low'] },
    title: { type: 'string' }, detail: { type: 'string' }, evidence: { type: 'string' }, suggested_fix: { type: 'string' } },
    required: ['file', 'line', 'severity', 'title', 'detail', 'evidence', 'suggested_fix'] } } },
  required: ['findings'],
}
const VERDICTS = {
  type: 'object',
  properties: { verdicts: { type: 'array', items: { type: 'object', properties: {
    index: { type: 'integer' }, real: { type: 'boolean' }, severity: { type: 'string', enum: ['critical', 'high', 'medium', 'low'] }, reasoning: { type: 'string' }, fix: { type: 'string' } },
    required: ['index', 'real', 'severity', 'reasoning', 'fix'] } } },
  required: ['verdicts'],
}
const CHECK = {
  type: 'object',
  properties: {
    tests_cpython: { type: 'string' }, tests_mpy: { type: 'string' }, gen_tuning_check: { type: 'string' }, sim_build: { type: 'string' },
    end_to_end: { type: 'string', description: 'what you ran (debug_server --demo, curl of /debug/status and /events, the page in the browser pane) and what you saw' },
    fixed: { type: 'array', items: { type: 'string' } }, problems: { type: 'array', items: { type: 'string' } },
  },
  required: ['tests_cpython', 'tests_mpy', 'gen_tuning_check', 'sim_build', 'end_to_end', 'fixed', 'problems'],
}

const owns = (t, f) => t.owned.some(p => f === p || (p.endsWith('/') && f.startsWith(p)))
const norm = f => { let s = String(f || '').trim(); if (args && args.repo && s.startsWith(args.repo + '/')) s = s.slice(args.repo.length + 1); if (s.startsWith('./')) s = s.slice(2); return s }

phase('Build')
const built = await parallel(TRACKS.map(t => () => agent(
  `${COMMON}\n\nTASK: build the ${t.name} track. You own ONLY these files (entries ending in / are prefixes; you may create the new ones): ${t.owned.join(', ')}. Two other agents are building the other tracks at the same time in the same tree, so never edit outside your list; if you need a change elsewhere, list it in outside_changes_needed. Your job: ${t.job}\n\nKeep the AGENTS.md hard rules (MicroPython 1.29 compatible for anything the watch or the browser runs, allocation-free render loop, tick helpers from finder.compat, only hal/ imports machine/network/espnow/socket). Match the surrounding code's style and comment density; small focused functions; no speculative options. Write tests that would fail without your code. Finish by running both runners on your modules (${t.tests}) and report the result lines; a failure in a module you do not own may be another agent mid-edit, note it and move on.`,
  { label: `build:${t.key}`, phase: 'Build', schema: FIX })))

phase('Integrate')
const outside1 = built.filter(Boolean).flatMap((b, i) => (b.outside_changes_needed || []).map(o => `- [${TRACKS[i].key}] ${o.file}: ${o.change} (why: ${o.reason})`))
const integ1 = await agent(
  `${COMMON}\n\nTASK: integrate the three tracks that were just built in parallel. You may edit any file now (the builders are done). First apply these cross-track requests if still needed:\n${outside1.join('\n') || '(none)'}\n\nThen make everything work together: (1) python3 tests/runner.py and node tools/mpy/run.mjs tests/runner.py, fix failures; (2) python3 tools/gen_tuning.py --check; (3) python3 tools/build_sim.py; (4) end to end: start python3 tools/debug_server.py --demo --no-log --http-port 8799 --udp-port 47299 in the background, curl http://127.0.0.1:8799/debug/status until both fake watches appear, read a few seconds of /events with curl -N --max-time 4 and check the datagrams match the contract, then load the built-in browser pane tools (ToolSearch "select:mcp__Claude_Browser__navigate,mcp__Claude_Browser__javascript_tool,mcp__Claude_Browser__computer,mcp__Claude_Browser__read_page") and open http://localhost:8799/local.html, switch to Real watches, and check both screens draw, the readouts and chart update, and switching back to Simulator restores the sim (window.fieldSim still works); also open dist/sim/index.html's behaviour without the bridge (python3 -m http.server on another port) and check Real shows as unavailable with the plain sentence. Kill every server you started. Report what you saw.`,
  { label: 'integrate:1', phase: 'Integrate', schema: CHECK })

phase('Review')
const LENSES = {
  bugs: 'adversarial correctness: Wi-Fi join failures and timeouts, a watch that never gets an IP, AP channel vs ESP-NOW channel (normal play must be unchanged), send errors, oversized datagrams, non-blocking sockets, tick wrap, allocation in the render loop, MicroPython 1.29 API differences (test claims with node tools/mpy/run.mjs), the bridge with zero/one/two watches, malformed datagrams, SSE client disconnects, thread safety, port already in use, log file growth, page state when records stop or the bridge dies, switching modes repeatedly',
  design: 'conformance to docs/design/debug-mode.md (spec and contract) and the AGENTS.md hard rules, plus security: could the Wi-Fi password ever be printed, logged, sent, committed or shown; is the bridge bound to localhost for HTTP; is everything user-facing plain language; does the artifact build degrade cleanly',
  quality: 'lean and reusable: duplicated record-building or parsing logic (one source for the datagram format), dead code, speculative options, over-defensive code, comments that restate code, tests that cannot fail',
}
const reviewed = await parallel(Object.keys(LENSES).map(k => async () => {
  const f = await agent(`${COMMON}\n\nTASK: read-only review (edit nothing) of the new debug-mode feature: every file in git diff HEAD --stat plus the untracked new files (git status --short). LENS: ${LENSES[k]}. Report up to 12 findings, most severe first; low severity only for real defects or clear waste. Prove bugs with a short script or test run where you can.`, { label: `review:${k}`, phase: 'Review', schema: FINDINGS })
  const list = (f && f.findings) || []
  if (!list.length) return []
  const v = await agent(`${COMMON}\n\nTASK: adversarial verification (read-only). For each finding below, try to REFUTE it: read the code, run it, check docs/design/debug-mode.md. real=true only if it is wrong now and fixing it is a net improvement; if uncertain, real=false. Give the exact fix for real ones.\n\n${list.map((x, i) => `[${i}] ${x.file}:${x.line} (${x.severity}) ${x.title}\n  ${x.detail}\n  evidence: ${x.evidence}\n  fix: ${x.suggested_fix}`).join('\n\n')}`, { label: `verify:${k}`, phase: 'Review', schema: VERDICTS })
  const vs = (v && v.verdicts) || []
  return list.map((x, i) => { const vd = vs.find(y => y.index === i); return Object.assign({}, x, { file: norm(x.file), lens: k, real: !!(vd && vd.real), severity: vd ? vd.severity : x.severity, fix: vd ? vd.fix : '', why: vd ? vd.reasoning : 'no verdict' }) })
}))
const findings = reviewed.filter(Boolean).flat()
const confirmed = findings.filter(x => x.real)
log(`review: ${findings.length} found, ${confirmed.length} confirmed`)

phase('Fix')
const fixes = await parallel(TRACKS.map(t => async () => {
  const mine = confirmed.filter(x => owns(t, x.file))
  if (!mine.length) return null
  return agent(`${COMMON}\n\nTASK: fix verified review findings in the ${t.name} track. You own only: ${t.owned.join(', ')}; other fixers work in parallel on the other tracks. Merge overlapping findings, add a test for each bug, run both runners on ${t.tests}, list changes needed elsewhere in outside_changes_needed.\n\n${mine.map(x => `- ${x.file}:${x.line} (${x.severity}) ${x.title}\n  ${x.detail}\n  verified fix: ${x.fix}`).join('\n')}`, { label: `fix:${t.key}`, phase: 'Fix', schema: FIX })
}))
const elsewhere = confirmed.filter(x => !TRACKS.some(t => owns(t, x.file)))
const outside2 = fixes.filter(Boolean).flatMap(f => f.outside_changes_needed || [])

phase('Docs')
const docs = await agent(
  `${COMMON}\n\nTASK: document the debug mode and apply leftover changes. You may edit any file now. (A) Apply these verified findings outside the three tracks and these requested changes:\n${elsewhere.map(x => `- ${x.file}:${x.line} ${x.title}: ${x.fix}`).join('\n') || '(none)'}\n${outside2.map(o => `- ${o.file}: ${o.change} (why: ${o.reason})`).join('\n') || ''}\n(B) Docs, in plain language for a non-expert owner: docs/hardware-setup.md gets a "Debug mode: watch the real watches on your laptop" section (what it is, the exact commands: secrets.py from secrets.example.py, deploy.py --debug A / B, python3 tools/debug_server.py, open http://localhost:8765/local.html, flip the toggle; both watches must join the same Wi-Fi; the macOS firewall may ask to allow python; how to turn it off with --no-debug; where the logs go and how to replay them; trying it with no watches via --demo). Update AGENTS.md (repo map rows for the new files, the commands table, the Security section: the game joins Wi-Fi only in debug mode with the gitignored secrets.py, rule 12 includes socket), README.md (one short paragraph), docs/architecture.md (the debug data path), hal/README.md (debuglink and the radio's associated mode), CLAUDE.md only if a rule for agents changes (e.g. the web-sim preview now runs tools/debug_server.py). Keep each doc's style; link instead of repeating. Run both runners at the end.`,
  { label: 'docs', phase: 'Docs', schema: FIX })

phase('Final')
const final = await agent(
  `${COMMON}\n\nTASK: final check of the debug-mode feature after review fixes and docs (you may edit any file to fix regressions; minimal changes; never weaken a test). Repeat exactly the integration procedure: both full runners, gen_tuning --check, build_sim, and the end-to-end run (debug_server --demo on ports 8799/47299, /debug/status, /events, the page in the built-in browser pane in Real mode and back to Simulator, and the artifact build without the bridge showing Real as unavailable). Also grep the whole diff and the new files for anything that could print or send WIFI_PASSWORD / WIFI_SSID values, and confirm git check-ignore covers secrets.py, webrepl_cfg.py and logs/. Kill every server you started. Report.`,
  { label: 'final', phase: 'Final', schema: CHECK })

return {
  build: built.map((b, i) => b && { track: TRACKS[i].key, applied: b.applied.map(a => a.title), skipped: b.skipped, tests: b.tests }),
  integrate1: integ1,
  review: { found: findings.length, confirmed: confirmed.map(x => `${x.file}:${x.line} [${x.severity}/${x.lens}] ${x.title}`), rejected: findings.filter(x => !x.real).map(x => `${x.file}:${x.line} ${x.title} -- ${String(x.why).slice(0, 140)}`) },
  fixes: fixes.map((f, i) => f && { track: TRACKS[i].key, applied: f.applied.map(a => a.title), skipped: f.skipped, tests: f.tests }),
  docs: docs && { applied: docs.applied.map(a => a.title), skipped: docs.skipped, tests: docs.tests },
  final,
}
