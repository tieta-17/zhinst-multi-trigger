"""
simulator.py
============
A fake HF2IS. It produces LF and HF demod data with a realistic large complex
baseline plus occasional "particle" events (a Gaussian bump in magnitude with a
characteristic phase). Some events are given a bead-like phase (should classify
in-window and trigger) and some a cell-like phase (out-of-window), so you can
watch the whole detect -> classify -> trigger path light up with no instrument
attached.

It exposes the SAME interface as ZhinstInstrument: clockbase() and
poll(poll_time) -> PollResult. That is the whole point of the adapter pattern.
"""

import time
import numpy as np

import config
from hardware import PollResult

CLOCKBASE = 210_000_000.0   # HF2 series ~210 MHz tick clock


class SimulatedInstrument:
    def __init__(self, log=print, event_rate_hz=6.0, seed=0):
        self.log = log
        self.rate = float(config.SAMPLING_RATE)
        self.clockbase_val = CLOCKBASE
        self.ticks_per_sample = CLOCKBASE / self.rate
        self._tick = np.float64(1_000_000)     # arbitrary start tick
        self._t0 = time.time()
        self.rng = np.random.default_rng(seed)
        self.event_rate = event_rate_hz

        # Large static baselines (this is what dominates the RAW phase and is
        # exactly what the live complex-baseline subtraction removes).
        self.lf_baseline = 0.020 * np.exp(1j * 0.9)
        self.hf_baseline = 0.015 * np.exp(1j * -0.4)

        # Bead reference phases (what your calibration means encode). Events near
        # these classify in-window; events offset by ~0.5 rad fall out.
        self.lf_bead_phase = 1.2
        self.hf_bead_phase = -0.8

        # Pending events: list of (tick_center, lf_amp, hf_amp, lf_ph, hf_ph, width)
        self._events = []
        self._clk_pulse_tick = None

        self.settings = {0: {"note": "simulated"}, 1: {"note": "simulated"}}

    def clockbase(self):
        return self.clockbase_val

    def _maybe_schedule_events(self, t_start, t_end):
        """Poisson-ish event scheduling within this poll's tick span."""
        dt = (t_end - t_start) / self.clockbase_val
        if self.rng.random() < self.event_rate * dt:
            center = self.rng.uniform(t_start, t_end)
            live = self.rng.random() < 0.6
            lf_ph = self.lf_bead_phase + (0.0 if live else 0.6) + self.rng.normal(0, 0.05)
            hf_ph = self.hf_bead_phase + (0.0 if live else 0.6) + self.rng.normal(0, 0.05)
            amp = self.rng.uniform(0.6e-3, 1.5e-3)
            width = 6 * self.ticks_per_sample   # ~6 samples wide
            self._events.append([center, amp, amp * 0.8, lf_ph, hf_ph, width, live])

    def poll(self, poll_time):
        # advance wall clock like a real poll would
        time.sleep(max(poll_time, 0.0005))
        n = max(int(round(self.rate * max(poll_time, 0.0005))), 8)

        ticks = self._tick + np.arange(n) * self.ticks_per_sample
        self._tick = ticks[-1] + self.ticks_per_sample

        self._maybe_schedule_events(ticks[0], ticks[-1])

        # start from baseline + gaussian measurement noise
        lf = np.full(n, self.lf_baseline) + (self.rng.normal(0, 3e-5, n)
                                             + 1j * self.rng.normal(0, 3e-5, n))
        hf = np.full(n, self.hf_baseline) + (self.rng.normal(0, 3e-5, n)
                                             + 1j * self.rng.normal(0, 3e-5, n))

        # add any active events (perturbation rides ON TOP of the baseline)
        still = []
        for ev in self._events:
            center, lfa, hfa, lfp, hfp, width, live = ev
            env = np.exp(-0.5 * ((ticks - center) / width) ** 2)
            lf += lfa * env * np.exp(1j * lfp)
            hf += hfa * env * np.exp(1j * hfp)
            if ticks[-1] - center < 4 * width:    # keep until it has fully passed
                still.append(ev)
        self._events = still

        aux = np.zeros(n)
        if self._clk_pulse_tick is not None:
            aux[0] = 0.1                          # fake sync pulse
            self._clk_pulse_tick = None

        return PollResult(
            lf_x=lf.real.copy(), lf_y=lf.imag.copy(), lf_ts=ticks.astype(np.uint64),
            hf_x=hf.real.copy(), hf_y=hf.imag.copy(), hf_ts=ticks.astype(np.uint64),
            aux=aux,
        )

    def pulse_clock(self):
        self._clk_pulse_tick = self._tick

    def cleanup(self):
        pass
