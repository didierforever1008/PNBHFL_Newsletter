from __future__ import annotations

import argparse
import difflib
import json
import logging
import re
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Callable, Dict, List, Tuple
from urllib.parse import urlparse

from config import get_bse_security_codes, get_competitors, get_settings
from models.schemas import (
    WeeklyCompanySection,
    parse_weekly_bundle,
)
from renderers.pdf_renderer import render_newsletter_pdf
from services.article_store import ArticleStore
from services.gemini_search import GeminiSearchClient
from services.news_api import NewsAPIClient, articles_to_prompt_block, collect_articles_for_competitors
from services.prompts import (
    article_signal_summary_prompt,
    weekly_company_intelligence_prompt,
    weekly_digest_agentic_competitor_prompt,
    weekly_digest_agentic_final_synthesis_prompt,
    weekly_digest_agentic_industry_prompt,
    weekly_digest_agentic_regulatory_prompt,
)
from services.agentic_pipeline import dedupe_by_title, normalize_article_outputs
from services.dedup import deduplicate_semantic_articles
from services.newsletter_composer import compose_newsletter
from services.bse_announcements import collect_bse_signals
from services.scoring import (
    annotate_base_scores,
    build_selection_metrics,
    finalize_scores,
    select_balanced_articles,
)
from services.source_feeds import fetch_rss_articles
from services.source_feeds import fetch_policy_and_rating_articles
from services.section_validators import (
    ALLOWED_COMPETITOR_SIGNAL_TYPES,
    ValidationResult,
    validate_competitor_section,
    validate_industry_section,
    validate_regulatory_section,
)
from utils.url_tools import resolve_article_url


# --- India-only filter -------------------------------------------------------------
# Hits if any of these tokens appear in title/description/content; misses if the article
# is clearly about a foreign market or foreign issuer.
_INDIA_HINTS = (
    "india", "indian", "rbi", "nhb", " crisil", " icra", " sebi", "rupee", "₹", " rs.",
    " inr ", "bse ", " nse ", "mumbai", "new delhi", "bengaluru", "chennai", "kolkata",
    "hyderabad", "pmay", "modi", "nirmala sitharaman", "finance ministry", "ministry of finance",
)
_NON_INDIA_TOKENS = (
    "anz ", "freddie mac", "fannie mae", "lloyds", "nationwide", "deutsche bank",
    "berlin", "frankfurt", "london", " uk ", "united kingdom", " us ", " u.s. ",
    "united states", "australia", "new zealand", "singapore", "hong kong", "tokyo",
    "germany", "german mortgage", "midwest", "florida", "california",
)


def _looks_indian(article) -> bool:
    blob = " ".join(str(x or "") for x in (
        getattr(article, "title", ""),
        getattr(article, "description", ""),
        getattr(article, "content", ""),
        getattr(article, "source", ""),
    )).lower()
    if any(tok in blob for tok in _INDIA_HINTS):
        return True
    # No India signal AND has a strong foreign signal -> drop.
    if any(tok in blob for tok in _NON_INDIA_TOKENS):
        return False
    # No signal either way: keep, since most NewsAPI hits for our queries are Indian.
    return True


def _filter_india_only(articles):
    kept = [a for a in articles if _looks_indian(a)]
    dropped = len(articles) - len(kept)
    if dropped:
        logging.getLogger(__name__).info(
            "India-filter: kept %s of %s industry articles (%s dropped as non-Indian).",
            len(kept), len(articles), dropped,
        )
    return kept


# --- Placeholder / "no-news" row filter --------------------------------------------
# The LLM sometimes emits rows that just explain the absence of news, e.g.
#   "No specific intelligence for X was found ... from the provided articles."
# We drop these so the newsletter only contains real signals. Patterns are case-insensitive.
_PLACEHOLDER_PATTERNS = (
    r"\bno (?:specific|material|direct|meaningful|relevant) (?:intelligence|information|signals?|news|updates?|movements?)\b",
    r"\bno (?:intelligence|information|signals?|movements?|updates?)\s+(?:was|were|found|available)\b",
    r"\bnot found in source reviewed\b",
    r"\b(?:was|were)\s+not\s+(?:available|found|reported|identified)\b",
    r"\bfrom the provided articles?\b",
    r"\bin the provided articles?\b",
    r"\bbased on the provided evidence\b",
    r"\bevidence provided was\b",
    r"\bno (?:source|sources) reviewed\b",
)
_PLACEHOLDER_RE = __import__("re").compile("|".join(_PLACEHOLDER_PATTERNS), __import__("re").IGNORECASE)


def _looks_like_placeholder(text: Any) -> bool:
    s = str(text or "").strip()
    if not s:
        return True
    if _PLACEHOLDER_RE.search(s):
        return True
    return False


def _clamp_newsapi_from(start_value: Any) -> str:
    """Clamp a YYYY-MM-DD start date to NewsAPI's free-tier 30-day lookback.

    NewsAPI free tier rejects from-dates older than ~30 days. This helper preserves
    the user-requested window for every other source (Screener, RSS, policy feeds)
    while letting NewsAPI succeed.
    """
    s = start_value.isoformat() if hasattr(start_value, "isoformat") else str(start_value)
    try:
        requested = date.fromisoformat(s)
    except (ValueError, TypeError):
        return s
    floor = date.today() - timedelta(days=29)
    if requested < floor:
        logging.getLogger(__name__).warning(
            "NewsAPI free-tier 30-day lookback: clamping from_date from %s to %s "
            "(Screener / RSS / policy feeds still use the full requested window).",
            s, floor.isoformat(),
        )
        return floor.isoformat()
    return s

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger(__name__)

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate weekly digest for configured competitors")
    parser.add_argument("--week-start", required=True, help="Start date YYYY-MM-DD")
    parser.add_argument("--week-end", required=True, help="End date YYYY-MM-DD")
    parser.add_argument("--out", required=True)
    parser.add_argument("--export-json")
    parser.add_argument(
        "--render-only",
        action="store_true",
        help=("Skip all ingestion, classification, and LLM composition. Load the most "
              "recent cached newsletter JSON for the given week from output/article_pipeline.db "
              "and re-render the PDF only. Useful when iterating on the renderer or styles."),
    )
    return parser.parse_args()


