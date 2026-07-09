from zhinst.toolkit import Session
import numpy as np
import time
import RPi.GPIO as GPIO
import matplotlib.pyplot as plt
from matplotlib.animation import FuncAnimation
from zhinst.core import ziDAQServer
import threading
import json
import sys

# MAX_MIN_VOLTAGE_THRESHOLD = 0.00003 # Threshold for triggering based on the distance between the maximum and minimum voltage
# MAX_VOLTAGE_THRESHOLD     = MAX_MIN_VOLTAGE_THRESHOLD / 2 # Threshold for triggering based on the maximum voltage. The voltage must reach this level to trigger. SAME AS LF_BEAD_PEAK_VOLTAGE
SAMPLING_RATE = 55100 # (Hz) (Will go to nearest rate choosen)
POLL_TIME = 0.0 # Actual poll time is this number + loop delay. Leave this at 0 for fastest polling. Any value below 0.02 will result in some frames having only one value.
SNAPSHOT_FILE_PATH = "./snapshots/" # folder where to store the snapshots
TRIGGER_DELAY_SCALE = 0 # TRIGGER_DELAY_SCALE * time between peaks = total delay from the end of negative peak
NUM_LOOPS = 5000000 # Number of loops to run. Set this to a really high number to run the program for a long time
NUM_FRAMES = 12 # This is the number of frames that the program looks at when runnning the threshold calculations. 
# NUM_FRAMES * actual poll time per loop should be greater than the length of signal component of interest
NUM_SUB_BUFFERS = 5000 # This sets the number of frames that each circular buffer holds. Do not set to less than 100.
INST_SAMPLE_DELAY = 0.0035
INST_CLK_SYNC_DELAY = 0.001
POLARITY_FLIPPED = False # Normal polarity means peak comes before min, flipped means min comes before peak


min_val = 9999999999
# Create directory to store snapshots
try:
    os.mkdir(SNAPSHOT_FILE_PATH)
except:
    # path already exists, do nothing
    pass

# Calibration functions
def param_calibration():
    file_path = input("Enter path to JSON calibration file: ")
    config = read_calibration_file(file_path)
    
    # If read_calibration_file returned None, it means an error occurred
    if config is None:
        print("Error in reading calibration file. Exiting.")
        sys.exit(1)
        
    return config

def read_calibration_file(file_path):
    try:
        with open(file_path, 'r', encoding='utf-8') as file:
            data = json.load(file)
            
            # Pack everything neatly into a configuration dictionary
            config = {
                "HF_BEAD_PHASE_MEAN": data['high_frequency_hf']['bead_phase_mean'],
                "HF_PHASE_RANGE": data['high_frequency_hf']['phase_range_box'],
                "LF_BEAD_PHASE_MEAN": data['low_frequency_lf']['bead_phase_mean'],
                "LF_PHASE_RANGE": data['low_frequency_lf']['phase_range_box'],
                "LF_BASELINE_PEAK_VOLTAGE": data['low_frequency_lf']['baseline_to_peak_voltage'], # Fixed typo here
                "BEAD_SIZE_UM": data['calibration_metadata']['bead_size_um']
            }
            return config
            
    except FileNotFoundError:
        print(f"Error: The file '{file_path}' could not be found.")
    except json.JSONDecodeError as e:
        print("Error: Invalid JSON syntax:", e)
    except KeyError as e:
        print(f"Error: Missing expected configuration key in JSON: {e}")
    except Exception as e:
        print(f"An unexpected error occurred: {e}")
        
    return None

config = param_calibration()
BEAD_SIZE_UM = config["BEAD_SIZE_UM"]
HF_BEAD_PHASE_MEAN = config["HF_BEAD_PHASE_MEAN"]
HF_PHASE_RANGE = config["HF_PHASE_RANGE"]
LF_BEAD_PHASE_MEAN = config["LF_BEAD_PHASE_MEAN"]
LF_PHASE_RANGE = config["LF_PHASE_RANGE"]
LF_BASELINE_PEAK_VOLTAGE = config["LF_BASELINE_PEAK_VOLTAGE"]

