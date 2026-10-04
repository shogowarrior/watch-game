// Review/fix round over the repo: per area, three lenses (bugs, design, quality) find
// problems, a skeptic verifies each, one fixer per area fixes its own files, then
// cross-cutting, docs and an integration check. Run it, read the result, run it again.
// args (all optional):
//   round: N                 shown to agents; >1 makes them scrutinise git diff HEAD
//   seenFile: path           findings already handled (one per line), not re-reported
//   open: [text]             known open issues to verify and fix
//   changed: [paths]         review only these files (use after the first full round)
//   lenses: ['bugs','design','quality'], skipCross: bool
//   repo, scratch: paths     defaults: the current directory, $TMPDIR/watch-game-review
export const meta = {
  name: 'review-fix-round',
  description: 'One full review round: find defects per area and lens, adversarially verify, fix per owned area, then cross-cutting, docs and integration',
  whenToUse: 'Repeated review/fix rounds over the whole watch-game repo until no findings survive',
  phases: [
    { title: 'Find', detail: 'per-area finders: bugs, design conformance, quality/slop; plus cross-cutting and docs' },
    { title: 'Verify', detail: 'batch adversarial verifier per finder, second skeptic for high/critical' },
    { title: 'Fix', detail: 'one fixer per area, only its own files' },
    { title: 'Cross-cutting', detail: 'serial fixer for cross-area findings and outside changes' },
    { title: 'Docs', detail: 'serial docs fixer' },
    { title: 'Integrate', detail: 'full test runs, tuning check, snapshots, sim build, regressions' },
  ],
}

const ROUND = (args && args.round) || 1
const SEEN_FILE = (args && args.seenFile) || ''
const OPEN = (args && args.open) || []
const CHANGED = (args && args.changed) || null
const LENS_KEYS = (args && args.lenses) || ['bugs', 'design', 'quality']
const SKIP_CROSS = !!(args && args.skipCross)
const REPO = (args && args.repo) || 'the current working directory (the repo root)'
const SCRATCH = (args && args.scratch) || 'a scratch directory outside the repo ($TMPDIR/watch-game-review)'

