#!/usr/bin/env python3
"""
Sol-Ark Modbus Web Interface Server & RS485-to-WiFi Dongle Bridge.

Provides a local REST API and HTTP server for real-time monitoring of Sol-Ark
inverters via Modbus TCP (RS485-to-WiFi / Ethernet gateways).
"""

from __future__ import annotations

import json
import logging
import math
import os
import re
import socket
import struct
import sys
import threading
import time
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from urllib.parse import parse_qs, urlparse

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
_LOGGER = logging.getLogger("solark_web_server")

BASE_DIR = Path(__file__).parent.resolve()
PUBLIC_DIR = BASE_DIR / "public"
CONFIG_FILE = BASE_DIR / "config.json"

# Sol-Ark Fault Lookup Table (Bit index 1 to 64 -> Code, Name, Description)
FAULT_TABLE: dict[int, tuple[str, str, str]] = {
    1: ("F1", "DC_Inversed_Failure", "Parallel unit off — notification, not a fault."),
    8: ("F8", "GFDI_Relay_Failure", "Check continuity on inverter neutral and ground; single neutral-to-ground bond."),
    13: ("F13", "Grid_Mode_change", "Grid/battery mode change — informational, not a fault."),
    14: ("F14", "DC_OverCurr_Failure", "High DC current; check loads and PV input."),
    15: ("F15", "AC_OverCurr_Failure", "AC load too large or battery discharge amps too low; reduce loads."),
    16: ("F16", "GFCI_Failure", "Ground fault circuit interrupter failure; check PV wiring and grounding."),
    18: ("F18", "Tz_AC_OverCurr_Fault", "AC overload or generator overload; inspect AC wiring and loads."),
    20: ("F20", "Tz_Dc_OverCurr_Fault", "DC current too high — excess PV or battery current."),
    22: ("F22", "Tz_EmergStop_Fault", "Emergency stop signal detected; verify stops/sensors."),
    23: ("F23", "Tz_GFCI_OC_Fault", "GFCI/PV overcurrent fault; check PV conductor insulation."),
    24: ("F24", "DC_Insulation_Fault", "PV insulation failure (moisture/exposed conductor)."),
    25: ("F25", "DC_Feedback_Fault", "No battery connected but 'Activate Battery' enabled."),
    26: ("F26", "BusUnbalance_Fault", "Uneven AC leg load or DC on AC output when off-grid."),
    29: ("F29", "Parallel_CANBus_Fault", "Parallel system communication error; check comm cables/MODBUS IDs."),
    30: ("F30", "AC_MainContactor_Fault", "AC main contactor fault — service contactor hardware."),
    31: ("F31", "Soft_Start_Failed", "Soft start of large motor failed; inspect load startup current."),
    34: ("F34", "AC_Overload_Fault", "AC overload/short; reduce heavy or faulty loads."),
    35: ("F35", "AC_NoUtility_Fault", "Grid connection lost — check utility supply."),
    37: ("F37", "DCLLC_Soft_Over_Cur", "Software DC overcurrent; inspect PV/battery inputs."),
    39: ("F39", "DCLLC_Over_Current", "Hardware DC overcurrent; reduce DC input."),
    40: ("F40", "Batt_Over_Current", "Battery discharge exceeds current limit; check battery settings."),
    41: ("F41", "Parallel_System_Stop_Fault", "A parallel master/slave disconnect triggered stop."),
    45: ("F45", "AC_UV_OverVolt_Fault", "AC undervoltage/overvoltage — self reset when grid stabilizes."),
    46: ("F46", "Battery_Backup_Fault", "No communication with parallel systems; check Master/Slave & ethernet."),
    47: ("F47", "AC_OverFreq_Fault", "Grid over-frequency disconnect — self reset when stable."),
    48: ("F48", "AC_UnderFreq_Fault", "Grid under-frequency disconnect — self reset when stable."),
    55: ("F55", "DC_VoltHigh_Fault", "PV voltage above spec (>500V) or high battery voltage."),
    56: ("F56", "DC_VoltLow_Fault", "Batteries over-discharged or incorrect batt settings."),
    58: ("F58", "BMS_Communication_Fault", "Cannot communicate with Lithium BMS while enabled."),
    60: ("F60", "Gen_Volt_or_Fre_Fault", "Generator voltage or frequency out of allowable range."),
    61: ("F61", "Button_Manual_OFF", "Parallel slave turned off without Master."),
    63: ("F63", "Arc_Fault", "Arc fault — check PV connectors/cabling."),
    64: ("F64", "Heatsink_HighTemp_Fault", "Heatsink over-temperature; check fans & cooling clearance."),
}

