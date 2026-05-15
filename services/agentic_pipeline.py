from __future__ import annotations

import difflib
import re
from typing import Any, Dict, List, Tuple


_REQUIRED_KEYS = {"company", "article_title", "weekly_summary", "signal_types", "confidence"}
_CANONICAL_SECTION_TAGS = {"industry", "regulatory", "competitor"}
_REGULATORY_HINTS = {
    "rbi",
    "nhb",
    "crisil",
    "icra",
    "regulatory",
    "policy",
    "rating",
    "guideline",
    "compliance",
}
_INDUSTRY_HINTS = {
    "industry",
    "sector",
    "housing finance",
    "home loan",
    "mortgage",
    "demand",
    "rate",
}


def _normalize_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _canonicalize_section_tag(raw_tag: str, company: str, title: str, summary: str) -> str:
    tag = _normalize_text(raw_tag).lower().replace("_", "-")
    tag_aliases = {
        "industry": "industry",
        "sector": "industry",
        "macro": "industry",
        "regulation": "regulatory",
        "regulatory": "regulatory",
        "policy": "regulatory",
        "rating": "regulatory",
        "competitor": "competitor",
        "company": "competitor",
        "peer": "competitor",
    }
    mapped = tag_aliases.get(tag)
    if mapped in _CANONICAL_SECTION_TAGS:
        return mapped

    text = f"{title} {summary}".lower()
    if any(h in text for h in _REGULATORY_HINTS):
        return "regulatory"
    if _normalize_text(company).lower() in {"", "industry"}:
        return "industry"
    if any(h in text for h in _INDUSTRY_HINTS):
        return "industry"
    return "competitor"


def _map_company_name(company_name: str, allowed_competitors: List[str]) -> str:
    normalized = _normalize_text(company_name).lower()
    if not normalized:
        return ""

    lookup = {c.lower(): c for c in allowed_competitors}
    if normalized in lookup:
        return lookup[normalized]

    # Lightweight canonical/fuzzy mapping for common legal suffix variants.
    cleaned = re.sub(r"\b(limited|ltd|inc|plc|co\.?|company)\b", "", normalized)
    cleaned = re.sub(r"[^a-z0-9 ]+", " ", cleaned)
    cleaned = _normalize_text(cleaned)
    cleaned_lookup = {
        _normalize_text(re.sub(r"\b(limited|ltd|inc|plc|co\.?|company)\b", "", c.lower())): c
        for c in allowed_competitors
    }
    if cleaned and cleaned in cleaned_lookup:
        return cleaned_lookup[cleaned]

    match = difflib.get_close_matches(normalized, list(lookup.keys()), n=1, cutoff=0.72)
    if match:
        return lookup[match[0]]
    return ""


def normalize_article_outputs(raw_items: List[Dict[str, Any]], allowed_competitors: List[str]) -> Dict[str, Any]:
    industry_items: List[Dict[str, Any]] = []
    regulatory_items: List[Dict[str, Any]] = []
    competitor_items: List[Dict[str, Any]] = []
    dropped_items: List[Dict[str, Any]] = []

    for idx, item in enumerate(raw_items):
        if not isinstance(item, dict):
            dropped_items.append({"index": idx, "reason": "not_a_dict"})
            continue

        missing = sorted(k for k in _REQUIRED_KEYS if k not in item)
        if missing:
            dropped_items.append({"index": idx, "reason": f"missing_keys:{','.join(missing)}", "item": item})
            continue

        company = _normalize_text(item.get("company"))
        title = _normalize_text(item.get("article_title"))
        summary = _normalize_text(item.get("weekly_summary"))
        confidence = _normalize_text(item.get("confidence")).lower() or "low"
        signal_types_raw = item.get("signal_types", [])
        if isinstance(signal_types_raw, list):
            signal_types = [_normalize_text(s).lower() for s in signal_types_raw if _normalize_text(s)]
        else:
            signal_types = [_normalize_text(signal_types_raw).lower()] if _normalize_text(signal_types_raw) else []

        if not title or not summary:
            dropped_items.append({"index": idx, "reason": "empty_title_or_summary", "item": item})
            continue

        section_tag = _canonicalize_section_tag(
            raw_tag=str(item.get("section_tag", "")),
            company=company,
            title=title,
            summary=summary,
        )

        mapped_company = _map_company_name(company, allowed_competitors)
        normalized = {
            "company": mapped_company or ("Industry" if section_tag != "competitor" else ""),
            "article_title": title,
            "weekly_summary": summary,
            "signal_types": signal_types,
            "confidence": confidence,
            "section_tag": section_tag,
        }

        if section_tag == "competitor":
            if not mapped_company:
                dropped_items.append({"index": idx, "reason": "unknown_competitor", "item": normalized})
                continue
            competitor_items.append(normalized)
        elif section_tag == "regulatory":
            regulatory_items.append(normalized)
        else:
            industry_items.append(normalized)

    return {
        "industry_items": industry_items,
        "regulatory_items": regulatory_items,
        "competitor_items": competitor_items,
        "dropped_items": dropped_items,
    }


def dedupe_by_title(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    seen: set[Tuple[str, str]] = set()
    output: List[Dict[str, Any]] = []
    for item in items:
        key = (
            _normalize_text(item.get("company", "")).lower(),
            _normalize_text(item.get("article_title", "")).lower(),
        )
        if key in seen:
            continue
        seen.add(key)
        output.append(item)
    return output
