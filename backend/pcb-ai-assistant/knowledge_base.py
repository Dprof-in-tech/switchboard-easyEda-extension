"""PCB Design Knowledge Base — system prompts, subcircuit templates, review logic."""

import json

SUBCIRCUITS = [
    {
        "id": "rs485_transceiver",
        "name": "RS485 Modbus Transceiver",
        "desc": "MAX485-based RS485 for Modbus RTU (inverters, PLCs, sensors)",
        "keywords": ["rs485","modbus","max485","inverter","serial","communication"],
        "components": [
            {"ref":"U_RS","value":"MAX485ESA+","pkg":"SOP-8","lcsc":"C6855"},
            {"ref":"R_TERM","value":"120R","pkg":"0805","lcsc":"C17437"},
            {"ref":"R_PU","value":"10K","pkg":"0805","lcsc":"C17414"},
            {"ref":"R_PD","value":"10K","pkg":"0805","lcsc":"C17414"},
            {"ref":"C_DEC","value":"100nF","pkg":"0805","lcsc":"C49678"},
            {"ref":"J_RS","value":"RJ45-8P8C","pkg":"TH","lcsc":"C386756"},
        ],
        "connections": [
            "MCU SoftSerial RX (PB0/D8) → MAX485 RO (pin 1)",
            "MCU SoftSerial TX (PB1/D9) → MAX485 DI (pin 4)",
            "MCU GPIO (PD7/D7) → MAX485 DE+RE tied (pins 2,3)",
            "MAX485 A (pin 6) → RJ45 pin 1 + 10K pull-up to VCC",
            "MAX485 B (pin 7) → RJ45 pin 2 + 10K pull-down to GND",
            "120R termination across A-B (solder jumper to enable)",
            "100nF decoupling on VCC pin 8",
        ],
        "notes": "DE+RE tied: HIGH=TX, LOW=RX. 9600 baud for Modbus RTU. RJ45 pinout varies by brand.",
    },
    {
        "id": "rtc_ds3231",
        "name": "DS3231 Real-Time Clock",
        "desc": "Battery-backed RTC with TCXO for timestamped logging",
        "keywords": ["rtc","ds3231","clock","time","i2c","timestamp","logging"],
        "components": [
            {"ref":"U_RTC","value":"DS3231MZ+","pkg":"SO-8","lcsc":"C9865"},
            {"ref":"R_SDA","value":"4.7K","pkg":"0805","lcsc":"C17673"},
            {"ref":"R_SCL","value":"4.7K","pkg":"0805","lcsc":"C17673"},
            {"ref":"C_RTC","value":"100nF","pkg":"0805","lcsc":"C49678"},
            {"ref":"BT1","value":"CR2032 holder","pkg":"TH","lcsc":"C70377"},
        ],
        "connections": [
            "MCU PC4 (A4) → DS3231 SDA (pin 5) + 4.7K pull-up",
            "MCU PC5 (A5) → DS3231 SCL (pin 6) + 4.7K pull-up",
            "DS3231 VCC (pin 2) → 5V + 100nF decoupling",
            "DS3231 VBAT (pin 1) → CR2032 positive",
            "DS3231 GND (pin 4) → GND + CR2032 negative",
        ],
        "notes": "I2C address 0x68 (fixed). Only one set of pull-ups per bus. Arduino lib: RTClib.",
    },
    {
        "id": "sd_card_logger",
        "name": "MicroSD Card Logger",
        "desc": "SPI MicroSD with 3.3V regulator and level shifting for data logging",
        "keywords": ["sd","microsd","logger","spi","storage","csv","data"],
        "components": [
            {"ref":"U_REG","value":"AMS1117-3.3","pkg":"SOT-223","lcsc":"C6186"},
            {"ref":"C_IN","value":"10uF","pkg":"0805","lcsc":"C15850"},
            {"ref":"C_OUT","value":"22uF","pkg":"0805","lcsc":"C159842"},
            {"ref":"C_SD","value":"100nF","pkg":"0805","lcsc":"C49678"},
            {"ref":"R_MOSI_T","value":"10K","pkg":"0805","lcsc":"C17414"},
            {"ref":"R_MOSI_B","value":"20K","pkg":"0805","lcsc":"C17561"},
            {"ref":"R_SCK_T","value":"10K","pkg":"0805","lcsc":"C17414"},
            {"ref":"R_SCK_B","value":"20K","pkg":"0805","lcsc":"C17561"},
            {"ref":"R_CS_T","value":"10K","pkg":"0805","lcsc":"C17414"},
            {"ref":"R_CS_B","value":"20K","pkg":"0805","lcsc":"C17561"},
            {"ref":"J_SD","value":"MicroSD socket","pkg":"SMD","lcsc":"C585354"},
        ],
        "connections": [
            "MCU PB3 (D11/MOSI) → 10K/20K divider → SD MOSI",
            "MCU PB5 (D13/SCK) → 10K/20K divider → SD SCK",
            "MCU PC3 (A3/CS) → 10K/20K divider → SD CS",
            "SD MISO → MCU PB4 (D12) direct (3.3V OK for 5V logic)",
            "AMS1117: 5V in (+ 10uF) → 3.3V out (+ 22uF) → SD VCC + 100nF",
        ],
        "notes": "Level shift: Vout = 5V × 20K/(10K+20K) = 3.33V. SD lib uses ~512B RAM. Use F() macro.",
    },
    {
        "id": "triac_driver",
        "name": "Opto-Isolated TRIAC Driver",
        "desc": "MOC3021 + BT136-800E for AC load switching with zero-cross",
        "keywords": ["triac","ac","switch","load","moc3021","bt136","opto","isolation"],
        "components": [
            {"ref":"U_OPT","value":"MOC3021M","pkg":"DIP-6","lcsc":"C46681"},
            {"ref":"Q_TR","value":"BT136-800E","pkg":"TO-220-3","lcsc":"C2901"},
            {"ref":"R_GATE","value":"360R","pkg":"0805","lcsc":"C17521"},
            {"ref":"R_SNUB","value":"100R","pkg":"0805","lcsc":"C17408"},
            {"ref":"C_SNUB","value":"100nF/400V","pkg":"TH","lcsc":"C106247"},
            {"ref":"R_LED","value":"330R","pkg":"0805","lcsc":"C17519"},
        ],
        "connections": [
            "MCU GPIO → 330R → MOC3021 pin 1 (anode)",
            "MOC3021 pin 2 (cathode) → GND",
            "MOC3021 pin 4 → 360R → BT136 gate",
            "MOC3021 pin 6 → BT136 MT1",
            "BT136 MT2 → AC load → AC-Live",
            "BT136 MT1 → AC-Neutral",
            "100R + 100nF/400V snubber across MT1-MT2",
        ],
        "notes": "Snubber prevents false triggering from dV/dt. Heat sink BT136 for loads >2A.",
    },
    {
        "id": "esp32_wifi",
        "name": "ESP32 WiFi/BLE Module",
        "desc": "ESP32-WROOM-32 for WiFi/Bluetooth upgrade from HC-05",
        "keywords": ["esp32","wifi","bluetooth","ble","wireless","iot","wroom"],
        "components": [
            {"ref":"U_ESP","value":"ESP32-WROOM-32","pkg":"Module","lcsc":"C82899"},
            {"ref":"U_REG3","value":"AMS1117-3.3","pkg":"SOT-223","lcsc":"C6186"},
            {"ref":"C_E1","value":"10uF","pkg":"0805","lcsc":"C15850"},
            {"ref":"C_E2","value":"22uF","pkg":"0805","lcsc":"C159842"},
            {"ref":"C_E3","value":"100nF","pkg":"0805","lcsc":"C49678"},
            {"ref":"R_EN","value":"10K","pkg":"0805","lcsc":"C17414"},
            {"ref":"R_IO0","value":"10K","pkg":"0805","lcsc":"C17414"},
        ],
        "connections": [
            "ESP32 3V3 → AMS1117-3.3 output + 100nF decoupling",
            "ESP32 EN → 10K pull-up to 3V3 (enable)",
            "ESP32 IO0 → 10K pull-up to 3V3 (boot mode)",
            "ESP32 TX0 (GPIO1) → MCU RX (or direct USB-UART)",
            "ESP32 RX0 (GPIO3) → MCU TX (with level shift if 5V MCU)",
            "ESP32 GND → Ground plane",
        ],
        "notes": "If replacing HC-05: ESP32 gives WiFi+BLE. Needs 3.3V logic — add level shifters for 5V MCU.",
    },
    {
        "id": "ssr_channel",
        "name": "Solid State Relay Channel (G3MB-202P)",
        "desc": "Omron G3MB-202P SSR for AC load switching — built-in zero-cross, 2A/240VAC",
        "keywords": ["ssr","solid state relay","g3mb","ac","switch","load","omron","relay"],
        "components": [
            {"ref":"SSR?","value":"G3MB-202P","pkg":"SIP-4","lcsc":"C19494207"},
            {"ref":"R_SSR?","value":"330R","pkg":"0805","lcsc":"C17519"},
        ],
        "connections": [
            "MCU GPIO → 330R → SSR pin 3 (+ input)",
            "SSR pin 4 (- input) → GND",
            "SSR pin 1 (LOAD) → AC load terminal",
            "SSR pin 2 (LOAD) → AC-LIVE",
        ],
        "notes": "G3MB-202P: 5V control, 2A@240VAC, built-in zero-cross & snubber. For >2A use G3MB-202P-4. LCSC C19494207 is the 5V DC control version.",
    },
    {
        "id": "current_sensor",
        "name": "ACS712 Current Sensor",
        "desc": "Hall-effect AC/DC current sensor for load monitoring",
        "keywords": ["current","sensor","acs712","power","monitor","measurement"],
        "components": [
            {"ref":"U_CS","value":"ACS712-20A","pkg":"SOP-8","lcsc":"C10681"},
            {"ref":"C_CS1","value":"100nF","pkg":"0805","lcsc":"C49678"},
            {"ref":"C_CS2","value":"1nF","pkg":"0805","lcsc":"C46653"},
        ],
        "connections": [
            "ACS712 IP+ (pins 1,2) → AC load line in",
            "ACS712 IP- (pins 3,4) → AC load line out",
            "ACS712 VCC (pin 8) → 5V + 100nF decoupling",
            "ACS712 VIOUT (pin 7) → MCU ADC pin (PC0/A0)",
            "ACS712 FILTER (pin 6) → 1nF to GND (noise filter)",
            "ACS712 GND (pin 5) → Ground",
        ],
        "notes": "Output: 2.5V at 0A, 100mV/A sensitivity (20A version). Use averaging for AC RMS.",
    },
]

