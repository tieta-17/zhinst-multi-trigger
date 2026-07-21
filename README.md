# HF2IS Live Acquisition + GUI

A refactor of the single-file live script into a threaded, queue-driven
application with live plotting. The science (complex-baseline subtraction,
time-matched HF sampling, bead-relative phase) is unchanged from the version we
worked out — it's just reorganised so a GUI can sit on top without endangering
the real-time loop.

## Architecture

```
                 cmd_q  (params)
   ┌──────────┐  ─────────────▶  ┌────────────────────┐
   │   GUI    │                  │ AcquisitionWorker  │
   │ (main    │  ◀─────────────  │  (its own thread)  │──▶ instrument.poll()
   │  thread) │   plot_q         │  - detect          │──▶ GPIO solenoids
   │          │  ◀─────────────  │  - classify        │
   └──────────┘   event_q        │  - trigger         │
                                 └────────────────────┘
```

* **AcquisitionWorker** (`acquisition.py`) owns the hot loop. It is the only
  thread that polls, classifies, and fires solenoids. It never blocks on a
  queue — `plot_q` drops its oldest item if the GUI falls behind, so a slow or
  frozen GUI can never delay a trigger.
* **GUI** (`gui.py`) runs on the main thread and only reads. A 30 Hz `QTimer`
  drains the queues and redraws. Controls turn into commands on `cmd_q`; the
  worker applies them at the top of its loop, so no locks are needed.
* **Hardware** (`hardware.py`) hides zhinst and RPi.GPIO behind small adapters
  with fallbacks, so the whole app runs on a laptop with nothing installed.
* **Simulator** (`simulator.py`) fakes the HF2IS — big baseline + injected
  particle events — so you can develop and demo offline.

## Running

```bash
# offline development / demo — synthetic data, full GUI, no hardware
python main.py --sim

# validate the acquisition logic in the terminal (no GUI)
python main.py --sim --headless --seconds 10

# REAL run on the Pi with the GUI
python main.py --calib path/to/calibration.json

# real run, terminal only
python main.py --calib path/to/calibration.json --headless
```

Install for the GUI: `pip install pyqtgraph PyQt5`
On Raspberry Pi OS: `sudo apt install python3-pyqt5 python3-pyqtgraph`

## The one thing to watch on the Pi (GIL / real-time)

Python threads share one interpreter lock, so a heavy GUI *can* steal CPU from
the acquisition thread and add jitter to your solenoid timing. This design
keeps the worker lean and the GUI decoupled, which is usually enough. If you
measure trigger jitter under load, move the worker into its own
`multiprocessing.Process` (it already only touches the instrument, GPIO, and
queues, so the change is small) — that gives acquisition its own core and its
own GIL. Alternatively run the GUI on a second machine and stream the queues.

## What has and hasn't been tested

* **Tested here:** every module byte-compiles; the full detect→classify→trigger
  loop runs against the simulator and produces correct in-window / out-of-window
  decisions (see the headless run).
* **Not tested here (no hardware/display in this environment):** the pyqtgraph
  GUI rendering, the real zhinst path, and the RPi.GPIO path. Those need to run
  on the Pi. The zhinst/GPIO imports are lazy, so failures there surface at
  runtime with a clear message rather than breaking `--sim`.

## Notes carried over from the diagnosis

* `hardware.ZhinstInstrument` now reads and logs each demod's `order`,
  `timeconstant`, and `freq` at startup — the "record the demod settings" gap.
  Set the LF and HF time constants equal (or compensate) so nearest-timestamp HF
  matching is exact.
* `save_snapshot` now writes an `.npz` with **both** channels' x/y/timestamps,
  the LF and HF detection indices, and both complex baselines, so an event can be
  replayed offline through the Python and MATLAB math.