def _render_only_from_cache(week_start: str, week_end: str, out: str) -> None:
    """Read the latest cached newsletter_composer_output for the given week from SQLite
    and re-render the PDF only. Zero network calls, zero LLM calls.
    """
    import sqlite3
    from config import get_settings as _get_settings

    settings = _get_settings()
    db_path = settings.sqlite_db_path
    if not Path(db_path).exists():
        raise SystemExit(
            f"[render-only] No SQLite store at {db_path}. Run the full pipeline at least "
            "once for this week range before using --render-only."
        )

    cache_url = f"internal://newsletter-composer-output/{week_start}-{week_end}"
    conn = sqlite3.connect(db_path)
    row = conn.execute(
        """
        SELECT p.payload_json
        FROM article_pipeline p JOIN articles a USING (article_id)
        WHERE p.stage = 'newsletter_composer_output' AND a.url = ?
        ORDER BY p.created_at DESC LIMIT 1
        """,
        (cache_url,),
    ).fetchone()
    conn.close()

    if not row:
        raise SystemExit(
            f"[render-only] No cached newsletter_composer_output for {week_start} to {week_end}. "
            "Run the full pipeline at least once for this week range to populate the cache."
        )

    payload = json.loads(row[0])
    newsletter_raw = payload.get("parsed_json") if isinstance(payload, dict) else None
    if not isinstance(newsletter_raw, dict):
        raise SystemExit(
            f"[render-only] Cached payload for {week_start}-{week_end} is not a newsletter dict. "
            "Re-run the full pipeline to refresh the cache."
        )

    # Always inject cover.date_range from the CLI args so the page-decor renders the
    # actual reporting period. Older cached newsletters (composed before we started
    # writing the cover section) lack this key and otherwise default to
    # "Reporting period not specified" — which then appears twice (once on each side
    # of " to ") on every page header.
    cover = newsletter_raw.get("cover")
    if not isinstance(cover, dict):
        cover = {}
    cover["date_range"] = f"{week_start} to {week_end}"
    cover.setdefault("title", "Housing Finance Weekly Digest")
    newsletter_raw["cover"] = cover

    agentic_pdf = str(Path(out).with_name(f"{Path(out).stem}_agentic_analysis.pdf"))
    Path(agentic_pdf).parent.mkdir(parents=True, exist_ok=True)
    logger.info("[render-only] Loaded cached newsletter for %s..%s; rendering %s",
                week_start, week_end, agentic_pdf)
    render_newsletter_pdf(newsletter=newsletter_raw, out_path=agentic_pdf)
    logger.info("[render-only] Wrote %s", agentic_pdf)


def _parse_week_range(week_start: str | None, week_end: str | None) -> Tuple[str, str]:
    end = date.fromisoformat(week_end) if week_end else date.today()
    start = date.fromisoformat(week_start) if week_start else (end - timedelta(days=6))
    if start > end:
        raise ValueError("week-start must be <= week-end")
    return start.isoformat(), end.isoformat()


def _save_json(path: str | None, payload: dict) -> None:
    if not path:
        return
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def _merge_articles(primary, supplemental):
    seen = {a.url for a in primary if getattr(a, "url", "")}
    out = list(primary)
    for a in supplemental:
        if getattr(a, "url", "") and a.url not in seen:
            out.append(a)
            seen.add(a.url)
    return out


def _filter_context_for_company(policy_articles, company: str, aliases: List[str]):
    tokens = [company.lower(), *(a.lower() for a in aliases)]
    selected = []
    for article in policy_articles:
        text = f"{getattr(article, 'title', '')} {getattr(article, 'description', '')} {getattr(article, 'content', '')}".lower()
        if any(t and t in text for t in tokens):
            selected.append(article)
    return selected


def _build_regulatory_updates(policy_rating_articles, max_items: int = 10) -> List[dict]:
    allowed_sources = {"RBI", "NHB", "CRISIL", "ICRA"}

    def _cap_text(value: str, max_len: int) -> str:
        cleaned = re.sub(r"\s+", " ", str(value or "")).strip()
        if not cleaned:
            return ""
        if len(cleaned) <= max_len:
            return cleaned
        return cleaned[: max_len - 1].rstrip() + "…"

    def _first_sentence(value: str, max_len: int) -> str:
        cleaned = re.sub(r"\s+", " ", str(value or "")).strip()
        if not cleaned:
            return ""
        parts = re.split(r"(?<=[.!?])\s+", cleaned, maxsplit=1)
        return _cap_text(parts[0], max_len)

    def _derive_signal(text: str) -> str:
        lowered = (text or "").lower()
        if any(token in lowered for token in ["repo", "liquidity", "cash reserve", "slr", "lcr"]):
            return "Potential liquidity implication for lenders/NBFCs"
        if any(token in lowered for token in ["rate", "borrowing", "yield", "funding", "refinance", "cost of funds"]):
            return "Potential funding-cost implication for HFCs"
        if any(token in lowered for token in ["compliance", "disclosure", "governance", "norm", "guideline", "penalty"]):
            return "Potential compliance/process impact"
        if any(token in lowered for token in ["downgrade", "upgrade", "outlook", "rating", "watch"]):
            return "Potential external-rating/sentiment implication"
        return "Potential operating-environment implication"

    updates: List[dict] = []
    seen_urls = set()
    for article in policy_rating_articles:
        source = str(getattr(article, "source", "") or "").upper().strip()
        if source not in allowed_sources:
            continue
        canonical_url = str(getattr(article, "url", "") or "").strip()
        original_url = str(getattr(article, "original_url", "") or "").strip()
        if not canonical_url and original_url:
            canonical_url = original_url
        if not canonical_url or canonical_url in seen_urls:
            continue
        seen_urls.add(canonical_url)

        date_text = _cap_text(str(getattr(article, "published_at", "") or "").strip() or "Unknown date", 32)
        title_text = _cap_text(str(getattr(article, "title", "") or "").strip() or "Not found in source reviewed", 120)
        description_text = _cap_text(str(getattr(article, "description", "") or "").strip(), 220)
        content_text = _cap_text(str(getattr(article, "content", "") or "").strip(), 320)

        summary_text = _first_sentence(description_text, 160) or _first_sentence(content_text, 160)
        if not summary_text:
            summary_text = _first_sentence(title_text, 160) or "Not found in source reviewed"

        impact_signal = _cap_text(_derive_signal(f"{title_text} {description_text} {content_text}"), 90)
        one_liner = _cap_text(f"{source} ({date_text}): {title_text}", 170)

        updates.append(
            {
                "line": one_liner,
                "summary": summary_text,
                "signal": impact_signal,
                "url": canonical_url,
                "canonical_url": canonical_url,
                "original_url": original_url or canonical_url,
                "is_aggregator": bool(getattr(article, "is_aggregator", False)),
                "source": source,
                "date": date_text,
                "title": title_text,
            }
        )
        if len(updates) >= max_items:
            break
    return updates


