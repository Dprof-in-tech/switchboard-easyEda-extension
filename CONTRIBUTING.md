# Contributing to SwitchBoard

Thanks for your interest in contributing! This guide explains how the codebase is organised and how to get a development environment running.

---

## Project Structure

```
switchboard-easyEda-extension/
├── README.md
├── CONTRIBUTING.md
│
├── extension/                        # EasyEDA Pro extension (TypeScript)
│   ├── extension.json                # Extension manifest (UUID, menus, entry point)
│   ├── package.json
│   ├── tsconfig.json
│   ├── src/
│   │   └── index.ts                  # Main extension logic (~1 600 lines)
│   ├── iframe/
│   │   └── index.html                # Chat UI (vanilla HTML/CSS/JS, ~836 lines)
│   ├── config/
│   │   ├── esbuild.common.ts         # esbuild base config (IIFE, no minify)
│   │   └── esbuild.prod.ts           # Build orchestrator
│   └── build/
│       └── packaged.ts               # Zips dist/ → .eext file
│
└── backend/
    └── pcb-ai-assistant/             # Flask backend (Python)
        ├── server.py                 # Flask routes and AI orchestration
        ├── ollama_client.py          # Thin HTTP wrapper around Ollama
        ├── knowledge_base.py         # System prompts, subcircuit templates, netlist parser
        ├── schematic_editor.py       # In-memory schematic mutation (sessions)
        ├── action_validator.py       # 4-layer action validation
        ├── easyeda_parser.py         # EasyEDA Standard JSON / text netlist parser
        ├── schematic_exporter.py     # Export session back to EasyEDA JSON
        ├── requirements.txt
        └── run.sh                    # Quick-start helper
```

---

## How the Extension Works

### Schematic Context (extension → backend)

When the user opens the chat panel the extension:

1. Calls `storeSchematicGlobally()` which reads components, nets, and the raw netlist from the live EasyEDA schematic using the EDA Pro API.
2. Stores the result in `globalThis.__switchboard_context`.
3. Opens the chat iframe.

The iframe reads `window.parent.__switchboard_context` and POST-s it to the backend's `/api/context` endpoint, which stores it in the session.

### IPC Bridge (extension ↔ iframe)

Functions cannot cross iframe boundaries in EasyEDA's sandbox. SwitchBoard uses a polling approach:

```
iframe                                extension (index.ts)
 │                                         │
 │  writes __switchboard_edit_request      │
 │──────────────────────────────────────▶ │
 │                                         │  polls every 200 ms
 │                                         │  calls applyEdits(actions)
 │                                         │  writes __switchboard_edit_results
 │  reads __switchboard_edit_results       │
 │◀────────────────────────────────────── │
```

### AI Request Flow (iframe → backend → Ollama)

```
User types message in iframe
        │
        ▼
POST /api/chat/stream  (SSE)
        │
        ▼
backend builds system prompt:
  current components + nets + subcircuit list + action format
        │
        ▼
Ollama streams response
        │
        ▼
backend parses :::action{...}::: blocks from response
        │
        ▼
4-layer validation  (action_validator.py)
        │
        ├─ valid → apply to session (schematic_editor.py), stream response to iframe
        │
        └─ invalid → build correction prompt → retry once with Ollama
```

### Applying Edits (iframe → extension → EasyEDA)

Once the user clicks "Apply" in the chat panel:

1. The iframe writes `{ actions, id }` to `window.parent.__switchboard_edit_request`.
2. `startEditPoller()` (running in the extension) detects the new id, calls `applyEdits(actions)`.
3. `applyEdits` calls EDA Pro API functions to place components, add net flags, draw wires, etc.
4. Results are written to `window.parent.__switchboard_edit_results`.
5. The iframe reads the results and shows a success/error toast.

---

## Development Setup

### Extension (TypeScript)

```bash
cd extension
npm install
npm run build        # compile + package → build/dist/switchboard_v1.0.0.eext
```

> **Note:** `npm run build` runs two steps internally:
> 1. `npm run compile` — esbuild bundles `src/index.ts` into `dist/index` (IIFE format)
> 2. `ts-node build/packaged.ts` — zips `dist/`, `iframe/`, and `extension.json` into the `.eext` file

Load the extension in EasyEDA Pro:
- Settings → Extensions → Extension Manager → Import `.eext`
- Enable **"Allow external interaction"**

### Backend (Python)

```bash
cd backend/pcb-ai-assistant
pip install -r requirements.txt
python server.py          # starts on http://localhost:7777
```

Ollama must be running before starting the server:

```bash
ollama serve
ollama pull llama3.1:8b   # or any other compatible model
```

Alternatively use the convenience script:

```bash
cd backend/pcb-ai-assistant
./run.sh
```

---

## How to Add a Subcircuit Template

Subcircuit templates live in `backend/pcb-ai-assistant/knowledge_base.py` in the `SUBCIRCUITS` dict.

Each entry has:

```python
"short_name": {
    "name": "Human Readable Name",
    "description": "What this subcircuit does",
    "components": [
        {"ref": "U1", "value": "PART_VALUE", "package": "PACKAGE", "lcsc": "CXXXXXX",
         "connections": {"1": "NET_A", "2": "NET_B", ...}}
    ],
    "wires": [],          # optional explicit wires
    "net_labels": [],     # optional net flags
    "keywords": ["keyword1", "keyword2"]   # used by detect_subcircuit()
}
```

After adding the entry, update `build_system_prompt()` in the same file to include the new subcircuit in the AI's context.

---

## Action Format

The AI produces structured edits inside `:::action{...}:::` fences. The extension and backend both parse these. Supported operations:

| `op` | Required fields | Description |
|------|----------------|-------------|
| `add_component` | `ref`, `value` | Place a new component |
| `remove_component` | `ref` | Delete a component |
| `modify_component` | `ref` + at least one of `value`, `package`, `lcsc` | Change component properties |
| `move_component` | `ref`, `x`, `y` | Move a placed component |
| `replace_component` | `ref`, `new_value` | Swap component at same ref |
| `connect_pin` | `ref`, `pin`, `net` | Connect a pin to a named net |
| `add_wire` | `points` (array of `[x,y]`) | Draw a wire |
| `remove_wire` | `points` | Remove a wire |
| `add_net_label` | `net`, `x`, `y` | Place a net flag |
| `remove_net_label` | `net`, `x`, `y` | Remove a net flag |
| `set_no_connect` | `ref`, `pin` | Mark a pin as No Connect |
| `run_drc` | — | Trigger EDA Design Rule Check |

---

## Validation System

`action_validator.py` runs four checks before any action reaches the schematic:

1. **Structural validation** — required fields present, referenced components exist, no duplicate refs on `add_component`.
2. **Orphan detection** — AI mentions a component in prose ("I added R5") but produces no action block for it.
3. **Response quality** — detects verbose answers, ASCII art, or markdown tables that suggest the model went off-script.
4. **Summary** — combines all warnings into a correction prompt that is fed back to the AI for a single retry.

---

## Code Style

- **TypeScript** — strict mode is enabled (`noImplicitAny`, `strictNullChecks`, all strict flags). Keep all types explicit.
- **Python** — follow PEP 8. No external formatters are configured; keep style consistent with existing files.
- **HTML/JS** — vanilla only. EasyEDA's iframe sandbox blocks most module systems; keep the chat UI as a single self-contained HTML file.

---

## Reporting Issues

Please include:
- EasyEDA Pro version
- Ollama model name and version (`ollama --version`)
- Backend log output (`python server.py` console)
- The schematic operation you attempted
