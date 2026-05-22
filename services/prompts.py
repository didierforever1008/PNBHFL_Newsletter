from __future__ import annotations

import json
from typing import Dict, List


# Shared editorial rules injected into every analyst-facing prompt.
# Keep them tight and explicit — the LLM responds to concrete bans far better
# than vague style guidance.
SHARED_RULES = (
    "EDITORIAL RULES (apply to every field you produce):\n"
    "1. INDIA-ONLY: Discuss ONLY the Indian housing-finance / mortgage / NBFC market. "
    "DROP every item about non-Indian markets (US, UK, EU, Australia, Germany, Singapore, etc.) "
    "and every foreign issuer (e.g. ANZ, Freddie Mac, Fannie Mae, Lloyds, Nationwide). If an "
    "evidence item is not India-focused, ignore it — do NOT mention it just to note its existence.\n"
    "2. NO META-LANGUAGE: NEVER write any phrase that refers to the underlying evidence — "
    "all of these are banned (singular OR plural): 'the provided article', 'the provided articles', "
    "'the article states', 'this article', 'these articles', 'from the provided articles', "
    "'in the provided articles', 'based on the provided evidence', 'as per the source', "
    "'according to the source', 'the source reports', 'as mentioned', 'the evidence shows'. "
    "The reader does not see the underlying article — write the FACT directly. "
    "Bad: 'The provided article focuses on the personal journey of X.' "
    "Good: 'X took over as CEO and described her early-career pivots.'\n"
    "2b. NO PLACEHOLDER 'NO-NEWS' ROWS: NEVER produce a row, bullet, or summary whose primary "
    "content is that nothing was found. Banned shapes: 'No specific intelligence was found…', "
    "'No direct signals…', 'No material signals…', 'No information was available…', "
    "'No movement was reported…', 'Not found in source reviewed', 'Evidence provided was…'. "
    "If a company / topic has nothing to report this period, OMIT IT ENTIRELY — do NOT emit a "
    "row that explains the absence.\n"
    "3. PROPER SENTENCE CASE: Every sentence and every bullet must START with a capital letter. "
    "Do not begin a sentence with a lowercase verb (e.g. 'announced the appointment...'). "
    "Spell out company names correctly (capitalised) and avoid awkward elliptical fragments.\n"
    "4. NO ELLIPSES ANYWHERE: This is a formal company-wide newsletter. Do NOT use '...', "
    "'..', or '…' in ANY field — not at the end, not mid-sentence, never. Every sentence "
    "must be complete and end with a single period (or '?' / '!' where genuinely warranted). "
    "If you can't say it cleanly in one sentence, say less but say it fully.\n"
    "5. CONCISE: 1–2 short factual sentences per item, max ~30 words. No marketing prose, no "
    "consultancy filler ('this development indicates...', 'going forward...').\n"
)


def weekly_newsletter_prompt(company: str, week_start: str, week_end: str, today_iso: str, articles_block: str) -> str:
    schema = {
        "title": f"Bi-Weekly Intelligence Newsletter: {company}",
        "executive_summary": ["3-6 concise bullet points"],
        "top_signals": [
            {
                "company": company,
                "signal_type": "financial|funding|risk|strategy|leadership|macro",
                "headline": "string",
                "summary": "string",
                "direction": "positive|negative|mixed|neutral",
                "impact": "high|medium|low",
                "evidence_strength": "high|medium|low",
                "source_title": "string",
                "source_url": "string",
                "source_date": "YYYY-MM-DD",
            }
        ],
        "company_highlights": ["string"],
        "sector_macro_context": ["string"],
        "caveats": ["string"],
        "references": [{"title": "string", "url": "string", "date": "YYYY-MM-DD", "snippet": "string", "source": "string"}],
    }
    return (
        f"You are a BFSI analyst. Today is {today_iso}. Analyze ONLY the provided article set for {company} between {week_start} and {week_end}. "
        "Return ONLY valid JSON. Separate facts from interpretation. Never invent numbers. If unavailable write 'Not found in source reviewed'. Keep outputs sectioned and readable; executive_summary must be short bullet points, not a paragraph. "
        f"Output schema: {json.dumps(schema)}\n\n"
        f"Articles:\n{articles_block}"
    )


