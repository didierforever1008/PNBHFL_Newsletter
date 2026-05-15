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
