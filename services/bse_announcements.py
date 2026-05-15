"""BSE corp-announcements -> Competitor Intelligence (all four sub-sections).

Hits BSE's official AnnGetData API directly. Returns full historical coverage,
filtered by date, as JSON.

Endpoint:
    GET https://api.bseindia.com/BseIndiaAPI/api/AnnGetData/w
        ?strScrip=<code>&strPrevDate=YYYYMMDD&strToDate=YYYYMMDD
        &strCat=-1&subcategory=-1&strType=C&strSearch=P

Pipeline:
  1. fetch_announcements_in_range(name, scrip_code, start, end)
       Hit BSE API, filter STRICTLY to [start, end].
  2. classify_announcements(llm, name, announcements, start, end)
       Single batched Gemini call. Classifies into one of the 11 sub-categories
       across the four Competitor Intelligence buckets, OR "Other" (dropped).
  3. collect_bse_signals(...)
       Orchestrates 1+2 across the competitor list and returns rows shaped for
       the existing `competitor_rows` argument of `compose_newsletter`. The
       `signal_types` field on each row is set so the existing composer
       (_group_competitor_rows) routes the row into the right bucket without
       any composer/template changes.

Category routing (mapped to competitor_rows.signal_types -> composer buckets):

    BUCKET                  LLM categories                                   signal_types
    ---------------------   ---------------------------------------------    ------------
    Operational Signals     Hiring | Management Change (NAMED person only)   ["operational"]
    Funding & Capital       Funding | Capital | Liquidity                    ["funding"|"capital"|"liquidity"]
    Risk & Governance       Risk | Governance | AssetQuality | Rumour        ["risk"|"governance"|"asset_quality"]
    Growth & Strategy       Growth | Strategy                                ["growth"|"strategy"]
    (dropped)               Other  |  Hiring/Mgmt-Change w/o named person     -

Operational Signals rules:
  * Item must be classified as "Hiring" or "Management Change".
  * Item must NAME a specific individual.
  * If the BSE headline does not name the individual, the attached PDF is downloaded,
    its text extracted, and a second LLM call pulls out name / position / action / date.
  * If neither the headline nor the PDF yields a name, the item is dropped.

Rumours are routed to Risk & Governance (BSE "Clarification on news item" is a SEBI
governance disclosure) and stay visibly tagged: their `weekly_summary` is prefixed "Rumour: ".
"""
from __future__ import annotations

import logging
import re
import time
from datetime import date, datetime
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

import requests

logger = logging.getLogger(__name__)

BSE_API_URL = "https://api.bseindia.com/BseIndiaAPI/api/AnnGetData/w"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
REQUEST_TIMEOUT = 30
ATTACHMENT_URL_TMPL = "https://www.bseindia.com/xml-data/corpfiling/AttachLive/{name}"


# Category -> signal_types[]. signal_types steer the composer's bucketing
# (see services/newsletter_composer.py: _group_competitor_rows).
# Rumours are routed to Risk & Governance (not Operational Signals), because the user
# wants Operational Signals reserved for *named* management changes only.
_CATEGORY_TO_SIGNAL_TYPES: Dict[str, List[str]] = {
    # Operational Signals  (Hiring / Management Change must also have a person name)
    "Hiring":            ["operational"],
    "Management Change": ["operational"],
    # Rumours move here: BSE "Clarification on news item" is a SEBI governance disclosure.
    "Rumour":            ["governance"],
    # Funding & Capital
    "Funding":           ["funding"],
    "Capital":           ["capital"],
    "Liquidity":         ["liquidity"],
    # Risk & Governance
    "Risk":              ["risk"],
    "Governance":        ["governance"],
    "AssetQuality":      ["asset_quality"],
    # Growth & Strategy
    "Growth":            ["growth"],
    "Strategy":          ["strategy"],
}
_VALID_CATEGORIES = set(_CATEGORY_TO_SIGNAL_TYPES.keys())


# ----- date helpers ---------------------------------------------------------------


def _coerce_date(v: Any) -> date:
    if isinstance(v, date) and not isinstance(v, datetime):
        return v
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, str):
        return date.fromisoformat(v.strip())
    raise ValueError(f"Cannot coerce to date: {v!r}")


def _parse_bse_datetime(s: str) -> Optional[date]:
    """BSE returns ISO datetimes like '2026-04-13T14:36:55.303'."""
    if not s:
        return None
    try:
        return datetime.fromisoformat(s).date()
    except ValueError:
        try:
            return datetime.strptime(s[:10], "%Y-%m-%d").date()
        except ValueError:
            return None


# ----- fetch + parse --------------------------------------------------------------