MAX_VOLTAGE_THRESHOLD = LF_BASELINE_PEAK_VOLTAGE


# input/output detection and commands
# input/output detection and commands
def print_controls():
    print("Enter 'p' to flip polarity\n" \
        "Enter 't' followed by a number to set threshold (mV)\n" \
        "Enter 'd' followed by a number to set solenoid delay (ms)\n" \
        "Enter 'd' followed by 1 or 2 followed by a number to set solenoid (1,2) duration (ms)\n" \
        "Enter 'l' followed by a number to set trigger lead time OFFSET (ms) -- a fixed correction on top of the per-bead measured travel time\n" \
        "Enter 'help' to view commands again")
def asynch_keyboard_listener():
    print_controls()
    while True:
        # polls stdin for input, waiting for newline to read input
        line = sys.stdin.readline().strip()
        # passes command over to function to handle input
        handle_commands(line)

def handle_commands(line):
    global MAX_VOLTAGE_THRESHOLD, POLARITY_FLIPPED, SOLENOID_PAIR_DELAY, SOLENOID_1_DURATION, SOLENOID_2_DURATION, TRIGGER_LEAD_TIME_OFFSET

    if line.lower() == "p":
        POLARITY_FLIPPED = not POLARITY_FLIPPED
        print(f"Polarity Flipped!\nCurrent: {'Min-Peak' if POLARITY_FLIPPED else 'Peak-Min'}")
    
    if line.lower().startswith("t"):
        try:
            _, val = line.split()
            MAX_VOLTAGE_THRESHOLD = float(val) / 1000
            print(f"Changed Threshold!\nCurrent: {val} mV")
        except:
            print('Invalid Sequence\nEnsure input format follows "t ___"')
    
    if line.lower().startswith("d"):
        try:
            # d {time} --> sets delay {time} between solenoid triggers (ms)
            # d {num} {time} --> sets duration of solenoid {num} to {time} (ms)
            parts = line.split()
            if len(parts) == 2:
                SOLENOID_PAIR_DELAY = float(parts[1]) / 1000
                print(f"Changed solenoid trigger delay!\nCurrent: {parts[1]} ms")
            elif len(parts) == 3:
                solenoid_id = int(parts[1])
                duration = float(parts[2])

                match solenoid_id:
                    case 1:
                        SOLENOID_1_DURATION = duration / 1000
                    case 2: 
                        SOLENOID_2_DURATION = duration / 1000
                    case _:
                        return "Invalid Solenoid ID"
                
                print(f"Changed solenoid {solenoid_id} duration to {duration} ms")
                    
        except:
            print('Invalid Sequence\nEnsure input format follows "d ___"')

    if line.lower().startswith("l"):
        try:
            # l {time} --> sets TRIGGER_LEAD_TIME_OFFSET (ms), the manual fixed-latency
            # correction added on top of the per-bead-derived trigger_lead_time
            _, val = line.split()
            TRIGGER_LEAD_TIME_OFFSET = float(val) / 1000
            print(f"Changed trigger lead time offset!\nCurrent: {val} ms")
        except:
            print('Invalid Sequence\nEnsure input format follows "l ___"')
    
    if line.lower() == "help":
        print_controls()


# Delay and Trigger functions
# pulse_duration is how long the pin stays HIGH
def trigger_function(pin, pulse_duration = 0.05):
    # Trigger whatever is needed
    # print("triggered")
    GPIO.output(pin, GPIO.HIGH)
    if (pin == 17):
        print(f"triggered at: {time.time()}")
    time.sleep(pulse_duration)
    GPIO.output(pin, GPIO.LOW)

def run_function_after_delay(delay, function):
    timer = threading.Timer(delay, function)
    timer.start()

