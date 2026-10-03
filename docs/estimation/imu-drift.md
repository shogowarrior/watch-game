# IMU drift: what the BMA423 can and cannot tell us

**Question:** the old `watch movement` notebook double-integrated the accelerometer
to get position and it ran away. Does a Kalman filter fix that?

**Short answer:** no. A Kalman filter can't fix it without an absolute reference.
With an accelerometer only (no gyro, no compass), position from double integration
is off by tens to hundreds of metres within a minute, and heading can't be observed
at all. A reliable, lightweight setup uses **steps × stride for distance, activity and
still detection for "is anyone moving", and tilt for gestures**. The Kalman filter
goes on the **RSSI range**, and the IMU sets its process noise. That's
`finder/motion.py` (`MotionTracker`).

Reproduce everything here with:

```
python3 tools/drift_demo.py --seeds 5 --bench
node tools/mpy/run.mjs tools/drift_demo.py --seeds 5 --bench   # identical numbers
```

## 1. What went wrong in the notebook

* `bma423.get_xyz()` already returns **g** (`range/2047 * raw`). The notebook
  multiplied by 981/1000, as if the values were mg going to cm/s², then labelled
  the result m/s². Every acceleration came out about **10x too small**.
* **Gravity was never removed.** The axis pointing up reads about +1 g forever, so
  that axis alone gives `0.5 * g * t^2`: about 49 m after 10 s even with the 10x
  error. With correct units it's about 490 m.
* Integration happened in the **sensor frame**. The watch rotates with the forearm on
  every arm swing, so "x" isn't a direction in the room.
* The 0.1 dead-band (about 0.1 g after the unit error) hides small real accelerations.
  It can't hide gravity or a tilt error.

## 2. BMA423 numbers (datasheet BST-BMA423-DS000, rev 1.1, May 2019)

| Parameter | Value | Why it matters |
|---|---|---|
| Resolution / sensitivity | 12 bit; 1024 LSB/g @ ±2 g, 512 @ ±4 g (driver: ~0.98 / 1.95 mg per LSB) | quantisation is negligible next to the offset |
| Zero-g offset | 80 mg (typ, ±4 g, 25 °C) | **dominant drift term**: 0.5·b·t² |
| Offset temperature drift (TCO) | 1 mg/K | a watch warming on the wrist moves the bias several mg, so a one-off calibration doesn't hold |
| Sensitivity temp. drift | 0.02 %/K | small |
| Nonlinearity / cross-axis | 0.5 %FS / 2 % | leaks gravity between axes (up to about 20 mg) |
| Noise density | 140 µg/√Hz; RMS 0.5 / 0.7 / 0.9 mg at ODR 25 / 50 / 100 Hz | small next to bias (see §3) |
| ODR | 12.5–1600 Hz (performance), 0.78–400 Hz (low-power) | features need **≥ 50 Hz** in low-power, taps need ≥ 200 Hz |
| Offset compensation regs | 3.9 mg LSB, ±0.5 g | can null the bias at one temperature only |
| On-chip features | step counter (wrist preset by default; "optimised on high accuracy"), step detector (low latency), activity recognition (reg 0x27: 0 still, 1 walking, 2 running, 3 unknown), tilt-on-wrist, tap/double-tap, any-/no-motion (slope threshold + duration) | cheap, low power, already on the chip; `MotionTracker.set_chip()` consumes them |

The datasheet gives no step-count accuracy figure. Published wrist step counters
land around 1–10 % error in structured walking. In free-living use they are worse
(MAPE often ≥ 20 %), and arm gestures cause **over-counting**. Wrist PDR papers
report distance errors of about 2–4 % when stride is personalised.

## 3. Why accelerometer-only dead reckoning drifts

With a constant residual bias *b*, velocity error is *b·t* and position error is
**½·b·t²**. A tilt error δθ leaks gravity as *g·sin δθ*. 1° is 17 mg, or 8.4 m of
error after 10 s. White noise contributes only σ_p ≈ n·t^1.5/√3, about 0.4 m after
60 s at 140 µg/√Hz. **Bias and tilt errors dominate, not noise.**

| residual bias | 10 s | 30 s | 60 s |
|---|---:|---:|---:|
| 40 mg (typical uncalibrated) | 20 m | 177 m | 706 m |
| 5 mg (well calibrated, same temp) | 2.5 m | 22 m | 88 m |
| 1 mg (≈ 1 K of TCO after calibration) | 0.5 m | 4.4 m | 18 m |

What a Kalman filter can and can't fix:

