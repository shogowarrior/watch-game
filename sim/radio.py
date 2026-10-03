"""Per-packet RSSI model for the two-watch link.

rssi = p0_link - 10 n log10(d) + shadowing + fast fading - body blocking (both
wearers) - obstacles (+ rare outliers), quantised to 1 dB. Packets below the
sensitivity floor, or dropped by the distance-dependent loss model, never arrive.
"""

import math

from sim.world import Obstacle

P0_NOM = -45.0      # nominal RSSI at 1 m, dBm (what calibrate() is told)
FLOOR = -96.0       # receiver sensitivity, dBm
DCORR = 6.0         # shadowing decorrelation distance, m
TAU_T = 8.0         # temporal (environment) drift time constant, s
FF_TAIL = 1.5       # fast fading: downward half is this much wider (deep fades)
DEV_OFF = 1.5       # per-device p0 offset, uniform +-dB (link total up to +-3)
LINK_ASYM = 1.0     # per-direction asymmetry, uniform +-dB
CAL_SIGMA = 1.0     # bump-calibration measurement noise, dB

PROFILE_NAMES = ["clean", "typical", "harsh", "indoor"]

PROFILES = {
    "clean": dict(n=2.0, sigma_ff=2.0, sigma_sh=2.0, body_db=5.0, sigma_t=0.5,
                  out_p=0.0, out_db=0.0, loss=0.02, extra_loss=0.0, walls=False),
    "typical": dict(n=2.2, sigma_ff=4.0, sigma_sh=4.0, body_db=9.0, sigma_t=1.0,
                    out_p=0.01, out_db=12.0, loss=0.05, extra_loss=0.0, walls=False),
    "harsh": dict(n=2.8, sigma_ff=6.0, sigma_sh=6.0, body_db=13.0, sigma_t=1.5,
                  out_p=0.05, out_db=15.0, loss=0.05, extra_loss=0.20, walls=False),
    "indoor": dict(n=3.0, sigma_ff=5.0, sigma_sh=5.0, body_db=10.0, sigma_t=1.0,
                   out_p=0.02, out_db=12.0, loss=0.05, extra_loss=0.0, walls=True),
}


def profile(p):
    """Profile dict from a name or a dict (dicts are completed from 'typical')."""
    if isinstance(p, str):
        return PROFILES[p]
    d = dict(PROFILES["typical"])
    d.update(p)
    return d


def body_loss(db, rel_bearing):
    """Attenuation when the partner is behind the wearer: 0 in front, ``db`` at 180 deg."""
    f = 0.5 * (1.0 - math.cos(rel_bearing))
    return db * f * math.sqrt(f)


def add_walls(world, rng, spacing=10.0, db=2.0, extent=160.0):
    """Grid of thin interior walls (random phase) centred on the walkers' midpoint."""
    cx = 0.5 * (world.a.x + world.b.x)
    cy = 0.5 * (world.a.y + world.b.y)
    k = int(extent / spacing)
    ox = rng.uniform(0.0, spacing)
    oy = rng.uniform(0.0, spacing)
    for i in range(-k, k + 1):
        x = cx + ox + i * spacing
        y = cy + oy + i * spacing
        world.obstacles.append(Obstacle(x - 0.1, cy - extent, x + 0.1, cy + extent, db))
        world.obstacles.append(Obstacle(cx - extent, y - 0.1, cx + extent, y + 0.1, db))


class Packet:
    """A received beacon: ``rx`` measured ``rssi``; payload carries the sender's last
    RSSI of us (``peer_rssi``) and the sender's motion (``peer_motion``)."""

    __slots__ = ("t_ms", "rx", "rssi", "peer_rssi", "peer_motion")

    def __init__(self, t_ms, rx, rssi, peer_rssi, peer_motion):
        self.t_ms = t_ms
        self.rx = rx
        self.rssi = rssi
        self.peer_rssi = peer_rssi
        self.peer_motion = peer_motion