const UNITS = [
  { key: 'game', name: 'game state machine', owned: ['finder/game.py', 'finder/menu.py', 'finder/render_params.py', 'finder/session.py', 'finder/pairing.py', 'tests/test_game.py', 'tests/test_menu.py', 'tests/test_render_params.py'], tests: 'test_game test_menu test_render_params test_episode' },
  { key: 'estimation', name: 'range estimation and motion', owned: ['finder/estimators/', 'finder/proximity.py', 'finder/motion.py', 'tests/test_est_', 'tests/est_helpers.py', 'tests/test_proximity.py', 'tests/test_motion.py'], tests: 'test_est_default test_est_ema test_est_kalman1d test_est_kalman2 test_est_median_ema test_est_particle test_proximity test_motion' },
  { key: 'direction', name: 'scan, arrow, gestures, haptic patterns, link, wire protocol, compat', owned: ['finder/scan.py', 'finder/arrow.py', 'finder/gestures.py', 'finder/haptic_patterns.py', 'finder/link.py', 'finder/proto.py', 'finder/compat.py', 'finder/__init__.py', 'tests/test_scan.py', 'tests/test_arrow.py', 'tests/test_gestures.py', 'tests/test_haptics.py', 'tests/test_link.py', 'tests/test_proto.py', 'tests/test_compat.py'], tests: 'test_scan test_arrow test_gestures test_haptics test_link test_proto test_compat' },
  { key: 'ui', name: 'strip renderer and ripple field', owned: ['ui/', 'tests/test_renderer.py', 'tools/render_snapshots.py', 'tools/png.py'], tests: 'test_renderer' },
  { key: 'hal', name: 'hardware drivers and fakes', owned: ['hal/__init__.py', 'hal/axp202.py', 'hal/bma423.py', 'hal/board.py', 'hal/ft6336.py', 'hal/haptics.py', 'hal/pins.py', 'hal/radio.py', 'hal/st7789.py', 'hal/watchdog.py', 'tests/test_hal_', 'tests/test_watchdog.py', 'tests/fakes/', 'tests/test_fakes.py', 'tools/bench_display.py', 'tools/radio_pingpong.py'], tests: 'test_hal_axp202 test_hal_bma423 test_hal_board test_hal_ft6336 test_hal_imports test_hal_radio test_hal_st7789 test_watchdog test_fakes' },
  { key: 'app', name: 'watch runtime and entry points', owned: ['app/', 'main.py', 'boot.py', 'tests/test_app_runtime.py', 'tools/deploy.py', 'tests/test_deploy.py'], tests: 'test_app_runtime test_deploy' },
  { key: 'debug', name: 'debug mode: watch USB/Wi-Fi link, laptop bridge, fake watches, Wi-Fi setup', owned: ['hal/debuglink.py', 'tools/debug_server.py', 'tools/fake_watches.py', 'tools/wifi_setup.py', 'secrets.example.py', 'tests/test_debuglink.py', 'tests/test_debug_server.py', 'tests/test_fake_watches.py', 'tests/test_wifi_setup.py', 'tests/test_secrets_guard.py'], tests: 'test_debuglink test_debug_server test_fake_watches test_wifi_setup test_secrets_guard' },
  { key: 'sim', name: 'two-watch simulator and web sim page', owned: ['sim/', 'web/sim/index.html', 'tests/test_sim.py', 'tests/test_webhost.py', 'tests/test_episode.py', 'tools/build_sim.py', 'tools/bench_webhost.py'], tests: 'test_sim test_webhost test_episode' },
  { key: 'tooling', name: 'tuning generation, bake-off, benches, runners, flashing scripts', owned: ['tools/gen_tuning.py', 'finder/tuning.py', 'docs/design/tokens.json', 'tests/test_tuning.py', 'tools/bakeoff.py', 'tools/bench_est.py', 'tools/drift_demo.py', 'tools/flash.sh', 'tools/fetch_bma423_config.sh', 'tools/mpy/run.mjs', 'tools/mpy/package.json', 'tests/runner.py', 'tests/test_runner.py', 'tests/__init__.py', 'requirements.txt', '.gitignore', '.claude/launch.json'], tests: 'test_tuning test_runner' },
]

const DOC_GROUPS = [
  { key: 'docs-guides', name: 'agent guides, READMEs, architecture, hardware setup, notebooks', files: ['AGENTS.md', 'CLAUDE.md', 'README.md', 'hal/README.md', 'docs/architecture.md', 'docs/hardware-setup.md', 'notebooks/finder_dev.ipynb', 'notebooks/tools.ipynb', 'notebooks/watch.ipynb', 'notebooks/watch movement.ipynb'] },
  { key: 'docs-specs', name: 'ui spec, debug-mode spec, design system, estimation and research docs', files: ['docs/design/ui-spec.md', 'docs/design/debug-mode.md', 'docs/design/design-system.md', 'docs/estimation/bakeoff.md', 'docs/estimation/imu-drift.md', 'docs/research/user-research.md'] },
]

const COMMON = `You are working on the "Homing" repo in ${REPO}: a two-watch find-each-other game for LILYGO T-Watch 2020 V1 running stock MicroPython 1.29 (finder/ = pure logic, ui/ = renderer, hal/ = drivers, app/ = watch loop, sim/ + web/sim = simulator, tools/ = host scripts). The AGENTS.md hard rules apply (they are in your context). Safety rules for every agent: never run git commands that change the index, working tree or history (git diff/log/show/grep/blame are fine); never open or print secrets.py or webrepl_cfg.py; never touch firmware/; never flash, erase or deploy to a watch; never download anything. Put any scratch files under ${SCRATCH} (create it if needed). Tests: python3 tests/runner.py [module ...] (CPython) and node tools/mpy/run.mjs tests/runner.py [module ...] (real MicroPython 1.29 in WebAssembly). Run commands from the repo root.`

