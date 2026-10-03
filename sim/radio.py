"""Per-packet RSSI model for the two-watch link.

rssi = p0_link - 10 n log10(d) + shadowing + fast fading - body blocking (both
wearers) - obstacles (+ rare outliers), quantised to 1 dB. Packets below the
sensitivity floor, or dropped by the distance-dependent loss model, never arrive.
"""

import math

P0_NOM = -45.0      # nominal RSSI at 1 m, dBm (what calibrate() is told)
FLOOR = -96.0       # receiver sensitivity, dBm
JITTER_MS = 10      # beacon period jitter, +- ms
DCORR = 6.0         # shadowing decorrelation distance, m
TAU_T = 8.0         # temporal (environment) drift time constant, s
FF_TAIL = 1.5       # fast fading: downward half is this much wider (deep fades)
DEV_OFF = 1.5       # per-device p0 offset, uniform +-dB (link total up to +-3)
LINK_ASYM = 1.0     # per-direction asymmetry, uniform +-dB
CAL_SIGMA = 1.0     # 1 m calibration (PAIRING calibrate) measurement noise, dB

PROFILE_NAMES = ["clean", "typical", "harsh", "indoor"]
INDOOR_PROFILES = ("harsh", "indoor")   # radio profiles where the watches use the indoor exponent

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
        world.add_obstacle(x - 0.1, cy - extent, x + 0.1, cy + extent, db)
        world.add_obstacle(cx - extent, y - 0.1, cx + extent, y + 0.1, db)


class Packet:
    """A received beacon measured at ``rssi``; payload carries the sender's last
    RSSI of us (``peer_rssi``) and the sender's motion (``peer_motion``)."""

    __slots__ = ("t_ms", "rssi", "peer_rssi", "peer_motion")

    def __init__(self, t_ms, rssi, peer_rssi, peer_motion):
        self.t_ms = t_ms
        self.rssi = rssi
        self.peer_rssi = peer_rssi
        self.peer_motion = peer_motion


class Radio:
    """Beacon scheduler + channel for world.a (index 0) and world.b (index 1).

    Call ``step(dt)`` right after ``world.step(dt)``; it returns
    ``(packets_rx_by_a, packets_rx_by_b)``. ``period_ms[i]`` is watch i's beacon
    period (a host may change it between steps).
    """

    def __init__(self, world, prof, rng, imus=None, rate_hz=10.0):
        self.world = world
        self.imus = imus
        per = int(1000.0 / rate_hz)
        self.period_ms = [per, per]    # per transmitter: each watch beacons at its own rate
        dev = rng.fork(1)
        off = dev.uniform(-DEV_OFF, DEV_OFF) + dev.uniform(-DEV_OFF, DEV_OFF)
        asym = dev.uniform(-LINK_ASYM, LINK_ASYM)
        self.p0_link = [P0_NOM + off + asym, P0_NOM + off - asym]  # indexed by receiver
        self.cal = [P0_NOM + dev.gauss(0.0, CAL_SIGMA), P0_NOM + dev.gauss(0.0, CAL_SIGMA)]
        self.set_profile(prof, rng)
        self._rj = rng.fork(5)
        t0 = int(world.t * 1000.0 + 0.5)     # the schedule starts now, also on a running world
        self.next_tx = [t0 + self._rj.randint(0, per - 1), t0 + self._rj.randint(0, per - 1)]
        self.last_rssi = [None, None]  # last RSSI measured by device i
        self.sent = [0, 0]

    def set_profile(self, prof, rng):
        """New environment (path loss, noise, shadowing state). The device offsets,
        their calibration and the beacon schedule are hardware: they stay."""
        p = profile(prof)
        self.p = p
        self._rs = rng.fork(2)
        self._rf = (rng.fork(3), rng.fork(4))
        self.sh = self._rs.gauss(0.0, p["sigma_sh"])
        self.tv = self._rs.gauss(0.0, p["sigma_t"])

    def cal_p0(self, rx):
        """What the 1 m PAIRING calibration (ui-spec §5.8) hands device ``rx`` (nominal p0 + noise)."""
        return self.cal[rx]

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
        margin = mean - FLOOR
        pl = p["loss"] + p["extra_loss"]
        pl += (1.0 - pl) / (1.0 + math.exp((margin - 5.0) / 2.5))
        lost = rf.random() < pl
        rssi = int(math.floor(mean + x + 0.5))
        if lost or rssi < FLOOR:
            return None
        imu = self.imus[tx] if self.imus else None
        pk = Packet(t_ms, rssi, self.last_rssi[tx], imu.info if imu else None)
        self.last_rssi[rx] = rssi
        return pk

    def step(self, dt):
        self._advance_channel(dt)
        t_end = int(self.world.t * 1000.0 + 0.5)
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
            nt[tx] = t + self.period_ms[tx] + self._rj.randint(-JITTER_MS, JITTER_MS)
        return out

    def step_with(self, dt, world, prof):
        """One ``step`` on a stand-in channel (``world`` geometry, full profile dict ``prof``,
        shadowing and drift from 0): the beacon schedule advances, the real channel stays."""
        saved = (self.world, self.p, self.sh, self.tv)
        self.world, self.p, self.sh, self.tv = world, prof, 0.0, 0.0
        try:
            return self.step(dt)
        finally:
            self.world, self.p, self.sh, self.tv = saved