def _call_bse_api(scrip_code: str, start: date, end: date) -> List[Dict[str, Any]]:
    """Single GET against the BSE AnnGetData endpoint. Returns the 'Table' array or []."""
    headers = {
        "User-Agent": USER_AGENT,
        "Referer":    "https://www.bseindia.com/",
        "Origin":     "https://www.bseindia.com",
        "Accept":     "application/json, text/plain, */*",
    }
    params = {
        "strCat":      "-1",
        "strPrevDate": start.strftime("%Y%m%d"),
        "strToDate":   end.strftime("%Y%m%d"),
        "strScrip":    scrip_code,
        "strSearch":   "P",
        "strType":     "C",
        "subcategory": "-1",
    }

    for attempt in range(3):
        try:
            r = requests.get(BSE_API_URL, params=params, headers=headers, timeout=REQUEST_TIMEOUT)
            if 500 <= r.status_code < 600:
                time.sleep(1.5 * (2 ** attempt))
                continue
            if r.status_code != 200:
                logger.warning(
                    "bse: HTTP %s for scrip %s (%s..%s); body: %s",
                    r.status_code, scrip_code, params["strPrevDate"], params["strToDate"],
                    r.text[:200],
                )
                return []
            try:
                data = r.json()
            except ValueError:
                logger.warning("bse: non-JSON response for scrip %s; first 200 chars: %s",
                               scrip_code, r.text[:200])
                return []
            rows = data.get("Table") or []
            if not isinstance(rows, list):
                return []
            return rows
        except requests.RequestException as e:
            logger.warning("bse: request failed for %s (attempt %s/3): %s",
                           scrip_code, attempt + 1, e)
            time.sleep(1.5 * (2 ** attempt))
    return []


def _row_to_announcement(row: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize one BSE API row to our internal shape."""
    title = (row.get("HEADLINE") or row.get("NEWSSUB") or "").strip()
    d = _parse_bse_datetime(row.get("NEWS_DT") or row.get("DT_TM") or "")
    attachment = (row.get("ATTACHMENTNAME") or "").strip()
    link = ATTACHMENT_URL_TMPL.format(name=attachment) if attachment else ""
    return {
        "title": title,
        "date": d,
        "link": link,
        "bse_category": (row.get("CATEGORYNAME") or "").strip(),
    }


def fetch_announcements_in_range(
    company_name: str,
    scrip_code: str,
    start: date,
    end: date,
) -> List[Dict[str, Any]]:
    """Fetch from BSE and return announcements STRICTLY inside [start, end]."""
    s = _coerce_date(start)
    e = _coerce_date(end)
    rows = _call_bse_api(scrip_code, s, e)

    in_range: List[Dict[str, Any]] = []
    for row in rows:
        ann = _row_to_announcement(row)
        if ann["date"] and s <= ann["date"] <= e and ann["title"]:
            in_range.append(ann)

    logger.info(
        "bse: %s (scrip %s) - %s total returned, %s in [%s..%s]",
        company_name, scrip_code, len(rows), len(in_range),
        s.isoformat(), e.isoformat(),
    )
    return in_range


# ----- LLM classification ---------------------------------------------------------


_CLASSIFY_PROMPT = """You are classifying corp-announcements filed with BSE by Indian
housing-finance companies.

Company: {company}
Date range (strict): {start} to {end}

Below is a numbered list of announcements (date, BSE category, headline). For EACH
numbered item, return EXACTLY ONE classification.

Announcements:
{numbered_list}

Allowed categories — pick ONE for each item:

A) OPERATIONAL SIGNALS — STRICT GATE. Emit ONLY when ALL THREE conditions hold:
   (1) NAMED INDIVIDUAL — an actual personal name (e.g. "Shri Sanjay Dayal",
       "Mr. Manish Chourasia", "Dr. Vibha Padalkar"). A role/title alone such as
       "MD & CEO", "CFO", "Managing Director", "Chief Operating Officer" does NOT
       count as a named individual.
   (2) SPECIFIC ACTION — exactly one of: Appointment | Resignation | Retirement |
       Role Change | Death. Routine items such as investor meetings, analyst meets,
       earnings calls, board-meeting intimations, business-update sessions,
       trading-window notices, voting-result intimations etc. DO NOT qualify and
       must be classified as "Other".
   (3) EVENT DATE — the date the change took effect, or the date of death.
- "Hiring": a NAMED individual is appointed / inducted (action="Appointment").
- "Management Change": a NAMED individual resigns / retires / moves role / dies
  (action="Resignation" | "Retirement" | "Role Change" | "Death").
- "Rumour": clarification on news, denial or confirmation of a media report, response to
  market speculation, reply to stock-exchange query about news circulation

B) FUNDING & CAPITAL
- "Funding": debt issuance (NCDs, bonds, debentures), borrowing approval, refinancing
- "Capital": equity raise (rights / preferential / QIP), share allotment, conversion
- "Liquidity": commercial paper, working-capital arrangements, redemption schedule

C) RISK & GOVERNANCE
- "Risk": credit risk event, operational risk, fraud disclosure, material litigation
- "Governance": audit-committee action, regulatory order, compliance disclosure, AGM/EGM matters
- "AssetQuality": NPA disclosure, slippage, restructuring, write-off, provision change

D) GROWTH & STRATEGY
- "Growth": new partnership, geographic expansion, branch/product launch, distribution tie-up
- "Strategy": M&A, divestment, business restructuring, strategic announcement

