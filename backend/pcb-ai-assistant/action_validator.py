"""
Action Validator — validates AI-generated actions before they reach the schematic.

Four validation layers:
1. validate_actions     — checks refs exist, no duplicates, required fields
2. detect_orphan_descriptions — catches "I added X" without action blocks
3. check_response_quality     — detects verbose/noisy responses
4. build_validation_summary   — combines all warnings
"""

import re


def validate_actions(actions, session):
    """Validate actions against session state. Returns (valid_actions, warnings)."""
    known_refs = {c["ref"] for c in session.get("components", [])}
    known_nets = set()
    for label in session.get("labels", []):
        if label.get("net"):
            known_nets.add(label["net"])

    valid = []
    warnings = []

    for action in actions:
        op = action.get("op", "")
        ref = action.get("ref", "")

        # Ops that require existing ref
        if op in ("modify_component", "remove_component", "move_component", "replace_component", "connect_pin", "set_no_connect"):
            if ref and ref not in known_refs:
                hint = _fuzzy_hint(ref, known_refs)
                warnings.append(
                    f"BLOCKED {op} → '{ref}' does not exist in schematic{hint}. "
                    f"Use add_component to create it first."
                )
                continue

        # remove_net_label needs existing label
        if op == "remove_net_label":
            name = action.get("name", "")
            if name and name not in known_nets:
                warnings.append(f"BLOCKED remove_net_label → '{name}' not found.")
                continue

        # add_component should not duplicate existing ref
        if op == "add_component" and ref and ref in known_refs:
            warnings.append(
                f"BLOCKED add_component → '{ref}' already exists. "
                f"Use modify_component to change it."
            )
            continue

        # Required fields check
        problem = _check_required_fields(action)
        if problem:
            warnings.append(f"BLOCKED {op} → {problem}")
            continue

        valid.append(action)

        # Track intra-batch additions so later actions can reference them
        if op == "add_component" and ref:
            known_refs.add(ref)

    return valid, warnings


def detect_orphan_descriptions(clean_text, actions):
    """Detect when AI describes changes without emitting action blocks."""
    warnings = []

    # Find refs mentioned with action verbs in the text
    verb_pattern = (
        r"(?:added|adding|add|placed|placing|place|removed|removing|remove|"
        r"changed|changing|change|modified|modifying|modify|moved|moving|move)\s+"
    )
    ref_pattern = r"([A-Z][A-Z_]?\d+)"

    described_refs = set()
    for m in re.finditer(verb_pattern + ref_pattern, clean_text, re.IGNORECASE):
        described_refs.add(m.group(1))

    actioned_refs = {a.get("ref", "") for a in actions if a.get("ref")}

    orphans = described_refs - actioned_refs
    if orphans:
        warnings.append(
            f"AI described changes to {', '.join(sorted(orphans))} "
            f"but no :::action::: blocks were emitted. "
            f"These changes were NOT applied. Ask the AI to retry with proper action blocks."
        )

    # Count mismatch: "adding 5 components" but only 2 action blocks
    for m in re.finditer(r"(?:adding|add|placed|placing)\s+(\d+)\s+component", clean_text, re.IGNORECASE):
        claimed = int(m.group(1))
        actual = sum(1 for a in actions if a.get("op") == "add_component")
        if actual < claimed:
            warnings.append(
                f"AI claimed to add {claimed} components but only {actual} action blocks found. "
                f"{claimed - actual} components were NOT added."
            )

    return warnings


def check_response_quality(text):
    """Detect excessive verbosity, ASCII art, and tables."""
    warnings = []
    lines = text.split("\n")

    if len(lines) > 60:
        warnings.append(
            f"Response is {len(lines)} lines (target: <30). Consider asking for a shorter answer."
        )

    # ASCII art / box drawing
    art_lines = sum(
        1 for l in lines
        if re.search(r"[│├└┌┐┘─┬┴┼]{3,}", l) or re.search(r"[-=]{10,}", l)
    )
    if art_lines > 3:
        warnings.append(f"Response contains ASCII art/diagrams ({art_lines} lines).")

    # Tables
    table_lines = sum(
        1 for l in lines
        if l.strip().startswith("|") and l.strip().endswith("|")
    )
    if table_lines > 5:
        warnings.append(f"Response contains a {table_lines}-row table.")

    return warnings


def build_validation_summary(action_warnings, orphan_warnings, quality_warnings):
    """Combine all warnings into a structured dict."""
    all_warnings = action_warnings + orphan_warnings + quality_warnings
    return {
        "warnings": all_warnings,
        "has_warnings": len(all_warnings) > 0,
        "blocked_actions": len(action_warnings),
        "orphan_descriptions": len(orphan_warnings) > 0,
    }


# ── Helpers ──

def _fuzzy_hint(ref, known_refs):
    """Suggest similar refs if they exist."""
    if not known_refs:
        return ""
    # Strip trailing digits to get prefix (e.g., R23 → R)
    prefix = re.sub(r"\d+$", "", ref)
    matches = sorted(r for r in known_refs if r.startswith(prefix))
    if matches:
        shown = ", ".join(matches[:5])
        return f" (existing {prefix}* refs: {shown})"
    return ""


def _check_required_fields(action):
    """Check an action has the minimum required fields. Returns error string or None."""
    op = action.get("op", "")

    if op == "add_component":
        if not action.get("ref"):
            return "missing 'ref'"
        if not action.get("value"):
            return "missing 'value'"

    elif op == "add_wire":
        pts = action.get("points", [])
        if len(pts) < 2:
            return "wire needs at least 2 points"

    elif op == "add_net_label":
        if not action.get("name") and not action.get("net"):
            return "missing net name"

    elif op == "modify_component":
        # Must have at least one property to change besides op and ref
        props = {k for k in action if k not in ("op", "ref")}
        if not props:
            return "no properties to modify"

    elif op in ("remove_component", "delete_component", "move_component"):
        if not action.get("ref"):
            return "missing 'ref'"

    elif op == "replace_component":
        if not action.get("ref"):
            return "missing 'ref' (component to replace)"
        if not action.get("value") and not action.get("new_value"):
            return "missing 'value' (replacement component)"

    elif op == "connect_pin":
        if not action.get("ref"):
            return "missing 'ref'"
        if not action.get("pin"):
            return "missing 'pin' (pin number or name)"
        if not action.get("net") and not action.get("name"):
            return "missing 'net' (net name to connect to)"

    elif op == "set_no_connect":
        if not action.get("ref"):
            return "missing 'ref'"

    elif op == "run_drc":
        pass  # no fields required

    return None
