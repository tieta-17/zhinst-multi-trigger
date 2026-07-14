import sys
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QTimer
from gui import InstrumentGUI
from commands import command_queue

app = QApplication(sys.argv)
window = InstrumentGUI()

def process_queue():
    while not command_queue.empty():
        print(command_queue.get())


timer = QTimer()
timer.timeout.connect(process_queue)
timer.start(20)


window.show()
app.exec()