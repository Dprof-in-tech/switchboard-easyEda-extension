"""
Schematic Exporter — exports modified schematics back to EasyEDA JSON,
SwitchBoard project JSON, or BOM CSV.
"""

import json, csv, io, copy, re
from pathlib import Path


class SchematicExporter:

    def export_easyeda(self, session):
        """Export as EasyEDA-compatible JSON. Patches original if available, else generates new."""
        original_path = session.get("schematic")
        if original_path and Path(original_path).exists():
            return self._patch_original(original_path, session)
        return self._generate_fresh(session)

    def export_project(self, session):
        """Export as SwitchBoard project JSON (our own format, re-importable)."""
        return {
            "format": "switchboard_v1",
            "components": session.get("components", []),
            "nets": session.get("nets", []),
            "wires": session.get("wires", []),
            "labels": session.get("labels", []),
            "summary": session.get("summary", ""),
            "edit_log": [
                {"ok": e.get("ok"), "msg": e.get("msg", ""), "action": e.get("action", {})}
                for e in session.get("edit_log", [])
            ],
        }

    def export_bom_csv(self, session):
        """Export BOM as CSV string."""
        comps = session.get("components", [])
        agg = {}
        for c in comps:
            key = f"{c['value']}|{c.get('package', '')}"
            if key not in agg:
                agg[key] = {"value": c["value"], "package": c.get("package", ""),
                            "lcsc": c.get("lcsc", ""), "qty": 0, "refs": []}
            agg[key]["qty"] += 1
            agg[key]["refs"].append(c["ref"])

        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(["Value", "Package", "Qty", "References", "LCSC"])
        for item in sorted(agg.values(), key=lambda x: x["refs"][0]):
            writer.writerow([
                item["value"], item["package"], item["qty"],
                " ".join(item["refs"]), item["lcsc"],
            ])
        return buf.getvalue()

    # ── EasyEDA patching ──

    def _patch_original(self, original_path, session):
        """Patch the original EasyEDA JSON with all edits."""
        data = json.loads(Path(original_path).read_text(encoding="utf-8", errors="replace"))

        # Find the shape array
        shapes = self._get_shapes(data)
        if shapes is None:
            return self._generate_fresh(session)

        # Build ref → shape index map
        ref_idx = {}
        for i, s in enumerate(shapes):
            if not isinstance(s, str) or not s.startswith("LIB~"):
                continue
            ref = self._extract_ref_from_shape(s)
            if ref:
                ref_idx[ref] = i

        # Track which refs exist in current session vs original
        current_refs = {c["ref"] for c in session.get("components", [])}
        original_refs = set(ref_idx.keys())

        # Remove deleted components
        for ref in original_refs - current_refs:
            if ref in ref_idx:
                shapes[ref_idx[ref]] = None

        # Add new components (those not in original)
        for c in session.get("components", []):
            if c["ref"] not in original_refs:
                shapes.append(self._gen_lib_shape(c))

        # Modify existing components (check for value/package changes)
        for c in session.get("components", []):
            if c["ref"] in ref_idx and ref_idx[c["ref"]] is not None:
                idx = ref_idx[c["ref"]]
                if shapes[idx] is not None:
                    shapes[idx] = self._update_lib_shape(shapes[idx], c)

        # Add new wires
        for w in session.get("wires", []):
            if not w.get("_original", False):
                shapes.append(self._gen_wire_shape(w))

        # Add new net labels
        for label in session.get("labels", []):
            if not label.get("_original", False):
                shapes.append(self._gen_flag_shape(label))

        # Clean up None entries
        if isinstance(shapes, list):
            cleaned = [s for s in shapes if s is not None]
            self._set_shapes(data, cleaned)

        return data

    def _generate_fresh(self, session):
        """Generate a fresh EasyEDA JSON from session data."""
        shapes = []

        for c in session.get("components", []):
            shapes.append(self._gen_lib_shape(c))
        for w in session.get("wires", []):
            shapes.append(self._gen_wire_shape(w))
        for label in session.get("labels", []):
            shapes.append(self._gen_flag_shape(label))

        return {
            "editorVersion": "6.5.40",
            "docType": "1",
            "title": "SwitchBoard Export",
            "schematics": [{
                "dataStr": {
                    "shape": shapes,
                    "BBox": self._calc_bbox(session),
                }
            }],
        }

    # ── Shape generators ──

    def _gen_lib_shape(self, comp):
        """Generate a LIB shape string for a component."""
        x, y = comp.get("x", 0), comp.get("y", 0)
        ref = comp.get("ref", "")
        value = comp.get("value", "")
        pkg = comp.get("package", "")
        lcsc = comp.get("lcsc", "")
        mfr = comp.get("mfr_part", "")
        cid = comp.get("id", f"new_{ref}")

        # Build backtick attribute string
        attrs_parts = []
        if mfr:
            attrs_parts.extend(["Manufacturer Part", mfr])
        if lcsc:
            attrs_parts.extend(["Supplier Part", lcsc])
        if pkg:
            attrs_parts.extend(["package", pkg])
        attrs = "`".join(attrs_parts)

        main = f"LIB~{x}~{y}~{attrs}~0~0~{cid}~0~gge_sb"

        # Ref text sub-shape
        ref_sub = f"T~P~{x}~{y - 20}~0~#0000FF~0~L~0~comment~{ref}~1~start~{x}~{y - 20}"
        # Value text sub-shape
        val_sub = f"T~N~{x}~{y + 20}~0~#008800~0~L~0~comment~{value}~1~start~{x}~{y + 20}"

        return f"{main}#@${ref_sub}#@${val_sub}"

    def _gen_wire_shape(self, wire):
        """Generate a W shape string for a wire."""
        pts = wire.get("points", [])
        if len(pts) < 2:
            return ""
        coords = " ".join(f"{p['x']} {p['y']}" for p in pts)
        color = wire.get("color", "#008800")
        return f"W~{coords}~{color}~1~0~wire_new~0"

    def _gen_flag_shape(self, label):
        """Generate an F shape string for a net flag/label."""
        x, y = label.get("x", 0), label.get("y", 0)
        name = label.get("net", label.get("text", ""))
        return f"F~part_netLabel_gnD~{x}~{y}~0~flag_new~~0^^0~{x}~{y}~0~0~0~{x}~{y}~^^{name}~#000000~{x}~{y - 10}~0~start~0~"

    def _update_lib_shape(self, shape_str, comp):
        """Update ref/value in an existing LIB shape string."""
        subs = shape_str.split("#@$")
        new_subs = [subs[0]]
        for sub in subs[1:]:
            if sub.strip().startswith("T~P~") and "comment~" in sub:
                # Update ref
                sub = self._replace_comment_field(sub, comp["ref"])
            elif sub.strip().startswith("T~N~") and "comment~" in sub:
                # Update value
                sub = self._replace_comment_field(sub, comp.get("value", ""))
            new_subs.append(sub)
        return "#@$".join(new_subs)

    def _replace_comment_field(self, sub, new_value):
        """Replace the field after 'comment~' in a T~ sub-shape."""
        parts = sub.split("~")
        try:
            ci = parts.index("comment")
            if ci + 1 < len(parts):
                parts[ci + 1] = new_value
        except ValueError:
            pass
        return "~".join(parts)

    # ── Helpers ──

    def _get_shapes(self, data):
        if not isinstance(data, dict):
            return None
        schems = data.get("schematics", [])
        if isinstance(schems, list) and schems:
            return schems[0].get("dataStr", {}).get("shape")
        elif isinstance(schems, dict):
            for v in schems.values():
                if isinstance(v, dict):
                    return v.get("dataStr", {}).get("shape")
        return None

    def _set_shapes(self, data, shapes):
        schems = data.get("schematics", [])
        if isinstance(schems, list) and schems:
            schems[0]["dataStr"]["shape"] = shapes
        elif isinstance(schems, dict):
            for v in schems.values():
                if isinstance(v, dict):
                    v["dataStr"]["shape"] = shapes
                    return

    def _extract_ref_from_shape(self, shape_str):
        for sub in shape_str.split("#@$"):
            if sub.strip().startswith("T~P~") and "comment~" in sub:
                parts = sub.split("~")
                try:
                    ci = parts.index("comment")
                    if ci + 1 < len(parts):
                        return parts[ci + 1].strip()
                except ValueError:
                    pass
        return None

    def _calc_bbox(self, session):
        xs, ys = [], []
        for c in session.get("components", []):
            xs.append(c["x"]); ys.append(c["y"])
        for w in session.get("wires", []):
            for p in w.get("points", []):
                xs.append(p["x"]); ys.append(p["y"])
        if not xs:
            return {"x": 0, "y": 0, "width": 2000, "height": 1500}
        return {
            "x": min(xs) - 100, "y": min(ys) - 100,
            "width": max(xs) - min(xs) + 200,
            "height": max(ys) - min(ys) + 200,
        }
