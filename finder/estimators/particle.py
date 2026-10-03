"""Particle filter over (distance, radial heading) with step-counter speed bounds.

Each particle is (d, c, b): distance in metres, c in [-1, 1] the share of the
combined walking speed S pointing away from the partner (radial velocity
v = c * S), and b the slow shadowing offset in dB. S comes from both step
counters (fresh steps -> moving, cadence x stride -> speed); it is never
integrated, so step-count drift only loosens or tightens the bound. When both
watches are still S = 0 and v = 0; c is kept, so walking on after a pause
resumes the old direction.

b is Rao-Blackwellised: a per-particle Kalman mean with one shared variance
(it decorrelates over distance walked), so slow shadowing is not mistaken for
motion. RSSI (own and the partner's echo) enters through the log-distance
model plus the walker's own body loss (walking away puts the body in the way)
with a Student-t (nu = 3) likelihood, so fades and outliers barely move it.
Particles stay sorted by d, so systematic resampling keeps the order and the
weighted median / 16th / 84th percentiles are one cumulative pass.

Cost: one loop over the particles per packet (propagate, weight, trend sums;
weights are renormalised inside the next pass), a near-sorted insertion pass,
and resampling when ESS < n/2. Gaussian noise comes from a +/- paired table.
"""

import math

from finder.compat import ticks_diff, clamp
from finder.estimators.base import RangeEstimator, PathLoss, KDB, ACT_RUN

N_PART = 24         # ~1.5 ms/packet estimated on ESP32 MicroPython; 64 scores ~0.01 higher
N_PL = 2.5          # path-loss exponent assumed (sim profiles span 2.0-3.0)
P0_ADJ = -4.0       # mean loss (other wearer's body, fades) vs. the 1 m calibration, dB
BODY_DB = 7.0       # walker's own body blocks when walking away: loss = BODY_DB*((1+c)/2)^2
SIGMA = 7.0         # fast fading, t-likelihood scale, dB
SIGMA_PEER = 8.0    # partner's echo: independent fading, possibly stale
SH_SIGMA = 3.0      # shadowing + calibration offset prior, dB
SH_DCORR = 6.0      # shadowing decorrelation distance, m
SH_TAU = 8.0        # ... and time constant while still, s
SH_T_SIGMA = 2.0    # temporal part, dB
C_NOISE = 0.25      # random walk of c per sqrt(s) while walking
C_JUMP = 0.04       # per second per particle: redraw c (turns)
LD_JIT = 0.08       # ln-distance jitter added to particles on resampling
D_MIN = 0.3
D_MAX = 200.0
STRIDE = 0.7
V_WALK_MIN = 0.9    # speed assumed once steps appear (cadence still ramping)
V_RUN = 2.5
V_UNKNOWN = 1.4     # no motion info from that watch
S_MARGIN = 1.15     # stride/cadence error allowance on the speed bound
STEP_STALE_MS = 1100   # no new step for this long -> stopped
STEP_GAP_MS = 1600     # two steps closer than this -> walking
MAX_DT = 2.0
V_TREND = 0.2       # |v| (m/s) that counts as moving closer/farther
M_ON = 0.05         # P(closer) - P(farther) to switch the trend on
M_OFF = -0.05       # ... and to keep it
N_RESET = 2         # particles re-seeded from the measurement on each resample
ENV_K = 0.18        # path-loss exponent per dB of mean |RSSI step| (fading depth ~ clutter)
FAD0 = 5.5          # prior mean |RSSI step| between packets, dB
FAD_A = 0.02        # its smoothing per packet
N_SPAN = 0.5        # adapted exponent stays within pl.n +/- this
OUT_H = 1.1         # reported distance holds unless the median moves by this factor
OUT_TAU = 0.3       # ... then follows it (log domain) with this time constant, s
FLOOR = -96.0       # receiver sensitivity, dBm
PRX_NEAR = 25.0     # use the reception model below FLOOR + this
PRX_M0 = 6.0        # mean margin over FLOOR with 50% reception, dB
PRX_W = 6.0         # width of the (algebraic) reception sigmoid, dB
RNG_SEED = 12345
G_PAD = 256         # Gaussian table (+/- pairs) is 2n + this; a random window per step


