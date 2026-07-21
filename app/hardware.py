"""
hardware.py
===========
Thin abstractions over the two pieces of real hardware:

  * GPIO       -> RPi.GPIO on the Pi, a logging no-op everywhere else.
  * Instrument -> the Zurich HF2IS via zhinst, OR the simulator.

Both real imports are LAZY (done inside the constructors), so importing this
module -- and running in --sim mode -- needs nothing installed. That is what
lets you develop the whole GUI on your laptop.

The acquisition worker only ever talks to these interfaces, never to zhinst or
RPi.GPIO directly. That decoupling is what makes sim mode and the future
"move acquisition into its own process" change easy.
"""

import time
from dataclasses import dataclass
import numpy as np

import config


# ----------------------------------------------------------------------------
# A single poll's worth of data, normalised into a plain object so the worker
# never has to know whether it came from zhinst or the simulator.
# ----------------------------------------------------------------------------
@dataclass
class PollResult:
    lf_x: np.ndarray
    lf_y: np.ndarray
    lf_ts: np.ndarray      # instrument ticks
    hf_x: np.ndarray
    hf_y: np.ndarray
    hf_ts: np.ndarray
    aux: np.ndarray        # auxin0 (clock-sync pulse channel)


# ----------------------------------------------------------------------------
# GPIO
# ----------------------------------------------------------------------------
class NullGPIO:
    """Stand-in used off-Pi. Records the last few pin events for the GUI/log."""
    def __init__(self, log=print):
        self._log = log
    def setup_pins(self, pins):        pass
    def output(self, pin, high):
        # Comment this out if it gets noisy; useful when validating in sim.
        # self._log(f"[gpio] pin {pin} -> {'HIGH' if high else 'LOW'}")
        pass
    def cleanup(self):                 pass


class RaspberryGPIO:
    def __init__(self, log=print):
        import RPi.GPIO as GPIO          # lazy: only needed on the Pi
        self._GPIO = GPIO
        self._log = log
        GPIO.setmode(GPIO.BCM)

    def setup_pins(self, pins):
        for p in pins:
            self._GPIO.setup(p, self._GPIO.OUT)
            self._GPIO.output(p, self._GPIO.LOW)

    def output(self, pin, high):
        self._GPIO.output(pin, self._GPIO.HIGH if high else self._GPIO.LOW)

    def cleanup(self):
        self._GPIO.cleanup()


def make_gpio(sim, log=print):
    if sim:
        return NullGPIO(log)
    try:
        return RaspberryGPIO(log)
    except Exception as e:            # not on a Pi / lib missing
        log(f"[gpio] RPi.GPIO unavailable ({e}); using NullGPIO")
        return NullGPIO(log)


# ----------------------------------------------------------------------------
# Instrument -- real (zhinst) adapter
# ----------------------------------------------------------------------------
class ZhinstInstrument:
    """Wraps the HF2IS. Exposes clockbase() and poll()->PollResult."""
    def __init__(self, device_id="dev1051", sampling_rate=config.SAMPLING_RATE, log=print):
        from zhinst.toolkit import Session      # lazy imports
        self._session = Session("localhost", hf2=True)
        self._device = self._session.connect_device(device_id)
        dev = self._device
        self.clockbase_val = dev.clockbase()

        for d in (0, 1):
            dev.demods[d].enable(True)
            dev.demods[d].adcselect(0)           # signal voltage input
            dev.demods[d].rate(sampling_rate)
        time.sleep(0.1)

        # Pin down and log the demod filter settings so LF/HF group delay matches.
        # (This is the "record the demod settings" gap the postdoc flagged.)
        self.settings = {}
        for d in (0, 1):
            self.settings[d] = {
                "rate": float(dev.demods[d].rate()),
                "order": int(dev.demods[d].order()),
                "timeconstant": float(dev.demods[d].timeconstant()),
                "freq": float(dev.demods[d].freq()),
            }
        log(f"[instr] demod settings: {self.settings}")

        dev.demods[0].sample.unsubscribe()
        dev.demods[1].sample.unsubscribe()
        dev.demods[0].sample.subscribe()
        dev.demods[1].sample.subscribe()
        time.sleep(0.005)

    def clockbase(self):
        return self.clockbase_val

    def poll(self, poll_time):
        dev = self._device
        pr = self._session.poll(recording_time=poll_time)
        s0 = pr[dev.demods[0].sample]
        s1 = pr[dev.demods[1].sample]
        return PollResult(
            lf_x=s0["x"], lf_y=s0["y"], lf_ts=s0["timestamp"],
            hf_x=s1["x"], hf_y=s1["y"], hf_ts=s1["timestamp"],
            aux=s0["auxin0"],
        )

    def cleanup(self):
        try:
            self._device.demods[0].sample.unsubscribe()
            self._device.demods[1].sample.unsubscribe()
        except Exception:
            pass


def make_instrument(sim, log=print, **kwargs):
    if sim:
        from simulator import SimulatedInstrument
        log("[instr] running in SIMULATION mode")
        return SimulatedInstrument(log=log, **kwargs)
    return ZhinstInstrument(log=log, **kwargs)
