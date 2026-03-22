#!/bin/bash
# SwitchBoard — Quick Start Script

echo ""
echo "═══════════════════════════════════════════════"
echo "  ⚡ SwitchBoard — PCB Design AI Assistant"
echo "═══════════════════════════════════════════════"
echo ""

# Check Python
if ! command -v python3 &> /dev/null; then
    echo "  ❌ Python 3 not found. Install it first."
    exit 1
fi

# Check Ollama
if curl -s http://localhost:11434/api/tags > /dev/null 2>&1; then
    echo "  ✅ Ollama is running"
    MODELS=$(curl -s http://localhost:11434/api/tags | python3 -c "import sys,json; [print('    •',m['name']) for m in json.load(sys.stdin).get('models',[])]" 2>/dev/null)
    if [ -n "$MODELS" ]; then
        echo "  Models available:"
        echo "$MODELS"
    else
        echo "  ⚠️  No models found. Run: ollama pull llama3.1:8b"
    fi
else
    echo "  ⚠️  Ollama not running. Start it with: ollama serve"
    echo "  (SwitchBoard will work but AI chat will be offline)"
fi

echo ""

# Install deps
pip install -r requirements.txt --quiet 2>/dev/null

# Run
echo "  Starting SwitchBoard on http://localhost:7777"
echo "═══════════════════════════════════════════════"
echo ""
python3 server.py