class _Steps:
    """Walking/still and speed from a step counter (per watch)."""

    def __init__(self):
        self.n = None
        self.t1 = None
        self.t0 = None

    def speed(self, t, m):
        if m is None:
            return V_UNKNOWN
        s = m.steps
        if self.n is None:
            self.n = s
        elif s != self.n:
            k = s - self.n
            self.n = s
            self.t0 = t if k > 1 else self.t1
            self.t1 = t
        if self.t1 is None or ticks_diff(t, self.t1) > STEP_STALE_MS:
            return 0.0
        if self.t0 is None or ticks_diff(self.t1, self.t0) > STEP_GAP_MS:
            return 0.0
        v = m.step_rate_hz * STRIDE
        if m.activity == ACT_RUN and v < V_RUN:
            v = V_RUN
        return v if v > V_WALK_MIN else V_WALK_MIN


class Estimator(RangeEstimator):
    name = "particle"

    def __init__(self, path_loss=None, n=None):
        self.n = n or N_PART
        self.adapt = path_loss is None
        RangeEstimator.__init__(self, path_loss or PathLoss(n=N_PL))

    def set_exponent(self, n):
        RangeEstimator.set_exponent(self, n)
        self.n_env = n

    def reset(self):
        RangeEstimator.reset(self)
        n = self.n
        self.D = [0.0] * n
        self.C = [0.0] * n
        self.B = [0.0] * n
        self.W = [1.0 / n] * n
        self._d2 = [0.0] * n
        self._c2 = [0.0] * n
        self._b2 = [0.0] * n
        self.tot = 1.0
        self.P = SH_SIGMA * SH_SIGMA
        self._s = RNG_SEED
        m = 2 * n + G_PAD
        g = []
        while len(g) < m:
            u1 = 1.0 - self._u()
            r = math.sqrt(-2.0 * math.log(u1)) * math.cos(6.283185307179586 * self._u())
            g.append(r)
            g.append(-r)
        self._g = g
        self._me = _Steps()
        self._peer = _Steps()
        self.speed = 0.0
        self.p_closer = 0.0
        self.p_farther = 0.0
        self.v_mean = 0.0
        self.ess = float(n)
        self.fad = FAD0
        self._z = None
        self.n_env = self.pl.n

    def _u(self):
        """Uniform [0, 1) from a small-int LCG (no bignums on MicroPython)."""
        s = (self._s * 75 + 74) % 65537
        self._s = s
        return s / 65537.0

    def _k(self):
        return KDB * self.pl.n

    def _dist(self, z):
        d = math.exp((self.pl.p0 + P0_ADJ - z) / self._k())
        return D_MIN if d < D_MIN else D_MAX if d > D_MAX else d

    def _init(self, z, s):
        n = self.n
        d0 = self._dist(z)
        sp = 2.0 * SH_SIGMA / self._k()
        vt = V_TREND / s if s > 0.0 else 2.0
        pc = pf = cm = 0.0
        w = 1.0 / n
        for j in range(n):
            c = 2.0 * self._u() - 1.0
            self.D[j] = d0 * math.exp(sp * (2.0 * (j + 0.5) / n - 1.0))
            self.C[j] = c
            self.B[j] = 0.0
            self.W[j] = w
            cm += w * c
            if c < -vt:
                pc += w
            elif c > vt:
                pf += w
        self.tot = 1.0
        self.p_closer = pc
        self.p_farther = pf
        self.v_mean = cm

    def update(self, t_ms, rssi, peer_rssi=None, my_motion=None, peer_motion=None):
        s = (self._me.speed(t_ms, my_motion) + self._peer.speed(t_ms, peer_motion)) * S_MARGIN
        self.speed = s
        if rssi is None:
            if s == 0.0:
                self.trend = 0
                self.trend_conf = 0.0
            return
        self._note_noise(t_ms, rssi)
        if self.last_t is None:
            self._init(float(rssi), s)
            self._z = rssi
            self.last_t = t_ms
            self._report(s, 1.0, self._pct(1.0))
            return
        dt = ticks_diff(t_ms, self.last_t) * 0.001
        self.last_t = t_ms
        if dt < 0.3:
            self.fad += FAD_A * (abs(rssi - self._z) - self.fad)
            if self.adapt:
                n0 = self.pl.n
                self.n_env = clamp(n0 + ENV_K * (self.fad - FAD0), n0 - N_SPAN, n0 + N_SPAN)
        self._z = rssi
        if dt <= 0.0:
            dt = 0.001
        elif dt > MAX_DT:
            dt = MAX_DT
        self._report(s, dt, self._step(dt, s, float(rssi), peer_rssi))

    def _step(self, dt, s, z, zp):
        """Propagate, weight, sort; returns (lo, med, hi) of the weighted particles."""
        n = self.n
        D = self.D
        C = self.C
        B = self.B
        W = self.W
        g = self._g
        u = self._u
        o = int(u() * (len(g) - 2 * n))
        k = self._k()
        p0 = self.pl.p0 + P0_ADJ
        bq = 0.25 * BODY_DB
        dmin = D_MIN
        dmax = D_MAX
        sq = math.sqrt(dt)
        cn = C_NOISE * sq if s > 0.0 else 0.0
        sdt = s * dt
        if s > 0.0:
            for _ in range(int(C_JUMP * dt * n + u())):
                C[int(u() * n)] = 2.0 * u() - 1.0
        # shared Kalman variance of b: decorrelates with distance walked and time
        rho = math.exp(-sdt / SH_DCORR)
        rt = math.exp(-dt / SH_TAU)
        P = rho * rho * self.P + (1.0 - rho * rho) * SH_SIGMA * SH_SIGMA
        P += (1.0 - rt * rt) * SH_T_SIGMA * SH_T_SIGMA
        S1 = P + SIGMA * SIGMA
        G1 = P / S1
        inv = 1.0 / (3.0 * S1)
        P = P * (1.0 - G1)
        has_p = zp is not None
        G2 = invp = 0.0
        if has_p:
            zp = float(zp)
            S2 = P + SIGMA_PEER * SIGMA_PEER
            G2 = P / S2
            invp = 1.0 / (3.0 * S2)
            P = P * (1.0 - G2)
        self.P = P
        # near the sensitivity floor only strong fades get through: weight by reception odds
        cz = z < FLOOR + PRX_NEAR
        fl = FLOOR + PRX_M0
        iw = 1.0 / PRX_W
        vt = V_TREND / s if s > 0.0 else 2.0
        sc = 1.0 / self.tot
        log = math.log
        tot = ss = pc = pf = cm = 0.0
        dp = 0.0
        srt = True
        for j in range(n):
            c = C[j]
            if cn:
                c += cn * g[o + j]
                if c > 1.0:
                    c = 2.0 - c
                elif c < -1.0:
                    c = -2.0 - c
            d = D[j] + c * sdt
            if d < dmin:
                d = dmin + dmin - d
                c = -c
            elif d > dmax:
                d = dmax
            f = 1.0 + c
            h = p0 - k * log(d) - bq * f * f
            b = B[j] * rho
            e = z - h - b
            a = 1.0 + e * e * inv
            w = W[j] * sc
            if cz:
                x = (h + b - fl) * iw
                w *= 0.5 + 0.5 * x / (1.0 + (x if x > 0.0 else -x))
            b += G1 * e / a
            if has_p:
                e = zp - h - b
                x = 1.0 + e * e * invp
                b += G2 * e / x
                a *= x
            w /= a * a
            D[j] = d
            C[j] = c
            B[j] = b
            W[j] = w
            tot += w
            ss += w * w
            cm += w * c
            if c < -vt:
                pc += w
            elif c > vt:
                pf += w
            if d < dp:
                srt = False
            dp = d
        if tot <= 0.0:
            for j in range(n):
                W[j] = 1.0
            tot = ss = float(n)
        it = 1.0 / tot
        self.tot = tot
        self.p_closer = pc * it
        self.p_farther = pf * it
        self.v_mean = cm * it
        self.ess = tot * tot / ss
        if not srt:
            self._sort()
        r = self._pct(tot)
        if self.ess < 0.5 * n:
            self._resample(z)
        return r

    def _sort(self):
        """Insertion sort by d: particles barely reorder between packets."""
        D = self.D
        C = self.C
        B = self.B
        W = self.W
        for j in range(1, self.n):
            d = D[j]
            if d < D[j - 1]:
                c = C[j]
                b = B[j]
                w = W[j]
                i = j - 1
                while i >= 0 and D[i] > d:
                    D[i + 1] = D[i]
                    C[i + 1] = C[i]
                    B[i + 1] = B[i]
                    W[i + 1] = W[i]
                    i -= 1
                D[i + 1] = d
                C[i + 1] = c
                B[i + 1] = b
                W[i + 1] = w

    def _pct(self, tot):
        """Weighted 16th / 50th / 84th percentile of d (particles sorted)."""
        D = self.D
        W = self.W
        m = self.n - 1
        j = 0
        cum = W[0]
        t = 0.16 * tot
        while cum < t and j < m:
            j += 1
            cum += W[j]
        lo = D[j]
        t = 0.5 * tot
        while cum < t and j < m:
            j += 1
            cum += W[j]
        med = D[j]
        t = 0.84 * tot
        while cum < t and j < m:
            j += 1
            cum += W[j]
        return lo, med, D[j]

    def _resample(self, z):
        n = self.n
        D = self.D
        C = self.C
        B = self.B
        W = self.W
        D2 = self._d2
        C2 = self._c2
        B2 = self._b2
        step = self.tot / n
        v = self._u() * step
        cum = W[0]
        i = 0
        m = n - 1
        g = self._g
        o = int(self._u() * (len(g) - n))
        jit = LD_JIT
        for j in range(n):
            while v > cum and i < m:
                i += 1
                cum += W[i]
            D2[j] = D[i] * (1.0 + jit * g[o + j])
            C2[j] = C[i]
            B2[j] = B[i]
            v += step
        self.D = D2
        self.C = C2
        self.B = B2
        self._d2 = D
        self._c2 = C
        self._b2 = B
        w = 1.0 / n
        for j in range(n):
            W[j] = w
        self.tot = 1.0
        self._sort()
        # sensor reset: a few particles re-seeded at the measured distance
        d0 = self._dist(z)
        for _ in range(N_RESET):
            j = int(self._u() * n)
            dd = d0 * math.exp(0.5 * g[int(self._u() * len(g))])
            self._place(j, dd, 2.0 * self._u() - 1.0)

    def _place(self, j, d, c):
        """Move particle j to distance d (b = 0) keeping the arrays sorted."""
        D = self.D
        C = self.C
        B = self.B
        m = self.n - 1
        while j > 0 and D[j - 1] > d:
            D[j] = D[j - 1]
            C[j] = C[j - 1]
            B[j] = B[j - 1]
            j -= 1
        while j < m and D[j + 1] < d:
            D[j] = D[j + 1]
            C[j] = C[j + 1]
            B[j] = B[j + 1]
            j += 1
        D[j] = d
        C[j] = c
        B[j] = 0.0

    def _report(self, s, dt, r):
        lo, med, hi = r
        k = self._k()
        self.rssi_f = self.pl.p0 + P0_ADJ - k * math.log(med)
        sd = 0.5 * k * math.log(hi / lo)
        self.rssi_var = sd * sd
        self.rate_db_s = -k * self.v_mean * s / med
        x = self.pl.n / self.n_env
        if x != 1.0:
            lo = math.pow(lo, x)
            hi = math.pow(hi, x)
            med = math.pow(med, x)
        o = self.dist_m
        if o is not None:
            raw = med
            q = med / o
            if 1.0 / OUT_H < q < OUT_H:
                med = o
            else:
                med = o * math.pow(q, 1.0 - math.exp(-dt / OUT_TAU))
            k = med / raw       # move the 1-sigma bounds with the held median
            lo *= k
            hi *= k
        self.dist_m = med
        self.dist_lo_m = lo
        self.dist_hi_m = hi
        pc = self.p_closer
        pf = self.p_farther
        tr = self.trend
        m = pc - pf
        if s <= 0.0:
            tr = 0
        elif tr == 1 and m < M_OFF or tr == -1 and -m < M_OFF:
            tr = 0
        if tr == 0 and s > 0.0:
            if m > M_ON:
                tr = 1
            elif m < -M_ON:
                tr = -1
        self.trend = tr
        self.trend_conf = pc if tr == 1 else pf if tr == -1 else 0.0
