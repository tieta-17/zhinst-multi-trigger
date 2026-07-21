"""
config.py
=========
All the tunable numbers live here in ONE place, split into two groups:

1. Constants that are fixed at startup (sampling rate, buffer sizes, GPIO pins).
2. `Params` -- the values you change while running (threshold, polarity, solenoid
   timing). These are owned by the acquisition thread and only ever mutated there,
   via commands coming off the command queue. The GUI never writes them directly;
   it enqueues a command and the worker applies it at the top of its loop. That is
   what keeps the two threads from stepping on each other WITHOUT needing a lock.

Keeping this file import-clean (no hardware imports) means every other module and
your dev machine can import it with nothing installed.
"""

from dataclasses import dataclass, field
from typing import List

# ----------------------------------------------------------------------------
# Fixed acquisition constants (set once, at startup)
# ----------------------------------------------------------------------------
SAMPLING_RATE       = 55100     # Hz (device snaps to nearest supported rate)
POLL_TIME           = 0.001     # s per poll; <0.02 can yield single-sample frames
NUM_LOOPS           = 5_000_000 # effectively "run forever"
NUM_FRAMES          = 12        # frames per detection window
NUM_SUB_BUFFERS     = 5000      # frames held in each rolling buffer (>= 100)
BASELINE_WINDOW     = 100       # frames of complex baseline (the live high-pass)

INST_SAMPLE_DELAY   = 0.0035    # s, fixed instrument sampling latency
INST_CLK_SYNC_DELAY = 0.001     # s, clock-sync correction

SNAPSHOT_FILE_PATH  = "./snapshots/"

# Geometry for the flow-rate-independent trigger lead time
DETECTION_TO_ACTUATION_UM = 5450
DETECTION_WINDOW_UM       = 200
DISTANCE_RATIO            = DETECTION_TO_ACTUATION_UM / DETECTION_WINDOW_UM

# ----------------------------------------------------------------------------
# GPIO pin map (BCM numbering)
# ----------------------------------------------------------------------------
SOLENOID_PIN_1 = 17
SOLENOID_PIN_2 = 23
PIN27          = 27
CLK_SYNC_PIN   = 22

# ----------------------------------------------------------------------------
# Live plotting
# ----------------------------------------------------------------------------
PLOT_FPS            = 30        # GUI redraw rate (decoupled from acquisition)
PLOT_HISTORY_PTS    = 3000      # points kept on screen per curve
PLOT_DECIMATE_BLOCK = 16        # raw samples per min/max decimation block


@dataclass
class Params:
    """Runtime-tunable parameters, owned by the acquisition worker."""
    max_voltage_threshold: float = 0.0003   # V (LF baseline-to-peak)
    polarity_flipped: bool       = False
    debounce_period: float       = 0.100    # s
    solenoid_pair_delay: float   = 0.015    # s between solenoid 1 and 2
    solenoid_1_duration: float   = 0.060    # s pin-high
    solenoid_2_duration: float   = 0.045    # s pin-high
    trigger_lead_time_offset: float = 0.0   # s, non-flow-scaling latency fudge

    # Calibration (filled from the JSON file at startup)
    bead_size_um: float          = 0.0
    lf_bead_phase_mean: float    = 0.0
    hf_bead_phase_mean: float    = 0.0
    lf_phase_range: List[float]  = field(default_factory=lambda: [-0.2, 0.2])
    hf_phase_range: List[float]  = field(default_factory=lambda: [-0.2, 0.2])

    def apply_command(self, name: str, value):
        """Mutate a single field in response to a GUI command. Returns a human
        string describing what changed, or None if the command was unknown."""
        if name == "threshold":
            self.max_voltage_threshold = float(value)
            return f"threshold = {value*1000:.4f} mV"
        if name == "flip_polarity":
            self.polarity_flipped = not self.polarity_flipped
            return f"polarity = {'Min-Peak' if self.polarity_flipped else 'Peak-Min'}"
        if name == "debounce":
            self.debounce_period = float(value)
            return f"debounce = {value*1000:.1f} ms"
        if name == "solenoid_pair_delay":
            self.solenoid_pair_delay = float(value)
            return f"solenoid pair delay = {value*1000:.1f} ms"
        if name == "solenoid_1_duration":
            self.solenoid_1_duration = float(value)
            return f"solenoid 1 = {value*1000:.1f} ms"
        if name == "solenoid_2_duration":
            self.solenoid_2_duration = float(value)
            return f"solenoid 2 = {value*1000:.1f} ms"
        if name == "lead_offset":
            self.trigger_lead_time_offset = float(value)
            return f"lead offset = {value*1000:.1f} ms"
        return None
