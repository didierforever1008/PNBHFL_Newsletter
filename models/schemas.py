from __future__ import annotations

from dataclasses import asdict, dataclass, field, is_dataclass
from typing import Any, Dict, List


@dataclass
class SearchResult:
    title: str
    url: str
    canonical_url: str = ""
    original_url: str = ""
    is_aggregator: bool = False
    date: str = ""
    snippet: str = ""
    source: str = ""


@dataclass
class WeeklySignal:
    company: str
    signal_type: str
    headline: str
    summary: str
    direction: str
    impact: str
    evidence_strength: str
    source_title: str
    source_url: str
    source_date: str = ""


@dataclass
class WeeklyCompanySection:
    company: str
    executive_summary: str
    top_signals: List[WeeklySignal] = field(default_factory=list)
    company_highlights: List[str] = field(default_factory=list)
    sector_macro_context: List[str] = field(default_factory=list)
    caveats: List[str] = field(default_factory=list)
    references: List[SearchResult] = field(default_factory=list)
    metrics: Dict[str, Any] = field(default_factory=dict)


@dataclass
class QuarterlySignal:
    metric: str
    value: str
    trend: str
    commentary: str
    confidence: str
    source_title: str
    source_url: str


@dataclass
class CompanyQuarterSummary:
    company: str
    reporting_period: str
    executive_summary: str
    revenue_or_income: str
    profit_pat: str
    aum_or_disbursement: str
    asset_quality: str
    funding_liquidity: str
    strategy_commentary: str
    management_commentary: str
    risks_caveats: str
    signals: List[QuarterlySignal] = field(default_factory=list)


@dataclass
class ComparisonMetric:
    metric: str
    company_a: str
    value_a: str
    company_b: str
    value_b: str
    commentary: str


