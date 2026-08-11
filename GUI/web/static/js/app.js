// WebSocket connection
const socket = io();

// State management
let state = {
    connected: false,
    chargePointId: null,
    voltage: '0 V',
    cpState: 'Unknown'
};

// DOM Elements
const elements = {
    connectionStatus: document.getElementById('connection-status'),
    statusText: document.getElementById('status-text'),
    chargePointId: document.getElementById('charge-point-id'),
    voltageValue: document.getElementById('voltage-value'),
    cpStateValue: document.getElementById('cp-state-value'),
    heartbeatIndicator: document.getElementById('heartbeat-indicator'),
    pingIndicator: document.getElementById('ping-indicator'),
    btnRemoteStart: document.getElementById('btn-remote-start'),
    btnRemoteStop: document.getElementById('btn-remote-stop'),
    btnStartCharge: document.getElementById('btn-start-charge'),
    btnStartDischarge: document.getElementById('btn-start-discharge'),
    btnSoftReset: document.getElementById('btn-soft-reset'),
    btnHardReset: document.getElementById('btn-hard-reset'),
    toast: document.getElementById('toast'),
    tempL1: document.getElementById('temp-l1'),
    tempL2: document.getElementById('temp-l2'),
    tempL3: document.getElementById('temp-l3'),
    tempN: document.getElementById('temp-n'),
    acDetected: document.getElementById('ac-detected'),
    triggeringOnPowerOutage: document.getElementById('triggering-on-power-outage'),
    // K-VAS (EV Battery)
    kvasKeyBadge: document.getElementById('kvas-key-badge'),
    kvasKeyText: document.getElementById('kvas-key-text'),
    kvasSoc: document.getElementById('kvas-soc'),
    kvasSocLevel: document.getElementById('kvas-soc-level'),
    kvasSoh: document.getElementById('kvas-soh'),
    kvasPackVoltage: document.getElementById('kvas-pack-voltage'),
    kvasPackCurrent: document.getElementById('kvas-pack-current'),
    kvasCellVoltage: document.getElementById('kvas-cell-voltage'),
    kvasCellTemp: document.getElementById('kvas-cell-temp'),
    kvasVin: document.getElementById('kvas-vin'),
    kvasStatusKeyId: document.getElementById('kvas-status-keyid'),
    kvasStatusDecrypted: document.getElementById('kvas-status-decrypted'),
    kvasStatusHmacFailed: document.getElementById('kvas-status-hmac-failed'),
    kvasStatusUndecryptable: document.getElementById('kvas-status-undecryptable'),
    kvasStatusTsdt: document.getElementById('kvas-status-tsdt'),
    kvasStatusResultCode: document.getElementById('kvas-status-resultcode'),
    kvasHistoryBody: document.getElementById('kvas-history-body')
};

// Initialize on page load
document.addEventListener('DOMContentLoaded', () => {
    console.log('Page loaded, initializing...');
    fetchState();
    setupEventListeners();
});

// Fetch current state from server
async function fetchState() {
    try {
        const response = await fetch('/api/state');
        const data = await response.json();
        updateState(data);
    } catch (error) {
        console.error('Error fetching state:', error);
    }
}

// Update local state and UI
function updateState(newState) {
    state = { ...state, ...newState };
    updateUI();
}

