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
├── ollama_client.py       # Ollama wrapper with streaming
├── knowledge_base.py      # System prompt, subcircuit templates, netlist parser
├── schematic_editor.py    # Applies edit operations to schematic session
├── action_validator.py    # 4-layer validation for AI-generated actions
├── easyeda_parser.py      # EasyEDA Standard JSON parser
├── schematic_exporter.py  # Export to EasyEDA format
├── templates/index.html   # Standalone web UI
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
| `/api/health` | GET | Check Ollama connection |