E) "Other" — anything routine: q-results without commentary, dividend declarations, board-meeting
  intimations, voting results, scrutinizer reports, certificates, register-of-charges filings, etc.

Rules:
- Set "relevant" = true ONLY when category is one of the 11 above (NOT "Other").
- MATERIALITY CAP: Across ALL items for this company, mark AT MOST 2 items as
  relevant=true — the two most material to a senior HFC executive reviewing
  competitor activity for the period. Mark every other item "Other" (relevant=false),
  EVEN IF it would technically fit one of the categories above. Prefer concrete events
  (e.g., management change, capital raise, NPA disclosure, rumour clarification) over
  routine intimations.
- "summary" formatting:
  * ONE concise factual sentence, max ~25 words.
  * MUST start with a capital letter and end with a single period (never "..", "...",
    a trailing comma, or a dangling clause).
  * MUST NOT start with the company name (the renderer prepends it). Start with a verb
    or noun phrase, e.g. "Appointed Sanjay Dayal as Chief Operating Officer." — NOT
    "LIC Housing Finance appointed..."
  * NEVER use meta-phrases: "the provided article", "the article states",
    "this announcement", "as per the filing", "according to the source".
    Write the FACT directly.
  * No marketing language, no URLs, no source names, no quoted strings >10 words.
- Always emit one entry per input announcement; preserve the original "index". List the
  two relevant=true items FIRST in the "items" array (most material first).

For Hiring and Management Change items, ALSO populate THREE extra fields:
- "person_name": real personal name with honorific if present (e.g. "Shri Sanjay Dayal",
  "Mr. Manish Chourasia"). NEVER a role/title like "MD & CEO" or "CFO" or "Managing Director".
  If the headline does NOT name the individual, set to null — a downstream step will read
  the attached PDF to try to recover it.
- "action": one of "Appointment" | "Resignation" | "Retirement" | "Role Change" | "Death",
  or null if the headline does not state the action clearly.
- "event_date": "YYYY-MM-DD" of when the action took effect (or the date of death). null if
  the headline does not state the date clearly.

For ALL OTHER categories, set person_name, action, and event_date to null.

Return ONLY this JSON shape:
{{
  "items": [
    {{"index": 1, "relevant": true, "category": "Hiring",
      "person_name": "Shri Sanjay Dayal", "action": "Appointment",
      "event_date": "2026-05-09", "summary": "..."}},
    {{"index": 2, "relevant": true, "category": "Management Change",
      "person_name": null, "action": null, "event_date": null, "summary": "..."}},
    {{"index": 3, "relevant": false, "category": "Other",
      "person_name": null, "action": null, "event_date": null, "summary": "..."}}
  ]
}}
"""


# Deterministic pre-classifier: if an announcement title clearly indicates a management
# change, force category="Management Change" even if the LLM classifies it as "Other".
# This catches obvious filings that flash-lite / flash-pro occasionally mis-bucket.
_MGMT_CHANGE_TITLE_PATTERNS = (
    r"\bchange in (senior )?(management|key managerial|director)",
    r"\bchange in (smp|kmp)\b",
    r"\bresignation of\b",
    r"\bappointment of\b",
    r"\bretirement of\b",
    r"\bdemise of\b",
    r"\bpass(?:ing|ed) away\b",
    r"\bcessation of\b",
    r"\bappointed as\b",
    r"\b(?:reg|regulation)\.?\s*30.*\b(change|management)\b",
)
_MGMT_CHANGE_TITLE_RE = re.compile("|".join(_MGMT_CHANGE_TITLE_PATTERNS), re.IGNORECASE)


def _title_indicates_management_change(title: str) -> bool:
    return bool(title and _MGMT_CHANGE_TITLE_RE.search(title))


def _build_numbered_list(announcements: List[Dict[str, Any]]) -> str:
    lines: List[str] = []
    for i, ann in enumerate(announcements, start=1):
        d = ann.get("date")
        date_str = d.isoformat() if isinstance(d, date) else "unknown"
        title = (ann.get("title") or "").replace("\n", " ").strip()[:240]
        cat = (ann.get("bse_category") or "").strip()
        cat_tag = f" [{cat}]" if cat else ""
        lines.append(f"{i}. [{date_str}]{cat_tag} {title}")
    return "\n".join(lines)


def classify_announcements(
    llm: Any,
    company_name: str,
    announcements: List[Dict[str, Any]],
    start: date,
    end: date,
) -> List[Dict[str, Any]]:
    """Batched LLM call. Returns enriched dicts ONLY for items in a relevant category."""
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
        logger.warning("bse: LLM classification failed for %s: %s", company_name, e)
        return []

    items = response.get("items") if isinstance(response, dict) else None
    if not isinstance(items, list):
        logger.warning("bse: classification returned non-list 'items' for %s", company_name)
        items = []

    # Index the LLM's verdicts by `index` so we can patch them.
    by_index: Dict[int, Dict[str, Any]] = {}
    for it in items:
        if not isinstance(it, dict):
            continue
        idx = it.get("index")
        if isinstance(idx, int) and 1 <= idx <= len(announcements):
            by_index[idx] = it

    # Title-pattern override: if a headline UNAMBIGUOUSLY indicates a management change
    # (e.g. "Change in Senior Management Personnel", "Resignation of <X>", "Demise of <Y>")
    # but the LLM classified it as "Other" / Funding / Risk / Growth, force it to
    # "Management Change". Downstream gate still requires named-individual + action + date
    # (via PDF enrichment if needed) so we don't admit garbage — we just stop the LLM
    # from silently dropping obvious filings.
    for idx, ann in enumerate(announcements, start=1):
        title = (ann.get("title") or "").strip()
        if not _title_indicates_management_change(title):
            continue
        existing = by_index.get(idx, {})
        if existing.get("category") in ("Hiring", "Management Change"):
            continue
        by_index[idx] = {
            **existing,
            "index": idx,
            "relevant": True,
            "category": "Management Change",
            # person_name / action / event_date stay whatever the LLM (if any) supplied;
            # if they are empty, the PDF-enrichment fallback in collect_bse_signals will
            # try to recover them, and the final gate still drops items that don't reach
            # named-individual + valid-action + event-date.
            "summary": existing.get("summary") or "Change in management personnel.",
        }
        logger.info(
            "bse: title-pattern override → forced Management Change for %s "
            "(title=%r, llm-category was %r)",
            company_name, title[:80], existing.get("category"),
        )

    enriched: List[Dict[str, Any]] = []
    for idx in sorted(by_index.keys()):
        it = by_index[idx]
        if not bool(it.get("relevant")):
            continue
        category = str(it.get("category", "")).strip()
        if category not in _VALID_CATEGORIES:
            continue
        summary = str(it.get("summary", "")).strip()
        if not summary:
            continue
        ann = announcements[idx - 1]
        def _opt_str(key: str) -> str:
            v = it.get(key)
            return str(v).strip() if v else ""
        enriched.append({
            "category": category,
            "summary": summary,
            "title": ann.get("title", ""),
            "date": ann.get("date"),
            "link": ann.get("link", ""),
            "person_name": _opt_str("person_name"),
            "action":      _opt_str("action"),
            "event_date":  _opt_str("event_date"),
        })
    return enriched


# ----- PDF enrichment for missing-name management-change items --------------------
# When the BSE headline says e.g. "Change in Senior Management Personnel" but does not
# name the individual, we download the attached PDF, extract its text, and ask the LLM
# to pull out the name / position / action / date / reason. Only if the PDF yields a
# name do we keep the item in Operational Signals.


_PDF_EXTRACT_PROMPT = """You are reading a BSE corporate-announcement PDF filing about
ONE OR MORE management changes at an Indian housing-finance company. Many filings bundle
a resignation AND a replacement appointment in the same document — extract EVERY named
change, not just the first one.

