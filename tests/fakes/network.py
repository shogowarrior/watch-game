"""Fake of MicroPython's ``network`` module (enough for ESP-NOW setup)."""

STA_IF = 0
AP_IF = 1


class WLAN:
    PM_NONE = 0
    PM_PERFORMANCE = 1
    PM_POWERSAVE = 2

    def __init__(self, iface=STA_IF):
        self.iface = iface
        self._active = False
        self.cfg = {"mac": b"\x24\x0a\xc4\x00\x00\x01", "channel": 1}
        self.connected = False

    def active(self, a=None):
        if a is None:
            return self._active
        self._active = bool(a)

    def config(self, *args, **kw):
        if args:
            return self.cfg.get(args[0])
        self.cfg.update(kw)

    def disconnect(self):
        self.connected = False

    def isconnected(self):
        return self.connected
