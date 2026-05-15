from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Dict, Iterable, List, Sequence
from urllib.parse import urlparse

from services.news_api import NewsArticle

SOURCE_RELIABILITY: Dict[str, float] = {
    "reuters.com": 0.95,
    "economictimes.com": 0.85,
    "livemint.com": 0.85,
    "moneycontrol.com": 0.80,
}

MATERIALITY_KEYWORDS = [
    "results",
    "earnings",
    "profit",
    "npa",
    "aum",
    "fundraise",
    "rating",
    "regulation",
]


@dataclass
class SelectionMetrics:
    articles_checked: int
    articles_after_dedup: int
    articles_selected: int
    avg_reliability_checked: float
    avg_reliability_selected: float
    coverage_confidence: float


def extract_domain(url: str) -> str:
    try:
        domain = urlparse((url or "").strip()).netloc.lower()
    except Exception:  # noqa: BLE001
        domain = ""
    return domain.replace("www.", "")


def source_reliability_for_url(url: str, default: float = 0.60) -> float:
    domain = extract_domain(url)
    if not domain:
        return default
    for known_domain, score in SOURCE_RELIABILITY.items():
        if domain == known_domain or domain.endswith(f".{known_domain}"):
            return score
    return default


def _keyword_hits(text: str, keywords: Iterable[str]) -> int:
    lowered = (text or "").lower()
    return sum(lowered.count(k.lower()) for k in keywords if k)


def _materiality_score(article: NewsArticle) -> float:
    text = f"{article.title} {article.description} {article.content}".lower()
    hits = _keyword_hits(text, MATERIALITY_KEYWORDS)
    return min(1.0, hits / 4.0)


def _relevance_score(article: NewsArticle, company: str, aliases: Sequence[str]) -> float:
    title = (article.title or "").lower()
    body = f"{article.description} {article.content}".lower()
    tokens = [company, *aliases]
    hits = _keyword_hits(f"{title} {body}", tokens)
    title_boost = 0.25 if any(t and t.lower() in title for t in tokens) else 0.0
    early_body = body[:260]
    early_boost = 0.20 if any(t and t.lower() in early_body for t in tokens) else 0.0
    normalized_hits = min(1.0, hits / 6.0)
    return min(1.0, normalized_hits + title_boost + early_boost)


def _derive_theme_cluster(article: NewsArticle) -> str:
    text = f"{article.title} {article.description} {article.content}".lower()
    for keyword in MATERIALITY_KEYWORDS:
        if keyword in text:
            return keyword
    words = re.findall(r"[a-z0-9]+", text)
    return words[0] if words else "misc"


def annotate_base_scores(articles: Sequence[NewsArticle], company: str, aliases: Sequence[str]) -> None:
    for article in articles:
        article.domain = extract_domain(article.url)
        article.reliability_score = source_reliability_for_url(article.url)
        article.relevance_score = _relevance_score(article, company, aliases)
        article.materiality_score = _materiality_score(article)
        article.theme_cluster = _derive_theme_cluster(article)
        article.cluster_size = max(1, int(getattr(article, "cluster_size", 1) or 1))


def finalize_scores(articles: Sequence[NewsArticle]) -> None:
    theme_counts = Counter(getattr(a, "theme_cluster", "misc") for a in articles)
    for article in articles:
        cluster_size = max(1, int(getattr(article, "cluster_size", 1) or 1))
        duplicate_penalty = min(0.5, 0.15 * (cluster_size - 1))
        theme_size = max(1, theme_counts.get(getattr(article, "theme_cluster", "misc"), 1))
        theme_penalty = min(0.4, 0.1 * (theme_size - 1))
        article.novelty_score = max(0.0, 1.0 - duplicate_penalty - theme_penalty)
        article.final_score = (
            0.35 * float(getattr(article, "relevance_score", 0.0))
            + 0.25 * float(getattr(article, "reliability_score", 0.0))
            + 0.20 * float(getattr(article, "novelty_score", 0.0))
            + 0.20 * float(getattr(article, "materiality_score", 0.0))
        )


def select_balanced_articles(
    articles: Sequence[NewsArticle],
    top_n: int = 18,
    max_per_domain: int = 5,
    max_per_theme: int = 5,
) -> List[NewsArticle]:
    ranked = sorted(articles, key=lambda a: float(getattr(a, "final_score", 0.0)), reverse=True)
    selected: List[NewsArticle] = []
    domain_counts: Dict[str, int] = defaultdict(int)
    theme_counts: Dict[str, int] = defaultdict(int)
    selected_urls = set()

    high_reliability = [a for a in ranked if float(getattr(a, "reliability_score", 0.0)) > 0.9]
    if high_reliability:
        first = high_reliability[0]
        selected.append(first)
        selected_urls.add(first.url)
        domain_counts[getattr(first, "domain", "")] += 1
        theme_counts[getattr(first, "theme_cluster", "misc")] += 1

    for article in ranked:
        if len(selected) >= top_n:
            break
        if article.url in selected_urls:
            continue
        domain = getattr(article, "domain", "")
        theme = getattr(article, "theme_cluster", "misc")
        if domain_counts[domain] >= max_per_domain:
            continue
        if theme_counts[theme] >= max_per_theme:
            continue
        selected.append(article)
        selected_urls.add(article.url)
        domain_counts[domain] += 1
        theme_counts[theme] += 1

    return selected


def build_selection_metrics(
    checked_articles: Sequence[NewsArticle],
    deduped_articles: Sequence[NewsArticle],
    selected_articles: Sequence[NewsArticle],
) -> SelectionMetrics:
    checked_rel = [float(getattr(a, "reliability_score", 0.0)) for a in checked_articles]
    selected_rel = [float(getattr(a, "reliability_score", 0.0)) for a in selected_articles]
    avg_checked = sum(checked_rel) / len(checked_rel) if checked_rel else 0.0
    avg_selected = sum(selected_rel) / len(selected_rel) if selected_rel else 0.0
    coverage_confidence = min(1.0, (0.6 * avg_selected) + (0.4 * min(len(selected_articles) / 8.0, 1.0)))
    return SelectionMetrics(
        articles_checked=len(checked_articles),
        articles_after_dedup=len(deduped_articles),
        articles_selected=len(selected_articles),
        avg_reliability_checked=round(avg_checked, 4),
        avg_reliability_selected=round(avg_selected, 4),
        coverage_confidence=round(coverage_confidence, 4),
    )
