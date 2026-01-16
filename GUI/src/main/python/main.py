fbs = False  # fbs package required for this

import sys
from PySide6.QtWidgets import QApplication
from main_window import MainWindow
from controller import Controller


if __name__ == '__main__':
    if fbs:
        from fbs_runtime.application_context.PySide6 import ApplicationContext
        appctxt = ApplicationContext()       # 1. Instantiate ApplicationContext
        window = MainWindow()
        controller = Controller(window)
        window.show()
        exit_code = appctxt.app.exec()      # 2. Invoke appctxt.app.exec()
        sys.exit(exit_code)
    else:
        app = QApplication(sys.argv)
        window = MainWindow()
        controller = Controller(window)
        window.show()
        sys.exit(app.exec())
