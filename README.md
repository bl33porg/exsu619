# EXSU 620 Digital Prototyping — Urine Output Monitor

**Not for clinical use**

## Overview

UOM-1 estimates urine output by measuring changes in the weight of a collection bag. A 1 kg load cell and HX711 amplifier send readings to a Raspberry Pi Pico 2 W, which calculates estimated volume and hourly output in mL/kg/h. The system includes artifact-detection logic, a web dashboard, a NeoPixel status ring, an audible alarm, and an acknowledgement button.

The Pico performs measurement processing and local alarm decisions. A separate Python relay running on a laptop retrieves alarm events from the Pico over Wi-Fi and handles Telegram messages. Local alarms can continue without the laptop or internet connection.

## Hardware

The HX711 connects to the Pico using **GP2 (DT)**, **GP6 (SCK)**, **3V3 (VCC)**, and **GND**. The load-cell wires connect to the HX711 as **red–E+**, **black–E−**, **green–A+**, and **white–A−**. The NeoPixel ring uses **GP18** for data, the PAM8302A audio amplifier uses **GP14** for audio input, and the acknowledgement button uses **GP17**. The NeoPixel ring and amplifier receive 5 V and ground through the breadboard. All modules require a common ground.

## Software

The Pico runs `code.py` as its main program. `uom.py` handles volume calculation, measurement disturbances, alarms and records; `uom_web.py` provides the dashboard API and CSV export. The browser interface uses `dashboard.html` and supporting assets. On the laptop, `telegram_relay.py` uses `uom_telegram.py` to send alerts and process Telegram commands. `uom_config.py` contains local configuration settings.

## Running the Prototype

1. Copy the Pico firmware, web files and required CircuitPython libraries to the **CIRCUITPY** drive.
2. For physical testing, set `SIMULATE = False` and `SPEED = 1` in `code.py`. Configure the Pico's Wi-Fi settings, then open the dashboard at the address shown in the serial console.
3. Connect the laptop to a network that can reach the Pico. In the laptop relay folder, configure `PICO_URL`, `TELEGRAM_TOKEN` and `RECIPIENTS`, then install `requests` and run `python3 telegram_relay.py`. Keep the Pico's `TELEGRAM_TOKEN` empty when using the laptop relay to avoid duplicate notifications.

## Alarms and Verification

The firmware raises a nurse advisory for output below **0.5 mL/kg/h over 3 hours** and a physician alert for output below **0.3 mL/kg/h over 6 hours**. Its two-hour no-output condition currently uses **less than 2 mL accepted**, rather than exactly zero. Technical alarms identify sensor and measurement faults. Events, acknowledgements and hourly records are available through the dashboard.

**Software testing:** 19 of 19 simulated checks passed. Original calibration across 0–188 g showed **R² ≈ 0.99999**. Final-mount calibration and physical artifact-rejection verification remain incomplete. A sustained external force may be incorrectly recorded as fluid accumulation.

**Safety and privacy:** This is a bench-only prototype. Do not use real patient information or commit Wi-Fi passwords or Telegram tokens to the repository.

