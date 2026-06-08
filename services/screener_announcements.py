"""Screener.in announcements -> Operational Signals (Competitor Intelligence).

Pipeline:
  1. fetch_announcements_in_range(name, ticker, start, end)
       Scrape the Announcements panel on https://www.screener.in/company/<ticker>/
       and filter STRICTLY to dates inside [start, end].
  2. classify_announcements(llm, name, announcements, start, end)
       Single batched Gemini call. Keeps only items classified as
       "Hiring" | "Management Change" | "Rumour".
  3. collect_operational_signals(...)
       Orchestrates 1+2 across the competitor list and returns rows shaped for the
       existing `competitor_rows` argument of `compose_newsletter`. Each row carries
       `signal_types=["operational"]` so the existing `_group_competitor_rows`
       routes it into the **Operational Signals** sub-section of Competitor
       Intelligence with no template changes required.

Rumours are visibly tagged: their `weekly_summary` is prefixed with "Rumour: ".
"""
from __future__ import annotations

import logging
import re
import time
from datetime import date, datetime
from typing import Any, Dict, List, Optional
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

SCREENER_BASE = "https://www.screener.in"
USER_AGENT = "HousingFinanceDigestBot/1.0 (research; contact: pnb-hfc@example.com)"
REQUEST_TIMEOUT = 30

# === Screener selectors — patch here if the site changes its HTML =================
# Screener restructured the announcements panel in 2025; the old li.document selectors
# no longer match. Items now sit inside <section id="documents"> -> <div class="show-more-box">
# -> <a href="https://www.bseindia.com/stockinfo/AnnPdfOpen.aspx?Pname=...">. The link text
# bundles "<BSE-category-prefix><DD MMM> - <summary excerpt>" — date is parsed from this
# concatenation; year is inferred from the [start, end] window passed by the caller.
ANNOUNCEMENTS_SECTION_ID = "documents"
ANNOUNCEMENT_ROW_SELECTOR = "section#documents .show-more-box a, section#documents .documents a"
# ==================================================================================

_RELEVANT_CATEGORIES = {"Hiring", "Management Change", "Rumour"}
_DATE_PATTERNS = ("%d %b %Y", "%d %B %Y", "%Y-%m-%d", "%d-%m-%Y", "%d %b, %Y")


# ----- date helpers ---------------------------------------------------------------


def _coerce_date(v: Any) -> date:
    if isinstance(v, date) and not isinstance(v, datetime):
        return v
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, str):
        return date.fromisoformat(v.strip())
    raise ValueError(f"Cannot coerce to date: {v!r}")


def _parse_screener_date(text: str) -> Optional[date]:
    text = (text or "").strip()
    m = re.search(r"\d{1,2}\s+\w+\s+\d{4}", text) or re.search(r"\d{4}-\d{2}-\d{2}", text)
    candidate = m.group(0) if m else text
    for fmt in _DATE_PATTERNS:
        try:
            return datetime.strptime(candidate, fmt).date()
        except ValueError:
            continue
    return None


# ----- fetch + parse --------------------------------------------------------------


def _fetch_company_page(ticker: str) -> Optional[str]:
    url = f"{SCREENER_BASE}/company/{ticker}/"
    headers = {"User-Agent": USER_AGENT, "Accept-Language": "en-IN,en;q=0.9"}
    for attempt in range(3):
        try:
            r = requests.get(url, headers=headers, timeout=REQUEST_TIMEOUT)
            if 500 <= r.status_code < 600:
                time.sleep(1.5 * (2 ** attempt))
                continue
            if r.status_code != 200:
                logger.warning(
                    "screener: HTTP %s for ticker %s (%s)",
                    r.status_code, ticker, url,
                )
                return None
            return r.text
        except requests.RequestException as e:
            logger.warning(
                "screener: fetch failed for %s (attempt %s/3): %s",
                ticker, attempt + 1, e,
            )
            time.sleep(1.5 * (2 ** attempt))
    return None


_LINK_DATE_PATTERN = re.compile(r"(?P<day>\d{1,2})\s+(?P<mon>[A-Za-z]{3,9})", re.IGNORECASE)


