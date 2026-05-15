from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Dict, List

import requests

logger = logging.getLogger(__name__)


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
        response = requests.get(url, params=params, timeout=self.timeout_seconds)
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
        output[company] = client.fetch_company_articles(
            company=company,
            aliases=aliases,
            from_date=from_date,
            to_date=to_date,
            max_articles=per_company_limit,
        )
    return output
