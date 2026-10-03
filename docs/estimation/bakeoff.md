# Range-estimator bake-off

Terms used here (RSSI, dBm, σ, p₁ₘ, path-loss exponent n, multipath, EMA,
Kalman filter and others) are explained in the
[ui-spec glossary](../design/ui-spec.md#13-glossary). This doc also uses:
**AR(1)**, a random value that drifts, each step keeping most of the
last one; **log-normal**, noise that is normal in dB; **N(0, 1 dB)**, normal
noise with mean 0 and sd 1 dB; **s.e.**, the standard error of a mean; **p99**,
the value 99 % of samples stay under; **AGC**, the radio's automatic gain
control; **shadowing**, slow signal loss from bodies, walls or terrain between
the watches that changes as you move; **fast fading**, packet-to-packet jumps
from multipath.

**Question:** which RSSI + IMU range estimator should the game use by default?

The candidates (`finder/estimators/`):

| estimator | what it does |
|---|---|
| `ema` | A running average (EMA) of the signal level; the baseline |
| `median_ema` | The median of the last few readings (drops spikes), then a running average that reacts faster while walking |
| `kalman1d` | A Kalman filter on the signal level |
| `kalman2` | The same, also tracking how fast the signal changes, with that rate capped by both step counters |
| `particle` | A particle filter: many weighted guesses of distance and walking direction, re-weighted on each packet and resampled |

**Answer:** `kalman2` (`finder/estimators/__init__.py`: `DEFAULT = "kalman2"`,
`make()` builds it). Scored the way the game runs it (the tokens path-loss
exponent per environment, the game's own zones and trend gate), it ties with
`particle` on overall score: it leads on the held-out seeds 100–129 (+0.012 ±
0.005 per run), and `particle` leads on the tuning seeds 0–9 and on a second
unseen block, 200–229 (−0.002 ± 0.005). It is the default because it has the
best overall score of the cheap estimators (+0.007 ± 0.002 per run over
`median_ema`, +0.006 ± 0.003 over `kalman1d` on 100–129), lower distance error
than `particle` on every block (0.506 vs 0.609 on 100–129, 0.512 vs 0.585 on
200–229; `median_ema` and `kalman1d` are about as good on distance), is expected
to fit the 2 ms budget (0.3–1.0 ms extrapolated, §2 and §4), allocates 11× less
per packet than `particle`, and has no internal randomness. `particle` still
follows trends best (higher trend accuracy, shorter reversal lag) at about 5–6×
the cost; see [When to switch to `particle`](#when-to-switch-to-particle).

Reproduce:

```
python3 tools/bakeoff.py --est ema,median_ema,kalman1d,kalman2,particle --seeds 100-129
python3 tools/bakeoff.py --est ... --seeds 100-129 --imu drifty      # or ideal
python3 tools/bakeoff.py --est ... --seeds 200-229                   # second unseen block
node tools/mpy/run.mjs tools/bakeoff.py --quick --est particle,kalman2,ema
```

The full held-out run takes about 40 s on CPython, and nothing was cut from it.

## 1. Method

### World model (`sim/world.py`, `sim/radio.py`, `sim/imu.py`)

* **Walkers:** two walkers on a 2D plane, stepped every 50 ms. Each watch sends a
  beacon at 10 Hz with ±10 ms jitter. A beacon carries the sender's last RSSI of us
  (`peer_rssi`) and its motion hint.
* **Received signal:** `p0_link - 10 n log10(d)`, plus the following terms, then
  quantised to 1 dB:
  * shadowing, shared by both directions: AR(1) over distance walked (6 m) plus a
    slow drift over time (8 s)
  * asymmetric fast fading, with a wider tail downwards (deep fades)
  * body blocking for each wearer, from 0 dB with the partner in front to `body_db`
    with the partner behind
  * walls or obstacles
  * rare outliers
* **Packet loss:** packets under -96 dBm are lost, and so is a distance-dependent
  fraction.
* **Link offset:** `p0_link` is -45 dBm ± up to 3 dB from per-device offsets, plus
  ±1 dB between directions. The 1 m calibration an estimator receives (the
  PAIRING `STAND 1 STEP APART` mean) is the nominal value + N(0, 1 dB).
* **Motion hints:** they mimic the BMA423 step counter and activity register. Each
  person has their own stride and count bias, the scale drifts slowly, arm gestures
  add phantom steps, slow shuffles lose steps, turning on the spot counts as
  shuffling (6 steps per full turn, `sim/imu.py` `TURN_STEPS_PER_REV`), the
  activity flag lags by 2–3 s, and cadence is smoothed over 3 s. No
  accelerometer data is integrated anywhere; see [imu-drift.md](imu-drift.md)
  for why.

### Noise profiles

| profile | n | fast fading σ | shadowing σ | body dB | outliers | extra |
|---|---|---|---|---|---|---|
| clean | 2.0 | 2 | 2 | 5 | none | |
| typical | 2.2 | 4 | 4 | 9 | 1 % × 12 dB | |
| harsh | 2.8 | 6 | 6 | 13 | 5 % × 15 dB | +20 % packet loss |
| indoor | 3.0 | 5 | 5 | 10 | 2 % × 12 dB | 2 dB wall every 10 m |

### Scenarios (`sim/scenarios.py`), from watch A's point of view

| scenario | what happens | s |
|---|---|---|
| approach | A walks 40 m to a still B | 36 |
| both_approach | both walk towards each other from 40 m | 24 |
| stationary | both stand 15 m apart | 60 |
| walk_away_back | A walks 25 m away, then turns and comes back (`turn_t` gives the reversal lag) | 50 |
| orbit | A circles B at 20 m, so the distance stays constant while A walks | 60 |
| rotate_in_place | A turns slowly on the spot at 10 m (body shadow sweeps) | 60 |
| zigzag_search | noisy hot/cold search from 50 m, with wrong turns and pauses | 100 |
| nlos_wall | detour behind a 12 dB wall, then approach | 38 |
| far_edge | A from 95 m out to 110 m, then back to 80 m, near the sensitivity floor | 45 |
| pause_and_go | 10 s walking, 10 s standing, from 45 m | 72 |

### Metrics (`tools/bakeoff.py`), taken after a 3 s warm-up

Each run is simulated once and the same trace is fed to every estimator, set up
as `Game` sets it up: the tokens path-loss exponent for the profile's
environment (`calibrate.n` 2.6; `n_indoor` 3.0 for `harsh` and `indoor`, the
profiles where the web simulator switches the watches to indoor), then the 1 m
calibration. Each received packet is one `update`, and each 50 ms tick with no
packet is one `update(t, None, ...)` (the game itself sends no idle ticks). Each
tick also feeds the game's `finder.proximity.Proximity` (zones with hysteresis
and dwell, the gated trend), with a delivery meter at the trace's beacon rate.

* `dist_log_rmse`: RMS of `ln(est/true)` distance. 0.5 is roughly a ×1.65 error.
* `dist_cov`: fraction of ticks that have an estimate.
* `trend_acc`: fraction of the ticks where the distance changes by more than
  0.3 m/s on which the estimator's raw trend has the right sign. Showing 0 counts
  as wrong.
* `trend_cov`: fraction of those same ticks where the trend is not 0.
* `false_trend`: fraction of the ticks where the distance changes by less than
  0.1 m/s on which the trend is not 0.
* `false_verdict`, `gated_acc`, `gated_cov`: the same three for the game's gated
  trend, the one the player sees (ui-spec §5.5). They are not in the score. The
  report also checks the ui-spec §5.5 target, `false_verdict` < 0.05 on `orbit`
  under `typical` and `harsh`.
* `reversal_lag_s`: seconds from the turn in `walk_away_back` until the trend has
  shown "warmer" for 1 s in a row. It is not part of the score.
* `rev_miss`: share of `walk_away_back` runs where "warmer" never held for 1 s
  after the turn (averaged over those runs only).
* `zone_flips/min`: the game's zone changes (`ZoneTracker`: enter 28/14/7 m,
  exit 36/18/9 m, dwell 3000/2000/1500 ms) beyond those of the true distance
  through the same zone rules.
* `us/update`: mean over every call. `us/pkt` averages packet updates only, which
  is the real per-beacon cost.
* `runs`: traces scored.
* **score:** `0.35(1-min(1,rmse)) + 0.35 trend_acc + 0.15(1-false_trend) + 0.15(1-min(1,flips/10))`.

Tables in §5 were measured before this setup existed: each estimator used its
own exponent, and `zone_flips` counted fixed 3/8/20/45 m zones without
hysteresis. Their numbers are not comparable with §2 and §3 (see §7).

### Held-out protocol

The candidates were tuned on seeds 0–9. The numbers below use **seeds 100–129**,
which were never used for tuning. That is 30 seeds × 4 profiles × 10 scenarios =
1200 runs per estimator, with the `typical` IMU model unless stated otherwise.

## 2. Results (held-out, seeds 100–129)

| estimator | score | dist_log_rmse | dist_cov | trend_acc | trend_cov | false_trend | false_verdict | gated_acc | gated_cov | reversal_lag_s | zone_flips/min | us/update | us/pkt |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| kalman2 | **0.660** | **0.506** | 0.92 | 0.643 | 0.78 | 0.208 | 0.042 | **0.175** | **0.18** | 3.74 | 0.46 | 1.1 | 2.3 |
| particle | 0.653 | 0.609 | 0.92 | **0.729** | **0.86** | 0.222 | **0.010** | 0.090 | 0.09 | **2.13** | 0.39 | 4.8 | 13.7 |
| kalman1d | 0.649 | 0.521 | 0.92 | 0.621 | 0.78 | 0.210 | 0.022 | 0.097 | 0.10 | 4.81 | **0.32** | 1.8 | 3.3 |
| median_ema | 0.643 | **0.506** | 0.92 | 0.572 | 0.73 | **0.164** | 0.032 | 0.160 | 0.16 | 3.55 | 0.37 | 1.8 | 4.3 |
| ema (baseline) | 0.519 | 0.533 | 0.92 | 0.526 | 0.74 | 0.807 | 0.031 | 0.090 | 0.09 | 3.66 | 0.50 | 0.5 | 1.0 |

Timings are CPython on the dev Mac. `rev_miss` is 0 for every estimator.

**Other seed blocks:** on the tuning seeds (0–9) the scores are particle
0.677, kalman1d 0.655, kalman2 0.648, median_ema 0.643 and ema 0.510. On a
second unseen block, 200–229, the order is particle 0.655, kalman2 0.647,
kalman1d 0.645, median_ema 0.633 (ema 0.517). So the order of the top two flips
between seed blocks and is within noise.

**Are the gaps real?** Paired per-run score differences, over the same 1200 traces:

| comparison | mean Δ | s.e. | runs won |
|---|---|---|---|
| kalman2 − particle | +0.012 | 0.005 | 668/1200 |
| kalman2 − kalman1d | +0.006 | 0.003 | 590/1200 |
| kalman2 − median_ema | +0.007 | 0.002 | 695/1200 |
| particle − kalman1d | −0.005 | 0.004 | 566/1200 |
| kalman2 − particle (seeds 200–229) | −0.002 | 0.005 | 595/1200 |

The per-seed composite scores have a spread of about 0.03–0.04 (SD) for every
estimator.

**Trend precision:** `trend_acc / trend_cov` measures how often a shown trend is
right: particle 0.849, kalman2 0.820, kalman1d 0.794, median_ema 0.788, ema 0.711.

**The gated trend** (what the player sees) is much more careful than the raw
one: false verdicts drop to 1–4 %, but a trend shows on only 9–18 % of the
moving ticks. On `orbit` it still misses the ui-spec §5.5 target under
`typical` for every estimator (`kalman2` 0.282); see ui-spec §5.5.

**Reversal lag in `walk_away_back`** (120 runs), in seconds:

| estimator | median | p90 | max |
|---|---|---|---|
| particle | 0.88 | 6.65 | 14.3 |
| median_ema | 2.00 | 8.95 | 21.0 |
| kalman2 | 2.03 | 8.85 | 15.6 |
| kalman1d | 2.75 | 11.75 | 21.6 |

**False trends in truly static scenes:**

| scene | particle | kalman2 | kalman1d | median_ema |
|---|---|---|---|---|
| stationary | 0.046 (max 0.18) | 0.020 (max 0.08) | 0.018 | 0.014 |
| rotate_in_place | 0.028 | 0.013 | 0.014 | 0.009 |

`orbit` is excluded because A keeps walking there. Every estimator shows a trend in
`orbit` (0.80–0.99), because a few seconds of RSSI can't tell sideways walking from
walking towards or away. Without `orbit`, false_trend is particle 0.137, kalman2
0.135, kalman1d 0.129, median_ema 0.094.

### IMU error model (held-out)

Each cell is the score, with false_trend in brackets.

| imu | ema | median_ema | kalman1d | kalman2 | particle |
|---|---|---|---|---|---|
| ideal | 0.519 (0.81) | 0.644 (0.15) | 0.653 (0.18) | 0.662 (0.18) | 0.659 (0.19) |
| typical | 0.519 (0.81) | 0.643 (0.16) | 0.649 (0.21) | 0.660 (0.21) | 0.653 (0.22) |
| drifty | 0.519 (0.81) | 0.635 (0.22) | 0.633 (0.31) | 0.648 (0.29) | 0.635 (0.33) |

No estimator integrates steps into a position, so drift in stride or step count
costs at most 0.02. Phantom-step bursts in `drifty` raise false trends everywhere,
and `particle` is hit hardest.

### `kalman2` and `particle` by scenario and profile (held-out)

Each cell is score / dist_log_rmse / trend_acc / false_trend.

| est | approach | both_approach | stationary | walk_away_back | orbit | rotate_in_place | zigzag_search | nlos_wall | far_edge | pause_and_go |
|---|---|---|---|---|---|---|---|---|---|---|
| kalman2 | 0.70 / 0.54 / 0.76 / 0.19 | 0.77 / 0.44 / 0.85 / 0.17 | 0.72 / 0.49 / - / 0.02 | 0.71 / 0.50 / 0.77 / 0.17 | 0.52 / 0.43 / - / 0.87 | 0.72 / 0.47 / - / 0.01 | 0.66 / 0.47 / 0.61 / 0.21 | 0.61 / 0.66 / 0.64 / 0.23 | 0.54 / 0.58 / 0.31 / 0.09 | 0.65 / 0.51 / 0.55 / 0.12 |
| particle | 0.70 / 0.62 / 0.84 / 0.16 | 0.70 / 0.68 / 0.88 / 0.15 | 0.74 / 0.45 / - / 0.05 | 0.70 / 0.55 / 0.76 / 0.16 | 0.41 / 0.58 / - / 0.99 | 0.75 / 0.43 / - / 0.03 | 0.64 / 0.64 / 0.71 / 0.24 | 0.75 / 0.43 / 0.79 / 0.20 | 0.40 / 1.33 / 0.38 / 0.10 | 0.67 / 0.64 / 0.76 / 0.13 |

| est | clean | typical | harsh | indoor |
|---|---|---|---|---|
| kalman2 | 0.77 / 0.39 / 0.85 / 0.24 | 0.68 / 0.50 / 0.73 / 0.23 | 0.61 / 0.55 / 0.53 / 0.20 | 0.57 / 0.60 / 0.46 / 0.17 |
| particle | 0.70 / 0.62 / 0.87 / 0.23 | 0.64 / 0.70 / 0.79 / 0.23 | 0.60 / 0.68 / 0.65 / 0.22 | 0.68 / 0.42 / 0.61 / 0.20 |

`kalman2` is ahead on `clean` and `typical`, the two are tied on `harsh`, and
`particle` is well ahead `indoor` (+0.11), mostly on distance error.

### Cost on MicroPython

`node tools/mpy/run.mjs tools/bakeoff.py --quick --est particle,kalman2,ema` runs on
MicroPython 1.29 (WebAssembly). The metric columns match CPython exactly: kalman2
0.772, particle 0.735, ema 0.601. Only the timings differ:

| estimator | us/update (WASM) | us/pkt (WASM) | ×ema per packet | us/pkt (CPython) |
|---|---|---|---|---|
| particle (n=24) | 77.0 | 162.7 | 15× | 13.7 |
| kalman2 | 15.9 | 30.4 | 2.9× | 2.5 |
| ema | 5.2 | 10.6 | 1× | 1.3 |

Under WASM, `ticks_us` only has 1 ms resolution, so only these means over thousands
of calls are meaningful. `tools/bench_est.py` instead replays the 575 packets of one
`zigzag_search` trace back to back and times the whole block, so the 1 ms resolution
doesn't matter, and on MicroPython counts heap bytes per packet with GC off. It runs
on CPython, the WASM port and a watch (§3), and is tested in `tests/test_sim.py`:

| estimator | us/pkt (WASM) | bytes/pkt (WASM) | us/pkt (CPython) |
|---|---|---|---|
| particle (n=24) | 180 | 19 980 | 14.2 |
| particle (n=12) | 103 | 10 821 | 8.3 |
| median_ema | 45 | 1 578 | 4.0 |
| kalman1d | 42 | 2 393 | 3.4 |
| kalman2 | 33 | 1 720 | 2.1 |
| ema | 10 | 620 | 0.9 |

Every float result in MicroPython is a 16-byte heap block (measured), so `particle`
makes about 1 250 short-lived objects per packet and `kalman2` about 110; neither is
allocation-free. The ESP32 (240 MHz, boxed single-precision floats) has not been
measured. Assuming it is 10–30× slower than WASM on the dev Mac, `kalman2` takes
about 0.3–1.0 ms per packet and `particle` about 1.8–5.4 ms, before GC pauses, which
come about 11× more often with `particle`.

Scores for `particle` with fewer particles (held-out; a one-off run of `bakeoff.run`
with the estimator built by `make("particle", n=N)`, since `tools/bakeoff.py` has no
particle-count option):

| particles | score | CPython us/pkt |
|---|---|---|
| 12 | 0.644 | 8.1 |
| 16 | 0.652 | 9.8 |
| 24 (default) | 0.653 | 13.3 |
| 32 | 0.655 | 16.9 |

No particle count reaches `kalman2` (0.660).

## 3. Decision: `DEFAULT = "kalman2"`

| criterion | particle | kalman2 | verdict |
|---|---|---|---|
| overall score (seeds 100–129 / 200–229) | 0.653 / **0.655** | **0.660** / 0.647 | tied: +0.012 ± 0.005 and −0.002 ± 0.005 per run |
| distance error (seeds 100–129 / 200–229) | 0.609 / 0.585 | **0.506 / 0.512** | kalman2, clearly |
| trend accuracy / precision | **0.729 / 0.85** | 0.643 / 0.82 | particle |
| false trend, both still | 0.046 | **0.020** | kalman2 (2.3× fewer) |
| false trend, drifty IMU | 0.33 | **0.29** | kalman2 |
| reversal lag, median | **0.9 s** | 2.0 s | particle |
| harsh / indoor | 0.60 / **0.68** | **0.61** / 0.57 | tied on harsh, particle indoor |
| cost per packet (WASM, block-timed) | 180 µs | **33 µs** | kalman2, 5.5× cheaper |
| fits the 2 ms ESP32 budget | unmeasured, probably borderline or over | expected (0.3–1.0 ms extrapolated, §2) | kalman2 |
| allocation / GC (MicroPython, measured) | ~20 KB (~1250 heap blocks) per packet; 7 lists of n plus a noise table | ~1.7 KB (~110 blocks) per packet, no lists | kalman2, 11× less |
| determinism / simplicity | internal PRNG, 468 lines | no randomness, 200 lines | kalman2 |

Scored the way the game runs, `kalman2` ties with `particle` on overall score,
has lower distance error than `particle`, and is the one expected to fit the
per-update budget, which is a hard requirement: the game shares the CPU with the
display, the radio and the touch loop. `particle` is still the better trend
follower (faster reversals, more trends shown and right) and the better indoor
estimator, but it costs about 5–6× more and has only been shown to fit the
budget by extrapolation from a Mac. `ema` must not ship: its raw trend is wrong
81 % of the time while nobody moves.

### When to switch to `particle`

1. On a real T-Watch, time `make("particle").update` per packet over a few minutes
   of beaconing, including GC: `gc.collect()` once, then time with `ticks_us`.
   For a first number, copy `finder/`, `sim/`, `tools/bakeoff.py`,
   `tools/cli.py` and `tools/bench_est.py` to the watch, keeping the `tools/`
   folder, and run `from tools import bench_est; bench_est.main(["--est",
   "kalman2,particle"])` (mean per packet and bytes per packet, not p99).
2. Switch only if real-watch data (§4) puts `particle` ahead, or field tests show
   that faster trends matter more than distance error, **and** its p99 is under
   about 1.5 ms (leaving headroom for UI and radio). `make("particle", n=12)` is
   cheaper but scores lower (0.644).
3. Before switching, re-run this bake-off with the recalibrated profile from §4.

## 4. Known limitations

* **The simulator is not reality.** Every number here comes from the radio model
  above. Real ESP32 RSSI has antenna patterns that depend on wrist pose, multipath
  that doesn't follow a log-normal distribution, Wi-Fi co-channel interference on
  the ESP-NOW channel, and AGC steps. None of these are modelled.
  The ranking is more trustworthy than the absolute scores.
* **Distance bias is tuned to the sim.** Each candidate folds a fixed or adaptive
  body/fading loss into its path loss, on top of the tokens exponent (2.6
  outdoors, 3.0 indoors). If the real `n` or body loss differ, distances will be
  biased in the same direction in every scene.
* **far_edge (80–110 m)** is poor for everyone: log error 0.6–1.3. Under `indoor`
  no packets arrive at all, and only `dist_cov` shows that.
* **Metric quirks:**
  * showing no trend while moving counts as wrong, the same as a wrong trend, so
    always guessing a direction pays off
  * a frozen display never flips zones
  * reversal lag is reported but not scored
  * `orbit` can't be solved from RSSI and step counts alone
* **`peer_rssi` has no freshness flag.** A peer that stops hearing us keeps
  re-sending its last value. `kalman1d` and `particle` give it a larger noise,
  `median_ema` folds it into its running median, and `kalman2` uses it only after
  averaging its offset over 20 reports (2 s). Repeats still count as new
  measurements, so the protocol should add an age or sequence field.
* **Timing:** ESP32 cost is extrapolated, as described above. The single-precision
  float behaviour of the ESP32 port is untested, because the WASM port uses doubles.

### What to measure on real watches to recalibrate

Use two watches, log `(t_ms, rssi, peer_rssi, steps, activity)` on both at 10 Hz,
and record the true distance with a tape or GPS on a field.

1. **`p0` and `n`:** hold still at 1, 2, 4, 8, 16, 32 and 64 m, 30 s per point, in
   both an open field and a hallway. Fit the median RSSI against `log10(d)` to get
   the slope `n` and the intercept `p0` per environment. Check how well the 1 m
   calibration predicts `p0` (`CAL_SIGMA`).
2. **Fading σ and tail:** at a fixed distance, the spread of RSSI from packet to
   packet while still gives `sigma_ff` and how skewed the fades are (`FF_TAIL`).
   Walking slowly past a fixed point gives the shadowing σ and its decorrelation
   distance.
3. **Body loss:** at 10 m, have the wearer turn slowly on the spot. The RSSI peak
   minus the trough gives `body_db`, per wearer and per wrist. This is the same test
   as the R0 bench study in `docs/research/user-research.md`.
4. **Asymmetry and device offsets:** swap the watches and compare `rssi` with
   `peer_rssi` at the same spot (`DEV_OFF`, `LINK_ASYM`).
5. **Packet loss vs RSSI** near the floor, which sets `FLOOR` and the loss model.
6. **IMU:** compare the chip step count with counted steps over 100 m, and the delay
   of the activity flag (`stride_err`, `count_err`, `lat_lo/hi`). Count phantom
   steps while gesturing (`phantom_hz`).
7. Put these values into `sim/radio.py` `PROFILES` or `sim/imu.py`, re-run the
   bake-off, and only then re-tune the tokens exponents (`calibrate.n`,
   `n_indoor`), then `BIAS_*` (kalman2) or `P0_ADJ` (particle).

See also [imu-drift.md](imu-drift.md) for why the IMU is used only as a motion hint
and never integrated into a position.

## 5. Earlier audit (old harness, superseded by §2, §3 and §7)

This audit ran on the earlier harness (each estimator's own exponent, fixed
3/8/20/45 m zones), so its numbers are not comparable with §2 and §3.

An independent pass tried to refute the result of that time: it re-read
`tools/bakeoff.py`, `sim/*.py` and every estimator, re-ran the held-out
comparison and ran the stress tests below. On that harness, `kalman2` stayed the
default and `particle` scored higher.

### Checked and found sound

* **Trend sign.** The sim's `radial_speed` is d(distance)/dt, so the metric expects
  +1 when it is below -0.3 m/s. `kalman2` (+rate = rising RSSI), `particle`
  (P(closer) - P(farther)) and `kalman1d` all use +1 = closer. `reversal_lag_s`
  waits for +1 after the turn back, which is also right.
* **No ground-truth leakage.** Nothing under `finder/` imports `sim`. Estimators
  get RSSI, `peer_rssi` (the partner's previous measurement of us), the 1 m
  calibration, and `MotionInfo` (activity, cadence, step count), which the sim
  derives from simulated step counts with per-person bias, drift, phantom steps and
  latency, never from velocity or heading. True distance and radial speed only
  reach the scorer. The motion hints are sampled at the end of each 50 ms tick, so
  they can be up to 50 ms ahead of a packet, which doesn't matter.
* **No metric gaming.** Showing 0 while moving counts as wrong, and showing a trend
  while the distance is constant counts as false. A wrong arrow and no arrow score
  the same, so the audit also counted wrong arrows while moving: particle 0.116,
  kalman2 0.150. `particle`'s lead was not guesswork: its precision was higher too.
* **Held-out result reproduced** exactly (seeds 100–129, 1200 runs). §2 has the
  current numbers for three seed blocks.
* **Tick wrap** (`--wrap`, seeds 100–109) gives exactly the same numbers as no wrap.
* **MicroPython:** `--quick` scores are identical to CPython's, and both test suites
  pass.
* **Division by zero / None:** every divisor is floored (`p11 >= 1e-6`, distances
  >= `D_MIN`, standard error when `sw < 5`), and runs without packets
  (`far_edge`/`indoor`) give `None` distance error, which `mean_metrics` skips for
  every estimator alike (`dist_cov` 0.92 for all).

### Fixed

1. **`kalman2` kept a stale arrow between packets.** It cleared the trend when both
   watches were still only on packet updates, so the watch could keep showing
   "warmer" or "colder" after both wearers stopped, until the next packet arrived.
   Now a no-packet update with speed 0 clears the trend as well, like `particle`
   and `kalman1d` do (`test_stop_clears_trend_without_packets`).
2. **The claim that `kalman2` "allocates nothing" was wrong.** In MicroPython every
   float result is a 16-byte heap block; §2 has the measured bytes per packet.
3. **New `tools/bench_est.py`** (§2, Cost on MicroPython).

### Stress tests (old harness, not re-run; seeds 100–114, all scenarios and profiles; score)

| variant | particle | kalman2 | kalman1d | median_ema |
|---|---|---|---|---|
| as simulated | 0.668 | 0.646 | 0.630 | 0.621 |
| body blocking × 1.5 | 0.656 | 0.625 | 0.615 | 0.612 |
| body blocking × 0.5 | 0.654 | 0.644 | 0.623 | 0.611 |
| no body blocking | 0.610 | **0.614** | 0.592 | 0.572 |
| motion hints 1 s late | 0.652 | 0.632 | 0.608 | 0.607 |
| motion hints 2 s late | 0.634 | 0.614 | 0.589 | 0.588 |
| 1 m calibration sees the real link offset (seeds 100–129) | 0.667 | 0.637 | | |
| 20 Hz beacons (seeds 100–109) | 0.672 | 0.644 | 0.628 | 0.628 |

* On the old harness, `particle`'s lead depended on the sim's **body-blocking
  model**. It explicitly models "walking away puts my body in the way". With half
  the body loss the lead shrank to 0.010, and with none `kalman2` was marginally
  ahead. Real body loss is unmeasured (§4, item 3). This is one more reason not to
  switch on simulation alone.
* A late step counter (BMA423 polling or step-detection delay, not modelled) raised
  false trends for everyone (kalman2 0.18 → 0.33 at 2 s) but didn't change the
  order.
* The 1 m calibration is the nominal value plus noise, not the real per-device
  link offset. If calibration captured the offset, every estimator would score a
  little higher and the order would stay the same.


## 6. Update: path-loss exponent (after testing in the web simulator)

Running the full game in the web simulator showed a problem the bake-off's averaged score hid. `kalman2` used a
single effective exponent `n = 3.0`, tuned over all four noise profiles at once. In the outdoor profiles (true n 2.0
and 2.2) that compresses every distance: at a true 36 m the watches read 9–17 m and entered WARM. One exponent cannot fit
open fields and indoor spaces, so the exponent is now an environment setting: `tokens.json` `thresholds.calibrate.n`
(outdoor, default 2.6) and `n_indoor` (3.0), exposed as `Game.set_place(indoor)`. The simulator switches the watches to
indoor for the `harsh` and `indoor` radio profiles. On the watch, players switch it with the MENU row `PLACE: OUT/IN` (ui-spec MENU).

Mean over 6 scenarios x 6 held-out seeds (100–105) per profile, `kalman2` with its fading-bias term. `bias` is the mean
of ln(estimate / truth): negative means the watch says closer than it is. `zone` is the share of samples whose zone
(far/near/warm/hot) matches the true zone.

| profile | n 3.0 (old) | n 2.6 (new outdoor) | n 2.2, no bias term |
|---|---|---|---|
| clean | rmse 0.54, bias −0.39, zone 49 % | rmse 0.34, bias −0.05, zone 64 % | rmse 0.33, bias +0.07, zone 68 % |
| typical | rmse 0.53, bias −0.26, zone 51 % | rmse 0.48, bias +0.11, zone 58 % | rmse 0.67, bias +0.51, zone 46 % |
| harsh | rmse 0.50, bias +0.17, zone 57 % | rmse 0.76, bias +0.61, zone 37 % | rmse 1.26, bias +1.19, zone 13 % |
| indoor | rmse 0.59, bias +0.49, zone 49 % | rmse 1.03, bias +0.97, zone 15 % | rmse 1.55, bias +1.50, zone 5 % |

So: 2.6 outdoors, 3.0 indoors/crowded (the old value, best there). Recalibrate both values from real walks (log RSSI
against measured distance) once the watches run.

## 7. Update: the bake-off scores what the game runs

With 2.6 used everywhere the old harness scored `kalman2` at 0.600, last of the four candidates, because it had no
environment setting and used the outdoor exponent on the indoor profiles too. `tools/bakeoff.py` now sets each
estimator up as `Game` does (the tokens exponent per environment, `n_indoor` for `harsh` and `indoor`), counts zone
flips through the game's own `ZoneTracker`, and scores the game's gated trend (`false_verdict`, `gated_acc`,
`gated_cov`). The estimators changed too: `kalman2` holds a step for 1300 ms (the BMA423 counter is polled once a
second, so 1000 ms read as stopping between counts), counts unknown activity without new steps as still, and lost its
display deadband; every estimator takes its default p₁ₘ from tokens; `ema` and `kalman2` also take the outdoor
exponent (`ema` went from 2.2 to 2.6), while `kalman1d`, `median_ema` and `particle` keep their own `N_PL`, which
`Game` and the bake-off replace via `set_exponent`; and every estimator publishes `noise_db` for the unreliable gate
(since then learnt the same way in every estimator by `RangeEstimator._note_noise`, so the gated columns compare the
same gate; `kalman2` also takes its measurement noise from it). §2 and §3 were re-measured with all of this: `kalman2` leads on the held-out seeds 100–129 (0.660,
`particle` 0.653) and ties with `particle` across seed blocks.
