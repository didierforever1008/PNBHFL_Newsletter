from __future__ import annotations

import logging
from datetime import datetime
from email.utils import parsedate_to_datetime
from typing import Iterable, List
from urllib.parse import urlparse
from xml.etree import ElementTree as ET

import requests

from services.news_api import NewsArticle
from utils.url_tools import resolve_article_url

logger = logging.getLogger(__name__)

SOURCE_FEEDS = {
    "Reuters": "https://feeds.reuters.com/reuters/businessNews",
    "ET BFSI": "https://economictimes.indiatimes.com/industry/banking/finance/rssfeeds/13358259.cms",
    "Financial Express BFSI": "https://www.financialexpress.com/industry/banking-finance/feed/",
}

POLICY_RATING_QUERIES = [
    ("RBI", "RBI housing finance circular OR RBI housing finance press release"),
    ("NHB", '"National Housing Bank" housing finance circular OR NHB regulation'),
    ("CRISIL", "CRISIL housing finance rating action OR CRISIL HFC outlook"),
    ("ICRA", "ICRA housing finance rating action OR ICRA HFC outlook"),
]


def _safe_parse_date(raw: str) -> str:
    if not raw:
        return ""
    try:
        dt = parsedate_to_datetime(raw)
        return dt.date().isoformat()
    except Exception:  # noqa: BLE001
        return raw[:10]


def fetch_rss_articles(company: str, aliases: Iterable[str], week_start: str, week_end: str, timeout_seconds: int = 20) -> List[NewsArticle]:
    start = datetime.fromisoformat(week_start).date()
    end = datetime.fromisoformat(week_end).date()
    tokens = [company.lower(), *(a.lower() for a in aliases)]

    out: List[NewsArticle] = []
    seen = set()

    for source_name, url in SOURCE_FEEDS.items():
        try:
            response = requests.get(url, timeout=timeout_seconds)
            if response.status_code >= 400:
                continue
            root = ET.fromstring(response.text)
        except Exception:  # noqa: BLE001
            continue

        items = root.findall('.//item')
        for item in items:
            title = (item.findtext('title') or '').strip()
            desc = (item.findtext('description') or '').strip()
            link = (item.findtext('link') or '').strip()
            pub = (item.findtext('pubDate') or '').strip()
            content = f"{title} {desc}".lower()
            if not any(t and t in content for t in tokens):
                continue

            date_iso = _safe_parse_date(pub)
            try:
                parsed = datetime.fromisoformat(date_iso).date()
                if parsed < start or parsed > end:
                    continue
            except Exception:  # noqa: BLE001
                pass

            resolved = resolve_article_url(link)
            canonical_url = resolved.canonical_url or resolved.original_url
            if not canonical_url or canonical_url in seen:
                continue
            seen.add(canonical_url)
            out.append(
                NewsArticle(
                    company=company,
                    title=title,
                    description=desc,
                    content=desc,
                    url=canonical_url,
                    original_url=resolved.original_url,
                    is_aggregator=resolved.is_aggregator,
                    source=source_name,
                    published_at=date_iso,
                )
            )

    logger.info("Fetched %s supplemental RSS articles for %s", len(out), company)
    return out


