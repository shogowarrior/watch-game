"""Fake of MicroPython's ``espnow`` module with an in-memory air interface.

Packets sent by any ESPNow instance are delivered to every other active
instance; tests can also inject packets with ``inject(inst, mac, msg, rssi)``.
"""

from tests.fakes import network as _net

MAX_DATA_LEN = 250
_instances = []
sent = []


def reset_fakes():
    del _instances[:]
    del sent[:]


class ESPNow:
    def __init__(self):
        self._active = False
        self.peers = []
        self.cfg = {}
        self.rx = []  # list of (mac, msg, rssi, t_ms)
        # sender address = this device's STA MAC, as on the watch (radio.mac)
        self.mac = _net.mac[0] or b"\x24\x0a\xc4\x00\x00" + bytes([len(_instances) + 1])
        _instances.append(self)

    def active(self, a=None):
        if a is None:
            return self._active
        self._active = bool(a)

    def config(self, **kw):
        self.cfg.update(kw)

    def add_peer(self, mac, *args, **kw):
        if mac in self.peers:
            raise OSError("ESP_ERR_ESPNOW_EXIST")
        self.peers.append(bytes(mac))

    def send(self, mac, msg=None, sync=True):
        if msg is None:
            mac, msg = None, mac
        sent.append((self.mac, bytes(msg)))
        for o in _instances:
            if o is not self and o._active:
                o.rx.append((self.mac, bytes(msg), -50, 0))
        return True

    def any(self):
        return len(self.rx) > 0

    def recvinto(self, data, timeout_ms=None):
        if not self.rx:
            return 0
        mac, msg, rssi, t = self.rx.pop(0)
        data[0] = mac
        buf = data[1]
        n = len(msg)
        buf[:n] = msg
        if len(data) >= 4:
            data[2] = rssi
            data[3] = t
        return n


def inject(inst, mac, msg, rssi=-60, t_ms=0):
    inst.rx.append((bytes(mac), bytes(msg), rssi, t_ms))