// Update UI based on current state
function updateUI() {
    // Connection status
    if (state.connected) {
        elements.connectionStatus.classList.remove('status-disconnected');
        elements.connectionStatus.classList.add('status-connected');
        elements.statusText.textContent = 'Connected';
        elements.chargePointId.textContent = state.charge_point_id || '—';
        
        // Enable controls
        elements.btnStartCharge.disabled = false;
        elements.btnStartDischarge.disabled = false;
        elements.btnSoftReset.disabled = false;
        elements.btnHardReset.disabled = false;
    } else {
        elements.connectionStatus.classList.remove('status-connected');
        elements.connectionStatus.classList.add('status-disconnected');
        elements.statusText.textContent = 'Disconnected';
        elements.chargePointId.textContent = '—';
        
        // Disable controls
        elements.btnStartCharge.disabled = true;
        elements.btnStartDischarge.disabled = true;
        elements.btnSoftReset.disabled = true;
        elements.btnHardReset.disabled = true;
    }
    
    // Voltage and CP state
    elements.voltageValue.textContent = state.voltage || '0 V';
    elements.cpStateValue.textContent = state.cp_state || 'Unknown';

    // K-VAS (initial render from /api/state; live updates come via the
    // kvas_status/kvas_battery socket events below)
    if (state.kvas) {
        updateKvasStatus(state.kvas);
        if (state.kvas.last_record) {
            updateKvasRecord(state.kvas.last_record);
        }
        if (state.kvas.history && state.kvas.history.length) {
            updateKvasHistory(state.kvas.history);
        }
    }
}

// --- K-VAS helpers -----------------------------------------------------------

function fmtNum(value, digits, suffix) {
    return (value === null || value === undefined) ? '—' : `${Number(value).toFixed(digits)}${suffix}`;
}

function updateKvasStatus(kvas) {
    if (kvas.key_id) {
        elements.kvasKeyBadge.classList.remove('status-disconnected');
        elements.kvasKeyBadge.classList.add('status-connected');
        elements.kvasKeyText.textContent = `Key ${kvas.key_id}`;
    } else {
        elements.kvasKeyBadge.classList.remove('status-connected');
        elements.kvasKeyBadge.classList.add('status-disconnected');
        elements.kvasKeyText.textContent = 'No session key';
    }
    elements.kvasStatusKeyId.textContent = kvas.key_id || '—';
    elements.kvasStatusDecrypted.textContent = kvas.records_decrypted ?? 0;
    elements.kvasStatusHmacFailed.textContent = kvas.records_hmac_failed ?? 0;
    elements.kvasStatusUndecryptable.textContent = kvas.records_undecryptable ?? 0;
    elements.kvasStatusTsdt.textContent = kvas.last_tsdt || '—';
    elements.kvasStatusResultCode.textContent = kvas.last_result_code ?? '—';
}

function updateKvasRecord(tags) {
    elements.kvasSoc.textContent = fmtNum(tags.soc_percent, 1, ' %');
    elements.kvasSocLevel.style.width = `${Math.max(0, Math.min(100, tags.soc_percent ?? 0))}%`;
    elements.kvasSoh.textContent = fmtNum(tags.soh_percent, 0, ' %');
    elements.kvasPackVoltage.textContent = fmtNum(tags.pack_voltage_v, 1, ' V');
    elements.kvasPackCurrent.textContent = fmtNum(tags.pack_current_a, 1, ' A');
    elements.kvasCellVoltage.textContent =
        `${fmtNum(tags.cell_voltage_max_v, 2, '')} / ${fmtNum(tags.cell_voltage_min_v, 2, '')} V`;
    elements.kvasCellTemp.textContent =
        `${fmtNum(tags.cell_temp_max_c, 0, '')} / ${fmtNum(tags.cell_temp_min_c, 0, '')} °C`;
    elements.kvasVin.textContent = tags.vin || '—';
}

function updateKvasHistory(history) {
    if (!history.length) {
        return;
    }
    // Newest first in the table.
    const rows = history.slice().reverse().map((r) => `
        <tr>
            <td>${r.timeStamp ?? '—'}</td>
            <td>${r.counter ?? '—'}</td>
            <td>${fmtNum(r.soc, 1, '')}</td>
            <td>${fmtNum(r.soh, 0, '')}</td>
            <td>${fmtNum(r.pack_voltage_v, 1, '')}</td>
            <td>${fmtNum(r.pack_current_a, 1, '')}</td>
            <td>${fmtNum(r.cell_voltage_max_v, 2, '')} / ${fmtNum(r.cell_voltage_min_v, 2, '')}</td>
            <td>${fmtNum(r.cell_temp_max_c, 0, '')} / ${fmtNum(r.cell_temp_min_c, 0, '')}</td>
            <td>${r.vin || '—'}</td>
        </tr>`).join('');
    elements.kvasHistoryBody.innerHTML = rows;
}

