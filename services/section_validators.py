from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Sequence

ALLOWED_COMPETITOR_SIGNAL_TYPES = {"financial", "funding", "risk", "strategy", "leadership", "macro"}


@dataclass
class ValidationResult:
    is_valid: bool
    errors: List[str]
    metrics: Dict[str, Any]


def _to_str_list(value: Any) -> List[str]:
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def validate_industry_section(payload: Dict[str, Any]) -> ValidationResult:
    bullets = _to_str_list(payload.get("industry_summary", []))
    required_themes = {
        "rates_funding": ["rate", "funding", "yield", "liquidity", "cost of funds", "refinanc"],
        "demand_growth": ["demand", "growth", "disbursement", "originat", "volume", "momentum"],
        "risk_asset_quality": ["risk", "asset quality", "npa", "delinquen", "slippage", "collection"],
        "policy": ["policy", "regulator", "rbi", "nhb", "guideline", "compliance", "norm"],
    }

    combined_text = " ".join(bullets).lower()
    covered_themes = {
        theme: any(keyword in combined_text for keyword in keywords)
        for theme, keywords in required_themes.items()
    }

    missing = [theme for theme, covered in covered_themes.items() if not covered]
    errors: List[str] = []
    if not bullets:
        errors.append("industry_summary must contain at least one non-empty bullet.")
    if missing:
        errors.append(
            "industry_summary missing required themes: " + ", ".join(missing)
        )

    metrics = {
        "section": "industry",
        "bullet_count": len(bullets),
        "required_theme_count": len(required_themes),
        "covered_theme_count": sum(1 for covered in covered_themes.values() if covered),
        "missing_themes": missing,
    }
    return ValidationResult(is_valid=not errors, errors=errors, metrics=metrics)


def validate_regulatory_section(payload: Dict[str, Any]) -> ValidationResult:
    rows = payload.get("regulatory_updates", [])
    if not isinstance(rows, list):
        rows = []

    required_fields = ["line", "summary", "signal", "source", "date", "title", "url"]
    row_errors: List[str] = []
    complete_rows = 0

    for idx, row in enumerate(rows):
        if not isinstance(row, dict):
            row_errors.append(f"row[{idx}] must be an object.")
            continue
        missing = [field for field in required_fields if not str(row.get(field, "")).strip()]
        if missing:
            row_errors.append(f"row[{idx}] missing required fields: {', '.join(missing)}")
            continue
        complete_rows += 1

    errors = []
    if not rows:
        errors.append("regulatory_updates must contain at least one row.")
    errors.extend(row_errors)

    metrics = {
        "section": "regulatory",
        "row_count": len(rows),
        "complete_row_count": complete_rows,
        "incomplete_row_count": max(len(rows) - complete_rows, 0),
        "error_count": len(errors),
    }
    return ValidationResult(is_valid=not errors, errors=errors, metrics=metrics)


def validate_competitor_section(
    payload: Dict[str, Any],
    allowed_signal_types: Sequence[str] | None = None,
) -> ValidationResult:
    rows = payload.get("competitor_table", [])
    if not isinstance(rows, list):
        rows = []

    allowed = {s.lower() for s in (allowed_signal_types or ALLOWED_COMPETITOR_SIGNAL_TYPES)}
    row_errors: List[str] = []
    invalid_signal_count = 0
    empty_summary_count = 0

    for idx, row in enumerate(rows):
        if not isinstance(row, dict):
            row_errors.append(f"row[{idx}] must be an object.")
            continue

        summary = str(row.get("weekly_summary", "")).strip()
        if not summary or summary.lower() == "not found in source reviewed":
            empty_summary_count += 1
            row_errors.append(f"row[{idx}] has empty weekly_summary.")

        signal_types = row.get("signal_types", [])
        if isinstance(signal_types, str):
            signal_values = [signal_types.strip().lower()] if signal_types.strip() else []
        elif isinstance(signal_types, list):
            signal_values = [str(v).strip().lower() for v in signal_types if str(v).strip()]
        else:
            signal_values = []

        invalid_signals = [signal for signal in signal_values if signal not in allowed]
        if invalid_signals:
            invalid_signal_count += len(invalid_signals)
            row_errors.append(
                f"row[{idx}] has disallowed signal_types: {', '.join(invalid_signals)}"
            )

    errors = []
    errors.extend(row_errors)

    metrics = {
        "section": "competitor",
        "row_count": len(rows),
        "empty_summary_count": empty_summary_count,
        "invalid_signal_count": invalid_signal_count,
        "allowed_signal_types": sorted(allowed),
        "error_count": len(errors),
    }
    return ValidationResult(is_valid=not errors, errors=errors, metrics=metrics)
