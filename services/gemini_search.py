"""Multi-provider LLM JSON client.

Despite the legacy filename, this client now supports:
  - openai (Chat Completions, JSON-object response_format)
  - gemini (generateContent, application/json)
  - ollama (local fallback)

A primary provider is configured at construction time. Optional fallback providers
are tried in order ONLY if the primary exhausts its own retries (each provider gets
its own exponential-backoff retry budget). This lets us keep OpenAI as primary while
gracefully surviving outages by falling through to Gemini.
"""
from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass
from datetime import date
from typing import Any, Dict, List, Optional, Sequence

import requests

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class _ProviderConfig:
    """One entry in the primary→fallback chain."""
    provider: str
    model: str
    api_key: str
    ollama_base_url: str = "http://localhost:11434"


class GeminiSearchClient:
    """Multi-provider LLM client. Name kept for backward compatibility with imports."""

    def __init__(
        self,
        api_key: str = "",
        model: str = "gpt-5.2",
        timeout_seconds: int = 60,
        provider: str = "openai",
        ollama_base_url: str = "http://localhost:11434",
        # Optional fallback chain. When the primary exhausts retries, each fallback
        # is tried in order. Each one re-uses the full retry+backoff budget.
        fallbacks: Optional[Sequence[_ProviderConfig]] = None,
    ) -> None:
        self.timeout_seconds = timeout_seconds
        self._primary = _ProviderConfig(
            provider=provider.strip().lower(),
            model=model,
            api_key=api_key,
            ollama_base_url=ollama_base_url.rstrip("/"),
        )
        if self._primary.provider not in {"openai", "gemini", "ollama"}:
            raise ValueError(
                f"Unsupported provider '{self._primary.provider}'. "
                "Use 'openai', 'gemini', or 'ollama'."
            )
        self._fallbacks: List[_ProviderConfig] = list(fallbacks or [])

        # Public attributes kept for back-compat with code that still reads .provider / .model.
        self.api_key = api_key
        self.model = model
        self.provider = self._primary.provider
        self.ollama_base_url = self._primary.ollama_base_url

    # --- Public entrypoint ------------------------------------------------

    def run_json_prompt(self, prompt: str, retries: int = 4) -> Dict[str, Any]:
        """Try the primary provider with retries; on total failure, try each fallback."""
        chain: List[_ProviderConfig] = [self._primary, *self._fallbacks]
        last_error: Optional[Exception] = None
        for idx, cfg in enumerate(chain):
            try:
                return self._run_with_retries(cfg, prompt, retries=retries)
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                if idx + 1 < len(chain):
                    next_provider = chain[idx + 1].provider
                    logger.warning(
                        "LLM provider %s failed after retries; falling back to %s: %s",
                        cfg.provider, next_provider, exc,
                    )
                else:
                    logger.warning("LLM provider %s failed and no fallback remains: %s",
                                   cfg.provider, exc)
        raise RuntimeError(f"All LLM providers failed. Last error: {last_error}")

    # --- Per-provider call with retry policy ------------------------------

    def _run_with_retries(self, cfg: _ProviderConfig, prompt: str, retries: int) -> Dict[str, Any]:
        last_error: Optional[Exception] = None
        for attempt in range(retries + 1):
            try:
                data = self._call_once(cfg, prompt)
                text = self._extract_text(cfg.provider, data)
                parsed = self._parse_json(cfg, text)
                if not isinstance(parsed, dict):
                    raise ValueError(f"{cfg.provider} output was not a JSON object")
                return parsed
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                logger.warning("LLM request attempt %s failed (provider=%s): %s",
                               attempt + 1, cfg.provider, exc)
                if attempt < retries:
                    backoff = min(2 ** attempt, 32)
                    time.sleep(backoff)
        raise RuntimeError(f"{cfg.provider} generation failed after retries: {last_error}")

    # --- Provider-specific HTTP calls ------------------------------------

    def _call_once(self, cfg: _ProviderConfig, prompt: str) -> Dict[str, Any]:
        if cfg.provider == "openai":
            return self._call_openai(cfg, prompt)
        if cfg.provider == "gemini":
            return self._call_gemini(cfg, prompt)
        if cfg.provider == "ollama":
            return self._call_ollama(cfg, prompt)
        raise RuntimeError(f"Unsupported provider '{cfg.provider}'")

    def _call_openai(self, cfg: _ProviderConfig, prompt: str) -> Dict[str, Any]:
        url = "https://api.openai.com/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {cfg.api_key}",
            "Content-Type": "application/json",
        }
        payload: Dict[str, Any] = {
            "model": cfg.model,
            "messages": [{"role": "user", "content": prompt}],
            "response_format": {"type": "json_object"},
            "temperature": 0.1,
        }
        response = requests.post(url, headers=headers, json=payload, timeout=self.timeout_seconds)
        if response.status_code >= 400:
            raise RuntimeError(f"HTTP {response.status_code}; body={response.text[:400]}")
        return response.json()

    def _call_gemini(self, cfg: _ProviderConfig, prompt: str) -> Dict[str, Any]:
        model = cfg.model.replace("models/", "").strip()
        url = (
            f"https://generativelanguage.googleapis.com/v1beta/models/{model}"
            f":generateContent?key={cfg.api_key}"
        )
        payload: Dict[str, Any] = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "temperature": 0.1,
                "maxOutputTokens": 4096,
                "responseMimeType": "application/json",
            },
        }
        response = requests.post(url, json=payload, timeout=self.timeout_seconds)
        if response.status_code >= 400:
            raise RuntimeError(f"HTTP {response.status_code}; body={response.text[:400]}")
        return response.json()

    def _call_ollama(self, cfg: _ProviderConfig, prompt: str) -> Dict[str, Any]:
        url = f"{cfg.ollama_base_url}/api/generate"
        payload: Dict[str, Any] = {
            "model": cfg.model,
            "prompt": prompt,
            "stream": False,
            "format": "json",
            "options": {"temperature": 0.1},
        }
        response = requests.post(url, json=payload, timeout=self.timeout_seconds)
        if response.status_code >= 400:
            raise RuntimeError(f"HTTP {response.status_code}; body={response.text[:400]}")
        return response.json()

    # --- Response unwrapping ---------------------------------------------

    @staticmethod
    def _extract_text(provider: str, data: Dict[str, Any]) -> str:
        if provider == "openai":
            choices = data.get("choices", [])
            if not choices:
                raise ValueError(f"OpenAI: no choices in response: {json.dumps(data)[:600]}")
            content = (choices[0].get("message") or {}).get("content") or ""
            content = content.strip()
            if not content:
                raise ValueError(f"OpenAI: empty content: {json.dumps(data)[:600]}")
            return content

        if provider == "ollama":
            ollama_response = data.get("response")
            if isinstance(ollama_response, str) and ollama_response.strip():
                return ollama_response.strip()
            raise ValueError(f"Ollama: empty response: {json.dumps(data)[:600]}")

        # Gemini
        candidates = data.get("candidates", [])
        if not candidates:
            raise ValueError(f"Gemini: no candidates: {json.dumps(data)[:600]}")
        parts = candidates[0].get("content", {}).get("parts", [])
        chunks = [p.get("text", "") for p in parts if p.get("text")]
        text = "\n".join(chunks).strip()
        if not text:
            raise ValueError(f"Gemini: empty text: {json.dumps(data)[:600]}")
        return text

    # --- JSON parsing (tolerant of stray prose / code fences) ------------

    def _parse_json(self, cfg: _ProviderConfig, text: str) -> Any:
        try:
            return json.loads(text)
        except Exception:
            loose = self._parse_json_loose(text)
            if isinstance(loose, dict):
                return loose
            return self._repair_json(cfg, text)

    def _repair_json(self, cfg: _ProviderConfig, text: str) -> Dict[str, Any]:
        repair_prompt = (
            "Convert the following content into strictly valid JSON without adding or removing "
            "factual fields. Return only a JSON object.\n\n"
            f"Content:\n{text[:12000]}"
        )
        repaired = self._call_once(cfg, repair_prompt)
        repaired_text = self._extract_text(cfg.provider, repaired)
        parsed = self._parse_json_loose(repaired_text)
        if not isinstance(parsed, dict):
            raise ValueError("Repaired output is not JSON object")
        return parsed

    @staticmethod
    def _parse_json_loose(text: str) -> Any:
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            fenced = re.search(r"```json\s*(\{.*\}|\[.*\])\s*```", text, flags=re.S)
            if fenced:
                return json.loads(fenced.group(1))
            start = text.find("{")
            end = text.rfind("}")
            if start >= 0 and end > start:
                try:
                    return json.loads(text[start : end + 1])
                except Exception:  # noqa: BLE001
                    return None
            return None


def iso_today() -> str:
    return date.today().isoformat()


def normalize_company_list(companies: List[str]) -> List[str]:
    return [c.strip() for c in companies if c and c.strip()]
