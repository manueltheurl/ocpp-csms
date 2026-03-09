# -*- coding: utf-8 -*-

################################################################################
## Form generated from reading UI file 'main_window.ui'
##
## Created by: Qt User Interface Compiler version 6.9.1
##
## WARNING! All changes made in this file will be lost when recompiling UI file!
################################################################################

from PySide6.QtCore import (QCoreApplication, QDate, QDateTime, QLocale,
    QMetaObject, QObject, QPoint, QRect,
    QSize, QTime, QUrl, Qt)
from PySide6.QtGui import (QAction, QBrush, QColor, QConicalGradient,
    QCursor, QFont, QFontDatabase, QGradient,
    QIcon, QImage, QKeySequence, QLinearGradient,
    QPainter, QPalette, QPixmap, QRadialGradient,
    QTransform)
from PySide6.QtWidgets import (QApplication, QFrame, QLabel, QMainWindow,
    QMenu, QMenuBar, QPushButton, QSizePolicy,
    QSlider, QStatusBar, QWidget)

class Ui_MainWindow(object):
    def setupUi(self, MainWindow):
        if not MainWindow.objectName():
            MainWindow.setObjectName(u"MainWindow")
        MainWindow.setEnabled(True)
        MainWindow.resize(1507, 915)
        self.centralwidget = QWidget(MainWindow)
        self.centralwidget.setObjectName(u"centralwidget")
        self.frame_status_connection_to_ocpp_client = QFrame(self.centralwidget)
        self.frame_status_connection_to_ocpp_client.setObjectName(u"frame_status_connection_to_ocpp_client")
        self.frame_status_connection_to_ocpp_client.setGeometry(QRect(10, 10, 221, 41))
        self.frame_status_connection_to_ocpp_client.setFrameShape(QFrame.Shape.StyledPanel)
        self.frame_status_connection_to_ocpp_client.setFrameShadow(QFrame.Shadow.Raised)
        self.lbl_status_connection_to_ocpp_client = QLabel(self.frame_status_connection_to_ocpp_client)
        self.lbl_status_connection_to_ocpp_client.setObjectName(u"lbl_status_connection_to_ocpp_client")
        self.lbl_status_connection_to_ocpp_client.setGeometry(QRect(10, 10, 203, 18))
        self.frame_voltage_analog_pin_mcu_cp_in = QFrame(self.centralwidget)
        self.frame_voltage_analog_pin_mcu_cp_in.setObjectName(u"frame_voltage_analog_pin_mcu_cp_in")
        self.frame_voltage_analog_pin_mcu_cp_in.setGeometry(QRect(10, 70, 511, 81))
        self.frame_voltage_analog_pin_mcu_cp_in.setFrameShape(QFrame.Shape.StyledPanel)
        self.frame_voltage_analog_pin_mcu_cp_in.setFrameShadow(QFrame.Shadow.Raised)
        self.lbl_desc_voltage_analog_pin_mcu_cp_in = QLabel(self.frame_voltage_analog_pin_mcu_cp_in)
        self.lbl_desc_voltage_analog_pin_mcu_cp_in.setObjectName(u"lbl_desc_voltage_analog_pin_mcu_cp_in")
        self.lbl_desc_voltage_analog_pin_mcu_cp_in.setGeometry(QRect(10, 10, 201, 18))
        self.lbl_value_voltage_analog_pin_mcu_cp_in = QLabel(self.frame_voltage_analog_pin_mcu_cp_in)
        self.lbl_value_voltage_analog_pin_mcu_cp_in.setObjectName(u"lbl_value_voltage_analog_pin_mcu_cp_in")
        self.lbl_value_voltage_analog_pin_mcu_cp_in.setGeometry(QRect(230, 10, 101, 18))
        self.lbl_desc_cp_state = QLabel(self.frame_voltage_analog_pin_mcu_cp_in)
        self.lbl_desc_cp_state.setObjectName(u"lbl_desc_cp_state")
        self.lbl_desc_cp_state.setGeometry(QRect(10, 50, 431, 18))
        self.lbl_value_cp_state = QLabel(self.frame_voltage_analog_pin_mcu_cp_in)
        self.lbl_value_cp_state.setObjectName(u"lbl_value_cp_state")
        self.lbl_value_cp_state.setGeometry(QRect(230, 50, 221, 20))
        self.frame_heartbeat = QFrame(self.centralwidget)
        self.frame_heartbeat.setObjectName(u"frame_heartbeat")
        self.frame_heartbeat.setGeometry(QRect(1400, 10, 91, 41))
        self.frame_heartbeat.setFrameShape(QFrame.Shape.StyledPanel)
        self.frame_heartbeat.setFrameShadow(QFrame.Shadow.Raised)
        self.lbl_heartbeat = QLabel(self.frame_heartbeat)
        self.lbl_heartbeat.setObjectName(u"lbl_heartbeat")
        self.lbl_heartbeat.setGeometry(QRect(10, 10, 69, 18))
        self.frame_ping = QFrame(self.centralwidget)
        self.frame_ping.setObjectName(u"frame_ping")
        self.frame_ping.setGeometry(QRect(1400, 60, 91, 41))
        self.frame_ping.setFrameShape(QFrame.Shape.StyledPanel)
        self.frame_ping.setFrameShadow(QFrame.Shadow.Raised)
        self.lbl_ping = QLabel(self.frame_ping)
        self.lbl_ping.setObjectName(u"lbl_ping")
        self.lbl_ping.setGeometry(QRect(10, 10, 69, 18))
        self.frame_set_charge_profile = QFrame(self.centralwidget)
        self.frame_set_charge_profile.setObjectName(u"frame_set_charge_profile")
        self.frame_set_charge_profile.setGeometry(QRect(10, 180, 341, 71))
        self.frame_set_charge_profile.setFrameShape(QFrame.Shape.StyledPanel)
        self.frame_set_charge_profile.setFrameShadow(QFrame.Shadow.Raised)
        self.lbl_set_charge_profile = QLabel(self.frame_set_charge_profile)
        self.lbl_set_charge_profile.setObjectName(u"lbl_set_charge_profile")
        self.lbl_set_charge_profile.setGeometry(QRect(10, 10, 311, 18))
        self.sld_set_charge_profile = QSlider(self.frame_set_charge_profile)
        self.sld_set_charge_profile.setObjectName(u"sld_set_charge_profile")
        self.sld_set_charge_profile.setGeometry(QRect(10, 40, 160, 16))
        self.sld_set_charge_profile.setMaximum(80)
        self.sld_set_charge_profile.setOrientation(Qt.Orientation.Horizontal)
        self.btn_soft_reset = QPushButton(self.centralwidget)
        self.btn_soft_reset.setObjectName(u"btn_soft_reset")
        self.btn_soft_reset.setGeometry(QRect(10, 300, 181, 26))
        self.btn_hard_reset = QPushButton(self.centralwidget)
        self.btn_hard_reset.setObjectName(u"btn_hard_reset")
        self.btn_hard_reset.setGeometry(QRect(10, 340, 181, 26))
        MainWindow.setCentralWidget(self.centralwidget)
        self.menubar = QMenuBar(MainWindow)
        self.menubar.setObjectName(u"menubar")
        self.menubar.setGeometry(QRect(0, 0, 1507, 23))
        self.menuSmartyPlug_CSMS = QMenu(self.menubar)
        self.menuSmartyPlug_CSMS.setObjectName(u"menuSmartyPlug_CSMS")
        MainWindow.setMenuBar(self.menubar)
        self.statusbar = QStatusBar(MainWindow)
        self.statusbar.setObjectName(u"statusbar")
        MainWindow.setStatusBar(self.statusbar)

        self.menubar.addAction(self.menuSmartyPlug_CSMS.menuAction())

        self.retranslateUi(MainWindow)

        QMetaObject.connectSlotsByName(MainWindow)
    # setupUi

    def retranslateUi(self, MainWindow):
        MainWindow.setWindowTitle(QCoreApplication.translate("MainWindow", u"MainWindow", None))
        self.lbl_status_connection_to_ocpp_client.setText(QCoreApplication.translate("MainWindow", u"Not Connected to OCPP client", None))
        self.lbl_desc_voltage_analog_pin_mcu_cp_in.setText(QCoreApplication.translate("MainWindow", u"Voltage Analog Pin Mcu CP In:", None))
        self.lbl_value_voltage_analog_pin_mcu_cp_in.setText(QCoreApplication.translate("MainWindow", u"0V", None))
        self.lbl_desc_cp_state.setText(QCoreApplication.translate("MainWindow", u"Resulting CP State:", None))
        self.lbl_value_cp_state.setText(QCoreApplication.translate("MainWindow", u"0V", None))
        self.lbl_heartbeat.setText(QCoreApplication.translate("MainWindow", u"Heartbeat", None))
        self.lbl_ping.setText(QCoreApplication.translate("MainWindow", u"Ping", None))
        self.lbl_set_charge_profile.setText(QCoreApplication.translate("MainWindow", u"Set charge profile: PLC communication only", None))
        self.btn_soft_reset.setText(QCoreApplication.translate("MainWindow", u"Soft Reset Smartyplug", None))
        self.btn_hard_reset.setText(QCoreApplication.translate("MainWindow", u"Hard Reset Smartyplug", None))
        self.menuSmartyPlug_CSMS.setTitle(QCoreApplication.translate("MainWindow", u"SmartyPlug CSMS", None))
    # retranslateUi

