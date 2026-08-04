# Sol-Ark Modbus Web Dashboard & Dongle Bridge

A standalone, lightweight, real-time web monitoring interface and Modbus TCP API server for Sol-Ark All-In-One Solar Inverters.

Designed to connect directly to RS485-to-WiFi dongles, Modbus TCP gateways (e.g. Waveshare, Protoss-PE11, `mbusd`), or direct IP connections.

---

## Features

* ⚡ **Live Energy Flow Canvas**: Real-time power balance animation between Solar PV Strings $\rightarrow$ Inverter $\rightarrow$ Battery $\leftrightarrow$ Home Load $\leftrightarrow$ Grid.
* ☀️ **Solar Array Strings**: Breakdown of PV1, PV2, PV3 string voltages, currents, and powers.
* 🔋 **Battery Monitoring**: State of Charge (SOC %), power, voltage, current, charging/discharging status, and daily charge/discharge counters.
* 🔌 **Utility Grid Metrics**: Grid relay status, L1/L2 voltages, frequency, grid import/export power, and daily buy/sell energy.
* 🏡 **Home Consumption**: House load power, load frequency, daily load energy.
* 🌡️ **Inverter Diagnostics & Alarms**: DC/AC heatsink temperatures and active 64-bit Modbus fault code decoding (e.g., F14, F15, F34, F45, F55, F64).
* 🔍 **Interactive Register Explorer**: Searchable and filterable live table of all read Modbus registers.
* ⚙️ **In-Browser Configuration Modal**: Dynamically update dongle IP address, port (`502`), Modbus Slave ID (`1`), and scan interval.

---

## Quick Start

### 1. Configure Connection
Edit `web_dashboard/config.yaml` or `config.json` with your RS485-to-WiFi dongle IP address:

```yaml
modbus:
  host: "192.168.1.100"  # Replace with your dongle IP address
  port: 502              # Modbus TCP Port
  slave_id: 1            # Slave Drop ID
```

### 2. Launch Server
Run `server.py` using Python 3:

```bash
python web_dashboard/server.py
```

### 3. Open Web Dashboard
Open your browser and navigate to:

```text
http://localhost:8080
```

> **Offline Demo Mode**: If your dongle is not currently plugged in, set `"demo_mode": true` in `config.json` or check **Enable Offline Demo Mode** in the in-browser config modal to simulate realistic live solar generation and power balancing.