def _split_screener_link_text(text: str, window_start: date, window_end: date) -> Dict[str, Any]:
    """Parse a Screener announcement <a> text into {bse_category, title, date}.

    Format observed Jun 2026: "<BSE-category-prefix><DD MMM> - <summary excerpt>"
    e.g. "Announcement under Regulation 30 (LODR)-Change in Management13 May - Shri Sandee..."

    The "DD MMM" is the BSE filing date; year is inferred from the [window_start, window_end]
    bounds passed in (Screener only shows the most recent ~40 items so the year is
    unambiguous in practice).
    """
    raw = (text or "").strip()
    match = _LINK_DATE_PATTERN.search(raw)
    if not match:
        return {"bse_category": raw, "title": raw, "date": None}

    day = match.group("day")
    mon = match.group("mon")
    bse_category = raw[: match.start()].strip(" -:\t")
    summary = raw[match.end():].strip(" -:\t")

    # Infer year. Try each year in the [start.year .. end.year] range and pick the first
    # parseable, in-window date.
    parsed_date: Optional[date] = None
    candidates = sorted({window_start.year, window_end.year, date.today().year})
    for year in candidates:
        for fmt in ("%d %b %Y", "%d %B %Y"):
            try:
                candidate = datetime.strptime(f"{day} {mon} {year}", fmt).date()
            except ValueError:
                continue
            # Prefer a candidate inside [start, end]; otherwise fall back to closest.
            if window_start <= candidate <= window_end:
                parsed_date = candidate
                break
        if parsed_date:
            break
    if parsed_date is None:
        # Fall back to start.year so the caller at least has SOMETHING to inspect.
        for fmt in ("%d %b %Y", "%d %B %Y"):
            try:
                parsed_date = datetime.strptime(f"{day} {mon} {window_start.year}", fmt).date()
                break
            except ValueError:
                continue

    headline = summary or bse_category or raw
    return {"bse_category": bse_category or "", "title": headline, "date": parsed_date}


def _parse_announcements(html: str, base_url: str, window_start: date, window_end: date) -> List[Dict[str, Any]]:
    """Return a list of {title, date, link, bse_category}. Pure function — easy to test."""
    soup = BeautifulSoup(html, "lxml")
    panel = soup.find(id=ANNOUNCEMENTS_SECTION_ID) or soup
    anchors = panel.select(ANNOUNCEMENT_ROW_SELECTOR)
    out: List[Dict[str, Any]] = []
    for a in anchors:
        href = a.get("href") or ""
        # Skip the "All" link that points to the BSE corp-filing landing page.
        if not href or href.endswith(("/500", "/500/")):
            continue
        text = a.get_text(" ", strip=True)
        if not text or len(text) < 8:
            continue
        link = urljoin(base_url, href) if href else ""
        parsed = _split_screener_link_text(text, window_start, window_end)
        out.append({
            "title":        parsed["title"],
            "date":         parsed["date"],
            "link":         link,
            "bse_category": parsed["bse_category"],
        })
    return out


def fetch_announcements_in_range(
    company_name: str,
    ticker: str,
    start: date,
    end: date,
) -> List[Dict[str, Any]]:
    """Scrape Screener and return announcements with date STRICTLY in [start, end].

    Returns the same dict shape as the NSE / BSE fetchers in services/bse_announcements.py
    so the downstream classifier and gate are exchange-agnostic.
    """
    s = _coerce_date(start)
    e = _coerce_date(end)
    base_url = f"{SCREENER_BASE}/company/{ticker}/"
    html = _fetch_company_page(ticker)
    if not html:
        return []

    items = _parse_announcements(html, base_url, s, e)
    in_range = [item for item in items if item.get("date") and s <= item["date"] <= e and item.get("title")]
    logger.info(
        "screener: %s (%s) — %s parsed, %s in [%s..%s]",
        company_name, ticker, len(items), len(in_range), s.isoformat(), e.isoformat(),
    )
    return in_range


# ----- LLM classification ---------------------------------------------------------


_CLASSIFY_PROMPT = """You are classifying announcements from Indian housing finance companies.

Company: {company}
Date range (strict): {start} to {end}

Below is a numbered list of announcements. For EACH numbered announcement, return EXACTLY ONE
classification.

Announcements:
{numbered_list}

Allowed categories:
- "Hiring": appointment of a new employee, senior/CXO recruitment, lateral hire, KMP induction.
- "Management Change": CXO/board change, resignation, retirement, succession, role reassignment.
- "Rumour": clarification on news, denial or confirmation of a media report, response to market
  speculation, reply to stock-exchange query about news circulation.
- "Other": anything else (Q-results, dividends, ratings, board meetings without personnel
  detail, allotments, intimations, etc.).

Rules:
- Set "relevant" = true ONLY when category is Hiring, Management Change, or Rumour.
- "summary": one or two factual sentences. No marketing language. No URLs. No source names.
  Do not quote more than 10 consecutive words from the title.
- Always emit one entry per input announcement; preserve the original "index".

Return ONLY this JSON shape:
{{
  "items": [
    {{"index": 1, "relevant": true, "category": "Hiring", "summary": "..."}},
    {{"index": 2, "relevant": false, "category": "Other", "summary": "..."}}
  ]
}}
"""


