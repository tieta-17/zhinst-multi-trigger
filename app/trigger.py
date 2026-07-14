import RPi.GPIO as GPIO
import time
import threading
import numpy as np
from settings import InstrumentSettings

class TriggerController():
    def __init__(self, settings):
        self.settings = settings
        self.time_sync = [0.0, 0.0]
        self.min_trigger_delay = float("inf")
        
        GPIO.setmode(GPIO.BCM)

        SOLENOID_PIN_1 = 17
        GPIO.setup(SOLENOID_PIN_1, GPIO.OUT)
        GPIO.output(SOLENOID_PIN_1, GPIO.LOW)

        SOLENOID_PIN_2 = 23
        GPIO.setup(SOLENOID_PIN_2, GPIO.OUT)
        GPIO.output(SOLENOID_PIN_2, GPIO.LOW)
        
        PIN27 = 27
        GPIO.setup(PIN27, GPIO.OUT)
        GPIO.output(PIN27, GPIO.LOW)

        CLK_SYNC_PIN = 22
        GPIO.setup(CLK_SYNC_PIN, GPIO.OUT)
        GPIO.output(CLK_SYNC_PIN, GPIO.LOW)


    def fire(self, pin, pulse_duration = 0.05):
        # Trigger whatever is needed
        # print("triggered")
        GPIO.output(pin, GPIO.HIGH)
        if (pin == 17):
            print(f"triggered at: {time.time()}")
        time.sleep(pulse_duration)
        GPIO.output(pin, GPIO.LOW)

    def run_function_after_delay(self, delay, function):
        timer = threading.Timer(delay, function)
        timer.start()

    def calculate_delay_and_trigger(self, peak_time_dif, pin, peak_timestamp, pulse_duration = 0.05, extra_delay = 0.0):
        
        timestamp_difference = peak_timestamp - self.time_sync[1]
        instrument_time_difference = MFIA_CLK_PERIOD * timestamp_difference
        print(f"peak_timestamp: {peak_timestamp:.3f} ms, time_sync: {self.time_sync[1]:.3f} ms")
        current_time = time.time()
        system_time_difference = current_time - self.time_sync[0]
        
        peak_current_time_dif = system_time_difference - instrument_time_difference + self.settings.inst_clk_sync_delay

        # Derive this bead's travel time to the actuation zone from its OWN measured
        # transit time across the detection window (peak_time_dif), scaled by the
        # distance ratio, instead of assuming a fixed flow rate. This self-corrects
        # for flow rate drift and per-bead velocity variation automatically.

        # extra_delay is to account for solenoid 1 and 2 triggering at different times
        # trigger_lead_time = peak_time_dif * DISTANCE_RATIO
        trigger_delay = 0.220 + self.settings.trigger_offset - peak_current_time_dif - self.settings.inst_sample_delay + extra_delay #TRIGGER_DELAY_SCALE*peak_time_dif - peak_current_time_dif 
        self.run_function_after_delay(trigger_delay, lambda: self.fire(pin, pulse_duration))
        
        print(f"inst t dif: {instrument_time_difference * 1000:.3f} ms, sys t dif: {system_time_difference * 1000:.3f} ms, peak_current_time_dif = {peak_current_time_dif * 1000:.3f} ms, trigger_delay = {trigger_delay * 1000:.3f} ms, current time = {time.time():.3f}")
        self.min_trigger_delay = np.min([self.min_trigger_delay, trigger_delay])
        print(f"min val: {self.min_trigger_delay * 1000:.3f} ms")