def _post_process_regulatory_updates(
    *,
    raw_regulatory_updates: Any,
    policy_rating_articles,
    regulatory_evidence_count: int,
    regulatory_evidence_threshold: int,
    llm: GeminiSearchClient,
    store: ArticleStore,
    settings,
    start: str,
    end: str,
) -> List[dict]:
    required_fields = ("line", "summary", "signal", "source", "date", "url")

    def _normalize_row(item: Any) -> dict:
        if isinstance(item, str):
            item = {"line": item}
        row = item if isinstance(item, dict) else {}
        return {
            "line": str(row.get("line", "")).strip(),
            "summary": str(row.get("summary", "")).strip(),
            "signal": str(row.get("signal", "")).strip(),
            "source": str(row.get("source", "")).strip(),
            "date": str(row.get("date", "")).strip(),
            "url": str(row.get("url", "")).strip(),
            "title": str(row.get("title", "")).strip(),
            "confidence": str(row.get("confidence", "")).strip(),
        }

    def _complete_count(rows: List[dict]) -> int:
        return sum(1 for row in rows if all(str(row.get(field, "")).strip() for field in required_fields))

    def _has_placeholder_only(rows: List[dict]) -> bool:
        if not rows:
            return True
        return all(
            str(row.get("line", "")).strip().lower() in {"", "not found in source reviewed"}
            for row in rows
        )

    min_rows_required = 3 if regulatory_evidence_count >= regulatory_evidence_threshold else 1

    normalized = [_normalize_row(item) for item in (raw_regulatory_updates if isinstance(raw_regulatory_updates, list) else [])]
    normalized = [row for row in normalized if row.get("line")]
    complete_rows = _complete_count(normalized)
    weak_quality = complete_rows < min_rows_required

    if weak_quality and regulatory_evidence_count > 0:
        evidence_seed_rows = _build_regulatory_updates(policy_rating_articles, max_items=8)
        constrained_prompt = (
            f"Repair ONLY regulatory_updates for housing finance between {start} and {end}. "
            "Return ONLY valid JSON with schema {\"regulatory_updates\": ["
            "{\"line\":\"...\",\"summary\":\"...\",\"signal\":\"...\",\"source\":\"...\",\"date\":\"YYYY-MM-DD\",\"url\":\"https://...\"}]}. "
            "Each row must include all required fields: line, summary, signal, source, date, url. "
            f"Return at least {min_rows_required} complete rows when evidence supports it. "
            "Use only evidence below; do not invent facts.\n\n"
            f"Evidence rows:\n{json.dumps(evidence_seed_rows, ensure_ascii=False)[:22000]}\n\n"
            f"Current weak rows:\n{json.dumps(normalized, ensure_ascii=False)[:12000]}"
        )
        _record_stage_with_llm_version(
            store=store,
            settings=settings,
            url=f"internal://agentic-regulatory-postprocess-retry-prompt/{start}-{end}",
            stage="agentic_regulatory_postprocess_retry_prompt",
            prompt=constrained_prompt,
        )
        retried = llm.run_json_prompt(constrained_prompt, retries=1)
        _record_stage_with_llm_version(
            store=store,
            settings=settings,
            url=f"internal://agentic-regulatory-postprocess-retry-output/{start}-{end}",
            stage="agentic_regulatory_postprocess_retry_output",
            parsed_json=retried,
        )
        retried_rows = [_normalize_row(item) for item in (retried.get("regulatory_updates", []) if isinstance(retried, dict) else [])]
        retried_rows = [row for row in retried_rows if row.get("line")]
        if _complete_count(retried_rows) >= complete_rows:
            normalized = retried_rows
            complete_rows = _complete_count(normalized)

    if complete_rows < min_rows_required:
        sparse_evidence = regulatory_evidence_count < regulatory_evidence_threshold
        if sparse_evidence:
            fallback_rows = _build_regulatory_updates(policy_rating_articles, max_items=max(min_rows_required, 3))
            normalized = [_normalize_row(item) for item in fallback_rows]
            for row in normalized:
                row["confidence"] = "low"
        elif not normalized:
            normalized = []

    normalized = [row for row in normalized if row.get("line")]
    if not normalized or _has_placeholder_only(normalized):
        normalized = [
            {
                "line": "Not found in source reviewed",
                "summary": "Not found in source reviewed",
                "signal": "Potential operating-environment implication",
                "url": "",
                "source": "",
                "date": "",
                "title": "Not found in source reviewed",
                "confidence": "low",
            }
        ]

    for row in normalized:
        for field in required_fields:
            if not str(row.get(field, "")).strip():
                if field == "summary":
                    row[field] = "Not found in source reviewed"
                elif field == "signal":
                    row[field] = "Potential operating-environment implication"
                else:
                    row[field] = ""
    return normalized


def _build_llm_client(settings) -> GeminiSearchClient:
    from services.gemini_search import _ProviderConfig

    def _cfg_for(p: str) -> "_ProviderConfig":
        if p == "openai":
            return _ProviderConfig(
                provider="openai", model=settings.openai_model, api_key=settings.openai_api_key,
                ollama_base_url=settings.ollama_base_url,
            )
        if p == "gemini":
            return _ProviderConfig(
                provider="gemini", model=settings.gemini_model, api_key=settings.gemini_api_key,
                ollama_base_url=settings.ollama_base_url,
            )
        if p == "ollama":
            return _ProviderConfig(
                provider="ollama", model=settings.ollama_model, api_key="",
                ollama_base_url=settings.ollama_base_url,
            )
        raise ValueError(f"Unsupported provider: {p}")

    primary = _cfg_for(settings.llm_provider)
    fallbacks = [_cfg_for(p) for p in settings.llm_fallback_providers if p != settings.llm_provider]

    timeout_seconds = 240 if "ollama" in (settings.llm_provider, *settings.llm_fallback_providers) else 60
    logger.info(
        "Initializing LLM client primary=%s/%s fallbacks=%s timeout_seconds=%s",
        primary.provider, primary.model,
        [f"{f.provider}/{f.model}" for f in fallbacks] or "(none)",
        timeout_seconds,
    )
    return GeminiSearchClient(
        api_key=primary.api_key,
        model=primary.model,
        timeout_seconds=timeout_seconds,
        provider=primary.provider,
        ollama_base_url=primary.ollama_base_url,
        fallbacks=fallbacks,
    )


def _record_stage_with_llm_version(
    *,
    store: ArticleStore,
    settings,
    url: str,
    stage: str,
    prompt: str | None = None,
    parsed_json: dict[str, Any] | list[Any] | None = None,
) -> None:
    model_name = settings.gemini_model if settings.llm_provider == "gemini" else settings.ollama_model
    store.record_stage(
        url=url,
        stage=stage,
        provider=settings.llm_provider,
        prompt=prompt,
        parsed_json=parsed_json,
        model=model_name,
        system_instruction_version=settings.llm_system_instruction_version,
    )


def _enrich_regulatory_url_fields(rows: List[dict]) -> List[dict]:
    enriched: List[dict] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        existing_canonical = str(row.get("canonical_url", "")).strip()
        existing_original = str(row.get("original_url", "")).strip()
        candidate_url = existing_canonical or str(row.get("url", "")).strip() or existing_original
        resolved = resolve_article_url(candidate_url) if candidate_url else resolve_article_url("")
        canonical_url = existing_canonical or resolved.canonical_url
        original_url = existing_original or resolved.original_url
        is_aggregator = bool(row.get("is_aggregator", False)) or resolved.is_aggregator
        normalized = dict(row)
        normalized["canonical_url"] = canonical_url
        normalized["original_url"] = original_url
        normalized["is_aggregator"] = is_aggregator
        normalized["url"] = canonical_url or original_url
        enriched.append(normalized)
    return enriched