const seenBlock = (SEEN_FILE
  ? `\n\nEarlier review rounds already reported many findings (fixed, judged not real, or deliberately skipped with a reason). The full list is in ${SEEN_FILE}: read it before you report. Do NOT re-report those unless the fix is incomplete, introduced a new problem, or the skip reason is wrong — in that case say so explicitly in the title.`
  : '') + (OPEN.length
  ? `\n\nKnown open issues from the last integration check. If one is in your scope, verify it and report it as a finding if it is real:\n- ${OPEN.join('\n- ')}`
  : '')
const roundBlock = ROUND > 1
  ? `\n\nThis is review round ${ROUND}. Earlier rounds already edited the code; those edits are uncommitted (see git diff HEAD). Review the current files as a whole, and give the lines changed by git diff HEAD extra adversarial scrutiny: fixes often introduce new bugs, leave dead code behind, or drift from docs.`
  : ''

const LENSES = {
  bugs: `LENS: adversarial correctness. Assume there ARE defects and hunt them. For every function ask what input, state, timing or platform makes it wrong: tick wrap (ticks wrap at 2^30 ms; raw tick comparison is a bug), None, empty, zero, negative, NaN/inf, integer vs float and int overflow differences on MicroPython, first call, re-entry, reset-then-reuse, stale state carried across rounds or states, unreachable or stuck states in state machines, missed transitions, off-by-one, wrong units (ms vs s, m, dBm, Q8), sign errors, copy-paste with the wrong variable, swallowed exceptions, allocation in per-frame/per-packet hot paths (hard rule 2), and violations of hardware rules 3-6, 14, 15. Trace callers across files with grep to confirm a defect actually reaches the game or the user. Prefer findings you can demonstrate: write and run a short script or test call, and put the command and its output in evidence.`,
  design: `LENS: design conformance. Judge this area against docs/design/ui-spec.md (behaviour source of truth), docs/design/tokens.json (its values win over the spec), docs/design/design-system.md, docs/architecture.md and the AGENTS.md hard rules. Flag: behaviour that deviates from the spec (quote the spec section), magic numbers that should come from finder/tuning.py or ui-spec, spec requirements that are missing or half-done, layering violations (finder/ touching hardware, ui/ reading game internals instead of RenderParams, app/ bypassing Board, sim/ reaching into private fields), tight coupling and leaky interfaces between modules, modules or classes that do too many jobs and should be split, and docstrings or comments that describe behaviour the code does not have.`,
  quality: `LENS: lean, modular, reusable code. Hunt for: (1) dead code: functions, classes, methods, constants, parameters, branches, imports, fields or test helpers nothing uses. Before claiming dead, grep the WHOLE repo including tests/, tools/, sim/, web/sim/index.html and notebooks/; (2) duplication: logic re-implemented here that already exists elsewhere (name the existing helper with file:line), or copy-paste with small variations; (3) bloat: speculative generality, options nobody passes, wrappers that add nothing, over-defensive checks for impossible states, try/except that hides bugs, redundant or derivable state; (4) AI slop: comments that narrate or restate the code, docstrings longer than the code they describe, filler wording, inconsistent naming, "just in case" code; (5) tests: vacuous or tautological assertions, tests that cannot fail, duplicated fixtures that should be shared, an important branch with no test. Each finding must name the simpler form (what to delete, or which helper to call). Do not flag things that exist for a documented reason (MicroPython allocation rules, hardware gotchas, generated finder/tuning.py) — read the module docstring first.`,
}

