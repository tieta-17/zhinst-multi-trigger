from zhinst.toolkit import Session
import numpy as np
import time
import RPi.GPIO as GPIO
import matplotlib.pyplot as plt
from matplotlib.animation import FuncAnimation
from zhinst.core import ziDAQServer
import threading


MAX_MIN_VOLTAGE_THRESHOLD = 0.00003 # Threshold for triggering based on the distance between the maximum and minimum voltage
MAX_VOLTAGE_THRESHOLD     = MAX_MIN_VOLTAGE_THRESHOLD / 2 # Threshold for triggering based on the maximum voltage. The voltage must reach this level to trigger.
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

min_val = 9999999999
# Create directory to store snapshots
try:
	os.mkdir(SNAPSHOT_FILE_PATH)
except:
	# path already exists, do nothing
	pass

# Delay and Trigger functions

def trigger_function(pin):
	# Trigger whatever is needed
	# print("triggered")
	GPIO.output(pin, GPIO.HIGH)
	if (pin == 17):
		print(f"triggered at: {time.time()}")
	time.sleep(0.05)
	GPIO.output(pin, GPIO.LOW)

def run_function_after_delay(delay, function):
	timer = threading.Timer(delay, function)
	timer.start()

def calculate_delay_and_trigger(peak_time_dif, pin, peak_timestamp):
	
	timestamp_difference = peak_timestamp - time_sync[1]
	instrument_time_difference = MFIA_CLK_PERIOD * timestamp_difference
	print(f"peak_timestamp: {peak_timestamp:.3f} ms, time_sync: {time_sync[1]:.3f} ms")
	current_time = time.time()
	system_time_difference = current_time - time_sync[0]
	
	peak_current_time_dif = system_time_difference - instrument_time_difference + INST_CLK_SYNC_DELAY
	trigger_delay = 0.07 - peak_current_time_dif - INST_SAMPLE_DELAY #TRIGGER_DELAY_SCALE*peak_time_5dif - peak_current_time_dif
	run_function_after_delay(trigger_delay, lambda: trigger_function(pin))
	
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
PIN = 17
GPIO.setup(PIN, GPIO.OUT)
GPIO.output(PIN, GPIO.LOW)

PIN27 = 27
GPIO.setup(PIN27, GPIO.OUT)
GPIO.output(PIN27, GPIO.LOW)

CLK_SYNC_PIN = 22
GPIO.setup(CLK_SYNC_PIN, GPIO.OUT)
GPIO.output(CLK_SYNC_PIN, GPIO.LOW)

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

start_time = time.time()

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

	
	if (r_window[max_index] > (MAX_VOLTAGE_THRESHOLD + np.mean(rolling_avg))):
		
		if (last_activated_state == 8):
			# Save snapshot of activation
			run_function_after_delay(0, lambda: save_snapshot(r_window, t_window))
		last_activated_state += 1
		
		# Calculate time difference and voltage difference between peaks
		peak_time_dif = (t_window[min_index] - t_window[max_index]) * MFIA_CLK_PERIOD # in s
		peak_voltage_dif = r_window[max_index] - r_window[min_index]
		# print(f"loop {i} inside first peak detect = {((time.time()) * 1000 % 10000):.3f} ms, prev V_dif = {prev_peak_voltage_dif}, current V_dif = {peak_voltage_dif}") 
		# Trigger Condition. Checks the following:
		# Large enough voltage difference
		# The postive peak comes before the negative peak (in time)
		# The timestamps are after the previous activation's timestamps (to prevent duplicate triggers from the same data)
		# If the current peak voltage difference is less than or equal to the previous loop's. (To allow full peak difference to be calculated before triggering)
		if (peak_voltage_dif > MAX_MIN_VOLTAGE_THRESHOLD and peak_time_dif > 0 and ((t_window[min_index] > prev_timestamps[0]) and (t_window[max_index] > prev_timestamps[0])) and prev_peak_voltage_dif >= peak_voltage_dif):
			print(f"loop {i} during trigger = {((time.time()) * 1000 % 10000):.3f} ms") 
			# Check if the function is repeating data
			prev_timestamps = [t_window[min_index], t_window[max_index]]
			
			# Trigger the trigger function
			print(f"trigger function called at {time.time()}")
			calculate_delay_and_trigger(peak_time_dif, PIN, t_window[max_index])
			
			# Peak Statistics
			print(f"Vmax  = {r_window[max_index]:.3f} V |") 
			print(f"Vmin  = {r_window[min_index]:.3f} V |") 
			print(f"Vdiff = {peak_voltage_dif:.3f} V | Tdiff = {peak_time_dif*1000:.3f} ms") 
			print(f"time since start        = {(time.time() - start_time):.3f} s")
			print(f"time since acq of data  = {(time.time() - loop_time)*1000:.3f} ms")
			print(f"time since loop start   = {(time.time() - start_loop_time)*1000:.3f} ms\n")
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