def weekly_company_intelligence_prompt(company: str, week_start: str, week_end: str, articles_block: str) -> str:
    schema = {
        "company": company,
        "executive_summary": ["2-4 bullet points"],
        "top_signals": [
            {
                "company": company,
                "signal_type": "financial|funding|risk|strategy|leadership|macro",
                "headline": "string",
                "summary": "string",
                "direction": "positive|negative|mixed|neutral",
                "impact": "high|medium|low",
                "evidence_strength": "high|medium|low",
                "source_title": "string",
                "source_url": "string",
                "source_date": "YYYY-MM-DD",
            }
        ],
        "company_highlights": ["string"],
        "sector_macro_context": ["string"],
        "caveats": ["string"],
        "references": [{"title": "string", "url": "string", "date": "YYYY-MM-DD", "snippet": "string", "source": "string"}],
    }
    return (
        f"Extract intelligence for {company} only, between {week_start} and {week_end}, from provided articles. "
        "Prioritize precise indicators: funding, asset quality, growth, governance, leadership, strategic moves. "
        "Only extract signals from provided articles. Do not infer or hallucinate. "
        "Return ONLY valid JSON with concise bullets and attributable signals. "
        f"Schema: {json.dumps(schema)}\n\nArticles:\n{articles_block}"
    )


def weekly_digest_aggregate_prompt(week_start: str, week_end: str, sections_json: str) -> str:
    schema = {
        "title": f"Bi-Weekly Competitor Digest ({week_start} to {week_end})",
        "executive_summary": ["4-8 bullets covering cross-company shifts"],
        "cross_company_themes": ["string"],
        "caveats": ["string"],
    }
    return (
        "You are a senior BFSI strategist. Given competitor-level intelligence JSON, write a portfolio-level digest summary. "
        "Make it precise and comparative; call out winners/laggards, risk hotspots, and notable strategic moves. "
        "Return ONLY valid JSON.\n"
        f"Schema: {json.dumps(schema)}\n\n"
        f"Competitor intelligence:\n{sections_json[:20000]}"
    )


def weekly_digest_prompt(companies: List[str], week_start: str, week_end: str, today_iso: str, articles_block: str) -> str:
    # kept for backward compatibility
    return weekly_digest_aggregate_prompt(week_start=week_start, week_end=week_end, sections_json=articles_block)


def weekly_digest_agentic_analysis_prompt(
    week_start: str,
    week_end: str,
    articles_block: str,
    competitor_list: List[str],
) -> str:
    schema = {
        "title": f"Bi-Weekly Housing Finance Industry Agentic Analysis ({week_start} to {week_end})",
        "time_period": f"{week_start} to {week_end}",
        "industry_summary": ["4-8 concise bullets on industry-wide movement"],
        "regulatory_updates": [
            {
                "line": "concise policy/rating update line",
                "summary": "one sentence summary",
                "signal": "one key signal/insight",
                "source": "RBI|NHB|CRISIL|ICRA|other source name",
                "date": "YYYY-MM-DD",
                "title": "source title",
                "url": "https://...",
            }
        ],
        "competitor_table": [
            {
                "company": "string",
                "weekly_summary": "1-2 sentence precise summary of this week's movement",
                "signal_types": ["financial|funding|risk|strategy|leadership|macro"],
            }
        ],
        "caveats": ["string"],
    }
    return (
        "You are a senior housing-finance intelligence strategist. Analyze ONLY the provided weekly article set, which includes "
        "competitor-specific articles and broader housing-finance industry movement articles. "
        "Return ONLY valid JSON.\n"
        "Requirements:\n"
        "- Produce three sections: (1) overall industry summary (2) regulatory updates (3) competitor tabular summary.\n"
        "- Competitor table must include: competitor name, this-week precise summary, and a few signal types.\n"
        f"- Allowed competitors (use ONLY these names): {', '.join(competitor_list)}.\n"
        "- Include only competitors where meaningful source-backed update exists this week.\n"
        "- Do not introduce external entities as competitors.\n"
        "- Industry summary should include at least one point each on rates/funding, demand/growth, risk/asset quality, and policy/regulation.\n"
        "- Keep summaries crisp and attributable; if data is missing, say 'Not found in source reviewed'.\n"
        f"Schema: {json.dumps(schema)}\n\n"
        f"Articles:\n{articles_block}"
    )


