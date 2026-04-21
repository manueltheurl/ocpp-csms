"""
Web-based GUI for OCPP CSMS
Flask application with WebSocket support for real-time updates
"""

# ============================================================================
# OCPP VERSION SELECTION - Change this to switch between OCPP versions
# ============================================================================
OCPP_VERSION = "1.6"  # Options: "1.6" or "2.0.1"
# ============================================================================

from flask import Flask, render_template, jsonify, request
from flask_socketio import SocketIO, emit
import asyncio
import logging
import websockets
from websockets.server import WebSocketServerProtocol
import ssl
from pathlib import Path
import sys
import os
import threading
from datetime import datetime, timezone
from enum import Enum, IntEnum


# Add parent directory to path to import central_system modules
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../../../')))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../../../../')))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../../')))
sys.path.append("../../central_systems/")  # Ensure parent directory is in path for imports

# Import the appropriate ChargePoint class based on version
if OCPP_VERSION == "1.6":
    from central_systems.central_system_v16 import ChargePoint16 as ChargePoint
    OCPP_SUBPROTOCOL = "ocpp1.6"
elif OCPP_VERSION == "2.0.1":
    from central_systems.central_system_v201 import ChargePoint201 as ChargePoint
    OCPP_SUBPROTOCOL = "ocpp2.0.1"
else:
    raise ValueError(f"Unsupported OCPP_VERSION: {OCPP_VERSION}")

import http

class AmpereIndicator(IntEnum):
    CHARGING = 16
    DISCHARGING = 15  # because negative is not allowed, right now we just use 15 to indicate discharging, but this can be improved in the future with a more explicit approach


# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

app = Flask(__name__)
app.config['SECRET_KEY'] = 'ocpp-csms-secret-key-2026'
socketio = SocketIO(app, cors_allowed_origins="*")


class PingTrackingWebSocketProtocol(WebSocketServerProtocol):
    """Custom WebSocket protocol that tracks ping frames"""
    
    async def ping(self, data=None):
        """Override ping method to trigger callback when ping is sent"""
        pong_waiter = await super().ping(data)
        
        if hasattr(ChargePoint, 'ping_callback') and ChargePoint.ping_callback:
            try:
                ChargePoint.ping_callback()
            except Exception as e:
                logging.error(f"Error in ping callback: {e}")
        
        return pong_waiter


class CentralSystemBackend:
    """Backend service for OCPP Central System"""
    
    def __init__(self):
        self.server = None
        self.tls_server = None
        self.loop = None
        self.running = False
        self.iso15118_certs = None
        self.reject_auth = False
        self.charge_point = None
        self.thread = None
        
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
        
        # Only accept connections matching the configured OCPP version
        if websocket.subprotocol != OCPP_SUBPROTOCOL:
            logging.error(f"{charge_point_id} tried to connect with unsupported protocol {websocket.subprotocol}")
            return await websocket.close()
        
        logging.info(f"{charge_point_id} connected using {OCPP_SUBPROTOCOL}")
        cp = ChargePoint(charge_point_id, websocket, iso15118_certs=self.iso15118_certs)
        
        self.charge_point = cp
        
        try:
            await cp.start()
        finally:
            self.charge_point = None
    
    async def run_async(self):
        """Run the central system server asynchronously"""
        host = self.config['host']
        port = self.config['port']
        tls_host = self.config.get('tls_host') or host
        tls_port = self.config['tls_port']
        cert_chain = self.config.get('cert_chain')
        
        self.server = await websockets.serve(
            self.on_connect,
            host,
            port,
            subprotocols=[OCPP_SUBPROTOCOL],
            process_request=self.process_request,
            ping_interval=3,
            ping_timeout=10,
            create_protocol=PingTrackingWebSocketProtocol
        )
        logging.info(f"OCPP {OCPP_VERSION} CSMS listening on ws://{host}:{port}")
        
        if cert_chain:
            ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            ssl_context.load_cert_chain(cert_chain)
            
            self.tls_server = await websockets.serve(
                self.on_connect,
                tls_host,
                tls_port,
                subprotocols=[OCPP_SUBPROTOCOL],
                process_request=self.process_request,
                ssl=ssl_context,
                ping_interval=60,
                ping_timeout=60,
                create_protocol=PingTrackingWebSocketProtocol
            )
            logging.info(f"OCPP {OCPP_VERSION} CSMS listening on wss://{tls_host}:{tls_port}")
        
        self.running = True
        logging.info("OCPP CSMS started successfully")
        
        await self.server.wait_closed()
        if self.tls_server:
            await self.tls_server.wait_closed()
    
    def run_thread(self):
        """Thread entry point for running the async event loop"""
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        try:
            self.loop.run_until_complete(self.run_async())
        except Exception as e:
            logging.error(f"Central system error: {e}", exc_info=True)
        finally:
            self.loop.close()
    
    def start(self):
        """Start the central system in a thread"""
        if self.running:
            logging.warning("Central system is already running")
            return False
        
        self.thread = threading.Thread(target=self.run_thread, daemon=True)
        self.thread.start()
        logging.info("Central system thread started")
        return True
    
    def stop(self):
        """Stop the central system"""
        if not self.running:
            logging.warning("Central system is not running")
            return False
        
        self.running = False
        
        if self.loop and self.loop.is_running():
            if self.server:
                self.loop.call_soon_threadsafe(self.server.close)
            if self.tls_server:
                self.loop.call_soon_threadsafe(self.tls_server.close)
        
        logging.info("Central system stopped")
        return True


