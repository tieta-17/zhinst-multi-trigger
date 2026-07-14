class SystemSettings:
    def __init__(self):
        self.threshold = 0.00003 # voltage threshold for triggering (v)
        self.polarity_flipped = False # peak voltage swing comes before min voltage swing
        self.solenoid_pair_delay = 0.015 # delay between solenoid 1 triggering and solenoid 2 triggering (s)
        self.solenoid1_duration = 0.060 # duration that solenoid 1 stays on (s)
        self.solenoid2_duration = 0.045 # duration that solenoid 2 stays on (s)
        self.trigger_offset = 0.0 # tunable parameter to increase triggering time after a delay
        self.debounce_period = 0.100 # delays a triggering until a specified period of inactivity has passed (s)

        # instrument parameters
        self.inst_clk_sync_delay = 0.001
        self.inst_sample_delay = 0.0035
        self.trigger_delay_scale = 0.00
         #self.mfia_clk_period = 

        self.server_host = "localhost"
        self.device_id = "dev1051"