Return JSON ONLY in this exact shape:
{{
  "changes": [
    {{
      "person_name": "Real personal name with honorific if present (e.g. 'Shri Sanjay Dayal').
                      NEVER a role/title alone like 'MD & CEO' or 'CFO'.",
      "position":    "Role / designation, e.g. 'Chief Operating Officer'",
      "action":      "Appointment | Resignation | Retirement | Role Change | Death  (one of these five only)",
      "effective_date": "YYYY-MM-DD — the date this specific action took effect (or the date of death). null only if truly not stated.",
      "reason":      "Short reason if stated, max ~12 words, else null"
    }}
  ]
}}

Rules:
- Return ONE entry in `changes` for EACH distinct named individual whose role / status
  changes. If the filing covers Selvin Uthaman resigning AND Ripudaman Bandral moving
  roles, BOTH must appear in `changes`, in the order they appear in the filing.
- person_name MUST be a real personal name. NEVER a title alone. Skip events where
  no individual is named.
- action MUST be exactly one of the five canonical values.
- effective_date is the date the action takes effect (NOT the filing date). It is
  per-person — different people in the same filing can have different effective dates.
- Do NOT invent any field. Use null for anything the filing does not state.
- If the filing describes NO personnel changes (e.g. it's actually a dividend notice
  filed under the wrong category), return `{{"changes": []}}`.

Filing text:
\"\"\"
{text}
\"\"\"
"""


# Pass-2 prompt: simpler/flatter shape with the SAME multi-event support. Used when the
# structured prompt above returned malformed output.
_PDF_EXTRACT_MINIMAL_PROMPT = """Read the filing below. It announces ONE OR MORE personnel
changes at an Indian housing-finance company. Many filings bundle two events (e.g. a
resignation AND a replacement appointment) — list every named change.

For each named individual whose role / status changes, output exactly these four facts:
1. The person's full name (with honorific like Mr./Mrs./Ms./Dr./Shri/Smt. if present).
2. The action: exactly one of Appointment / Resignation / Retirement / Role Change / Death.
3. The position / role title (e.g. "Chief Business Officer").
4. The effective date as YYYY-MM-DD.

