from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Dict, List

import requests

logger = logging.getLogger(__name__)

# Transient network errors we retry-with-backoff before giving up on a single fetch.
_TRANSIENT_NET_EXCEPTIONS = (
    requests.exceptions.ReadTimeout,
    requests.exceptions.ConnectTimeout,
    requests.exceptions.ConnectionError,
    requests.exceptions.ChunkedEncodingError,
)


@dataclass
class NewsArticle:
    company: str
    title: str
    description: str
    content: str
    url: str
    source: str
    published_at: str
    original_url: str = ""
    is_aggregator: bool = False
    query_bucket: str = ""
    domain: str = ""
    reliability_score: float = 0.0
    relevance_score: float = 0.0
    novelty_score: float = 0.0
    materiality_score: float = 0.0
    final_score: float = 0.0
    cluster_size: int = 1
    theme_cluster: str = "misc"


class NewsAPIClient:
    def __init__(self, api_key: str, timeout_seconds: int = 30) -> None:
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds

    def fetch_company_articles(self, company: str, aliases: List[str], from_date: str, to_date: str, max_articles: int = 20) -> List[NewsArticle]:
        if not self.api_key:
            raise ValueError("Missing NEWS_API_KEY. Set it in environment or .env file.")

        query_terms = [f'"{company}"'] + [f'"{a}"' for a in aliases[:4]]
        query = " OR ".join(query_terms)

        params = {
            "q": query,
            "from": from_date,
            "to": to_date,
            "language": "en",
            "sortBy": "publishedAt",
            "pageSize": min(max_articles, 100),
            "apiKey": self.api_key,
        }
        url = "https://newsapi.org/v2/everything"

        # Retry transient network errors (ReadTimeout / ConnectionError) with exponential
        # backoff before giving up on this single company's fetch. NewsAPI occasionally
        # takes >30 s to respond when their backend is under load; one slow request must
        # not abort the whole pipeline.
        response = None
        max_attempts = 3
        for attempt in range(1, max_attempts + 1):
            try:
                response = requests.get(url, params=params, timeout=self.timeout_seconds)
                break
            except _TRANSIENT_NET_EXCEPTIONS as exc:
                if attempt == max_attempts:
                    logger.warning(
                        "NewsAPI network error for '%s' after %s attempts (%s) — returning [] for this fetch. "
                        "Pipeline continues with other sources.",
                        company, max_attempts, type(exc).__name__,
                    )
                    return []
                backoff = 2 ** (attempt - 1)
                logger.info(
                    "NewsAPI %s on attempt %s/%s for '%s'; retrying in %ss.",
                    type(exc).__name__, attempt, max_attempts, company, backoff,
                )
                time.sleep(backoff)
        if response is None:
            return []

        if response.status_code == 429:
            # Developer-plan quota is 100 requests / 24 hours. When we've burned through
            # the daily budget, NewsAPI hard-rejects every request until the rolling
            # window clears. Treat this single status code as "no articles for now"
            # so the rest of the pipeline (BSE / RSS / policy feeds) can still
            # finish. Every other 4xx / 5xx still raises.
            logger.warning(
                "NewsAPI 429 (rate-limited) for '%s' — returning [] for this fetch. "
                "Developer-plan budget is 100 req/24h; pipeline continues with other sources.",
                company,
            )
            return []
        if response.status_code >= 400:
            raise RuntimeError(f"News API error {response.status_code}: {response.text[:400]}")

        data = response.json()
        rows = data.get("articles", [])
        out: List[NewsArticle] = []
        seen = set()
        for r in rows:
            link = (r.get("url") or "").strip()
            if not link or link in seen:
                continue
            seen.add(link)
            out.append(
                NewsArticle(
                    company=company,
                    title=(r.get("title") or "").strip(),
                    description=(r.get("description") or "").strip(),
                    content=(r.get("content") or "").strip(),
                    url=link,
                    source=((r.get("source") or {}).get("name") or "").strip(),
                    published_at=(r.get("publishedAt") or "").strip(),
                )
            )
        logger.info("Fetched %s articles for %s from News API", len(out), company)
        return out


def articles_to_prompt_block(articles: List[NewsArticle], limit: int = 30) -> str:
    lines: List[str] = []
    for idx, a in enumerate(articles[:limit], start=1):
        lines.append(
            f"Article {idx}\nCompany: {a.company}\nTitle: {a.title}\nSource: {a.source}\nDate: {a.published_at}\n"
            f"ReliabilityScore: {getattr(a, 'reliability_score', 0.0):.2f}\n"
            f"URL: {a.url}\nOriginalURL: {getattr(a, 'original_url', '')}\n"
            f"IsAggregator: {str(bool(getattr(a, 'is_aggregator', False))).lower()}\n"
            f"Summary: {a.description}\nContent: {a.content[:1200]}"
        )
    return "\n\n".join(lines)


def collect_articles_for_competitors(client: NewsAPIClient, competitor_map: Dict[str, List[str]], from_date: str, to_date: str, per_company_limit: int = 15) -> Dict[str, List[NewsArticle]]:
    output: Dict[str, List[NewsArticle]] = {}
    for company, aliases in competitor_map.items():
        try:
            output[company] = client.fetch_company_articles(
                company=company,
                aliases=aliases,
                from_date=from_date,
                to_date=to_date,
                max_articles=per_company_limit,
            )
        except Exception as exc:  # noqa: BLE001
            # Last-resort safety net: an unexpected per-company failure must not abort
            # the full pipeline. fetch_company_articles already retries transient
            # network errors and handles 429 gracefully; anything reaching this clause
            # is genuinely unexpected.
            logger.warning(
                "NewsAPI fetch raised unexpectedly for '%s' (%s: %s) — returning [] and continuing.",
                company, type(exc).__name__, str(exc)[:200],
            )
            output[company] = []
    return output