def calculate_delay_and_trigger(peak_time_dif, pin, peak_timestamp, pulse_duration = 0.05, extra_delay = 0.0):
    
    timestamp_difference = peak_timestamp - time_sync[1]
    instrument_time_difference = MFIA_CLK_PERIOD * timestamp_difference
    print(f"peak_timestamp: {peak_timestamp:.3f} ms, time_sync: {time_sync[1]:.3f} ms")
    current_time = time.time()
    system_time_difference = current_time - time_sync[0]
    
    peak_current_time_dif = system_time_difference - instrument_time_difference + INST_CLK_SYNC_DELAY

    # SOLENOID_PAIR_DELAY is time between triggering solenoid 1 and solenoid 2

    # Derive this bead's travel time to the actuation zone from its OWN measured
    # transit time across the detection window (peak_time_dif), scaled by the
    # distance ratio, instead of assuming a fixed flow rate. This self-corrects
    # for flow rate drift and per-bead velocity variation automatically.

    # extra_delay is to account for solenoid 1 and 2 triggering at different times
    trigger_lead_time = peak_time_dif * DISTANCE_RATIO
    trigger_delay = trigger_lead_time + TRIGGER_LEAD_TIME_OFFSET - peak_current_time_dif - INST_SAMPLE_DELAY + extra_delay #TRIGGER_DELAY_SCALE*peak_time_dif - peak_current_time_dif 
    run_function_after_delay(trigger_delay, lambda: trigger_function(pin, pulse_duration))
    
    print(f"inst t dif: {instrument_time_difference * 1000:.3f} ms, sys t dif: {system_time_difference * 1000:.3f} ms, peak_current_time_dif = {peak_current_time_dif * 1000:.3f} ms, trigger_delay = {trigger_delay * 1000:.3f} ms, current time = {time.time():.3f}")
    global min_val
    min_val = np.min([min_val, trigger_delay])
    print(f"min val: {min_val * 1000:.3f} ms")
    
def save_snapshot(r_window, t_window):
    data = np.column_stack((rb_x.get_x_buffers(NUM_FRAMES), rb_y.get_x_buffers(NUM_FRAMES), r_window, t_window))
    np.savetxt(SNAPSHOT_FILE_PATH+ str(time.time()) +"snapshot.csv", data, delimiter=",")


# Instrument Sync Functions

def calibrate_time_sync():
    GPIO.output(CLK_SYNC_PIN, GPIO.HIGH)
    global last_time_synced
    last_time_synced = time.time()
    time.sleep(0.0001)
    GPIO.output(CLK_SYNC_PIN, GPIO.LOW)

def get_calibrated_timestamp(pulse_signal, timestamps):
    for i in range(len(pulse_signal)):
        if (pulse_signal[i] > 0.05):
            # print(f"i: {i}, max signal: {np.max(pulse_signal)}")
            return timestamps[i]
    return 0

class rolling_buffer:
    def __init__(self, buffer_length):
        self.buffer_length = buffer_length;
        self.buffers = []
        self.write_index = 0;
        for i in range(0, self.buffer_length):
            self.buffers.append(np.zeros(1))
    
    def add_sub_buffer(self, sub_buffer):
        self.buffers[self.write_index] = sub_buffer;
        self.write_index += 1;
        if(self.write_index == self.buffer_length):
            self.write_index = 0;
            
    def get_full_buffer(self):
        ret_array = np.empty(0)
        for i in range(self.write_index, self.write_index + self.buffer_length):
            n = i % self.buffer_length;
            ret_array = np.append(ret_array, self.buffers[n])
        return ret_array
    def get_x_buffers(self, x):
        ret_array = np.empty(0)
        for i in range(self.write_index - x, self.write_index):
            if(i < 0):
                n = self.buffer_length + i
            else:
                n = i
            ret_array = np.append(ret_array, self.buffers[n])
        return ret_array
        
    def print_full(self):
        print("\nbuffer length:", self.buffer_length)
        print("write index:", self.write_index)
        print(self.buffers,"\n")
        

print("hello world")

# Initialize connection to the device
session = Session("localhost", hf2=True)
device = session.connect_device("dev1051")

MFIA_CLK_PERIOD = 1 / device.clockbase()
CLOCKBASE = device.clockbase()


print(list(device.demods[0].child_nodes(recursive=True, leavesonly=True)))

# Setup parameters for Instrument
device.demods[0].enable(True)
device.demods[0].adcselect(0) # Set to Signal Voltage Input
# device.imps[0].enable(0) # Hopefully turn off measurement control
device.demods[0].rate(SAMPLING_RATE) # Set Sampling Rate


