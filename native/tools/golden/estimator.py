"""finder/estimators/base.py: PathLoss both ways, and the shared noise tracking
(_note_noise), reset/calibrate/set_exponent and the distance helper through a
minimal subclass (the base's update is abstract)."""

from finder.estimators.base import PathLoss, RangeEstimator


def r(v):
    return "n" if v is None else repr(float(v))


class Probe(RangeEstimator):
    """step(): one own packet (noise learnt, rssi_f/rssi_var set) or None, then the distance."""

    def update(self, t_ms, rssi, peer_rssi=None, my_motion=None, peer_motion=None):
        raise NotImplementedError

    def step(self, t_ms, rssi, var):
        if rssi is not None:
            self._note_noise(t_ms, rssi)
            self.rssi_f = rssi
            self.rssi_var = var
        self._set_distance_from_rssi()


# (t, rssi or None, var): pairs under 1 s, a 1.5 s gap, two packets in the same ms,
# integer and fractional dBm, zero and negative variance (sd 0)
STEPS = ((0, None, 4.0), (100, -70, 4.0), (200, -73, 4.0), (300, -66, 9.0), (400, -66, 0.0),
         (500, -80.5, -1.0), (2000, -60, 2.25), (2100, -61, 16.0), (2100, -58, 16.0),
         (3099, -90, 100.0), (4100, -72.25, 0.5))


def lines():
    yield "# pl <p0> <n> <rssi> -> <rssi_to_dist>; pd <p0> <n> <d> -> <dist_to_rssi>"
    d = PathLoss()
    yield "pl0 -> %s %s" % (r(d.p0), r(d.n))
    for p0, n in ((-45.0, 2.6), (-41.5, 3.0), (-52.25, 2.0)):
        pl = PathLoss(p0, n)
        for rssi in (-30, -45, -60.5, -75, -96):
            yield "pl %s %s %s -> %s" % (r(p0), r(n), r(rssi), r(pl.rssi_to_dist(rssi)))
        for dm in (0.0, 0.01, 0.05, 0.3, 1.0, 7.5, 60.0):
            yield "pd %s %s %s -> %s" % (r(p0), r(n), r(dm), r(pl.dist_to_rssi(dm)))
    yield "# probe: new | reset | exp <n> | cal <p0> | step <t> <rssi> <var> ->"
    yield "#   noise_db jit rssi_f rssi_var dist_m dist_lo_m dist_hi_m trend trend_conf rate_db_s last_t p0 n"
    e = Probe()

    def state():
        return "%s %s %s %s %s %s %s %d %s %s %s %s %s" % (
            r(e.noise_db), r(e.jit), r(e.rssi_f), r(e.rssi_var), r(e.dist_m), r(e.dist_lo_m), r(e.dist_hi_m),
            e.trend, r(e.trend_conf), r(e.rate_db_s), "n" if e.last_t is None else e.last_t, r(e.pl.p0),
            r(e.pl.n))

    yield "new -> " + state()
    for k, cmd in enumerate(("", "exp 3.0", "cal -50.5", "reset", "exp 2.6")):
        if cmd:
            w = cmd.split()
            if w[0] == "exp":
                e.set_exponent(float(w[1]))
            elif w[0] == "cal":
                e.calibrate(float(w[1]))
            else:
                e.reset()
            yield cmd + " -> " + state()
        for t, rssi, var in STEPS:
            t += 10000 * k
            e.step(t, rssi, var)
            yield "step %d %s %s -> %s" % (t, r(rssi), r(var), state())
