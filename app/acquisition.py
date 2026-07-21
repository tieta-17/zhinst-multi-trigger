"""
acquisition.py
==============
The AcquisitionWorker owns the real-time loop. It is the ONLY thread that:
  * polls the instrument,
  * runs detection / classification,
  * fires the solenoids.

It talks to the GUI purely through three queues:
  * cmd_q   (GUI -> worker) : parameter changes, applied at the top of each loop.
  * plot_q  (worker -> GUI) : throttled, min/max-decimated traces for plotting.
  * event_q (worker -> GUI) : trigger events, log lines, status.

Golden rule: the worker NEVER blocks on a queue. plot_q uses drop-oldest so a
slow GUI can never stall triggering. All the timing-critical math is a faithful
port of your original loop.
"""

import os
import time
import queue
import threading
import numpy as np

import config
from core import (RollingBuffer, window_mean, complex_at, nearest_by_time,
                  minmax_decimate)


def _put_drop_oldest(q, item):
    """Non-blocking put that drops the oldest item if the queue is full."""
    try:
        q.put_nowait(item)
    except queue.Full:
        try:
            q.get_nowait()
        except queue.Empty:
            pass
        try:
            q.put_nowait(item)
        except queue.Full:
            pass


class AcquisitionWorker(threading.Thread):
    def __init__(self, instrument, gpio, params, cmd_q, plot_q, event_q, stop_event):
        super().__init__(daemon=True, name="AcquisitionWorker")
        self.instr = instrument
        self.hw = gpio
        self.p = params
        self.cmd_q = cmd_q
        self.plot_q = plot_q
        self.event_q = event_q
        self.stop_event = stop_event

        self.CLOCKBASE = instrument.clockbase()
        self.MFIA_CLK_PERIOD = 1.0 / self.CLOCKBASE

        # rolling buffers
        n = config.NUM_SUB_BUFFERS
        self.lf_rb_x = RollingBuffer(n); self.lf_rb_y = RollingBuffer(n)
        self.lf_rb_r = RollingBuffer(n); self.lf_rb_phase = RollingBuffer(n)
        self.hf_rb_x = RollingBuffer(n); self.hf_rb_y = RollingBuffer(n)
        self.hf_rb_r = RollingBuffer(n); self.hf_rb_phase = RollingBuffer(n)
        self.lf_rb_ts = RollingBuffer(n); self.hf_rb_ts = RollingBuffer(n)
        self.rb_aux = RollingBuffer(n)

        # baselines (the live complex high-pass)
        self.lf_base_hist = np.empty(0, dtype=complex)
        self.hf_base_hist = np.empty(0, dtype=complex)
        self.lf_r_hist = np.empty(0)
        self.lf_baseline_complex = 0 + 0j
        self.hf_baseline_complex = 0 + 0j

        # rotations derived from calibration means
        self.LF_ROTATION = np.exp(-1j * self.p.lf_bead_phase_mean)
        self.HF_ROTATION = np.exp(-1j * self.p.hf_bead_phase_mean)

        # state
        self.last_activated_state = 0
        self.prev_timestamps = [0, 0]
        self.prev_peak_voltage_dif = 0
        self.last_trigger_timestamp = 0
        self.trigger_count = 0
        self.time_sync = [time.time(), 0]
        self.last_time_synced = time.time()
        self.clk_sync_ready = False
        self.min_val = 1e12
        self.t0_tick = None

        # plot accumulation (decimated at flush)
        self._acc_ts = []; self._acc_lfr = []; self._acc_hfr = []
        self._acc_lfbase = []
        self._last_plot_emit = 0.0

        os.makedirs(config.SNAPSHOT_FILE_PATH, exist_ok=True)

    # ---- small emit helpers -------------------------------------------------
    def log(self, msg):
        _put_drop_oldest(self.event_q, {"type": "log", "msg": str(msg)})

    def status(self, **kw):
        kw["type"] = "status"
        _put_drop_oldest(self.event_q, kw)

    # ---- command handling ---------------------------------------------------
    def _drain_commands(self):
        while True:
            try:
                name, value = self.cmd_q.get_nowait()
            except queue.Empty:
                return
            if name == "stop":
                self.stop_event.set()
                return
            desc = self.p.apply_command(name, value)
            if desc:
                self.log(f"[cmd] {desc}")
                self.status(params=self._params_snapshot())

    def _params_snapshot(self):
        p = self.p
        return {
            "threshold": p.max_voltage_threshold,
            "polarity_flipped": p.polarity_flipped,
            "debounce": p.debounce_period,
            "solenoid_1_duration": p.solenoid_1_duration,
            "solenoid_2_duration": p.solenoid_2_duration,
            "solenoid_pair_delay": p.solenoid_pair_delay,
            "lead_offset": p.trigger_lead_time_offset,
        }

    # ---- GPIO / trigger machinery (ported) ----------------------------------
    def _trigger_pin(self, pin, pulse_duration=0.05):
        self.hw.output(pin, True)
        time.sleep(pulse_duration)
        self.hw.output(pin, False)

    def _after(self, delay, fn):
        threading.Timer(max(delay, 0.0), fn).start()

    def _calibrate_time_sync(self):
        self.hw.output(config.CLK_SYNC_PIN, True)
        self.last_time_synced = time.time()
        time.sleep(0.0001)
        self.hw.output(config.CLK_SYNC_PIN, False)
        # in sim mode the instrument can emit a matching aux pulse
        if hasattr(self.instr, "pulse_clock"):
            self.instr.pulse_clock()

    @staticmethod
    def _calibrated_timestamp(pulse_signal, timestamps):
        for i in range(len(pulse_signal)):
            if pulse_signal[i] > 0.05:
                return timestamps[i]
        return 0

    def _calc_delay_and_trigger(self, peak_time_dif, pin, peak_ts, pulse_duration=0.05, extra_delay=0.0):
        timestamp_difference = peak_ts - self.time_sync[1]
        instrument_time_difference = self.MFIA_CLK_PERIOD * timestamp_difference
        system_time_difference = time.time() - self.time_sync[0]
        peak_current_time_dif = (system_time_difference - instrument_time_difference
                                 + config.INST_CLK_SYNC_DELAY)
        trigger_delay = (0.220 + self.p.trigger_lead_time_offset - peak_current_time_dif
                         - config.INST_SAMPLE_DELAY + extra_delay)
        self._after(trigger_delay, lambda: self._trigger_pin(pin, pulse_duration))
        self.min_val = min(self.min_val, trigger_delay)

    def _save_snapshot(self, lf_r_window, t_window, hf_windows, detection):
        """Now saves BOTH channels + indices + baselines so an event can be
        replayed offline (the gap the postdoc flagged)."""
        hf_x_w, hf_y_w, hf_t_w, hf_detect = hf_windows
        fn = os.path.join(config.SNAPSHOT_FILE_PATH, f"{time.time()}_snapshot.npz")
        np.savez(fn,
                 lf_x=self.lf_rb_x.get_x_buffers(config.NUM_FRAMES),
                 lf_y=self.lf_rb_y.get_x_buffers(config.NUM_FRAMES),
                 lf_r=lf_r_window, lf_t=t_window,
                 hf_x=hf_x_w, hf_y=hf_y_w, hf_t=hf_t_w,
                 lf_detect=detection, hf_detect=hf_detect,
                 lf_baseline=self.lf_baseline_complex,
                 hf_baseline=self.hf_baseline_complex)

    # ---- plotting -----------------------------------------------------------
    def _accumulate_plot(self, ts, lf_r, hf_r):
        if self.t0_tick is None:
            self.t0_tick = ts[0]
        self._acc_ts.append(ts)
        self._acc_lfr.append(lf_r)
        self._acc_hfr.append(hf_r)
        self._acc_lfbase.append(np.full(len(ts), np.abs(self.lf_baseline_complex)))

    def _maybe_emit_plot(self):
        now = time.time()
        if now - self._last_plot_emit < 1.0 / config.PLOT_FPS:
            return
        if not self._acc_ts:
            return
        self._last_plot_emit = now
        ts = np.concatenate(self._acc_ts)
        lfr = np.concatenate(self._acc_lfr)
        hfr = np.concatenate(self._acc_hfr)
        lfb = np.concatenate(self._acc_lfbase)
        self._acc_ts.clear(); self._acc_lfr.clear()
        self._acc_hfr.clear(); self._acc_lfbase.clear()

        blk = config.PLOT_DECIMATE_BLOCK
        t_rel = (ts.astype(np.float64) - float(self.t0_tick)) * self.MFIA_CLK_PERIOD
        # block-center timestamps, duplicated to match the min/max pair count
        n = len(t_rel)
        npad = (-n) % blk
        t_pad = np.concatenate([t_rel, np.full(npad, t_rel[-1])]) if npad else t_rel
        centers = t_pad.reshape(-1, blk).mean(axis=1)
        x = np.repeat(centers, 2)
        _put_drop_oldest(self.plot_q, {
            "x": x,
            "lf_r": minmax_decimate(lfr, blk),
            "hf_r": minmax_decimate(hfr, blk),
            "lf_base": minmax_decimate(lfb, blk),
            "threshold": self.p.max_voltage_threshold,
        })

    # ---- main loop ----------------------------------------------------------
    def run(self):
        self.log("Acquisition started")
        start_time = time.time()
        self._after(0.1, self._calibrate_time_sync)

        for i in range(config.NUM_LOOPS):
            if self.stop_event.is_set():
                break
            self._drain_commands()

            if ((i + 1) % 500) == 0:
                self._after(0, self._calibrate_time_sync)
                self.clk_sync_ready = True

            try:
                pr = self.instr.poll(config.POLL_TIME)
            except Exception as e:
                self.log(f"[poll error] {e}")
                continue

            triggered = False
            try:
                if len(pr.lf_x) < 10 or len(pr.hf_x) < 10:
                    raise Exception("No poll data available")
                # sanity: flag gross LF/HF length mismatch (alignment guard)
                if abs(len(pr.lf_x) - len(pr.hf_x)) > 2:
                    self.log(f"[warn] LF/HF length mismatch {len(pr.lf_x)} vs {len(pr.hf_x)}")

                # ---- store LF ----
                lf_r = np.hypot(pr.lf_x, pr.lf_y)
                lf_complex = pr.lf_x + 1j * pr.lf_y
                self.lf_rb_x.add_sub_buffer(pr.lf_x); self.lf_rb_y.add_sub_buffer(pr.lf_y)
                self.lf_rb_r.add_sub_buffer(lf_r)
                self.lf_rb_phase.add_sub_buffer(np.angle(lf_complex))
                self.lf_rb_ts.add_sub_buffer(pr.lf_ts)
                self.rb_aux.add_sub_buffer(pr.aux)

                # ---- store HF ----
                hf_r = np.hypot(pr.hf_x, pr.hf_y)
                hf_complex = pr.hf_x + 1j * pr.hf_y
                self.hf_rb_x.add_sub_buffer(pr.hf_x); self.hf_rb_y.add_sub_buffer(pr.hf_y)
                self.hf_rb_r.add_sub_buffer(hf_r)
                self.hf_rb_phase.add_sub_buffer(np.angle(hf_complex))
                self.hf_rb_ts.add_sub_buffer(pr.hf_ts)

                # ---- complex baseline (live high-pass) ----
                self.lf_base_hist = np.append(self.lf_base_hist, np.mean(lf_complex))
                self.hf_base_hist = np.append(self.hf_base_hist, np.mean(hf_complex))
                self.lf_r_hist = np.append(self.lf_r_hist, np.mean(lf_r))
                if i > config.BASELINE_WINDOW:
                    self.lf_base_hist = self.lf_base_hist[1:]
                    self.hf_base_hist = self.hf_base_hist[1:]
                    self.lf_r_hist = self.lf_r_hist[1:]
                self.lf_baseline_complex = np.mean(self.lf_base_hist)
                self.hf_baseline_complex = np.mean(self.hf_base_hist)

                # ---- clock sync ----
                if self.clk_sync_ready:
                    cal_ts = self._calibrated_timestamp(
                        self.rb_aux.get_x_buffers(2), self.lf_rb_ts.get_x_buffers(2))
                    if cal_ts != 0:
                        self.time_sync = [self.last_time_synced, cal_ts]
                        self.clk_sync_ready = False

                self._accumulate_plot(pr.lf_ts, lf_r, hf_r)
            except Exception as e:
                continue

            # ---- windows ----
            lf_r_window = self.lf_rb_r.get_x_buffers(config.NUM_FRAMES)
            t_window = self.lf_rb_ts.get_x_buffers(config.NUM_FRAMES)
            lf_phase_window = self.lf_rb_phase.get_x_buffers(config.NUM_FRAMES)
            hf_phase_window = self.hf_rb_phase.get_x_buffers(config.NUM_FRAMES)
            lf_x_window = self.lf_rb_x.get_x_buffers(config.NUM_FRAMES)
            lf_y_window = self.lf_rb_y.get_x_buffers(config.NUM_FRAMES)
            hf_x_window = self.hf_rb_x.get_x_buffers(config.NUM_FRAMES)
            hf_y_window = self.hf_rb_y.get_x_buffers(config.NUM_FRAMES)
            hf_t_window = self.hf_rb_ts.get_x_buffers(config.NUM_FRAMES)

            max_index = int(np.argmax(lf_r_window))
            min_index = int(np.argmin(lf_r_window))
            lf_baseline = np.mean(self.lf_r_hist) if len(self.lf_r_hist) else 0.0

            if not self.p.polarity_flipped:
                is_breached = lf_r_window[max_index] - lf_baseline > self.p.max_voltage_threshold
                peak_time_dif = (t_window[min_index] - t_window[max_index]) * self.MFIA_CLK_PERIOD
                leading_peak_time = t_window[max_index]
                size_detection_index = max_index
            else:
                is_breached = lf_baseline - lf_r_window[min_index] > self.p.max_voltage_threshold
                peak_time_dif = (t_window[max_index] - t_window[min_index]) * self.MFIA_CLK_PERIOD
                leading_peak_time = t_window[min_index]
                size_detection_index = min_index

            current_timestamp = leading_peak_time

            if is_breached:
                if self.last_activated_state == 8:
                    hf_detect_snap = nearest_by_time(hf_t_window, t_window[size_detection_index])
                    self._after(0, lambda lrw=lf_r_window.copy(), tw=t_window.copy():
                                self._save_snapshot(lrw, tw,
                                    (hf_x_window.copy(), hf_y_window.copy(),
                                     hf_t_window.copy(), hf_detect_snap),
                                    size_detection_index))
                self.last_activated_state += 1

                peak_voltage_dif = abs(lf_r_window[max_index] - lf_r_window[min_index])

                if (peak_voltage_dif > self.p.max_voltage_threshold and peak_time_dif > 0
                        and t_window[min_index] > self.prev_timestamps[0]
                        and t_window[max_index] > self.prev_timestamps[0]
                        and self.prev_peak_voltage_dif >= peak_voltage_dif):

                    if (current_timestamp - self.last_trigger_timestamp) * self.MFIA_CLK_PERIOD < self.p.debounce_period:
                        continue

                    self.prev_timestamps = [t_window[min_index], t_window[max_index]]

                    # HF matched in TIME, not index
                    hf_detect = nearest_by_time(hf_t_window, t_window[size_detection_index])

                    z_lf = complex_at(lf_x_window, lf_y_window, size_detection_index)
                    z_hf = complex_at(hf_x_window, hf_y_window, hf_detect)
                    z_lf -= self.lf_baseline_complex
                    z_hf -= self.hf_baseline_complex

                    lf_phase_centered = np.angle(z_lf * self.LF_ROTATION)
                    hf_phase_centered = np.angle(z_hf * self.HF_ROTATION)

                    is_lf_in = self.p.lf_phase_range[0] <= lf_phase_centered <= self.p.lf_phase_range[1]
                    is_hf_in = self.p.hf_phase_range[0] <= hf_phase_centered <= self.p.hf_phase_range[1]

                    _put_drop_oldest(self.event_q, {
                        "type": "detect",
                        "t": (leading_peak_time - (self.t0_tick or leading_peak_time)) * self.MFIA_CLK_PERIOD,
                        "lf_phase": float(lf_phase_centered),
                        "hf_phase": float(hf_phase_centered),
                        "vdiff": float(peak_voltage_dif),
                        "in_window": bool(is_lf_in and is_hf_in),
                    })

                    if is_lf_in and is_hf_in:
                        self.trigger_count += 1
                        self.last_trigger_timestamp = current_timestamp
                        self._calc_delay_and_trigger(peak_time_dif, config.SOLENOID_PIN_1,
                                                     leading_peak_time, self.p.solenoid_1_duration)
                        self._calc_delay_and_trigger(peak_time_dif, config.SOLENOID_PIN_2,
                                                     leading_peak_time, self.p.solenoid_2_duration,
                                                     extra_delay=self.p.solenoid_pair_delay)
                        self.status(trigger_count=self.trigger_count,
                                    vdiff=float(peak_voltage_dif),
                                    tdiff=float(peak_time_dif))
                        self.log(f"TRIGGER #{self.trigger_count}  "
                                 f"Vdiff={peak_voltage_dif*1000:.4f} mV  "
                                 f"LFph={lf_phase_centered:+.3f} HFph={hf_phase_centered:+.3f}")
                        triggered = True
                        self.prev_peak_voltage_dif = peak_voltage_dif

                if not triggered:
                    self.prev_peak_voltage_dif = peak_voltage_dif
            else:
                triggered = False
                self.prev_peak_voltage_dif = 0
                self.last_activated_state = 0

            self._maybe_emit_plot()

        self.instr.cleanup()
        self.log(f"Acquisition stopped after {time.time()-start_time:.1f} s, "
                 f"{self.trigger_count} triggers")
        self.status(finished=True)
