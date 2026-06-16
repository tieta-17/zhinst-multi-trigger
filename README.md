## Configuration File Format

The system expects an input calibration file in JSON format to define calibration parameters, phase means, and bounding boxes for both High-Frequency (HF) and Low-Frequency (LF) processing.

### Example `calibration.json`

```json
{
  "calibration_metadata": {
    "bead_size_um": 5.0,
    "notes": "Standard calibration run"
  },
  "high_frequency_hf": {
    "bead_phase_mean": 0.125,
    "phase_range_box": [-0.5, 0.5]
  },
  "low_frequency_lf": {
    "bead_phase_mean": 1.45,
    "baseline_to_peak_voltage": 2.38,
    "phase_range_box": [-1.2, 1.2]
  }
}