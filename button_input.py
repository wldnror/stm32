"""Debounce the two panel buttons without legacy sysfs edge registration."""
import time


class ButtonInputs:
    def __init__(self, gpio, callbacks, debounce=0.04):
        self.gpio = gpio
        self.callbacks = callbacks
        self.debounce = debounce
        # Treat an input held during startup as a new press after debounce.
        self.stable = {pin: gpio.HIGH for pin in callbacks}
        self.candidate = dict(self.stable)
        self.changed_at = {pin: 0.0 for pin in callbacks}

    def poll(self, now=None):
        now = time.monotonic() if now is None else now
        for pin, callback in self.callbacks.items():
            value = self.gpio.input(pin)
            if value != self.candidate[pin]:
                self.candidate[pin] = value
                self.changed_at[pin] = now
            elif value != self.stable[pin] and now - self.changed_at[pin] >= self.debounce:
                self.stable[pin] = value
                callback(pin, value)
