"""Ollama API client with streaming support."""
import json, requests

class OllamaClient:
    def __init__(self, base_url="http://localhost:11434"):
        self.base_url = base_url

    def is_healthy(self):
        try:
            return requests.get(f"{self.base_url}/api/tags", timeout=3).status_code == 200
        except: return False

    def list_models(self):
        try:
            r = requests.get(f"{self.base_url}/api/tags", timeout=5)
            return [m["name"] for m in r.json().get("models", [])] if r.ok else []
        except: return []

    def chat(self, model, messages, temperature=0.7):
        r = requests.post(f"{self.base_url}/api/chat", json={
            "model": model, "messages": messages, "stream": False,
            "options": {"temperature": temperature, "num_ctx": 8192}
        }, timeout=180)
        r.raise_for_status()
        return r.json()

    def chat_stream(self, model, messages, temperature=0.7):
        r = requests.post(f"{self.base_url}/api/chat", json={
            "model": model, "messages": messages, "stream": True,
            "options": {"temperature": temperature, "num_ctx": 8192}
        }, timeout=180, stream=True)
        r.raise_for_status()
        for line in r.iter_lines():
            if line:
                yield json.loads(line)
