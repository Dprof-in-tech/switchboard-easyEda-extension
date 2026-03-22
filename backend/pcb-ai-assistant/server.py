#!/usr/bin/env python3
"""
SwitchBoard — Local AI-Powered PCB Design Assistant
Powered by Ollama + Open-Source LLMs

Run: python server.py
Open: http://localhost:7777
"""

import os, json, time, re
from pathlib import Path
from flask import Flask, render_template, request, jsonify, send_from_directory, Response, make_response
from ollama_client import OllamaClient
from easyeda_parser import EasyEDAParser
from knowledge_base import PCBKnowledgeBase, SUBCIRCUITS
from schematic_editor import SchematicEditor, parse_actions
from schematic_exporter import SchematicExporter
from action_validator import validate_actions, detect_orphan_descriptions, check_response_quality, build_validation_summary

app = Flask(__name__)

# ── CORS: allow EasyEDA extension to reach the backend ──
@app.after_request
def add_cors_headers(response):
    response.headers['Access-Control-Allow-Origin'] = '*'
    response.headers['Access-Control-Allow-Headers'] = 'Content-Type'
    response.headers['Access-Control-Allow-Methods'] = 'GET, POST, OPTIONS'
    return response

@app.route('/<path:path>', methods=['OPTIONS'])
@app.route('/', methods=['OPTIONS'])
def handle_options(path=''):
    resp = make_response()
    resp.headers['Access-Control-Allow-Origin'] = '*'
    resp.headers['Access-Control-Allow-Headers'] = 'Content-Type'
    resp.headers['Access-Control-Allow-Methods'] = 'GET, POST, OPTIONS'
    return resp
UPLOAD_DIR = Path("uploads"); UPLOAD_DIR.mkdir(exist_ok=True)
OUTPUT_DIR = Path("outputs"); OUTPUT_DIR.mkdir(exist_ok=True)
SESSION_DIR = Path("sessions"); SESSION_DIR.mkdir(exist_ok=True)

ollama = OllamaClient()
parser = EasyEDAParser()
kb = PCBKnowledgeBase()
editor = SchematicEditor()
exporter = SchematicExporter()

# Session store — loads from disk, writes back on changes
sessions = {}

def _session_path(sid):
    # Sanitize sid to prevent path traversal
    safe = re.sub(r'[^a-zA-Z0-9_-]', '_', sid)
    return SESSION_DIR / f"{safe}.json"

def _empty_session():
    return {
        "history": [], "schematic": None, "components": [], "nets": [],
        "summary": "", "wires": [], "labels": [], "edit_log": [],
        "rawNetlist": "",
    }

def _load_session(sid):
    """Load a session from disk if it exists."""
    p = _session_path(sid)
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return _empty_session()

def save_session(sid):
    """Persist a session to disk."""
    if sid not in sessions:
        return
    try:
        _session_path(sid).write_text(
            json.dumps(sessions[sid], default=str, ensure_ascii=False),
            encoding="utf-8",
        )
    except OSError as e:
        print(f"Warning: could not save session {sid}: {e}")

def get_session(sid="default"):
    if sid not in sessions:
        sessions[sid] = _load_session(sid)
    return sessions[sid]

# ── API: Extension Context Push ──

@app.route("/api/context", methods=["POST"])
def api_context():
    """Receive structured schematic context from the EasyEDA extension."""
    data = request.json
    sid = data.get("session_id", "default")
    session = get_session(sid)

    components = data.get("components", [])
    nets = data.get("nets", [])
    summary = data.get("summary", "")

    session["components"] = components
    session["nets"] = nets
    session["summary"] = summary
    session["wires"] = data.get("wires", [])
    session["labels"] = data.get("labels", [])
    session["rawNetlist"] = data.get("rawNetlist", "")
    session["edit_log"] = []
    save_session(sid)

    return jsonify({
        "success": True,
        "components": len(components),
        "nets": len(nets),
        "hasNetlist": bool(session.get("rawNetlist")),
        "summary": summary,
    })