* **It adds no information.** It weighs a motion model against *measurements*.
  With no measurement of position or velocity, its covariance grows like the
  error above. It reports the drift honestly but can't remove it.
* **ZUPT (zero-velocity update)** is the standard pseudo-measurement: when the
  sensor is known to be still, feed "v = 0". That resets velocity and makes the
  horizontal bias observable. Foot-mounted IMUs get this at every stance phase
  and reach about 0.5–2 % of distance travelled. **A wrist is never still while
  walking**, so ZUPT only fires when the player stops.
* **Yaw is unobservable** without a gyro or magnetometer, even in foot-mounted
  ZUPT systems. Gravity gives roll and pitch, and only when the wrist isn't
  accelerating. No filter can tell which way the player turned.

## 4. Simulation study (`tools/drift_demo.py`, `sim/accel_synth.py`)

The synthetic wrist runs at 50 Hz with gravity, a pendulum arm swing (±17°,
0.95 Hz), body bounce and surge at a 1.9 Hz cadence, yaw wobble, slow tilt wander
(±4°), tremor, and BMA423 errors: 0.7 mg noise, offset N(0, 40 mg) per axis,
±1 mg/K tempco with 6 K of warming, 1 % scale, 1 % cross-axis, 12-bit
quantisation at ±4 g. Scenarios are 60 s each: standing still (watch held face-up),
walking at 1.3 m/s, and walk 20 s / stand 10 s ×2. Methods (b), (c) and (e) are
**given the true yaw**, which the watch can't know, so they are optimistic.

Mean |error| in metres over 5 seeds. The ±2 g run gives the same numbers to
within 0.2 m.

