from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Tuple

from services.prompts import newsletter_composer_prompt


_COMPETITOR_CATEGORIES = [
    "Growth & Strategy",
    "Funding & Capital",
    "Risk & Governance",
    "Operational Signals",
]


def _as_non_empty_list(value: Any) -> List[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def _normalize_regulatory_item(item: Any) -> Dict[str, str]:
    if isinstance(item, str):
        text = item.strip() or "Not found in source reviewed"
        return {
            "title": text,
            "what_happened": text,
            "impact": "Not found in source reviewed",
            "why_it_matters": "Not found in source reviewed",
        }

    if not isinstance(item, dict):
        return {
            "title": "Not found in source reviewed",
            "what_happened": "Not found in source reviewed",
            "impact": "Not found in source reviewed",
            "why_it_matters": "Not found in source reviewed",
        }

    title = str(item.get("title") or item.get("line") or "Not found in source reviewed").strip()
    what_happened = str(item.get("what_happened") or item.get("summary") or item.get("line") or title).strip()
    impact = str(item.get("impact") or item.get("signal") or "Not found in source reviewed").strip()
    why_it_matters = str(item.get("why_it_matters") or item.get("signal") or "Not found in source reviewed").strip()
    return {
        "title": title or "Not found in source reviewed",
        "what_happened": what_happened or "Not found in source reviewed",
        "impact": impact or "Not found in source reviewed",
        "why_it_matters": why_it_matters or "Not found in source reviewed",
    }


def _group_competitor_rows(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    grouped = {name: [] for name in _COMPETITOR_CATEGORIES}
    for row in rows:
        if not isinstance(row, dict):
            continue
        company = str(row.get("company", "")).strip()
        summary_raw = str(row.get("weekly_summary", "")).strip()
        summary = _normalize_company_event(company, summary_raw)
        signal_types = row.get("signal_types", [])
        normalized_signals = []
        if isinstance(signal_types, list):
            normalized_signals = [str(s).strip().lower() for s in signal_types if str(s).strip()]
        elif str(signal_types).strip():
            normalized_signals = [str(signal_types).strip().lower()]

        if any(s in {"funding", "capital", "liquidity"} for s in normalized_signals):
            category = "Funding & Capital"
        elif any(s in {"risk", "governance", "compliance", "asset_quality"} for s in normalized_signals):
            category = "Risk & Governance"
        elif any(s in {"operations", "operational", "execution", "product"} for s in normalized_signals):
            category = "Operational Signals"
        else:
            category = "Growth & Strategy"

        grouped[category].append(
            {
                "company": company or "Not found in source reviewed",
                "event": summary,
                # Deterministic fallback has no elaboration; the renderer omits the
                # paragraph entirely when narrative is empty (no duplication).
                "narrative": "",
                "signal": ", ".join(normalized_signals[:3]) or "news",
                "severity": "Medium",
            }
        )

    return {
        "grouped_insights": [
            {"category": category, "items": items}
            for category, items in grouped.items()
            if items
        ]
    }


def _normalize_newsletter(raw: Dict[str, Any]) -> Dict[str, Any]:
    newsletter = raw if isinstance(raw, dict) else {}
    industry_pulse = newsletter.get("industry_pulse")
    regulatory_watch = newsletter.get("regulatory_watch")
    competitor_intelligence = newsletter.get("competitor_intelligence")
    patterns = newsletter.get("patterns")
    key_takeaways = newsletter.get("key_takeaways")

    if not isinstance(industry_pulse, dict):
        legacy_industry_summary = _as_non_empty_list(newsletter.get("industry_summary"))
        industry_pulse = {
            "summary_paragraph": " ".join(legacy_industry_summary[:3]).strip() or "Not found in source reviewed",
            "highlights": legacy_industry_summary[:5],
        }
    if not isinstance(regulatory_watch, list):
        regulatory_watch = newsletter.get("regulatory_updates", [])
    if not isinstance(regulatory_watch, list):
        regulatory_watch = []
    regulatory_watch = [_normalize_regulatory_item(item) for item in regulatory_watch]
    if not isinstance(competitor_intelligence, dict):
        competitor_intelligence = {}
    if not isinstance(patterns, list):
        patterns = []
    if not isinstance(key_takeaways, list):
        key_takeaways = []

    industry_pulse.setdefault("summary_paragraph", "Not found in source reviewed")
    industry_pulse.setdefault("highlights", [])
    competitor_intelligence.setdefault("grouped_insights", [])

    return {
        "industry_pulse": industry_pulse,
        "regulatory_watch": regulatory_watch,
        "competitor_intelligence": competitor_intelligence,
        "patterns": patterns,
        "key_takeaways": key_takeaways,
    }


def compose_newsletter(
    *,
    llm: Any,
    date_range: str,
    industry_summary: List[str],
    regulatory_updates: List[Dict[str, Any]],
    competitor_rows: List[Dict[str, Any]],
) -> Tuple[str, Dict[str, Any]]:
    # Deterministic grouping computed BEFORE the LLM call. We pass it as input AND
    # keep it as a fallback in case the LLM forgets to produce competitor_intelligence
    # in its output (which has been observed — the section then disappears from the PDF).
    grouped_competitor_intelligence = _group_competitor_rows(competitor_rows)
    prompt = newsletter_composer_prompt(
        date_range=date_range,
        industry_json=json.dumps({"industry_summary": industry_summary}, ensure_ascii=False),
        regulatory_json=json.dumps({"regulatory_updates": regulatory_updates}, ensure_ascii=False),
        competitor_json=json.dumps(
            {
                "competitor_table": competitor_rows,
                "grouped_competitor_intelligence": grouped_competitor_intelligence,
            },
            ensure_ascii=False,
        ),
    )
    raw_output = llm.run_json_prompt(prompt)
    newsletter = _normalize_newsletter(raw_output if isinstance(raw_output, dict) else {})

    # Fallback: if the LLM produced an empty or missing grouped_insights, use the
    # deterministic grouping. This guarantees the Competitor Intelligence section
    # is present in the PDF whenever competitor_rows had real data.
    existing_groups = newsletter.get("competitor_intelligence", {}).get("grouped_insights") or []
    has_real_groups = any(
        isinstance(g, dict) and g.get("items")
        for g in existing_groups
    )
    if not has_real_groups and grouped_competitor_intelligence.get("grouped_insights"):
        newsletter["competitor_intelligence"] = grouped_competitor_intelligence

    # Always inject cover info so the renderer's page-decor has the date range
    # regardless of whether the LLM echoed it back. Without this, the renderer
    # falls back to its default "Reporting period not specified" string, which then
    # appears twice in the page header (once on each side of " to ").
    cover = newsletter.get("cover")
    if not isinstance(cover, dict):
        cover = {}
    cover["date_range"] = date_range
    cover.setdefault("title", "Housing Finance Weekly Digest")
    newsletter["cover"] = cover
    return prompt, newsletter


def _clean_noise_lines(lines: List[str]) -> List[str]:
    cleaned: List[str] = []
    for raw in lines:
        line = str(raw).strip()
        if not line:
            continue
        if line in {"-", "—"}:
            continue
        if line.lower() == "ad":
            continue
        cleaned.append(line)
    return cleaned


def _normalize_company_event(company: str, summary: str) -> str:
    company_clean = (company or "").strip()
    event = (summary or "").strip()
    if not event:
        return ""

    if company_clean:
        patterns = [
            rf"^{re.escape(company_clean)}\s*[:,-]\s*",
            rf"^{re.escape(company_clean)}\s+",
        ]
        for pattern in patterns:
            event = re.sub(pattern, "", event, flags=re.IGNORECASE).strip()

    if not event:
        return ""

    # Tidy: kill trailing ellipses / multiple dots / dangling punctuation.
    event = re.sub(r"[\.…]{2,}\s*$", "", event)
    event = re.sub(r"[,;:\s]+$", "", event)

    # Capitalise first letter; the company name was just stripped from the front and
    # the residue often begins with a lowercase verb ("announced...", "approved...").
    if event and event[0].isalpha() and event[0] != event[0].upper():
        event = event[0].upper() + event[1:]

    # Ensure a single terminal period.
    if event and event[-1] not in ".!?":
        event = event + "."

    return event


def _split_markdown_sections(markdown_text: str) -> Dict[str, List[str]]:
    sections: Dict[str, List[str]] = {
        "industry": [],
        "regulatory": [],
        "competitor": [],
        "takeaways": [],
    }
    current = ""
    for raw_line in (markdown_text or "").splitlines():
        line = raw_line.strip()
        lower = line.lower()
        if "industry summary" in lower:
            current = "industry"
            continue
        if "regulatory update" in lower:
            current = "regulatory"
            continue
        if "competitor summary table" in lower or "competitor intelligence" in lower:
            current = "competitor"
            continue
        if "key takeaway" in lower:
            current = "takeaways"
            continue
        if current:
            sections[current].append(line)
    return {k: _clean_noise_lines(v) for k, v in sections.items()}


def _industry_paragraph(industry_lines: List[str]) -> tuple[str, List[str]]:
    bullets = [re.sub(r"^[-*]\s*", "", line).strip() for line in industry_lines if line.strip()]
    bullets = [b for b in bullets if b]
    if not bullets:
        return "", []

    ordered_points = bullets[:4]
    paragraph = " ".join(ordered_points).strip()

    label_map = [
        ("recruitment", "People & Distribution"),
        ("hiring", "People & Distribution"),
        ("risk", "Risk"),
        ("regulat", "Regulatory"),
        ("capital", "Capital"),
        ("profit", "Performance"),
        ("q4", "Quarterly Print"),
        ("stock", "Market Signal"),
    ]
    highlights: List[str] = []
    for bullet in bullets:
        lower = bullet.lower()
        label = "Sector Signal"
        for token, tag in label_map:
            if token in lower:
                label = tag
                break
        highlights.append(f"<b>{label}:</b> {bullet}")
        if len(highlights) == 3:
            break
    return paragraph, highlights


def _parse_regulatory_items(lines: List[str]) -> List[Dict[str, str]]:
    items: List[Dict[str, str]] = []
    for line in lines:
        text = re.sub(r"^[-*]\s*", "", line).strip()
        if not text:
            continue
        if ":" in text:
            title, detail = text.split(":", 1)
            summary = detail.strip()
        else:
            title, summary = text, text
        impact = "Strengthens compliance expectations and transparency standards across housing finance participants."
        items.append(
            {
                "title": title.strip(),
                "summary": summary,
                "impact": impact,
            }
        )
    return items[:8]


def _parse_competitor_rows(lines: List[str]) -> List[Dict[str, str]]:
    table_rows = [line for line in lines if "|" in line and "---" not in line]
    parsed: List[Dict[str, str]] = []
    for row in table_rows:
        cells = [cell.strip() for cell in row.strip("|").split("|")]
        if len(cells) < 2:
            continue
        company = cells[0]
        summary = _normalize_company_event(company, cells[1])
        if not company or company.lower() in {"company", "competitor name"}:
            continue
        narrative = (
            f"{company} {summary}. This suggests continued focus on execution quality and competitive positioning in a tightening operating environment."
        )
        parsed.append({"company": company, "paragraph": narrative})
    if parsed:
        return parsed[:12]

    # fallback for non-table lines
    for line in lines:
        text = re.sub(r"^[-*]\s*", "", line).strip()
        if not text:
            continue
        if ":" in text:
            company, detail = text.split(":", 1)
            detail_text = _normalize_company_event(company.strip(), detail.strip())
            parsed.append(
                {
                    "company": company.strip(),
                    "paragraph": f"{company.strip()} {detail_text}. This indicates a focused strategic response to current market dynamics.",
                }
            )
    return parsed[:12]


def _build_at_a_glance(
    industry_highlights: List[str],
    regulatory_items: List[Dict[str, str]],
    competitors: List[Dict[str, str]],
) -> Dict[str, List[str]]:
    return {
        "industry": industry_highlights[:2],
        "regulatory": [item["title"] for item in regulatory_items[:2] if item.get("title")],
        "competitor": [item["company"] for item in competitors[:2] if item.get("company")],
    }


def _rewrite_key_takeaways(
    raw_takeaways: List[str],
    industry_paragraph: str,
    regulatory_items: List[Dict[str, str]],
    competitors: List[Dict[str, str]],
) -> List[str]:
    cleaned = [re.sub(r"^[-*]\s*", "", t).strip() for t in raw_takeaways if str(t).strip()]
    if len(cleaned) >= 4:
        return cleaned[:5]
    return [
        "Regulatory tightening alongside steady demand signals suggests a disciplined growth cycle is underway for housing finance.",
        "Compliance-focused actions and competitive execution updates indicate governance quality is becoming a stronger differentiator.",
        "Company-level strategic moves appear increasingly aligned with sector-wide demand pockets and operating discipline requirements.",
        "Cross-sectional evidence points to greater advantage for lenders balancing growth initiatives with transparent disclosure practices.",
        "Near-term performance will likely depend on how effectively institutions coordinate risk, distribution, and capital strategy.",
    ][:5]


def transform_markdown_to_newsletter_json(markdown_text: str, date_range: str) -> Dict[str, Any]:
    sections = _split_markdown_sections(markdown_text)
    industry_paragraph, industry_highlights = _industry_paragraph(sections["industry"])
    regulatory_items = _parse_regulatory_items(sections["regulatory"])
    competitors = _parse_competitor_rows(sections["competitor"])
    at_a_glance = _build_at_a_glance(industry_highlights, regulatory_items, competitors)
    key_takeaways = _rewrite_key_takeaways(sections["takeaways"], industry_paragraph, regulatory_items, competitors)

    return {
        "header": {
            "title": "Housing Finance Weekly Digest",
            "date_range": date_range,
        },
        "at_a_glance": at_a_glance,
        "industry_paragraph": industry_paragraph,
        "industry_highlights": industry_highlights[:3],
        "regulatory": regulatory_items,
        "competitors": competitors,
        "key_takeaways": key_takeaways,
    }
