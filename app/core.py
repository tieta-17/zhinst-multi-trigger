"""
core.py
=======
Pure, hardware-free logic. Nothing here imports zhinst, RPi.GPIO, or Qt, so it
can be unit-tested and reasoned about on its own. This is where the science math
lives -- the same math the MATLAB pipeline does, just live.
"""

import json
import numpy as np


# ----------------------------------------------------------------------------
# Rolling buffer (ring of sub-buffers) -- unchanged in behaviour from your code,
# just documented. Each "sub-buffer" is one poll's worth of samples.
# ----------------------------------------------------------------------------
class RollingBuffer:
    def __init__(self, buffer_length):
        self.buffer_length = buffer_length
        self.buffers = [np.zeros(1) for _ in range(buffer_length)]
        self.write_index = 0

    def add_sub_buffer(self, sub_buffer):
        self.buffers[self.write_index] = sub_buffer
        self.write_index = (self.write_index + 1) % self.buffer_length

    def get_full_buffer(self):
        out = np.empty(0)
        for i in range(self.write_index, self.write_index + self.buffer_length):
            out = np.append(out, self.buffers[i % self.buffer_length])
        return out

    def get_x_buffers(self, x):
        """Concatenate the last `x` sub-buffers, oldest-first."""
        out = np.empty(0)
        for i in range(self.write_index - x, self.write_index):
            n = self.buffer_length + i if i < 0 else i
            out = np.append(out, self.buffers[n])
        return out


# ----------------------------------------------------------------------------
# Calibration file loader (lifted from your read_calibration_file, returns a
# dict; raises on bad file so the caller can decide what to do).
# ----------------------------------------------------------------------------
def read_calibration_file(file_path):
    with open(file_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return {
        "HF_BEAD_PHASE_MEAN": data["high_frequency_hf"]["bead_phase_mean"],
        "HF_PHASE_RANGE":     data["high_frequency_hf"]["phase_range_box"],
        "LF_BEAD_PHASE_MEAN": data["low_frequency_lf"]["bead_phase_mean"],
        "LF_PHASE_RANGE":     data["low_frequency_lf"]["phase_range_box"],
        "LF_BASELINE_PEAK_VOLTAGE": data["low_frequency_lf"]["baseline_to_peak_voltage"],
        "BEAD_SIZE_UM":       data["calibration_metadata"]["bead_size_um"],
    }


# ----------------------------------------------------------------------------
# Phase / window helpers -- the pieces we fixed earlier in the conversation.
# ----------------------------------------------------------------------------
def window_mean(arr, idx, half=3):
    """Mean of arr over [idx-half, idx+half) with edge clamping so an event at a
    buffer boundary can't produce an empty slice / nan. Matches MATLAB's 6-sample
    mean(A(pos-3:pos+2)) when idx is interior."""
    lo = max(idx - half, 0)
    hi = min(idx + half, len(arr))
    if hi <= lo:
        return arr[idx]
    return np.mean(arr[lo:hi])


def complex_at(x_win, y_win, idx, half=3):
    """Baseline-INCLUSIVE complex value at idx (before baseline subtraction)."""
    return window_mean(x_win, idx, half) + 1j * window_mean(y_win, idx, half)


def centered_phase(z, baseline_complex, rotation):
    """The full 'live high-pass then rotate' operation:
        (z - complex baseline)  ->  the particle perturbation vector
        * rotation (= exp(-j*bead_phase_mean))  ->  phase relative to beads
    Returns the bead-relative phase in radians."""
    return np.angle((z - baseline_complex) * rotation)


def nearest_by_time(hf_t_window, target_ts):
    """Index into the HF window whose timestamp is closest to target_ts.
    Cast to int64 first so uint64 device timestamps can't underflow-wrap."""
    return int(np.argmin(np.abs(hf_t_window.astype(np.int64) - np.int64(target_ts))))


# ----------------------------------------------------------------------------
# Min/max decimation for cheap live plotting. For every `block` raw samples we
# keep the min AND the max, so a sharp transit spike is never decimated away.
# Returns (decimated_values,) roughly length 2*ceil(n/block).
# ----------------------------------------------------------------------------
def minmax_decimate(values, block):
    n = len(values)
    if n == 0:
        return np.empty(0)
    if block <= 1 or n <= 2:
        return np.asarray(values, dtype=float)
    npad = (-n) % block
    if npad:
        values = np.concatenate([values, np.full(npad, values[-1])])
    reshaped = values.reshape(-1, block)
    mins = reshaped.min(axis=1)
    maxs = reshaped.max(axis=1)
    # interleave min,max so the on-screen trace preserves shape
    out = np.empty(mins.size + maxs.size, dtype=float)
    out[0::2] = mins
    out[1::2] = maxs
    return out