DEFAULT_CONFIG = {
    "host": "192.168.1.100",
    "port": 502,
    "slave_id": 1,
    "timeout": 3,
    "scan_interval": 5,
    "server_port": 8080,
    "demo_mode": False,
}


def load_config() -> dict:
    """Load configuration from JSON file or create defaults."""
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                return {**DEFAULT_CONFIG, **data}
        except Exception as err:
            _LOGGER.warning("Could not read config.json, using defaults: %s", err)
    return DEFAULT_CONFIG.copy()


def save_config(cfg: dict) -> None:
    """Save configuration to JSON file."""
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2)
        _LOGGER.info("Configuration saved to %s", CONFIG_FILE)
    except Exception as err:
        _LOGGER.error("Failed to save config.json: %s", err)


class ModbusTcpRawClient:
    """Lightweight, zero-dependency Modbus TCP client for reading holding registers."""

    def __init__(self, host: str, port: int = 502, timeout: float = 3.0):
        self.host = host
        self.port = port
        self.timeout = timeout
        self.transaction_id = 1

    def read_holding_registers(self, address: int, count: int, unit_id: int = 1) -> list[int] | None:
        """Read holding registers via raw Modbus TCP socket call (Function Code 0x03)."""
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(self.timeout)
            sock.connect((self.host, self.port))

            tx_id = self.transaction_id
            self.transaction_id = (self.transaction_id + 1) & 0xFFFF

            # MBAP Header: Transaction ID (2B), Protocol ID (2B=0), Length (2B), Unit ID (1B)
            # PDU: Function Code (1B=0x03), Start Address (2B), Quantity (2B)
            pdu = struct.pack(">B B H H", unit_id, 3, address, count)
            length = len(pdu)
            mbap = struct.pack(">H H H", tx_id, 0, length)
            request = mbap + pdu

            sock.sendall(request)
            response = sock.recv(512)
            sock.close()

            if len(response) < 9:
                return None

            resp_tx_id, resp_proto, resp_len, resp_unit, resp_fc, byte_cnt = struct.unpack(">H H H B B B", response[:9])
            if resp_fc != 3:
                _LOGGER.warning("Modbus error response code: 0x%02X", resp_fc)
                return None

            payload = response[9 : 9 + byte_cnt]
            if len(payload) < count * 2:
                return None

            registers = list(struct.unpack(f">{count}H", payload))
            return registers
        except Exception as exc:
            _LOGGER.debug("Modbus TCP read error at %s:%d (Addr %d): %s", self.host, self.port, address, exc)
            return None