const FINDING = {
  type: 'object',
  properties: {
    file: { type: 'string', description: 'repo-relative path' },
    line: { type: 'integer' },
    severity: { type: 'string', enum: ['critical', 'high', 'medium', 'low'] },
    category: { type: 'string', enum: ['bug', 'design', 'dead-code', 'duplication', 'bloat', 'slop', 'test-quality', 'docs-drift', 'perf', 'modularity'] },
    title: { type: 'string', description: 'one line, specific' },
    detail: { type: 'string' },
    evidence: { type: 'string', description: 'code quotes, grep results, or a reproduction command with its output' },
    suggested_fix: { type: 'string' },
  },
  required: ['file', 'line', 'severity', 'category', 'title', 'detail', 'evidence', 'suggested_fix'],
}
const FINDINGS = { type: 'object', properties: { findings: { type: 'array', items: FINDING } }, required: ['findings'] }
const VERDICTS = {
  type: 'object',
  properties: {
    verdicts: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          index: { type: 'integer' },
          real: { type: 'boolean' },
          severity: { type: 'string', enum: ['critical', 'high', 'medium', 'low'] },
          reasoning: { type: 'string' },
          fix: { type: 'string', description: 'the exact fix to apply if real' },
        },
        required: ['index', 'real', 'severity', 'reasoning', 'fix'],
      },
    },
  },
  required: ['verdicts'],
}
const VERDICT = {
  type: 'object',
  properties: { real: { type: 'boolean' }, reasoning: { type: 'string' }, fix: { type: 'string' } },
  required: ['real', 'reasoning', 'fix'],
}
const FIX_RESULT = {
  type: 'object',
  properties: {
    applied: { type: 'array', items: { type: 'object', properties: { title: { type: 'string' }, files: { type: 'array', items: { type: 'string' } }, change: { type: 'string' } }, required: ['title', 'files', 'change'] } },
    skipped: { type: 'array', items: { type: 'object', properties: { title: { type: 'string' }, reason: { type: 'string' } }, required: ['title', 'reason'] } },
    outside_changes_needed: { type: 'array', items: { type: 'object', properties: { file: { type: 'string' }, change: { type: 'string' }, reason: { type: 'string' } }, required: ['file', 'change', 'reason'] } },
    tests: { type: 'string', description: 'the result lines of both runners for your modules' },
  },
  required: ['applied', 'skipped', 'outside_changes_needed', 'tests'],
}
const INTEGRATE = {
  type: 'object',
  properties: {
    tests_cpython: { type: 'string' },
    tests_mpy: { type: 'string' },
    gen_tuning_check: { type: 'string' },
    snapshots: { type: 'string' },
    sim_build: { type: 'string' },
    regressions_fixed: { type: 'array', items: { type: 'string' } },
    visual_changes: { type: 'array', items: { type: 'string' }, description: 'any change that alters what a screen looks like (affects Design canvas mockups)' },
    remaining_problems: { type: 'array', items: { type: 'string' } },
  },
  required: ['tests_cpython', 'tests_mpy', 'gen_tuning_check', 'snapshots', 'sim_build', 'regressions_fixed', 'visual_changes', 'remaining_problems'],
}

function norm(f) {
  let s = String(f || '').trim()
  if (args && args.repo && s.startsWith(args.repo + '/')) s = s.slice(args.repo.length + 1)
  if (s.startsWith('./')) s = s.slice(2)
  return s
}
function owns(unit, file) {
  const f = norm(file)
  return unit.owned.some(p => f === p || ((p.endsWith('/') || p.endsWith('_')) && f.startsWith(p)))
}
function fmtFinding(f, i) {
  return `[${i}] ${f.file}:${f.line} (${f.severity}, ${f.category}) ${f.title}\n  detail: ${f.detail}\n  evidence: ${f.evidence}\n  suggested fix: ${f.suggested_fix}`
}
function fmtConfirmed(f) {
  return `- ${f.file}:${f.line} (${f.severity}, ${f.category}) ${f.title}\n  detail: ${f.detail}\n  verified fix: ${f.fix}`
}