class PCBKnowledgeBase:

    def build_system_prompt(self, schematic_summary="", components=None, nets=None, raw_netlist=""):
        prompt = """You are SwitchBoard — a PCB design assistant specializing in embedded systems, power electronics, and home automation circuits. You run locally via Ollama.

## Your capabilities:
- Analyze uploaded EasyEDA schematics (component lists, net connections, design issues)
- Suggest circuit modifications and new subcircuits
- Provide component values, pin connections, and PCB layout advice
- Generate BOM with LCSC/JLCPCB part numbers
- Review designs for common mistakes (missing decoupling, wrong values, safety issues)
- Help with ATmega328P, ESP32, STM32, and common microcontroller circuits
- Advise on AC mains safety, TRIAC switching, isolation, creepage distances

## CRITICAL RULES:
- Be CONCISE. No essays, no ASCII art, no tables, no block diagrams, no repeating information.
- NEVER describe what you would do — DO IT with :::action{...}::: blocks.
- Every component change MUST be an :::action{...}::: block or it won't be applied.
- Keep responses SHORT: one line per action, 2-3 line summary at end. MAX 30 lines total.
- For questions (not edits): direct answers with exact values and pin numbers. MAX 20 lines.
- READ THE COMPONENT LIST AND NETLIST CAREFULLY. If a component appears in the list below, it EXISTS on the schematic. Do NOT claim components are missing or suggest adding components that already exist. Do NOT ask the user what changed — read the data you have.
- This design uses NET FLAGS for all connections — named nets (like GND, 5V-OUTPUT, SSR_DRIVE1, LOAD1) connect components without physical wires. This is the standard approach. Do NOT say "0 nets" or "nothing is connected" — the netlist shows all connectivity.
- Nigerian power context: 220V/50Hz mains.
- Reference LCSC part numbers when suggesting components.
- Be AUTONOMOUS. When the user asks you to do something, DO IT immediately with action blocks. Do NOT ask clarifying questions unless truly ambiguous. Do NOT say "I can't" — use the tools you have.
- The "value" field is the most important field in add_component — use the EXACT manufacturer part number (e.g., "G3MB-202P", "AMS1117-3.3", "ATmega328P"). The extension searches EasyEDA's component library by this name to find and place the correct part.
- Only include "lcsc" if you got the number from the subcircuit templates above. NEVER guess LCSC numbers — a wrong number places the WRONG component (e.g., a capacitor instead of a relay). If unsure, omit lcsc entirely — the extension will search by part name.
- For SSR (solid state relay) channels, use G3MB-202P with LCSC C19494207 (5V control, SIP-4 package).

## Available subcircuit templates you can reference:
"""
        for sc in SUBCIRCUITS:
            prompt += f"\n### {sc['name']}\n{sc['desc']}\nComponents: {', '.join(c['value'] for c in sc['components'])}\n"

        if schematic_summary:
            prompt += f"\n\n## CURRENT SCHEMATIC LOADED:\n{schematic_summary}\n"
            if components:
                prompt += "\n### Component list:\n"
                for c in (components or [])[:80]:
                    prompt += f"- {c['ref']}: {c['value']} ({c.get('package','')})\n"
            if nets:
                prompt += "\n### Net connections:\n"
                for n in (nets or [])[:40]:
                    if isinstance(n, dict):
                        name = n.get("name", "")
                        pins = n.get("pins", [])
                        if pins:
                            prompt += f"- **{name}**: {', '.join(pins[:20])}\n"
                        else:
                            prompt += f"- **{name}**\n"
                    else:
                        prompt += f"- {n}\n"
            if raw_netlist:
                # Parse EasyEDA Pro JSON netlist into readable net connections
                connectivity = self._parse_pro_netlist(raw_netlist)
                if connectivity:
                    prompt += "\n### Circuit connectivity (from netlist):\n"
                    prompt += connectivity
                else:
                    # Fallback: include truncated raw
                    nl = raw_netlist[:3000]
                    prompt += f"\n### Raw netlist:\n```\n{nl}\n```\n"

        prompt += """

## Editing the schematic
You can DIRECTLY EDIT the loaded schematic by including action blocks in your response.
When the user asks you to add, remove, change, or connect components, DO IT — don't just describe it.

Wrap each edit in :::action{...}::: blocks. You may include multiple actions in one response.
Always explain what you're doing alongside the actions.

### Available operations:

**Add a component WITH automatic pin connections (PREFERRED):**
:::action{"op":"add_component", "ref":"U11", "value":"G3MB-202P", "package":"SIP-4", "lcsc":"C19494207", "connections":{"3":"SSR1_CTRL", "4":"GND", "1":"LOAD1", "2":"AC-LIVE"}}:::
The "connections" field maps pin numbers to net names. The extension auto-places net flags at exact pin positions — no coordinates needed!

**Add a component without connections (when you'll connect later):**
:::action{"op":"add_component", "ref":"R10", "value":"4.7K", "package":"0805"}:::

**Connect a pin on an existing component to a net:**
:::action{"op":"connect_pin", "ref":"U11", "pin":"1", "net":"VCC"}:::
Use this to wire pins on components that are already placed. The extension finds the pin position and places the net flag there.

**Remove a component:**
:::action{"op":"remove_component", "ref":"R10"}:::

**Modify a component (change value, package, lcsc, or ref):**
:::action{"op":"modify_component", "ref":"R10", "value":"10K", "package":"0805"}:::

**Replace a component (remove old, place new at same position):**
:::action{"op":"replace_component", "ref":"Q2", "value":"BTA16-600B", "package":"TO-220"}:::

**Place a predefined subcircuit (rs485_transceiver, rtc_ds3231, sd_card_logger, triac_driver, ssr_channel, esp32_wifi, current_sensor):**
:::action{"op":"add_subcircuit", "subcircuit_id":"rs485_transceiver"}:::

**Mark floating pins as No Connect:**
:::action{"op":"set_no_connect", "ref":"BRAIN", "pins":["5","6","21","23"]}:::
Omit "pins" to mark ALL unconnected pins on a component as NC.

**Run DRC check:**
:::action{"op":"run_drc"}:::

### Rules:
- ALWAYS use :::action{...}::: blocks when the user asks to make changes — writing "I added X" without an action block means NOTHING happened
- ALWAYS include "connections" in add_component to wire pins automatically. This is the #1 most important feature — it places net flags at exact pin positions so the schematic is properly connected
- Use connect_pin for wiring pins on components that are already on the schematic
- Pin numbers in "connections" must match the component's actual datasheet pin numbers (e.g., MAX485: pin 1=RO, pin 4=DI, pin 8=VCC)
- Do NOT specify x,y coordinates — the extension auto-places everything
- Do NOT use add_wire — use connections in add_component or connect_pin instead
- If a component doesn't exist yet, use add_component (not modify_component)
- If you need to swap for a completely different part, use replace_component
- Keep responses SHORT: emit actions with a one-line comment each, then a 2-3 line summary. NO tables, NO ASCII art, NO block diagrams
- READ THE NETLIST to understand which pins connect where before assuming component purposes
- For questions (not edits), keep answers under 20 lines
"""
        return prompt

    def _parse_pro_netlist(self, raw_netlist):
        """Parse EasyEDA Pro JSON netlist into readable net-to-pin connections."""
        try:
            data = json.loads(raw_netlist)
        except (json.JSONDecodeError, TypeError):
            return ""

        if not isinstance(data, dict):
            return ""

        # Build net → [(designator, pin_number)] mapping
        net_pins = {}
        for comp_id, comp_data in data.items():
            if not isinstance(comp_data, dict):
                continue
            props = comp_data.get("props", {})
            designator = props.get("Designator", "")
            name = props.get("Name", "")
            pins = comp_data.get("pins", {})
            label = designator or name or comp_id[:8]

            for pin_num, net_name in pins.items():
                if not net_name:
                    continue  # unconnected pin
                if net_name not in net_pins:
                    net_pins[net_name] = []
                net_pins[net_name].append(f"{label}.{pin_num}")

        if not net_pins:
            return ""

        # Format: named nets first, then unnamed ($xxx) nets with 2+ connections
        lines = []

        # Named nets (human-readable names)
        named = {n: pins for n, pins in net_pins.items() if not n.startswith("$")}
        for net_name in sorted(named):
            pins = named[net_name]
            lines.append(f"- **{net_name}**: {', '.join(sorted(pins))}")

        # Unnamed nets with 2+ connections (these are actual circuit connections)
        unnamed = {n: pins for n, pins in net_pins.items()
                   if n.startswith("$") and len(pins) >= 2}
        if unnamed:
            lines.append("\nInternal connections (unnamed nets):")
            for net_name in sorted(unnamed):
                pins = unnamed[net_name]
                lines.append(f"- {' ↔ '.join(sorted(pins))}")

        return "\n".join(lines) + "\n"

    def detect_subcircuit(self, msg):
        msg_lower = msg.lower()
        for sc in SUBCIRCUITS:
            for kw in sc["keywords"]:
                if kw in msg_lower:
                    return sc["id"]
        return None

    def get_review_prompt(self, summary, components, nets):
        return f"""Perform a thorough design review of this schematic. Check for:

1. **Missing decoupling capacitors** — every IC should have 100nF close to VCC pin
2. **Pull-up/pull-down resistors** — I2C needs pull-ups, SPI CS needs pull-ups, reset needs pull-up
3. **Power supply issues** — voltage regulator stability caps, bulk capacitance
4. **AC safety** — isolation between high-voltage and low-voltage sections, snubber networks on TRIACs
5. **Signal integrity** — level shifting between 3.3V and 5V devices
6. **Missing connections** — unused MCU pins should be documented, AVCC should be connected
7. **Component ratings** — resistor power ratings, capacitor voltage ratings for AC circuits
8. **ESD protection** — on external connectors (RS485, USB, Bluetooth)
9. **Thermal concerns** — heat sinking on voltage regulators and TRIACs
10. **Nigerian mains context** — 220V/50Hz, voltage fluctuations, generator switching transients

## Schematic data:
{summary}

## Components:
{json.dumps(components[:40], indent=2) if components else 'None loaded'}

## Nets:
{', '.join(nets[:30]) if nets else 'None'}

Provide your findings as:
- CRITICAL (must fix before fabrication)
- WARNING (should fix, risk of intermittent issues)
- SUGGESTION (nice to have, improves robustness)

Be specific — reference exact component designators and pin numbers."""