class SolArkDataPoller:
    """Background data collector for Sol-Ark inverter metrics."""

    def __init__(self):
        self.config = load_config()
        self.lock = threading.Lock()
        self.last_data = {}
        self.last_update_timestamp = 0.0
        self.is_connected = False
        self.last_error = None

        self.thread = threading.Thread(target=self._poll_loop, daemon=True)
        self.thread.start()

    def update_config(self, new_config: dict):
        with self.lock:
            self.config.update(new_config)
            save_config(self.config)

    def _poll_loop(self):
        while True:
            try:
                cfg = self.config.copy()
                demo_mode = cfg.get("demo_mode", False)

                if demo_mode:
                    data = self._generate_demo_data()
                    with self.lock:
                        self.last_data = data
                        self.last_update_timestamp = time.time()
                        self.is_connected = True
                        self.last_error = None
                else:
                    client = ModbusTcpRawClient(host=cfg["host"], port=cfg["port"], timeout=cfg["timeout"])
                    data = self._read_real_inverter(client, cfg["slave_id"])

                    with self.lock:
                        if data:
                            self.last_data = data
                            self.last_update_timestamp = time.time()
                            self.is_connected = True
                            self.last_error = None
                        else:
                            self.is_connected = False
                            self.last_error = f"Cannot connect to Modbus gateway at {cfg['host']}:{cfg['port']}"
                            # If connection fails, fall back to demo data so UI remains interactive
                            if not self.last_data:
                                self.last_data = self._generate_demo_data()

            except Exception as err:
                _LOGGER.exception("Unexpected error in poll loop: %s", err)
                with self.lock:
                    self.is_connected = False
                    self.last_error = str(err)

            time.sleep(max(2, self.config.get("scan_interval", 5)))

    def _read_real_inverter(self, client: ModbusTcpRawClient, slave_id: int) -> dict | None:
        """Read all register ranges from the physical inverter via Modbus TCP."""
        # Read range 60-79 (Daily Inverter, Daily Batt C/D, Daily Grid Buy/Sell, Grid Freq)
        r60_79 = client.read_holding_registers(60, 20, slave_id)
        if not r60_79:
            return None

        # Read range 84-91 (Daily Load, Total Load, Heatsink Temps)
        r84_91 = client.read_holding_registers(84, 8, slave_id)
        # Read range 96-108 (Total PV Energy, Fault Info, Batt Cap, Daily PV)
        r96_108 = client.read_holding_registers(96, 13, slave_id)
        # Read range 109-114 (PV Voltages and Currents)
        r109_114 = client.read_holding_registers(109, 6, slave_id)
        # Read range 150-170 (Grid L1/L2 Voltages and Powers)
        r150_170 = client.read_holding_registers(150, 21, slave_id)
        # Read range 183-196 (Battery SOC/Volt/Power/Current, PV Powers, Relays)
        r183_196 = client.read_holding_registers(183, 14, slave_id)

        # Parse registers
        daily_inv_e = round(r60_79[0] * 0.1, 2)
        daily_batt_c_e = round(r60_79[10] * 0.1, 2) if len(r60_79) > 10 else 0.0
        daily_batt_d_e = round(r60_79[11] * 0.1, 2) if len(r60_79) > 11 else 0.0
        daily_grid_buy = round(r60_79[16] * 0.1, 2) if len(r60_79) > 16 else 0.0
        daily_grid_sell = round(r60_79[17] * 0.1, 2) if len(r60_79) > 17 else 0.0
        grid_freq = round(r60_79[19] * 0.01, 2) if len(r60_79) > 19 else 60.0

        daily_load_e = round((r84_91[0] if r84_91 else 0) * 0.1, 2)
        tot_load_e = round(((r84_91[1] | (r84_91[2] << 16)) if r84_91 and len(r84_91) > 2 else 0) * 0.1, 1)
        dc_temp_c = round(((r84_91[6] if r84_91 else 1000) - 1000) * 0.1, 1)
        ac_temp_c = round(((r84_91[7] if r84_91 else 1000) - 1000) * 0.1, 1)

        # Fault bitmap (R103-R106 = 4 words)
        fault_raw = 0
        if r96_108 and len(r96_108) >= 11:
            w0, w1, w2, w3 = r96_108[7], r96_108[8], r96_108[9], r96_108[10]
            fault_raw = w0 | (w1 << 16) | (w2 << 32) | (w3 << 48)

        daily_pv_e = round((r96_108[12] if r96_108 and len(r96_108) > 12 else 0) * 0.1, 2)

        pv1_v = round((r109_114[0] if r109_114 else 0) * 0.1, 1)
        pv1_c = round((r109_114[1] if r109_114 else 0) * 0.1, 1)
        pv2_v = round((r109_114[2] if r109_114 else 0) * 0.1, 1)
        pv2_c = round((r109_114[3] if r109_114 else 0) * 0.1, 1)
        pv3_v = round((r109_114[4] if r109_114 else 0) * 0.1, 1)
        pv3_c = round((r109_114[5] if r109_114 else 0) * 0.1, 1)

        grid_l1_v = round((r150_170[0] if r150_170 else 0) * 0.1, 1)
        grid_l2_v = round((r150_170[1] if r150_170 else 0) * 0.1, 1)

        # Signed 16-bit helper
        def to_s16(val):
            return val - 65536 if val > 32767 else val

        grid_l1_p = to_s16(r150_170[17]) if r150_170 and len(r150_170) > 17 else 0
        grid_l2_p = to_s16(r150_170[18]) if r150_170 and len(r150_170) > 18 else 0
        grid_tot_p = to_s16(r150_170[19]) if r150_170 and len(r150_170) > 19 else 0

        batt_v = round((r183_196[0] if r183_196 else 0) * 0.01, 2)
        batt_soc = r183_196[1] if r183_196 else 0
        pv1_p = r183_196[3] if r183_196 else 0
        pv2_p = r183_196[4] if r183_196 else 0
        pv3_p = r183_196[5] if r183_196 else 0
        pv_tot_p = pv1_p + pv2_p + pv3_p

        batt_p = to_s16(r183_196[7]) if r183_196 and len(r183_196) > 7 else 0
        batt_c = round(to_s16(r183_196[8]) * 0.01, 2) if r183_196 and len(r183_196) > 8 else 0.0
        load_freq = round((r183_196[9] if r183_196 and len(r183_196) > 9 else 0) * 0.01, 2)
        inv_freq = round((r183_196[10] if r183_196 and len(r183_196) > 10 else 0) * 0.01, 2)
        grid_relay_raw = r183_196[11] if r183_196 and len(r183_196) > 11 else 0
        gen_relay_raw = r183_196[12] if r183_196 and len(r183_196) > 12 else 0

        # Decode active faults
        active_faults = []
        for bit in range(64):
            if fault_raw & (1 << bit):
                fn = bit + 1
                if fn in FAULT_TABLE:
                    code, name, desc = FAULT_TABLE[fn]
                else:
                    code, name, desc = f"F{fn}", "Unknown_Fault", f"Unknown fault bit {bit}"
                active_faults.append({"code": code, "name": name, "description": desc, "bit": bit})

        # Calculate estimated house load power
        # Load Power = PV Total Power + Battery Discharge Power - Grid Export Power
        # Note: batt_p > 0 is discharging, < 0 is charging; grid_tot_p > 0 is importing, < 0 is exporting
        batt_disch_p = max(0, batt_p)
        batt_chg_p = max(0, -batt_p)
        grid_import_p = max(0, grid_tot_p)
        grid_export_p = max(0, -grid_tot_p)
        load_power = max(0, pv_tot_p + batt_disch_p + grid_import_p - batt_chg_p - grid_export_p)

        return {
            "mode": "live",
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
            "summary": {
                "pv_power_w": pv_tot_p,
                "battery_power_w": batt_p,
                "battery_soc_pct": batt_soc,
                "grid_power_w": grid_tot_p,
                "load_power_w": load_power,
                "grid_connected": grid_relay_raw == 1,
            },
            "solar": {
                "pv_total_power_w": pv_tot_p,
                "daily_pv_energy_kwh": daily_pv_e,
                "strings": [
                    {"name": "PV String 1", "voltage_v": pv1_v, "current_a": pv1_c, "power_w": pv1_p},
                    {"name": "PV String 2", "voltage_v": pv2_v, "current_a": pv2_c, "power_w": pv2_p},
                    {"name": "PV String 3", "voltage_v": pv3_v, "current_a": pv3_c, "power_w": pv3_p},
                ],
            },
            "battery": {
                "soc_pct": batt_soc,
                "power_w": batt_p,
                "current_a": batt_c,
                "voltage_v": batt_v,
                "status": "Charging" if batt_p < 0 else ("Discharging" if batt_p > 0 else "Idle"),
                "daily_charge_kwh": daily_batt_c_e,
                "daily_discharge_kwh": daily_batt_d_e,
            },
            "grid": {
                "status": "Connected" if grid_relay_raw == 1 else "Disconnected / Outage",
                "relay_code": grid_relay_raw,
                "frequency_hz": grid_freq,
                "voltage_l1_v": grid_l1_v,
                "voltage_l2_v": grid_l2_v,
                "power_l1_w": grid_l1_p,
                "power_l2_w": grid_l2_p,
                "power_total_w": grid_tot_p,
                "daily_buy_kwh": daily_grid_buy,
                "daily_sell_kwh": daily_grid_sell,
            },
            "load": {
                "power_w": load_power,
                "daily_load_kwh": daily_load_e,
                "total_load_kwh": tot_load_e,
                "frequency_hz": load_freq,
                "inverter_freq_hz": inv_freq,
            },
            "diagnostics": {
                "dc_temp_c": dc_temp_c,
                "ac_temp_c": ac_temp_c,
                "fault_raw_bitmap": str(fault_raw),
                "active_faults": active_faults,
            },
            "registers": [
                {"address": 60, "key": "dailyinv_e", "name": "Daily Inverter Energy", "val": daily_inv_e, "unit": "kWh"},
                {"address": 70, "key": "daybattc_e", "name": "Daily Battery Charge Energy", "val": daily_batt_c_e, "unit": "kWh"},
                {"address": 71, "key": "daybattd_e", "name": "Daily Battery Discharge Energy", "val": daily_batt_d_e, "unit": "kWh"},
                {"address": 76, "key": "dailygridbuy_e", "name": "Daily Grid Buy Energy", "val": daily_grid_buy, "unit": "kWh"},
                {"address": 77, "key": "dailygridsell_e", "name": "Daily Grid Sell Energy", "val": daily_grid_sell, "unit": "kWh"},
                {"address": 79, "key": "gridfreq", "name": "Grid Frequency", "val": grid_freq, "unit": "Hz"},
                {"address": 84, "key": "dailyload_e", "name": "Daily Load Energy", "val": daily_load_e, "unit": "kWh"},
                {"address": 85, "key": "totalload_e", "name": "Total Load Energy", "val": tot_load_e, "unit": "kWh"},
                {"address": 90, "key": "dchstempc", "name": "DC Heatsink Temperature", "val": dc_temp_c, "unit": "°C"},
                {"address": 91, "key": "achstempc", "name": "AC Heatsink Temperature", "val": ac_temp_c, "unit": "°C"},
                {"address": 108, "key": "dailypv_e", "name": "Daily PV Energy", "val": daily_pv_e, "unit": "kWh"},
                {"address": 109, "key": "pv1_v", "name": "PV1 Voltage", "val": pv1_v, "unit": "V"},
                {"address": 110, "key": "pv1_c", "name": "PV1 Current", "val": pv1_c, "unit": "A"},
                {"address": 111, "key": "pv2_v", "name": "PV2 Voltage", "val": pv2_v, "unit": "V"},
                {"address": 112, "key": "pv2_c", "name": "PV2 Current", "val": pv2_c, "unit": "A"},
                {"address": 113, "key": "pv3_v", "name": "PV3 Voltage", "val": pv3_v, "unit": "V"},
                {"address": 114, "key": "pv3_c", "name": "PV3 Current", "val": pv3_c, "unit": "A"},
                {"address": 150, "key": "gridl1n_v", "name": "Grid L1-N Voltage", "val": grid_l1_v, "unit": "V"},
                {"address": 151, "key": "gridl2n_v", "name": "Grid L2-N Voltage", "val": grid_l2_v, "unit": "V"},
                {"address": 167, "key": "gridl1_p", "name": "Grid L1 Power", "val": grid_l1_p, "unit": "W"},
                {"address": 168, "key": "gridl2_p", "name": "Grid L2 Power", "val": grid_l2_p, "unit": "W"},
                {"address": 169, "key": "grid_p", "name": "Total Grid Power", "val": grid_tot_p, "unit": "W"},
                {"address": 183, "key": "batt_v", "name": "Battery Voltage", "val": batt_v, "unit": "V"},
                {"address": 184, "key": "batt_soc", "name": "Battery State of Charge", "val": batt_soc, "unit": "%"},
                {"address": 186, "key": "pv1_p", "name": "PV1 Power", "val": pv1_p, "unit": "W"},
                {"address": 187, "key": "pv2_p", "name": "PV2 Power", "val": pv2_p, "unit": "W"},
                {"address": 188, "key": "pv3_p", "name": "PV3 Power", "val": pv3_p, "unit": "W"},
                {"address": 190, "key": "batt_p", "name": "Battery Power", "val": batt_p, "unit": "W"},
                {"address": 191, "key": "batt_c", "name": "Battery Current", "val": batt_c, "unit": "A"},
                {"address": 194, "key": "grid_rly_raw", "name": "Grid Relay Raw", "val": grid_relay_raw, "unit": ""},
                {"address": 195, "key": "gen_rly_raw", "name": "Generator Relay Raw", "val": gen_relay_raw, "unit": ""},
            ],
        }

    def _generate_demo_data(self) -> dict:
        """Generate realistic dynamic solar inverter metrics for demonstration & offline testing."""
        now = time.time()
        # Simulate daytime sun curve
        sun_factor = max(0.0, math.sin((now % 86400) / 86400 * math.pi * 2 - math.pi / 2))
        pv1_p = int(2800 * sun_factor + (math.sin(now / 5) * 150))
        pv2_p = int(2400 * sun_factor + (math.cos(now / 7) * 100))
        pv3_p = int(2200 * sun_factor + (math.sin(now / 11) * 120))
        pv_tot_p = max(0, pv1_p + pv2_p + pv3_p)

        load_power = int(1800 + math.sin(now / 13) * 450)
        # Power balance: Net power = Solar - Load
        net = pv_tot_p - load_power

        if net > 0:  # Excess solar -> charge battery up to 3500W, export rest to grid
            batt_p = -min(3500, net)
            grid_p = -(net + batt_p)
        else:  # Deficit -> discharge battery
            batt_p = min(4000, -net)
            grid_p = -net - batt_p

        batt_soc = min(100, max(10, int(85 + math.sin(now / 300) * 12)))
        batt_v = round(52.0 + (batt_soc / 100.0) * 4.4, 2)
        batt_c = round(-batt_p / batt_v, 2)

        return {
            "mode": "demo",
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
            "summary": {
                "pv_power_w": pv_tot_p,
                "battery_power_w": batt_p,
                "battery_soc_pct": batt_soc,
                "grid_power_w": grid_p,
                "load_power_w": load_power,
                "grid_connected": True,
            },
            "solar": {
                "pv_total_power_w": pv_tot_p,
                "daily_pv_energy_kwh": 34.5,
                "strings": [
                    {"name": "PV String 1", "voltage_v": 365.2, "current_a": round(pv1_p / 365.2, 1) if pv1_p else 0.0, "power_w": pv1_p},
                    {"name": "PV String 2", "voltage_v": 358.8, "current_a": round(pv2_p / 358.8, 1) if pv2_p else 0.0, "power_w": pv2_p},
                    {"name": "PV String 3", "voltage_v": 372.1, "current_a": round(pv3_p / 372.1, 1) if pv3_p else 0.0, "power_w": pv3_p},
                ],
            },
            "battery": {
                "soc_pct": batt_soc,
                "power_w": batt_p,
                "current_a": batt_c,
                "voltage_v": batt_v,
                "status": "Charging" if batt_p < 0 else ("Discharging" if batt_p > 0 else "Idle"),
                "daily_charge_kwh": 14.2,
                "daily_discharge_kwh": 8.6,
            },
            "grid": {
                "status": "Connected",
                "relay_code": 1,
                "frequency_hz": 60.01,
                "voltage_l1_v": 121.4,
                "voltage_l2_v": 120.9,
                "power_l1_w": int(grid_p / 2),
                "power_l2_w": int(grid_p / 2),
                "power_total_w": grid_p,
                "daily_buy_kwh": 4.1,
                "daily_sell_kwh": 18.7,
            },
            "load": {
                "power_w": load_power,
                "daily_load_kwh": 22.4,
                "total_load_kwh": 14850.2,
                "frequency_hz": 60.00,
                "inverter_freq_hz": 60.00,
            },
            "diagnostics": {
                "dc_temp_c": 38.5,
                "ac_temp_c": 41.2,
                "fault_raw_bitmap": "0",
                "active_faults": [],
            },
            "registers": [
                {"address": 60, "key": "dailyinv_e", "name": "Daily Inverter Energy", "val": 34.5, "unit": "kWh"},
                {"address": 70, "key": "daybattc_e", "name": "Daily Battery Charge Energy", "val": 14.2, "unit": "kWh"},
                {"address": 71, "key": "daybattd_e", "name": "Daily Battery Discharge Energy", "val": 8.6, "unit": "kWh"},
                {"address": 76, "key": "dailygridbuy_e", "name": "Daily Grid Buy Energy", "val": 4.1, "unit": "kWh"},
                {"address": 77, "key": "dailygridsell_e", "name": "Daily Grid Sell Energy", "val": 18.7, "unit": "kWh"},
                {"address": 79, "key": "gridfreq", "name": "Grid Frequency", "val": 60.01, "unit": "Hz"},
                {"address": 84, "key": "dailyload_e", "name": "Daily Load Energy", "val": 22.4, "unit": "kWh"},
                {"address": 85, "key": "totalload_e", "name": "Total Load Energy", "val": 14850.2, "unit": "kWh"},
                {"address": 90, "key": "dchstempc", "name": "DC Heatsink Temperature", "val": 38.5, "unit": "°C"},
                {"address": 91, "key": "achstempc", "name": "AC Heatsink Temperature", "val": 41.2, "unit": "°C"},
                {"address": 108, "key": "dailypv_e", "name": "Daily PV Energy", "val": 34.5, "unit": "kWh"},
                {"address": 109, "key": "pv1_v", "name": "PV1 Voltage", "val": 365.2, "unit": "V"},
                {"address": 110, "key": "pv1_c", "name": "PV1 Current", "val": round(pv1_p / 365.2, 1) if pv1_p else 0, "unit": "A"},
                {"address": 111, "key": "pv2_v", "name": "PV2 Voltage", "val": 358.8, "unit": "V"},
                {"address": 112, "key": "pv2_c", "name": "PV2 Current", "val": round(pv2_p / 358.8, 1) if pv2_p else 0, "unit": "A"},
                {"address": 113, "key": "pv3_v", "name": "PV3 Voltage", "val": 372.1, "unit": "V"},
                {"address": 114, "key": "pv3_c", "name": "PV3 Current", "val": round(pv3_p / 372.1, 1) if pv3_p else 0, "unit": "A"},
                {"address": 150, "key": "gridl1n_v", "name": "Grid L1-N Voltage", "val": 121.4, "unit": "V"},
                {"address": 151, "key": "gridl2n_v", "name": "Grid L2-N Voltage", "val": 120.9, "unit": "V"},
                {"address": 167, "key": "gridl1_p", "name": "Grid L1 Power", "val": int(grid_p / 2), "unit": "W"},
                {"address": 168, "key": "gridl2_p", "name": "Grid L2 Power", "val": int(grid_p / 2), "unit": "W"},
                {"address": 169, "key": "grid_p", "name": "Total Grid Power", "val": grid_p, "unit": "W"},
                {"address": 183, "key": "batt_v", "name": "Battery Voltage", "val": batt_v, "unit": "V"},
                {"address": 184, "key": "batt_soc", "name": "Battery State of Charge", "val": batt_soc, "unit": "%"},
                {"address": 186, "key": "pv1_p", "name": "PV1 Power", "val": pv1_p, "unit": "W"},
                {"address": 187, "key": "pv2_p", "name": "PV2 Power", "val": pv2_p, "unit": "W"},
                {"address": 188, "key": "pv3_p", "name": "PV3 Power", "val": pv3_p, "unit": "W"},
                {"address": 190, "key": "batt_p", "name": "Battery Power", "val": batt_p, "unit": "W"},
                {"address": 191, "key": "batt_c", "name": "Battery Current", "val": batt_c, "unit": "A"},
                {"address": 194, "key": "grid_rly_raw", "name": "Grid Relay Raw", "val": 1, "unit": ""},
                {"address": 195, "key": "gen_rly_raw", "name": "Generator Relay Raw", "val": 0, "unit": ""},
            ],
        }


