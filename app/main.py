"""
main.py
=======
Entry point. Wires the queues, the acquisition worker, and (optionally) the GUI.

Usage:
  python main.py --sim                 # synthetic data, GUI, no hardware needed
  python main.py --sim --headless      # synthetic data, no GUI (logic smoke test)
  python main.py --calib cal.json      # REAL hardware run with the GUI
  python main.py --calib cal.json --headless   # real run, terminal only

The --sim flag is what lets you build and test everything on a laptop.
"""

import sys
import time
import queue
import argparse
import threading

import config
from config import Params
from core import read_calibration_file
import hardware
from acquisition import AcquisitionWorker


def build_params(args):
    p = Params()
    if args.calib:
        cfg = read_calibration_file(args.calib)
        p.bead_size_um = cfg["BEAD_SIZE_UM"]
        p.lf_bead_phase_mean = cfg["LF_BEAD_PHASE_MEAN"]
        p.hf_bead_phase_mean = cfg["HF_BEAD_PHASE_MEAN"]
        p.lf_phase_range = list(cfg["LF_PHASE_RANGE"])
        p.hf_phase_range = list(cfg["HF_PHASE_RANGE"])
        p.max_voltage_threshold = cfg["LF_BASELINE_PEAK_VOLTAGE"]
    else:
        # sim defaults roughly matching simulator.py's injected phases
        p.lf_bead_phase_mean = 1.2
        p.hf_bead_phase_mean = -0.8
        p.lf_phase_range = [-0.2, 0.2]
        p.hf_phase_range = [-0.2, 0.2]
        p.max_voltage_threshold = 0.0004
    return p


def make_queues():
    return (queue.Queue(maxsize=64),   # cmd
            queue.Queue(maxsize=8),    # plot (drop-oldest in worker)
            queue.Queue(maxsize=512))  # event


def run(args):
    params = build_params(args)
    cmd_q, plot_q, event_q = make_queues()
    stop_event = threading.Event()

    log = print
    gpio = hardware.make_gpio(sim=args.sim, log=log)
    gpio.setup_pins([config.SOLENOID_PIN_1, config.SOLENOID_PIN_2,
                     config.PIN27, config.CLK_SYNC_PIN])
    instr = hardware.make_instrument(sim=args.sim, log=log)

    worker = AcquisitionWorker(instr, gpio, params, cmd_q, plot_q, event_q, stop_event)
    worker.start()

    if args.headless:
        _run_headless(event_q, stop_event, args.seconds)
        gpio.cleanup()
        return

    # GUI on the main thread
    import pyqtgraph as pg
    from pyqtgraph.Qt import QtWidgets
    from gui import MainWindow
    app = QtWidgets.QApplication(sys.argv)
    win = MainWindow(params, cmd_q, plot_q, event_q, stop_event, sim=args.sim)
    win.show()
    app.exec_()
    stop_event.set()
    worker.join(timeout=2.0)
    gpio.cleanup()


def _run_headless(event_q, stop_event, seconds):
    """No GUI: just drain events to the terminal for `seconds`, then stop.
    Useful for validating the acquisition logic itself."""
    t_end = time.time() + seconds
    triggers = 0
    while time.time() < t_end and not stop_event.is_set():
        try:
            ev = event_q.get(timeout=0.2)
        except queue.Empty:
            continue
        if ev.get("type") == "log":
            print(ev["msg"])
        elif ev.get("type") == "detect":
            print(f"  detect  LFph={ev['lf_phase']:+.3f} HFph={ev['hf_phase']:+.3f} "
                  f"vdiff={ev['vdiff']*1000:.3f}mV  in_window={ev['in_window']}")
        elif ev.get("type") == "status" and "trigger_count" in ev:
            triggers = ev["trigger_count"]
    stop_event.set()
    print(f"\n[headless] done. triggers observed: {triggers}")


def main():
    ap = argparse.ArgumentParser(description="HF2IS live acquisition + GUI")
    ap.add_argument("--sim", action="store_true", help="use the simulator (no hardware)")
    ap.add_argument("--calib", default=None, help="path to calibration JSON (real runs)")
    ap.add_argument("--headless", action="store_true", help="no GUI, print events")
    ap.add_argument("--seconds", type=float, default=10.0, help="headless run length")
    args = ap.parse_args()

    if not args.sim and not args.calib:
        ap.error("real hardware runs need --calib; or pass --sim for the simulator")
    run(args)


if __name__ == "__main__":
    main()
