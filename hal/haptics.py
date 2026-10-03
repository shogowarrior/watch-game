"""Vibration motor (T-Watch 2020 V1: ERM on GPIO4 via a transistor).

Strength 0..1 is mapped to LEDC PWM duty; any non-zero strength gets at least
``min_duty`` so the ERM actually spins up. Writes happen only on change, so
``motor.set(player.tick(t))`` can be called every frame for free.
"""

from machine import Pin, PWM

from hal.pins import MOTOR

# 1 kHz: well above the ERM's mechanical response (so PWM just averages
# voltage), low enough for a clean LEDC duty resolution and low switching loss.
MOTOR_PWM_HZ = 1000


class Motor:
    def __init__(self, pin=MOTOR, freq=MOTOR_PWM_HZ, min_duty=0.35, max_duty=1.0):
        self._pin_id = pin
        self._pin = Pin(pin, Pin.OUT, value=0)
        self._pwm = PWM(self._pin, freq=freq, duty_u16=0)
        self._lo = int(min_duty * 65535)
        self._span = int(max_duty * 65535) - self._lo
        self._s = 0
        self._duty = 0

    @property
    def strength(self):
        return self._s

    @property
    def duty_u16(self):
        return self._duty

    def set(self, strength):
        """Drive at ``strength`` 0..1; no-op if unchanged."""
        if strength == self._s:
            return
        self._s = strength
        if strength <= 0:
            d = 0
        elif strength >= 1:
            d = self._lo + self._span
        else:
            d = self._lo + int(self._span * strength)
        if d != self._duty:
            self._duty = d
            self._pwm.duty_u16(d)

    def off(self):
        self.set(0)

    def deinit(self):
        """Stop PWM and leave the pin driven low (motor off)."""
        self.set(0)
        self._pwm.deinit()
        Pin(self._pin_id, Pin.OUT, value=0)
