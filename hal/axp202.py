"""Minimal AXP202 PMU driver for the T-Watch 2020 V1.

Covers what the game needs: LDO2 (backlight/panel) and LDO3 (audio) power,
battery gauge and ADCs, VBUS/charge status, the PEK side button (and its
long-press time) and power-off.

Register map checked against Lewis He's MIT AXP202X_Library (axp20x.h/.cpp)
and the repo's old ``axp202c.py`` (removed in 44880ab;
``git show 44880ab^:axp202c.py``). Notes:

* REG 0x12 bit1 is DCDC3, which powers the ESP32 itself. ``write()`` forces
  that bit on for every write to 0x12 (upstream does the same:
  ``FORCED_OPEN_DCDC3``). Clearing it browns the watch out instantly.
* Only single-byte register writes are used; the AXP20x multi-byte write
  format is not a plain auto-increment.
* Battery discharge current is 13 bits (H8 << 5 | L5), 0.5 mA/LSB, as in the
  C library (the old Python port read it as H8 << 4 | L4, which is wrong).
* IRQ status registers are write-1-to-clear: ``poll`` writes back exactly the
  bits it saw, so nothing that arrived in between is lost, and it reports a
  register only after its clear went through, so a bus error defers events
  to the next poll instead of dropping them.
"""

from finder.compat import const

ADDR = const(0x35)
CHIP_ID = const(0x41)

REG_STATUS = const(0x00)        # bit5 VBUS present, bit2 battery current direction
REG_CHG_STATUS = const(0x01)    # bit6 charging, bit5 battery connected
REG_IC_TYPE = const(0x03)
REG_POWER = const(0x12)         # output enables (see BIT_*)
REG_LDO24_MV = const(0x28)      # [7:4] LDO2 = 1800 + 100*n mV, [3:0] LDO4 table
REG_OFF_CTL = const(0x32)       # bit7: shut all outputs down
REG_POK_SET = const(0x36)       # [5:4] PEK long-press time, [1:0] power-off hold
REG_BAT_V_H8 = const(0x78)      # + 0x79 L4, 1.1 mV/LSB
REG_BAT_CHG_I_H8 = const(0x7A)  # + 0x7B L4, 0.5 mA/LSB
REG_BAT_DIS_I_H8 = const(0x7C)  # + 0x7D L5, 0.5 mA/LSB
REG_VBUS_V_H8 = const(0x5A)     # + 0x5B L4, 1.7 mV/LSB
REG_ADC_EN1 = const(0x82)
REG_BAT_PCT = const(0xB9)       # [6:0] percent, bit7 = not valid yet
REG_INTEN1 = const(0x40)        # ..0x44
REG_INTSTS1 = const(0x48)       # ..0x4C

# REG_POWER bits
BIT_DCDC3 = const(0x02)         # ESP32 supply: NEVER cleared
BIT_LDO2 = const(0x04)          # backlight / panel
BIT_LDO3 = const(0x40)          # audio amplifier

# REG_ADC_EN1 bits
ADC_BAT_V = const(0x80)
ADC_BAT_I = const(0x40)
ADC_VBUS_V = const(0x08)
ADC_APS_V = const(0x02)

# IRQ bits, per status register (index 0..4 = 0x48..0x4C)
IRQ1_VBUS_REMOVED = const(0x04)
IRQ1_VBUS_CONNECT = const(0x08)
IRQ3_PEK_LONG = const(0x01)
IRQ3_PEK_SHORT = const(0x02)
IRQ5_PEK_FALL = const(0x20)     # press (PWRON pulled low; AXP202X_Library IRQ 37)
IRQ5_PEK_RISE = const(0x40)     # release (IRQ 38)

# Events returned by poll() (bit mask)
EV_SHORT = const(0x01)          # PEK short press (on release)
EV_LONG = const(0x02)           # PEK held past the long-press time (set_long_press_ms)
EV_VBUS_IN = const(0x08)
EV_VBUS_OUT = const(0x10)
EV_PRESS = const(0x20)          # PEK falling edge: key pushed down (REG 0x4C bit5)
EV_RELEASE = const(0x40)        # PEK rising edge: key let go (REG 0x4C bit6)

