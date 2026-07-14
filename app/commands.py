from queue import Queue
from dataclasses import dataclass
from enum import Enum


command_queue = Queue()
status_queue = Queue()

class CommandType(Enum):
    SET_THRESHOLD = 1
    SET_POLARITY = 2
    START = 3
    STOP = 4


@dataclass
class Command:
    type: CommandType
    value: object = None

class StatusType(Enum):
    LOG = 1
    STATS = 2
    CONNECTION = 3
    WAVEFORM = 4

@dataclass
class Status:
    type: StatusType
    data: object
