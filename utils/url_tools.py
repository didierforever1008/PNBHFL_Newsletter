from __future__ import annotations

import base64
import urllib.parse
from dataclasses import dataclass
from typing import Optional

import requests


@dataclass(frozen=True)
class ResolvedURL:
    canonical_url: str
    original_url: str
    is_aggregator: bool


def resolve_article_url(url: str) -> ResolvedURL:
    original_url = (url or "").strip()
    if not original_url:
        return ResolvedURL(canonical_url="", original_url="", is_aggregator=False)

    canonical_url = decode_google_news_url(original_url).strip() or original_url
    is_aggregator = _is_aggregator_url(original_url) and canonical_url == original_url
    return ResolvedURL(
        canonical_url=canonical_url,
        original_url=original_url,
        is_aggregator=is_aggregator,
    )


def decode_google_news_url(url: str) -> str:
    """Decode Google News redirect/encoded URLs into canonical article URLs when possible."""
    parsed = urllib.parse.urlparse(url)

    # Direct links are already clean.
    if "news.google.com" not in parsed.netloc:
        return url

    query_params = urllib.parse.parse_qs(parsed.query)
    if "url" in query_params and query_params["url"]:
        return query_params["url"][0]

    # Encoded format in path: /rss/articles/{base64}
    path_parts = [p for p in parsed.path.split("/") if p]
    if len(path_parts) >= 3 and path_parts[-2] == "articles":
        candidate = _safe_base64_decode(path_parts[-1])
        if candidate and candidate.startswith(("http://", "https://")):
            return candidate

    resolved = _resolve_redirect(url)
    if resolved:
        return resolved

    return url


def _safe_base64_decode(value: str) -> Optional[str]:
    try:
        padding = "=" * (-len(value) % 4)
        raw = base64.urlsafe_b64decode(value + padding)
        text = raw.decode("utf-8", errors="ignore")
        start = text.find("http")
        if start >= 0:
            return text[start:].split("\x00")[0].strip()
        return None
    except Exception:
        return None


def _resolve_redirect(url: str) -> Optional[str]:
    try:
        response = requests.get(
            url,
            allow_redirects=True,
            timeout=8,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
                )
            },
        )
        final_url = response.url
        if final_url and "news.google.com" not in urllib.parse.urlparse(final_url).netloc:
            return final_url
    except requests.RequestException:
        return None
    return None


def _is_aggregator_url(url: str) -> bool:
    host = urllib.parse.urlparse((url or "").strip()).netloc.lower()
    return host.endswith("news.google.com")
