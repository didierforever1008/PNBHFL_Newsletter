from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Callable, List, Sequence

from services.news_api import NewsArticle


def _safe_parse_datetime(value: str) -> datetime:
    raw = (value or "").strip()
    if not raw:
        return datetime.max
    normalized = raw.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(normalized)
    except ValueError:
        try:
            return datetime.fromisoformat(raw[:10])
        except ValueError:
            return datetime.max


def _tokenize(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", (text or "").lower()))


def _jaccard_similarity(a: set[str], b: set[str]) -> float:
    if not a and not b:
        return 1.0
    union = a | b
    if not union:
        return 0.0
    return len(a & b) / len(union)


def normalized_article_text(article: NewsArticle, snippet_chars: int = 450) -> str:
    snippet = (article.content or article.description or "")[:snippet_chars]
    return f"{article.title} {snippet}".strip().lower()


@dataclass
class DedupCluster:
    representative: NewsArticle
    members: List[NewsArticle]


def cluster_articles_by_similarity(articles: Sequence[NewsArticle], threshold: float = 0.85) -> List[List[NewsArticle]]:
    clusters: List[dict] = []
    for article in articles:
        tokens = _tokenize(normalized_article_text(article))
        matched = None
        for cluster in clusters:
            if _jaccard_similarity(tokens, cluster["centroid_tokens"]) >= threshold:
                matched = cluster
                break

        if matched is None:
            clusters.append({"centroid_tokens": tokens, "members": [article]})
        else:
            matched["members"].append(article)
            merged_tokens = set(matched["centroid_tokens"])
            merged_tokens.update(tokens)
            matched["centroid_tokens"] = merged_tokens

    return [c["members"] for c in clusters]


def deduplicate_semantic_articles(
    articles: Sequence[NewsArticle],
    reliability_getter: Callable[[NewsArticle], float],
    threshold: float = 0.85,
) -> List[DedupCluster]:
    clusters = cluster_articles_by_similarity(articles, threshold=threshold)
    output: List[DedupCluster] = []
    for members in clusters:
        representative = sorted(
            members,
            key=lambda item: (
                -float(reliability_getter(item)),
                _safe_parse_datetime(item.published_at),
            ),
        )[0]
        output.append(DedupCluster(representative=representative, members=list(members)))
    return output
