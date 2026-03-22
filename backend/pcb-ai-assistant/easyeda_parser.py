"""
EasyEDA Schematic Parser — parses EasyEDA Standard JSON exports.
Tested against EasyEDA v6.5.x exports.

Shape format:
  - LIB~x~y~attrs...  (components, sub-shapes after #@$)
  - W~coords~color~width~layer~id~... (wires)
  - F~type~x~y~rot~id... (net flags like GND, VCC — uses ^^ separator)
  - T~type~x~y~rot~... (standalone text)
  - J~x~y~... (junctions)

Within LIB sub-shapes (separated by #@$):
  - T~P~...~comment~REF~...     -> reference designator
  - T~N~...~comment~VALUE~...   -> component value
  - T~PK~...~comment~PACKAGE~...-> package/footprint
"""

import json, re
from pathlib import Path


class EasyEDAParser:

    def parse(self, filepath):
        filepath = Path(filepath)
        content = filepath.read_text(encoding="utf-8", errors="replace")

        try:
            data = json.loads(content)
        except json.JSONDecodeError:
            return self._fallback_text(content, filepath.name)

        return self._parse_easyeda(data, filepath.name)

    def _parse_easyeda(self, data, filename):
        """Parse EasyEDA Standard JSON (v6.5+)."""
        components = []
        wires = []
        labels = []
        junctions = []

        # Collect shapes from schematic sheets (deduplicate by ref overlap)
        all_shapes = []
        bbox = None

        if isinstance(data, dict):
            schems = data.get("schematics", [])
            sheets = schems if isinstance(schems, list) else list(schems.values()) if isinstance(schems, dict) else []
            seen_refs = set()
            for sch in sheets:
                if not isinstance(sch, dict):
                    continue
                ds = sch.get("dataStr", {})
                shapes = ds.get("shape", [])
                # Extract refs from this sheet to check for duplication
                sheet_refs = set()
                for s in shapes:
                    if isinstance(s, str) and s.startswith("LIB~"):
                        for sub in s.split("#@$"):
                            if sub.strip().startswith("T~P~") and "comment~" in sub:
                                parts = sub.split("~")
                                try:
                                    ci = parts.index("comment")
                                    sheet_refs.add(parts[ci + 1].strip())
                                except (ValueError, IndexError):
                                    pass
                # Skip sheet if >50% of its refs already seen (duplicate sheet)
                if seen_refs and sheet_refs:
                    overlap = len(sheet_refs & seen_refs) / len(sheet_refs)
                    if overlap > 0.5:
                        continue
                seen_refs.update(sheet_refs)
                all_shapes.extend(shapes)
                if not bbox and "BBox" in ds:
                    bbox = ds["BBox"]

            # Also check top-level shape array (some exports)
            if not all_shapes:
                all_shapes = data.get("shape", [])
                if not all_shapes and "dataStr" in data:
                    all_shapes = data["dataStr"].get("shape", [])

        elif isinstance(data, list):
            all_shapes = data

        # Parse each shape
        for s in all_shapes:
            if not isinstance(s, str):
                continue
            tag = s.split("~")[0].strip()
            if tag == "LIB":
                self._parse_lib(s, components)
            elif tag == "W":
                self._parse_wire(s, wires)
            elif tag == "F":
                self._parse_flag(s, labels)
            elif tag == "J":
                self._parse_junction(s, junctions)

        # Calculate bounds
        if bbox and isinstance(bbox, dict):
            bounds = {
                "x": float(bbox.get("x", 0)),
                "y": float(bbox.get("y", 0)),
                "width": float(bbox.get("width", 2000)),
                "height": float(bbox.get("height", 1500)),
            }
        else:
            bounds = self._calc_bounds(components, wires, labels)

        net_names = sorted(set(l["net"] for l in labels if l.get("net")))
        summary = self._build_summary(filename, components, net_names)

        return {
            "components": components,
            "nets": net_names,
            "wires": wires,
            "labels": labels,
            "junctions": junctions,
            "shapes": [],
            "texts": [],
            "bounds": bounds,
            "summary": summary,
        }

    def _parse_lib(self, s, components):
        """Parse LIB shape: component instance with sub-shapes."""
        if "frame_lib" in s:
            return  # Skip title block frame

        # Split into sub-shapes by #@$
        subs = s.split("#@$")
        main_parts = subs[0].split("~")

        if len(main_parts) < 3:
            return

        x = _float(main_parts[1])
        y = _float(main_parts[2])

        # Extract backtick key-value attributes from main_parts[3]
        attrs = {}
        if len(main_parts) > 3:
            pairs = main_parts[3].split("`")
            for i in range(0, len(pairs) - 1, 2):
                attrs[pairs[i].strip()] = pairs[i + 1].strip()

        # Extract ref, value, package from T~ sub-shapes,
        # pins from P~ sub-shapes, and graphical primitives
        ref = ""
        value = ""
        package = ""
        pin_count = 0
        pin_names = []
        pin_positions = []  # Full pin data with paths and labels
        graphics = []  # Graphical primitives (R, PL, PG, PT, E, A)

        for sub in subs[1:]:
            sub = sub.strip()
            if not sub:
                continue
            tag = sub.split("~")[0]

            if tag == "P":
                # Pin: P~show~class~num~x~y~rot~id~0^^x~y^^path~color^^...
                pin_count += 1
                pin_data = self._parse_pin(sub)
                if pin_data:
                    pin_positions.append(pin_data)
                continue

            if tag == "T":
                tp = sub.split("~")
                if len(tp) < 2:
                    continue
                ttype = tp[1]
                text = ""
                try:
                    ci = tp.index("comment")
                    if ci + 1 < len(tp):
                        text = tp[ci + 1].strip()
                except ValueError:
                    continue
                if ttype == "P" and text:
                    ref = text
                elif ttype == "N" and text:
                    value = text
                elif ttype == "PK" and text:
                    package = text
                continue

            # Graphical primitives
            if tag == "R":
                g = self._parse_rect(sub)
                if g:
                    graphics.append(g)
            elif tag == "PL":
                g = self._parse_polyline(sub)
                if g:
                    graphics.append(g)
            elif tag == "PG":
                g = self._parse_polygon(sub)
                if g:
                    graphics.append(g)
            elif tag == "PT":
                g = self._parse_path(sub)
                if g:
                    graphics.append(g)
            elif tag == "E":
                g = self._parse_ellipse(sub)
                if g:
                    graphics.append(g)
            elif tag == "A":
                g = self._parse_arc(sub)
                if g:
                    graphics.append(g)

        # Fallbacks from attributes
        mfr_part = attrs.get("Manufacturer Part", "")
        spice_pre = attrs.get("spicePre", "")
        attr_pkg = attrs.get("package", "")

        if not ref and spice_pre:
            ref = spice_pre + "?"
        if not value and mfr_part:
            value = mfr_part
        if not package and attr_pkg:
            package = attr_pkg

        if ref or value:
            components.append({
                "ref": ref,
                "value": value,
                "x": x,
                "y": y,
                "rotation": 0,
                "package": package,
                "mfr_part": mfr_part,
                "lcsc": attrs.get("Supplier Part", ""),
                "id": main_parts[6] if len(main_parts) > 6 else "",
                "pins": pin_count,
                "pin_names": pin_names[:40],
                "pin_positions": pin_positions,
                "graphics": graphics,
            })

    def _parse_pin(self, sub):
        """Parse P~ pin shape with terminal path and labels."""
        # Split by ^^ to get sections
        sections = sub.split("^^")
        pp = sections[0].split("~")
        if len(pp) < 7:
            return None

        pin = {
            "x": _float(pp[4]),
            "y": _float(pp[5]),
            "num": pp[3],
            "rotation": _float(pp[6]),
        }

        # Section 3: terminal SVG path (e.g. "M 350 -640 h 20~#880000")
        if len(sections) >= 3:
            path_parts = sections[2].split("~")
            pin["path"] = path_parts[0].strip()

        # Section 4: pin name label (show~x~y~rot~name~anchor~~~color)
        if len(sections) >= 4:
            label_parts = sections[3].split("~")
            if len(label_parts) >= 5:
                show = label_parts[0].strip()
                name = label_parts[4].strip().rstrip("#")
                if show == "1" and name:
                    pin["name"] = name

        # Section 5: pin number label
        if len(sections) >= 5:
            num_parts = sections[4].split("~")
            if len(num_parts) >= 5:
                pin["numLabel"] = num_parts[4].strip()

        return pin

    def _parse_rect(self, sub):
        """Parse R~ rectangle: R~x~y~rx~ry~w~h~color~sw~?~fill~id~..."""
        p = sub.split("~")
        if len(p) < 7:
            return None
        return {
            "type": "R",
            "x": _float(p[1]), "y": _float(p[2]),
            "w": _float(p[5]), "h": _float(p[6]),
            "color": p[7] if len(p) > 7 else "#000000",
            "sw": _float(p[8]) if len(p) > 8 else 1,
            "fill": p[10] if len(p) > 10 else "none",
        }

    def _parse_polyline(self, sub):
        """Parse PL~ polyline: PL~'x1 y1 x2 y2...'~color~sw~?~fill~..."""
        p = sub.split("~")
        if len(p) < 3:
            return None
        tokens = p[1].strip().split()
        points = []
        for i in range(0, len(tokens) - 1, 2):
            points.append({"x": _float(tokens[i]), "y": _float(tokens[i + 1])})
        if len(points) < 2:
            return None
        return {
            "type": "PL",
            "points": points,
            "color": p[2] if len(p) > 2 else "#000000",
            "sw": _float(p[3]) if len(p) > 3 else 1,
        }

    def _parse_polygon(self, sub):
        """Parse PG~ polygon: PG~'x1 y1 x2 y2 x3 y3'~color~sw~?~fill~..."""
        p = sub.split("~")
        if len(p) < 3:
            return None
        tokens = p[1].strip().split()
        points = []
        for i in range(0, len(tokens) - 1, 2):
            points.append({"x": _float(tokens[i]), "y": _float(tokens[i + 1])})
        if len(points) < 3:
            return None
        return {
            "type": "PG",
            "points": points,
            "color": p[2] if len(p) > 2 else "#000000",
            "sw": _float(p[3]) if len(p) > 3 else 1,
            "fill": p[5] if len(p) > 5 else p[2],
        }

    def _parse_path(self, sub):
        """Parse PT~ path: PT~'M x y L x y Z'~color~sw~?~fill~..."""
        p = sub.split("~")
        if len(p) < 2:
            return None
        return {
            "type": "PT",
            "d": p[1].strip(),
            "color": p[2] if len(p) > 2 else "#000000",
            "sw": _float(p[3]) if len(p) > 3 else 1,
            "fill": p[5] if len(p) > 5 else "none",
        }

    def _parse_ellipse(self, sub):
        """Parse E~ ellipse: E~cx~cy~rx~ry~color~sw~?~fill~..."""
        p = sub.split("~")
        if len(p) < 5:
            return None
        return {
            "type": "E",
            "cx": _float(p[1]), "cy": _float(p[2]),
            "rx": _float(p[3]), "ry": _float(p[4]),
            "color": p[5] if len(p) > 5 else "#000000",
            "sw": _float(p[6]) if len(p) > 6 else 1,
            "fill": p[8] if len(p) > 8 else "none",
        }

    def _parse_arc(self, sub):
        """Parse A~ arc: A~'M x y A ...'~~color~sw~..."""
        p = sub.split("~")
        if len(p) < 2:
            return None
        # Arc path may span first two fields (separated by ~~)
        d = p[1].strip()
        return {
            "type": "A",
            "d": d,
            "color": p[3] if len(p) > 3 else "#000000",
            "sw": _float(p[4]) if len(p) > 4 else 1,
        }

    def _parse_wire(self, s, wires):
        """Parse W shape: wire segment with coordinates."""
        parts = s.split("~")
        if len(parts) < 2:
            return

        coords_str = parts[1].strip()
        tokens = coords_str.split(" ")
        points = []
        for i in range(0, len(tokens) - 1, 2):
            points.append({"x": _float(tokens[i]), "y": _float(tokens[i + 1])})

        if len(points) >= 2:
            color = parts[2] if len(parts) > 2 else "#008800"
            wires.append({"points": points, "color": color})

    def _parse_flag(self, s, labels):
        """Parse F shape: net flag (GND, VCC, net labels)."""
        # F shapes use ^^ as sub-separator
        # Part[0]: F~type~x~y~rot~id~~flag
        # Part[2]: NETNAME~color~x~y~...
        parts = s.split("^^")
        if len(parts) < 3:
            return

        main = parts[0].split("~")
        if len(main) < 4:
            return

        x = _float(main[2])
        y = _float(main[3])

        # Net name is first field of the third ^^ section
        net_section = parts[2].split("~")
        net_name = net_section[0].strip() if net_section else ""

        if net_name:
            labels.append({
                "x": x,
                "y": y,
                "text": net_name,
                "net": net_name,
                "type": "flag",
            })

    def _parse_junction(self, s, junctions):
        """Parse J shape: wire junction dot."""
        parts = s.split("~")
        if len(parts) >= 3:
            junctions.append({"x": _float(parts[1]), "y": _float(parts[2])})

    def _calc_bounds(self, components, wires, labels):
        xs, ys = [], []
        for c in components:
            xs.append(c["x"]); ys.append(c["y"])
        for w in wires:
            for p in w["points"]:
                xs.append(p["x"]); ys.append(p["y"])
        for l in labels:
            xs.append(l["x"]); ys.append(l["y"])
        if not xs:
            return {"x": 0, "y": 0, "width": 2000, "height": 1500}
        pad = 100
        return {
            "x": min(xs) - pad, "y": min(ys) - pad,
            "width": max(xs) - min(xs) + 2 * pad,
            "height": max(ys) - min(ys) + 2 * pad,
        }

    def _build_summary(self, filename, components, nets):
        lines = [f"Schematic: {filename}", f"Components: {len(components)}", f"Nets: {len(nets)}"]
        by_type = {}
        type_names = {
            "R": "Resistors", "C": "Capacitors", "U": "ICs/Modules",
            "Q": "TRIACs/Transistors", "D": "Diodes", "J": "Connectors",
            "P": "Headers", "X": "Crystals", "L": "Inductors",
            "CN": "Connectors", "TR": "Connectors", "BT": "Batteries",
            "BRAIN": "Microcontroller", "RST": "Switch", "PWR": "Power",
            "HC": "Bluetooth", "S": "Switch",
        }
        for c in components:
            prefix = re.match(r"([A-Za-z]+)", c["ref"])
            t = prefix.group(1) if prefix else "?"
            by_type.setdefault(t, []).append(c)

        lines.append("\nComponent breakdown:")
        for t, items in sorted(by_type.items()):
            name = type_names.get(t, t)
            vals = ", ".join(f"{c['ref']}={c['value']}" for c in items[:10])
            if len(items) > 10:
                vals += f" (+{len(items) - 10} more)"
            lines.append(f"  {name} ({len(items)}): {vals}")

        if nets:
            lines.append(f"\nNet names: {', '.join(nets)}")
        return "\n".join(lines)

    def _fallback_text(self, content, filename):
        """Last resort for non-JSON files."""
        components = []
        nets = set()
        for m in re.finditer(r"\b([RCULQDJPX]\d{1,4})\b", content):
            ref = m.group(1)
            if ref not in [c["ref"] for c in components]:
                components.append({"ref": ref, "value": "?", "x": 0, "y": 0,
                                   "rotation": 0, "package": "", "mfr_part": "", "lcsc": "", "id": ""})
        for m in re.finditer(r"\b(VCC|GND|5V[_-]?OUTPUT|3V3|SDA|SCL|TX[D]?|RX[D]?)\b", content):
            nets.add(m.group(1))
        return {
            "components": components, "nets": sorted(nets),
            "wires": [], "labels": [], "junctions": [], "shapes": [], "texts": [],
            "bounds": {"x": 0, "y": 0, "width": 2000, "height": 1500},
            "summary": self._build_summary(filename, components, sorted(nets)),
        }


def _float(v):
    try:
        return float(v)
    except (ValueError, TypeError):
        return 0.0