def _build_section_repair_prompt(
    *,
    section: str,
    original_prompt: str,
    invalid_output: Dict[str, Any],
    errors: List[str],
) -> str:
    return (
        f"{original_prompt}\n\n"
        f"VALIDATION FAILED for section '{section}'. Repair ONLY this section output.\n"
        "Do not add new sections. Keep the same schema keys and return ONLY valid JSON.\n"
        "Fix all listed issues exactly:\n"
        + "\n".join(f"- {err}" for err in errors)
        + "\nPrevious invalid JSON:\n"
        + json.dumps(invalid_output, ensure_ascii=False)[:20000]
    )


def _run_validated_section_prompt(
    *,
    llm: GeminiSearchClient,
    store: ArticleStore,
    settings,
    start: str,
    end: str,
    section: str,
    stage_prefix: str,
    prompt: str,
    validator: Callable[[Dict[str, Any]], ValidationResult],
) -> Dict[str, Any]:
    prompt_url = f"internal://agentic-{section}-prompt/{start}-{end}"
    output_url = f"internal://agentic-{section}-output/{start}-{end}"
    validation_stage = f"agentic_{section}_validation"

    _record_stage_with_llm_version(
        store=store,
        settings=settings,
        url=prompt_url,
        stage=f"{stage_prefix}_prompt",
        prompt=prompt,
    )
    raw = llm.run_json_prompt(prompt)
    _record_stage_with_llm_version(
        store=store,
        settings=settings,
        url=output_url,
        stage=f"{stage_prefix}_output",
        parsed_json=raw,
    )

    validation = validator(raw if isinstance(raw, dict) else {})
    metrics: Dict[str, Any] = {
        "attempt": 1,
        "retry_triggered": False,
        "passed": validation.is_valid,
        "errors": validation.errors,
        **validation.metrics,
    }

    if not validation.is_valid:
        metrics["retry_triggered"] = True
        repair_prompt = _build_section_repair_prompt(
            section=section,
            original_prompt=prompt,
            invalid_output=raw if isinstance(raw, dict) else {},
            errors=validation.errors,
        )
        _record_stage_with_llm_version(
            store=store,
            settings=settings,
            url=f"internal://agentic-{section}-repair-prompt/{start}-{end}",
            stage=f"{stage_prefix}_repair_prompt",
            prompt=repair_prompt,
        )
        repaired = llm.run_json_prompt(repair_prompt, retries=1)
        _record_stage_with_llm_version(
            store=store,
            settings=settings,
            url=f"internal://agentic-{section}-repair-output/{start}-{end}",
            stage=f"{stage_prefix}_repair_output",
            parsed_json=repaired,
        )
        retry_validation = validator(repaired if isinstance(repaired, dict) else {})
        metrics.update(
            {
                "attempt": 2,
                "passed": retry_validation.is_valid,
                "errors": retry_validation.errors,
                "first_pass_errors": validation.errors,
                "final_metrics": retry_validation.metrics,
            }
        )
        raw = repaired if isinstance(repaired, dict) else {}

    store.record_validation_metrics(
        scope=f"{section}/{start}-{end}",
        stage=validation_stage,
        metrics=metrics,
    )
    return raw if isinstance(raw, dict) else {}

