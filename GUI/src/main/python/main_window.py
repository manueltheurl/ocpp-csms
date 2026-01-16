from designer.main_window_ui import Ui_MainWindow
from PySide6.QtWidgets import QMainWindow


class MainWindow(QMainWindow, Ui_MainWindow):

    WINDOW_TITLE = "My Application"

    def __init__(self):
        super().__init__()
        self.setupUi(self)
        self.setWindowTitle(self.WINDOW_TITLE)