// Setup event listeners
function setupEventListeners() {
    // Transaction control buttons
    elements.btnRemoteStart.addEventListener('click', () => remoteStartTransaction());
    elements.btnRemoteStop.addEventListener('click', () => remoteStopTransaction());
    
    // Charge control buttons
    elements.btnStartCharge.addEventListener('click', () => startCharging());
    elements.btnStartDischarge.addEventListener('click', () => startDischarging());
    
    // Reset buttons
    elements.btnSoftReset.addEventListener('click', () => sendReset('soft'));
    elements.btnHardReset.addEventListener('click', () => sendReset('hard'));
}

// Remote start transaction
async function remoteStartTransaction() {
    if (!state.connected) {
        showToast('Cannot start transaction: No OCPP client connected', 'error');
        return;
    }
    
    try {
        const response = await fetch('/api/transaction/start', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({ 
                connector_id: 1,
                id_tag: 'WEB_INTERFACE'
            })
        });
        
        const data = await response.json();
        
        if (data.success) {
            showToast(data.message, 'success');
        } else {
            showToast(data.message, 'error');
        }
    } catch (error) {
        console.error('Error starting transaction:', error);
        showToast('Error starting transaction', 'error');
    }
}

// Remote stop transaction
async function remoteStopTransaction() {
    if (!state.connected) {
        showToast('Cannot stop transaction: No OCPP client connected', 'error');
        return;
    }
    
    try {
        const response = await fetch('/api/transaction/stop', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({})
        });
        
        const data = await response.json();
        
        if (data.success) {
            showToast(data.message, 'success');
        } else {
            showToast(data.message, 'error');
        }
    } catch (error) {
        console.error('Error stopping transaction:', error);
        showToast('Error stopping transaction', 'error');
    }
}

// Start charging
async function startCharging() {
    if (!state.connected) {
        showToast('Cannot start charging: No OCPP client connected', 'error');
        return;
    }
    
    try {
        const response = await fetch('/api/charge/start', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({ ampere: 16 })
        });
        
        const data = await response.json();
        
        if (data.success) {
            showToast(data.message, 'success');
        } else {
            showToast(data.message, 'error');
        }
    } catch (error) {
        console.error('Error starting charging:', error);
        showToast('Error starting charging', 'error');
    }
}

// Start discharging
async function startDischarging() {
    if (!state.connected) {
        showToast('Cannot start discharging: No OCPP client connected', 'error');
        return;
    }
    
    try {
        const response = await fetch('/api/charge/discharge', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({ ampere: 15 })
        });
        
        const data = await response.json();
        
        if (data.success) {
            showToast(data.message, 'success');
        } else {
            showToast(data.message, 'error');
        }
    } catch (error) {
        console.error('Error starting discharging:', error);
        showToast('Error starting discharging', 'error');
    }
}

// Send reset command
async function sendReset(type) {
    if (!state.connected) {
        showToast('Cannot send reset: No OCPP client connected', 'error');
        return;
    }
    
    const confirmMessage = type === 'hard' 
        ? 'Are you sure you want to perform a HARD RESET? This will forcefully restart the charge point.'
        : 'Are you sure you want to perform a SOFT RESET?';
    
    if (!confirm(confirmMessage)) {
        return;
    }
    
    try {
        const response = await fetch(`/api/reset/${type}`, {
            method: 'POST'
        });
        
        const data = await response.json();
        
        if (data.success) {
            showToast(data.message, 'success');
        } else {
            showToast(data.message, 'error');
        }
    } catch (error) {
        console.error(`Error sending ${type} reset:`, error);
        showToast(`Error sending ${type} reset`, 'error');
    }
}