time.sleep(0.1)
print("Sampling Rate = ",device.demods[0].rate())
SAMPLING_RATE = device.demods[0].rate()
print("Frequency = ",device.demods[0].freq())


# Initalize GPIO
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

# SOLENOID_PAIR_DELAY is the time between triggering solenoid 1 and solenoid 2
SOLENOID_PAIR_DELAY = 0.05

# SOLENOID_{num}_DELAY is the time that each solenoid stays HIGH (clamped)
SOLENOID_1_DURATION = 0.06
SOLENOID_2_DURATION = 0.03

device.demods[0].sample.subscribe()
time.sleep(0.0005)
num_samples = 0

last_time_synced = 1.2

# Initial Sync of RPi time and instrument timestamp
run_function_after_delay(0.1, calibrate_time_sync)
poll_result = session.poll(recording_time=1)

aux_signal = poll_result[device.demods[0].sample]["auxin0"]
timestamps = poll_result[device.demods[0].sample]["timestamp"]

time_sync = [last_time_synced, get_calibrated_timestamp(aux_signal, timestamps)]
        
print("time_sync:", time_sync)


# --------testing sample data-------------
# sample_data = np.loadtxt("Data_Impedance.txt")
# sample_index = 0
# sample_bin_length = np.random.randint(1000,2200)
# sample_timestamp = np.linspace(0, 20, len(sample_data))

# Buffer Setup
rb_x 			= rolling_buffer(buffer_length = NUM_SUB_BUFFERS)
rb_y 			= rolling_buffer(buffer_length = NUM_SUB_BUFFERS)
rb_r 			= rolling_buffer(buffer_length = NUM_SUB_BUFFERS)
rb_timestamp 	= rolling_buffer(buffer_length = NUM_SUB_BUFFERS)
rb_auxin0		= rolling_buffer(buffer_length = NUM_SUB_BUFFERS)
rolling_avg 	= np.empty(0)

# Real time plot setup
# plt.ion()
# fig = plt.figure()
# ax = fig.add_subplot(111)
# plt.ylim([-1,1])
# plt.xlim([100000,140000])
# xxx = np.zeros(1)
# line, = ax.plot(rb_r.get_x_buffers(120))

# Holds how many loops the function has stayed above the max threshold
last_activated_state = 0
# Checks if max/min values have already been used
prev_timestamps = [0,0]
prev_peak_voltage_dif = 0
# Is true when there is an error with polling
poll_error = False
# Clock sync ready signal
clk_sync_ready = False

print("Ready to Receive Data!")
asynch_keyboard_listener()

start_time = time.time()

# timing constants
# holds the timestamp in which the last trigger occured
last_trigger_timestamp  = 0

# DEBOUNCE_PERIOD is the minimum time between triggers. Any trigger less than DEBOUNCE_PERIOD is void
DEBOUNCE_PERIOD = 0.100 # 100ms debounce period

# DETECTION_TO_ACTUATION_UM is the distance from detection from the sensors to actuation with the solenoids
# DETECTION_WINDOW_UM is the distance of the sensors
# These parameters are provided by the user

DETECTION_TO_ACTUATION_UM = 5450
DETECTION_WINDOW_UM = 200
DISTANCE_RATIO = DETECTION_TO_ACTUATION_UM / DETECTION_WINDOW_UM


# TRIGGER_LEAD_TIME_OFFSET is for latencies that do NOT scale with flow rate
# (GPIO edge delay, cabling, mechanical solenoid opening lag) -- tune this with
# the 'l' command while watching % deflection. NOTE: the old hardcoded value here
# was 0.07s;
TRIGGER_LEAD_TIME_OFFSET = 0.0

trigger_count = 0

