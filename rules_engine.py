"""
rules_engine.py — Parses the verified Rules Engine Excel file and
builds a structured, token-efficient checklist string for the AI prompt.

Expected columns (case-insensitive):
  Rule_ID, Standard, Section_Reference, Applies_When, Element_Type,
  Parameter, Min_Value, Max_Value, Units, Direction,
  Exception_Notes, Field_Verification_Tips
"""
import logging
from typing import List, Dict, Tuple

logger = logging.getLogger("millwork_qc")

# ── Column name normalizer ──────────────────────────────────────────────────
_COL_ALIASES = {
    "rule_id":                  "Rule_ID",
    "standard":                 "Standard",
    "section_reference":        "Section_Reference",
    "applies_when":             "Applies_When",
    "element_type":             "Element_Type",
    "parameter":                "Parameter",
    "min_value":                "Min_Value",
    "max_value":                "Max_Value",
    "units":                    "Units",
    "direction":                "Direction",
    "exception_notes":          "Exception_Notes",
    "field_verification_tips":  "Field_Verification_Tips",
}


def _normalize_headers(raw_headers: list) -> Dict[str, int]:
    """Return {canonical_key: column_index} for known columns."""
    mapping = {}
    for i, h in enumerate(raw_headers):
        if h is None:
            continue
        key = str(h).strip().lower().replace(" ", "_")
        canonical = _COL_ALIASES.get(key)
        if canonical:
            mapping[canonical] = i
    return mapping


def _cell(row: tuple, mapping: Dict[str, int], col: str, default=None):
    """Safely get a cell value from a row tuple by canonical column name."""
    idx = mapping.get(col)
    if idx is None or idx >= len(row):
        return default
    val = row[idx]
    if val is None:
        return default
    return val


def _fmt_value(min_val, max_val, units, direction) -> str:
    """Build a human-readable requirement string like 'MIN 9 in' or 'RANGE 25–55 %'."""
    u = f" {units}" if units else ""
    if direction == "RANGE" and min_val is not None and max_val is not None:
        return f"RANGE {min_val} to {max_val}{u}"
    if direction == "MIN" and min_val is not None:
        return f"MIN {min_val}{u}"
    if direction == "MAX" and max_val is not None:
        return f"MAX {max_val}{u}"
    if direction == "REQUIREMENT":
        return "REQUIREMENT (see notes)"
    # Fallback
    parts = []
    if min_val is not None:
        parts.append(f"MIN {min_val}{u}")
    if max_val is not None:
        parts.append(f"MAX {max_val}{u}")
    return " / ".join(parts) if parts else direction or "N/A"


def parse_rules_excel(filepath: str) -> Tuple[List[Dict], str]:
    """
    Parse the Rules Engine Excel file.

    Returns:
        rules   — list of rule dicts (one per row)
        prompt  — formatted string ready to inject into the AI prompt
    """
    try:
        import openpyxl
    except ImportError:
        logger.error("openpyxl is not installed. Run: pip install openpyxl")
        return [], ""

    try:
        wb = openpyxl.load_workbook(filepath, read_only=True, data_only=True)
        ws = wb.active
    except Exception as e:
        logger.error(f"Failed to open rules Excel '{filepath}': {e}")
        return [], ""

    rows = list(ws.iter_rows(values_only=True))
    if not rows:
        logger.warning(f"Rules Excel '{filepath}' appears empty.")
        return [], ""

    # Detect header row — try first row
    mapping = _normalize_headers(list(rows[0]))
    if "Rule_ID" not in mapping:
        logger.warning(f"No 'Rule_ID' column found in '{filepath}'. Skipping.")
        return [], ""

    rules: List[Dict] = []
    prompt_lines: List[str] = []

    prompt_lines.append(
        "RULES CHECKLIST — Evaluate EVERY rule below against the drawing. "
        "For each rule, find the matching element on the drawing, read its shown value, "
        "and compare to the requirement. "
        "Use the 'WHERE TO LOOK' field as the spatial anchor for your bbox_pct.\n"
    )

    for row in rows[1:]:
        # Skip fully empty rows
        if all(v is None for v in row):
            continue

        rule_id   = _cell(row, mapping, "Rule_ID",   "?")
        standard  = _cell(row, mapping, "Standard",  "")
        section   = _cell(row, mapping, "Section_Reference", "")
        applies   = _cell(row, mapping, "Applies_When", "")
        elem_type = _cell(row, mapping, "Element_Type", "")
        param     = _cell(row, mapping, "Parameter",  "")
        min_val   = _cell(row, mapping, "Min_Value")
        max_val   = _cell(row, mapping, "Max_Value")
        units     = _cell(row, mapping, "Units",     "")
        direction = _cell(row, mapping, "Direction", "")
        exc_notes = _cell(row, mapping, "Exception_Notes", "")
        tip       = _cell(row, mapping, "Field_Verification_Tips", "")

        requirement = _fmt_value(min_val, max_val, units, direction)

        rule_dict = {
            "Rule_ID":               str(rule_id),
            "Standard":              str(standard),
            "Section_Reference":     str(section),
            "Applies_When":          str(applies),
            "Element_Type":          str(elem_type),
            "Parameter":             str(param),
            "Requirement":           requirement,
            "Exception_Notes":       str(exc_notes) if exc_notes else "",
            "Field_Verification_Tips": str(tip) if tip else "",
        }
        rules.append(rule_dict)

        # Build compact prompt line — token-efficient one-liner per rule
        line_parts = [
            f"[{rule_id}]",
            f"{standard} {section}".strip(" |"),
            f"Element: {elem_type}" if elem_type else "",
            f"Check: {param}" if param else "",
            f"Requirement: {requirement}",
        ]
        if applies:
            line_parts.append(f"Applies when: {applies}")
        if exc_notes:
            line_parts.append(f"Exception: {exc_notes}")
        if tip:
            line_parts.append(f"WHERE TO LOOK ON DRAWING (use as bbox anchor): {tip}")

        # Remove empty parts and join
        prompt_lines.append(" | ".join(p for p in line_parts if p))

    wb.close()

    prompt_str = "\n".join(prompt_lines)
    logger.info(f"Rules Engine: parsed {len(rules)} rules from '{filepath}'")
    return rules, prompt_str