async function findAndVerify(scopeText, lensKey, lensText, label, phaseTag) {
  const found = await agent(
    `${COMMON}\n\nTASK: read-only review (do not edit any file). Scope: ${scopeText}\n\n${lensText}${roundBlock}${seenBlock}\n\nRead every file in scope completely (and the callers/callees you need elsewhere). Report up to 12 findings, most severe first. Report only things you are confident are real; quality beats quantity, but do not hold back real defects. In this round report ONLY findings of medium severity or worse, plus low-severity correctness bugs (wrong behaviour). Skip low-severity cleanup, naming, test-shape and wording items entirely. The bar for low severity is high: report a low finding only if fixing it is a clear net gain (deletes code or tests, removes real duplication, fixes a misleading comment or doc, or removes a trap for the next developer). Do not report matters of taste, renames, reordering, or 'could also be written as' suggestions — the review loop must converge. Return [] if nothing material is wrong.`,
    { label: `find:${label}:${lensKey}`, phase: 'Find', schema: FINDINGS })
  const findings = (found && found.findings) || []
  if (!findings.length) return []
  const v = await agent(
    `${COMMON}\n\nTASK: adversarial verification (read-only: do not edit any file). Another reviewer (lens: ${lensKey}) reported the findings below about: ${scopeText}\n\nFor EACH finding, try hard to REFUTE it. Open the cited code and its callers; grep the whole repo (including tests/, tools/, sim/, web/sim/index.html, notebooks/) before accepting "dead code"; run code to reproduce bugs where possible. Mark real=true only when the problem exists in the CURRENT code AND fixing it is a net improvement that keeps the AGENTS.md hard rules and docs/design/ui-spec.md intact. Typical false positives: "dead" code used by tools, notebooks or the web page; duplication that exists for MicroPython allocation reasons; spec-mandated behaviour; generated finder/tuning.py; claims about MicroPython that are wrong (test them with node tools/mpy/run.mjs). If uncertain, real=false. Also mark real=false for churn: a fix that is a matter of taste, only moves code around without making it smaller or clearer, adds code or tests for a negligible gain, or reverses a deliberate decision recorded in a docstring or doc. Re-rate severity honestly (critical: breaks the game or hardware; high: wrong behaviour a player or developer hits; medium: real defect or clear waste; low: cosmetic/minor cleanup). For real findings give the exact fix to apply.\n\nFINDINGS:\n${findings.map(fmtFinding).join('\n\n')}`,
    { label: `verify:${label}:${lensKey}`, phase: 'Verify', schema: VERDICTS })
  const verdicts = (v && v.verdicts) || []
  const out = []
  for (let i = 0; i < findings.length; i++) {
    const vd = verdicts.find(x => x.index === i)
    const f = Object.assign({}, findings[i], { file: norm(findings[i].file), lens: lensKey, source: label })
    if (!vd || !vd.real) { out.push(Object.assign(f, { status: 'rejected', why: vd ? vd.reasoning : 'no verdict' })); continue }
    f.severity = vd.severity
    f.fix = vd.fix
    if (vd.severity === 'critical' || vd.severity === 'high') {
      const second = await agent(
        `${COMMON}\n\nTASK: independent skeptic (read-only: do not edit any file). A finding was rated ${vd.severity}. Try to prove it is NOT real or NOT that severe. Reproduce it if you can. If uncertain, real=false.\n\n${fmtFinding(f, 0)}\nfirst verifier: ${vd.reasoning}\nproposed fix: ${vd.fix}`,
        { label: `skeptic:${label}:${f.file}:${f.line}`, phase: 'Verify', schema: VERDICT })
      if (!second || !second.real) { out.push(Object.assign(f, { status: 'rejected', why: 'second skeptic: ' + (second ? second.reasoning : 'no verdict') })); continue }
      f.fix = f.fix + '\n  second skeptic: ' + second.fix
    }
    out.push(Object.assign(f, { status: 'confirmed' }))
  }
  return out
}

