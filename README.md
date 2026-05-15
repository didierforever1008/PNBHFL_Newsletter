# Housing Finance Weekly Digest

A Python pipeline that generates a competitive-intelligence newsletter for the Indian
housing-finance sector. The output is a polished, multi-page PDF assembled from four
data sources, classified and synthesized with an LLM, and laid out via ReportLab.

## What the pipeline does, end to end

1. **Ingestion** — for each tracked competitor (configured in `config.py`):
   - NewsAPI (last ~30 days, free-tier-aware)
   - Supplemental RSS feeds (Reuters / BFSI sources)
   - BSE corp-announcements via `api.bseindia.com/BseIndiaAPI/api/AnnGetData/w`
   - Policy / rating feeds (RBI / NHB / CRISIL / ICRA)
2. **Per-competitor curation** — scoring, semantic deduplication, balanced selection.
3. **BSE classifier** — LLM tags each announcement into one of:
   `Hiring | Management Change | Rumour | Funding | Capital | Liquidity |
   Risk | Governance | Asset Quality | Growth | Strategy | Other`.
   When a headline announces a management change but does not name the individual,
   the attached PDF is downloaded, text-extracted with `pdfplumber`, and a second
   LLM call pulls out `{person_name, position, action, effective_date, reason}`.
   A single filing may yield multiple named changes.
4. **Strict Operational Signals gate** — only items with a real named individual +
   recognized action (`Appointment | Resignation | Retirement | Role Change | Death`) +
   event date survive.
5. **Synthesis** — `compose_newsletter` produces `industry_pulse`, `regulatory_watch`,
   `competitor_intelligence.grouped_insights`, `patterns`, `key_takeaways`. Each
   industry highlight and regulatory update carries distinct
   `pointer / impact / why_it_matters` fields.
6. **Rendering** — ReportLab layout with:
   - Coloured-card index (one unique colour per section)
   - Section colour-coding propagated to every callout (`accent_hex` left strip)
   - `KeepTogether` + `CondPageBreak` to suppress blank pages between sections
   - PDF-magic-byte validation, baseline-aligned typography

## Provider routing

LLM calls are routed through a fallback chain set in `.env`:

```
LLM_PROVIDER=openai
LLM_FALLBACK_PROVIDERS=gemini
OPENAI_MODEL=gpt-5.2
GEMINI_MODEL=gemini-3-pro-preview
```

The client (`services/gemini_search.py`) supports `openai`, `gemini`, and `ollama`.
If the primary provider exhausts its retry budget, every fallback in the list is
tried in order before raising.

## Audit trail

Every fetched article (NewsAPI / RSS / BSE) is upserted into `output/article_pipeline.db`:

| Table | Purpose |
|---|---|
| `articles` | One row per unique URL — title, company, source, dates, original URL |
| `article_pipeline` | One row per processing stage per article (JSON payload) — 25+ stage names including `bse_classification`, `bse_pdf_enrichment`, `bse_kept`, `bse_gate_dropped` |
| `llm_system_instruction_audit` | LLM call audit log — stage, provider, model, version |

Audit data is queryable into a pandas DataFrame for compliance review.

## Setup

```powershell
# 1. Install deps
pip install -r requirements.txt

# 2. Create your .env (this file is gitignored)
#    Required keys:
#      LLM_PROVIDER, LLM_FALLBACK_PROVIDERS,
#      GEMINI_MODEL, GEMINI_API_KEY,
#      OPENAI_MODEL, OPENAI_API_KEY,
#      NEWS_API_KEY
```

## Running a digest

```powershell
# Full pipeline (LLM calls + render) — for a new date range
python app.py --week-start 2026-05-01 --week-end 2026-05-15 ^
  --out output/newsletter_2026-05-01_to_2026-05-15.pdf

# Render-only — re-render the cached newsletter JSON for an existing week
python app.py --render-only --week-start 2026-05-01 --week-end 2026-05-15 ^
  --out output/newsletter_2026-05-01_to_2026-05-15.pdf
```

The renderer appends `_agentic_analysis.pdf` to the stem, so the final file lands at
`output/newsletter_<start>_to_<end>_agentic_analysis.pdf` alongside a `.md`.

## Project layout

```
app.py                       # CLI entrypoint + per-stage orchestration
config.py                    # competitors, BSE codes, Screener tickers, settings loader
requirements.txt
services/
  news_api.py                # NewsAPI client (429-graceful)
  source_feeds.py            # RSS + policy/rating ingestion
  bse_announcements.py       # BSE API scrape + LLM classifier + PDF enrichment + gate
  screener_announcements.py  # Legacy Screener scraper (kept for reference)
  gemini_search.py           # Multi-provider LLM client (openai/gemini/ollama + fallback)
  newsletter_composer.py     # Final synthesis prompt + deterministic-grouping fallback
  prompts.py                 # All shared LLM prompts + editorial rules
  article_store.py           # SQLite audit store
  dedup.py / scoring.py / section_validators.py / agentic_pipeline.py
models/
  schemas.py
renderers/
  pdf_renderer.py            # ReportLab layout (index, callouts, page-decor)
utils/
  url_tools.py
```

## Notes

- `services/screener_announcements.py` was the earlier ingestion source — kept on disk
  but no longer wired in. BSE has full historical coverage and richer per-filing detail.
- BSE's attachment URLs migrate from `corpfiling/AttachLive/` to `corpfiling/AttachHis/`
  after some period; the downloader retries the alternate path on 404.
- NewsAPI free tier limits lookback to ~30 days; the `_clamp_newsapi_from` helper in
  `app.py` clamps the from-date so the API call succeeds while every other source
  (BSE, RSS, policy feeds) still uses the full requested window.
