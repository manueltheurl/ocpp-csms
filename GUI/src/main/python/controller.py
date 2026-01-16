from main_window import MainWindow
import asyncio
import logging
import websockets
from websockets.server import WebSocketServerProtocol
import ssl
from pathlib import Path
import sys
import os

from PySide6.QtCore import QObject, QEvent, Qt, Signal, QThread, QTimer

# Add parent directory to path to import central_system modules
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../../../..')))

from central_systems.central_system_v16 import ChargePoint16
from central_systems.central_system_v201 import ChargePoint201
import http


class PingTrackingWebSocketProtocol(WebSocketServerProtocol):
    """Custom WebSocket protocol that tracks ping frames"""
    
    async def ping(self, data=None):
        """Override ping method to trigger callback when ping is sent"""
        # Call the original ping method
        pong_waiter = await super().ping(data)
        
        # Trigger the ping callback if registered
        if hasattr(ChargePoint16, 'ping_callback') and ChargePoint16.ping_callback:
            try:
                ChargePoint16.ping_callback()
            except Exception as e:
                logging.error(f"Error in ping callback: {e}")
        
        return pong_waiter


class CentralSystemBackend(QThread):
    """Backend service for OCPP Central System"""
    
    def __init__(self):
        super().__init__()
        self.server = None
        self.tls_server = None
        self.loop = None
        self.running = False
        self.iso15118_certs = None
        self.reject_auth = False
        
       
        # Default configuration
        self.config = {
            'host': '0.0.0.0',
            'port': 9000,
            'tls_host': None,
            'tls_port': 9001,
            'cert_chain': None,
            'certs_dir': None
        }
    
    def configure(self, **kwargs):
        """Update configuration parameters"""
        self.config.update(kwargs)
        
        # Handle certificates directory
        if self.config.get('certs_dir'):
            certs = Path(self.config['certs_dir'])
            if certs.exists():
                self.iso15118_certs = certs
                logging.info(f"ISO15118 certificates loaded from {certs}")
            else:
                logging.warning(f"Certificates directory does not exist: {certs}")
    
    async def process_request(self, connection, request):
        """Process incoming WebSocket requests"""
        logging.info(f'Request:\n{request}')
        if self.reject_auth:
            logging.info('Rejecting authorization (reject_auth enabled)')
            return (
                http.HTTPStatus.UNAUTHORIZED,
                [],
                b'Invalid credentials\n',
            )
        return None
    
    async def on_connect(self, websocket, path):
        """Handle new WebSocket connections"""
        try:
            requested_protocols = websocket.request_headers["Sec-WebSocket-Protocol"]
        except KeyError:
            logging.error("Client hasn't requested any Subprotocol. Closing Connection")
            return await websocket.close()
        
        if websocket.subprotocol:
            logging.info("Protocols Matched: %s", websocket.subprotocol)
        else:
            logging.warning(
                "Protocols Mismatched | Expected Subprotocols: %s, "
                "but client supports %s | Closing connection",
                websocket.available_subprotocols,
                requested_protocols,
            )
            return await websocket.close()
        
        charge_point_id = path.strip("/")
        
        if websocket.subprotocol == "ocpp1.6":
            logging.info(f"{charge_point_id} connected using OCPP1.6")
            cp = ChargePoint16(charge_point_id, websocket, iso15118_certs=self.iso15118_certs)
        else:
            logging.info(f"{charge_point_id} connected using OCPP2.0.1")
            cp = ChargePoint201(charge_point_id, websocket, iso15118_certs=self.iso15118_certs)
        
        await cp.start()
    
    async def run_async(self):
        """Run the central system server asynchronously"""
        host = self.config['host']
        port = self.config['port']
        tls_host = self.config.get('tls_host') or host
        tls_port = self.config['tls_port']
        cert_chain = self.config.get('cert_chain')
        
        # Start plaintext WebSocket server
        # Increase ping timeout to 60s or disable with ping_interval=None
        self.server = await websockets.serve(
            self.on_connect,
            host,
            port,
            subprotocols=["ocpp1.6", "ocpp2.0.1"],
            process_request=self.process_request,
            ping_interval=3,  # Send ping every 3 seconds
            ping_timeout=10,  # Wait 10 seconds for pong response
            create_protocol=PingTrackingWebSocketProtocol
        )
        logging.info(f"OCPP CSMS listening on ws://{host}:{port}")
        
        # Start TLS WebSocket server if certificate chain is provided
        if cert_chain:
            ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            ssl_context.load_cert_chain(cert_chain)
            
            self.tls_server = await websockets.serve(
                self.on_connect,
                tls_host,
                tls_port,
                subprotocols=["ocpp1.6", "ocpp2.0.1"],
                process_request=self.process_request,
                ssl=ssl_context,
                ping_interval=60,  # Send ping every 60 seconds
                ping_timeout=60,   # Wait 60 seconds for pong response
                create_protocol=PingTrackingWebSocketProtocol
            )
            logging.info(f"OCPP CSMS listening on wss://{tls_host}:{tls_port}")
        
        self.running = True
        logging.info("OCPP CSMS started successfully")
        
        # Keep running until stopped
        await self.server.wait_closed()
        if self.tls_server:
            await self.tls_server.wait_closed()
    
    def run(self):
        """QThread entry point for running the async event loop"""
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        try:
            self.loop.run_until_complete(self.run_async())
        except Exception as e:
            logging.error(f"Central system error: {e}", exc_info=True)
        finally:
            self.loop.close()
    
    def start(self):
        """Start the central system in a QThread"""
        if self.running or self.isRunning():
            logging.warning("Central system is already running")
            return False
        
        super().start()
        logging.info("Central system QThread started")
        return True
    
    def stop(self):
        """Stop the central system"""
        if not self.running and not self.isRunning():
            logging.warning("Central system is not running")
            return False
        
        self.running = False
        
        # Schedule server closures in the event loop
        if self.loop and self.loop.is_running():
            if self.server:
                self.loop.call_soon_threadsafe(self.server.close)
            if self.tls_server:
                self.loop.call_soon_threadsafe(self.tls_server.close)
        
        # Wait for QThread to finish
        self.wait(5000)  # Wait up to 5 seconds
        
        logging.info("Central system stopped")
        return True