def article_signal_summary_prompt(article_block: str, competitor_list: List[str]) -> str:
    schema = {
        "section_tag": "industry|regulatory|competitor",
        "company": "string (use 'Industry' when not competitor-specific)",
        "article_title": "string",
        "article_summary": "1-3 sentence summary aligned to the selected section_tag perspective",
        "signals": [
            {
                "signal_type": "financial|funding|risk|strategy|leadership|macro|regulatory|governance|operations|market",
                "signal": "source-backed signal statement",
                "source_snippet": "short attributable snippet/paraphrase from the provided article only",
                "impact": "positive|negative|mixed|neutral",
            }
        ],
        "signal_types": ["financial|funding|risk|strategy|leadership|macro"],
        "confidence": "high|medium|low",
        "section_tag": "industry|regulatory|competitor",
    }
    return (
        "Summarize the single article below into compact intelligence JSON. "
        "Return ONLY valid JSON. If company is unclear, use 'Industry'. "
        "Classify each article into section_tag: industry, regulatory, or competitor. "
        f"If company is recognized, map to one of these names only: {', '.join(competitor_list)}. "
        f"Schema: {json.dumps(schema)}\n\n"
        f"Article:\n{article_block}"
    )


def article_signal_extraction_prompt(article_block: str, competitor_list: List[str]) -> str:
    schema = {
        "summary": "1-2 sentence precise summary",
        "extracted_signals": ["signal bullet"],
        "section_tag": "industry|regulatory|competitor",
        "confidence": "high|medium|low",
        "source_metadata": {
            "url": "string",
            "title": "string",
            "date": "YYYY-MM-DD",
            "source": "string",
        },
    }
    return (
        "Analyze the single article below and extract structured intelligence. "
        "Return ONLY valid JSON. "
        f"If a competitor is mentioned, map company names only from this set: {', '.join(competitor_list)}. "
        "Tag the article into exactly one section_tag: industry, regulatory, or competitor. "
        f"Schema: {json.dumps(schema)}\n\n"
        f"Article:\n{article_block}"
    )


def industrial_news_agent_prompt(week_start: str, week_end: str, normalized_articles_json: str) -> str:
    schema = {
        "industry_summary": ["4-8 concise bullets on industry-wide movement"],
        "caveats": ["string"],
    }
    return (
        "You are the industrial_news_agent for the INDIAN housing-finance market. "
        f"Synthesize only section_tag='industry' items for {week_start} to {week_end}. "
        "Return ONLY valid JSON.\n"
        + SHARED_RULES +
        f"Schema: {json.dumps(schema)}\n\n"
        f"Normalized article intelligence:\n{normalized_articles_json[:45000]}"
    )


def regulatory_news_agent_prompt(week_start: str, week_end: str, normalized_articles_json: str) -> str:
    schema = {
        "regulatory_updates": [
            {"line": "string", "url": "string", "source": "string", "date": "string", "title": "string"}
        ],
        "caveats": ["string"],
    }
    return (
        "You are the regulatory_news_agent for housing finance. "
        f"Synthesize only section_tag='regulatory' items for {week_start} to {week_end}. "
        "Return ONLY valid JSON.\n"
        f"Schema: {json.dumps(schema)}\n\n"
        f"Normalized article intelligence:\n{normalized_articles_json[:45000]}"
    )


def competitor_news_agent_prompt(
    week_start: str,
    week_end: str,
    normalized_articles_json: str,
    competitor_list: List[str],
) -> str:
    schema = {
        "competitor_table": [
            {
                "company": "string",
                "weekly_summary": "1-2 sentence precise summary of this week's movement",
                "signal_types": ["financial|funding|risk|strategy|leadership|macro"],
            }
        ],
        "caveats": ["string"],
    }
    return (
        "You are the competitor_news_agent for INDIAN housing-finance lenders. "
        f"Synthesize only section_tag='competitor' items for {week_start} to {week_end}. "
        f"Allowed competitors (use ONLY these names): {', '.join(competitor_list)}. "
        "Include only competitors with meaningful India-focused source-backed updates. "
        "Return ONLY valid JSON.\n"
        + SHARED_RULES +
        f"Schema: {json.dumps(schema)}\n\n"
        f"Normalized article intelligence:\n{normalized_articles_json[:45000]}"
    )