# Global central system instance
central_system = CentralSystemBackend()

# State variables
state = {
    'connected': False,
    'charge_point_id': None,
    'energy': '0 Wh',
    'voltage': '0 V',
    'soc': '0 %',
    'cp_state': 'Unknown',
    'charge_profile': 0,
    'transaction_id': 0,
    'last_heartbeat': None,
    'last_ping': None,
    'temperatures': {
        'L1': None,
        'L2': None,
        'L3': None,
        'N': None
    },
    'ac_detected': None,
    'triggering_on_power_outage': None
}


def on_heartbeat_received():
    """Callback when heartbeat is received"""
    state['last_heartbeat'] = datetime.now().isoformat()
    socketio.emit('heartbeat', {'timestamp': state['last_heartbeat']})
    logging.info("Heartbeat received")


def on_ping_received():
    """Callback when ping is received"""
    state['last_ping'] = datetime.now().isoformat()
    socketio.emit('ping', {'timestamp': state['last_ping']})
    logging.info("Ping received")


def on_transaction_event(data):
    """Callback when transaction events occur (start/stop)"""
    event = data.get('event')
    transaction_id = data.get('transaction_id', 0)
    
    if event == 'start':
        state['transaction_id'] = transaction_id
        logging.info(f"🔋 Transaction {transaction_id} started")
        socketio.emit('transaction_started', {'transaction_id': transaction_id})
    elif event == 'stop':
        logging.info(f"🛑 Transaction {transaction_id} stopped")
        socketio.emit('transaction_stopped', {'transaction_id': transaction_id})
        state['transaction_id'] = 0  # Clear transaction ID


def on_meter_value_received(data):
    """Callback when meter values are received"""
    energy = data.get('energy')
    if energy is not None:
        state['energy'] = f"{energy} Wh"
        socketio.emit('meter_value', {'energy': state['energy']})
        logging.info(f"Energy value received: {energy} Wh")
    
    voltage = data.get('voltage')
    if voltage is not None:
        state['voltage'] = f"{voltage} V"
        socketio.emit('meter_value', {'voltage': state['voltage']})
        logging.info(f"Voltage value received: {voltage} V")
    
    soc = data.get('soc')
    if soc is not None:
        state['soc'] = f"{soc} %"
        socketio.emit('meter_value', {'soc': state['soc']})
        logging.info(f"SoC value received: {soc} %")
    
    # Handle temperature values for L1, L2, L3, and N
    temperatures = data.get('temperatures', {})
    print(temperatures)
    if temperatures:
        for phase, temp in temperatures.items():
            if temp is not None:
                state['temperatures'][phase] = temp
                logging.info(f"🌡️  Temperature {phase}: {temp}°C")
        
        # Emit temperature update to web interface
        socketio.emit('temperature_update', {'temperatures': state['temperatures']})


