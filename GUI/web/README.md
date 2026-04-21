# OCPP CSMS Web GUI

A modern, professional web-based interface for monitoring and controlling OCPP (Open Charge Point Protocol) charge points. This application provides real-time monitoring, charging profile management, and control capabilities for EV charging stations.

## Features

- **Real-time Monitoring**: Live updates of charge point status, voltage readings, and connection state
- **Connection Management**: Visual indicators for OCPP client connections, heartbeats, and ping responses
- **Charging Profile Control**: Adjust charging current limits (0-80A) with an intuitive slider interface
- **Remote Control**: Send soft and hard reset commands to connected charge points
- **Responsive Design**: Professional UI that works seamlessly on desktop, tablet, and mobile devices
- **WebSocket Communication**: Instant updates using Socket.IO for real-time data synchronization
- **OCPP 1.6 & 2.0.1 Support**: Compatible with both major OCPP versions

## Screenshots

The interface includes:
- Connection status dashboard with real-time indicators
- Voltage and CP state monitoring
- Charging profile slider control
- Soft/Hard reset buttons
- Toast notifications for user feedback
- Responsive mobile-friendly layout

## Installation

### Prerequisites

- Python 3.8 or higher
- pip package manager

### Setup

1. **Navigate to the web GUI directory:**
   ```bash
   cd GUI/web
   ```

2. **Install dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

3. **Start the application:**
   ```bash
   python app.py
   ```

4. **Access the web interface:**
   Open your browser and navigate to:
   ```
   http://localhost:5000
   ```

## Configuration

The OCPP central system is configured with the following defaults:

- **WebSocket Server**: `ws://0.0.0.0:9000`
- **TLS WebSocket Server**: `wss://0.0.0.0:9001` (if certificates are provided)
- **Web Interface**: `http://0.0.0.0:5000`

### Certificate Configuration

To enable TLS/SSL for OCPP connections, configure the certificate chain in `app.py`:

```python
central_system.configure(
    cert_chain='/path/to/certificate.pem',
    certs_dir='/path/to/iso15118/certs'
)
```

## Usage

### Connecting a Charge Point

Configure your OCPP charge point to connect to:
```
ws://YOUR_SERVER_IP:9000/YOUR_CHARGE_POINT_ID
```

### Monitoring

- **Connection Status**: Green badge indicates active OCPP connection
- **Heartbeat Indicator**: Flashes red when heartbeat messages are received
- **Ping Indicator**: Flashes blue when ping messages are exchanged
- **Voltage Reading**: Displays real-time voltage from the charge point's analog input
- **CP State**: Shows the current Control Pilot state (Level A/B/C/D/E/F)

### Charging Profile Control

Use the slider to set the charging current limit:
- **0A**: PLC communication only (no charging)
- **6-80A**: Set specific amperage limits
- Changes are automatically sent after a 500ms debounce period

### Remote Control

- **Soft Reset**: Gracefully restart the charge point
- **Hard Reset**: Force immediate restart (use with caution)

## Architecture

### Backend (app.py)

- **Flask**: Web server framework
- **Flask-SocketIO**: WebSocket support for real-time updates
- **asyncio**: Asynchronous OCPP message handling
- **websockets**: OCPP WebSocket server implementation

### Frontend

- **HTML5**: Semantic markup with responsive meta tags
- **CSS3**: Modern styling with CSS Grid, Flexbox, and animations
- **JavaScript**: Real-time updates with Socket.IO client
- **No external frameworks**: Pure JavaScript for minimal dependencies

### Real-time Communication Flow

```
Charge Point (OCPP) → WebSocket Server → Backend Callbacks → Socket.IO → Web Browser
Web Browser → HTTP/REST API → Backend → OCPP Message → Charge Point
```

## API Endpoints

### REST API

- `GET /`: Main web interface
- `GET /api/state`: Get current system state
- `POST /api/charging-profile`: Set charging profile
  ```json
  {"ampere": 32}
  ```
- `POST /api/reset/soft`: Send soft reset command
- `POST /api/reset/hard`: Send hard reset command

### WebSocket Events (Socket.IO)

#### Emitted by Server:
- `connection_status`: OCPP client connection state changes
- `heartbeat`: Heartbeat message received
- `ping`: Ping message received
- `meter_value`: Voltage and energy measurements
- `cp_status`: Control Pilot state updates

## Mobile Support

The interface is fully responsive and optimized for:
- **Desktop**: Full-featured layout with grid display
- **Tablet**: Adaptive layout with touch-friendly controls
- **Mobile**: Single-column layout, larger touch targets, optimized spacing

## Troubleshooting

### Charge Point Won't Connect

1. Verify the charge point is configured with correct WebSocket URL
2. Check firewall rules allow connections on port 9000
3. Review logs in terminal for connection attempts
4. Ensure charge point supports OCPP 1.6 or 2.0.1

### Web Interface Not Updating

1. Check browser console for WebSocket connection errors
2. Verify Socket.IO is properly connected (check console logs)
3. Refresh the page to re-establish connection
4. Ensure the backend server is running

### Commands Not Working

1. Verify charge point is connected (green status badge)
2. Check browser console for error messages
3. Review backend logs for OCPP message errors
4. Confirm charge point supports the requested operation

## Development

### File Structure

```
web/
├── app.py                 # Flask backend application
├── requirements.txt       # Python dependencies
├── templates/
│   └── index.html        # HTML template
└── static/
    ├── css/
    │   └── style.css     # Styling and responsive design
    └── js/
        └── app.js        # Frontend JavaScript logic
```

### Customization

- **Styling**: Modify CSS variables in `style.css` for colors and spacing
- **Features**: Extend backend callbacks in `app.py` for additional OCPP messages
- **UI**: Update `index.html` to add new controls or displays

## Security Notes

- Change the `SECRET_KEY` in production environments
- Use HTTPS in production (configure a reverse proxy like nginx)
- Implement authentication if exposing to the internet
- Keep dependencies updated for security patches

## License

This project is part of the EnerHance OCPP CSMS system.

## Support

For issues or questions, review the logs in the terminal where the application is running. All OCPP messages and system events are logged with timestamps.

## Version

Web GUI Version 1.0 - February 2026