def weekly_digest_agentic_from_summaries_prompt(
    week_start: str,
    week_end: str,
    summaries_json: str,
    competitor_list: List[str],
) -> str:
    schema = {
        "title": f"Bi-Weekly Housing Finance Industry Agentic Analysis ({week_start} to {week_end})",
        "time_period": f"{week_start} to {week_end}",
        "industry_summary": ["4-8 concise bullets on industry-wide movement"],
        "regulatory_updates": [
            {
                "line": "concise policy/rating update line",
                "summary": "one sentence summary",
                "signal": "one key signal/insight",
                "source": "RBI|NHB|CRISIL|ICRA|other source name",
                "date": "YYYY-MM-DD",
                "title": "source title",
                "url": "https://...",
            }
        ],
        "competitor_table": [
            {
                "company": "string",
                "weekly_summary": "1-2 sentence precise summary of this week's movement",
                "signal_types": ["financial|funding|risk|strategy|leadership|macro"],
            }
        ],
        "caveats": ["string"],
    }
    return (
        "You are a senior housing-finance intelligence strategist. "
        "Given pre-synthesized section outputs (industry/regulatory/competitor) and normalization metadata, produce a final weekly agentic analysis. "
        "Return ONLY valid JSON.\n"
        "Requirements:\n"
        "- Section 1: overall industry summary\n"
        "- Section 2: regulatory updates\n"
        "- Section 3: competitor table with competitor name, this-week precise summary, and signal types\n"
        f"- Allowed competitors (use ONLY these names): {', '.join(competitor_list)}.\n"
        "- Include only competitors where meaningful source-backed update exists this week.\n"
        "- Do not introduce external entities as competitors.\n"
        "- Industry summary should include at least one point each on rates/funding, demand/growth, risk/asset quality, and policy/regulation.\n"
        "- Deduplicate repeated themes and keep summaries concise.\n"
        f"Schema: {json.dumps(schema)}\n\n"
        f"Article micro-summaries:\n{summaries_json[:45000]}"
    )


def weekly_digest_agentic_industry_prompt(
    week_start: str,
    week_end: str,
    source_block: str,
) -> str:
    schema = {
        "industry_summary": ["4-8 concise bullets on industry-wide movement"],
        "caveats": ["string"],
    }
    return (
        f"You are a senior INDIAN housing-finance strategist. Extract industry-wide movement "
        f"between {week_start} and {week_end} from the weekly evidence. Cover ONLY the Indian market — "
        "drop foreign-market items silently. Return ONLY valid JSON.\n"
        "Focus on rates/funding, demand/growth, risk/asset quality, and capital/funding environment "
        "as they apply to Indian HFCs / NBFCs / banks.\n"
        + SHARED_RULES +
        f"Schema: {json.dumps(schema)}\n\n"
        f"Evidence:\n{source_block[:45000]}"
    )


def weekly_digest_agentic_regulatory_prompt(
    week_start: str,
    week_end: str,
    source_block: str,
) -> str:
    schema = {
        "regulatory_updates": [
            {
                "line": "concise policy/rating update line",
                "summary": "one sentence summary",
                "signal": "one key signal/insight",
                "source": "RBI|NHB|CRISIL|ICRA|other source name",
                "date": "YYYY-MM-DD",
                "title": "source title",
                "url": "https://...",
            }
        ],
        "caveats": ["string"],
    }
    return (
        f"Extract regulatory and rating-agency updates for housing finance between {week_start} and {week_end}. "
        "Use only provided evidence. Return ONLY valid JSON.\n"
        "For each item in regulatory_updates include exactly one concise line, one summary sentence, one signal/insight, "
        "and source metadata fields (source/date/title/url).\n"
        "If updates are sparse, include one object populated with 'Not found in source reviewed'.\n"
        f"Schema: {json.dumps(schema)}\n\n"
        f"Evidence:\n{source_block[:45000]}"
    )