async function runUnit(unit) {
  const inScope = CHANGED ? CHANGED.filter(f => owns(unit, f)) : null
  if (inScope && !inScope.length) { log(`${unit.key}: no changed files, skipped`); return { unit: unit.key, all: [], mine: [], foreign: [], fix: null } }
  const scope = inScope
    ? `the ${unit.name} area, limited to the files the last fix round changed: ${inScope.join(', ')}`
    : `the ${unit.name} area: ${unit.owned.join(', ')} (entries ending in / or _ are prefixes)`
  const lensResults = await parallel(LENS_KEYS.map(k => () => findAndVerify(scope, k, LENSES[k], unit.key, 'Find')))
  const all = lensResults.filter(Boolean).flat()
  const confirmed = all.filter(f => f.status === 'confirmed')
  const mine = confirmed.filter(f => owns(unit, f.file))
  const foreign = confirmed.filter(f => !owns(unit, f.file))
  let fix = null
  if (mine.length) {
    log(`${unit.key}: ${mine.length} confirmed in own files, ${foreign.length} routed to cross-cutting`)
    fix = await agent(
      `${COMMON}\n\nTASK: apply verified review fixes. You own ONLY these files (entries ending in / or _ are prefixes): ${unit.owned.join(', ')}. Other agents are editing other files at the same time, so do not edit anything outside your list.\n\nFix every finding below. Rules: keep behaviour consistent with docs/design/ui-spec.md and the AGENTS.md hard rules (MicroPython 1.29 compatible, allocation-free hot paths, tuning values from finder/tuning.py which is generated — never hand-edit it); match the surrounding code's naming, comment density and idiom; prefer deleting code over adding it; before deleting anything grep the whole repo (tests/, tools/, sim/, web/sim/index.html, notebooks/) for users; update or add tests in your owned test files so each bug fix has a test that would have failed before; keep new tests short, reuse existing fixtures and helpers, and prefer extending an existing test over adding a near-copy. Several findings may overlap: merge them. If a finding turns out to be wrong once you look closer, skip it with the reason. If a correct fix needs edits outside your files (another module, docs such as ui-spec.md, AGENTS.md, README, tokens.json), do not make them: list each in outside_changes_needed with the exact change. Do not regenerate snapshots or rebuild the web sim. When done, run both runners for your modules: python3 tests/runner.py ${unit.tests} and node tools/mpy/run.mjs tests/runner.py ${unit.tests}. A failure in a module you do not own may be another agent mid-edit: note it, do not chase it. Report the result lines.\n\nVERIFIED FINDINGS:\n${mine.map(fmtConfirmed).join('\n')}`,
      { label: `fix:${unit.key}`, phase: 'Fix', schema: FIX_RESULT })
  } else {
    log(`${unit.key}: nothing confirmed in own files (${foreign.length} routed to cross-cutting)`)
  }
  return { unit: unit.key, all, mine, foreign, fix }
}

phase('Find')
const crossScope = 'the whole codebase: finder/, ui/, hal/, app/, sim/, tools/, tests/, web/sim/index.html, main.py, boot.py'
const CROSS = [
  { key: 'xdup', lens: `LENS: duplication and reuse ACROSS modules. Look for the same logic implemented in two or more places in different areas: math helpers (clamp, lerp, Q8, wrap angle, dB/metre conversion), tick handling, RSSI path-loss conversion, ring buffers, JSON/PNG/telemetry writers, argument parsing in tools, repeated fake hardware setup in tests, the same constants defined twice, renderer or field logic re-implemented in sim/webhost.py or web/sim/index.html, estimator boilerplate copied between estimators. For each, name every copy with file:line and the single place it should live. Only report duplication where sharing is a clear win and keeps MicroPython/allocation rules.` },
  { key: 'xarch', lens: `LENS: architecture and modularity across the whole repo. Look for god modules or classes doing many jobs (finder/game.py is ~1400 lines, app/runtime.py ~760, ui/renderer.py ~760: decide whether each SHOULD be split, and only flag a split when it gives clear, separable responsibilities with a narrow interface), circular or upward imports, hidden coupling through private attributes (obj._x used from another module), interfaces that leak internals, inconsistent patterns for the same job across modules, and public APIs nobody calls. Report concrete, bounded refactors with the exact seams; do not propose rewrites.` },
]
const unitsPromise = pipeline(UNITS, u => runUnit(u))
const crossPromise = SKIP_CROSS ? Promise.resolve([]) : parallel(CROSS.map(c => () => findAndVerify(crossScope, c.key, c.lens, 'cross', 'Find')))
const docsPromise = parallel(DOC_GROUPS.filter(g => !CHANGED || g.files.some(f => CHANGED.includes(f))).map(g => () => findAndVerify(
  `the ${g.name}: ${(CHANGED ? g.files.filter(f => CHANGED.includes(f)) : g.files).join(', ')}`, 'docs',
  `LENS: documentation accuracy and clarity. Check every factual claim against the current code: commands, flags, file paths, function and class names, constants and thresholds (compare with docs/design/tokens.json and finder/tuning.py), behaviour, test counts, the AGENTS.md repo map. Flag stale, wrong or missing statements, contradictions between docs, notebooks that import removed modules or call APIs that no longer exist, and slop: filler, repetition of another doc instead of a link, jargon a newcomer cannot follow without a definition. The user is not an RF/estimation expert and asked for plain language.`,
  g.key, 'Find')))

