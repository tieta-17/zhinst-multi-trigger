import numpy as np

def get_calibrated_timestamp(pulse_signal, timestamps):
    for i in range(len(pulse_signal)):
        if (pulse_signal[i] > 0.05):
            # print(f"i: {i}, max signal: {np.max(pulse_signal)}")
            return timestamps[i]
    return 0

def normalize_phase(X,Y, phase_mean):
    z = X + 1j * Y
    z_norm = z * np.exp(-1j * phase_mean)
    z_relative = np.angle(z_norm)
    return z_relative