class Controller(QObject):
    # Signals for thread-safe GUI updates
    heartbeat_signal = Signal()
    ping_signal = Signal()
    meter_value_signal = Signal(dict)
    client_connected_signal = Signal(str)
    client_disconnected_signal = Signal(str)
    
    def __init__(self, view: MainWindow):
        super().__init__()
        self.view = view
        self.threads = []  # Track running threads
        self.central_system = CentralSystemBackend()
        self.is_client_connected = False
        
        # Register meter value callback
        ChargePoint16.meter_value_callback = self.on_meter_value_received
        # Register heartbeat callback
        ChargePoint16.heartbeat_callback = self.on_heartbeat_received
        # Register ping callback
        ChargePoint16.ping_callback = self.on_ping_received
        # Register connection status callbacks
        ChargePoint16.connection_established_callback = self.on_client_connected
        ChargePoint16.connection_closed_callback = self.on_client_disconnected
        
        self.connect_signals()
        
        # Initialize connection status label
        self.update_connection_status(False)
        
        self.start_central_system()

    def connect_signals(self):
        # Connect internal signals for thread-safe updates
        self.heartbeat_signal.connect(self.flash_heartbeat_label)
        self.ping_signal.connect(self.flash_ping_label)
        self.meter_value_signal.connect(self.update_meter_values)
        self.client_connected_signal.connect(self.handle_client_connected)
        self.client_disconnected_signal.connect(self.handle_client_disconnected)
        
        # Connect UI signals
        self.view.sld_set_duty_cycle.valueChanged.connect(self.update_label_duty_cycle)
    
    def on_client_connected(self, charge_point_id):
        """Callback when OCPP client connects (called from worker thread)"""
        self.client_connected_signal.emit(charge_point_id)
    
    def handle_client_connected(self, charge_point_id):
        """Handle client connection in GUI thread"""
        try:
            self.is_client_connected = True
            self.update_connection_status(True)
            logging.info(f"OCPP client {charge_point_id} connected")
        except Exception as e:
            logging.error(f"Error handling client connection: {e}")
    
    def on_client_disconnected(self, charge_point_id):
        """Callback when OCPP client disconnects (called from worker thread)"""
        self.client_disconnected_signal.emit(charge_point_id)
    
    def handle_client_disconnected(self, charge_point_id):
        """Handle client disconnection in GUI thread"""
        try:
            self.is_client_connected = False
            self.update_connection_status(False)
            logging.info(f"OCPP client {charge_point_id} disconnected")
        except Exception as e:
            logging.error(f"Error handling client disconnection: {e}")
    
    def update_connection_status(self, connected):
        """Update the connection status label"""
        try:
            if hasattr(self.view, 'lbl_status_connection_to_ocpp_client'):
                if connected:
                    self.view.lbl_status_connection_to_ocpp_client.setText("OCPP client connected")
                    self.view.frame_status_connection_to_ocpp_client.setStyleSheet("background-color: green;")
                else:
                    self.view.lbl_status_connection_to_ocpp_client.setText("OCPP client not connected")
                    self.view.frame_status_connection_to_ocpp_client.setStyleSheet("background-color: red;")
        except Exception as e:
            logging.error(f"Error updating connection status label: {e}")
    
    def on_heartbeat_received(self):
        """Callback when heartbeat is received from charge point (called from worker thread)"""
        self.heartbeat_signal.emit()

    def on_ping_received(self):
        """Callback when ping is received from charge point (called from worker thread)"""
        self.ping_signal.emit()

    def flash_heartbeat_label(self):
        """Flash the heartbeat label to indicate heartbeat received (runs in GUI thread)"""

        self.view.frame_heartbeat.setStyleSheet("background-color: red;")
        logging.info("Heartbeat received - flashing red")
        QTimer.singleShot(500, self.unflash_heartbeat_label)
    
    def unflash_heartbeat_label(self):
        """Reset the heartbeat label color (runs in GUI thread)"""

        self.view.frame_heartbeat.setStyleSheet("background-color: white;")

    def flash_ping_label(self):
        """Flash the ping label to indicate ping received (runs in GUI thread)"""

        self.view.frame_ping.setStyleSheet("background-color: green;")
        logging.info("Ping received - flashing green")
        QTimer.singleShot(300, self.unflash_ping_label)

    def unflash_ping_label(self):
        """Reset the ping label color (runs in GUI thread)"""
        self.view.frame_ping.setStyleSheet("background-color: white;")

    def on_meter_value_received(self, data):
        """Callback when meter values are received from charge point (called from worker thread)"""
        self.meter_value_signal.emit(data)
    
    def update_meter_values(self, data):
        """Update GUI with meter values (runs in GUI thread)"""
        try:
            voltage = data.get('voltage')
            # energy = data.get('energy')
            # connector_id = data.get('connector_id')
            
            # Update GUI label
            if hasattr(self.view, 'lbl_value_voltage_analog_pin_mcu_cp_in'):
                self.view.lbl_value_voltage_analog_pin_mcu_cp_in.setText(f"{voltage} V")
                logging.info(f"Updated voltage label: {voltage} V")
            
            # You can add more label updates here for other values
            # For example, if you have an energy label:
            # if energy is not None and hasattr(self.view, 'lbl_energy'):
            #     self.view.lbl_energy.setText(f"{energy} Wh")
                
        except Exception as e:
            logging.error(f"Error updating GUI with meter values: {e}")
    
    def start_central_system(self, **config):
        """Start the OCPP central system with given configuration"""
        self.central_system.configure(**config)
        return self.central_system.start()
    
    def stop_central_system(self):
        """Stop the OCPP central system"""
        return self.central_system.stop()
    
    def is_central_system_running(self):
        """Check if central system is running"""
        return self.central_system.running

    def update_label_duty_cycle(self, value):
        self.view.lbl_set_duty_cycle.setText(f"Set Duty Cycle {int(value)}%")
