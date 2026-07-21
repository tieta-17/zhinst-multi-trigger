"""
gui.py
======
pyqtgraph front end. Runs on the MAIN thread and does nothing but:
  * drain plot_q / event_q on a QTimer (~30 Hz) and redraw,
  * turn button/spinbox changes into commands on cmd_q.

It never touches the instrument, GPIO, or the worker's state. That one-way data
flow (worker -> queues -> GUI, GUI -> cmd_q -> worker) is what keeps the live
loop safe from GUI stalls.

pyqtgraph is used instead of matplotlib because it repaints far faster -- it
matters on a Raspberry Pi. Install: pip install pyqtgraph PyQt5
(on Raspberry Pi OS: sudo apt install python3-pyqt5 python3-pyqtgraph)
"""

import queue
import numpy as np

import pyqtgraph as pg
from pyqtgraph.Qt import QtWidgets, QtCore

import config


class MainWindow(QtWidgets.QMainWindow):
    def __init__(self, params, cmd_q, plot_q, event_q, stop_event, sim=False):
        super().__init__()
        self.p = params
        self.cmd_q = cmd_q
        self.plot_q = plot_q
        self.event_q = event_q
        self.stop_event = stop_event

        self.setWindowTitle("HF2IS Live" + ("  [SIMULATION]" if sim else ""))
        self.resize(1200, 720)

        # on-screen ring buffers
        self.hist = config.PLOT_HISTORY_PTS
        self.x = np.zeros(self.hist)
        self.lf_r = np.zeros(self.hist)
        self.lf_base = np.zeros(self.hist)
        self.hf_r = np.zeros(self.hist)
        self._filled = 0

        self._build_ui()

        self.timer = QtCore.QTimer()
        self.timer.timeout.connect(self._tick)
        self.timer.start(int(1000 / config.PLOT_FPS))

    # ---- layout -------------------------------------------------------------
    def _build_ui(self):
        central = QtWidgets.QWidget()
        self.setCentralWidget(central)
        root = QtWidgets.QHBoxLayout(central)

        # left: controls
        controls = QtWidgets.QVBoxLayout()
        root.addLayout(controls, 0)

        controls.addWidget(QtWidgets.QLabel("<b>Controls</b>"))

        self.thr_spin = self._spin(controls, "Threshold (mV)",
                                   self.p.max_voltage_threshold * 1000,
                                   0.0, 100.0, 0.01,
                                   lambda v: self._send("threshold", v / 1000.0))
        self.deb_spin = self._spin(controls, "Debounce (ms)",
                                   self.p.debounce_period * 1000,
                                   0.0, 1000.0, 1.0,
                                   lambda v: self._send("debounce", v / 1000.0))
        self.s1_spin = self._spin(controls, "Solenoid 1 (ms)",
                                  self.p.solenoid_1_duration * 1000,
                                  0.0, 500.0, 1.0,
                                  lambda v: self._send("solenoid_1_duration", v / 1000.0))
        self.s2_spin = self._spin(controls, "Solenoid 2 (ms)",
                                  self.p.solenoid_2_duration * 1000,
                                  0.0, 500.0, 1.0,
                                  lambda v: self._send("solenoid_2_duration", v / 1000.0))
        self.pair_spin = self._spin(controls, "Pair delay (ms)",
                                    self.p.solenoid_pair_delay * 1000,
                                    0.0, 500.0, 1.0,
                                    lambda v: self._send("solenoid_pair_delay", v / 1000.0))
        self.lead_spin = self._spin(controls, "Lead offset (ms)",
                                    self.p.trigger_lead_time_offset * 1000,
                                    -200.0, 200.0, 1.0,
                                    lambda v: self._send("lead_offset", v / 1000.0))

        self.pol_btn = QtWidgets.QPushButton("Flip polarity")
        self.pol_btn.clicked.connect(lambda: self._send("flip_polarity", None))
        controls.addWidget(self.pol_btn)
        self.pol_lbl = QtWidgets.QLabel("Polarity: Peak-Min")
        controls.addWidget(self.pol_lbl)

        self.trig_lbl = QtWidgets.QLabel("Triggers: 0")
        self.trig_lbl.setStyleSheet("font-size:16px;")
        controls.addWidget(self.trig_lbl)

        self.stop_btn = QtWidgets.QPushButton("STOP")
        self.stop_btn.setStyleSheet("background:#a33;color:white;font-weight:bold;")
        self.stop_btn.clicked.connect(self._stop)
        controls.addWidget(self.stop_btn)

        controls.addStretch(1)
        controls.addWidget(QtWidgets.QLabel("<b>Log</b>"))
        self.log_box = QtWidgets.QPlainTextEdit()
        self.log_box.setReadOnly(True)
        self.log_box.setMaximumBlockCount(400)
        self.log_box.setMinimumWidth(300)
        controls.addWidget(self.log_box, 2)

        # right: plots
        plots = pg.GraphicsLayoutWidget()
        root.addWidget(plots, 1)

        self.p_lf = plots.addPlot(row=0, col=0, title="LF magnitude")
        self.p_lf.showGrid(x=True, y=True, alpha=0.3)
        self.p_lf.setLabel("bottom", "time", "s")
        self.curve_lfr = self.p_lf.plot(pen=pg.mkPen("#4da6ff", width=1))
        self.curve_lfbase = self.p_lf.plot(pen=pg.mkPen("#888", width=1, style=QtCore.Qt.DashLine))
        self.thr_line = pg.InfiniteLine(angle=0, pen=pg.mkPen("#ff5050", style=QtCore.Qt.DotLine))
        self.p_lf.addItem(self.thr_line)

        self.p_hf = plots.addPlot(row=1, col=0, title="HF magnitude")
        self.p_hf.showGrid(x=True, y=True, alpha=0.3)
        self.p_hf.setLabel("bottom", "time", "s")
        self.p_hf.setXLink(self.p_lf)
        self.curve_hfr = self.p_hf.plot(pen=pg.mkPen("#7ee081", width=1))

        # phase scatter (bead-relative), the live version of your MATLAB plot
        self.p_ph = plots.addPlot(row=2, col=0, title="Bead-relative phase (LF vs HF)")
        self.p_ph.showGrid(x=True, y=True, alpha=0.3)
        self.p_ph.setLabel("bottom", "LF phase", "rad")
        self.p_ph.setLabel("left", "HF phase", "rad")
        self.scatter_in = pg.ScatterPlotItem(size=6, brush=pg.mkBrush("#2ecc71"))
        self.scatter_out = pg.ScatterPlotItem(size=6, brush=pg.mkBrush("#e74c3c"))
        self.p_ph.addItem(self.scatter_out)
        self.p_ph.addItem(self.scatter_in)
        self._ph_in = []   # (lf, hf) tuples
        self._ph_out = []

    def _spin(self, layout, label, val, lo, hi, step, cb):
        layout.addWidget(QtWidgets.QLabel(label))
        s = QtWidgets.QDoubleSpinBox()
        s.setRange(lo, hi); s.setSingleStep(step); s.setValue(val); s.setDecimals(3)
        s.valueChanged.connect(cb)
        layout.addWidget(s)
        return s

    # ---- command out --------------------------------------------------------
    def _send(self, name, value):
        try:
            self.cmd_q.put_nowait((name, value))
        except queue.Full:
            pass

    def _stop(self):
        self._send("stop", None)
        self.stop_btn.setEnabled(False)
        self.stop_btn.setText("Stopping...")

    # ---- periodic update ----------------------------------------------------
    def _tick(self):
        self._drain_plots()
        self._drain_events()

    def _append(self, x, lf_r, lf_base, hf_r):
        k = len(x)
        if k >= self.hist:
            self.x = x[-self.hist:]
            self.lf_r = lf_r[-self.hist:]
            self.lf_base = lf_base[-self.hist:]
            self.hf_r = hf_r[-self.hist:]
            self._filled = self.hist
            return
        self.x = np.roll(self.x, -k);       self.x[-k:] = x
        self.lf_r = np.roll(self.lf_r, -k); self.lf_r[-k:] = lf_r
        self.lf_base = np.roll(self.lf_base, -k); self.lf_base[-k:] = lf_base
        self.hf_r = np.roll(self.hf_r, -k); self.hf_r[-k:] = hf_r
        self._filled = min(self.hist, self._filled + k)

    def _drain_plots(self):
        last = None
        # collapse everything queued this tick; only the newest threshold matters
        while True:
            try:
                msg = self.plot_q.get_nowait()
            except queue.Empty:
                break
            self._append(msg["x"], msg["lf_r"], msg["lf_base"], msg["hf_r"])
            last = msg
        if self._filled == 0:
            return
        s = self.hist - self._filled
        self.curve_lfr.setData(self.x[s:], self.lf_r[s:])
        self.curve_lfbase.setData(self.x[s:], self.lf_base[s:])
        self.curve_hfr.setData(self.x[s:], self.hf_r[s:])
        if last is not None:
            self.thr_line.setValue(last["threshold"] + float(np.mean(self.lf_base[s:])))

    def _drain_events(self):
        while True:
            try:
                ev = self.event_q.get_nowait()
            except queue.Empty:
                break
            t = ev.get("type")
            if t == "log":
                self.log_box.appendPlainText(ev["msg"])
            elif t == "detect":
                pt = (ev["lf_phase"], ev["hf_phase"])
                if ev["in_window"]:
                    self._ph_in.append(pt)
                else:
                    self._ph_out.append(pt)
                self._ph_in = self._ph_in[-2000:]
                self._ph_out = self._ph_out[-2000:]
                if self._ph_in:
                    a = np.array(self._ph_in); self.scatter_in.setData(a[:, 0], a[:, 1])
                if self._ph_out:
                    a = np.array(self._ph_out); self.scatter_out.setData(a[:, 0], a[:, 1])
            elif t == "status":
                if "trigger_count" in ev:
                    self.trig_lbl.setText(f"Triggers: {ev['trigger_count']}")
                if "params" in ev and "polarity_flipped" in ev["params"]:
                    flipped = ev["params"]["polarity_flipped"]
                    self.pol_lbl.setText(f"Polarity: {'Min-Peak' if flipped else 'Peak-Min'}")
                if ev.get("finished"):
                    self.log_box.appendPlainText("[worker finished]")

    def closeEvent(self, e):
        self.stop_event.set()
        e.accept()