def fetch_google_news_articles(
    company: str,
    aliases: Iterable[str],
    week_start: str,
    week_end: str,
    timeout_seconds: int = 20,
    max_articles: int = 25,
) -> List[NewsArticle]:
    """Per-company Google News RSS search, filtered to [week_start, week_end].

    Google News RSS doesn't accept arbitrary date filtering on the URL, so we use the
    `when:30d` qualifier to widen recall and then filter client-side by the parsed pubDate.
    Items whose title/description don't mention the company name or one of its aliases
    are dropped (Google News matches loosely across content).
    """
    from urllib.parse import quote_plus

    start = datetime.fromisoformat(week_start).date()
    end = datetime.fromisoformat(week_end).date()
    tokens = [t.lower() for t in [company, *aliases] if str(t).strip()]
    if not tokens:
        return []

    # Quoted company + aliases joined by OR keeps recall high while preventing
    # tokenization noise. Append India context so the search stays within Indian sources.
    quoted_terms = " OR ".join(f'"{t}"' for t in [company, *aliases][:6])
    # 'when:30d' returns up to 30 days of items; we then date-filter precisely client-side.
    query = f'{quoted_terms} India housing finance when:30d'
    rss_url = (
        "https://news.google.com/rss/search"
        f"?q={quote_plus(query)}&hl=en-IN&gl=IN&ceid=IN:en"
    )

    out: List[NewsArticle] = []
    seen = set()
    try:
        response = requests.get(rss_url, timeout=timeout_seconds, headers={"User-Agent": "HousingFinanceDigestBot/1.0"})
        if response.status_code >= 400:
            logger.warning("google news: HTTP %s for %s", response.status_code, company)
            return []
        root = ET.fromstring(response.text)
    except Exception as exc:  # noqa: BLE001
        logger.warning("google news: fetch failed for %s: %s", company, exc)
        return []

    items = root.findall(".//item")
    for item in items[: max_articles * 3]:  # over-fetch then filter
        title = (item.findtext("title") or "").strip()
        desc = (item.findtext("description") or "").strip()
        link = (item.findtext("link") or "").strip()
        pub = (item.findtext("pubDate") or "").strip()
        if not (title and link):
            continue

        # Relevance gate: company or alias must appear in title or description
        haystack = f"{title} {desc}".lower()
        if not any(t in haystack for t in tokens):
            continue

        # Date filter
        date_iso = _safe_parse_date(pub)
        try:
            parsed = datetime.fromisoformat(date_iso).date()
            if parsed < start or parsed > end:
                continue
        except Exception:  # noqa: BLE001
            # If we can't parse the date, drop the item — better than including stale items
            continue

        resolved = resolve_article_url(link)
        canonical_url = resolved.canonical_url or resolved.original_url
        if not canonical_url or canonical_url in seen:
            continue
        seen.add(canonical_url)

        out.append(
            NewsArticle(
                company=company,
                title=title,
                description=desc,
                content=desc,
                url=canonical_url,
                original_url=resolved.original_url,
                is_aggregator=resolved.is_aggregator,
                source="Google News",
                published_at=date_iso,
            )
        )
        if len(out) >= max_articles:
            break

    logger.info("Fetched %s Google News articles for %s", len(out), company)
    return out


def fetch_policy_and_rating_articles(week_start: str, week_end: str, timeout_seconds: int = 20) -> List[NewsArticle]:
    start = datetime.fromisoformat(week_start).date()
    end = datetime.fromisoformat(week_end).date()
    out: List[NewsArticle] = []
    seen = set()

    from urllib.parse import quote_plus

    for source_name, query in POLICY_RATING_QUERIES:
        rss_url = (
            "https://news.google.com/rss/search"
            f"?q={quote_plus(query + ' when:7d')}&hl=en-IN&gl=IN&ceid=IN:en"
        )
        try:
            response = requests.get(rss_url, timeout=timeout_seconds)
            if response.status_code >= 400:
                continue
            root = ET.fromstring(response.text)
        except Exception:  # noqa: BLE001
            continue

        for item in root.findall(".//item"):
            title = (item.findtext("title") or "").strip()
            desc = (item.findtext("description") or "").strip()
            link = (item.findtext("link") or "").strip()
            pub = (item.findtext("pubDate") or "").strip()
            date_iso = _safe_parse_date(pub)
            try:
                parsed = datetime.fromisoformat(date_iso).date()
                if parsed < start or parsed > end:
                    continue
            except Exception:  # noqa: BLE001
                pass
            resolved = resolve_article_url(link)
            canonical_url = resolved.canonical_url or resolved.original_url
            if not canonical_url or canonical_url in seen:
                continue
            seen.add(canonical_url)
            out.append(
                NewsArticle(
                    company="Industry",
                    title=title,
                    description=desc,
                    content=desc,
                    url=canonical_url,
                    original_url=resolved.original_url,
                    is_aggregator=resolved.is_aggregator,
                    source=source_name,
                    published_at=date_iso,
                )
            )

    logger.info("Fetched %s policy/rating context articles (RBI/NHB/CRISIL/ICRA)", len(out))
    return out
