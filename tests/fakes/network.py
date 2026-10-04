"""Fake of MicroPython's ``network`` module (ESP-NOW setup and the debug-mode join).

``set_ap(ssid, key, channel)`` puts one access point in range; ``connect``
with its name and password joins it after ``polls`` calls to
``isconnected()``. A wrong password and an unknown name both stay
``STAT_CONNECTING`` and never connect: with its default unlimited reconnects
the ESP32 port keeps retrying, so ``status()`` cannot tell them apart. A
name or password that is not text raises TypeError, as on the watch. Each
WLAN records its ``config(...)`` writes in ``sets``.
"""

STA_IF = 0
AP_IF = 1
STAT_IDLE = 1000
STAT_CONNECTING = 1001
STAT_GOT_IP = 1010
STAT_NO_AP_FOUND = 201
STAT_WRONG_PASSWORD = 202

_n = [0]       # WLANs made since reset_fakes(): each one stands for a new device
mac = [None]   # MAC of the newest WLAN (the fake ESPNow sends from it)
ap = {}        # the access point in range (set_ap), empty: none


def reset_fakes():
    _n[0] = 0
    mac[0] = None
    ap.clear()


def set_ap(ssid, key, channel=11, ip="192.168.1.40", mask="255.255.255.0", polls=3):
    ap.clear()
    ap.update(ssid=ssid, key=key, channel=channel, ip=ip, mask=mask, polls=polls)


class WLAN:
    PM_NONE = 0
    PM_PERFORMANCE = 1
    PM_POWERSAVE = 2

    def __init__(self, iface=STA_IF):
        _n[0] += 1
        m = b"\x24\x0a\xc4\x00\x00" + bytes([_n[0]])
        mac[0] = m
        self.iface = iface
        self._active = False
        self.cfg = {"mac": m, "channel": 1}
        self.sets = []
        self.connected = False
        self._status = STAT_IDLE
        self._polls = 0
        self._ssid = None
        self._bad_key = False      # wrong password: retries forever, never connects

    def active(self, a=None):
        if a is None:
            return self._active
        self._active = bool(a)

    def config(self, *args, **kw):
        if args:
            return self.cfg.get(args[0])
        self.sets.append(kw)
        self.cfg.update(kw)

    def connect(self, ssid=None, key=None):
        if not self._active:
            raise OSError("Wifi Not Started")
        for v in (ssid, key):          # like mp_obj_str_get_data: text (or bytes) or None
            if v is not None and not isinstance(v, (str, bytes)):
                raise TypeError("can't convert '%s' object to str implicitly" % type(v).__name__)
        self.connected = False
        self._polls = 0
        self._ssid = ssid
        self._bad_key = bool(ap) and ssid == ap["ssid"] and key != ap["key"]
        self._status = STAT_CONNECTING

    def isconnected(self):
        if (self._status == STAT_CONNECTING and not self._bad_key and ap
                and self._ssid == ap["ssid"]):
            self._polls += 1
            if self._polls >= ap["polls"]:
                self.connected = True
                self._status = STAT_GOT_IP
                self.cfg["channel"] = ap["channel"]
        return self.connected

    def status(self):
        return self._status

    def ifconfig(self):
        if not self.connected:
            return ("0.0.0.0", "0.0.0.0", "0.0.0.0", "0.0.0.0")
        return (ap["ip"], ap["mask"], "192.168.1.1", "192.168.1.1")

    def disconnect(self):
        self.connected = False
        self._bad_key = False
        self._status = STAT_IDLE