def _build_numbered_list(announcements: List[Dict[str, Any]]) -> str:
    lines: List[str] = []
    for i, ann in enumerate(announcements, start=1):
        d = ann.get("date")
        date_str = d.isoformat() if isinstance(d, date) else "unknown"
        title = re.sub(r"\s+", " ", (ann.get("title") or "")).strip()[:240]
        lines.append(f"{i}. [{date_str}] {title}")
    return "\n".join(lines)


def classify_announcements(
    llm: Any,
    company_name: str,
    announcements: List[Dict[str, Any]],
    start: date,
    end: date,
) -> List[Dict[str, Any]]:
    """Batched LLM call. Returns enriched dicts only for relevant items."""
    if not announcements:
        return []

    prompt = _CLASSIFY_PROMPT.format(
        company=company_name,
        start=_coerce_date(start).isoformat(),
        end=_coerce_date(end).isoformat(),
        numbered_list=_build_numbered_list(announcements),
    )

    try:
        response = llm.run_json_prompt(prompt)
    except Exception as e:  # noqa: BLE001
        logger.warning("screener: LLM classification failed for %s: %s", company_name, e)
        return []

    items = response.get("items") if isinstance(response, dict) else None
    if not isinstance(items, list):
        logger.warning("screener: classification returned non-list 'items' for %s", company_name)
        return []

    enriched: List[Dict[str, Any]] = []
    for it in items:
        if not isinstance(it, dict):
            continue
        idx = it.get("index")
        if not isinstance(idx, int) or idx < 1 or idx > len(announcements):
            continue
        if not bool(it.get("relevant")):
            continue
        category = str(it.get("category", "")).strip()
        if category not in _RELEVANT_CATEGORIES:
            continue
        summary = str(it.get("summary", "")).strip()
        if not summary:
            continue
        ann = announcements[idx - 1]
        enriched.append({
            "category": category,
            "summary": summary,
            "title": ann.get("title", ""),
            "date": ann.get("date"),
        })
    return enriched


# ----- row builder + orchestrator -------------------------------------------------


def _format_summary(category: str, summary: str) -> str:
    """Prefix rumours so they're visibly tagged in the PDF; pass others through."""
    if category == "Rumour":
        return f"Rumour: {summary}"
    return summary


def collect_operational_signals(
    *,
    llm: Any,
    competitors: List[str],
    tickers: Dict[str, str],
    start: date,
    end: date,
    inter_company_sleep_seconds: float = 1.2,
) -> List[Dict[str, Any]]:
    """Run the full Screener pipeline and return rows for `competitor_rows`.

    Each returned dict has the shape:
        {"company": <name>, "weekly_summary": <text>, "signal_types": ["operational"]}

    Companies without a ticker mapping are skipped with a log line. Network/LLM failures
    are caught per-company so one bad ticker doesn't poison the run.
    """
    s = _coerce_date(start)
    e = _coerce_date(end)

    rows: List[Dict[str, Any]] = []
    for company in competitors:
        ticker = tickers.get(company)
        if not ticker:
            logger.info("screener: skipping %s (no ticker mapped)", company)
            continue

        try:
            anns = fetch_announcements_in_range(company, ticker, s, e)
        except Exception as exc:  # noqa: BLE001
            logger.warning("screener: fetch raised for %s (%s): %s", company, ticker, exc)
            anns = []

        # Polite throttle between companies regardless of outcome.
        time.sleep(inter_company_sleep_seconds)

        if not anns:
            continue

        try:
            relevant = classify_announcements(llm, company, anns, s, e)
        except Exception as exc:  # noqa: BLE001
            logger.warning("screener: classify raised for %s: %s", company, exc)
            continue

        for r in relevant:
            rows.append({
                "company": company,
                "weekly_summary": _format_summary(r["category"], r["summary"]),
                "signal_types": ["operational"],
            })

    logger.info(
        "screener: collected %s operational-signal row(s) across %s competitor(s).",
        len(rows), len(competitors),
    )
    return rows
