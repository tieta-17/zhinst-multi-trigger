from PySide6.QtWidgets import (
    QWidget,
    QLabel,
    QDoubleSpinBox,
    QCheckBox,
    QVBoxLayout,
)

import commands as cmd


class InstrumentGUI(QWidget):

    def __init__(self):
        super().__init__()

        self.setWindowTitle("HF2IS Instrument Controller")

        layout = QVBoxLayout()

        layout.addWidget(QLabel("Threshold (mV)"))

        self.threshold = QDoubleSpinBox()
        self.threshold.setRange(0,100)
        self.threshold.setValue(30)

        self.threshold.valueChanged.connect(
            lambda x:
                cmd.command_queue.put(
                    cmd.CommandType.SET_THRESHOLD,
                    x/1000
                )
        )

        layout.addWidget(self.threshold)

        self.flip = QCheckBox("Flip Polarity")

        self.flip.stateChanged.connect(
            lambda state:
                cmd.command_queue.put(
                    ("polarity", bool(state))
                )
        )

        layout.addWidget(self.flip)

        self.setLayout(layout)