def on_data_transfer_received(data):
    """Callback when DataTransfer message is received"""
    vendor_id = data.get('vendor_id', '')
    message_id = data.get('message_id', '')
    transfer_data = data.get('data', {})
    
    if vendor_id == 'SmartyPlugger' and message_id == 'Heartbeat':
        # Parse temperature values from Heartbeat DataTransfer
        temperatures_updated = False
        
        if 'N' in transfer_data and transfer_data['N'] is not None:
            state['temperatures']['N'] = transfer_data['N']
            logging.info(f"🌡️  Temperature N: {transfer_data['N']}°C")
            temperatures_updated = True
        
        if 'L1' in transfer_data and transfer_data['L1'] is not None:
            state['temperatures']['L1'] = transfer_data['L1']
            logging.info(f"🌡️  Temperature L1: {transfer_data['L1']}°C")
            temperatures_updated = True
        
        if 'L2' in transfer_data and transfer_data['L2'] is not None:
            state['temperatures']['L2'] = transfer_data['L2']
            logging.info(f"🌡️  Temperature L2: {transfer_data['L2']}°C")
            temperatures_updated = True
        
        if 'AcDetected' in transfer_data:
            state['ac_detected'] = bool(transfer_data['AcDetected'])
            logging.info(f"⚡ AC Detected: {state['ac_detected']}")
        
        if 'TriggeringOnPowerOutage' in transfer_data:
            state['triggering_on_power_outage'] = bool(transfer_data['TriggeringOnPowerOutage'])
            logging.info(f"🔌 Triggering on Power Outage: {state['triggering_on_power_outage']}")
        
        # Emit combined update to web interface
        socketio.emit('temperature_update', {
            'temperatures': state['temperatures'],
            'ac_detected': state['ac_detected'],
            'triggering_on_power_outage': state['triggering_on_power_outage']
        })


def on_status_notification_received(data):
    """Callback when status notification is received"""
    status = data.get('status', 'Unknown')
    
    # Map status values based on OCPP version
    if OCPP_VERSION == "1.6":
        # OCPP 1.6 status values
        status_text_map = {
            'Available': 'Level A - Available',
            'Preparing': 'Level B - Preparing',
            'Charging': 'Level C/D - Charging',
            'SuspendedEVSE': 'Suspended by EVSE',
            'SuspendedEV': 'Suspended by EV',
            'Finishing': 'Finishing',
            'Reserved': 'Reserved',
            'Unavailable': 'Level F - Unavailable',
            'Faulted': 'Level E - Faulted'
        }
    elif OCPP_VERSION == "2.0.1":
        # OCPP 2.0.1 status values
        status_text_map = {
            'Available': 'Level A - Available',
            'Occupied': 'Level B - Occupied',
            'Charging': 'Level C/D - Charging',
            'Reserved': 'Reserved',
            'Unavailable': 'Level F - Unavailable',
            'Faulted': 'Level E - Faulted'
        }
    else:
        status_text_map = {}
    
    state['cp_state'] = status_text_map.get(status, status)
    socketio.emit('cp_status', {'status': state['cp_state']})
    logging.info(f"Status notification: {state['cp_state']}")


def on_client_connected(charge_point_id):
    """Callback when client connects"""
    state['connected'] = True
    state['charge_point_id'] = charge_point_id
    socketio.emit('connection_status', {'connected': True, 'charge_point_id': charge_point_id})
    logging.info(f"Client connected: {charge_point_id}")


def on_client_disconnected(charge_point_id):
    """Callback when client disconnects"""
    state['connected'] = False
    state['charge_point_id'] = None
    socketio.emit('connection_status', {'connected': False, 'charge_point_id': None})
    logging.info(f"Client disconnected: {charge_point_id}")


# Register callbacks
ChargePoint.heartbeat_callback = on_heartbeat_received
ChargePoint.ping_callback = on_ping_received
ChargePoint.meter_value_callback = on_meter_value_received
ChargePoint.status_notification_callback = on_status_notification_received
ChargePoint.connection_established_callback = on_client_connected
ChargePoint.connection_closed_callback = on_client_disconnected
ChargePoint.transaction_callback = on_transaction_event
ChargePoint.data_transfer_callback = on_data_transfer_received