def weekly_digest_agentic_competitor_prompt(
    week_start: str,
    week_end: str,
    competitor_list: List[str],
    source_block: str,
) -> str:
    schema = {
        "competitor_table": [
            {
                "company": "string",
                "weekly_summary": "1-2 sentence precise summary of this week's movement",
                "signal_types": ["financial|funding|risk|strategy|leadership|macro"],
            }
        ],
        "caveats": ["string"],
    }
    return (
        f"Build a weekly competitor table for INDIAN housing-finance coverage between {week_start} and {week_end}. "
        "Return ONLY valid JSON.\n"
        f"Allowed competitors (use ONLY these names): {', '.join(competitor_list)}.\n"
        "Include only companies with meaningful India-focused source-backed updates. OMIT a company "
        "entirely if its evidence is just biographical fluff or non-Indian commentary.\n"
        + SHARED_RULES +
        f"Schema: {json.dumps(schema)}\n\n"
        f"Evidence:\n{source_block[:45000]}"
    )


def weekly_digest_agentic_final_synthesis_prompt(
    week_start: str,
    week_end: str,
    competitor_list: List[str],
    industry_json: str,
    regulatory_json: str,
    competitor_json: str,
) -> str:
    schema = {
        "title": f"Bi-Weekly Housing Finance Industry Agentic Analysis ({week_start} to {week_end})",
        "time_period": f"{week_start} to {week_end}",
        "industry_summary": ["4-8 concise bullets on industry-wide movement"],
        "regulatory_updates": [
            {
                "line": "concise policy/rating update line",
                "summary": "one sentence summary",
                "signal": "one key signal/insight",
                "source": "RBI|NHB|CRISIL|ICRA|other source name",
                "date": "YYYY-MM-DD",
                "title": "source title",
                "url": "https://...",
            }
        ],
        "competitor_table": [
            {
                "company": "string",
                "weekly_summary": "1-2 sentence precise summary of this week's movement",
                "signal_types": ["financial|funding|risk|strategy|leadership|macro"],
            }
        ],
        "caveats": ["string"],
    }
    return (
        "Synthesize the final weekly housing-finance agentic analysis from three pre-computed sections "
        "(industry, regulatory, competitor). Return ONLY valid JSON.\n"
        f"Allowed competitors: {', '.join(competitor_list)}.\n"
        "Preserve evidence-backed specificity; deduplicate repeated points.\n"
        f"Schema: {json.dumps(schema)}\n\n"
        f"Industry section JSON:\n{industry_json[:14000]}\n\n"
        f"Regulatory section JSON:\n{regulatory_json[:14000]}\n\n"
        f"Competitor section JSON:\n{competitor_json[:20000]}"
    )


def quarterly_report_prompt(companies: List[str], today_iso: str, articles_block: str) -> str:
    schema = {
        "title": "Quarterly Results Intelligence Report",
        "executive_summary": "string",
        "company_summaries": [
            {
                "company": "string",
                "reporting_period": "string",
                "executive_summary": "string",
                "revenue_or_income": "string",
                "profit_pat": "string",
                "aum_or_disbursement": "string",
                "asset_quality": "string",
                "funding_liquidity": "string",
                "strategy_commentary": "string",
                "management_commentary": "string",
                "risks_caveats": "string",
                "signals": [
                    {
                        "metric": "string",
                        "value": "string",
                        "trend": "up|down|flat|mixed|unknown",
                        "commentary": "string",
                        "confidence": "high|medium|low",
                        "source_title": "string",
                        "source_url": "string",
                    }
                ],
            }
        ],
        "comparison_table": [{"metric": "string"}],
        "key_themes": ["string"],
        "risks_caveats": ["string"],
        "references": [{"title": "string", "url": "string", "date": "YYYY-MM-DD", "snippet": "string", "source": "string"}],
    }
    return (
        f"You are a BFSI analyst. Today is {today_iso}. Analyze ONLY the provided latest quarterly-result-related articles for {', '.join(companies)}. "
        "Focus on attributable facts and mark missing data explicitly. Return ONLY valid JSON. "
        f"Output schema: {json.dumps(schema)}\n\n"
        f"Articles:\n{articles_block}"
    )