const [unitResults, crossResults, docsResults] = await Promise.all([unitsPromise, crossPromise, docsPromise])
const units = unitResults.filter(Boolean)

phase('Cross-cutting')
const crossFindings = crossResults.filter(Boolean).flat()
const crossConfirmed = crossFindings.filter(f => f.status === 'confirmed').concat(units.flatMap(u => u.foreign))
const outsideCode = units.flatMap(u => ((u.fix && u.fix.outside_changes_needed) || []).map(o => Object.assign({ from: u.unit }, o)))
const isDoc = f => /\.(md|ipynb)$/.test(norm(f))
const outsideCodeOnly = outsideCode.filter(o => !isDoc(o.file))
const outsideDocs = outsideCode.filter(o => isDoc(o.file))
const crossCodeConfirmed = crossConfirmed.filter(f => !isDoc(f.file))
const crossDocConfirmed = crossConfirmed.filter(f => isDoc(f.file))
let crossFix = null
if (crossCodeConfirmed.length || outsideCodeOnly.length) {
  crossFix = await agent(
    `${COMMON}\n\nTASK: apply cross-cutting fixes. The per-area fixers have finished; you are now the only agent editing code, so you may edit any code or test file (but not .md docs or notebooks: list doc changes in outside_changes_needed). Apply (A) every verified cross-cutting finding and (B) every change the per-area fixers could not make because it was outside their files. Rules: keep behaviour consistent with docs/design/ui-spec.md and the AGENTS.md hard rules; tokens.json -> python3 tools/gen_tuning.py regenerates finder/tuning.py (never hand-edit it); prefer deleting and sharing over adding; grep the whole repo before removing anything; add or update tests; for big refactors (splitting a module) do it only if the finding was verified and you can keep every test green, otherwise skip with a reason. Run both full runners at the end: python3 tests/runner.py and node tools/mpy/run.mjs tests/runner.py, fix what you broke, report the result lines.\n\n(A) VERIFIED CROSS-CUTTING FINDINGS:\n${crossCodeConfirmed.map(fmtConfirmed).join('\n') || '(none)'}\n\n(B) CHANGES REQUESTED BY AREA FIXERS:\n${outsideCodeOnly.map(o => `- [${o.from}] ${o.file}: ${o.change} (why: ${o.reason})`).join('\n') || '(none)'}`,
    { label: 'fix:cross', phase: 'Cross-cutting', schema: FIX_RESULT })
}

phase('Docs')
const docsFindings = docsResults.filter(Boolean).flat()
const docsConfirmed = docsFindings.filter(f => f.status === 'confirmed').concat(crossDocConfirmed)
const moreDocs = outsideDocs.concat(((crossFix && crossFix.outside_changes_needed) || []).map(o => Object.assign({ from: 'cross' }, o)))
const codeChanges = units.flatMap(u => ((u.fix && u.fix.applied) || []).map(a => `- [${u.unit}] ${a.title}: ${a.change}`))
  .concat(((crossFix && crossFix.applied) || []).map(a => `- [cross] ${a.title}: ${a.change}`))