def _run_weekly_digest_agentic_analysis(
    *,
    settings,
    store: ArticleStore,
    article_map,
    curated_evidence_by_company: Dict[str, dict] | None,
    policy_rating_articles,
    fallback_company_sections: List[WeeklyCompanySection],
    start: str,
    end: str,
    out: str,
    export_json: str | None,
) -> None:
    llm = _build_llm_client(settings)
    news_client = NewsAPIClient(api_key=settings.news_api_key)
    allowed_competitors = list(article_map.keys())
    allowed_competitor_tokens = {str(name or "").strip().lower() for name in allowed_competitors if str(name or "").strip()}
    industry_article_urls = set()
    regulatory_sources = {"rbi", "nhb", "crisil", "icra"}
    regulatory_keywords = {
        "policy",
        "regulation",
        "regulatory",
        "guideline",
        "circular",
        "notification",
        "master direction",
        "rating",
        "outlook",
        "downgrade",
        "upgrade",
        "watch",
        "rbi",
        "nhb",
        "crisil",
        "icra",
    }
    macro_keywords = {
        "housing finance",
        "mortgage",
        "home loan",
        "interest rate",
        "repo",
        "inflation",
        "liquidity",
        "affordable housing",
        "real estate",
        "property market",
        "credit growth",
    }

    def _to_text(value) -> str:
        return str(value or "").strip().lower()

    def _is_competitor_item(*, article=None, summary=None) -> bool:
        company = _to_text(getattr(article, "company", ""))
        if isinstance(summary, dict):
            company = _to_text(summary.get("company")) or company
        if company in allowed_competitor_tokens:
            return True

        title = _to_text(getattr(article, "title", ""))
        desc = _to_text(getattr(article, "description", ""))
        if isinstance(summary, dict):
            title = f"{title} {_to_text(summary.get('article_title'))}".strip()
            desc = f"{desc} {_to_text(summary.get('weekly_summary'))}".strip()
        combined = f"{company} {title} {desc}"
        return any(token and token in combined for token in allowed_competitor_tokens)

    def _is_regulatory_item(*, article=None, summary=None) -> bool:
        source = _to_text(getattr(article, "source", ""))
        if source in regulatory_sources:
            return True
        title = _to_text(getattr(article, "title", ""))
        desc = _to_text(getattr(article, "description", ""))
        content = _to_text(getattr(article, "content", ""))
        signal_text = ""
        if isinstance(summary, dict):
            title = f"{title} {_to_text(summary.get('article_title'))}".strip()
            desc = f"{desc} {_to_text(summary.get('weekly_summary'))}".strip()
            signal_types = summary.get("signal_types", [])
            if isinstance(signal_types, list):
                signal_text = " ".join(_to_text(x) for x in signal_types)
        combined = f"{source} {title} {desc} {content} {signal_text}"
        return any(keyword in combined for keyword in regulatory_keywords)

    def _is_industry_item(*, article=None, summary=None) -> bool:
        url = _to_text(getattr(article, "url", ""))
        if url and url in industry_article_urls:
            return True

        company = _to_text(getattr(article, "company", ""))
        title = _to_text(getattr(article, "title", ""))
        desc = _to_text(getattr(article, "description", ""))
        if isinstance(summary, dict):
            company = _to_text(summary.get("company")) or company
            title = f"{title} {_to_text(summary.get('article_title'))}".strip()
            desc = f"{desc} {_to_text(summary.get('weekly_summary'))}".strip()

        industry_tagged = company in {"industry", "housing finance industry"}
        macro_relevant = any(keyword in f"{title} {desc}" for keyword in macro_keywords)
        return industry_tagged or macro_relevant

    industry_keywords = [
        "India housing finance",
        "India home loan",
        "RBI housing finance",
        "NHB India regulation",
        "Indian mortgage market",
        "affordable housing India",
        "PMAY housing",
    ]
    industry_articles = news_client.fetch_company_articles(
        company="Indian Housing Finance Industry",
        aliases=industry_keywords,
        from_date=_clamp_newsapi_from(start),
        to_date=end,
        max_articles=25,
    )
    # Drop articles that are clearly non-Indian (foreign markets / foreign issuers
    # sometimes slip through NewsAPI's keyword match).
    industry_articles = _filter_india_only(industry_articles)
    store.upsert_articles(industry_articles)
    industry_article_urls = {str(getattr(article, "url", "") or "").strip().lower() for article in industry_articles if getattr(article, "url", "")}

    all_articles = []
    seen_urls = set()
    for company_articles in article_map.values():
        for article in company_articles:
            if article.url in seen_urls:
                continue
            seen_urls.add(article.url)
            all_articles.append(article)
    for article in industry_articles:
        if article.url in seen_urls:
            continue
        seen_urls.add(article.url)
        all_articles.append(article)
    for article in policy_rating_articles:
        if article.url in seen_urls:
            continue
        seen_urls.add(article.url)
        all_articles.append(article)

    curated_url_metadata: Dict[str, Dict[str, Any]] = {}
    curated_articles = []
    curated_seen_urls = set()
    if settings.llm_provider == "gemini" and curated_evidence_by_company:
        for company, payload in curated_evidence_by_company.items():
            selected_items = payload.get("selected_articles", []) if isinstance(payload, dict) else []
            for item in selected_items:
                if not isinstance(item, dict):
                    continue
                url = str(item.get("url", "")).strip()
                if not url:
                    continue
                curated_url_metadata[url] = item
                if url in curated_seen_urls:
                    continue
                article_match = next((article for article in all_articles if article.url == url), None)
                if article_match:
                    curated_articles.append(article_match)
                    curated_seen_urls.add(url)
                else:
                    logger.debug("Curated evidence url not found in merged article pools for %s: %s", company, url)

    for article in all_articles[:160]:
        normalized_payload = {
            "url": article.url,
            "company": getattr(article, "company", ""),
            "title": getattr(article, "title", ""),
            "source": getattr(article, "source", ""),
            "published_at": getattr(article, "published_at", ""),
            "snippet": (getattr(article, "description", "") or "")[:450],
        }
        _record_stage_with_llm_version(
            store=store,
            settings=settings,
            url=article.url,
            stage="article_normalized",
            parsed_json=normalized_payload,
        )

    micro_summaries = []
    if settings.llm_provider == "ollama":
        for article in all_articles[:120]:
            article_block = articles_to_prompt_block([article], limit=1)
            try:
                one_article_prompt = article_signal_summary_prompt(
                    article_block=article_block,
                    competitor_list=allowed_competitors,
                )
                article_summary = llm.run_json_prompt(one_article_prompt)
                if isinstance(article_summary, dict):
                    micro_summaries.append(article_summary)
                    _record_stage_with_llm_version(
                        store=store,
                        settings=settings,
                        url=article.url,
                        stage="article_micro_extraction",
                        prompt=one_article_prompt,
                        parsed_json=article_summary,
                    )
            except Exception as exc:  # noqa: BLE001
                logger.warning("Ollama micro-summary failed for article '%s': %s", getattr(article, "title", "unknown"), exc)
                continue

    else:
        evidence_articles = list(all_articles)
        if curated_articles:
            evidence_articles = list(curated_articles)
            for article in industry_articles:
                if article.url not in curated_seen_urls:
                    evidence_articles.append(article)
            for article in policy_rating_articles:
                if article.url not in curated_seen_urls:
                    evidence_articles.append(article)

        for article in evidence_articles[:120]:
            curated_meta = curated_url_metadata.get(article.url, {})
            scores = {
                "reliability_score": float(curated_meta.get("reliability_score", getattr(article, "reliability_score", 0.0))),
                "relevance_score": float(curated_meta.get("relevance_score", getattr(article, "relevance_score", 0.0))),
                "novelty_score": float(curated_meta.get("novelty_score", getattr(article, "novelty_score", 0.0))),
                "materiality_score": float(curated_meta.get("materiality_score", getattr(article, "materiality_score", 0.0))),
                "final_score": float(curated_meta.get("final_score", getattr(article, "final_score", 0.0))),
            }
            normalized_summary = {
                "company": curated_meta.get("company") or getattr(article, "company", "") or "Industry",
                "article_title": getattr(article, "title", ""),
                "weekly_summary": (getattr(article, "description", "") or getattr(article, "title", ""))[:280],
                "signal_types": ["macro"],
                "confidence": "medium" if curated_meta else "low",
                "url": article.url,
                "source": getattr(article, "source", ""),
                "published_at": getattr(article, "published_at", ""),
                "selection_metadata": {
                    "is_curated_competitor_evidence": bool(curated_meta),
                    "query_bucket": getattr(article, "query_bucket", ""),
                    "domain": getattr(article, "domain", ""),
                    "cluster_size": int(getattr(article, "cluster_size", 1)),
                    "theme_cluster": getattr(article, "theme_cluster", "misc"),
                },
                "scores": scores,
            }
            micro_summaries.append(normalized_summary)
            _record_stage_with_llm_version(
                store=store,
                settings=settings,
                url=article.url,
                stage="article_micro_extraction",
                parsed_json=normalized_summary,
            )

    evidence_block = (
        json.dumps(micro_summaries, ensure_ascii=False)
        if micro_summaries
        else articles_to_prompt_block(all_articles, limit=160)
    )
    regulatory_source_block = json.dumps(
        [
            item
            for item in micro_summaries
            if isinstance(item, dict)
            and (
                str(item.get("section_tag", "")).strip().lower() == "regulatory"
                or _is_regulatory_item(summary=item)
            )
        ],
        ensure_ascii=False,
    )
    if regulatory_source_block in {"[]", ""}:
        regulatory_source_block = articles_to_prompt_block(policy_rating_articles, limit=60)
    regulatory_tagged_micro_items = [
        item
        for item in micro_summaries
        if isinstance(item, dict) and str(item.get("section_tag", "")).strip().lower() == "regulatory"
    ]
    regulatory_evidence_threshold = 3
    regulatory_evidence_count = len(policy_rating_articles) + len(regulatory_tagged_micro_items)
    if regulatory_evidence_count < regulatory_evidence_threshold:
        logger.warning(
            "Regulatory evidence below threshold for %s to %s: count=%s threshold=%s",
            start,
            end,
            regulatory_evidence_count,
            regulatory_evidence_threshold,
        )

    industry_prompt = weekly_digest_agentic_industry_prompt(start, end, evidence_block)
    industry_raw = _run_validated_section_prompt(
        llm=llm,
        store=store,
        settings=settings,
        start=start,
        end=end,
        section="industry",
        stage_prefix="agentic_industry",
        prompt=industry_prompt,
        validator=validate_industry_section,
    )

    regulatory_prompt = weekly_digest_agentic_regulatory_prompt(start, end, regulatory_source_block)
    regulatory_raw = _run_validated_section_prompt(
        llm=llm,
        store=store,
        settings=settings,
        start=start,
        end=end,
        section="regulatory",
        stage_prefix="agentic_regulatory",
        prompt=regulatory_prompt,
        validator=validate_regulatory_section,
    )

    competitor_prompt = weekly_digest_agentic_competitor_prompt(start, end, allowed_competitors, evidence_block)
    competitor_raw = _run_validated_section_prompt(
        llm=llm,
        store=store,
        settings=settings,
        start=start,
        end=end,
        section="competitor",
        stage_prefix="agentic_competitor",
        prompt=competitor_prompt,
        validator=lambda payload: validate_competitor_section(
            payload,
            allowed_signal_types=sorted(ALLOWED_COMPETITOR_SIGNAL_TYPES),
        ),
    )

    final_prompt = weekly_digest_agentic_final_synthesis_prompt(
        week_start=start,
        week_end=end,
        competitor_list=allowed_competitors,
        industry_json=json.dumps(industry_raw, ensure_ascii=False),
        regulatory_json=json.dumps(regulatory_raw, ensure_ascii=False),
        competitor_json=json.dumps(competitor_raw, ensure_ascii=False),
    )
    _record_stage_with_llm_version(
        store=store,
        settings=settings,
        url=f"internal://agentic-final-prompt/{start}-{end}",
        stage="agentic_final_prompt",
        prompt=final_prompt,
    )
    raw = llm.run_json_prompt(final_prompt)
    _record_stage_with_llm_version(
        store=store,
        settings=settings,
        url=f"internal://agentic-final-output/{start}-{end}",
        stage="agentic_final_output",
        parsed_json=raw,
    )

    title = str(raw.get("title", f"Weekly Housing Finance Industry Agentic Analysis ({start} to {end})"))
    time_period = str(raw.get("time_period", f"{start} to {end}"))
    industry_summary = [str(x) for x in raw.get("industry_summary", []) if str(x).strip()]
    if not industry_summary:
        fallback_lines = []
        fallback_lines.extend(
            [
                f"{a.source}: {a.title}"
                for a in policy_rating_articles
                if (a.source or "").upper() in {"RBI", "NHB", "CRISIL", "ICRA"}
            ][:6]
        )
        for section in fallback_company_sections:
            if (section.executive_summary or "").lower().startswith("- no qualifying coverage"):
                continue
            lines = [ln.strip("- ").strip() for ln in (section.executive_summary or "").split("\n") if ln.strip()]
            if lines:
                fallback_lines.append(f"{section.company}: {lines[0]}")
        # de-duplicate while preserving order
        seen = set()
        industry_summary = []
        for line in fallback_lines:
            key = line.lower().strip()
            if not key or key in seen:
                continue
            seen.add(key)
            industry_summary.append(line)
        industry_summary = industry_summary[:8]
    competitor_rows_raw = [row for row in raw.get("competitor_table", []) if isinstance(row, dict)]
    competitor_rows: List[dict] = []
    canonical_lookup = {name.lower(): name for name in allowed_competitors}
    used_companies = set()
    for row in competitor_rows_raw:
        source_name = str(row.get("company", "")).strip()
        if not source_name:
            continue
        canonical_name = canonical_lookup.get(source_name.lower())
        if not canonical_name:
            matches = difflib.get_close_matches(source_name.lower(), list(canonical_lookup.keys()), n=1, cutoff=0.72)
            canonical_name = canonical_lookup.get(matches[0]) if matches else None
        if not canonical_name or canonical_name in used_companies:
            continue
        competitor_rows.append(
            {
                "company": canonical_name,
                "weekly_summary": str(row.get("weekly_summary", "Not found in source reviewed")),
                "signal_types": row.get("signal_types", []),
            }
        )
        used_companies.add(canonical_name)
    competitor_rows = [
        row
        for row in competitor_rows
        if str(row.get("weekly_summary", "")).strip().lower() not in {"", "not found in source reviewed"}
    ]
    # Defensive post-filter: drop "no-news" placeholder rows the LLM sometimes emits
    # despite the prompt ban. These typically read "No specific intelligence was found...",
    # "Evidence provided was...", "from the provided articles", etc.
    competitor_rows = [
        row for row in competitor_rows if not _looks_like_placeholder(row.get("weekly_summary", ""))
    ]
    raw_regulatory_updates = raw.get("regulatory_updates", [])
    normalized_regulatory_updates: List[dict] = []
    auto_converted_regulatory_string_rows = 0
    for item in raw_regulatory_updates:
        if isinstance(item, dict) and "line" in item:
            normalized_regulatory_updates.append(item)
        elif isinstance(item, str):
            normalized_regulatory_updates.append(
                {
                    "line": item,
                    "summary": "",
                    "signal": "",
                    "url": "",
                    "source": "",
                    "date": "",
                    "title": "",
                }
            )
            auto_converted_regulatory_string_rows += 1
    if auto_converted_regulatory_string_rows:
        logger.warning(
            "Auto-converted %s string row(s) in regulatory_updates to dict schema during agentic parsing.",
            auto_converted_regulatory_string_rows,
        )
    regulatory_updates = [
        row
        for row in normalized_regulatory_updates
        if str(row.get("line", "")).strip()
    ]
    regulatory_updates = _enrich_regulatory_url_fields(regulatory_updates)
    if not regulatory_updates:
        regulatory_updates = _build_regulatory_updates(policy_rating_articles)
    if not regulatory_updates:
        regulatory_updates = [
            {
                "line": "Not found in source reviewed",
                "summary": "Not found in source reviewed",
                "signal": "Potential operating-environment implication",
                "url": "",
                "canonical_url": "",
                "original_url": "",
                "is_aggregator": False,
                "source": "",
                "date": "",
                "title": "Not found in source reviewed",
            }
        ]
    if not competitor_rows:
        for section in fallback_company_sections:
            if (section.executive_summary or "").lower().startswith("- no qualifying coverage"):
                continue
            if not (section.references or section.top_signals or section.company_highlights):
                continue
            summary_lines = [ln.strip("- ").strip() for ln in (section.executive_summary or "").split("\n") if ln.strip()]
            summary = summary_lines[0] if summary_lines else "Not found in source reviewed"
            signal_types = [s.signal_type for s in section.top_signals[:3] if (s.signal_type or "").strip()] or ["news"]
            competitor_rows.append(
                {
                    "company": section.company,
                    "weekly_summary": summary,
                    "signal_types": signal_types,
                }
            )
    caveats = [
        str(x) for x in raw.get("caveats", [])
        if str(x).strip() and not _looks_like_placeholder(x)
    ]

    # === BSE corp-announcements -> Competitor Intelligence (all four buckets) ====
    # Pull each competitor's BSE filings via the AnnGetData API, filter STRICTLY to
    # [start, end], ask the LLM to classify each item into one of:
    #   Operational Signals (Hiring/Management Change/Rumour) |
    #   Funding & Capital | Risk & Governance | Growth & Strategy
    # The signal_types tag on each row steers the existing newsletter composer's
    # bucketing logic, so no composer/template changes are needed.
    try:
        bse_rows = collect_bse_signals(
            llm=llm,
            competitors=allowed_competitors,
            bse_codes=get_bse_security_codes(),
            start=start,
            end=end,
            store=store,  # writes every BSE filing + classification + enrichment + disposition
        )
        if bse_rows:
            logger.info(
                "BSE: adding %s row(s) to competitor_rows across the 4 CI buckets.",
                len(bse_rows),
            )
            competitor_rows.extend(bse_rows)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "BSE announcements pipeline failed; continuing without BSE signals: %s",
            exc,
        )

    newsletter_prompt, newsletter_raw = compose_newsletter(
        llm=llm,
        date_range=time_period or f"{start} to {end}",
        industry_summary=industry_summary,
        regulatory_updates=regulatory_updates,
        competitor_rows=competitor_rows,
    )
    _record_stage_with_llm_version(
        store=store,
        settings=settings,
        url=f"internal://newsletter-composer-prompt/{start}-{end}",
        stage="newsletter_composer_prompt",
        prompt=newsletter_prompt,
    )
    _record_stage_with_llm_version(
        store=store,
        settings=settings,
        url=f"internal://newsletter-composer-output/{start}-{end}",
        stage="newsletter_composer_output",
        parsed_json=newsletter_raw,
    )

    if not isinstance(newsletter_raw.get("cover"), dict):
        newsletter_raw["cover"] = {}
    newsletter_raw["cover"].setdefault("title", "Weekly Housing Finance Intelligence")
    newsletter_raw["cover"].setdefault("date_range", time_period or f"{start} to {end}")
    newsletter_raw["cover"].setdefault("tagline", "Regulatory, Industry and Competitive Insights")
    newsletter_raw.setdefault(
        "index",
        [
            "Industry Pulse",
            "Regulatory Watch",
            "Competitor Intelligence",
            "Key Takeaways",
        ],
    )
    newsletter_raw.setdefault("industry_pulse", {"summary_paragraph": "Not found in source reviewed", "highlights": []})
    newsletter_raw.setdefault("regulatory_watch", [])
    newsletter_raw.setdefault("competitor_intelligence", {"grouped_insights": []})
    newsletter_raw.setdefault("market_patterns", [])
    newsletter_raw.setdefault("key_takeaways", [])
    # Regression check: newsletter output is expected to include these keys.
    # Required keys: industry_pulse, regulatory_watch, competitor_intelligence, key_takeaways

    payload = {
        "title": title,
        "time_period": time_period,
        "industry_summary": industry_summary,
        "regulatory_updates": regulatory_updates,
        "competitor_table": competitor_rows,
        "caveats": caveats,
        "newsletter": newsletter_raw,
    }

    agentic_pdf = str(Path(out).with_name(f"{Path(out).stem}_agentic_analysis.pdf"))
    agentic_json = export_json and str(Path(export_json).with_name(f"{Path(export_json).stem}_agentic_analysis.json"))
    agentic_md = str(Path(agentic_pdf).with_suffix(".md"))

    md_lines = [f"# {title}", "", "## Time Period", f"- {time_period}", "", "## Industry Summary"]
    md_lines.extend([f"- {line}" for line in (industry_summary or ["Not found in source reviewed"])])
    md_lines.extend(["", "## Regulatory Updates (Policies / Ratings)"])
    if regulatory_updates:
        for item in regulatory_updates:
            line = str(item.get("line", "Not found in source reviewed")).strip()
            summary = str(item.get("summary", "Not found in source reviewed")).strip()
            signal = str(item.get("signal", "Not found in source reviewed")).strip()
            canonical_url = str(item.get("canonical_url", "")).strip() or str(item.get("url", "")).strip()
            original_url = str(item.get("original_url", "")).strip()
            if canonical_url:
                md_lines.append(f"- {line} ([source]({canonical_url}))")
            elif original_url:
                md_lines.append(f"- {line} ([aggregator source]({original_url}))")
            else:
                md_lines.append(f"- {line}")
            md_lines.append(f"  - Summary: {summary or 'Not found in source reviewed'}")
            md_lines.append(f"  - Signal: {signal or 'Not found in source reviewed'}")
            if canonical_url and original_url and canonical_url != original_url:
                md_lines.append(f"  - Original aggregator URL: {original_url}")
            if not canonical_url and original_url:
                md_lines.append("  - URL type: Aggregator (canonical publisher URL unavailable)")
    else:
        md_lines.append("- Not found in source reviewed")
    md_lines.extend(["", "## Competitor Summary Table", "", "| Competitor Name | This Week News Precise Summary | Signal Types |", "|---|---|---|"])
    if competitor_rows:
        for row in competitor_rows:
            md_lines.append(
                f"| {row.get('company', 'Unknown')} | {row.get('weekly_summary', 'Not found in source reviewed')} | "
                f"{', '.join(row.get('signal_types', [])) if isinstance(row.get('signal_types', []), list) else row.get('signal_types', '')} |"
            )
    else:
        md_lines.append("| No source-backed competitor updates found | Not found in source reviewed | Not found in source reviewed |")

    if caveats:
        md_lines.extend(["", "## Caveats", *[f"- {line}" for line in caveats]])

    Path(agentic_md).write_text("\n".join(md_lines), encoding="utf-8")
    render_newsletter_pdf(
        newsletter=newsletter_raw,
        out_path=agentic_pdf,
    )
    _save_json(agentic_json, payload)
    logger.info("Weekly digest agentic analysis generated: %s (markdown: %s)", agentic_pdf, agentic_md)