@app.route('/')
def index():
    """Render the main page"""
    return render_template('index.html')


@app.route('/api/state')
def get_state():
    """Get current state"""
    return jsonify(state)


@app.route('/api/charging-profile', methods=['POST'])
def set_charging_profile():
    """Set charging current limit (version-specific implementation)"""
    data = request.json
    ampere_limit = int(data.get('ampere', 0))
    
    if not state['connected']:
        return jsonify({'success': False, 'message': 'No OCPP client connected'}), 400
    
    state['charge_profile'] = ampere_limit
    
    async def send_message():
        try:
            if OCPP_VERSION == "1.6":
                # OCPP 1.6: Use SetChargingProfile
                from ocpp.v16.datatypes import ChargingProfile, ChargingSchedule, ChargingSchedulePeriod
                from ocpp.v16.enums import ChargingProfileKindType, ChargingProfilePurposeType, ChargingRateUnitType
                from ocpp.v16 import call
                
                # Create charging schedule period
                charging_schedule_period = ChargingSchedulePeriod(
                    start_period=0,
                    limit=float(ampere_limit)
                )
                
                # Create charging schedule
                charging_schedule = ChargingSchedule(
                    charging_rate_unit=ChargingRateUnitType.amps,
                    charging_schedule_period=[charging_schedule_period],
                    start_schedule=datetime.now(timezone.utc).isoformat()
                )
                
                # Create charging profile
                charging_profile = ChargingProfile(
                    charging_profile_id=1,
                    stack_level=0,
                    charging_profile_purpose=ChargingProfilePurposeType.tx_default_profile,
                    charging_profile_kind=ChargingProfileKindType.absolute,
                    charging_schedule=charging_schedule
                )
                
                payload = call.SetChargingProfile(
                    connector_id=1,
                    cs_charging_profiles=charging_profile
                )
                
                if central_system.charge_point:
                    response = await central_system.charge_point.set_charging_profile_req(payload)
                    logging.info(f"SetChargingProfile response: {response}")
                else:
                    logging.error("No charge point instance available")
                    
            elif OCPP_VERSION == "2.0.1":
                # OCPP 2.0.1: Use SetVariables to control MaxCurrent
                from ocpp.v201.datatypes import SetVariableDataType, ComponentType, VariableType
                from ocpp.v201.enums import AttributeEnumType
                
                set_variable_data = SetVariableDataType(
                    attribute_type=AttributeEnumType.actual,
                    attribute_value=str(ampere_limit),
                    component=ComponentType(name="EVSE", instance="1"),
                    variable=VariableType(name="MaxCurrent")
                )
                
                if central_system.charge_point:
                    response = await central_system.charge_point.set_variables_req(
                        set_variable_data=[set_variable_data]
                    )
                    logging.info(f"SetVariables (MaxCurrent) response: {response}")
                else:
                    logging.error("No charge point instance available")
        except Exception as e:
            logging.error(f"Error sending charging limit command: {e}", exc_info=True)
    
    if central_system.loop and central_system.loop.is_running():
        asyncio.run_coroutine_threadsafe(send_message(), central_system.loop)
        return jsonify({'success': True, 'message': f'Charging limit set to {ampere_limit}A (OCPP {OCPP_VERSION})'})
    else:
        return jsonify({'success': False, 'message': 'Central system not running'}), 500