def pdf_compare_prompt(company_a: str, company_b: str, extracted_text_a: str, extracted_text_b: str) -> str:
    schema = {
        "title": f"Comparative Quarterly Report: {company_a} vs {company_b}",
        "executive_summary": "string",
        "reporting_period_validation": ["string"],
        "company_a_snapshot": ["string"],
        "company_b_snapshot": ["string"],
        "side_by_side_table": [{"metric": "string", company_a: "string", company_b: "string"}],
        "comparison_metrics": [
            {
                "metric": "string",
                "company_a": company_a,
                "value_a": "string",
                "company_b": company_b,
                "value_b": "string",
                "commentary": "string",
            }
        ],
        "key_insights": ["string"],
        "risks_caveats": ["string"],
        "appendix": [{"item": "string", "evidence": "string", "page_reference": "string"}],
    }
    return (
        "You are a BFSI analyst. Use ONLY the provided document extracts. Extract facts first, then interpretation. "
        "Do not infer quarter from filename; detect reporting period from content. Preserve page references where possible. "
        "If a metric is missing, write 'Not found in source reviewed'. Return ONLY valid JSON with this schema: "
        f"{json.dumps(schema)}\n\n"
        f"Document A ({company_a}) extracts:\n{extracted_text_a[:35000]}\n\n"
        f"Document B ({company_b}) extracts:\n{extracted_text_b[:35000]}"
    )


def industry_synthesis_prompt(week_start: str, week_end: str, industry_items_json: str) -> str:
    schema = {"industry_summary": ["4-8 concise bullets on industry-wide movement"]}
    return (
        "You are a senior INDIAN housing-finance analyst. Synthesize ONLY India-relevant "
        "industry-tagged article items into concise bullets. Drop foreign-market items silently. "
        "Return ONLY valid JSON. If no India-relevant evidence exists, return an empty "
        "'industry_summary' array — do NOT pad with 'Not found in source reviewed'.\n"
        + SHARED_RULES +
        f"Schema: {json.dumps(schema)}\n\n"
        f"Week: {week_start} to {week_end}\n"
        f"Industry items:\n{industry_items_json[:25000]}"
    )


def regulatory_synthesis_prompt(week_start: str, week_end: str, regulatory_items_json: str) -> str:
    schema = {
        "regulatory_updates": [
            {
                "line": "concise policy/rating update line",
                "summary": "one sentence summary",
                "signal": "one key signal/insight",
                "source": "RBI|NHB|CRISIL|ICRA|other source name",
                "date": "YYYY-MM-DD",
                "title": "source title",
                "url": "https://...",
            }
        ]
    }
    return (
        "You are a policy and ratings analyst. Synthesize ONLY regulatory-tagged article items into concise updates. "
        "Focus on RBI/NHB/CRISIL/ICRA implications and return ONLY valid JSON. "
        "If no meaningful evidence exists, use 'Not found in source reviewed'.\n"
        f"Schema: {json.dumps(schema)}\n\n"
        f"Week: {week_start} to {week_end}\n"
        f"Regulatory items:\n{regulatory_items_json[:25000]}"
    )


def competitor_synthesis_prompt(week_start: str, week_end: str, competitor_items_json: str, competitor_list: List[str]) -> str:
    schema = {
        "competitor_table": [
            {
                "company": "string",
                "weekly_summary": "1-2 sentence precise summary of this week's movement",
                "signal_types": ["financial|funding|risk|strategy|leadership|macro"],
            }
        ]
    }
    return (
        "You are a competitive-intelligence analyst for INDIAN housing-finance lenders. "
        "Synthesize ONLY competitor-tagged article items into a competitor table. "
        "Return ONLY valid JSON and do not introduce companies outside the allowed list.\n"
        f"Allowed competitors: {', '.join(competitor_list)}.\n"
        "If no meaningful evidence exists for a competitor, OMIT that competitor entirely "
        "(do not include a row saying 'no material signals').\n"
        + SHARED_RULES +
        f"Schema: {json.dumps(schema)}\n\n"
        f"Week: {week_start} to {week_end}\n"
        f"Competitor items:\n{competitor_items_json[:25000]}"
    )


