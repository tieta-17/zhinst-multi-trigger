import sys
import json

HF_BEAD_PHASE_MEAN: float = 0.0
LF_BEAD_PHASE_MEAN: float = 0.0
HF_PHASE_RANGE: list = None
LF_PHASE_RANGE: list = None
BEAD_SIZE_UM: float = 0.0
LF_BASELINE_PEAK_VOLTAGE: float = 0.0


import json
import sys

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

res = param_calibration()
print(res)


POLARITY_FLIPPED = False
MAX_MIN_VOLTAGE_THRESHOLD = 0.5

def asynch_keyboard_listener():
	print("Enter 'p' to flip polarity\nEnter 't' followed by a number to set threshold")
	while True:
		line = sys.stdin.readline().strip()
		handle_commands(line)

def handle_commands(line):
	global MAX_MIN_VOLTAGE_THRESHOLD, POLARITY_FLIPPED

	if line.lower() == "p":
		POLARITY_FLIPPED = not POLARITY_FLIPPED
		print(f"Polarity Flipped!\nCurrent: {"Min-Peak" if POLARITY_FLIPPED else "Peak-Min"}")
	
	if line.lower().startswith("t"):
		try:
			_, val = line.split()
			MAX_MIN_VOLTAGE_THRESHOLD = float(val)
			print(f"Changed Threshold!\nCurrent: {val} V")
		except:
			print('Invalid Sequence\nEnsure input format follows "t ___"')
			
asynch_keyboard_listener()