class Radio:
    """Beacon scheduler + channel for world.a (index 0) and world.b (index 1).

    Call ``step(dt)`` right after ``world.step(dt)``; it returns
    ``(packets_rx_by_a, packets_rx_by_b)``.
    """

    def __init__(self, world, prof="typical", rng=None, imus=None, rate_hz=10.0,
                 jitter_ms=10, p0_nom=P0_NOM, floor=FLOOR):
        from sim.rng import Rng
        rng = rng or Rng(0)
        p = profile(prof)
        self.p = p
        self.world = world
        self.imus = imus
        self.floor = floor
        self.p0_nom = p0_nom
        self.period_ms = int(1000.0 / rate_hz)
        self.jitter_ms = jitter_ms
        dev = rng.fork(1)
        off = dev.uniform(-DEV_OFF, DEV_OFF) + dev.uniform(-DEV_OFF, DEV_OFF)
        asym = dev.uniform(-LINK_ASYM, LINK_ASYM)
        self.p0_link = [p0_nom + off + asym, p0_nom + off - asym]  # indexed by receiver
        self.cal = [p0_nom + dev.gauss(0.0, CAL_SIGMA), p0_nom + dev.gauss(0.0, CAL_SIGMA)]
        self._rs = rng.fork(2)
        self._rf = (rng.fork(3), rng.fork(4))
        self._rj = rng.fork(5)
        self.sh = self._rs.gauss(0.0, p["sigma_sh"])
        self.tv = self._rs.gauss(0.0, p["sigma_t"])
        self.next_tx = [self._rj.randint(0, self.period_ms - 1), self._rj.randint(0, self.period_ms - 1)]
        self.last_rssi = [None, None]  # last RSSI measured by device i
        self.sent = [0, 0]
        self.recv = [0, 0]
        self._t_ms = int(world.t * 1000.0)

    def cal_p0(self, rx):
        """What a bump-to-pair calibration hands device ``rx`` (nominal p0 + noise)."""
        return self.cal[rx]

    def mean_rssi(self, rx):
        """Noise-free (no fast fading / outliers) RSSI at receiver ``rx``, dBm."""
        return self.p0_link[rx] + self._common()

    def _common(self):
        w = self.world
        p = self.p
        d = w.distance()
        if d < 0.1:
            d = 0.1
        r = -10.0 * p["n"] * math.log10(d)
        r -= body_loss(p["body_db"], w.rel_bearing(0)) + body_loss(p["body_db"], w.rel_bearing(1))
        return r - w.los_db() + self.sh + self.tv

    def _advance_channel(self, dt):
        p = self.p
        w = self.world
        moved = w.a.moved + w.b.moved
        if moved > 0.0:
            rho = math.exp(-moved / DCORR)
            self.sh = rho * self.sh + math.sqrt(1.0 - rho * rho) * self._rs.gauss(0.0, p["sigma_sh"])
        if p["sigma_t"] > 0.0:
            rho = math.exp(-dt / TAU_T)
            self.tv = rho * self.tv + math.sqrt(1.0 - rho * rho) * self._rs.gauss(0.0, p["sigma_t"])

    def _receive(self, t_ms, tx, mean):
        rx = 1 - tx
        p = self.p
        rf = self._rf[rx]
        self.sent[tx] += 1
        x = rf.gauss(0.0, p["sigma_ff"])
        if x < 0.0:
            x *= FF_TAIL
        if p["out_p"] > 0.0 and rf.random() < p["out_p"]:
            if rf.random() < 0.8:
                x -= p["out_db"] * rf.uniform(0.6, 1.4)
            else:
                x += 0.5 * p["out_db"] * rf.uniform(0.6, 1.4)
        margin = mean - self.floor
        pl = p["loss"] + p["extra_loss"]
        pl += (1.0 - pl) / (1.0 + math.exp((margin - 5.0) / 2.5))
        lost = rf.random() < pl
        rssi = int(math.floor(mean + x + 0.5))
        if lost or rssi < self.floor:
            return None
        self.recv[rx] += 1
        imu = self.imus[tx] if self.imus else None
        pk = Packet(t_ms, rx, rssi, self.last_rssi[tx], imu.info if imu else None)
        self.last_rssi[rx] = rssi
        return pk

    def step(self, dt):
        self._advance_channel(dt)
        t_end = int(self.world.t * 1000.0 + 0.5)
        self._t_ms = t_end
        out = ([], [])
        nt = self.next_tx
        if nt[0] > t_end and nt[1] > t_end:
            return out
        c = self._common()
        means = (self.p0_link[0] + c, self.p0_link[1] + c)
        while True:
            tx = 0 if nt[0] <= nt[1] else 1
            t = nt[tx]
            if t > t_end:
                break
            pk = self._receive(t, tx, means[1 - tx])
            if pk is not None:
                out[1 - tx].append(pk)
            nt[tx] = t + self.period_ms + self._rj.randint(-self.jitter_ms, self.jitter_ms)
        return out