# Simple Li-ion curve for when the fuel gauge is not valid yet (mV, %).
_OCV = ((4150, 100), (4050, 90), (3950, 75), (3850, 55), (3780, 40),
        (3720, 25), (3650, 12), (3550, 5), (3400, 0))


class AXP202:
    """AXP202 on a shared I2C bus. ``irq_pin``: optional GPIO35 ``Pin`` (active low)."""

    def __init__(self, i2c, addr=ADDR, irq_pin=None):
        self.i2c = i2c
        self.addr = addr
        self.irq_pin = irq_pin
        self._b = bytearray(1)
        self._sts = bytearray(5)        # poll(): INTSTS1..5
        cid = self.read(REG_IC_TYPE)
        if cid != CHIP_ID:
            raise OSError("AXP202 id 0x%02x" % cid)
        self.enable_adcs(ADC_BAT_V | ADC_BAT_I | ADC_VBUS_V | ADC_APS_V)

    # -- raw access -------------------------------------------------------
    def read(self, reg):
        self.i2c.readfrom_mem_into(self.addr, reg, self._b)
        return self._b[0]

    def write(self, reg, val):
        if reg == REG_POWER:
            val |= BIT_DCDC3
        self._b[0] = val & 0xFF
        self.i2c.writeto_mem(self.addr, reg, self._b)

    def _update(self, reg, set_bits, clear_bits=0):
        v = self.read(reg)
        n = (v & ~clear_bits) | set_bits
        if n != v:
            self.write(reg, n)
        return n

    def _h8l(self, reg_h, bits):
        h = self.read(reg_h)
        lo = self.read(reg_h + 1)
        return (h << bits) | (lo & ((1 << bits) - 1))

    # -- power outputs ----------------------------------------------------
    def set_output(self, bit, on):
        """Read-modify-write REG 0x12 (``write()`` keeps DCDC3 on)."""
        if bit & BIT_DCDC3 and not on:
            raise ValueError("DCDC3 powers the ESP32")
        return self._update(REG_POWER, bit if on else 0, 0 if on else bit)

    def output_on(self, bit):
        return bool(self.read(REG_POWER) & bit)

    def set_ldo2(self, on):
        """Backlight / panel supply."""
        self.set_output(BIT_LDO2, on)

    def set_ldo3(self, on):
        """Audio amplifier supply."""
        self.set_output(BIT_LDO3, on)

    def ldo2_on(self):
        return self.output_on(BIT_LDO2)

    def ldo3_on(self):
        return self.output_on(BIT_LDO3)

    def set_ldo2_mv(self, mv):
        """LDO2 voltage, 1800..3300 mV in 100 mV steps (LDO4 bits untouched)."""
        mv = 1800 if mv < 1800 else 3300 if mv > 3300 else mv
        n = (mv - 1800) // 100
        self._update(REG_LDO24_MV, n << 4, 0xF0)

    def shutdown(self):
        """Power the watch off (REG 0x32 bit7). Only after ``game.power_off``."""
        self._update(REG_OFF_CTL, 0x80)

    # -- ADC / battery ----------------------------------------------------
    def enable_adcs(self, mask):
        self._update(REG_ADC_EN1, mask)

    def battery_voltage(self):
        """Battery voltage in mV (int)."""
        return self._h8l(REG_BAT_V_H8, 4) * 11 // 10

    def vbus_voltage(self):
        """VBUS voltage in mV (int)."""
        return self._h8l(REG_VBUS_V_H8, 4) * 17 // 10

    def discharge_current_ma(self):
        """Battery discharge current in mA (int, 0 while charging)."""
        return self._h8l(REG_BAT_DIS_I_H8, 5) >> 1

    def charge_current_ma(self):
        return self._h8l(REG_BAT_CHG_I_H8, 4) >> 1

    def battery_connected(self):
        return bool(self.read(REG_CHG_STATUS) & 0x20)

    def is_charging(self):
        return bool(self.read(REG_CHG_STATUS) & 0x40)

    def vbus_present(self):
        return bool(self.read(REG_STATUS) & 0x20)

    def battery_percent(self):
        """0..100 from the fuel gauge, or from voltage while it is not valid."""
        v = self.read(REG_BAT_PCT)
        if not v & 0x80:
            v &= 0x7F
            return 100 if v > 100 else v
        return percent_from_mv(self.battery_voltage())

    # -- IRQs / PEK button --------------------------------------------------
    def enable_irqs(self, idx, mask):
        """Set ``mask`` in INTEN(idx+1), idx 0..4 (read-modify-write)."""
        self._update(REG_INTEN1 + idx, mask)

    def disable_irqs(self, idx, mask):
        self._update(REG_INTEN1 + idx, 0, mask)

    def enable_pek(self, edges=False, vbus=False):
        """Enable PEK short/long IRQs (and press/release edges, VBUS plug)."""
        self.enable_irqs(2, IRQ3_PEK_SHORT | IRQ3_PEK_LONG)
        if edges:
            self.enable_irqs(4, IRQ5_PEK_FALL | IRQ5_PEK_RISE)
        if vbus:
            self.enable_irqs(0, IRQ1_VBUS_CONNECT | IRQ1_VBUS_REMOVED)

    def set_long_press_ms(self, ms):
        """PEK long-press time, REG 0x36 bits 5:4 (1000/1500/2000/2500 ms);
        the power-on and power-off hold bits are left alone."""
        n = 0 if ms < 1250 else 1 if ms < 1750 else 2 if ms < 2250 else 3
        self._update(REG_POK_SET, n << 4, 0x30)

    def clear_irqs(self):
        """Clear every IRQ status bit (write 0xFF to 0x48..0x4C)."""
        for i in range(5):
            self.write(REG_INTSTS1 + i, 0xFF)

    def irq_pending(self):
        """True if the IRQ line is asserted (always True without a pin)."""
        p = self.irq_pin
        return p is None or p.value() == 0

    def poll(self):
        """Read and clear IRQ status; return EV_* mask (0 if nothing).

        Cheap when ``irq_pin`` is set: no I2C traffic unless the line is low.
        Clears unrelated pending bits too, so the shared line releases. All
        five registers are read before any is cleared, and a register is only
        decoded once its clear went through: a failed read or clear leaves
        those bits latched for the next poll, so no I2C error loses an event.
        """
        if not self.irq_pending():
            return 0
        st = self._sts
        for i in range(5):
            st[i] = self.read(REG_INTSTS1 + i)
        for i in range(5):
            if st[i]:
                try:
                    self.write(REG_INTSTS1 + i, st[i])   # write 1s to clear what we saw
                except OSError:
                    st[i] = 0       # still latched: the next poll reports it
        ev = 0
        for i in range(5):
            s = st[i]
            if not s:
                continue
            if i == 2:
                if s & IRQ3_PEK_SHORT:
                    ev |= EV_SHORT
                if s & IRQ3_PEK_LONG:
                    ev |= EV_LONG
            elif i == 4:
                if s & IRQ5_PEK_FALL:
                    ev |= EV_PRESS
                if s & IRQ5_PEK_RISE:
                    ev |= EV_RELEASE
            elif i == 0:
                if s & IRQ1_VBUS_CONNECT:
                    ev |= EV_VBUS_IN
                if s & IRQ1_VBUS_REMOVED:
                    ev |= EV_VBUS_OUT
        return ev

    def status(self):
        """Dict snapshot for notebooks / debug screens (allocates)."""
        return {
            "percent": self.battery_percent(),
            "mv": self.battery_voltage(),
            "dis_ma": self.discharge_current_ma(),
            "chg_ma": self.charge_current_ma(),
            "charging": self.is_charging(),
            "vbus": self.vbus_present(),
            "ldo2": self.ldo2_on(),
            "ldo3": self.ldo3_on(),
        }


def percent_from_mv(mv):
    """Rough state of charge (0..100) from a resting cell voltage."""
    if mv >= _OCV[0][0]:
        return 100
    for i in range(1, len(_OCV)):
        v1, p1 = _OCV[i]
        if mv >= v1:
            v0, p0 = _OCV[i - 1]
            return p1 + (p0 - p1) * (mv - v1) // (v0 - v1)
    return 0
