# SwitchBoard Backend

The Flask backend that powers the SwitchBoard extension. Handles AI inference via Ollama, schematic editing, action validation, and subcircuit templates.

## Setup

```bash
# Install Ollama (https://ollama.ai)
ollama pull llama3.1:8b
ollama serve

# Start backend
pip install -r requirements.txt
python server.py
```

Backend runs at `http://localhost:7777`.

## Project Structure

```
├── server.py              # Flask API (chat, review, BOM, context sync)
├── ollama_client.py       # Ollama wrapper with streaming support
├── knowledge_base.py      # System prompt, subcircuit templates, netlist parser
├── schematic_editor.py    # Applies edit operations to schematic session
├── action_validator.py    # 4-layer validation for AI-generated actions
├── easyeda_parser.py      # EasyEDA Standard JSON parser
├── schematic_exporter.py  # Export to EasyEDA format
├── templates/index.html   # Standalone web UI (optional)
└── requirements.txt
```

## API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/context` | POST | Sync schematic data from extension |
| `/api/chat/stream` | POST | Streaming chat with AI (SSE) |
| `/api/chat` | POST | Non-streaming chat |
| `/api/review` | POST | AI design review |
| `/api/bom` | POST | Generate BOM |
| `/api/subcircuits` | GET | List subcircuit templates |
| `/api/session/history` | POST | Get chat history for session reload |
| `/api/models` | GET | List available Ollama models |
| `/api/health` | GET | Check Ollama connection |

## Request / Response Examples

### POST `/api/context`

Sent automatically by the extension when the chat panel opens or after **Sync Schematic**.

```json
{
  "session_id": "default",
  "components": [
    {"ref": "R1", "value": "10K", "package": "R0402", "x": 100, "y": 200}
  ],
  "nets": [
    {"name": "VCC", "pins": ["R1.1", "U1.2"]}
  ],
  "wires": [],
  "summary": "Schematic: 5 components, 3 nets",
  "rawNetlist": "..."
}
```

### POST `/api/chat/stream`

Uses Server-Sent Events (SSE). The AI response streams token-by-token; action blocks are parsed server-side before the response is forwarded.

```json
{
  "session_id": "default",
  "message": "Add a 10K pull-up resistor on pin 5 of U1",
  "model": "llama3.1:8b"
}
```

Response stream (SSE):

```
data: {"type": "token", "content": "I'll add a 10K pull-up..."}
data: {"type": "action", "op": "add_component", "ref": "R5", "value": "10K", ...}
data: {"type": "done", "edit_results": [...]}
```

## Session Management

Each session is stored as a JSON file in `sessions/{session_id}.json`. A session contains:

- `history` — chat messages
- `components` / `nets` / `wires` — current schematic snapshot
- `edit_log` — list of all applied actions
- `rawNetlist` — last known netlist text

Sessions are loaded on the first request for a given `session_id` and saved after every context update or chat turn.

## Validation System

Before any action reaches the schematic, `action_validator.py` runs four checks:

1. **Structural** — required fields present, referenced components exist.
2. **Orphan detection** — AI mentions a part in prose but produces no action block.
3. **Response quality** — detects verbose, off-format responses.
4. **Auto-correction** — if issues are found, a correction prompt is built and the AI retries once.

## Configuration

| Setting | Default | Where |
|---------|---------|-------|
| Ollama URL | `http://localhost:11434` | `ollama_client.py` line 5 |
| Server port | `7777` | `server.py` (last line) |
| Session directory | `sessions/` | `server.py` |
| CORS origin | `*` | `server.py` |
