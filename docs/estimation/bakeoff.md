# Range-estimator bake-off

**Question:** which RSSI + IMU range estimator should the game use by default?

**Answer:** `kalman2` (`finder/estimators/__init__.py`: `DEFAULT = "kalman2"`,
`make()` builds it). `particle` scores higher, but it costs about 5x more per packet.
On the ESP32 that probably puts it at or over the 2 ms budget, and nobody has measured
it on a watch yet. `kalman2` is second on held-out seeds, clearly ahead of the other
two, fits the budget with a wide margin, allocates 11× less per packet, and has no
internal randomness. Once `particle` is timed on real hardware, switching to it is a
one-line change (see [When to switch to `particle`](#when-to-switch-to-particle)).

Reproduce:

```
python3 tools/bakeoff.py --est ema,median_ema,kalman1d,kalman2,particle --seeds 100-129
python3 tools/bakeoff.py --est ... --seeds 100-129 --imu drifty      # or ideal
node tools/mpy/run.mjs tools/bakeoff.py --quick --est particle,kalman2,ema
```

The full held-out run takes about 30 s on CPython, and nothing was cut from it.

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
  ±1 dB between directions. The bump calibration an estimator receives is the
  nominal value + N(0, 1 dB).
* **Motion hints:** they mimic the BMA423 step counter and activity register. Each
  person has their own stride and count bias, the scale drifts slowly, arm gestures
  add phantom steps, slow shuffles lose steps, the activity flag lags by 2–3 s, and
  cadence is smoothed over 3 s. No accelerometer data is integrated anywhere; see
  [imu-drift.md](imu-drift.md) for why.

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
| far_edge | A at 95–110 m, near the sensitivity floor | 45 |
| pause_and_go | 10 s walking, 10 s standing, from 45 m | 72 |

### Metrics (`tools/bakeoff.py`), taken after a 3 s warm-up

Each run is simulated once and the same trace is fed to every estimator. Each
received packet is one `update`, and each 50 ms tick with no packet is one
`update(t, None, ...)`.

* `dist_log_rmse`: RMS of `ln(est/true)` distance. 0.5 is roughly a ×1.65 error.
* `dist_cov`: fraction of ticks that have an estimate.
* `trend_acc`: fraction of the ticks where the distance changes by more than
  0.3 m/s on which the trend has the right sign. Showing 0 counts as wrong.
* `trend_cov`: fraction of those same ticks where the trend is not 0.
* `false_trend`: fraction of the ticks where the distance changes by less than
  0.1 m/s on which the trend is not 0.
* `reversal_lag_s`: seconds from the turn in `walk_away_back` until the trend has
  shown "warmer" for 1 s in a row. It is not part of the score.
* `zone_flips/min`: changes of the distance zone (3/8/20/45 m) beyond the true
  changes.
* `us/update`: mean over every call. `us/pkt` is new in this evaluation and
  averages packet updates only, which is the real per-beacon cost.
* **score:** `0.35(1-min(1,rmse)) + 0.35 trend_acc + 0.15(1-false_trend) + 0.15(1-min(1,flips/10))`.

### Held-out protocol

The candidates were tuned on seeds 0–9. The numbers below use **seeds 100–129**,
which were never used for tuning. That is 30 seeds × 4 profiles × 10 scenarios =
1200 runs per estimator, with the `typical` IMU model unless stated otherwise.

### Harness fixes made before evaluating

* `tools/mpy/run.mjs` did not copy `hal/` into the WebAssembly filesystem, so all 55
  `test_hal_*` tests failed under MicroPython. Now `hal/` is copied and both runtimes
  pass the full suite.
* `tools/bakeoff.py` now also reports `us/pkt`. `us/update` includes the cheap
  no-packet ticks and understated the per-beacon cost by 2–3x.
* No estimator file was changed for the evaluation. The later audit (§5) fixed one
  `kalman2` bug; the tables in §2 and §3 include that fix.

## 2. Results (held-out, seeds 100–129)

| estimator | score | dist_log_rmse | dist_cov | trend_acc | trend_cov | false_trend | reversal_lag_s | zone_flips/min | us/update | us/pkt |
|---|---|---|---|---|---|---|---|---|---|---|
| particle | **0.662** | 0.579 | 0.92 | **0.743** | **0.86** | 0.222 | **2.59** | 0.84 | 4.6 | 13.4 |
| kalman2 | 0.635 | **0.558** | 0.92 | 0.635 | 0.78 | 0.180 | 3.50 | 1.01 | 0.9 | 2.2 |
| kalman1d | 0.620 | 0.600 | 0.92 | 0.620 | 0.78 | 0.210 | 4.82 | 0.36 | 1.7 | 3.2 |
| median_ema | 0.616 | 0.583 | 0.92 | 0.572 | 0.73 | **0.164** | 3.55 | **0.35** | 1.6 | 4.2 |
| ema (baseline) | 0.224 | 0.968 | 0.92 | 0.526 | 0.74 | 0.807 | 3.66 | 25.89 | 0.3 | 0.8 |

Timings are CPython on the dev Mac. `kalman2` numbers include the audit fix (§5); before it
the row read 0.634 / false_trend 0.187.

**Tuning seeds vs held-out seeds:** on the tuning seeds (0–9) the scores were
particle 0.678, kalman2 0.630, kalman1d 0.625, median_ema 0.620 and ema 0.202. The
ranking is unchanged on the held-out seeds, and only `particle` lost noticeably
(−0.016). None of the candidates had overfitted in a way that matters.

**Are the gaps real?** Paired per-run score differences, over the same 1200 traces:

| comparison | mean Δ | s.e. | runs won |
|---|---|---|---|
| particle − kalman2 | +0.016 | 0.003 | 644/1200 |
| particle − kalman1d | +0.028 | 0.003 | 695/1200 |
| kalman2 − kalman1d | +0.012 | 0.002 | 652/1200 |
| kalman2 − median_ema | +0.010 | 0.002 | 713/1200 |

The per-seed composite scores have a spread of about 0.028 (SD) for every
estimator.

**Trend precision:** `trend_acc / trend_cov` measures how often the arrow is right
when one is shown: particle 0.865, kalman2 0.809, kalman1d 0.793, median_ema 0.788,
ema 0.711.

**Reversal lag in `walk_away_back`** (120 runs), in seconds:

| estimator | median | p90 | max |
|---|---|---|---|
| particle | 0.98 | 8.7 | 15.9 |
| kalman2 | 1.83 | 9.35 | 15.6 |
| median_ema | 2.00 | 9.05 | 21.0 |
| kalman1d | 2.75 | 12.0 | 21.6 |

**False trends in truly static scenes:**

| scene | particle | kalman2 | kalman1d | median_ema |
|---|---|---|---|---|
| stationary | 0.046 (max 0.18) | 0.017 (max 0.08) | 0.018 | 0.014 |
| rotate_in_place | 0.027 | 0.011 | 0.014 | 0.009 |

`orbit` is excluded because A keeps walking there. Every estimator shows a trend in
`orbit` (0.80–0.99), because a few seconds of RSSI can't tell sideways walking from
walking towards or away. Without `orbit`, false_trend is particle 0.137, kalman2
0.104, kalman1d 0.129, median_ema 0.094.

### IMU error model (held-out)

Each cell is the score, with false_trend in brackets.

| imu | ema | median_ema | kalman1d | kalman2 | particle |
|---|---|---|---|---|---|
| ideal | 0.224 (0.81) | 0.618 (0.15) | 0.625 (0.18) | 0.637 (0.16) | 0.668 (0.18) |
| typical | 0.224 (0.81) | 0.616 (0.16) | 0.620 (0.21) | 0.635 (0.18) | 0.662 (0.22) |
| drifty | 0.224 (0.81) | 0.608 (0.22) | 0.605 (0.31) | 0.622 (0.26) | 0.646 (0.33) |

No estimator integrates steps into a position, so drift in stride or step count
costs at most 0.02. Phantom-step bursts in `drifty` raise false trends everywhere,
and `particle` is hit hardest.

### Top 2 by scenario (held-out)

| scenario | est | score | dist_log_rmse | trend_acc | trend_cov | false_trend | reversal_lag_s | zone_flips/min |
|---|---|---|---|---|---|---|---|---|
| approach | particle | 0.708 | 0.60 | 0.87 | 0.95 | 0.16 | - | 0.94 |
| approach | kalman2 | 0.662 | 0.61 | 0.74 | 0.92 | 0.14 | - | 0.97 |
| both_approach | particle | 0.702 | 0.62 | 0.89 | 0.99 | 0.15 | - | 1.43 |
| both_approach | kalman2 | 0.726 | 0.52 | 0.84 | 0.98 | 0.13 | - | 1.12 |
| stationary | particle | 0.691 | 0.53 | - | - | 0.05 | - | 0.69 |
| stationary | kalman2 | 0.708 | 0.50 | - | - | 0.02 | - | 0.71 |
| walk_away_back | particle | 0.704 | 0.51 | 0.76 | 0.99 | 0.16 | 2.59 | 0.57 |
| walk_away_back | kalman2 | 0.705 | 0.51 | 0.76 | 0.87 | 0.12 | 3.50 | 0.93 |
| orbit | particle | 0.523 | 0.46 | - | - | 0.99 | - | 0.00 |
| orbit | kalman2 | 0.541 | 0.48 | - | - | 0.87 | - | 0.00 |
| rotate_in_place | particle | 0.712 | 0.49 | - | - | 0.03 | - | 0.83 |
| rotate_in_place | kalman2 | 0.701 | 0.46 | - | - | 0.01 | - | 2.11 |
| zigzag_search | particle | 0.657 | 0.57 | 0.73 | 0.89 | 0.24 | - | 0.78 |
| zigzag_search | kalman2 | 0.626 | 0.55 | 0.61 | 0.81 | 0.19 | - | 1.10 |
| nlos_wall | particle | 0.681 | 0.55 | 0.79 | 0.83 | 0.20 | - | 1.51 |
| nlos_wall | kalman2 | 0.649 | 0.52 | 0.64 | 0.75 | 0.17 | - | 1.16 |
| far_edge | particle | 0.408 | 1.07 | 0.38 | 0.51 | 0.10 | - | 0.69 |
| far_edge | kalman2 | 0.377 | 0.99 | 0.29 | 0.44 | 0.06 | - | 1.20 |
| pause_and_go | particle | 0.686 | 0.58 | 0.78 | 0.85 | 0.13 | - | 0.92 |
| pause_and_go | kalman2 | 0.612 | 0.59 | 0.56 | 0.72 | 0.09 | - | 0.80 |

### Top 2 by profile (held-out)

| profile | est | score | dist_log_rmse | trend_acc | trend_cov | false_trend | reversal_lag_s | zone_flips/min |
|---|---|---|---|---|---|---|---|---|
| clean | particle | 0.723 | 0.55 | 0.88 | 0.99 | 0.23 | 1.06 | 0.53 |
| clean | kalman2 | 0.711 | 0.54 | 0.83 | 0.97 | 0.21 | 1.23 | 0.73 |
| typical | particle | 0.651 | 0.65 | 0.80 | 0.95 | 0.23 | 1.67 | 1.14 |
| typical | kalman2 | 0.655 | 0.55 | 0.70 | 0.91 | 0.20 | 1.76 | 1.29 |
| harsh | particle | 0.640 | 0.57 | 0.68 | 0.81 | 0.22 | 2.77 | 0.89 |
| harsh | kalman2 | 0.603 | 0.55 | 0.54 | 0.70 | 0.17 | 4.10 | 1.12 |
| indoor | particle | 0.634 | 0.53 | 0.61 | 0.69 | 0.20 | 4.87 | 0.79 |
| indoor | kalman2 | 0.568 | 0.60 | 0.46 | 0.55 | 0.14 | 6.91 | 0.89 |

`particle` gains most where the channel is rough (harsh +0.04, indoor +0.07). On
clean and typical channels the two are almost tied.

### Cost on MicroPython

`node tools/mpy/run.mjs tools/bakeoff.py --quick --est particle,kalman2,ema` runs on
MicroPython 1.29 (WebAssembly). The metric columns match CPython exactly: particle
0.759, kalman2 0.712, ema 0.362. Only the timings differ:

| estimator | us/update (WASM) | us/pkt (WASM) | ×ema per packet | us/pkt (CPython) |
|---|---|---|---|---|
| particle (n=24) | 85.0 | 181.9 | 18× | 13.4 |
| kalman2 | 17.9 | 33.6 | 3.4× | 2.2 |
| ema | 5.0 | 10.0 | 1× | 0.8 |

Under WASM, `ticks_us` only has 1 ms resolution, so only these means over thousands
of calls are meaningful. `tools/bench_est.py` (added in the audit, §5) instead replays
the 575 packets of one `zigzag_search` trace back to back, times the whole block, and
on MicroPython counts heap bytes per packet with GC off:

| estimator | us/pkt (WASM) | bytes/pkt (WASM) | us/pkt (CPython) |
|---|---|---|---|
| particle (n=24) | 174 | 19 882 | 12.2 |
| particle (n=12) | 97 | 10 723 | 8.1 |
| median_ema | 44 | 1 566 | 4.0 |
| kalman1d | 40 | 2 336 | 3.0 |
| kalman2 | 30 | 1 754 | 2.0 |
| ema | 9 | 562 | 0.7 |

Every float result in MicroPython is a 16-byte heap block (measured), so `particle`
makes about 1 240 short-lived objects per packet and `kalman2` about 110; neither is
allocation-free. The ESP32 (240 MHz, boxed single-precision floats) has not been
measured. Assuming it is 10–30× slower than WASM on the dev Mac, `kalman2` takes
about 0.3–0.9 ms per packet and `particle` about 1.7–5.2 ms, before GC pauses, which
come about 11× more often with `particle`.

Scores for `particle` with fewer particles (held-out):

| particles | score | CPython us/pkt |
|---|---|---|
| 12 | 0.645 | 9.6 |
| 16 | 0.657 | 11.6 |
| 24 (default) | 0.662 | 13.4 |
| 32 | 0.663 | 20.4 |

At 12 particles it still beats `kalman2` (0.645 vs 0.635), at about 3× the cost.

## 3. Decision: `DEFAULT = "kalman2"`

| criterion | particle | kalman2 | verdict |
|---|---|---|---|
| overall score (held-out) | 0.662 | 0.635 | particle, +0.016 ± 0.003 per run |
| distance error | 0.579 | **0.558** | kalman2, slightly |
| trend accuracy / precision | **0.743 / 0.87** | 0.635 / 0.81 | particle, clearly |
| false trend, both still | 0.046 | **0.017** | kalman2 (2.7× fewer) |
| false trend, drifty IMU | 0.33 | **0.26** | kalman2 |
| reversal lag, median | **1.0 s** | 1.8 s | particle |
| harsh / indoor | **0.64 / 0.63** | 0.60 / 0.57 | particle |
| cost per packet (WASM, block-timed) | 174 µs | **30 µs** | kalman2, 5.9× cheaper |
| fits the 2 ms ESP32 budget | unmeasured, probably borderline or over | yes, with a wide margin | kalman2 |
| allocation / GC (MicroPython, measured) | ~20 KB (~1240 heap blocks) per packet; 7 lists of n plus a noise table | ~1.7 KB (~110 blocks) per packet, no lists | kalman2, 11× less |
| determinism / simplicity | internal PRNG, 459 lines | no randomness, 209 lines | kalman2 |

`particle` is the better tracker in simulation, especially on rough channels. But
the per-update budget is a hard requirement, and `particle` has only been shown to
meet it by extrapolation from a Mac. The game also shares the CPU with the display,
the radio and the touch loop. `kalman2` is the best estimator that clearly fits:
second overall, significantly ahead of `kalman1d` and `median_ema`, lowest distance
error, and few false "warmer/colder" hints while nobody moves. `ema` must not ship:
it gives false trends 81 % of the time and flips zones 26 times a minute.

### When to switch to `particle`

1. On a real T-Watch, time `make("particle").update` per packet over a few minutes
   of beaconing, including GC: `gc.collect()` once, then time with `ticks_us`.
   For a first number, copy `finder/`, `sim/` and `tools/bench_est.py` to the watch
   and run `bench_est.main(["--est", "kalman2,particle"])` (mean per packet and
   bytes per packet, not p99).
2. If the p99 is under about 1.5 ms (leaving headroom for UI and radio), set
   `DEFAULT = "particle"`. If it is over, try `make("particle", n=12)`, which is
   still better than `kalman2` in simulation.
3. Before switching, re-run this bake-off with the recalibrated profile from §4.

## 4. Known limitations

* **The simulator is not reality.** Every number here comes from the radio model
  above. Real ESP32 RSSI has antenna patterns that depend on wrist pose, multipath
  that doesn't follow a log-normal distribution, BLE advertising channel hopping (a
  different offset on channels 37/38/39), and AGC steps. None of these are modelled.
  The ranking is more trustworthy than the absolute scores.
* **Distance bias is tuned to the sim.** Each candidate folds a fixed or adaptive
  body/fading loss and a steep exponent (2.5–3.0) into its path loss. If the real
  `n` or body loss differ, distances will be biased in the same direction in every
  scene.
* **far_edge (80–110 m)** is poor for everyone: log error about 1.0, reading about
  30–40 m. Under `indoor` no packets arrive at all, and only `dist_cov` shows that.
* **Metric quirks:**
  * showing no trend while moving counts as wrong, so always guessing a direction
    pays off
  * a frozen display never flips zones
  * reversal lag is reported but not scored
  * `orbit` can't be solved from RSSI and step counts alone
* **`peer_rssi` has no freshness flag.** A peer that stops hearing us keeps
  re-sending its last value. The estimators weight it lightly, but the protocol
  should add an age or sequence field.
* **Timing:** ESP32 cost is extrapolated, as described above. The single-precision
  float behaviour of the ESP32 port is untested, because the WASM port uses doubles.

### What to measure on real watches to recalibrate

Use two watches, log `(t_ms, rssi, peer_rssi, steps, activity)` on both at 10 Hz,
and record the true distance with a tape or GPS on a field.

1. **`p0` and `n`:** hold still at 1, 2, 4, 8, 16, 32 and 64 m, 30 s per point, in
   both an open field and a hallway. Fit the median RSSI against `log10(d)` to get
   the slope `n` and the intercept `p0` per environment. Check how well the bump
   calibration at 1 m predicts `p0` (`CAL_SIGMA`).
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
   bake-off, and only then re-tune `N_EFF`/`BIAS_*` (kalman2) or
   `N_PL`/`P0_ADJ` (particle).

See also [imu-drift.md](imu-drift.md) for why the IMU is used only as a motion hint
and never integrated into a position.

## 5. Audit

An independent pass tried to refute the result above: it re-read `tools/bakeoff.py`,
`sim/*.py` and every estimator, re-ran the held-out comparison and ran the stress
tests below. **Verdict: confirmed.** `kalman2` stays the default, and `particle`
stays the better tracker in simulation that has not been shown to fit the budget.

### Checked and found sound

* **Trend sign.** The sim's `radial_speed` is d(distance)/dt, so the metric expects
  +1 when it is below -0.3 m/s. `kalman2` (+rate = rising RSSI), `particle`
  (P(closer) - P(farther)) and `kalman1d` all use +1 = closer. `reversal_lag_s`
  waits for +1 after the turn back, which is also right.
* **No ground-truth leakage.** Nothing under `finder/` imports `sim`. Estimators
  get RSSI, `peer_rssi` (the partner's previous measurement of us), the bump
  calibration, and `MotionInfo` (activity, cadence, step count), which the sim
  derives from simulated step counts with per-person bias, drift, phantom steps and
  latency, never from velocity or heading. True distance and radial speed only
  reach the scorer. The motion hints are sampled at the end of each 50 ms tick, so
  they can be up to 50 ms ahead of a packet, which doesn't matter.
* **No metric gaming.** Showing 0 while moving counts as wrong, and showing a trend
  while the distance is constant counts as false. A wrong arrow and no arrow score
  the same, so the audit also counted wrong arrows while moving: particle 0.116,
  kalman2 0.150. `particle`'s lead is not guesswork: its precision is higher too.
* **Held-out result reproduced** exactly (seeds 100–129, 1200 runs, ~30 s). A third
  seed block, 200–229, gives the same order: particle 0.657, kalman2 0.626,
  kalman1d 0.615, median_ema 0.609. On the tuning seeds 0–9 `particle` scores 0.678
  and drops to 0.662 on held-out seeds, while `kalman2` goes from 0.631 to 0.635.
  That suggests `particle` is slightly overfitted to seeds 0–9, but not enough to
  change the order.
* **Tick wrap** (`--wrap`, seeds 100–109) gives exactly the same numbers as no wrap.
* **MicroPython:** `--quick` scores are identical to CPython's, and both test suites
  pass.
* **Division by zero / None:** every divisor is floored (`p11 >= 1e-6`, distances
  >= `D_MIN`, standard error when `sw < 5`), and runs without packets
  (`far_edge`/`indoor`) give `None` distance error, which `mean_metrics` skips for
  every estimator alike (`dist_cov` 0.92 for all).

### Fixed

1. **`kalman2` kept a stale arrow between packets.** It cleared the trend when both
   watches were still only on packet updates. On no-packet ticks, the watch
   could keep showing "warmer" or "colder" after both wearers stopped, until the
   next packet arrived (0.18 % of all ticks, more in lossy profiles). Now a
   no-packet update with speed 0 clears the trend as well, like `particle` and
   `kalman1d` do. New test: `test_stop_clears_trend_without_packets`. Effect:
   false_trend 0.187 → 0.180, score 0.634 → 0.635. The other metrics don't change.
2. **The claim that `kalman2` "allocates nothing" was wrong.** In MicroPython every
   float result is a 16-byte heap block. Measured on MicroPython (WASM): `kalman2`
   1.7 KB per packet, `particle` 19.9 KB (the doc said about 500 floats; it is about
   1 240). The ratio (11×) supports the decision more strongly than before. The
   section on MicroPython cost now has the measured table.
3. **New `tools/bench_est.py`.** It times packet updates as one block, so the 1 ms
   `ticks_us` resolution of the WASM port doesn't matter, and it reports bytes per
   packet on MicroPython. It runs on CPython, the WASM port, and a watch that has
   `finder/` and `sim/` copied to it. It is tested in `tests/test_sim.py`. The
   block-timed WASM numbers (kalman2 30 µs, particle 174 µs) agree with the per-call
   means from the bake-off (34 / 182).

### Stress tests (seeds 100–114, all scenarios and profiles; score)

| variant | particle | kalman2 | kalman1d | median_ema |
|---|---|---|---|---|
| as simulated | 0.668 | 0.646 | 0.630 | 0.621 |
| body blocking × 1.5 | 0.656 | 0.625 | 0.615 | 0.612 |
| body blocking × 0.5 | 0.654 | 0.644 | 0.623 | 0.611 |
| no body blocking | 0.610 | **0.614** | 0.592 | 0.572 |
| motion hints 1 s late | 0.652 | 0.632 | 0.608 | 0.607 |
| motion hints 2 s late | 0.634 | 0.614 | 0.589 | 0.588 |
| bump calibration sees the real link offset (seeds 100–129) | 0.667 | 0.637 | | |
| 20 Hz beacons (seeds 100–109) | 0.672 | 0.644 | 0.628 | 0.628 |

* `particle`'s lead depends on the sim's **body-blocking model**. It explicitly models
  "walking away puts my body in the way". With half the body loss the lead shrinks
  to 0.010, and with none `kalman2` is marginally ahead. Real body loss is
  unmeasured (§4, item 3). This is one more reason not to switch on simulation
  alone.
* A late step counter (BMA423 polling or step-detection delay, not modelled) raises
  false trends for everyone (kalman2 0.18 → 0.33 at 2 s) but doesn't change the
  order.
* The bump calibration is the nominal value plus noise, not the real per-device
  link offset. If calibration captured the offset, every estimator would score a
  little higher and the order would stay the same.

### Still open

* The ESP32 cost is still extrapolated. `bench_est.py` on a watch is the next step.
* The ESP32 port uses single-precision floats and the WASM port uses doubles. The
  filters keep variances floored and `kalman1d`/`median_ema` re-centre their sums,
  but this has not been run in float32.
* A wrong arrow scores the same as no arrow. The game may want to penalise wrong
  arrows more heavily. That would not change this decision (see wrong-arrow rates
  above).


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

So: 2.6 outdoors, 3.0 indoors/crowded (the old value, best there). The composite held-out score with 2.6 used
everywhere is 0.600 (was 0.635), because the harness has no environment setting and scores the outdoor exponent on
indoor profiles too; with the setting applied per environment the indoor rows keep their old numbers. Recalibrate both
values from real walks (log RSSI against measured distance) once the watches run.
