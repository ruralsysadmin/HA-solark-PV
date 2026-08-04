/**
 * Sol-Ark Modbus Web Interface Application Logic.
 * Handles API fetching, DOM updates, interactive table filtering, and config modal management.
 */

let pollTimer = null;
let currentRegisters = [];

document.addEventListener('DOMContentLoaded', () => {
  initApp();
});

function initApp() {
  setupEventListeners();
  fetchMetrics();
  startPolling();
}

function setupEventListeners() {
  // Poll rate dropdown
  document.getElementById('poll-rate').addEventListener('change', (e) => {
    startPolling(parseInt(e.target.value, 10));
  });

  // Search in register explorer
  document.getElementById('register-search').addEventListener('input', (e) => {
    filterRegisterTable(e.target.value);
  });

  // Modal controls
  const modal = document.getElementById('config-modal');
  document.getElementById('config-btn').addEventListener('click', () => {
    openConfigModal();
  });
  document.getElementById('modal-close').addEventListener('click', () => {
    modal.classList.add('hidden');
  });
  document.getElementById('modal-cancel').addEventListener('click', () => {
    modal.classList.add('hidden');
  });

  // Config Form submission
  document.getElementById('config-form').addEventListener('submit', (e) => {
    e.preventDefault();
    saveConfigModal();
  });

  // Error banner dismiss
  document.getElementById('dismiss-error').addEventListener('click', () => {
    document.getElementById('error-banner').classList.add('hidden');
  });
}

function startPolling(intervalSeconds = null) {
  if (pollTimer) clearInterval(pollTimer);

  const rate = intervalSeconds || parseInt(document.getElementById('poll-rate').value, 10);
  pollTimer = setInterval(fetchMetrics, rate * 1000);
}

async function fetchMetrics() {
  try {
    const res = await fetch('/api/metrics');
    if (!res.ok) throw new Error(`HTTP error ${res.status}`);

    const payload = await res.json();
    updateUI(payload);
  } catch (err) {
    console.error('Fetch error:', err);
    showError('Lost communication with backend server.');
  }
}