Reply with ONLY a JSON object whose `changes` key holds a list. No prose, no markdown,
no code fences. Use this EXACT shape (each item is a flat object). If a field is genuinely
not stated, use empty string "".

{{"changes":[
  {{"person_name":"Mr. Selvin Uthaman","action":"Resignation","position":"Chief Business Officer","event_date":"2026-05-01"}},
  {{"person_name":"Mr. Ripudaman Bandral","action":"Role Change","position":"Chief Business Officer","event_date":"2026-05-02"}}
]}}

Filing:
\"\"\"
{text}
\"\"\"
"""


def _try_download_once(url: str) -> Optional[bytes]:
    headers = {"User-Agent": USER_AGENT, "Referer": "https://www.bseindia.com/"}
    try:
        r = requests.get(url, headers=headers, timeout=REQUEST_TIMEOUT)
    except requests.RequestException as e:
        logger.info("bse: PDF download failed for %s: %s", url, e)
        return None
    if r.status_code != 200:
        logger.info("bse: PDF download HTTP %s for %s", r.status_code, url)
        return None
    if not r.content.startswith(b"%PDF-"):
        logger.info("bse: download for %s is not a PDF (first bytes: %s)",
                    url, r.content[:8])
        return None
    return r.content


def _download_pdf_bytes(url: str) -> Optional[bytes]:
    """Download a BSE attachment PDF. BSE serves recent filings under
    `corpfiling/AttachLive/<uuid>.pdf` and older ones under `AttachHis/...`. The
    AnnGetData API only returns the bare filename so we don't always know which
    path is current — try the URL as given, then the alternate Attach* path.
    """
    if not url:
        return None
    payload = _try_download_once(url)
    if payload is not None:
        return payload
    # Fallback: swap AttachLive <-> AttachHis
    alt = None
    if "/AttachLive/" in url:
        alt = url.replace("/AttachLive/", "/AttachHis/")
    elif "/AttachHis/" in url:
        alt = url.replace("/AttachHis/", "/AttachLive/")
    if alt:
        logger.info("bse: trying alternate BSE path: %s", alt)
        return _try_download_once(alt)
    return None


def _extract_pdf_text(pdf_bytes: bytes) -> str:
    try:
        import io
        import pdfplumber
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            chunks = []
            for page in pdf.pages[:5]:  # 5 pages is plenty for an announcement
                t = page.extract_text()
                if t:
                    chunks.append(t)
        return "\n".join(chunks).strip()
    except Exception as e:  # noqa: BLE001
        logger.info("bse: pdfplumber failed to extract text: %s", e)
        return ""


def _try_llm_extract(llm: Any, prompt: str, pass_label: str) -> Optional[Dict[str, Any]]:
    """Single LLM extraction attempt. Tolerates the LLM wrapping the result in a list."""
    try:
        result = llm.run_json_prompt(prompt)
    except Exception as e:  # noqa: BLE001
        logger.info("bse: PDF extraction %s LLM call failed: %s", pass_label, e)
        return None
    if isinstance(result, list) and result and isinstance(result[0], dict):
        result = result[0]
    if not isinstance(result, dict):
        logger.info("bse: PDF extraction %s returned non-dict: %r", pass_label, type(result).__name__)
        return None
    return result


def _normalize_changes(extracted: Any) -> List[Dict[str, Any]]:
    """Pull a list of change dicts out of whatever shape the LLM returned.

    Supported shapes (tolerant of common drift):
      * {"changes": [ {...}, {...} ]}                    -- new canonical shape
      * [ {...}, {...} ]                                 -- bare list
      * {"person_name": "...", ...}                      -- legacy single-event dict
    Returns a list of dicts; each dict is a single named change. Empty list if the
    shape can't be reconciled or nothing was extracted.
    """
    if isinstance(extracted, dict):
        if isinstance(extracted.get("changes"), list):
            return [c for c in extracted["changes"] if isinstance(c, dict)]
        # Legacy single-event shape — wrap so downstream code can iterate uniformly.
        if (extracted.get("person_name") or "").strip():
            return [extracted]
    if isinstance(extracted, list):
        return [c for c in extracted if isinstance(c, dict)]
    return []


def _enrich_from_pdf(llm: Any, announcement: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Download attached PDF, extract text, ask LLM to pull out structured fields.

    Returns a LIST of change dicts (one per named individual in the filing). A single
    BSE filing often bundles multiple events — e.g. one person resigns and a successor
    is appointed in the same document. Returning a list lets us emit one Operational
    Signals row per named change.

    Each item in the returned list has the keys:
      person_name, position, action, effective_date, reason

    Two LLM passes:
      Pass 1 — full schema. Used when the LLM behaves.
      Pass 2 — minimal flat shape (smaller, harder to mangle).
    Returns [] on hard failure or when the filing names no individuals.
    """
    link = (announcement.get("link") or "").strip()
    if not link:
        return []
    pdf_bytes = _download_pdf_bytes(link)
    if not pdf_bytes:
        return []
    text = _extract_pdf_text(pdf_bytes)
    if not text or len(text) < 80:
        return []

    # --- Pass 1: full schema -------------------------------------------------
    pass1 = _try_llm_extract(llm,
                             _PDF_EXTRACT_PROMPT.format(text=text[:8000]),
                             "pass1")
    changes1 = _normalize_changes(pass1)
    changes1 = [c for c in changes1 if (c.get("person_name") or "").strip()]
    if changes1:
        return changes1

    # --- Pass 2: minimal-shape fallback -------------------------------------
    # Triggers when pass 1 returned no usable named change. The minimal prompt is
    # smaller and easier for the LLM to produce correctly.
    logger.info("bse: PDF extraction falling back to minimal-shape prompt for %s", link)
    pass2 = _try_llm_extract(llm,
                             _PDF_EXTRACT_MINIMAL_PROMPT.format(text=text[:8000]),
                             "pass2")
    changes2 = _normalize_changes(pass2)
    out: List[Dict[str, Any]] = []
    for c in changes2:
        if not (c.get("person_name") or "").strip():
            continue
        # Minimal prompt uses "event_date"; canonical key downstream is "effective_date".
        if "event_date" in c and "effective_date" not in c:
            c["effective_date"] = c.pop("event_date")
        out.append(c)
    return out