def newsletter_composer_prompt(
    date_range: str,
    industry_json: str,
    regulatory_json: str,
    competitor_json: str,
) -> str:
    schema = {
        "industry_pulse": {
            "summary_paragraph": "Insight-driven paragraph on industry movement.",
            "highlights": [
                {
                    "pointer": (
                        "ONE concise factual sentence — the headline shown in the "
                        "highlighted box. Max ~25 words."
                    ),
                    "impact": (
                        "ONE sentence — the direct, concrete impact on Indian housing-finance "
                        "companies / NBFCs / banks. NOT a paraphrase of 'pointer'."
                    ),
                    "why_it_matters": (
                        "ONE sentence — strategic interpretation for an HFC leadership team: "
                        "what to do about it, what dynamic it signals. NOT a paraphrase of "
                        "'pointer' or 'impact'."
                    ),
                }
            ],
        },
        "regulatory_watch": [
            {
                "title": "string",
                "what_happened": "string",
                "impact": "string",
                "why_it_matters": "string",
            }
        ],
        "competitor_intelligence": {
            "grouped_insights": [
                {
                    "category": "Growth|Risk|Funding|Strategy",
                    "items": [
                        {
                            "company": "string",
                            "event": (
                                "ONE concise factual sentence (max ~20 words). The 'what'. "
                                "This is the highlighted-box headline."
                            ),
                            "narrative": (
                                "2-3 sentences elaborating on the event: WHY it matters, "
                                "competitive read-through, strategic context, expected impact "
                                "on the housing-finance market. MUST ADD INFORMATION beyond "
                                "the 'event' sentence — do NOT rephrase or repeat it. "
                                "This is the paragraph shown below the highlighted box."
                            ),
                            "signal": "string",
                            "severity": "Low|Medium|High",
                        }
                    ],
                }
            ]
        },
        "patterns": [
            {
                "pattern_name": "string",
                "observation": "string",
                "insight": "string",
                "risk": "string",
            }
        ],
        "key_takeaways": ["string", "string", "string"],
    }
    return (
        "You are the newsletter_composer for an INDIAN housing-finance intelligence pipeline. "
        "Input JSON contains section-level outputs from industry, regulatory, and competitor synthesis. "
        "Transform bullet-heavy material into insight-driven narrative while preserving factual grounding.\n"
        "Return ONLY valid JSON.\n"
        + SHARED_RULES +
        "Requirements:\n"
        "- Transform 'industry_summary' input into 'industry_pulse' with a concise summary_paragraph plus highlights. "
        "Drop any non-India item silently. If no India-relevant signal exists, return an EMPTY highlights array "
        "and an empty summary_paragraph — do NOT pad with 'Not found in source reviewed'.\n"
        "- Each industry highlight MUST be an OBJECT with three distinct fields:\n"
        "    * 'pointer'         : ONE complete, self-explanatory sentence of ~15-20 words. The\n"
        "                          renderer puts this directly in the highlighted box as its\n"
        "                          headline — there is no separate short title — so the sentence\n"
        "                          must stand alone and be short enough to fit on one line.\n"
        "    * 'impact'          : 1 sentence — direct concrete impact on Indian HFCs / NBFCs / banks.\n"
        "    * 'why_it_matters'  : 1 sentence — strategic interpretation for an HFC leadership team.\n"
        "  The three fields MUST be distinct (no rephrasing, no repetition).\n"
        "  Bad example:\n"
        "    pointer: 'RBI penalises Hinduja Housing Finance for KYC violations.'\n"
        "    impact:  'RBI fined Hinduja Housing Finance for KYC issues.'\n"
        "    why_it_matters: 'Hinduja Housing Finance was fined by RBI for KYC.'\n"
        "  Good example:\n"
        "    pointer: 'RBI penalises Hinduja Housing Finance for KYC violations.'\n"
        "    impact:  'A monetary penalty plus reputational drag for one mid-size HFC; peers face higher KYC audit scrutiny next quarter.'\n"
        "    why_it_matters: 'Signals an enforcement-led tightening cycle — HFCs should accelerate KYC remediation budgets and disclose progress proactively.'\n"
        "- Transform each 'regulatory_updates' item into structured 'regulatory_watch' entries with: title, what_happened, impact, why_it_matters.\n"
        "    * 'what_happened' MUST be ONE complete self-explanatory sentence of ~15-20 words.\n"
        "      The renderer puts this directly in the highlighted box as its headline — there is\n"
        "      no separate short title in the rendered PDF — so it must stand alone and be short\n"
        "      enough to fit on a single line.\n"
        "    * 'title' is used only as a fallback if 'what_happened' is empty; keep it equally tight.\n"
        "    * 'impact' and 'why_it_matters' each: 1 sentence, distinct from what_happened and each other.\n"
        "- Convert flat competitor summaries into grouped intelligence categories using ONLY: Growth & Strategy, Funding & Capital, Risk & Governance, Operational Signals.\n"
        "- UNIQUENESS RULE (CRITICAL): every underlying news item must be placed in EXACTLY ONE of\n"
        "  the four categories above. Do NOT duplicate the same filing / article / event across\n"
        "  multiple grouped_insights buckets. Pick the single most-relevant category and place\n"
        "  the item there only. If two facets of the same news (e.g. a capital raise that also has\n"
        "  governance implications) feel relevant, pick the more material one and mention the\n"
        "  secondary angle inside that one narrative rather than creating a second item.\n"
        "- For EACH competitor item, produce 'event' and (for non-Operational categories)\n"
        "  a substantive 'narrative'. The renderer concatenates them below the company-name\n"
        "  box and renders the full text in flowing prose — NO truncation, NO ellipsis. Plan\n"
        "  for ~2-3 typeset lines of total content.\n"
        "- DROP-IF-NO-NARRATIVE RULE: For Growth & Strategy / Funding & Capital / Risk &\n"
        "  Governance, an item WITHOUT a real, distinct 'narrative' will be silently dropped\n"
        "  by the renderer (better to show nothing than a one-line stub). So either write\n"
        "  the narrative properly or do NOT emit the item at all. Routine filings with no\n"
        "  meaningful read-through should be omitted from the grouped_insights output.\n"
        "- Field shape:\n"
        "    * 'event'     = ONE complete factual sentence. The base news.\n"
        "                    For Operational Signals: name + action + position + effective date.\n"
        "                    For Growth & Strategy / Funding & Capital / Risk & Governance:\n"
        "                      what happened + the key number, outcome, or party involved.\n"
        "    * 'narrative' = OPTIONAL for Operational Signals (renderer ignores it there).\n"
        "                    For the other three CI categories: 1 sentence of EXPLANATION —\n"
        "                    why the development matters, the strategic / market implication,\n"
        "                    or the read-through for Indian HFC peers. Write it as a complete\n"
        "                    sentence (do NOT end with an ellipsis or a dangling clause). The\n"
        "                    full event+narrative together should fit in roughly two typeset\n"
        "                    lines but DO NOT artificially truncate — clarity over brevity.\n"
        "    * NEVER write '...' or '…' in any field. The output is a formal company-wide\n"
        "      newsletter and must read as polished prose.\n"
        "    * Event and narrative MUST be distinct — narrative must add information, not\n"
        "      rephrase the event.\n"
        "    * Bad example:\n"
        "        event:     'LIC Housing Finance appointed Sanjay Dayal as Chief Operating Officer.'\n"
        "        narrative: 'LIC Housing Finance appointed Sanjay Dayal as Chief Operating Officer.'\n"
        "    * Good example:\n"
        "        event:     'LIC Housing Finance appointed Sanjay Dayal as Chief Operating Officer.'\n"
        "        narrative: 'The appointment fills a senior operating role at one of India's largest\n"
        "                    HFCs and signals a renewed push on execution discipline. Peers will\n"
        "                    watch how the new COO sequences distribution and asset-quality\n"
        "                    priorities heading into FY27.'\n"
        "- For each regulatory update infer and write a clear 'why_it_matters'.\n"
        "- Detect cross-company patterns and represent them under patterns. OMIT a pattern if you cannot describe a concrete India-specific dynamic.\n"
        "- Keep output executive, specific, and non-repetitive.\n"
        "- If a section/category has no evidence, OMIT it (empty array/string). Never fall back to placeholder text like 'Not found in source reviewed' inside generated prose.\n"
        f"- Date range: {date_range}.\n"
        f"Schema: {json.dumps(schema)}\n\n"
        f"Industry summary JSON:\n{industry_json[:15000]}\n\n"
        f"Regulatory summary JSON:\n{regulatory_json[:15000]}\n\n"
        f"Competitor summary JSON:\n{competitor_json[:20000]}"
    )