| method | still 10s | still 30s | still 60s | walk 10s | walk 30s | walk 60s | stopgo 10s | stopgo 30s | stopgo 60s |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| theory 0.5*b*t^2, b=40 mg | 19.6 | 176.5 | 706.1 | 19.6 | 176.5 | 706.1 | 19.6 | 176.5 | 706.1 |
| theory 0.5*b*t^2, b=5 mg | 2.45 | 22.1 | 88.3 | 2.45 | 22.1 | 88.3 | 2.45 | 22.1 | 88.3 |
| (a) naive, as notebook (x0.981, dead-band) | 49.6 | 444.7 | 1778 | 46.7 | 402.0 | 1597 | 46.7 | 406.6 | 1644 |
| (a') naive, correct units (x9.81) | 496.4 | 4455 | 17812 | 451.8 | 4007 | 15968 | 451.8 | 4063 | 16451 |
| (b) LP-tilt gravity removal + 2x integrate | 6.54 | 17.8 | 36.7 | 8.54 | 21.1 | 48.1 | 8.54 | 21.8 | 59.3 |
| (c) (b) + ZUPT velocity reset | 0.03 | 0.03 | 0.03 | 8.54 | 21.1 | 48.1 | 8.54 | 14.0 | 35.9 |
| (e) KF [v, bias] + ZUPT pseudo-meas. | 0.03 | 0.03 | 0.03 | 8.54 | 21.1 | 48.1 | 8.54 | 14.0 | 37.7 |
| (d) PDR: steps x 0.7 m (distance only) | 0.00 | 0.00 | 0.00 | 0.53 | 1.13 | 2.03 | 0.53 | 1.58 | 2.60 |

Step detection (software detector in `MotionTracker`): walk 113.4 vs 113.1 true
steps (+0.3 %), stop-go 78.0 vs 76.0 (+2.6 %), still 0 false steps. True path
lengths are 77.4 m (walk) and 52.0 m (stop-go).

What the table shows:

* **(a)** is dominated by gravity (`0.5·g·t²`). Fixing the units makes it 10x worse,
  not better.
* **(b)** removing gravity with a low-pass tilt estimate also soaks up the constant
  bias, since bias and gravity look the same to a low-pass. But the arm swing makes
  the tilt estimate lag by up to ±17°. That leaked gravity rectifies into a spurious
  2–3 m/s "velocity" forward and sideways: 48 m of error on a 77 m walk. When still,
  the slow tilt wander still costs about 37 m per minute.
* **(c) ZUPT** is excellent while standing (3 cm) and does nothing while walking,
  because the wrist never stops. In stop-go it only helps from the first stop on.
* **(e) the Kalman filter** (per axis, state [v, accel bias], ZUPT measurements,
  tuned) ties with (c). Its error source is unmodelled arm-swing leakage, not a
  constant bias, so it has nothing to estimate. A looser bias model
  (σ_b0 = 0.5 m/s²) made stop-go worse (59 m), because the ZUPT correction was
  blamed on bias.
* **(d) PDR** stays within 2–3 % of distance. That's the stride mismatch
  (0.7 vs 0.684 m) plus about 2 extra steps. It gives **no direction**. The
  simulator is kind to it: gait is perfectly periodic and there are no gestures.
  Expect 5–10 % on hardware without stride calibration.

## 5. Recommendation

**Use (on every watch, `finder/motion.py`):**

* `MotionTracker.set_chip(t, steps, act_code)` fed from the BMA423 step counter and
  ACTIVITY_TYPE register, polled at about 1 Hz. This is the primary step source.
  When the chip feed is absent for 5 s, the **software detector** takes over: band-
  pass on |a| (about 0.6–3 Hz), peak with hysteresis, 0.25–2 s interval gate, and
  a 4-step streak confirmation that rejects isolated arm gestures.
* `add_sample(t, x, y, z)` at 25–50 Hz for `is_still`: sd of |a| over 1 s, with
  hysteresis at 12 mg (enter) and 25 mg (leave). The same call gives gravity, tilt,
  and `face_up` (within 20° of flat to enter, over 30° to leave) for the scan
  gesture. Configure the BMA423 for **ODR 50 Hz** (the features need it) and
  **±4 g** (fast arm moves clip at ±2 g; the resolution cost is irrelevant).
* Distance walked is `dist_m = steps × stride`. Speed magnitude is
  `step_rate_hz × stride` (`MotionInfo.speed_mps`). Calibrate stride per player
  if possible, e.g. by pacing a known distance.
  Cost: about 16 µs per `add_sample` on the WebAssembly MicroPython port. This is
  estimated, not measured, at roughly 0.3–0.8 ms on the ESP32, inside the 2 ms
  budget. Per sample there are no list or object allocations, only float
  temporaries.

**Direction.** The IMU can't give a heading. Direction must come from the RSSI
gradient: warmer or colder while the player walks. Any direction hint (the arrow)
is valid only as long as the player hasn't turned, and the accelerometer can't
detect turns. So the arrow's confidence **decays with distance walked and time**
since it was established:
`heading_confidence(walked_m, elapsed_s) = 0.5^(walked/4 m + elapsed/20 s)`.
Show the arrow fading and ask for a fresh "walk a few steps" probe once it drops
below about 0.3.

**Where the Kalman filter goes: on the RSSI range, with IMU-adaptive process noise.**

* State [range (or filtered RSSI), range rate]. Measurements are the RSSI samples
  at about 10 Hz, plus the partner's reported RSSI when the link is symmetric.
* Process noise Q comes from both watches' `MotionInfo`: if both are `ACT_STILL`,
  Q is about 0. That's a ZUPT for the range: the distance can't change, so average
  hard and feed `rate = 0` as a pseudo-measurement. Walking gives
  `Q ∝ (v_me + v_peer)²` with `v = step_rate × stride`. Running raises it further.
* Use `v_me + v_peer` as a hard bound on |range rate| to gate RSSI outliers, such as
  multipath jumps and body shadowing.
* Report the trend (warmer or colder) only when the rate estimate exceeds its sigma.
  Steps since the last trend flip give "I walked 5 m and it got colder" style hints.

**Don't:** double-integrate for position, trust any accelerometer-only heading,
or expect a KF or ZUPT on the wrist to fix walking drift.

## 6. Sources

* Bosch Sensortec, *BMA423 Data Sheet* BST-BMA423-DS000-01 rev 1.1 (May 2019),
  as distributed with Watchy:
  <https://watchy.sqfmi.com/assets/files/BST-BMA423-DS000-1509600-950150f51058597a6234dd3eaafbb1f0.pdf>.
  Tables used: output signal, table 13 (noise vs ODR), features (§4.x), ACTIVITY_TYPE register (0x27).
* Wahlström & Skog, *Fifteen Years of Progress at Zero Velocity: A Review*
  (arXiv 2008.09208). ZUPT on foot-mounted IMUs, yaw unobservability, and error of
  about 0.5–2 % of distance.
* *A Robust Step Detection Algorithm and Walking Distance Estimation
  Based on Daily Wrist Activity Recognition Using a Smart Band*, Sensors 2018
  (PMC6069265). Wrist steps about 98.7 %, distance 2.2–4.2 %.
* Validity of wrist activity trackers in free-living settings (e.g. PMC6995435,
  PMC9270058). Upper-limb activity inflates step counts, and free-living MAPE is
  often ≥ 20 %.
* *Pedestrian Inertial Navigation: An Overview of Model and Data-Driven Approaches*
  (arXiv 2407.21676). A survey of PDR vs strapdown INS.
