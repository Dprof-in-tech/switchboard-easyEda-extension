# SwitchBoard

An AI-powered extension for [EasyEDA Pro](https://pro.easyeda.com/) that lets you design circuits through natural language. It reads your live schematic, understands the circuit topology, and autonomously places and wires components — all running locally with [Ollama](https://ollama.ai/).

> _"Replace the TRIAC setup with 5 solid state relay channels"_
>
> SwitchBoard removes the old components, places G3MB-202P SSRs, wires every pin with net flags, validates the result, and auto-corrects mistakes — without leaving EasyEDA.

## How It Works

1. **Open any schematic** in EasyEDA Pro
2. **Click SwitchBoard** in the top menu → Open Chat
3. **Describe what you want** in plain English
4. **SwitchBoard does it** — places components, wires pins, validates results

The extension reads your schematic in real-time (components, nets, full netlist connectivity), sends it as context to a local LLM, and applies the AI's edits back into EasyEDA with pin-level precision.

## Key Features

### Pin-Aware Auto-Connections

When the AI adds a component, it declares **logical connections** — the extension reads actual pin positions from EasyEDA and places net flags at the exact coordinates:

```json
{
  "op": "add_component",
  "ref": "U11",
  "value": "G3MB-202P",
  "connections": {"3": "SSR1_CTRL", "4": "GND", "1": "LOAD1", "2": "AC-LIVE"}
}
```

No coordinate guessing. The extension calls `getAllPins()` after placement and wires each pin to its net automatically.

### Smart Placement

New components are placed intelligently — near related parts (shared nets), on a grid, with collision avoidance. No more piling everything in a corner.

### Validation & Auto-Correction

A 4-layer validation system catches AI mistakes before they reach your schematic:
- Blocks operations on non-existent components
- Detects when the AI describes changes without action blocks
- Catches wrong LCSC numbers (e.g., capacitor placed instead of relay)
- Feeds failures back to the AI for automatic retry

### DRC Auto-Fix

Click **Fix DRC** to automatically scan for floating pins and mark them No Connect — no manual pin-by-pin clicking.

### Subcircuit Templates

Pre-built circuit blocks with verified LCSC part numbers — RS485 transceiver, DS3231 RTC, MicroSD logger, SSR channels, TRIAC drivers, ESP32, current sensors. Ask the AI to add any of them.

### Works Offline

Everything runs on your machine. The backend talks to Ollama locally — your schematics never leave your computer.

## Architecture

```
EasyEDA Pro
  └── SwitchBoard Extension (TypeScript)
        ├── Reads schematic via EDA API (components, nets, netlist)
        ├── Chat panel (iframe)
        ├── Smart placement engine
        ├── Pin-aware connection system
        └── DRC auto-fix
              │  HTTP
              ▼
SwitchBoard Backend (Python/Flask, localhost:7777)
  ├── Ollama streaming client
  ├── Action validator (4-layer)
  ├── Schematic editor
  └── Knowledge base + subcircuit templates
              │
              ▼
Ollama (local LLM)
```

## Setup

### Prerequisites

- [EasyEDA Pro](https://pro.easyeda.com/) (desktop)
- [Ollama](https://ollama.ai/) with any model (`ollama pull llama3.1:8b`)
- Python 3.10+
- Node.js 20+

### Install

```bash
# Build the extension
cd extension
npm install
npm run build

# Start the backend
cd ../backend/pcb-ai-assistant
pip install -r requirements.txt
python server.py
```

Then in EasyEDA Pro:
1. Settings → Extensions → Extension Manager → Import the `.eext` file
2. Enable **"Allow external interaction"** in extension settings
3. Open a schematic → click **SwitchBoard** → **Open Chat**

## What the AI Can Do

| Say this | SwitchBoard does this |
|----------|----------------------|
| "Add a 10K pull-up on pin 5" | Places resistor, wires one end to the pin, other to VCC |
| "Replace U3 with an ESP32" | Removes old part, places ESP32 at same position |
| "Add decoupling caps on all ICs" | Adds 100nF caps with connections to VCC and GND |
| "Run a design review" | Analyzes the full schematic for issues |
| "What is R4 connected to?" | Reads the netlist and tells you exactly |
| "Add the RS485 subcircuit" | Places MAX485 + termination + bias resistors, all wired |

## Tech Stack

- **Extension**: TypeScript, EasyEDA Pro API SDK
- **Backend**: Python, Flask
- **AI**: Ollama (any compatible model)
- **UI**: Vanilla HTML/CSS/JS (no framework — runs in EasyEDA's iframe sandbox)

## Project Structure

```
switchboard-easyEda-extension/
├── extension/                 # EasyEDA Pro extension (TypeScript)
│   ├── src/index.ts           # Core logic: schematic reading, smart placement, IPC bridge
│   ├── iframe/index.html      # Chat UI (self-contained, no framework)
│   ├── extension.json         # Extension manifest (menus, UUID, entry point)
│   └── config/                # esbuild configuration
│
└── backend/pcb-ai-assistant/  # Flask server (Python)
    ├── server.py              # API routes, AI orchestration, session management
    ├── ollama_client.py       # HTTP wrapper for Ollama (streaming + non-streaming)
    ├── knowledge_base.py      # System prompts and subcircuit templates
    ├── schematic_editor.py    # In-memory schematic mutations
    ├── action_validator.py    # 4-layer validation for AI-generated actions
    ├── easyeda_parser.py      # EasyEDA JSON / text netlist parser
    └── schematic_exporter.py  # Export session back to EasyEDA format
```

See [CONTRIBUTING.md](./CONTRIBUTING.md) for a deeper walkthrough of the architecture, IPC bridge design, and how to add new subcircuit templates.

## Troubleshooting

| Problem | Fix |
|---------|-----|
| Chat panel is blank | Ensure backend is running (`python server.py`) and reachable at `http://localhost:7777` |
| "Ollama not reachable" error | Run `ollama serve` and confirm the model is pulled (`ollama list`) |
| Extension not visible in EasyEDA | Re-import the `.eext` file and enable "Allow external interaction" in Extension Manager |
| Component placed but not wired | Click **Sync Schematic** to refresh context, then ask again |
| Actions applied but schematic unchanged | The action may have been blocked by validation; check the chat response for details |
| Build fails (`Cannot find module`) | Run `npm install` inside the `extension/` directory first |

## Author

**Isaac Onyemaechi** — Software Engineer

## License

MIT