_VALID_OPERATIONAL_ACTIONS = ("Appointment", "Resignation", "Retirement", "Role Change", "Death")

# Tokens that strongly suggest the string is a role/title, not a personal name.
_TITLE_TOKENS = {
    "director", "officer", "ceo", "cfo", "coo", "cto", "cio", "cmo", "managing", "executive",
    "chief", "head", "vice", "president", "secretary", "treasurer", "auditor", "manager",
    "kmp", "personnel",
}


def _looks_like_personal_name(s: str) -> bool:
    """True if `s` looks like an actual personal name; False if it's just a role/title."""
    if not s:
        return False
    s = s.strip()
    if not s:
        return False
    # Strong positive: honorific present
    if re.search(r"\b(Shri|Smt|Sri|Mr|Mrs|Ms|Dr)\.?\b", s, re.IGNORECASE):
        return True
    # Strong negative: every meaningful word is a title token
    words = [w.lower() for w in re.findall(r"[A-Za-z][A-Za-z'\-]+", s)]
    if not words:
        return False
    if all(w in _TITLE_TOKENS for w in words):
        return False
    # Heuristic positive: at least two consecutive Capitalised personal-name-shaped words.
    if re.search(r"\b[A-Z][a-z]{2,}\s+[A-Z][a-z]{2,}\b", s):
        return True
    # Otherwise treat as not-a-name.
    return False


def _normalize_action(raw: str) -> str:
    """Map LLM action verbs to the canonical 5-value enum, or '' if not recognised."""
    if not raw:
        return ""
    s = raw.strip().lower().rstrip(".")
    mapping = {
        "appointment": "Appointment", "appointed": "Appointment", "induction": "Appointment",
        "hired": "Appointment", "joined": "Appointment",
        "resignation": "Resignation", "resigned": "Resignation", "ceased": "Resignation",
        "retirement": "Retirement", "retired": "Retirement",
        "role change": "Role Change", "reassigned": "Role Change",
        "reassignment": "Role Change", "transferred": "Role Change", "promoted": "Role Change",
        "death": "Death", "demise": "Death", "deceased": "Death", "passed away": "Death",
    }
    return mapping.get(s, "")


def _build_operational_summary(name: str, action: str, position: str, eff_date: str, reason: str = "") -> str:
    """Build a clean one-sentence Operational Signals summary. All three core inputs
    (name, action, eff_date) must already be validated. Returns "" if anything is missing.
    """
    if not (name and action and eff_date):
        return ""
    parts: List[str] = []
    if action == "Appointment":
        parts.append(f"Appointed {name}")
        if position:
            parts.append(f"as {position}")
    elif action == "Resignation":
        parts.append(f"{name} resigned")
        if position:
            parts.append(f"as {position}")
    elif action == "Retirement":
        parts.append(f"{name} retired")
        if position:
            parts.append(f"as {position}")
    elif action == "Role Change":
        parts.append(f"{name} moved roles")
        if position:
            parts.append(f"to {position}")
    elif action == "Death":
        parts.append(f"{name} passed away")
        if position:
            parts.append(f"while serving as {position}")
    parts.append(f"effective {eff_date}")
    if reason and len(reason) <= 80:
        parts.append(f"— {reason}")
    summary = " ".join(parts).strip()
    if summary and summary[-1] not in ".!?":
        summary += "."
    return summary


# ----- ArticleStore audit helpers -------------------------------------------------
# Optional integration: when an ArticleStore instance is passed to collect_bse_signals,
# every BSE filing is persisted into the same DB used by the news pipeline, with the
# attachment PDF URL as the unique key. Classification / PDF-enrichment / final-gate
# outcomes are recorded as `article_pipeline` events so a single DataFrame can audit
# the full provenance (news + BSE) of every newsletter row.


