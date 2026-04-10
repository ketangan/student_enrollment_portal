"""
Generic export profile service.

Reads the `exports` section from a school's YAML config.
New export profile = add a YAML block. Zero code changes required.
"""
from __future__ import annotations

import json
import re
import logging
from typing import Any

logger = logging.getLogger(__name__)


def get_export_configs(config_raw: dict) -> dict[str, dict]:
    """Returns {profile_name: field_map} for all configured exports. Order preserved."""
    exports = config_raw.get("exports", {}) if isinstance(config_raw, dict) else {}
    if not isinstance(exports, dict):
        return {}
    return {
        name: block["field_map"]
        for name, block in exports.items()
        if isinstance(block, dict) and isinstance(block.get("field_map"), dict) and block["field_map"]
    }


def slugify_export_name(name: str) -> str:
    """Sanitizes an export profile name for safe use as an admin action __name__.

    Note: slugify alone does NOT guarantee uniqueness. Two different YAML profile
    names can produce the same slug. Collision disambiguation is the caller's
    responsibility (see used_names set with _2, _3 suffix in SubmissionAdmin).
    """
    slug = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
    return slug or "export"


def normalize_csv_value(val: Any) -> str:
    """Normalizes arbitrary submission field values for CSV output.

    Type mapping:
      None            → ""
      bool            → "Yes" / "No"
      list / tuple    → comma-joined string of each element
      dict            → JSON string (may be verbose in CSV cells — expected behavior)
      anything else   → str()
    """
    if val is None:
        return ""
    if isinstance(val, bool):
        return "Yes" if val else "No"
    if isinstance(val, (list, tuple)):
        return ", ".join(str(v) for v in val)
    if isinstance(val, dict):
        return json.dumps(val, ensure_ascii=False)
    return str(val)


def resolve_export_row(submission_data: dict, field_map: dict) -> tuple[dict, list[str]]:
    """
    Resolves one submission into export columns using the field_map.
    Returns (row_dict, warnings).

    Spec per column (all others are invalid → "" + warning):
      {"source": "key"}           — single field lookup; missing → "" + warning
      {"value": "literal"}        — hardcoded literal; never looked up
      {"source_any": ["k1","k2"]} — first key with a non-empty normalized value wins;
                                    key-present-but-empty is skipped (try next);
                                    none found / malformed → "" + warning
      "bare_string"               — treated as source: only; missing → "" + warning
                                    (NOT a literal fallback — use value: for literals)
    """
    row: dict[str, str] = {}
    warnings: list[str] = []

    for export_col, spec in field_map.items():
        if not isinstance(spec, dict):
            # Legacy bare string — source only, never literal fallback
            key = str(spec)
            if key in submission_data:
                row[export_col] = normalize_csv_value(submission_data[key])
            else:
                row[export_col] = ""
                warnings.append(
                    f"Column '{export_col}': field '{key}' not found in submission data"
                )
            continue

        if "value" in spec:
            row[export_col] = normalize_csv_value(spec["value"])

        elif "source" in spec:
            key = spec["source"]
            if key in submission_data:
                row[export_col] = normalize_csv_value(submission_data[key])
            else:
                row[export_col] = ""
                warnings.append(
                    f"Column '{export_col}': field '{key}' not found in submission data"
                )

        elif "source_any" in spec:
            sources = spec["source_any"]
            if not isinstance(sources, list) or not all(isinstance(s, str) for s in sources):
                row[export_col] = ""
                warnings.append(
                    f"Column '{export_col}': 'source_any' must be a list of strings, "
                    f"got {type(sources).__name__}"
                )
            else:
                found = False
                for key in sources:
                    if key in submission_data:
                        normalized = normalize_csv_value(submission_data[key])
                        if normalized:  # skip present-but-empty; try next key
                            row[export_col] = normalized
                            found = True
                            break
                if not found:
                    row[export_col] = ""
                    warnings.append(
                        f"Column '{export_col}': none of {sources} found with non-empty value "
                        "in submission data"
                    )

        else:
            row[export_col] = ""
            warnings.append(
                f"Column '{export_col}': invalid spec — must have 'source', 'value', or 'source_any'"
            )

    return row, warnings
