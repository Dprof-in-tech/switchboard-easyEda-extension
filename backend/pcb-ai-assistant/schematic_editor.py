"""
Schematic Editor — mutates the in-memory EasyEDA schematic.

All operations work on the session's component/wire/label lists and
optionally on the raw EasyEDA JSON for re-export.
"""

import copy, json, re
from pathlib import Path


class SchematicEditor:
    """Applies edit operations to a parsed schematic session."""

    def apply(self, action, session):
        """Dispatch an action dict to the right handler. Returns a result dict."""
        op = action.get("op")
        handler = {
            "add_component": self._add_component,
            "remove_component": self._remove_component,
            "modify_component": self._modify_component,
            "move_component": self._move_component,
            "add_wire": self._add_wire,
            "remove_wire": self._remove_wire,
            "add_net_label": self._add_net_label,
            "remove_net_label": self._remove_net_label,
            "add_subcircuit": self._add_subcircuit,
            "replace_component": self._replace_component,
            "connect_pin": self._connect_pin,
            "set_no_connect": self._set_no_connect,
            "run_drc": self._run_drc,
        }.get(op)

        if not handler:
            return {"ok": False, "error": f"Unknown operation: {op}"}

        try:
            return handler(action, session)
        except Exception as e:
            return {"ok": False, "error": str(e)}

    # ── Component ops ──

    def _add_component(self, action, session):
        ref = action.get("ref", "")
        value = action.get("value", "")
        if not ref or not value:
            return {"ok": False, "error": "ref and value are required"}

        # Check for duplicate ref
        for c in session["components"]:
            if c["ref"] == ref:
                return {"ok": False, "error": f"Component {ref} already exists"}

        # Guess pin count from package if not specified
        pins = action.get("pins", 0)
        if not pins:
            pkg = action.get("package", "").upper()
            pin_map = {"SOP-8":8,"SOIC-8":8,"DIP-8":8,"SOT-23":3,"SOT-223":4,
                        "TO-220":3,"TO-92":3,"DIP-6":6,"DIP-14":14,"DIP-16":16,
                        "DIP-28":28,"DIP-40":40,"TQFP-32":32,"QFP-44":44}
            for k,v in pin_map.items():
                if k in pkg:
                    pins = v
                    break

        comp = {
            "ref": ref,
            "value": value,
            "x": action.get("x", self._next_x(session)),
            "y": action.get("y", self._next_y(session)),
            "rotation": action.get("rotation", 0),
            "package": action.get("package", ""),
            "mfr_part": action.get("mfr_part", ""),
            "lcsc": action.get("lcsc", ""),
            "pins": pins,
            "pin_names": [],
            "id": f"added_{ref}",
        }
        session["components"].append(comp)
        self._rebuild_summary(session)
        return {"ok": True, "msg": f"Added {ref} ({value})", "component": comp}

    def _remove_component(self, action, session):
        ref = action.get("ref", "")
        before = len(session["components"])
        session["components"] = [c for c in session["components"] if c["ref"] != ref]
        if len(session["components"]) == before:
            return {"ok": False, "error": f"Component {ref} not found"}
        self._rebuild_summary(session)
        return {"ok": True, "msg": f"Removed {ref}"}

    def _modify_component(self, action, session):
        ref = action.get("ref", "")
        for c in session["components"]:
            if c["ref"] == ref:
                changed = []
                for field in ("value", "package", "mfr_part", "lcsc", "rotation"):
                    if field in action and action[field] is not None:
                        old = c.get(field, "")
                        c[field] = action[field]
                        changed.append(f"{field}: {old} -> {action[field]}")
                if "new_ref" in action:
                    c["ref"] = action["new_ref"]
                    changed.append(f"ref: {ref} -> {action['new_ref']}")
                if not changed:
                    return {"ok": False, "error": "No fields to change"}
                self._rebuild_summary(session)
                return {"ok": True, "msg": f"Modified {ref}: {'; '.join(changed)}"}
        return {"ok": False, "error": f"Component {ref} not found"}

    def _move_component(self, action, session):
        ref = action.get("ref", "")
        for c in session["components"]:
            if c["ref"] == ref:
                c["x"] = action.get("x", c["x"])
                c["y"] = action.get("y", c["y"])
                return {"ok": True, "msg": f"Moved {ref} to ({c['x']}, {c['y']})"}
        return {"ok": False, "error": f"Component {ref} not found"}

    def _replace_component(self, action, session):
        """Replace a component: remove old, add new at same position."""
        ref = action.get("ref", "")
        new_value = action.get("value", action.get("new_value", ""))
        if not ref:
            return {"ok": False, "error": "ref required"}
        if not new_value:
            return {"ok": False, "error": "replacement value required"}

        # Find and remove old component, preserving its position
        old = None
        for i, c in enumerate(session["components"]):
            if c["ref"] == ref:
                old = c
                session["components"].pop(i)
                break
        if not old:
            return {"ok": False, "error": f"Component {ref} not found"}

        # Add new component at the same position
        session["components"].append({
            "ref": action.get("new_ref", ref),
            "value": new_value,
            "package": action.get("package", action.get("new_package", old.get("package", ""))),
            "x": old.get("x", 0),
            "y": old.get("y", 0),
            "lcsc": action.get("lcsc", action.get("new_lcsc", "")),
        })
        return {"ok": True, "msg": f"Replaced {ref} ({old['value']}) → {new_value}"}

    # ── Wire ops ──

    def _add_wire(self, action, session):
        points = action.get("points", [])
        if len(points) < 2:
            return {"ok": False, "error": "Wire needs at least 2 points"}
        # Normalize points to dicts
        normalized = []
        for p in points:
            if isinstance(p, dict):
                normalized.append({"x": float(p.get("x", 0)), "y": float(p.get("y", 0))})
            elif isinstance(p, (list, tuple)) and len(p) >= 2:
                normalized.append({"x": float(p[0]), "y": float(p[1])})
        if len(normalized) < 2:
            return {"ok": False, "error": "Invalid point format"}

        wire = {"points": normalized, "color": action.get("color", "#3fb950")}
        if "wires" not in session:
            session["wires"] = []
        session["wires"].append(wire)
        return {"ok": True, "msg": f"Added wire with {len(normalized)} points"}

    def _remove_wire(self, action, session):
        idx = action.get("index")
        wires = session.get("wires", [])
        if idx is None or idx < 0 or idx >= len(wires):
            return {"ok": False, "error": f"Wire index {idx} out of range (0-{len(wires)-1})"}
        wires.pop(idx)
        return {"ok": True, "msg": f"Removed wire {idx}"}

    # ── Net label ops ──

    def _add_net_label(self, action, session):
        name = action.get("name", "")
        if not name:
            return {"ok": False, "error": "Net label name required"}
        label = {
            "x": action.get("x", 0),
            "y": action.get("y", 0),
            "text": name,
            "net": name,
            "type": "flag",
        }
        if "labels" not in session:
            session["labels"] = []
        session["labels"].append(label)
        # Update nets list
        if name not in session.get("nets", []):
            session.setdefault("nets", []).append(name)
            session["nets"].sort()
        self._rebuild_summary(session)
        return {"ok": True, "msg": f"Added net label '{name}'"}

    def _remove_net_label(self, action, session):
        name = action.get("name", "")
        labels = session.get("labels", [])
        before = len(labels)
        session["labels"] = [l for l in labels if l.get("net") != name]
        if len(session["labels"]) == before:
            return {"ok": False, "error": f"Net label '{name}' not found"}
        # Remove from nets if no more labels reference it
        remaining_nets = set(l["net"] for l in session["labels"] if l.get("net"))
        session["nets"] = sorted(remaining_nets)
        self._rebuild_summary(session)
        return {"ok": True, "msg": f"Removed net label '{name}'"}

    # ── Subcircuit placement ──

    def _add_subcircuit(self, action, session):
        from knowledge_base import SUBCIRCUITS

        sc_id = action.get("subcircuit_id", "")
        template = None
        for sc in SUBCIRCUITS:
            if sc["id"] == sc_id:
                template = sc
                break
        if not template:
            return {"ok": False, "error": f"Subcircuit '{sc_id}' not found"}

        base_x = action.get("x", self._next_x(session))
        base_y = action.get("y", self._next_y(session))

        added = []
        spacing = 120
        for i, comp_tmpl in enumerate(template["components"]):
            # Auto-number refs to avoid conflicts
            ref = self._unique_ref(comp_tmpl["ref"], session)
            comp = {
                "ref": ref,
                "value": comp_tmpl["value"],
                "x": base_x + (i % 4) * spacing,
                "y": base_y + (i // 4) * spacing,
                "rotation": 0,
                "package": comp_tmpl.get("pkg", ""),
                "mfr_part": comp_tmpl["value"],
                "lcsc": comp_tmpl.get("lcsc", ""),
                "id": f"sc_{sc_id}_{ref}",
            }
            session["components"].append(comp)
            added.append(ref)

        self._rebuild_summary(session)
        return {
            "ok": True,
            "msg": f"Added subcircuit '{template['name']}' ({len(added)} components: {', '.join(added)})",
            "components_added": added,
            "connections": template.get("connections", []),
            "notes": template.get("notes", ""),
        }

    # ── Pin connection (pass-through — actual work done by extension) ──

    def _connect_pin(self, action, session):
        """Pass-through for connect_pin — the EasyEDA extension handles the actual pin lookup and net flag placement."""
        ref = action.get("ref", "")
        pin = action.get("pin", "")
        net = action.get("net", action.get("name", ""))
        if not ref or not pin or not net:
            return {"ok": False, "error": "connect_pin requires ref, pin, and net"}
        return {"ok": True, "msg": f"connect_pin {ref}.{pin} → {net} (extension will place net flag at pin)"}

    # ── DRC operations (pass-through — extension handles actual work) ──

    def _set_no_connect(self, action, session):
        ref = action.get("ref", "")
        pins = action.get("pins", [])
        if not ref:
            return {"ok": False, "error": "set_no_connect requires ref"}
        pin_desc = f" pins {','.join(pins)}" if pins else " (all unconnected)"
        return {"ok": True, "msg": f"set_no_connect {ref}{pin_desc} (extension will mark NC flags)"}

    def _run_drc(self, action, session):
        return {"ok": True, "msg": "run_drc (extension will execute schematic DRC check)"}

    # ── Helpers ──

    def _unique_ref(self, base_ref, session):
        """Make a ref unique by appending a number if needed."""
        existing = {c["ref"] for c in session["components"]}
        if base_ref not in existing:
            return base_ref
        # Strip trailing digits/? and increment
        prefix = re.match(r"([A-Za-z_]+)", base_ref)
        prefix = prefix.group(1) if prefix else base_ref
        n = 1
        while f"{prefix}{n}" in existing:
            n += 1
        return f"{prefix}{n}"

    def _next_x(self, session):
        """Find a reasonable X for placing new components."""
        if not session["components"]:
            return 500
        max_x = max(c["x"] for c in session["components"])
        return max_x + 200

    def _next_y(self, session):
        """Find a reasonable Y for placing new components."""
        if not session["components"]:
            return 400
        ys = [c["y"] for c in session["components"]]
        return sum(ys) / len(ys)

    def _rebuild_summary(self, session):
        """Rebuild the text summary after edits."""
        comps = session["components"]
        nets = session.get("nets", [])
        lines = [f"Components: {len(comps)}", f"Nets: {len(nets)}"]

        type_names = {
            "R": "Resistors", "C": "Capacitors", "U": "ICs/Modules",
            "Q": "TRIACs/Transistors", "D": "Diodes", "J": "Connectors",
        }
        by_type = {}
        for c in comps:
            m = re.match(r"([A-Za-z]+)", c["ref"])
            t = m.group(1) if m else "?"
            by_type.setdefault(t, []).append(c)

        lines.append("\nComponent breakdown:")
        for t, items in sorted(by_type.items()):
            name = type_names.get(t, t)
            vals = ", ".join(f"{c['ref']}={c['value']}" for c in items[:10])
            lines.append(f"  {name} ({len(items)}): {vals}")

        if nets:
            lines.append(f"\nNet names: {', '.join(nets)}")
        session["summary"] = "\n".join(lines)


def parse_actions(text):
    """Extract :::action{...}::: blocks from AI response text.
    Returns (clean_text, list_of_action_dicts).
    """
    pattern = r":::action\s*(\{.*?\})\s*:::"
    actions = []
    for m in re.finditer(pattern, text, re.DOTALL):
        try:
            action = json.loads(m.group(1))
            actions.append(action)
        except json.JSONDecodeError:
            pass
    clean = re.sub(pattern, "", text, flags=re.DOTALL).strip()
    return clean, actions
