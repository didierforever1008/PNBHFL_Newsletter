from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Dict, List

from dotenv import load_dotenv

load_dotenv()

COMPETITOR_ALIASES: Dict[str, List[str]] = {
    "HDFC Ltd": ["HDFC Housing", "Housing Development Finance", "HDFC home loans"],
    "LIC Housing Finance": ["LIC HFL", "LIC Housing", "LIC HF"],
    "Indiabulls Housing Finance": ["Indiabulls HFL", "IBHFL", "Indiabulls home loans"],
    "Can Fin Homes": ["Canfin Homes", "Can Fin HFL", "Canara Bank housing"],
    "Aavas Financiers": ["Aavas HFC", "Aavas home loans", "AU Small Finance housing"],
    "Home First Finance": ["HomeFirst Finance", "Home First HFC", "HFFC"],
    "Repco Home Finance": ["Repco HFL", "Repco HFC", "Repco housing"],
    "Tata Capital Housing Finance": ["Tata Housing Finance", "Tata Capital home loans", "TCHFL"],
    "Bajaj Finserv Home Loans": ["Bajaj Housing Finance", "BHFL", "Bajaj home loans"],
    "Shriram Housing Finance": ["Shriram HFC", "Shriram Housing", "Shriram home loans"],
    "L&T Finance Housing": ["L&T Housing Finance", "L&T home loans", "LTF housing"],
    "Aditya Birla Housing Finance": ["ABHFL", "Aditya Birla HFC", "AB Housing Finance"],
    "Cholamandalam Investment": ["Chola HFC", "Cholamandalam Housing", "Chola home loans", "CIFC"],
}

# Screener.in tickers — kept for reference; the active source for announcements is
# now BSE (see BSE_SECURITY_CODES below).
SCREENER_TICKERS: Dict[str, str] = {
    "HDFC Ltd":                     "HDFCBANK",     # merged into HDFC Bank in 2023
    "LIC Housing Finance":          "LICHSGFIN",
    "Indiabulls Housing Finance":   "SAMMAANCAP",   # renamed Sammaan Capital in 2024
    "Can Fin Homes":                "CANFINHOME",
    "Aavas Financiers":             "AAVAS",
    "Home First Finance":           "HOMEFIRST",
    "Repco Home Finance":           "REPCOHOME",
    "Tata Capital Housing Finance": "TATACAP",     # parent Tata Capital (IPO Oct 2025)
    "Bajaj Finserv Home Loans":     "BAJAJHFL",    # = Bajaj Housing Finance
    "Shriram Housing Finance":      "SHRIRAMFIN",  # parent
    "L&T Finance Housing":          "LTF",         # parent
    "Aditya Birla Housing Finance": "ABCAPITAL",   # parent (Aditya Birla Capital)
    "Cholamandalam Investment":     "CHOLAFIN",
}

# BSE security codes (6-digit scrip numbers). These power the corp-announcements
# pull at https://api.bseindia.com/BseIndiaAPI/api/AnnGetData/w. Auto-discovered
# from Screener.in pages; verify if any company changes parent/listing.
BSE_SECURITY_CODES: Dict[str, str] = {
    "HDFC Ltd":                     "500180",   # tracks HDFC Bank post-2023 merger
    "LIC Housing Finance":          "500253",
    "Indiabulls Housing Finance":   "535789",   # Sammaan Capital
    "Can Fin Homes":                "511196",
    "Aavas Financiers":             "541988",
    "Home First Finance":           "543259",
    "Repco Home Finance":           "535322",
    "Tata Capital Housing Finance": "544574",   # Tata Capital parent
    "Bajaj Finserv Home Loans":     "544252",   # = Bajaj Housing Finance
    "Shriram Housing Finance":      "511218",   # parent Shriram Finance
    "L&T Finance Housing":          "533519",   # parent L&T Finance
    "Aditya Birla Housing Finance": "540691",   # parent Aditya Birla Capital
    "Cholamandalam Investment":     "511243",
}