# ── Pages ──

@app.route("/")
def index():
    models = ollama.list_models()
    return render_template("index.html", models=models, subcircuits=SUBCIRCUITS)

# ── API: Ollama ──

@app.route("/api/models")
def api_models():
    return jsonify({"models": ollama.list_models(), "healthy": ollama.is_healthy()})

@app.route("/api/session/history", methods=["POST"])
def api_session_history():
    """Return chat history for a session so the iframe can reload previous conversation."""
    sid = request.json.get("session_id", "default") if request.json else "default"
    session = get_session(sid)
    return jsonify({"history": session.get("history", [])})

@app.route("/api/chat", methods=["POST"])
def api_chat():
    data = request.json
    msg = data.get("message", "").strip()
    model = data.get("model", "glm-5:cloud")
    sid = data.get("session_id", "default")
    if not msg:
        return jsonify({"error": "Empty message"}), 400

    session = get_session(sid)
    system = kb.build_system_prompt(session["summary"], session["components"], session["nets"], session.get("rawNetlist", ""))

    # Detect subcircuit requests
    sc_match = kb.detect_subcircuit(msg)

    messages = [{"role": "system", "content": system}]
    for h in session["history"][-12:]:
        messages.append(h)
    messages.append({"role": "user", "content": msg})

    try:
        resp = ollama.chat(model=model, messages=messages)
        reply = resp.get("message", {}).get("content", "No response.")

        # Parse and validate edit actions
        clean_text, actions = parse_actions(reply)
        valid_actions, action_warnings = validate_actions(actions, session)
        orphan_warnings = detect_orphan_descriptions(clean_text, actions)
        quality_warnings = check_response_quality(clean_text)
        validation = build_validation_summary(action_warnings, orphan_warnings, quality_warnings)

        # Apply only validated actions
        edit_results = []
        for action in valid_actions:
            result = editor.apply(action, session)
            result["action"] = action
            edit_results.append(result)
            session["edit_log"].append(result)

        session["history"].append({"role": "user", "content": msg})
        session["history"].append({"role": "assistant", "content": clean_text})

        # Auto-correction: if actions were blocked, retry once
        retry_text = ""
        needs_retry = validation["blocked_actions"] > 0 or validation["orphan_descriptions"]
        if needs_retry:
            correction = _build_correction_prompt(validation, session, edit_results)
            messages.append({"role": "assistant", "content": clean_text})
            messages.append({"role": "user", "content": correction})
            try:
                retry_resp = ollama.chat(model=model, messages=messages)
                retry_reply = retry_resp.get("message", {}).get("content", "")
                retry_clean, retry_actions = parse_actions(retry_reply)
                retry_valid, _ = validate_actions(retry_actions, session)
                for action in retry_valid:
                    result = editor.apply(action, session)
                    result["action"] = action
                    edit_results.append(result)
                    session["edit_log"].append(result)
                session["history"].append({"role": "assistant", "content": retry_clean})
                retry_text = retry_clean
            except Exception:
                pass  # correction failed, proceed with original results

        save_session(sid)
        return jsonify({
            "response": clean_text + ("\n\n---\n**Auto-correction:**\n" + retry_text if retry_text else ""),
            "model": model, "subcircuit_hint": sc_match,
            "edits": edit_results,
            "validation": validation,
            "schematic_updated": len(edit_results) > 0,
            "components": session["components"] if edit_results else None,
            "nets": session["nets"] if edit_results else None,
            "wires": session.get("wires") if edit_results else None,
            "labels": session.get("labels") if edit_results else None,
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

def _build_correction_prompt(validation, session, applied_actions=None):
    """Build a concise correction prompt from validation warnings.
    Only asks AI to fix blocked/missing actions — NOT re-emit successful ones."""
    known_refs = sorted(c["ref"] for c in session.get("components", []))
    parts = ["SOME ACTIONS FAILED. Only fix the issues below — do NOT re-emit actions that already succeeded.\n"]

    # Show what was blocked
    for w in validation["warnings"]:
        if w.startswith("BLOCKED") or "NOT applied" in w:
            parts.append(f"- {w}")

    # Show what already succeeded so the AI doesn't duplicate
    if applied_actions:
        succeeded = []
        for a in applied_actions:
            op = a.get("action", {}).get("op", "")
            ref = a.get("action", {}).get("ref", "") or a.get("action", {}).get("name", "")
            if a.get("ok") and op and ref:
                succeeded.append(f"{op}({ref})")
        if succeeded:
            parts.append(f"\nALREADY APPLIED (do NOT repeat): {', '.join(succeeded)}")

    parts.append(f"\nCurrent component refs: {', '.join(known_refs[:40])}")
    parts.append("\nRules: ONLY emit :::action{...}::: blocks for the FAILED items above. Do NOT re-add components or labels that already exist. Be brief.")
    return "\n".join(parts)

@app.route("/api/chat/stream", methods=["POST"])
def api_chat_stream():
    """Streaming chat endpoint."""
    data = request.json
    msg = data.get("message", "").strip()
    model = data.get("model", "glm-5:cloud")
    sid = data.get("session_id", "default")
    if not msg:
        return jsonify({"error": "Empty message"}), 400

    session = get_session(sid)
    system = kb.build_system_prompt(session["summary"], session["components"], session["nets"], session.get("rawNetlist", ""))

    messages = [{"role": "system", "content": system}]
    for h in session["history"][-12:]:
        messages.append(h)
    messages.append({"role": "user", "content": msg})

    def generate():
        full_response = ""
        try:
            for chunk in ollama.chat_stream(model=model, messages=messages):
                token = chunk.get("message", {}).get("content", "")
                if token:
                    full_response += token
                    yield f"data: {json.dumps({'token': token})}\n\n"

            # Parse, validate, and execute edit actions from the complete response
            clean_text, actions = parse_actions(full_response)
            valid_actions, action_warnings = validate_actions(actions, session)
            orphan_warnings = detect_orphan_descriptions(clean_text, actions)
            quality_warnings = check_response_quality(clean_text)
            validation = build_validation_summary(action_warnings, orphan_warnings, quality_warnings)

            # Apply only validated actions
            edit_results = []
            for action in valid_actions:
                result = editor.apply(action, session)
                result["action"] = action
                edit_results.append(result)
                session["edit_log"].append(result)

            session["history"].append({"role": "user", "content": msg})
            session["history"].append({"role": "assistant", "content": clean_text})

            if edit_results:
                edits_with_actions = [{"ok": r["ok"], "msg": r.get("msg", ""), "error": r.get("error", ""), "action": r.get("action", {})} for r in edit_results]
                yield f"data: {json.dumps({'edits': edits_with_actions, 'schematic_updated': True})}\n\n"

            # Emit validation warnings
            if validation["has_warnings"]:
                yield f"data: {json.dumps({'validation': validation})}\n\n"

            # ── Auto-correction: feed validation back to AI if actions were blocked ──
            needs_retry = validation["blocked_actions"] > 0 or validation["orphan_descriptions"]
            if needs_retry:
                correction = _build_correction_prompt(validation, session, edit_results)
                yield f"data: {json.dumps({'retry': True, 'reason': 'Auto-correcting...'})}\n\n"

                # Add context and ask AI to retry
                messages.append({"role": "assistant", "content": clean_text})
                messages.append({"role": "user", "content": correction})

                retry_response = ""
                for chunk in ollama.chat_stream(model=model, messages=messages):
                    token = chunk.get("message", {}).get("content", "")
                    if token:
                        retry_response += token
                        yield f"data: {json.dumps({'token': token})}\n\n"

                # Validate and apply retry actions (no further retries)
                retry_clean, retry_actions = parse_actions(retry_response)
                retry_valid, retry_warnings = validate_actions(retry_actions, session)

                retry_edits = []
                for action in retry_valid:
                    result = editor.apply(action, session)
                    result["action"] = action
                    retry_edits.append(result)
                    session["edit_log"].append(result)

                session["history"].append({"role": "assistant", "content": retry_clean})

                if retry_edits:
                    edits_with_actions = [{"ok": r["ok"], "msg": r.get("msg", ""), "error": r.get("error", ""), "action": r.get("action", {})} for r in retry_edits]
                    yield f"data: {json.dumps({'edits': edits_with_actions, 'schematic_updated': True})}\n\n"

                if retry_warnings:
                    yield f"data: {json.dumps({'validation': build_validation_summary(retry_warnings, [], [])})}\n\n"

            save_session(sid)
            yield f"data: {json.dumps({'done': True})}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'error': str(e)})}\n\n"

    return Response(generate(), mimetype="text/event-stream")

# ── API: Schematic Import ──

@app.route("/api/upload", methods=["POST"])
def api_upload():
    if "file" not in request.files:
        return jsonify({"error": "No file"}), 400
    f = request.files["file"]
    if not f.filename:
        return jsonify({"error": "No filename"}), 400

    filepath = UPLOAD_DIR / f.filename
    f.save(filepath)
    sid = request.form.get("session_id", "default")

    try:
        result = parser.parse(filepath)
        session = get_session(sid)
        session["schematic"] = str(filepath)
        session["components"] = result["components"]
        session["nets"] = result["nets"]
        # Tag original wires/labels so exporter doesn't duplicate them
        wires = result.get("wires", [])
        for w in wires:
            w["_original"] = True
        labels = result.get("labels", [])
        for l in labels:
            l["_original"] = True
        session["wires"] = wires
        session["labels"] = labels
        session["summary"] = result["summary"]
        session["edit_log"] = []
        save_session(sid)

        return jsonify({
            "success": True,
            "filename": f.filename,
            "summary": result["summary"],
            "components": result["components"],
            "nets": result["nets"],
            "shapes": result.get("shapes", []),
            "wires": result.get("wires", []),
            "labels": result.get("labels", []),
            "bounds": result.get("bounds", {}),
            "junctions": result.get("junctions", []),
        })
    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({"error": str(e)}), 500

# ── API: Design Review ──

@app.route("/api/review", methods=["POST"])
def api_review():
    data = request.json
    model = data.get("model", "glm-5:cloud")
    sid = data.get("session_id", "default")
    session = get_session(sid)

    if not session["summary"]:
        return jsonify({"error": "No schematic loaded"}), 400

    prompt = kb.get_review_prompt(session["summary"], session["components"], session["nets"])
    try:
        resp = ollama.chat(model=model, messages=[
            {"role": "system", "content": kb.build_system_prompt("", [], [])},
            {"role": "user", "content": prompt},
        ])
        return jsonify({"review": resp.get("message", {}).get("content", "")})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ── API: Subcircuits ──

@app.route("/api/subcircuits")
def api_subcircuits():
    return jsonify({"subcircuits": SUBCIRCUITS})

@app.route("/api/subcircuit/<name>")
def api_subcircuit_detail(name):
    for sc in SUBCIRCUITS:
        if sc["id"] == name:
            return jsonify(sc)
    return jsonify({"error": "Not found"}), 404

# ── API: Schematic Edit ──

@app.route("/api/edit", methods=["POST"])
def api_edit():
    """Direct edit endpoint — apply one or more actions to the schematic."""
    data = request.json
    sid = data.get("session_id", "default")
    session = get_session(sid)

    if not session["components"] and not session["summary"]:
        return jsonify({"error": "No schematic loaded"}), 400

    actions = data.get("actions", [])
    if not actions:
        # Single action in body
        if "op" in data:
            actions = [data]
        else:
            return jsonify({"error": "No actions provided"}), 400

    results = []
    for action in actions:
        result = editor.apply(action, session)
        result["action"] = action
        results.append(result)
        session["edit_log"].append(result)

    save_session(sid)
    return jsonify({
        "results": results,
        "components": session["components"],
        "nets": session["nets"],
        "wires": session.get("wires", []),
        "labels": session.get("labels", []),
    })

@app.route("/api/schematic", methods=["POST"])
def api_schematic_state():
    """Return the current schematic state for a session."""
    sid = request.json.get("session_id", "default")
    session = get_session(sid)
    return jsonify({
        "components": session["components"],
        "nets": session["nets"],
        "wires": session.get("wires", []),
        "labels": session.get("labels", []),
        "summary": session["summary"],
        "edit_log": session.get("edit_log", []),
    })

@app.route("/api/edit/undo", methods=["POST"])
def api_undo():
    """Undo is not yet supported — placeholder."""
    return jsonify({"error": "Undo not yet implemented"}), 501

# ── API: Export ──

@app.route("/api/export/easyeda", methods=["POST"])
def api_export_easyeda():
    """Export modified schematic as EasyEDA JSON."""
    sid = request.json.get("session_id", "default")
    session = get_session(sid)
    if not session["components"]:
        return jsonify({"error": "No schematic data to export"}), 400
    data = exporter.export_easyeda(session)
    return Response(
        json.dumps(data, indent=2, ensure_ascii=False),
        mimetype="application/json",
        headers={"Content-Disposition": "attachment; filename=switchboard_export.json"},
    )

@app.route("/api/export/project", methods=["POST"])
def api_export_project():
    """Export as SwitchBoard project JSON."""
    sid = request.json.get("session_id", "default")
    session = get_session(sid)
    if not session["components"]:
        return jsonify({"error": "No schematic data to export"}), 400
    data = exporter.export_project(session)
    return Response(
        json.dumps(data, indent=2),
        mimetype="application/json",
        headers={"Content-Disposition": "attachment; filename=switchboard_project.json"},
    )

@app.route("/api/export/bom", methods=["POST"])
def api_export_bom():
    """Export BOM as CSV."""
    sid = request.json.get("session_id", "default")
    session = get_session(sid)
    if not session["components"]:
        return jsonify({"error": "No schematic data to export"}), 400
    csv_data = exporter.export_bom_csv(session)
    return Response(
        csv_data,
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=switchboard_bom.csv"},
    )

# ── API: BOM ──

@app.route("/api/bom", methods=["POST"])
def api_bom():
    sid = request.json.get("session_id", "default")
    session = get_session(sid)
    if not session["components"]:
        return jsonify({"error": "No schematic"}), 400

    agg = {}
    for c in session["components"]:
        key = f"{c['value']}|{c.get('package','')}"
        if key not in agg:
            agg[key] = {"value": c["value"], "package": c.get("package", ""), "qty": 0, "refs": []}
        agg[key]["qty"] += 1
        agg[key]["refs"].append(c["ref"])
    return jsonify({"bom": list(agg.values())})

# ── API: Health ──

@app.route("/api/health")
def api_health():
    ok = ollama.is_healthy()
    return jsonify({"ollama": ok, "models": ollama.list_models() if ok else []})

# ── Static files ──

@app.route("/outputs/<path:fn>")
def download(fn):
    return send_from_directory(OUTPUT_DIR, fn, as_attachment=True)


if __name__ == "__main__":
    print("\n" + "═" * 52)
    print("  ⚡ SwitchBoard — PCB Design AI Assistant")
    print("  ⚡ Powered by Ollama + Open-Source LLMs")
    print("═" * 52)
    ok = ollama.is_healthy()
    if ok:
        ms = ollama.list_models()
        print(f"\n  Ollama: Connected")
        print(f"  Models: {', '.join(ms) if ms else 'None — run: ollama pull llama3.1:8b'}")
    else:
        print(f"\n  Ollama: NOT CONNECTED — start with: ollama serve")
    print(f"\n  Open: http://localhost:7777")
    print("═" * 52 + "\n")
    app.run(host="0.0.0.0", port=7777, debug=True)