function updateUI(payload) {
  const { connected, last_error, config, data } = payload;

  // 1. Update Connection Badge
  const statusBadge = document.getElementById('status-badge');
  const statusText = document.getElementById('status-text');

  if (data.mode === 'demo') {
    statusBadge.className = 'badge badge-demo';
    statusText.textContent = 'Demo Mode (Simulation)';
  } else if (connected) {
    statusBadge.className = 'badge badge-live';
    statusText.textContent = `Live: ${config.host}:${config.port}`;
  } else {
    statusBadge.className = 'badge badge-offline';
    statusText.textContent = 'Dongle Disconnected';
  }

  // 2. Error Banner
  const errorBanner = document.getElementById('error-banner');
  const errorMsg = document.getElementById('error-message');
  if (last_error && data.mode !== 'demo') {
    errorMsg.textContent = last_error;
    errorBanner.classList.remove('hidden');
  } else {
    errorBanner.classList.add('hidden');
  }

  // 3. Timestamp
  document.getElementById('last-update').textContent = data.timestamp || new Date().toLocaleTimeString();

  // 4. Hero Energy Flow Canvas
  const summary = data.summary || {};
  const pvPower = summary.pv_power_w || 0;
  const battPower = summary.battery_power_w || 0;
  const gridPower = summary.grid_power_w || 0;
  const loadPower = summary.load_power_w || 0;
  const battSoc = summary.battery_soc_pct || 0;

  document.getElementById('flow-solar-power').textContent = `${formatNumber(pvPower)} W`;
  document.getElementById('flow-solar-sub').textContent = `Daily: ${data.solar?.daily_pv_energy_kwh || 0} kWh`;

  document.getElementById('flow-batt-power').textContent = `${formatNumber(Math.abs(battPower))} W ${battPower < 0 ? 'Charging' : (battPower > 0 ? 'Discharging' : '')}`;
  document.getElementById('flow-batt-soc').textContent = `SOC: ${battSoc}%`;

  document.getElementById('flow-grid-power').textContent = `${formatNumber(Math.abs(gridPower))} W ${gridPower < 0 ? 'Exporting' : (gridPower > 0 ? 'Importing' : '')}`;
  document.getElementById('flow-grid-status').textContent = `Grid: ${data.grid?.status || 'Connected'}`;

  document.getElementById('flow-load-power').textContent = `${formatNumber(loadPower)} W`;
  document.getElementById('flow-load-sub').textContent = `Daily: ${data.load?.daily_load_kwh || 0} kWh`;

  document.getElementById('flow-inv-temp').textContent = `DC ${data.diagnostics?.dc_temp_c || 0}°C | AC ${data.diagnostics?.ac_temp_c || 0}°C`;

  // 5. Section 1: Solar Strings
  document.getElementById('solar-total-power').textContent = `${formatNumber(data.solar?.pv_total_power_w || 0)} W`;
  document.getElementById('solar-daily-gen').textContent = `Daily Yield: ${data.solar?.daily_pv_energy_kwh || 0} kWh`;

  const stringsContainer = document.getElementById('pv-strings-container');
  stringsContainer.innerHTML = '';
  (data.solar?.strings || []).forEach((str) => {
    const row = document.createElement('div');
    row.className = 'string-row';
    row.innerHTML = `
      <span class="string-name">${str.name}</span>
      <span class="string-metrics">${str.voltage_v} V | ${str.current_a} A | <strong>${formatNumber(str.power_w)} W</strong></span>
    `;
    stringsContainer.appendChild(row);
  });

  // 6. Section 2: Battery System
  document.getElementById('batt-soc-text').textContent = `${data.battery?.soc_pct || 0}%`;
  document.getElementById('batt-soc-bar').style.width = `${data.battery?.soc_pct || 0}%`;

  document.getElementById('batt-power-val').textContent = `${formatNumber(data.battery?.power_w || 0)} W`;
  document.getElementById('batt-volt-val').textContent = `${data.battery?.voltage_v || 0} V`;
  document.getElementById('batt-curr-val').textContent = `${data.battery?.current_a || 0} A`;
  document.getElementById('batt-status-val').textContent = data.battery?.status || 'Idle';
  document.getElementById('batt-daily-chg').textContent = `${data.battery?.daily_charge_kwh || 0} kWh`;
  document.getElementById('batt-daily-disch').textContent = `${data.battery?.daily_discharge_kwh || 0} kWh`;

  // 7. Section 3: Utility Grid
  const netGrid = data.grid?.power_total_w || 0;
  document.getElementById('grid-net-power').textContent = `${formatNumber(Math.abs(netGrid))} W ${netGrid < 0 ? '(Export)' : '(Import)'}`;
  document.getElementById('grid-relay-status').textContent = `Relay Status: ${data.grid?.status || 'Connected'}`;
  document.getElementById('grid-l1-v').textContent = `${data.grid?.voltage_l1_v || 0} V`;
  document.getElementById('grid-l2-v').textContent = `${data.grid?.voltage_l2_v || 0} V`;
  document.getElementById('grid-freq-val').textContent = `${data.grid?.frequency_hz || 60} Hz`;
  document.getElementById('grid-daily-buy').textContent = `${data.grid?.daily_buy_kwh || 0} kWh`;
  document.getElementById('grid-daily-sell').textContent = `${data.grid?.daily_sell_kwh || 0} kWh`;

  // 8. Section 4: Home Load
  document.getElementById('load-total-power').textContent = `${formatNumber(data.load?.power_w || 0)} W`;
  document.getElementById('load-daily-e').textContent = `Daily Consumed: ${data.load?.daily_load_kwh || 0} kWh`;
  document.getElementById('load-freq-val').textContent = `${data.load?.frequency_hz || 60} Hz`;
  document.getElementById('inv-freq-val').textContent = `${data.load?.inverter_freq_hz || 60} Hz`;
  document.getElementById('load-tot-e').textContent = `${data.load?.total_load_kwh || 0} kWh`;

  // 9. Section 5: Diagnostics & Faults
  document.getElementById('dc-temp-val').textContent = `${data.diagnostics?.dc_temp_c || 0} °C`;
  document.getElementById('ac-temp-val').textContent = `${data.diagnostics?.ac_temp_c || 0} °C`;

  const activeFaults = data.diagnostics?.active_faults || [];
  const faultsContainer = document.getElementById('faults-container');
  const faultTag = document.getElementById('fault-count-tag');

  if (activeFaults.length === 0) {
    faultTag.textContent = 'No Active Faults';
    faultTag.style.background = 'rgba(16, 185, 129, 0.15)';
    faultTag.style.color = '#34d399';
    faultsContainer.innerHTML = '<div class="no-faults-msg">✅ System operating normally with zero active fault codes.</div>';
  } else {
    faultTag.textContent = `${activeFaults.length} Active Alarm(s)`;
    faultTag.style.background = 'rgba(239, 68, 68, 0.2)';
    faultTag.style.color = '#f87171';
    faultsContainer.innerHTML = '';
    activeFaults.forEach((f) => {
      const card = document.createElement('div');
      card.className = 'fault-item';
      card.innerHTML = `
        <span class="fault-code">${f.code}</span>
        <div>
          <strong>${f.name}</strong>
          <div style="font-size: 12px; opacity: 0.8; margin-top: 2px;">${f.description}</div>
        </div>
      `;
      faultsContainer.appendChild(card);
    });
  }

  // 10. Section 6: Register Table
  currentRegisters = data.registers || [];
  const searchTerm = document.getElementById('register-search').value;
  filterRegisterTable(searchTerm);
}