POLLER = SolArkDataPoller()


class SolArkRequestHandler(SimpleHTTPRequestHandler):
    """HTTP Request handler serving REST APIs and static dashboard UI."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(PUBLIC_DIR), **kwargs)

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/api/metrics":
            self.send_json_response(
                {
                    "connected": POLLER.is_connected,
                    "last_error": POLLER.last_error,
                    "config": POLLER.config,
                    "data": POLLER.last_data,
                }
            )
        elif parsed.path == "/api/config":
            self.send_json_response(POLLER.config)
        elif parsed.path == "/api/status":
            self.send_json_response(
                {
                    "connected": POLLER.is_connected,
                    "last_update": POLLER.last_update_timestamp,
                    "last_error": POLLER.last_error,
                }
            )
        else:
            super().do_GET()

    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path == "/api/config":
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length).decode("utf-8")
            try:
                new_cfg = json.loads(body)
                POLLER.update_config(new_cfg)
                self.send_json_response({"status": "success", "config": POLLER.config})
            except Exception as err:
                self.send_json_response({"status": "error", "message": str(err)}, status=400)
        else:
            self.send_error(440, "Method not allowed")

    def send_json_response(self, obj: dict, status: int = 200):
        body = json.dumps(obj).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)


def main():
    cfg = load_config()
    server_address = (cfg.get("server_host", "0.0.0.0"), cfg.get("server_port", 8080))
    httpd = HTTPServer(server_address, SolArkRequestHandler)
    _LOGGER.info("==========================================================")
    _LOGGER.info("Sol-Ark Modbus Web Monitor Server Running!")
    _LOGGER.info("Web Dashboard URL: http://localhost:%d", cfg.get("server_port", 8080))
    _LOGGER.info("Target Dongle Gateway: %s:%d (Slave ID: %d)", cfg["host"], cfg["port"], cfg["slave_id"])
    _LOGGER.info("==========================================================")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        _LOGGER.info("Shutting down server...")
        httpd.server_close()


if __name__ == "__main__":
    main()