@app.route('/api/charge/start', methods=['POST'])
def start_charging():
    """Start charging at specified current (default 16A)"""
    data = request.json or {}
    ampere_limit = int(data.get('ampere', AmpereIndicator.CHARGING))  # Default 16A
    
    if not state['connected']:
        return jsonify({'success': False, 'message': 'No OCPP client connected'}), 400
    
    state['charge_profile'] = ampere_limit
    
    async def send_message():
        try:
            if OCPP_VERSION == "1.6":
                # OCPP 1.6: Use SetChargingProfile
                from ocpp.v16.datatypes import ChargingProfile, ChargingSchedule, ChargingSchedulePeriod
                from ocpp.v16.enums import ChargingProfileKindType, ChargingProfilePurposeType, ChargingRateUnitType
                from ocpp.v16 import call
                
                charging_schedule_period = ChargingSchedulePeriod(
                    start_period=0,
                    limit=float(ampere_limit)
                )
                
                charging_schedule = ChargingSchedule(
                    charging_rate_unit=ChargingRateUnitType.amps,
                    charging_schedule_period=[charging_schedule_period],
                    start_schedule=datetime.now(timezone.utc).isoformat()
                )
                
                charging_profile = ChargingProfile(
                    charging_profile_id=1,
                    stack_level=0,
                    charging_profile_purpose=ChargingProfilePurposeType.tx_default_profile,
                    charging_profile_kind=ChargingProfileKindType.absolute,
                    charging_schedule=charging_schedule
                )
                
                payload = call.SetChargingProfile(
                    connector_id=1,
                    cs_charging_profiles=charging_profile
                )
                
                if central_system.charge_point:
                    response = await central_system.charge_point.set_charging_profile_req(payload)
                    logging.info(f"Start Charging ({ampere_limit}A) response: {response}")
                else:
                    logging.error("No charge point instance available")
            elif OCPP_VERSION == "2.0.1":
                # OCPP 2.0.1 implementation (if needed)
                pass
        except Exception as e:
            logging.error(f"Error sending start charging command: {e}", exc_info=True)
    
    if central_system.loop and central_system.loop.is_running():
        asyncio.run_coroutine_threadsafe(send_message(), central_system.loop)
        return jsonify({'success': True, 'message': f'Charging started at {ampere_limit}A'})
    else:
        return jsonify({'success': False, 'message': 'Central system not running'}), 500


@app.route('/api/charge/discharge', methods=['POST'])
def start_discharging():
    """Start discharging """
    data = request.json or {}
    ampere_limit = int(data.get('ampere', AmpereIndicator.DISCHARGING))
    
    if not state['connected']:
        return jsonify({'success': False, 'message': 'No OCPP client connected'}), 400
    
    state['charge_profile'] = ampere_limit
    
    async def send_message():
        try:
            if OCPP_VERSION == "1.6":
                # OCPP 1.6: Use SetChargingProfile
                from ocpp.v16.datatypes import ChargingProfile, ChargingSchedule, ChargingSchedulePeriod
                from ocpp.v16.enums import ChargingProfileKindType, ChargingProfilePurposeType, ChargingRateUnitType
                from ocpp.v16 import call
                
                charging_schedule_period = ChargingSchedulePeriod(
                    start_period=0,
                    limit=float(ampere_limit)
                )
                
                charging_schedule = ChargingSchedule(
                    charging_rate_unit=ChargingRateUnitType.amps,
                    charging_schedule_period=[charging_schedule_period],
                    start_schedule=datetime.now(timezone.utc).isoformat()
                )
                
                charging_profile = ChargingProfile(
                    charging_profile_id=1,
                    stack_level=0,
                    charging_profile_purpose=ChargingProfilePurposeType.tx_default_profile,
                    charging_profile_kind=ChargingProfileKindType.absolute,
                    charging_schedule=charging_schedule
                )
                
                payload = call.SetChargingProfile(
                    connector_id=1,
                    cs_charging_profiles=charging_profile
                )
                
                if central_system.charge_point:
                    response = await central_system.charge_point.set_charging_profile_req(payload)
                    logging.info(f"Start Discharging ({ampere_limit}A) response: {response}")
                else:
                    logging.error("No charge point instance available")
            elif OCPP_VERSION == "2.0.1":
                # OCPP 2.0.1 implementation (if needed)
                pass
        except Exception as e:
            logging.error(f"Error sending start discharging command: {e}", exc_info=True)
    
    if central_system.loop and central_system.loop.is_running():
        asyncio.run_coroutine_threadsafe(send_message(), central_system.loop)
        return jsonify({'success': True, 'message': f'Discharging started at {ampere_limit}A'})
    else:
        return jsonify({'success': False, 'message': 'Central system not running'}), 500