@dataclass
class ReportBundle:
    report_type: str
    title: str
    executive_summary: str
    time_period: str = ""
    news_sources: List[str] = field(default_factory=list)
    news_from: List[str] = field(default_factory=list)
    digest_metrics: Dict[str, Any] = field(default_factory=dict)
    policy_watch: List[Dict[str, str]] = field(default_factory=list)
    rating_watch: List[Dict[str, str]] = field(default_factory=list)
    top_signals: List[WeeklySignal] = field(default_factory=list)
    company_highlights: List[str] = field(default_factory=list)
    sector_macro_context: List[str] = field(default_factory=list)
    caveats: List[str] = field(default_factory=list)
    references: List[SearchResult] = field(default_factory=list)
    weekly_company_sections: List[WeeklyCompanySection] = field(default_factory=list)
    company_summaries: List[CompanyQuarterSummary] = field(default_factory=list)
    comparison_table: List[Dict[str, str]] = field(default_factory=list)
    comparison_metrics: List[ComparisonMetric] = field(default_factory=list)
    appendix: List[Dict[str, str]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return _serialize_dataclass(self)


def _serialize_dataclass(value: Any) -> Any:
    if is_dataclass(value):
        return {k: _serialize_dataclass(v) for k, v in asdict(value).items()}
    if isinstance(value, list):
        return [_serialize_dataclass(v) for v in value]
    if isinstance(value, dict):
        return {k: _serialize_dataclass(v) for k, v in value.items()}
    return value




def _as_text(value: Any) -> str:
    if isinstance(value, list):
        return "\n".join(f"- {str(v)}" for v in value if str(v).strip())
    if value is None:
        return ""
    return str(value)



def _parse_search_result(raw: Dict[str, Any]) -> SearchResult:
    canonical_url = str(raw.get("canonical_url", raw.get("url", "")))
    original_url = str(raw.get("original_url", raw.get("url", "")))
    return SearchResult(
        title=str(raw.get("title", "")),
        url=canonical_url or original_url,
        canonical_url=canonical_url,
        original_url=original_url,
        is_aggregator=bool(raw.get("is_aggregator", False)),
        date=str(raw.get("date", "")),
        snippet=str(raw.get("snippet", "")),
        source=str(raw.get("source", "")),
    )


def _parse_weekly_signal(raw: Dict[str, Any], company_fallback: str = "") -> WeeklySignal:
    return WeeklySignal(
        company=str(raw.get("company", company_fallback)),
        signal_type=str(raw.get("signal_type", "other")),
        headline=str(raw.get("headline", raw.get("title", "Not found in source reviewed"))),
        summary=str(raw.get("summary", "Not found in source reviewed")),
        direction=str(raw.get("direction", "unknown")),
        impact=str(raw.get("impact", "unknown")),
        evidence_strength=str(raw.get("evidence_strength", "unknown")),
        source_title=str(raw.get("source_title", raw.get("source", ""))),
        source_url=str(raw.get("source_url", raw.get("url", ""))),
        source_date=str(raw.get("source_date", raw.get("date", ""))),
    )

def parse_weekly_bundle(data: Dict[str, Any]) -> ReportBundle:
    refs = [_parse_search_result(r) for r in data.get("references", []) if isinstance(r, dict)]
    signals = [_parse_weekly_signal(s, company_fallback=str(data.get("company", ""))) for s in data.get("top_signals", []) if isinstance(s, dict)]
    return ReportBundle(
        report_type="weekly-newsletter",
        title=data.get("title", "Bi-Weekly Intelligence Newsletter"),
        executive_summary=_as_text(data.get("executive_summary", "")),
        time_period=str(data.get("time_period", "")),
        news_sources=[str(s) for s in data.get("news_sources", []) if str(s).strip()],
        news_from=[str(s) for s in data.get("news_from", []) if str(s).strip()],
        digest_metrics=data.get("digest_metrics", {}),
        top_signals=signals,
        company_highlights=data.get("company_highlights", []),
        sector_macro_context=data.get("sector_macro_context", []),
        caveats=data.get("caveats", []),
        references=refs,
    )


def parse_weekly_digest_bundle(data: Dict[str, Any]) -> ReportBundle:
    sections: List[WeeklyCompanySection] = []
    for row in data.get("company_sections", []):
        signals = [_parse_weekly_signal(s, company_fallback=str(row.get("company", ""))) for s in row.get("top_signals", []) if isinstance(s, dict)]
        refs = [_parse_search_result(r) for r in row.get("references", []) if isinstance(r, dict)]
        sections.append(
            WeeklyCompanySection(
                company=row.get("company", "Unknown"),
                executive_summary=_as_text(row.get("executive_summary", "")),
                top_signals=signals,
                company_highlights=row.get("company_highlights", []),
                sector_macro_context=row.get("sector_macro_context", []),
                caveats=row.get("caveats", []),
                references=refs,
                metrics=row.get("metrics", {}),
            )
        )

    return ReportBundle(
        report_type="weekly-digest",
        title=data.get("title", "Bi-Weekly Competitor Digest"),
        executive_summary=_as_text(data.get("executive_summary", "")),
        time_period=str(data.get("time_period", "")),
        news_sources=[str(s) for s in data.get("news_sources", []) if str(s).strip()],
        news_from=[str(s) for s in data.get("news_from", []) if str(s).strip()],
        digest_metrics=data.get("digest_metrics", {}),
        weekly_company_sections=sections,
        company_highlights=data.get("cross_company_themes", []),
        caveats=data.get("caveats", []),
    )


def parse_quarterly_bundle(data: Dict[str, Any]) -> ReportBundle:
    refs = [SearchResult(**r) for r in data.get("references", [])]
    summaries: List[CompanyQuarterSummary] = []
    for s in data.get("company_summaries", []):
        q_signals = [QuarterlySignal(**q) for q in s.get("signals", [])]
        s = {**s, "signals": q_signals}
        summaries.append(CompanyQuarterSummary(**s))
    return ReportBundle(
        report_type="quarterly-report",
        title=data.get("title", "Quarterly Competitor Report"),
        executive_summary=_as_text(data.get("executive_summary", "")),
        company_summaries=summaries,
        comparison_table=data.get("comparison_table", []),
        company_highlights=data.get("key_themes", []),
        caveats=data.get("risks_caveats", []),
        references=refs,
    )


def parse_pdf_compare_bundle(data: Dict[str, Any]) -> ReportBundle:
    metrics = [ComparisonMetric(**m) for m in data.get("comparison_metrics", [])]
    return ReportBundle(
        report_type="compare-pdfs",
        title=data.get("title", "Comparative Results Report"),
        executive_summary=_as_text(data.get("executive_summary", "")),
        company_highlights=data.get("reporting_period_validation", []),
        comparison_metrics=metrics,
        caveats=data.get("risks_caveats", []),
        appendix=data.get("appendix", []),
        comparison_table=data.get("side_by_side_table", []),
    )