let docsFix = null
if (docsConfirmed.length || moreDocs.length || codeChanges.length) {
  docsFix = await agent(
    `${COMMON}\n\nTASK: bring the docs in line. You may edit .md files and notebooks (use NotebookEdit for .ipynb; never put credentials in a notebook). Do not edit code. Apply (A) every verified docs finding, (B) every doc change requested by code fixers, and (C) check that the code changes made in this round (list below) are reflected wherever docs describe that code (AGENTS.md repo map and rules, README.md, hal/README.md, docs/architecture.md, docs/design/ui-spec.md, design-system.md). The ui-spec is the behaviour source of truth: if a code change altered behaviour without a spec change, update the spec to match ONLY if the change was a verified bug fix; otherwise list it under skipped. Plain language; keep each doc's existing style; delete repetition rather than add text.\n\n(A) VERIFIED DOCS FINDINGS:\n${docsConfirmed.map(fmtConfirmed).join('\n') || '(none)'}\n\n(B) REQUESTED DOC CHANGES:\n${moreDocs.map(o => `- [${o.from}] ${o.file}: ${o.change} (why: ${o.reason})`).join('\n') || '(none)'}\n\n(C) CODE CHANGES THIS ROUND:\n${codeChanges.join('\n') || '(none)'}`,
    { label: 'fix:docs', phase: 'Docs', schema: FIX_RESULT })
}

phase('Integrate')
const integ = await agent(
  `${COMMON}\n\nTASK: integration check after this review round's fixes (other agents are done; you may edit any code, test or doc file to fix regressions, but make minimal changes and never weaken a test just to pass it). Run, in order, and read the result lines: (1) python3 tests/runner.py; (2) node tools/mpy/run.mjs tests/runner.py; (3) python3 tools/gen_tuning.py --check; (4) python3 tools/render_snapshots.py (then git diff --stat docs/design/snapshots to see which PNGs changed; for changed ones, open the PNG with the Read tool and say whether the visual change is intended); (5) python3 tools/build_sim.py; (6) python3 tools/bakeoff.py --quick (only to confirm it runs; note the kalman2 score). Fix any failure and rerun until green. Then read git diff HEAD --stat and skim the diff for obvious breakage: leftover debug prints, unused imports introduced, half-applied renames, docs referring to removed names (grep for every removed public name). Report everything.\n\nThis round's code changes:\n${codeChanges.join('\n') || '(none)'}`,
  { label: 'integrate', phase: 'Integrate', schema: INTEGRATE })

const allFindings = units.flatMap(u => u.all).concat(crossFindings, docsFindings)
const summary = {
  round: ROUND,
  counts: {
    found: allFindings.length,
    confirmed: allFindings.filter(f => f.status === 'confirmed').length,
    rejected: allFindings.filter(f => f.status === 'rejected').length,
    bySeverity: ['critical', 'high', 'medium', 'low'].map(s => `${s}:${allFindings.filter(f => f.status === 'confirmed' && f.severity === s).length}`).join(' '),
  },
  confirmed: allFindings.filter(f => f.status === 'confirmed').map(f => `${f.file}:${f.line} [${f.severity}/${f.category}/${f.source}:${f.lens}] ${f.title}`),
  rejected: allFindings.filter(f => f.status === 'rejected').map(f => `${f.file}:${f.line} [${f.category}] ${f.title} -- ${String(f.why).slice(0, 160)}`),
  fixes: units.map(u => ({ unit: u.unit, applied: u.fix ? u.fix.applied.map(a => a.title) : [], skipped: u.fix ? u.fix.skipped : [], tests: u.fix ? u.fix.tests : '' })),
  crossFix: crossFix ? { applied: crossFix.applied.map(a => a.title), skipped: crossFix.skipped, tests: crossFix.tests } : null,
  docsFix: docsFix ? { applied: docsFix.applied.map(a => a.title), skipped: docsFix.skipped } : null,
  integrate: integ,
}
return summary