def run_weekly_digest(week_start: str, week_end: str, out: str, export_json: str | None) -> None:
    settings = get_settings()
    start, end = _parse_week_range(week_start, week_end)
    store = ArticleStore(settings.sqlite_db_path)

    competitors = get_competitors()
    news_client = NewsAPIClient(api_key=settings.news_api_key)
    llm = _build_llm_client(settings)
    policy_rating_articles = fetch_policy_and_rating_articles(week_start=start, week_end=end)
    store.upsert_articles(policy_rating_articles)

    article_map = collect_articles_for_competitors(news_client, competitor_map=competitors, from_date=_clamp_newsapi_from(start), to_date=end, per_company_limit=12)
    company_sections: List[WeeklyCompanySection] = []
    curated_evidence_by_company: Dict[str, dict] = {}

    for company, items in article_map.items():
        aliases = competitors.get(company, [company])
        context_articles = _filter_context_for_company(policy_rating_articles, company=company, aliases=aliases)
        supplemental = fetch_rss_articles(company=company, aliases=aliases, week_start=start, week_end=end)
        merged = _merge_articles(_merge_articles(items, supplemental), context_articles)
        store.upsert_articles(merged)
        annotate_base_scores(merged, company=company, aliases=aliases)
        for article in merged:
            store.record_by_url(
                article.url,
                stage="scoring",
                payload={
                    "company": company,
                    "reliability_score": float(getattr(article, "reliability_score", 0.0)),
                    "relevance_score": float(getattr(article, "relevance_score", 0.0)),
                    "novelty_score": float(getattr(article, "novelty_score", 0.0)),
                    "materiality_score": float(getattr(article, "materiality_score", 0.0)),
                },
            )

        checked_count = len(merged)
        semantic_clusters = deduplicate_semantic_articles(
            merged,
            reliability_getter=lambda article: float(getattr(article, "reliability_score", 0.0)),
            threshold=0.85,
        )
        deduped_articles = []
        for cluster in semantic_clusters:
            representative = cluster.representative
            representative.cluster_size = len(cluster.members)
            deduped_articles.append(representative)

        finalize_scores(deduped_articles)
        selected_articles = select_balanced_articles(deduped_articles, top_n=18, max_per_domain=5, max_per_theme=5)
        llm_article_cap = 18 if settings.llm_provider == "gemini" else 8
        llm_prompt_limit = 20 if settings.llm_provider == "gemini" else 8
        selected_for_llm = selected_articles[:llm_article_cap]
        curated_evidence_by_company[company] = {
            "selected_urls": [article.url for article in selected_articles if getattr(article, "url", "")],
            "selected_articles": [
                {
                    "company": company,
                    "url": article.url,
                    "original_url": getattr(article, "original_url", ""),
                    "is_aggregator": bool(getattr(article, "is_aggregator", False)),
                    "title": getattr(article, "title", ""),
                    "source": getattr(article, "source", ""),
                    "published_at": getattr(article, "published_at", ""),
                    "domain": getattr(article, "domain", ""),
                    "query_bucket": getattr(article, "query_bucket", ""),
                    "theme_cluster": getattr(article, "theme_cluster", "misc"),
                    "cluster_size": int(getattr(article, "cluster_size", 1)),
                    "reliability_score": float(getattr(article, "reliability_score", 0.0)),
                    "relevance_score": float(getattr(article, "relevance_score", 0.0)),
                    "novelty_score": float(getattr(article, "novelty_score", 0.0)),
                    "materiality_score": float(getattr(article, "materiality_score", 0.0)),
                    "final_score": float(getattr(article, "final_score", 0.0)),
                    "selected_for_llm": article in selected_for_llm,
                }
                for article in selected_articles
                if getattr(article, "url", "")
            ],
            "selection_summary": {
                "checked_articles": len(merged),
                "deduped_articles": len(deduped_articles),
                "selected_articles": len(selected_articles),
                "llm_articles": len(selected_for_llm),
            },
        }
        store.record_stage(
            url=f"internal://curated-evidence/{company}/{start}-{end}",
            stage="curated_evidence_selection",
            provider=settings.llm_provider,
            parsed_json=curated_evidence_by_company[company],
        )
        for article in selected_for_llm:
            store.record_by_url(
                article.url,
                stage="selected_for_llm",
                payload={"company": company, "final_score": float(getattr(article, "final_score", 0.0))},
            )
        prompt_block = articles_to_prompt_block(selected_for_llm, limit=llm_prompt_limit)
        selection_metrics = build_selection_metrics(
            checked_articles=merged,
            deduped_articles=deduped_articles,
            selected_articles=selected_articles,
        )

        if not selected_articles:
            company_sections.append(
                WeeklyCompanySection(
                    company=company,
                    executive_summary="- No qualifying coverage found in selected sources for this week.",
                    company_highlights=["No major developments identified"],
                    caveats=["Coverage gap for this company in current week window."],
                    references=[],
                    metrics={
                        "articles_checked": selection_metrics.articles_checked,
                        "articles_selected": selection_metrics.articles_selected,
                        "avg_reliability": selection_metrics.avg_reliability_selected,
                        "coverage_confidence": selection_metrics.coverage_confidence,
                    },
                )
            )
            logger.info("%s | checked=%s deduped=%s selected=0 signals=0 (coverage gap)", company, checked_count, len(deduped_articles))
            continue

        company_prompt = weekly_company_intelligence_prompt(company=company, week_start=start, week_end=end, articles_block=prompt_block)
        try:
            company_raw = llm.run_json_prompt(company_prompt)
        except RuntimeError as exc:
            if settings.llm_provider != "ollama":
                raise
            logger.warning("Ollama timed out on full company prompt for %s; retrying with condensed context: %s", company, exc)
            condensed_block = articles_to_prompt_block(selected_for_llm[:4], limit=4)
            condensed_prompt = weekly_company_intelligence_prompt(
                company=company,
                week_start=start,
                week_end=end,
                articles_block=condensed_block,
            )
            company_raw = llm.run_json_prompt(condensed_prompt, retries=1)
        company_bundle = parse_weekly_bundle(company_raw)
        for signal in company_bundle.top_signals:
            store.record_by_url(
                signal.source_url,
                stage="llm_signal",
                payload={
                    "company": company,
                    "signal_type": signal.signal_type,
                    "direction": signal.direction,
                    "impact": signal.impact,
                    "headline": signal.headline,
                },
            )
        references = company_bundle.references
        signals = company_bundle.top_signals
        highlights = company_bundle.company_highlights or ["No major developments identified"]
        executive_summary = company_bundle.executive_summary or "- No material signals detected for this period"
        if not signals:
            executive_summary = f"{executive_summary}\n- No material signals detected for this period"

        section = WeeklyCompanySection(
            company=company,
            executive_summary=executive_summary,
            top_signals=signals[:8],
            company_highlights=highlights[:6],
            sector_macro_context=(company_bundle.sector_macro_context or [])[:4],
            caveats=(company_bundle.caveats or [])[:4],
            references=references[:20],
            metrics={
                "articles_checked": selection_metrics.articles_checked,
                "articles_selected": selection_metrics.articles_selected,
                "avg_reliability": selection_metrics.avg_reliability_selected,
                "coverage_confidence": selection_metrics.coverage_confidence,
            },
        )
        company_sections.append(section)
        logger.info(
            "%s | checked=%s deduped=%s selected=%s signals=%s refs=%s",
            company,
            selection_metrics.articles_checked,
            selection_metrics.articles_after_dedup,
            selection_metrics.articles_selected,
            len(section.top_signals),
            len(section.references),
        )

    logger.info("Skipping base weekly digest exports; generating only *_agentic_analysis outputs.")
    _run_weekly_digest_agentic_analysis(
        settings=settings,
        store=store,
        article_map=article_map,
        curated_evidence_by_company=curated_evidence_by_company,
        policy_rating_articles=policy_rating_articles,
        fallback_company_sections=company_sections,
        start=start,
        end=end,
        out=out,
        export_json=export_json,
    )


def main() -> None:
    args = parse_args()
    if getattr(args, "render_only", False):
        _render_only_from_cache(week_start=args.week_start, week_end=args.week_end, out=args.out)
        return
    run_weekly_digest(week_start=args.week_start, week_end=args.week_end, out=args.out, export_json=args.export_json)


if __name__ == "__main__":
    main()