def _bse_article_record(company: str, ann: Dict[str, Any]) -> SimpleNamespace:
    """Build a SimpleNamespace shaped for ArticleStore.upsert_articles().

    Uses the announcement's attachment PDF URL as the canonical URL — that's the unique,
    citable artifact for audit, and it dedupes naturally across re-runs.
    """
    d = ann.get("date")
    return SimpleNamespace(
        url=(ann.get("link") or "").strip(),
        original_url=(ann.get("link") or "").strip(),
        is_aggregator=False,
        company=company,
        title=(ann.get("title") or "").strip(),
        source="BSE",
        published_at=d.isoformat() if isinstance(d, date) else "",
    )


def _safe_record(store: Any, *, url: str, stage: str, payload: Dict[str, Any]) -> None:
    """Record a pipeline event; swallow any failure so audit never breaks the run."""
    if store is None or not url:
        return
    try:
        store.record_by_url(url=url, stage=stage, payload=payload)
    except Exception as exc:  # noqa: BLE001
        logger.warning("bse: audit write failed (stage=%s, url=%s): %s", stage, url, exc)


def _safe_upsert(store: Any, records: List[SimpleNamespace]) -> None:
    if store is None or not records:
        return
    try:
        store.upsert_articles(records)
    except Exception as exc:  # noqa: BLE001
        logger.warning("bse: audit upsert failed: %s", exc)


# ----- row builder + orchestrator -------------------------------------------------


def _format_summary(category: str, summary: str) -> str:
    """Prefix rumours so they're visibly tagged in the PDF; pass others through."""
    if category == "Rumour":
        return f"Rumour: {summary}"
    return summary