function filterRegisterTable(query = '') {
  const tbody = document.getElementById('register-table-body');
  tbody.innerHTML = '';

  const q = query.toLowerCase().trim();
  const filtered = currentRegisters.filter((r) => {
    if (!q) return true;
    return (
      r.address.toString().includes(q) ||
      r.key.toLowerCase().includes(q) ||
      r.name.toLowerCase().includes(q)
    );
  });

  filtered.forEach((r) => {
    const tr = document.createElement('tr');
    tr.innerHTML = `
      <td>R${r.address}</td>
      <td><code>${r.key}</code></td>
      <td>${r.name}</td>
      <td>${r.val}</td>
      <td>${r.unit || '-'}</td>
    `;
    tbody.appendChild(tr);
  });
}

async function openConfigModal() {
  try {
    const res = await fetch('/api/config');
    const cfg = await res.json();

    document.getElementById('cfg-host').value = cfg.host || '';
    document.getElementById('cfg-port').value = cfg.port || 502;
    document.getElementById('cfg-slave-id').value = cfg.slave_id || 1;
    document.getElementById('cfg-interval').value = cfg.scan_interval || 5;
    document.getElementById('cfg-demo').checked = !!cfg.demo_mode;

    document.getElementById('config-modal').classList.remove('hidden');
  } catch (err) {
    showError('Could not load current configuration.');
  }
}

async function saveConfigModal() {
  const newConfig = {
    host: document.getElementById('cfg-host').value.trim(),
    port: parseInt(document.getElementById('cfg-port').value, 10),
    slave_id: parseInt(document.getElementById('cfg-slave-id').value, 10),
    scan_interval: parseInt(document.getElementById('cfg-interval').value, 10),
    demo_mode: document.getElementById('cfg-demo').checked,
  };

  try {
    const res = await fetch('/api/config', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(newConfig),
    });

    if (res.ok) {
      document.getElementById('config-modal').classList.add('hidden');
      fetchMetrics();
    } else {
      showError('Failed to save configuration.');
    }
  } catch (err) {
    showError('Error posting configuration to server.');
  }
}

function showError(msg) {
  const errorBanner = document.getElementById('error-banner');
  document.getElementById('error-message').textContent = msg;
  errorBanner.classList.remove('hidden');
}

function formatNumber(num) {
  if (num === null || num === undefined) return '0';
  return num.toLocaleString('en-US');
}
