import commands as cmd
from zhinst.toolkit import Session
import RPi.GPIO as GPIO

POLL_TIME = 0.001 # Actual poll time is this number + loop delay. Leave this at 0 for fastest polling. Any value below 0.02 will result in some frames having only one value.

class AcquistionController:
    def __init(self, settings):
        self.settings = settings # system calibration parameters described in settings.py
        self.running = False
        self.trigger_count = 0
        self.session = None
        self.device = None

    def connect(self):
        self.session = Session(self.settings.server_host, hf2 = True)
        self.device = self.session.connect_device(self.settings.device_id)

    def run(self):
        self.running = True

        while self.running:
            self.process_commands()
            
            poll_result = self.session.poll(
                recording_time = POLL_TIME
            )

            self.process_poll(poll_result)

            self.poll()
            self.detect()
            self.classify()
            self.trigger()
    
    def process_commands(self):
        while not cmd.command_queue.empty():
            command = cmd.command_queue.get()
            
            match command.type():
                case cmd.CommandType.SET_THRESHOLD:
                    self.settings.threshold = command.value
            
                case cmd.CommandType.SET_POLARITY:
                    self.settings.polarity_flipped = command.value