def collect_bse_signals(
    *,
    llm: Any,
    competitors: List[str],
    bse_codes: Dict[str, str],
    start: date,
    end: date,
    inter_company_sleep_seconds: float = 0.6,
    store: Any = None,
) -> List[Dict[str, Any]]:
    """Run the full BSE pipeline and return rows for `competitor_rows`.

    Each returned dict has the shape:
        {"company": <name>, "weekly_summary": <text>, "signal_types": [<routing tag>]}

    The signal_types value steers the newsletter composer's bucketing:
      ["operational"]                    -> Operational Signals
      ["funding"|"capital"|"liquidity"]  -> Funding & Capital
      ["risk"|"governance"|"asset_quality"] -> Risk & Governance
      ["growth"|"strategy"]              -> Growth & Strategy (default)

    Companies without a BSE security code mapping are skipped with a log line.
    Network/LLM failures are caught per-company so one bad scrip doesn't poison the run.
    """
    s = _coerce_date(start)
    e = _coerce_date(end)

    rows: List[Dict[str, Any]] = []
    bucket_counts: Dict[str, int] = {
        "Operational Signals": 0,
        "Funding & Capital": 0,
        "Risk & Governance": 0,
        "Growth & Strategy": 0,
    }
    bucket_of = {
        "Hiring": "Operational Signals", "Management Change": "Operational Signals",
        # Rumours route to Risk & Governance — BSE "Clarification on news item" is a
        # SEBI governance disclosure, and the user reserved Operational Signals for
        # named management changes only.
        "Rumour": "Risk & Governance",
        "Funding": "Funding & Capital", "Capital": "Funding & Capital", "Liquidity": "Funding & Capital",
        "Risk": "Risk & Governance", "Governance": "Risk & Governance", "AssetQuality": "Risk & Governance",
        "Growth": "Growth & Strategy", "Strategy": "Growth & Strategy",
    }

    for company in competitors:
        scrip = (bse_codes.get(company) or "").strip()
        if not scrip:
            logger.info("bse: skipping %s (no BSE security code mapped)", company)
            continue

        try:
            anns = fetch_announcements_in_range(company, scrip, s, e)
        except Exception as exc:  # noqa: BLE001
            logger.warning("bse: fetch raised for %s (scrip %s): %s", company, scrip, exc)
            anns = []

        time.sleep(inter_company_sleep_seconds)

        if not anns:
            continue

        # AUDIT: every fetched BSE filing is upserted into the articles table so the
        # auditor can see the full universe of filings considered for this period.
        _safe_upsert(store, [_bse_article_record(company, a) for a in anns if a.get("link")])

        try:
            relevant = classify_announcements(llm, company, anns, s, e)
        except Exception as exc:  # noqa: BLE001
            logger.warning("bse: classify raised for %s: %s", company, exc)
            continue

        # AUDIT: record the classifier's verdict for each in-scope (relevant=true) item.
        # Dropped/"Other" items don't get a pipeline event but their article row is still
        # in `articles` (from the upsert above), so the auditor knows they were considered.
        for r in relevant:
            _safe_record(store, url=(r.get("link") or "").strip(),
                         stage="bse_classification",
                         payload={
                             "category":   r.get("category"),
                             "person_name": r.get("person_name"),
                             "action":     r.get("action"),
                             "event_date": r.get("event_date"),
                             "summary":    r.get("summary"),
                         })

        # Operational Signals STRICT gate. To survive, a Hiring / Management Change row
        # must have ALL THREE: (1) a real personal name, (2) a recognized action from the
        # 5-value enum, (3) an event date. When any of these is missing from the headline,
        # we download the attached BSE PDF and ask the LLM to enumerate EVERY named change
        # in the filing — many BSE filings bundle multiple events (e.g. one person resigns
        # AND a successor is appointed in the same document). Each named change is then
        # individually gated and, if it passes, emitted as its own Operational Signals row.
        gated: List[Dict[str, Any]] = []
        for r in relevant:
            cat = r["category"]
            if cat in ("Hiring", "Management Change"):
                hd_name   = (r.get("person_name") or "").strip()
                hd_action = _normalize_action(r.get("action") or "")
                hd_date   = (r.get("event_date") or "").strip()

                # Build the list of candidate (name, action, position, date, reason) tuples
                # to evaluate against the gate. Start with the headline-derived fields if
                # they already form a complete row — otherwise enrich from the PDF.
                headline_complete = (
                    _looks_like_personal_name(hd_name)
                    and hd_action in _VALID_OPERATIONAL_ACTIONS
                    and hd_date
                )
                candidates: List[Dict[str, Any]] = []
                if headline_complete:
                    candidates.append({
                        "person_name":    hd_name,
                        "action":         hd_action,
                        "effective_date": hd_date,
                        "position":       "",
                        "reason":         "",
                    })
                else:
                    # PDF enrichment returns a list — one dict per named individual.
                    extracted_list = _enrich_from_pdf(llm, r)
                    _safe_record(store, url=(r.get("link") or "").strip(),
                                 stage="bse_pdf_enrichment",
                                 payload={
                                     "success":   bool(extracted_list),
                                     "count":     len(extracted_list),
                                     "extracted": extracted_list,
                                 })
                    for ex in extracted_list:
                        candidates.append({
                            "person_name":    (ex.get("person_name") or "").strip(),
                            "action":         _normalize_action(ex.get("action") or ""),
                            "effective_date": (ex.get("effective_date") or "").strip(),
                            "position":       (ex.get("position") or "").strip(),
                            "reason":         (ex.get("reason") or "").strip(),
                        })

                # Now run each candidate through the named-individual / action / date gate.
                kept_any_for_this_filing = False
                for cand in candidates:
                    name   = cand["person_name"]
                    action = cand["action"]
                    edate  = cand["effective_date"]
                    if not (
                        _looks_like_personal_name(name)
                        and action in _VALID_OPERATIONAL_ACTIONS
                        and edate
                    ):
                        continue
                    rebuilt = _build_operational_summary(
                        name, action, cand["position"], edate, cand["reason"],
                    )
                    if not rebuilt:
                        continue
                    gated.append({
                        **r,
                        "person_name": name,
                        "action":      action,
                        "event_date":  edate,
                        "summary":     rebuilt,
                    })
                    kept_any_for_this_filing = True
                    logger.info(
                        "bse: operational item kept for %s — %s (%s, %s).",
                        company, name, action, edate,
                    )

                if not kept_any_for_this_filing:
                    logger.info(
                        "bse: dropping operational filing for %s — no named change passed "
                        "the gate (candidates=%s).",
                        company, len(candidates),
                    )
                    _safe_record(store, url=(r.get("link") or "").strip(),
                                 stage="bse_gate_dropped",
                                 payload={
                                     "reason":     "missing_required_field",
                                     "candidates": candidates,
                                 })
                continue  # operational items handled — don't fall through to the generic append below

            # Non-operational items (Funding / Risk / Growth / Rumour) pass through unchanged.
            gated.append(r)

        # Brevity cap: at most 2 line items per competitor in the newsletter.
        gated = gated[:2]

        for r in gated:
            cat = r["category"]
            signal_types = list(_CATEGORY_TO_SIGNAL_TYPES.get(cat, []))
            final_summary = _format_summary(cat, r["summary"])
            rows.append({
                "company": company,
                "weekly_summary": final_summary,
                "signal_types": signal_types,
            })
            bucket_counts[bucket_of[cat]] = bucket_counts.get(bucket_of[cat], 0) + 1

            # AUDIT: record the final disposition of every BSE filing that made it into
            # the newsletter, including its newsletter bucket and the final summary used.
            _safe_record(store, url=(r.get("link") or "").strip(),
                         stage="bse_kept",
                         payload={
                             "bucket":        bucket_of[cat],
                             "category":     cat,
                             "signal_types": signal_types,
                             "summary":      final_summary,
                             "person_name":  r.get("person_name") or "",
                             "action":       r.get("action") or "",
                             "event_date":   r.get("event_date") or "",
                         })

    summary_parts = [f"{k}={v}" for k, v in bucket_counts.items() if v]
    logger.info(
        "bse: collected %s row(s) across %s competitor(s); buckets: %s",
        len(rows), len(competitors), ", ".join(summary_parts) or "(none)",
    )
    return rows


# Backwards-compatible alias — keep callers that imported the old name working.
collect_operational_signals = collect_bse_signals