@app.route('/api/charge/stop', methods=['POST'])
def stop_charging():
    """Stop charging by setting current limit to 0A"""
    if not state['connected']:
        return jsonify({'success': False, 'message': 'No OCPP client connected'}), 400
    
    state['charge_profile'] = 0
    
    async def send_message():
        try:
            if OCPP_VERSION == "1.6":
                # OCPP 1.6: Use SetChargingProfile with 0A limit
                from ocpp.v16.datatypes import ChargingProfile, ChargingSchedule, ChargingSchedulePeriod
                from ocpp.v16.enums import ChargingProfileKindType, ChargingProfilePurposeType, ChargingRateUnitType
                from ocpp.v16 import call
                
                charging_schedule_period = ChargingSchedulePeriod(
                    start_period=0,
                    limit=0.0  # 0A = stop charging
                )
                
                charging_schedule = ChargingSchedule(
                    charging_rate_unit=ChargingRateUnitType.amps,
                    charging_schedule_period=[charging_schedule_period],
                    start_schedule=datetime.now(timezone.utc).isoformat()
                )
                
                charging_profile = ChargingProfile(
                    charging_profile_id=1,
                    stack_level=0,
                    charging_profile_purpose=ChargingProfilePurposeType.tx_default_profile,
                    charging_profile_kind=ChargingProfileKindType.absolute,
                    charging_schedule=charging_schedule
                )
                
                payload = call.SetChargingProfile(
                    connector_id=1,
                    cs_charging_profiles=charging_profile
                )
                
                if central_system.charge_point:
                    response = await central_system.charge_point.set_charging_profile_req(payload)
                    logging.info(f"Stop Charging response: {response}")
                else:
                    logging.error("No charge point instance available")
            elif OCPP_VERSION == "2.0.1":
                # OCPP 2.0.1 implementation (if needed)
                pass
        except Exception as e:
            logging.error(f"Error sending stop charging command: {e}", exc_info=True)
    
    if central_system.loop and central_system.loop.is_running():
        asyncio.run_coroutine_threadsafe(send_message(), central_system.loop)
        return jsonify({'success': True, 'message': 'Charging stopped'})
    else:
        return jsonify({'success': False, 'message': 'Central system not running'}), 500


@app.route('/api/transaction/start', methods=['POST'])
def remote_start_transaction():
    """Start a charging transaction remotely"""
    data = request.json or {}
    connector_id = int(data.get('connector_id', 1))
    id_tag = data.get('id_tag', 'WEB_INTERFACE')
    
    if not state['connected']:
        return jsonify({'success': False, 'message': 'No OCPP client connected'}), 400
    
    async def send_message():
        try:
            if OCPP_VERSION == "1.6":
                from ocpp.v16 import call
                
                # RemoteStartTransaction with optional charging profile
                payload = call.RemoteStartTransaction(
                    connector_id=connector_id,
                    id_tag=id_tag
                )
                
                if central_system.charge_point:
                    response = await central_system.charge_point.remote_start_transaction_req(
                        connector_id=connector_id,
                        id_tag=id_tag
                    )
                    logging.info(f"RemoteStartTransaction response: {response}")
                else:
                    logging.error("No charge point instance available")
            elif OCPP_VERSION == "2.0.1":
                # OCPP 2.0.1 implementation
                pass
        except Exception as e:
            logging.error(f"Error sending RemoteStartTransaction: {e}", exc_info=True)
    
    if central_system.loop and central_system.loop.is_running():
        asyncio.run_coroutine_threadsafe(send_message(), central_system.loop)
        return jsonify({'success': True, 'message': f'Transaction started on connector {connector_id}'})
    else:
        return jsonify({'success': False, 'message': 'Central system not running'}), 500