@dataclass(frozen=True)
class Settings:
    llm_provider: str = "openai"
    # Comma-separated list in .env (e.g. "gemini,ollama"); parsed into a tuple here.
    llm_fallback_providers: tuple = ()

    # Per-provider config
    gemini_api_key: str = ""
    gemini_model: str = "gemini-3-pro-preview"
    openai_api_key: str = ""
    openai_model: str = "gpt-5.2"
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "llama3.1:8b"

    llm_system_instruction_version: str = "v1"
    sqlite_db_path: str = "output/article_pipeline.db"
    news_api_key: str = ""


_ALLOWED_PROVIDERS = {"openai", "gemini", "ollama"}


def get_settings() -> Settings:
    provider = (os.getenv("LLM_PROVIDER", "openai").strip().lower() or "openai")
    if provider not in _ALLOWED_PROVIDERS:
        raise ValueError(f"LLM_PROVIDER must be one of {sorted(_ALLOWED_PROVIDERS)}.")

    fallback_raw = os.getenv("LLM_FALLBACK_PROVIDERS", "").strip().lower()
    fallback_providers: tuple = tuple(
        p.strip() for p in fallback_raw.split(",") if p.strip() and p.strip() != provider
    )
    for p in fallback_providers:
        if p not in _ALLOWED_PROVIDERS:
            raise ValueError(f"LLM_FALLBACK_PROVIDERS contains unsupported '{p}'.")

    gemini_api_key = os.getenv("GEMINI_API_KEY", "").strip()
    openai_api_key = os.getenv("OPENAI_API_KEY", "").strip()

    # Validate primary provider has the key it needs.
    if provider == "gemini" and not gemini_api_key:
        raise ValueError("LLM_PROVIDER=gemini but GEMINI_API_KEY is missing.")
    if provider == "openai" and not openai_api_key:
        raise ValueError("LLM_PROVIDER=openai but OPENAI_API_KEY is missing.")
    # Validate fallbacks too (so we don't fall back to a misconfigured provider).
    if "gemini" in fallback_providers and not gemini_api_key:
        raise ValueError("LLM_FALLBACK_PROVIDERS includes gemini but GEMINI_API_KEY is missing.")
    if "openai" in fallback_providers and not openai_api_key:
        raise ValueError("LLM_FALLBACK_PROVIDERS includes openai but OPENAI_API_KEY is missing.")

    gemini_model = os.getenv("GEMINI_MODEL", "gemini-3-pro-preview").strip() or "gemini-3-pro-preview"
    openai_model = os.getenv("OPENAI_MODEL", "gpt-5.2").strip() or "gpt-5.2"
    ollama_base_url = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434").strip() or "http://localhost:11434"
    ollama_model = os.getenv("OLLAMA_MODEL", "llama3.1:8b").strip() or "llama3.1:8b"
    llm_system_instruction_version = os.getenv("LLM_SYSTEM_INSTRUCTION_VERSION", "v1").strip() or "v1"
    sqlite_db_path = os.getenv("SQLITE_DB_PATH", "output/article_pipeline.db").strip() or "output/article_pipeline.db"
    news_api_key = os.getenv("NEWS_API_KEY", "").strip()
    return Settings(
        llm_provider=provider,
        llm_fallback_providers=fallback_providers,
        gemini_api_key=gemini_api_key,
        gemini_model=gemini_model,
        openai_api_key=openai_api_key,
        openai_model=openai_model,
        ollama_base_url=ollama_base_url,
        ollama_model=ollama_model,
        llm_system_instruction_version=llm_system_instruction_version,
        sqlite_db_path=sqlite_db_path,
        news_api_key=news_api_key,
    )


def get_competitors() -> Dict[str, List[str]]:
    return COMPETITOR_ALIASES


def first_n_competitors(n: int) -> List[str]:
    return list(COMPETITOR_ALIASES.keys())[:n]


def get_screener_tickers() -> Dict[str, str]:
    """Return the competitor-name -> Screener.in slug map (a copy)."""
    return dict(SCREENER_TICKERS)


def get_bse_security_codes() -> Dict[str, str]:
    """Return the competitor-name -> BSE security code (scrip number) map (a copy)."""
    return dict(BSE_SECURITY_CODES)