// Show toast notification
function showToast(message, type = 'info') {
    elements.toast.textContent = message;
    elements.toast.className = `toast ${type}`;
    elements.toast.classList.add('show');
    
    setTimeout(() => {
        elements.toast.classList.remove('show');
    }, 3000);
}

// Flash indicator
function flashIndicator(indicator, className) {
    indicator.classList.add(className);
    setTimeout(() => {
        indicator.classList.remove(className);
    }, 500);
}

// WebSocket event handlers
socket.on('connect', () => {
    console.log('WebSocket connected');
    showToast('Connected to server', 'success');
});

socket.on('disconnect', () => {
    console.log('WebSocket disconnected');
    showToast('Disconnected from server', 'error');
});

socket.on('connection_status', (data) => {
    console.log('Connection status update:', data);
    state.connected = data.connected;
    state.charge_point_id = data.charge_point_id;
    updateUI();
    
    if (data.connected) {
        showToast(`OCPP client connected: ${data.charge_point_id}`, 'success');
    } else {
        showToast('OCPP client disconnected', 'error');
    }
});

socket.on('heartbeat', (data) => {
    console.log('Heartbeat received:', data);
    flashIndicator(elements.heartbeatIndicator, 'active');
});

socket.on('ping', (data) => {
    console.log('Ping received:', data);
    flashIndicator(elements.pingIndicator, 'ping-active');
});

socket.on('meter_value', (data) => {
    console.log('Meter value update:', data);
    
    if (data.voltage !== undefined) {
        state.voltage = data.voltage;
        elements.voltageValue.textContent = data.voltage;
    }
});

socket.on('cp_status', (data) => {
    console.log('CP status update:', data);
    state.cp_state = data.status;
    elements.cpStateValue.textContent = data.status;
});

socket.on('temperature_update', (data) => {
    console.log('Temperature update:', data);
    const temps = data.temperatures;
    
    if (temps.L1 !== null && temps.L1 !== undefined) {
        elements.tempL1.textContent = `${temps.L1.toFixed(1)}°C`;
    }
    if (temps.L2 !== null && temps.L2 !== undefined) {
        elements.tempL2.textContent = `${temps.L2.toFixed(1)}°C`;
    }
    if (temps.L3 !== null && temps.L3 !== undefined) {
        elements.tempL3.textContent = `${temps.L3.toFixed(1)}°C`;
    }
    if (temps.N !== null && temps.N !== undefined) {
        elements.tempN.textContent = `${temps.N.toFixed(1)}°C`;
    }
    
    // Update AC detected status
    if (data.ac_detected !== null && data.ac_detected !== undefined) {
        elements.acDetected.textContent = data.ac_detected ? 'Yes' : 'No';
        elements.acDetected.style.color = data.ac_detected ? '#4CAF50' : '#f44336';
    }
    
    // Update triggering on power outage status
    if (data.triggering_on_power_outage !== null && data.triggering_on_power_outage !== undefined) {
        elements.triggeringOnPowerOutage.textContent = data.triggering_on_power_outage ? 'Yes' : 'No';
        elements.triggeringOnPowerOutage.style.color = data.triggering_on_power_outage ? '#FF9800' : '#4CAF50';
    }
});

socket.on('kvas_status', (data) => {
    console.log('K-VAS status update:', data);
    updateKvasStatus(data);
});

socket.on('kvas_battery', (data) => {
    console.log('K-VAS battery record:', data);
    if (data.record) {
        updateKvasRecord(data.record);
    }
    if (data.history) {
        updateKvasHistory(data.history);
    }
});

// Error handling
window.addEventListener('error', (event) => {
    console.error('JavaScript error:', event.error);
});

// Handle visibility change (refresh state when page becomes visible)
document.addEventListener('visibilitychange', () => {
    if (!document.hidden) {
        fetchState();
    }
});

// Periodic state refresh (every 30 seconds as backup)
setInterval(() => {
    if (!document.hidden) {
        fetchState();
    }
}, 30000);

console.log('App initialized');