# main loop
for i in range(NUM_LOOPS):
    
    if i > 0 and (time.time()-start_loop_time) * 1000 > 75:
        print(f"loop {i-1} time = {((time.time()-start_loop_time) * 1000):.3f} ms") 
        print("\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n")
        
    start_loop_time = time.time()
    
    # Sync the raspberry pi to the instrument every loop to prevent clock drift 
    # (i+1) % N == 0 means that this will sync every N loops
    if (((i+1) % 500) == 0):
        # print(f"Syncing Clocks. Time last synced: {last_time_synced}")
        run_function_after_delay(0, calibrate_time_sync)
        clk_sync_ready = True
        
    poll_result = session.poll(recording_time=POLL_TIME)
        
    run_function_after_delay(0, lambda: trigger_function(PIN27))

    loop_time = time.time()
    # if i > 0:
        # print(f"loop {i} after polling = {((time.time()) * 1000 % 10000):.3f} ms") 
    try:
        # store data from Lock-In Amplifier
        x_signal 	= poll_result[device.demods[0].sample]["x"]
        y_signal 	= poll_result[device.demods[0].sample]["y"]
        timestamps 	= poll_result[device.demods[0].sample]["timestamp"]
        aux_signal 	= poll_result[device.demods[0].sample]["auxin0"]

        # Check if there was any actual data to be polled
        if (len(x_signal) < 10):
            raise Exception("No poll data available")
            
        
        rb_timestamp.add_sub_buffer(timestamps)
        rb_x.add_sub_buffer(x_signal)
        rb_y.add_sub_buffer(y_signal)
        r = np.hypot(x_signal, y_signal)
        rb_r.add_sub_buffer(r)
        rb_auxin0.add_sub_buffer(aux_signal)
        
        # Check for a clock sync signal
        if clk_sync_ready == True:
            cal_ts = get_calibrated_timestamp(rb_auxin0.get_x_buffers(2), rb_timestamp.get_x_buffers(2))
            # Check if the peak is actually in the buffer and return the timestamp
            if (cal_ts != 0):
                time_sync = [last_time_synced, cal_ts]
                clk_sync_ready = False
                # print("Clocks Synced")
            
        # take the average of the last 100 frames. This removes the DC offset from the recieved data.
        rolling_avg = np.append(rolling_avg, np.mean(r))
        if (i > 100):
            rolling_avg = rolling_avg[1:]
        poll_error = False
        
    except Exception as e:
        poll_error = True
        # print("Failed at loop number:", i, e)
        # print(poll_result[device.demods[0].sample]["y"])
        continue
    
    # threshold check block
    r_window = rb_r.get_x_buffers(NUM_FRAMES)
    max_index = np.argmax(r_window) # index of positive peak
    min_index = np.argmin(r_window) # index of negative peak
    t_window = rb_timestamp.get_x_buffers(NUM_FRAMES)
    
    # if i > 0:
        # print(f"loop {i} after rolling buffer assignment = {((time.time()) * 1000 % 10000):.3f} ms") 

    # polarity conditions
    is_threshold_breached = False
    baseline = np.mean(rolling_avg)

    if not POLARITY_FLIPPED: # max comes before min (normal)
        # normal polarity, threshold breached when we get a value thats greater than the max threshold + rolling mean
        is_threshold_breached =  r_window[max_index] - baseline > (MAX_VOLTAGE_THRESHOLD)
        peak_time_dif = (t_window[min_index] - t_window[max_index]) * MFIA_CLK_PERIOD
        leading_peak_time = t_window[max_index]
    
    else: # min comes before max
        is_threshold_breached = baseline - r_window[min_index] > (MAX_VOLTAGE_THRESHOLD)
        peak_time_dif = (t_window[max_index] - t_window[min_index]) * MFIA_CLK_PERIOD
        leading_peak_time = t_window[min_index]
    
    current_timestamp = leading_peak_time

    if is_threshold_breached:
        if (last_activated_state == 8):
            # Save snapshot of activation
            run_function_after_delay(0, lambda: save_snapshot(r_window, t_window))
        last_activated_state += 1
        

        
        # Calculate time difference and voltage difference between peaks
        peak_voltage_dif = abs(r_window[max_index] - r_window[min_index]) # abs to guarantee positive voltage diff
        # print(f"loop {i} inside first peak detect = {((time.time()) * 1000 % 10000):.3f} ms, prev V_dif = {prev_peak_voltage_dif}, current V_dif = {peak_voltage_dif}") 
        # Trigger Condition. Checks the following:
        # Large enough voltage difference
        # The postive peak comes before the negative peak (in time)
        # The timestamps are after the previous activation's timestamps (to prevent duplicate triggers from the same data)
        # If the current peak voltage difference is less than or equal to the previous loop's. (To allow full peak difference to be calculated before triggering)
        if (peak_voltage_dif > MAX_VOLTAGE_THRESHOLD and peak_time_dif > 0 and ((t_window[min_index] > prev_timestamps[0]) and (t_window[max_index] > prev_timestamps[0])) and prev_peak_voltage_dif >= peak_voltage_dif):
            if (current_timestamp - last_trigger_timestamp) * MFIA_CLK_PERIOD < DEBOUNCE_PERIOD:
                continue
                
            print(f"loop {i} during trigger = {((time.time()) * 1000 % 10000):.3f} ms") 
            # Check if the function is repeating data
            prev_timestamps = [t_window[min_index], t_window[max_index]]
            
            # Trigger the trigger function
            print(f"trigger function called at {time.time()}")
            last_trigger_timestamp = current_timestamp
            trigger_count += 1
            # leading_peak_time is the timestamp of whatever peak occurs first in time (min peak or max peak depending on polarity)
            calculate_delay_and_trigger(peak_time_dif, SOLENOID_PIN_1, leading_peak_time, SOLENOID_1_DURATION)
            calculate_delay_and_trigger(peak_time_dif, SOLENOID_PIN_2 , leading_peak_time, SOLENOID_2_DURATION, extra_delay= SOLENOID_PAIR_DELAY)
            
            # Peak Statistics
            print(f"Vmax  = {r_window[max_index] * 1000:.4f} mV |") 
            print(f"Vmin  = {r_window[min_index] * 1000:.4f} mV |") 
            print(f"Vdiff = {peak_voltage_dif * 1000:.4f} mV | Tdiff = {peak_time_dif*1000:.3f} ms") 
            print(f"time since start        = {(time.time() - start_time):.3f} s")
            print(f"time since acq of data  = {(time.time() - loop_time)*1000:.3f} ms")
            print(f"time since loop start   = {(time.time() - start_loop_time)*1000:.3f} ms\n")
            print(f"number of triggers since start of loop: {trigger_count}\n")
            triggered = True
            prev_peak_voltage_dif = peak_voltage_dif
        if (triggered == False):
            prev_peak_voltage_dif = peak_voltage_dif
    
    else:
        triggered = False
        prev_peak_voltage_dif = 0
        last_activated_state = 0
    
    # if i > 0:
        # print(f"loop {i} end loop = {((time.time()) * 1000 % 10000):.3f} ms") 
    
    # num_samples += len(poll_result[device.demods[0].sample]["x"])
    
    # xxx = np.append(xxx, rb_r.get_x_buffers(1))
    # if (i > 120):
        # xxx = xxx[int(len(xxx)/120):]
    # line.set_ydata(xxx[::8])
    # line.set_xdata(np.arange(len((xxx[::8]))))
    
    # plt.draw()
    # plt.pause(0.000001)

    
    
# results
end_time = time.time()
diff_time = end_time - start_time

print("total_time", diff_time, "s")
print("time per loop =",diff_time / NUM_LOOPS *1000, "ms")

print(num_samples)
device.unsubscribe()

print("\n")

_, axis = plt.subplots(1, 1)
axis.plot((rb_timestamp.get_full_buffer()[500::]-rb_timestamp.get_full_buffer()[0])*MFIA_CLK_PERIOD, rb_r.get_full_buffer()[500::])
max_value = np.max(rb_r.get_full_buffer())
min_value = np.min(rb_r.get_full_buffer())
print((max_value + min_value)/2)

axis.grid(True)
axis.set_xlabel("timestamp")
axis.set_ylabel("amplitude")
plt.show()
time_length =  1 / device.demods[0].rate() * num_samples
#print("time range", time_length)
print("poll time per loop", time_length / NUM_LOOPS * 1000, "ms");

print("done!")