@app.route('/api/transaction/stop', methods=['POST'])
def remote_stop_transaction():
    """Stop a charging transaction remotely"""
    data = request.json or {}
    transaction_id = int(data.get('transaction_id', state.get('transaction_id', 0)))
    
    if not state['connected']:
        return jsonify({'success': False, 'message': 'No OCPP client connected'}), 400
    
    if transaction_id == 0:
        return jsonify({'success': False, 'message': 'No active transaction'}), 400
    
    async def send_message():
        try:
            if OCPP_VERSION == "1.6":
                from ocpp.v16 import call
                
                payload = call.RemoteStopTransaction(
                    transaction_id=transaction_id
                )
                
                if central_system.charge_point:
                    response = await central_system.charge_point.remote_stop_transaction_req(
                        transaction_id=transaction_id
                    )
                    logging.info(f"RemoteStopTransaction response: {response}")
                else:
                    logging.error("No charge point instance available")
            elif OCPP_VERSION == "2.0.1":
                # OCPP 2.0.1 implementation
                pass
        except Exception as e:
            logging.error(f"Error sending RemoteStopTransaction: {e}", exc_info=True)
    
    if central_system.loop and central_system.loop.is_running():
        asyncio.run_coroutine_threadsafe(send_message(), central_system.loop)
        return jsonify({'success': True, 'message': f'Transaction {transaction_id} stopped'})
    else:
        return jsonify({'success': False, 'message': 'Central system not running'}), 500


@app.route('/api/reset/<reset_type>', methods=['POST'])
def send_reset(reset_type):
    """Send reset command (version-specific implementation)"""
    if reset_type not in ['soft', 'hard']:
        return jsonify({'success': False, 'message': 'Invalid reset type'}), 400
    
    if not state['connected']:
        return jsonify({'success': False, 'message': 'No OCPP client connected'}), 400
    
    async def send_message():
        try:
            if OCPP_VERSION == "1.6":
                # OCPP 1.6: Use ResetType enum
                from ocpp.v16.enums import ResetType
                
                reset_type_map = {
                    'soft': ResetType.soft,
                    'hard': ResetType.hard
                }
                
                reset_type_ocpp = reset_type_map.get(reset_type)
                
                if central_system.charge_point:
                    response = await central_system.charge_point.reset_req(type=reset_type_ocpp)
                    logging.info(f"{reset_type_ocpp} Reset response: {response}")
                else:
                    logging.error("No charge point instance available")
                    
            elif OCPP_VERSION == "2.0.1":
                # OCPP 2.0.1: Use ResetEnumType
                from ocpp.v201.enums import ResetEnumType
                
                reset_type_map = {
                    'soft': ResetEnumType.immediate,
                    'hard': ResetEnumType.on_idle
                }
                
                reset_type_ocpp = reset_type_map.get(reset_type)
                
                if central_system.charge_point:
                    response = await central_system.charge_point.reset_req(type=reset_type_ocpp)
                    logging.info(f"{reset_type_ocpp} Reset response: {response}")
                else:
                    logging.error("No charge point instance available")
        except Exception as e:
            logging.error(f"Error sending Reset: {e}", exc_info=True)
    
    if central_system.loop and central_system.loop.is_running():
        asyncio.run_coroutine_threadsafe(send_message(), central_system.loop)
        return jsonify({'success': True, 'message': f'{reset_type.capitalize()} reset sent (OCPP {OCPP_VERSION})'})
    else:
        return jsonify({'success': False, 'message': 'Central system not running'}), 500


@socketio.on('connect')
def handle_connect():
    """Handle WebSocket connection"""
    logging.info('Web client connected')
    emit('connection_status', {'connected': state['connected'], 'charge_point_id': state['charge_point_id']})


@socketio.on('disconnect')
def handle_disconnect():
    """Handle WebSocket disconnection"""
    logging.info('Web client disconnected')


if __name__ == '__main__':
    # Print OCPP version information
    logging.info("=" * 60)
    logging.info(f"OCPP CSMS GUI Starting with OCPP Version: {OCPP_VERSION}")
    logging.info(f"Protocol: {OCPP_SUBPROTOCOL}")
    logging.info("=" * 60)
    
    # Only start OCPP central system once (not in the reloader process)
    # Flask debug mode spawns a child process with WERKZEUG_RUN_MAIN set
    if os.environ.get('WERKZEUG_RUN_MAIN') != 'true':
        central_system.start()
    
    # Run Flask app with SocketIO
    socketio.run(
        app,
        host='0.0.0.0',
        port=1234,  # thats only the GUI, the server itself will still run on 9000
        debug=False,
        use_reloader=False,
        allow_unsafe_werkzeug=True
    )
